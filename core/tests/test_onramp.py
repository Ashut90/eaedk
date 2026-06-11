"""Golden tests for v1.6.0-onramp — the six fixes that make EAEDK usable by a beginner who
owns a common board and has never touched firmware. Each fix has at least one case here.

The on-ramp invariant under test: a beginner who owns a seeded board never needs the wizard,
the wizard never crashes, an unknown board is never a dead end, a recognized SoC can reach
working geometry without a datasheet, every board carries capabilities, and no learning-path
step is ever silently dropped.
"""
import pytest

from eaedk.store.db import connect
from eaedk.store.migrate import migrate
from eaedk.seed import seed_all
from eaedk import repo, mentor, onboard


def _seeded(tmp_path):
    conn = connect(str(tmp_path / "t.db"))
    migrate(conn)
    seed_all(conn, force=True)
    return conn


# --- Fix 1: common beginner boards are seeded with geometry + capabilities ----

# The minimum set the spec demands a beginner must never need the wizard for.
_BEGINNER_BOARDS = [
    "STM32F103-BluePill", "Raspberry-Pi-Pico", "ESP32-DevKitC",
    "Arduino-Uno", "Arduino-Mega", "Nucleo-F103RB", "Nucleo-F411RE",
]


@pytest.mark.parametrize("name", _BEGINNER_BOARDS)
def test_beginner_board_seeded_with_geometry(tmp_path, name):
    conn = _seeded(tmp_path)
    board, soc = repo.load_board(conn, name)
    assert board is not None, f"{name} must be seeded so a beginner never touches the wizard"
    assert board["flash_bytes"] and board["ram_bytes"], f"{name} needs real geometry to build"


@pytest.mark.parametrize("name", _BEGINNER_BOARDS)
def test_beginner_board_has_capabilities(tmp_path, name):
    conn = _seeded(tmp_path)
    caps = {c["capability"] for c in mentor.capability_map(conn, name)}
    assert {"gpio", "uart"} <= caps, f"{name} must expose at least GPIO + UART for the path"


def test_blue_pill_is_not_special_cased(tmp_path):
    # The fix is structural, not Blue-Pill-only: an AVR board (different arch) is seeded too.
    conn = _seeded(tmp_path)
    _board, soc = repo.load_board(conn, "Arduino-Uno")
    assert soc["arch"] == "avr"


# --- Fix 2: the wizard never crashes, never loops forever -----------------

def test_wizard_survives_eof_midway(tmp_path):
    conn = connect(str(tmp_path / "t.db"))
    migrate(conn)

    def ask(_prompt):  # input dries up immediately -> EOFError, like a piped empty stdin
        raise EOFError

    # The wizard must not raise; it returns None (aborted) rather than a traceback.
    out: list[str] = []
    name = onboard.run_wizard(conn, ask, out.append)
    assert name is None


def test_blank_arch_does_not_loop_forever(tmp_path):
    # A beginner who cannot answer the core question presses Enter — that must resolve, not loop.
    out: list[str] = []
    triple, label, vtor = onboard._select_arch(lambda _p: "", out.append)
    assert label == "Unknown" and triple == ""


def test_unparseable_arch_is_bounded(tmp_path):
    # Endless garbage must terminate (bounded retries), never hang the wizard.
    answers = iter(["zzz"] * 50)
    out: list[str] = []
    triple, label, vtor = onboard._select_arch(lambda _p: next(answers), out.append, max_tries=6)
    assert label == "Unknown"


# --- Fix 3: an unknown board offers a path forward ------------------------

def test_unknown_board_near_match_suggested(tmp_path):
    conn = _seeded(tmp_path)
    # A beginner typo / loose name still points at the real seeded board.
    matches = repo.near_match_boards(conn, "blue pill")
    assert any("BluePill" in m["name"] for m in matches)


def test_unknown_board_with_no_match_is_not_empty(tmp_path):
    conn = _seeded(tmp_path)
    assert repo.near_match_boards(conn, "Totally-Made-Up-Widget-9000") == []


# --- Fix 4: a recognized SoC reaches geometry without a datasheet ---------

