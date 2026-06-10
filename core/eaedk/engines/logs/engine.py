"""Log Analysis Engine (spec §4.8) — deterministic signatures first, LLM triage fallback.

Pipeline: read file -> detect format -> match the Signature DB. If a known signature matches,
that is the (HIGH-confidence, cited) answer. Only when nothing matches do we slice a strict
context window around the crash vector and hand it to the post-filtered LLM, which must return
structural hypotheses without inventing hardware facts.

Async by construction: file I/O and the (blocking) LLM call run via ``asyncio.to_thread`` so a
caller can analyze multiple logs concurrently.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ... import repo
from ...llm import prompts
from ...llm.gateway import Gateway
from ...llm.postfilter import Allowlist, build_allowlist, filter_text, numbers_in_text
from .parser import SignatureMatch, crash_window, detect_format, match_signatures

_ALLOWED_CONF = {"HIGH", "MEDIUM", "LOW", "UNKNOWN"}


@dataclass
class LogAnalysisResult:
    path: str
    format: str
    n_lines: int
    matches: list[SignatureMatch] = field(default_factory=list)
    triage: dict[str, Any] | None = None
    log_file_id: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path, "format": self.format, "n_lines": self.n_lines,
            "matches": [vars(m) for m in self.matches], "triage": self.triage,
            "log_file_id": self.log_file_id,
        }

    def to_markdown(self) -> str:
        L = [f"## Log Analysis: {self.path}",
             f"Format detected: {self.format}  ({self.n_lines} lines)", "",
             "## Signature Matches (deterministic)"]
        if self.matches:
            for m in self.matches:
                L.append(f"- [HIGH] {m.severity}  line {m.line_no}: \"{m.line}\"")
                L.append(f"    cause: {m.cause}")
                L.append(f"    fix:   {m.fix}")
        else:
            L.append("- No known signature matched.")
        if self.triage is not None:
            L += ["", "## LLM Triage (degraded fallback)"]
            t = self.triage
            if not t.get("available"):
                L.append(f"- {t.get('reason', 'unavailable')}")
            else:
                L.append(f"confidence: {t.get('confidence', 'LOW')}  "
                         f"(window from line {t.get('window_start')})")
                for h in t.get("hypotheses", []):
                    L.append(f"- cause: {h.get('cause','')}")
                    L.append(f"    evidence: {h.get('evidence_line','')}")
                    L.append(f"    check:    {h.get('suggested_check','')}")
                if not t.get("hypotheses"):
                    L.append(t.get("text", "(no structured hypotheses)"))
                L.append(f"\n[LLM] {t.get('removed', 0)} uncited hardware claim(s) removed "
                         f"by post-filter.")
        return "\n".join(L)


def _read(path: str) -> str:
    return Path(path).read_text(encoding="utf-8", errors="replace")


def _load_signatures(conn: sqlite3.Connection) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT id, format, pattern_regex, cause, fix, severity FROM log_signatures").fetchall()]


def _store_log_file(conn, project, path: str, text: str, fmt: str) -> int:
    now = datetime.now(timezone.utc).isoformat()
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    pid = project["id"] if project else None
    with conn:
        return conn.execute(
            "INSERT INTO log_files(project_id,format,uri,hash,created_at) VALUES (?,?,?,?,?)",
            (pid, fmt, path, digest, now)).lastrowid


def _store_match_analyses(conn, log_file_id: int, matches: list[SignatureMatch]) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with conn:
        for m in matches:
            conn.execute(
                "INSERT INTO log_analyses(log_file_id,signature_id,llm_hypothesis,confidence,"
                "created_at) VALUES (?,?,NULL,'HIGH',?)", (log_file_id, m.signature_id, now))


def _store_triage(conn, log_file_id: int, triage: dict) -> None:
    if not triage.get("available"):
        return
    now = datetime.now(timezone.utc).isoformat()
    payload = json.dumps({"hypotheses": triage.get("hypotheses", []),
                          "text": triage.get("text")})
    with conn:
        conn.execute(
            "INSERT INTO log_analyses(log_file_id,signature_id,llm_hypothesis,confidence,"
            "created_at) VALUES (?,NULL,?,?,?)",
            (log_file_id, payload, triage.get("confidence", "LOW"), now))


_FENCE = re.compile(r"^```(?:json)?|```$", re.MULTILINE)


def _extract_json(raw: str) -> dict | None:
    s = _FENCE.sub("", raw).strip()
    a, b = s.find("{"), s.rfind("}")
    if a == -1 or b == -1 or b < a:
        return None
    try:
        obj = json.loads(s[a:b + 1])
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        return None


def _postfilter_payload(raw: str, allow: Allowlist) -> dict[str, Any]:
    """Parse the model's JSON payload and post-filter every string field. Falls back to
    filtering raw text if the payload isn't valid JSON."""
    parsed = _extract_json(raw)
    removed = 0
    if parsed is None:
        text, removed = filter_text(raw, allow)
        return {"hypotheses": [], "text": text, "confidence": "LOW", "removed": removed}
    hyps = []
    for h in parsed.get("hypotheses", []):
        if not isinstance(h, dict):
            continue
        clean = {}
        for k in ("cause", "evidence_line", "suggested_check"):
            v = h.get(k, "")
            if isinstance(v, str):
                fv, r = filter_text(v, allow)
                clean[k] = fv
                removed += r
            else:
                clean[k] = v
        hyps.append(clean)
    conf = str(parsed.get("confidence", "LOW")).upper()
    if conf not in _ALLOWED_CONF:
        conf = "LOW"
    return {"hypotheses": hyps, "text": None, "confidence": conf, "removed": removed}


async def _llm_triage(conn, fmt: str, text: str, project, gateway: Gateway) -> dict[str, Any]:
    start, window, crash_line = crash_window(text, 100)
    context = "\n".join(window)
    if not gateway.available():
        return {"available": False,
                "reason": f"LLM gateway unavailable (model '{gateway.model}' not pulled)."}
    prompt = prompts.build_log_triage_prompt(fmt, context)
    raw = await asyncio.to_thread(gateway.provider.generate, prompts.LOG_SYSTEM, prompt)
    allow = build_allowlist(conn, project) if project else Allowlist()
    allow.numbers |= numbers_in_text(context)  # numbers quoted from the log are "cited"
    out = _postfilter_payload(raw, allow)
    out.update({"available": True, "window_start": start, "crash_line": crash_line})
    return out


async def analyze_log_async(conn: sqlite3.Connection, path: str,
                            project_name: str | None = None, use_llm: bool = False,
                            gateway: Gateway | None = None) -> LogAnalysisResult:
    text = await asyncio.to_thread(_read, path)
    lines = text.splitlines()
    fmt = detect_format(text)
    sigs = _load_signatures(conn)
    matches = match_signatures(text, fmt, sigs)

    project = repo.get_project(conn, project_name) if project_name else None
    log_file_id = _store_log_file(conn, project, path, text, fmt)

    triage = None
    if matches:
        _store_match_analyses(conn, log_file_id, matches)
    elif use_llm:
        triage = await _llm_triage(conn, fmt, text, project, gateway or Gateway())
        _store_triage(conn, log_file_id, triage)

    return LogAnalysisResult(path=path, format=fmt, n_lines=len(lines),
                             matches=matches, triage=triage, log_file_id=log_file_id)


def analyze_log(conn: sqlite3.Connection, path: str, project_name: str | None = None,
                use_llm: bool = False, gateway: Gateway | None = None) -> LogAnalysisResult:
    """Synchronous wrapper for the CLI."""
    return asyncio.run(analyze_log_async(conn, path, project_name, use_llm, gateway))
