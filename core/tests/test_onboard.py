"""Tests for the interactive board onboarding wizard (no TTY; scripted ask/out)."""
import json

from eaedk.store.db import connect
from eaedk.store.migrate import migrate
from eaedk import repo
from eaedk.onboard import run_wizard, parse_mem


def _drive(conn, answers):
    it = iter(answers)
    out_lines: list[str] = []
    name = run_wizard(conn, lambda prompt: next(it), out_lines.append)
    return name, out_lines


def test_parse_mem_units_and_hex():
    assert parse_mem("0x08000000") == 0x08000000
    assert parse_mem("2MB") == 2 * 1024 * 1024
    assert parse_mem("512KB") == 512 * 1024
    assert parse_mem("131072") == 131072
    assert parse_mem("") is None
    assert parse_mem("garbage") is None


def test_successful_terminal_onboard_pass(tmp_path):
    conn = connect(str(tmp_path / "t.db"))
    migrate(conn)

    answers = [
        "MyH743",          # board name
        "STMicro",         # vendor
        "STM32H743ZI",     # soc name
        "4",               # arch select -> Cortex-M7 (VTOR 512)
        "2MB",             # flash size  -> 2097152
        "0x08000000",      # flash base
        "1MB",             # ram size    -> 1048576
        "0x20000000",      # ram base
        # partitions: bootloader + A/B/C, offsets within 2 MiB, aligned, non-overlapping
        "0x0", "0x20000",       # Bootloader  off 0,        size 128 KiB
        "0x20000", "0x80000",   # Slot A      off 0x20000,  size 512 KiB
        "0xA0000", "0x80000",   # Slot B      off 0xA0000,  size 512 KiB
        "0x120000", "0x80000",  # Slot C      off 0x120000, size 512 KiB
    ]
    name, out = _drive(conn, answers)

    assert name == "MyH743"
    board, soc = repo.load_board(conn, "MyH743")
    assert board is not None
    assert board["flash_bytes"] == 2 * 1024 * 1024
    assert board["flash_base"] == 0x08000000
    assert board["ram_bytes"] == 1024 * 1024
    assert soc["arch"] == "arm-cortex-m7"
    assert board["confidence"] == "HIGH"          # all core fields known

    parts = conn.execute(
        "SELECT key, value FROM facts WHERE kind='partition' ORDER BY key").fetchall()
    assert {p["key"] for p in parts} == {"bootloader", "slot_a", "slot_b", "slot_c"}
    boot = next(json.loads(p["value"]) for p in parts if p["key"] == "bootloader")
    assert boot == {"base": 0x0, "size": 0x20000}

    blob = "\n".join(out)
    assert "Board onboarded: MyH743" in blob
    assert "VTOR 512B" in blob                     # arch binding surfaced
    assert "Confidence   HIGH" in blob


def test_blank_field_drops_to_medium_and_writes_null(tmp_path):
    conn = connect(str(tmp_path / "t.db"))
    migrate(conn)
    answers = [
        "PartialBoard", "ST", "SOC", "3",
        "", "0x08000000", "512KB", "0x20000000",   # flash size left blank -> UNKNOWN
        "", "", "", "", "", "", "", "",            # no partitions
    ]
    name, out = _drive(conn, answers)
    assert name == "PartialBoard"
    board, _ = repo.load_board(conn, "PartialBoard")
    assert board["flash_bytes"] is None            # null written
    assert board["confidence"] == "MEDIUM"         # downgraded
    assert "Confidence   MEDIUM" in "\n".join(out)


def test_two_boards_can_share_a_soc(tmp_path):
    conn = connect(str(tmp_path / "t.db"))
    migrate(conn)
    base = ["{n}", "ST", "SharedSoC", "4", "2MB", "0x08000000", "1MB", "0x20000000",
            "", "", "", "", "", "", "", ""]
    for n in ("BoardOne", "BoardTwo"):
        _drive(conn, [a.replace("{n}", n) for a in base])
    assert conn.execute("SELECT COUNT(*) FROM socs WHERE name='SharedSoC'").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM boards").fetchone()[0] == 2


def test_overlap_is_caught_and_reprompted(tmp_path):
    conn = connect(str(tmp_path / "t.db"))
    migrate(conn)

    answers = [
        "OverlapBoard", "ST", "SOC", "4",
        "2MB", "0x08000000", "1MB", "0x20000000",
        # First attempt: Slot B overlaps Slot C (B ends at 0x120000, C starts at 0x100000)
        "0x0", "0x20000",       # Bootloader
        "0x20000", "0x80000",   # Slot A
        "0xA0000", "0x80000",   # Slot B -> ends 0x120000
        "0x100000", "0x80000",  # Slot C -> starts 0x100000  (OVERLAP)
        # Reprompt (Enter keeps shown value, fix only Slot C base)
        "", "",                 # Bootloader unchanged
        "", "",                 # Slot A unchanged
        "", "",                 # Slot B unchanged
        "0x120000", "",         # Slot C base fixed; size kept
    ]
    name, out = _drive(conn, answers)
    blob = "\n".join(out)

    assert name == "OverlapBoard"
    assert "Error:" in blob and "overlap" in blob.lower()
    # After the fix it committed with HIGH confidence and 4 partitions.
    board, _ = repo.load_board(conn, "OverlapBoard")
    assert board["confidence"] == "HIGH"
    n = conn.execute("SELECT COUNT(*) FROM facts WHERE kind='partition'").fetchone()[0]
    assert n == 4
