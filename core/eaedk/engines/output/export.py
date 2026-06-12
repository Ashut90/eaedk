"""Engineering Output Engine — export structured deliverables as real files.

Gated on feasibility: a project that is not `feasible` is refused by default (the engineer can
`--force` a DRAFT). Reads through repo helpers + the orchestrator; writes files only. The truth
hierarchy carries into the artifacts via the generators (UNKNOWN -> explicit placeholder).
"""
from __future__ import annotations

import platform
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
    geometry_unknown: bool = False
    contaminated: bool = False          # Fix 6: folder already holds a different project's files
    existing_label: str = ""            # what the folder currently belongs to (for the message)


_MARKER = ".eaedk-export"
# Files that mark a folder as a previous EAEDK export, even without a marker (legacy/foreign dir).
_EAEDK_SIGNATURE_FILES = ("BRINGUP_CHECKLIST.md", "START_HERE.md", "CMakeLists.txt")


def _occupant(out: Path) -> str | None:
    """If ``out`` already holds another project's export, return a label for it, else None.

    Precise when a `.eaedk-export` marker is present (`project|board`); falls back to detecting
    EAEDK-generated files for a legacy/foreign folder written before markers existed.
    """
    marker = out / _MARKER
    if marker.exists():
        try:
            return marker.read_text(encoding="utf-8").strip() or "an earlier export"
        except OSError:
            return "an earlier export"
    if out.is_dir() and any((out / f).exists() for f in _EAEDK_SIGNATURE_FILES):
        return "an earlier export (no project marker)"
    return None


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
    facts: dict[str, str] = {}
    learning_path: list = []
    if board_name:
        facts = repo.board_facts_map(conn, board_name)
        from ...mentor import learning_path_for         # lazy (avoids import cycle)
        learning_path = learning_path_for(conn, repo.board_capability_names(conn, board_name))
    # Fix 1: the exact tools the user must install, in build-then-flash order (compiler,
    # build system, flash tool), so START_HERE can emit a concrete install command.
    by_kind = {r["kind"]: r["name"] for r in reqs}
    required_tools = [by_kind[k] for k in ("compiler", "build_system", "flash_tool")
                      if k in by_kind]
    # Fix 2: the real flash command — board -> SoC -> openocd target + the probe a beginner
    # most likely has, plus the full seeded probe map for the "other probes" table.
    flash_profile = dict(repo.soc_flash_profile_for(conn, board_name)) \
        if board_name and repo.soc_flash_profile_for(conn, board_name) else None
    probes = [dict(p) for p in repo.debug_probes(conn)]
    return {
        "project": project, "resp": resp, "board": board, "soc": soc,
        "board_name": board_name, "checklist": repo.checklist(conn, project["id"]),
        "reqs": reqs, "compiler_req": next((r for r in reqs if r["kind"] == "compiler"), None),
        "flash_req": flash_req, "detected_flash": detected_flash,
        "tracked": repo.list_risks_by_status(conn, project["id"], "tracked"),
        "facts": facts, "learning_path": learning_path,
        "host_os": platform.system(), "required_tools": required_tools,
        "flash_profile": flash_profile, "probes": probes,
    }


def _blockers(resp) -> list[str]:
    return [f"{v['check']} [{v['status']}]: {v['reason']}" for v in resp.validations
            if v.get("gating", True)
            and (v["status"] == "FAIL" or (v["status"] == "UNKNOWN" and v["engaged"]))]


_GEOMETRY_MSG = ("Board has no flash/RAM geometry — generated build files will NOT compile. "
                 "Run `eaedk ingest` on the datasheet, onboard the board with real flash/RAM "
                 "values, or use a seeded board first.")


def _geometry_unknown(board) -> bool:
    """Linker geometry can't be emitted (flash base/size missing) -> files won't build."""
    if not board:
        return True
    return board.get("flash_base") is None or board.get("flash_bytes") is None


def export_project(conn: sqlite3.Connection, project: sqlite3.Row, out_dir: str,
                   force: bool = False, only: str | None = None,
                   wokwi: bool = False) -> ExportResult:
    data = gather(conn, project)
    feas = data["resp"].feasibility
    if feas != "feasible" and not force:
        blockers = ([_GEOMETRY_MSG] if feas == "no_geometry" else _blockers(data["resp"]))
        return ExportResult(feasibility=feas, refused=True, blockers=blockers)

    arch = data["soc"]["arch"] if data["soc"] else None
    out = Path(out_dir)
    # Fix 6 (v1.9.2): never silently mix two projects' files in one folder. A marker matching
    # THIS project is fine (re-export); a different project/board (or a foreign export) is refused
    # unless --force.
    label = f"{project['name']}|{data['board_name']}"
    occupant = _occupant(out)
    if occupant is not None and occupant != label and not force:
        return ExportResult(feasibility=feas, contaminated=True, existing_label=occupant,
                            out_dir=str(out))
    files: dict[str, str] = {}

    if only in (None, "checklist"):
        files["BRINGUP_CHECKLIST.md"] = gen.render_checklist(data)
    if only in (None, "flash"):
        files["FLASH.md"] = gen.render_flash(data)
    if only in (None, "cmake"):
        from . import codegen
        if arch == "avr":
            # Fix 5 (v1.9.2): AVR (Arduino) uses avr-gcc/avrdude, not the ARM CMake/linker flow.
            # Emit a real blink+UART scaffold + a matching START_HERE so the mentor recipe's
            # `cat START_HERE.md` succeeds for the most common beginner board.
            files["src/main.c"] = codegen.render_avr_main_c(data)
            files["START_HERE.md"] = codegen.render_avr_start_here(data)
        elif (arch or "").startswith("xtensa") and data["project"]["goal_type"] == "bare_metal_app":
            # v2.2.0: ESP32 (Xtensa) builds with ESP-IDF; emit an ESP-IDF blink + a Wokwi-first
            # START_HERE so a no-hardware beginner can finish the journey in simulation.
            files["src/main.c"] = codegen.render_esp32_main_c(data)
            files["START_HERE.md"] = codegen.render_esp32_start_here(data)
        else:
            files["CMakeLists.txt"] = gen.render_cmake_lists(data)
            files["cmake/toolchain.cmake"] = gen.render_toolchain_cmake(data)
            if gen.is_mcu(arch):
                files["linker/memory.ld"] = gen.render_linker(data)
                # bare_metal_app gets working teach-commented code + a plain-language guide;
                # other goals keep the minimal stub.
                if data["project"]["goal_type"] == "bare_metal_app":
                    files["src/main.c"] = codegen.render_main_c(data)
                    files["START_HERE.md"] = codegen.render_start_here(data)
                else:
                    files["src/main.c"] = gen.render_main_c(data)

    # v2.1.0: optional Wokwi simulation files (default off; normal export unchanged).
    if wokwi and only is None:
        from .wokwi import wokwi_files
        files.update(wokwi_files(data["board_name"], data["soc"], data["project"]["name"]))

    written: list[str] = []
    for rel, content in files.items():
        path = out / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        written.append(str(path))
    # Fix 6: stamp the folder so a later export for a different project is caught. Not counted
    # in `written` (it's bookkeeping, not a deliverable).
    if written:
        (out / _MARKER).write_text(label, encoding="utf-8")
    # If geometry is missing, the linker is placeholders — flag it loudly (e.g. --force path).
    geometry_unknown = _geometry_unknown(data["board"]) and any(
        "linker" in w or "CMakeLists" in w for w in written)
    return ExportResult(feasibility=feas, written=sorted(written), out_dir=str(out),
                        geometry_unknown=geometry_unknown)
