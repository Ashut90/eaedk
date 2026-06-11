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
from ...engines.validation.rules import RULES
from ...llm import prompts
from ...llm.gateway import Gateway
from ...llm.postfilter import Allowlist, build_allowlist, filter_text, numbers_in_text
from .parser import SignatureMatch, crash_window, detect_format, match_signatures

_ALLOWED_CONF = {"HIGH", "MEDIUM", "LOW", "UNKNOWN"}

# Deterministic implication: which words in a triage hypothesis implicate a validation rule.
# Only rules that are *already* a project gap can be implicated, so this never invents a rule.
_RULE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "DDR_TIMING_VERIFIED": ("ddr", "dram", "timing", "memory training", "relocation"),
    "LOAD_ADDR_CONFLICT": ("load address", "memory map", "overlap", "relocation"),
    "BOOT_FLOW_CONSISTENCY": ("boot flow", "handoff", "entry point", "kernel load"),
    "FLASH_CAPACITY": ("flash", "image size", "overflow"),
    "RAM_BUDGET": ("ram", "stack", "heap"),
    "VECTOR_TABLE_PLACEMENT": ("vector table", "vtor"),
    "CONSOLE_UART_DEFINED": ("console", "uart", "serial", "stdout"),
    "PARTITION_LAYOUT_FITS": ("partition", "slot", "layout"),
    "PARTITION_NO_OVERLAP": ("partition", "overlap"),
    "RECOVERY_PRESENT": ("recovery", "rollback"),
    "TOOLCHAIN_ARCH_MATCH": ("toolchain", "cross-compile", "architecture"),
    "POWER_SEQUENCE": ("power", "rail", "regulator", "sequenc"),
    "PINMUX_CONFLICT": ("pin", "mux", "pinmux"),
    "DRIVER_COMPATIBLE_STRING": ("compatible", "device tree", "dtb"),
    "REGISTER_MAP_PRESENT": ("register map", "register"),
}


# Fix 6 (v1.7.0): a Cortex-M fault report carries the faulting address — turn it into a
# concrete, runnable next step instead of "decode CFSR / inspect the stacked PC".
_PC_RE = re.compile(r"\bPC[=:\s]+0x([0-9a-fA-F]{4,8})\b")
_FLASH_ADDR_RE = re.compile(r"\b0x(0[0-9a-fA-F]{7})\b")     # fallback: a flash-range address
_FAULT_WORDS = ("hardfault", "cfsr", "busfault", "usagefault", "memmanage", "cortex-m")


def _sanitize_elf(project_name: str | None) -> str:
    if not project_name:
        return "build/<your-firmware>.elf"
    safe = "".join(c if c.isalnum() else "_" for c in project_name)
    return f"build/{safe}.elf"


def crash_locate_hint(text: str, matches: list[SignatureMatch],
                      project_name: str | None) -> str | None:
    """Concrete next action for an MCU fault: addr2line on the faulting PC. None if there's no
    fault match or no address to act on (degrade silently rather than guess)."""
    fault = any(w in (m.cause + " " + m.line).lower() for m in matches for w in _FAULT_WORDS)
    if not fault:
        return None
    m = _PC_RE.search(text) or _FLASH_ADDR_RE.search(text)
    if not m:
        return None
    addr = "0x" + m.group(1)
    elf = _sanitize_elf(project_name)
    return (f"Find the crash location (the line of C it faulted on) with:\n"
            f"    arm-none-eabi-addr2line -e {elf} {addr}")


