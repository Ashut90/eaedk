"""Golden tests for v1.9.2-readme-door — the six walls a first-time user hit (docs/15-readme-door).

F1 README quickstart, F2 eaedk entry point, F3 board-discovery message, F4 db-seed message,
F5 AVR export produces a real START_HERE, F6 export won't silently mix two projects' files.
"""
from pathlib import Path

import pytest

from eaedk.store.db import connect
from eaedk.store.migrate import migrate
from eaedk.seed import seed_all
from eaedk import repo
from eaedk.cli import _unknown_board
from eaedk.engines.output import export_project

_REPO = Path(__file__).resolve().parents[2]


def _seeded(tmp_path):
    conn = connect(str(tmp_path / "t.db"))
    migrate(conn)
    seed_all(conn, force=True)
    return conn


# --- F1: README getting-started block --------------------------------------

def test_readme_quickstart_is_exact_and_jargon_free():
    md = (_REPO / "README.md").read_text(encoding="utf-8")
    assert "## Getting started" in md
    # the exact 9-step sequence, in order, with no PYTHONPATH / python3 -m
    for cmd in ["git clone https://github.com/Ashut90/eaedk", "cd eaedk",
                "python3 -m venv .venv", "source .venv/bin/activate", "pip install -e .",
                "eaedk db init", "eaedk db seed", "eaedk board list",
                "eaedk mentor --board STM32F103-BluePill"]:
        assert cmd in md, cmd
    gs = md.split("## Getting started", 1)[1].split("## ", 1)[0]
    assert "PYTHONPATH" not in gs and "python3 -m eaedk.cli" not in gs


# --- F2: the eaedk command exists after install ----------------------------

def test_eaedk_console_script_is_declared():
    pp = (_REPO / "pyproject.toml").read_text(encoding="utf-8")
    assert "[project.scripts]" in pp
    assert 'eaedk = "eaedk.cli:main"' in pp


# --- F3: board discovery ----------------------------------------------------

def test_unknown_board_points_at_board_list(tmp_path, capsys):
    conn = _seeded(tmp_path)
    _unknown_board(conn, "totally-made-up-board")
    err = capsys.readouterr().err
    assert "Board not found" in err
    assert "eaedk board list" in err
    assert "board add --interactive" in err


def test_readme_lists_board_list_before_mentor():
    md = (_REPO / "README.md").read_text(encoding="utf-8")
    assert md.index("eaedk board list") < md.index("eaedk mentor --board STM32F103-BluePill")


# --- F4: db seed message ----------------------------------------------------

def test_db_seed_already_seeded_message_names_force(tmp_path):
    conn = _seeded(tmp_path)                       # first seed succeeds
    with pytest.raises(RuntimeError) as e:
        seed_all(conn, force=False)                # second seed must guide, not confuse
    msg = str(e.value)
    assert "already seeded" in msg.lower()
    assert "eaedk db seed --force" in msg
    assert "force=True" not in msg                 # the old, wrong instruction is gone


# --- F5: AVR export produces a real START_HERE ------------------------------

def test_avr_export_produces_start_here_and_scaffold(tmp_path):
    conn = _seeded(tmp_path)
    repo.create_project(conn, "uno", "bare_metal_app", "Arduino-Uno")
    out = tmp_path / "uno-fw"
    res = export_project(conn, repo.get_project(conn, "uno"), str(out))
    assert not res.refused and not res.contaminated
    # the file the mentor recipe ends on must now exist
    assert (out / "START_HERE.md").exists()
    assert (out / "src" / "main.c").exists()
    sh = (out / "START_HERE.md").read_text()
    # correct AVR toolchain, NOT the ARM cross-compiler
    assert "avr-gcc" in sh and "avrdude" in sh
    assert "arm-none-eabi" not in sh and "arm-none-eabi" not in (out / "src" / "main.c").read_text()
    main_c = (out / "src" / "main.c").read_text()
    assert "<avr/io.h>" in main_c and "DDRB" in main_c   # idiomatic, real registers


def test_arm_export_unchanged_by_avr_branch(tmp_path):
    # Preservation: a Cortex-M board still gets the full ARM bundle exactly as before.
    conn = _seeded(tmp_path)
    repo.create_project(conn, "bp", "bare_metal_app", "STM32F103-BluePill")
    out = tmp_path / "bp-fw"
    export_project(conn, repo.get_project(conn, "bp"), str(out))
    for rel in ("START_HERE.md", "src/main.c", "linker/memory.ld", "CMakeLists.txt",
                "cmake/toolchain.cmake"):
        assert (out / rel).exists(), rel
    assert "arm-none-eabi" in (out / "cmake/toolchain.cmake").read_text()


# --- F6: export folder cross-contamination ---------------------------------

def test_export_refuses_to_mix_two_projects(tmp_path):
    conn = _seeded(tmp_path)
    repo.create_project(conn, "uno", "bare_metal_app", "Arduino-Uno")
    repo.create_project(conn, "bp", "bare_metal_app", "STM32F103-BluePill")
    out = tmp_path / "shared"
    # first project claims the folder
    r1 = export_project(conn, repo.get_project(conn, "uno"), str(out))
    assert r1.written and not r1.contaminated
    # a DIFFERENT project into the same folder is refused (not silently mixed)
    r2 = export_project(conn, repo.get_project(conn, "bp"), str(out))
    assert r2.contaminated and not r2.written
    assert "Arduino-Uno" in r2.existing_label
    # the Blue Pill files were NOT written
    assert "STM32F103-BluePill" not in (out / "START_HERE.md").read_text()


def test_export_same_project_reexport_is_allowed(tmp_path):
    conn = _seeded(tmp_path)
    repo.create_project(conn, "uno", "bare_metal_app", "Arduino-Uno")
    out = tmp_path / "uno-fw"
    export_project(conn, repo.get_project(conn, "uno"), str(out))
    r2 = export_project(conn, repo.get_project(conn, "uno"), str(out))   # same project again
    assert not r2.contaminated and r2.written


def test_export_force_overrides_contamination(tmp_path):
    conn = _seeded(tmp_path)
    repo.create_project(conn, "uno", "bare_metal_app", "Arduino-Uno")
    repo.create_project(conn, "bp", "bare_metal_app", "STM32F103-BluePill")
    out = tmp_path / "shared"
    export_project(conn, repo.get_project(conn, "uno"), str(out))
    r = export_project(conn, repo.get_project(conn, "bp"), str(out), force=True)
    assert not r.contaminated and r.written
    assert "STM32F103-BluePill" in (out / "START_HERE.md").read_text()
