"""Tests for the interactive `project init` flow (scripted ask/out, no TTY)."""
from eaedk.store.db import connect
from eaedk.store.migrate import migrate
from eaedk.seed import seed_all
from eaedk import repo
from eaedk.project_init import run_project_init


def _seeded(tmp_path):
    conn = connect(str(tmp_path / "t.db"))
    migrate(conn)
    seed_all(conn, force=True)
    return conn


def _drive(conn, answers):
    it = iter(answers)
    out: list[str] = []
    name = run_project_init(conn, lambda p: next(it), out.append)
    return name, "\n".join(out)


def test_init_uboot_shows_blockers(tmp_path):
    conn = _seeded(tmp_path)
    name, out = _drive(conn, ["bringup", "STM32MP157", "3"])   # goal 3 = uboot
    assert name == "bringup"
    p = repo.get_project(conn, "bringup")
    assert p["goal_type"] == "uboot" and p["template_id"] is not None
    # immediate assessment ran, and the DDR HIGH-UNKNOWN is flagged as a blocker
    assert "Initial assessment" in out
    assert "YOU HAVE BLOCKERS" in out
    assert "DDR_TIMING_VERIFIED" in out


def test_init_bootloader_clean_no_blockers(tmp_path):
    conn = _seeded(tmp_path)
    name, out = _drive(conn, ["bl", "Nucleo-F411RE", "2"])     # goal 2 = bootloader
    assert name == "bl"
    assert "Initial assessment" in out
    assert "YOU HAVE BLOCKERS" not in out                       # nothing engaged yet


def test_init_custom_goal_is_templateless(tmp_path):
    conn = _seeded(tmp_path)
    # Custom is the last menu option (after the 8 templated goals) -> index 9, then an identifier.
    # (v1.8.0 added the multicore + low_power templates, shifting Custom from 7 to 9; the option
    # itself is unchanged — still the templateless escape hatch at the end of the list.)
    name, out = _drive(conn, ["c1", "Nucleo-F411RE", "9", "secure_enclave_bringup"])
    assert name == "c1"
    p = repo.get_project(conn, "c1")
    assert p["goal_type"] == "secure_enclave_bringup"
    assert p["template_id"] is None                             # template-less
    # global rules still run, so the assessment still prints
    assert "Initial assessment" in out


def test_init_aborts_with_no_boards(tmp_path):
    conn = connect(str(tmp_path / "t.db"))
    migrate(conn)                                               # migrate only, no seed
    name, out = _drive(conn, ["whatever"])
    assert name is None
    assert "no boards onboarded" in out.lower()


def test_init_rejects_duplicate_name(tmp_path):
    conn = _seeded(tmp_path)
    repo.create_project(conn, "dup", "bootloader", "Nucleo-F411RE")
    name, out = _drive(conn, ["dup"])
    assert name is None
    assert "already exists" in out