@dataclass
class LogAnalysisResult:
    path: str
    format: str
    n_lines: int
    matches: list[SignatureMatch] = field(default_factory=list)
    triage: dict[str, Any] | None = None
    log_file_id: int | None = None
    write_backs: list[dict[str, Any]] = field(default_factory=list)
    next_action: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path, "format": self.format, "n_lines": self.n_lines,
            "matches": [vars(m) for m in self.matches], "triage": self.triage,
            "log_file_id": self.log_file_id, "write_backs": self.write_backs,
            "next_action": self.next_action,
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
            if self.next_action:
                L += ["", "## Next: find where it crashed", self.next_action]
        else:
            # Never silent: if the LLM wasn't consulted, point the engineer at it.
            hint = "" if self.triage is not None else " Run with --llm for triage."
            L.append(f"- No known signature matched.{hint}")
        if self.triage is not None:
            L += ["", "## LLM Triage (degraded fallback)"]
            t = self.triage
            if not t.get("available"):
                L.append(f"- {t.get('reason', 'unavailable')}")
            else:
                L.append(f"confidence: {t.get('confidence', 'LOW')}  "
                         f"(window from line {t.get('window_start')})")
                corr = t.get("correlation")
                if corr:
                    ng, nr, nf = (len(corr["validation_gaps"]), len(corr["risks"]),
                                  len(corr["unverified_facts"]))
                    L.append(f"correlated against project '{corr['project']}': "
                             f"{ng} validation gap(s), {nr} risk(s), {nf} unverified fact(s)")
                for h in t.get("hypotheses", []):
                    L.append(f"- cause: {h.get('cause','')}")
                    L.append(f"    evidence: {h.get('evidence_line','')}")
                    L.append(f"    check:    {h.get('suggested_check','')}")
                if not t.get("hypotheses"):
                    L.append(t.get("text", "(no structured hypotheses)"))
                L.append(f"\n[LLM] {t.get('removed', 0)} uncited hardware claim(s) removed "
                         f"by post-filter.")
        if self.write_backs:
            L += ["", "## Written Back to Project"]
            for w in self.write_backs:
                items = ", ".join(w["items"]) or "(no checklist item)"
                L.append(f"- {w['rule']} [{w['status']}] -> note on [{items}]; "
                         f"tracked risk [{w['severity']}] {w['risk']}")
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


def build_correlation(conn: sqlite3.Connection, project) -> dict[str, Any]:
    """Cross-engine correlation payload for a project: the architectural gaps a boot-log
    failure should be reasoned against — unresolved validations, open risks, and the
    unverified (MEDIUM/LOW) hardware assumptions sitting in the fact layer for this board.
    """
    from ...orchestrator import assess_project
    resp = assess_project(conn, project)
    gaps = [{"check": v["check"], "status": v["status"], "reason": v["reason"]}
            for v in resp.validations
            if v["status"] == "FAIL" or (v["status"] == "UNKNOWN" and v.get("engaged"))]
    risks = [{"rule_key": r["rule_key"], "severity": r["severity"],
              "explanation": r["explanation"]}
             for r in resp.risks if r["severity"] in ("HIGH", "MEDIUM")]
    unverified = []
    board_name = repo.project_board_name(conn, project)
    if board_name:
        for r in repo.unverified_board_facts(conn, board_name):
            unverified.append({"domain": r["domain"], "key": r["fact_key"],
                               "value": r["fact_value"], "confidence": r["confidence"]})
    return {"project": project["name"], "feasibility": resp.feasibility,
            "validation_gaps": gaps, "risks": risks, "unverified_facts": unverified}


def _correlation_text(payload: dict[str, Any]) -> str:
    lines = [f"Project: {payload['project']}  (deterministic feasibility: {payload['feasibility']})"]
    if payload["validation_gaps"]:
        lines.append("Unresolved validation checks:")
        lines += [f"  - {g['check']} [{g['status']}]: {g['reason']}"
                  for g in payload["validation_gaps"]]
    if payload["risks"]:
        lines.append("Open risks:")
        lines += [f"  - [{r['severity']}] {r['rule_key']}: {r['explanation']}"
                  for r in payload["risks"]]
    if payload["unverified_facts"]:
        lines.append("Unverified hardware assumptions (MEDIUM/LOW confidence):")
        lines += [f"  - {f['domain']}.{f['key']} = {f['value']} [{f['confidence']}]"
                  for f in payload["unverified_facts"]]
    if not (payload["validation_gaps"] or payload["risks"] or payload["unverified_facts"]):
        lines.append("(no unresolved validations, risks, or unverified assumptions)")
    return "\n".join(lines)


