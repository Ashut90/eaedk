"""Engineering Output Engine — export structured deliverables as real files.

Gated on feasibility: a project that is not `feasible` is refused by default (the engineer can
`--force` a DRAFT). Reads through repo helpers + the orchestrator; writes files only. The truth
hierarchy carries into the artifacts via the generators (UNKNOWN -> explicit placeholder).
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ... import repo
from ...orchestrator import assess_project
from . import generators as gen


@dataclass
class ExportResult:
    feasibility: str
    refused: bool = False
    blockers: list[str] = field(default_factory=list)
    written: list[str] = field(default_factory=list)
    out_dir: str = ""


def gather(conn: sqlite3.Connection, project: sqlite3.Row) -> dict[str, Any]:
    resp = assess_project(conn, project)
    board_name = repo.project_board_name(conn, project)
    board, soc = repo.load_board(conn, board_name) if board_name else (None, None)
    reqs = [dict(r) for r in repo.load_board_toolchain_reqs(conn, board_name)] if board_name else []
    detected = [dict(d) for d in repo.load_toolchain(conn)]
    flash_req = next((r for r in reqs if r["kind"] == "flash_tool"), None)
    detected_flash = None
    if flash_req:
        detected_flash = next((d for d in detected if d["name"] == flash_req["name"]), None)
    return {
        "project": project, "resp": resp, "board": board, "soc": soc,
        "board_name": board_name, "checklist": repo.checklist(conn, project["id"]),
        "reqs": reqs, "compiler_req": next((r for r in reqs if r["kind"] == "compiler"), None),
        "flash_req": flash_req, "detected_flash": detected_flash,
        "tracked": repo.list_risks_by_status(conn, project["id"], "tracked"),
    }


def _blockers(resp) -> list[str]:
    return [f"{v['check']} [{v['status']}]: {v['reason']}" for v in resp.validations
            if v.get("gating", True)
            and (v["status"] == "FAIL" or (v["status"] == "UNKNOWN" and v["engaged"]))]


def export_project(conn: sqlite3.Connection, project: sqlite3.Row, out_dir: str,
                   force: bool = False, only: str | None = None) -> ExportResult:
    data = gather(conn, project)
    feas = data["resp"].feasibility
    if feas != "feasible" and not force:
        return ExportResult(feasibility=feas, refused=True, blockers=_blockers(data["resp"]))

    arch = data["soc"]["arch"] if data["soc"] else None
    out = Path(out_dir)
    files: dict[str, str] = {}

    if only in (None, "checklist"):
        files["BRINGUP_CHECKLIST.md"] = gen.render_checklist(data)
    if only in (None, "flash"):
        files["FLASH.md"] = gen.render_flash(data)
    if only in (None, "cmake"):
        files["CMakeLists.txt"] = gen.render_cmake_lists(data)
        files["cmake/toolchain.cmake"] = gen.render_toolchain_cmake(data)
        if gen.is_mcu(arch):
            files["linker/memory.ld"] = gen.render_linker(data)
            files["src/main.c"] = gen.render_main_c(data)

    written: list[str] = []
    for rel, content in files.items():
        path = out / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        written.append(str(path))
    return ExportResult(feasibility=feas, written=sorted(written), out_dir=str(out))
