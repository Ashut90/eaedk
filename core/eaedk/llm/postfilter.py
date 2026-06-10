"""Uncited-claim post-filter (spec §4.3) — the structural enforcement of the truth hierarchy.

Builds an allowlist of *cited* numeric values from SQLite (board fields, human-verified
facts, and engineer-provided project inputs), then removes any sentence that asserts a
hardware fact — a hex address, a memory size, a clock frequency, or a timing value — whose
number is not in that allowlist.

Conservative by design: a sentence with any un-allowed hardware number is stripped whole and
replaced with a marker. Frequencies and timings are never stored in the MVP DB, so any such
assertion is removed — the LLM must not invent clock/timing values. Better to drop a
true-but-uncited claim than to let an invented one through.
"""
from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from typing import Any

from ..context import as_int
from .. import repo

REMOVED_MARKER = "[uncited claim removed — verify against TRM]"

_HEX = re.compile(r"0x[0-9a-fA-F]+")
_SIZE = re.compile(r"\b(\d+(?:\.\d+)?)\s?(KB|KiB|MB|MiB|GB|GiB|B)\b")
_FREQ = re.compile(r"\b(\d+(?:\.\d+)?)\s?(kHz|MHz|GHz)\b", re.IGNORECASE)
_TIME = re.compile(r"\b(\d+(?:\.\d+)?)\s?(ns|ps|us|µs|ms)\b", re.IGNORECASE)
_SENT = re.compile(r"(?<=[.!?])\s+")

# unit -> candidate byte multipliers (both binary and decimal where ambiguous)
_SIZE_FACTORS: dict[str, list[int]] = {
    "b": [1],
    "kb": [1000, 1024], "kib": [1024],
    "mb": [1_000_000, 1024 * 1024], "mib": [1024 * 1024],
    "gb": [1_000_000_000, 1024 ** 3], "gib": [1024 ** 3],
}


@dataclass
class Allowlist:
    numbers: set[int] = field(default_factory=set)


def _collect_ints(value: Any, out: set[int]) -> None:
    """Recursively collect integer values from scalars / regions / partitions."""
    if isinstance(value, bool):
        return
    if isinstance(value, int):
        out.add(value)
        return
    if isinstance(value, str):
        s = value.strip()
        if s[:1] in "[{":  # a JSON-encoded region/partition stored as text
            try:
                _collect_ints(json.loads(s), out)
                return
            except json.JSONDecodeError:
                pass
        iv = as_int(value)
        if iv is not None:
            out.add(iv)
        return
    if isinstance(value, dict):
        for v in value.values():
            _collect_ints(v, out)
    elif isinstance(value, list):
        for v in value:
            _collect_ints(v, out)


def build_allowlist(conn: sqlite3.Connection, project: sqlite3.Row) -> Allowlist:
    nums: set[int] = set()

    board_name = repo.project_board_name(conn, project)
    if board_name:
        board, _soc = repo.load_board(conn, board_name)
        if board:
            for k in ("flash_base", "flash_bytes", "ram_base", "ram_bytes", "ddr_bytes"):
                v = board.get(k)
                if isinstance(v, int):
                    nums.add(v)
        # Cited facts read through the unified engineering_facts VIEW (provenance preserved
        # via citation_id). For partition facts, also admit the ABSOLUTE address
        # (flash_base + offset) so verified slot boundaries the LLM cites aren't stripped.
        flash_base = board.get("flash_base") if board else None
        for row in conn.execute(
            "SELECT ef.kind, ef.fact_value FROM engineering_facts ef "
            "JOIN boards b ON b.id = ef.board_id "
            "WHERE b.name = ? AND ef.citation_id IS NOT NULL", (board_name,)).fetchall():
            _collect_ints(row["fact_value"], nums)
            if row["kind"] == "partition" and isinstance(flash_base, int):
                try:
                    base = int(json.loads(row["fact_value"]).get("base"))
                    nums.add(flash_base + base)
                except (ValueError, TypeError, AttributeError):
                    pass

    inputs, _conf = repo.load_inputs(conn, project["id"])
    for v in inputs.values():
        _collect_ints(v, nums)

    return Allowlist(numbers=nums)


_DEC_RE = re.compile(r"\b\d{3,}\b")


def numbers_in_text(text: str) -> set[int]:
    """Collect hex literals and 3+ digit decimals appearing in provided text (e.g. a log
    evidence window). These count as 'cited by the source' so the model may quote them."""
    nums: set[int] = set()
    for m in _HEX.finditer(text):
        nums.add(int(m.group(), 16))
    for m in _DEC_RE.finditer(text):
        nums.add(int(m.group()))
    return nums


def _size_allowed(num: str, unit: str, allow: Allowlist) -> bool:
    n = float(num)
    for f in _SIZE_FACTORS.get(unit.lower(), []):
        if int(round(n * f)) in allow.numbers:
            return True
    return False


def _sentence_ok(sentence: str, allow: Allowlist) -> tuple[bool, bool]:
    """Returns (all_hw_numbers_allowed, had_any_hw_number)."""
    had = False
    for m in _HEX.finditer(sentence):
        had = True
        if int(m.group(), 16) not in allow.numbers:
            return False, True
    for m in _SIZE.finditer(sentence):
        had = True
        if not _size_allowed(m.group(1), m.group(2), allow):
            return False, True
    # Frequencies and timings are never in the MVP knowledge base -> always uncited.
    if _FREQ.search(sentence) or _TIME.search(sentence):
        return False, True
    return True, had


def filter_text(text: str, allow: Allowlist) -> tuple[str, int]:
    """Strip sentences asserting un-allowed hardware numbers. Returns (filtered, removed)."""
    removed = 0
    out_lines: list[str] = []
    for line in text.splitlines():
        if not line.strip():
            out_lines.append(line)
            continue
        kept: list[str] = []
        for sent in _SENT.split(line):
            ok, had = _sentence_ok(sent, allow)
            if had and not ok:
                kept.append(REMOVED_MARKER)
                removed += 1
            else:
                kept.append(sent)
        out_lines.append(" ".join(kept))
    return "\n".join(out_lines), removed