async def _llm_triage(conn, fmt: str, text: str, project, gateway: Gateway,
                      project_aware: bool = False) -> dict[str, Any]:
    start, window, crash_line = crash_window(text, 100)
    context = "\n".join(window)
    if not gateway.available():
        return {"available": False,
                "reason": f"LLM gateway unavailable (model '{gateway.model}' not pulled)."}

    correlation = None
    correlation_text = None
    if project_aware and project is not None:
        correlation = build_correlation(conn, project)
        correlation_text = _correlation_text(correlation)

    prompt = prompts.build_log_triage_prompt(fmt, context, correlation_text)
    raw = await asyncio.to_thread(gateway.provider.generate, prompts.LOG_SYSTEM, prompt)
    allow = build_allowlist(conn, project) if project else Allowlist()
    allow.numbers |= numbers_in_text(context)  # numbers quoted from the log are "cited"
    out = _postfilter_payload(raw, allow)
    out.update({"available": True, "window_start": start, "crash_line": crash_line,
                "correlation": correlation})
    return out


def write_back(conn: sqlite3.Connection, project, triage: dict, path: str,
               log_file_id: int) -> list[dict[str, Any]]:
    """Close the loop: when a triage hypothesis implicates a project gap, append a note to
    the owning checklist item(s) and open/append a tracked risk (severity inherited from the
    validation rule). Deterministic — only implicates gaps that already exist.
    """
    correlation = triage.get("correlation")
    hyps = triage.get("hypotheses", [])
    if not correlation or not hyps:
        return []
    blob = " ".join(f"{h.get('cause','')} {h.get('suggested_check','')} "
                    f"{h.get('evidence_line','')}" for h in hyps).lower()
    cause = next((h.get("cause", "") for h in hyps if h.get("cause")), "triage hypothesis")
    now = datetime.now(timezone.utc).isoformat()
    ref = f"{Path(path).name}#{log_file_id}"
    note = f"[{now}] log-triage ({ref}): {cause}"

    written: list[dict[str, Any]] = []
    for gap in correlation["validation_gaps"]:          # only FAIL / engaged-UNKNOWN rules
        rule = gap["check"]
        kws = _RULE_KEYWORDS.get(rule, ())
        if not any(kw in blob for kw in kws):
            continue
        items = repo.items_for_rule(conn, project["id"], rule)
        for item_key in items:
            repo.append_checklist_note(conn, project["id"], item_key, note)
        severity = RULES[rule].severity if rule in RULES else "MEDIUM"
        action = repo.upsert_tracked_risk(
            conn, project["id"], rule, severity,
            explanation=f"From log triage ({ref}, gap {gap['status']}): {cause}",
            mitigation=gap.get("reason"))
        written.append({"rule": rule, "status": gap["status"], "severity": severity,
                        "items": items, "risk": action})
    return written


async def analyze_log_async(conn: sqlite3.Connection, path: str,
                            project_name: str | None = None, use_llm: bool = False,
                            gateway: Gateway | None = None,
                            project_aware: bool = False) -> LogAnalysisResult:
    text = await asyncio.to_thread(_read, path)
    lines = text.splitlines()
    fmt = detect_format(text)
    sigs = _load_signatures(conn)
    matches = match_signatures(text, fmt, sigs)

    project = repo.get_project(conn, project_name) if project_name else None
    log_file_id = _store_log_file(conn, project, path, text, fmt)

    triage = None
    write_backs: list[dict[str, Any]] = []
    if matches:
        _store_match_analyses(conn, log_file_id, matches)
    elif use_llm:
        triage = await _llm_triage(conn, fmt, text, project, gateway or Gateway(),
                                   project_aware=project_aware)
        _store_triage(conn, log_file_id, triage)
        if project_aware and project is not None and triage.get("available"):
            write_backs = write_back(conn, project, triage, path, log_file_id)

    next_action = crash_locate_hint(text, matches, project_name)
    return LogAnalysisResult(path=path, format=fmt, n_lines=len(lines), matches=matches,
                             triage=triage, log_file_id=log_file_id, write_backs=write_backs,
                             next_action=next_action)


def analyze_log(conn: sqlite3.Connection, path: str, project_name: str | None = None,
                use_llm: bool = False, gateway: Gateway | None = None,
                project_aware: bool = False) -> LogAnalysisResult:
    """Synchronous wrapper for the CLI."""
    return asyncio.run(analyze_log_async(conn, path, project_name, use_llm, gateway,
                                         project_aware))