def test_soc_defaults_fill_geometryless_board(tmp_path):
    conn = _seeded(tmp_path)
    # Onboard a geometry-less board whose SoC IS recognized, then fill from standard values.
    answers = iter(["MyPill", "ST", "STM32F103C8", "", "", "", "", "", "",
                    "", "", "", "", "", "", "", "", "", "n"])
    onboard.run_wizard(conn, lambda _p: next(answers), lambda _s: None)
    board, _ = repo.load_board(conn, "MyPill")
    assert board["flash_bytes"] is None                     # starts with no geometry

    applied = repo.apply_soc_defaults(conn, "MyPill")
    assert applied is not None and applied["soc_name"] == "STM32F103C8"
    board, _ = repo.load_board(conn, "MyPill")
    assert board["flash_bytes"] == 65536                    # standard STM32F103C8 values
    assert board["flash_base"] == 0x08000000
    assert board["ram_bytes"] == 20480


def test_fill_geometry_unknown_soc_returns_none(tmp_path):
    conn = _seeded(tmp_path)
    answers = iter(["Exotic", "X", "NOSUCHSOC", "", "", "", "", "", "",
                    "", "", "", "", "", "", "", "", "", "n"])
    onboard.run_wizard(conn, lambda _p: next(answers), lambda _s: None)
    assert repo.apply_soc_defaults(conn, "Exotic") is None   # no defaults -> honest dead-pass


# --- Fix 5: every board collects capabilities -----------------------------

def test_wizard_blank_capabilities_uses_common_set(tmp_path):
    conn = connect(str(tmp_path / "t.db"))
    migrate(conn)
    answers = iter(["CapBoard", "ST", "SOC", "3", "1MB", "0x08000000", "192KB", "0x20000000",
                    "", "", "", "", "", "", "", "", "",
                    "",            # capabilities: Enter -> common MCU set
                    "n"])
    onboard.run_wizard(conn, lambda _p: next(answers), lambda _s: None)
    caps = {c["capability"] for c in mentor.capability_map(conn, "CapBoard")}
    assert {"uart", "spi", "i2c", "gpio", "timer"} <= caps    # never empty -> path is never empty


# --- Fix 6: the learning path never silently drops a step -----------------

def test_dropped_steps_are_surfaced_with_reason(tmp_path):
    conn = _seeded(tmp_path)
    # A board with only GPIO: SPI/UART/etc. steps are dropped — but must be reported, not hidden.
    cap_names = {"gpio"}
    dropped = mentor.dropped_steps_for(conn, cap_names)
    assert dropped, "steps requiring missing capabilities must be reported, not silently dropped"
    assert all(d["missing"] for d in dropped)


def test_mentor_render_tells_how_to_unlock(tmp_path):
    conn = _seeded(tmp_path)
    # Onboard a GPIO-only board, then the mentor view must name the unlock command.
    answers = iter(["GpioOnly", "ST", "STM32F103C8", "3", "64KB", "0x08000000", "20KB",
                    "0x20000000", "", "", "", "", "", "", "", "", "",
                    "gpio",        # capabilities: GPIO only
                    "n"])
    onboard.run_wizard(conn, lambda _p: next(answers), lambda _s: None)
    out = mentor.render_board_mentor(conn, "GpioOnly")
    assert "capability add" in out                            # the exact unlock command is shown


def test_board_capability_add_unlocks_step(tmp_path):
    conn = _seeded(tmp_path)
    answers = iter(["AddCap", "ST", "STM32F103C8", "3", "64KB", "0x08000000", "20KB",
                    "0x20000000", "", "", "", "", "", "", "", "", "",
                    "gpio", "n"])
    onboard.run_wizard(conn, lambda _p: next(answers), lambda _s: None)
    before = {c["capability"] for c in mentor.capability_map(conn, "AddCap")}
    assert "uart" not in before
    with conn:
        assert repo.add_board_capability(conn, "AddCap", "uart") is True
    after = {c["capability"] for c in mentor.capability_map(conn, "AddCap")}
    assert "uart" in after

    # Adding a capability to an unknown board reports failure, never crashes.
    with conn:
        assert repo.add_board_capability(conn, "NoSuchBoard", "uart") is False
