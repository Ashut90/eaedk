"""Datasheet ingestion engine: stage candidates (never silent-write), confirm to the
knowledge base, optional LLM assist for fields the deterministic pass missed.

The ONLY path that writes to `facts` is ``confirm_candidate`` (via ``repo.record_fact`` with
``source_type='DATASHEET'``). Ingestion itself only stages `fact_candidates`.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ... import repo
from .extract import Candidate, Page, extract_from_pages, TARGET_KEYS
from .pdf import pdf_to_pages

_DOMAIN_FOR_KEY = {
    "flash_base": "MEMORY", "flash_bytes": "MEMORY", "ram_base": "MEMORY",
    "ram_bytes": "MEMORY", "sysclk_max_hz": "CLOCK",
}


@dataclass
class IngestResult:
    board: str
    source_id: int
    candidates: list[dict[str, Any]] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)


def _file_hash(path: str) -> str | None:
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return None


def _llm_assist(pages: list[Page], missing: list[str], gateway) -> list[Candidate]:
    """Best-effort LLM extraction for fields the deterministic pass left empty. LOW + flagged.
    Guarded: any failure (no model, bad JSON) yields no candidates."""
    if not missing or gateway is None or not gateway.available():
        return []
    # Feed only pages that look relevant, capped, to keep the prompt small.
    relevant = [p for p in pages if re.search(r"memory|flash|sram|clock|MHz", p.text, re.I)][:3]
    if not relevant:
        return []
    context = "\n\n".join(f"[page {p.number}]\n{p.text[:1500]}" for p in relevant)
    system = ("You extract embedded hardware facts from datasheet text. Return ONLY JSON "
              "{\"<key>\": {\"value\": \"...\", \"page\": <int>}} for keys you can find "
              "VERBATIM in the text. Omit any key you cannot find. Never guess.")
    prompt = (f"KEYS NEEDED: {', '.join(missing)}\n"
              f"(flash_base/ram_base as 0x hex; *_bytes as integer bytes; sysclk_max_hz in Hz)\n"
              f"DATASHEET TEXT:\n{context}\n\nJSON:")
    try:
        raw = gateway.provider.generate(system, prompt)
        a, b = raw.find("{"), raw.rfind("}")
        data = json.loads(raw[a:b + 1]) if a != -1 and b != -1 else {}
    except Exception:
        return []
    out: list[Candidate] = []
    for key in missing:
        item = data.get(key)
        if not isinstance(item, dict):
            continue
        val = item.get("value")
        if val in (None, ""):
            continue
        page = item.get("page") if isinstance(item.get("page"), int) else (relevant[0].number)
        out.append(Candidate(_DOMAIN_FOR_KEY.get(key, "MEMORY"),
                             "clock" if key == "sysclk_max_hz" else "memmap",
                             key, str(val), "llm", "LOW", page, "LLM-assisted", ""))
    return out


def ingest_datasheet(conn: sqlite3.Connection, pdf_path: str, board_name: str,
                     use_llm: bool = False, gateway=None,
                     reader: Callable[[str], list[Page]] = pdf_to_pages) -> IngestResult:
    board = conn.execute("SELECT id FROM boards WHERE name=?", (board_name,)).fetchone()
    if board is None:
        raise ValueError(f"unknown board: {board_name}")
    board_id = board["id"]

    pages = reader(pdf_path)
    source_id = repo.create_datasheet_source(
        conn, title=Path(pdf_path).name, uri=str(pdf_path), file_hash=_file_hash(pdf_path))

    candidates = extract_from_pages(pages)
    if use_llm:
        found = {c.fact_key for c in candidates}
        missing = [k for k in TARGET_KEYS if k not in found]
        from ...llm.gateway import Gateway
        candidates += _llm_assist(pages, missing, gateway or Gateway())

    counts: dict[str, int] = {}
    summary: list[dict[str, Any]] = []
    with conn:
        for c in candidates:
            cid = repo.add_fact_candidate(
                conn, board_id=board_id, source_id=source_id, domain=c.domain, kind=c.kind,
                fact_key=c.fact_key, fact_value=c.fact_value, method=c.method,
                confidence=c.confidence, page=c.page, section=c.section, snippet=c.snippet)
            counts[c.confidence] = counts.get(c.confidence, 0) + 1
            summary.append({"id": cid, "key": c.fact_key, "value": c.fact_value,
                            "confidence": c.confidence, "method": c.method, "page": c.page})
    return IngestResult(board=board_name, source_id=source_id, candidates=summary, counts=counts)


def confirm_candidate(conn: sqlite3.Connection, candidate_id: int,
                      confidence: str | None = None) -> str:
    """Commit a pending candidate to the knowledge base (the only write path). Confirmation
    counts as human verification. Returns 'confirmed' | 'not_found' | 'already'."""
    c = repo.get_fact_candidate(conn, candidate_id)
    if c is None:
        return "not_found"
    if c["status"] != "pending":
        return "already"
    with conn:
        repo.record_fact(
            conn, board_id=c["board_id"], domain=c["domain"], kind=c["kind"],
            fact_key=c["fact_key"], fact_value=c["fact_value"], source_type="DATASHEET",
            confidence=confidence or c["confidence"], source_id=c["source_id"],
            citation_page=c["page"], citation_section=c["section"], snippet=c["snippet"],
            verified_by_human=True)
        conn.execute("UPDATE fact_candidates SET status='confirmed' WHERE id=?", (candidate_id,))
    return "confirmed"


def reject_candidate(conn: sqlite3.Connection, candidate_id: int) -> str:
    c = repo.get_fact_candidate(conn, candidate_id)
    if c is None:
        return "not_found"
    if c["status"] != "pending":
        return "already"
    repo.set_candidate_status(conn, candidate_id, "rejected")
    return "rejected"
