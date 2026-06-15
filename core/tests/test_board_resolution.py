"""v3.1 Gap 1 & 2 — board-name auto-coercion and the `board --capabilities` flag alias."""
from eaedk.store.db import connect
from eaedk.store.migrate import migrate
from eaedk.seed import seed_all
from eaedk import repo
from eaedk.cli import build_parser, cmd_board_root, cmd_board_capabilities


def _seeded(tmp_path):
    conn = connect(str(tmp_path / "t.db"))
    migrate(conn)
    seed_all(conn, force=True)
    return conn


# --- Gap 2: resolve_board_name -------------------------------------------------------------

def test_gap2_exact_match_is_exact(tmp_path):
    conn = _seeded(tmp_path)
    assert repo.resolve_board_name(conn, "STM32F103-BluePill") == ("STM32F103-BluePill", True)


def test_gap2_case_insensitive_is_exact(tmp_path):
    conn = _seeded(tmp_path)
    assert repo.resolve_board_name(conn, "nucleo-f411re") == ("Nucleo-F411RE", True)


def test_gap2_substring_coerces(tmp_path):
    conn = _seeded(tmp_path)
    assert repo.resolve_board_name(conn, "bluepill") == ("STM32F103-BluePill", False)
    assert repo.resolve_board_name(conn, "f411") == ("Nucleo-F411RE", False)
    assert repo.resolve_board_name(conn, "Blue Pill") == ("STM32F103-BluePill", False)


def test_gap2_fuzzy_typo_coerces(tmp_path):
    conn = _seeded(tmp_path)
    name, exact = repo.resolve_board_name(conn, "stm32f103-bluepil")    # one-char typo
    assert name == "STM32F103-BluePill" and exact is False


def test_gap2_garbage_returns_none(tmp_path):
    conn = _seeded(tmp_path)
    assert repo.resolve_board_name(conn, "xyzzy-nonsense") == (None, False)
    assert repo.resolve_board_name(conn, "") == (None, False)


# --- Gap 1: the `board --capabilities` flag form parses to the same handler -----------------

def test_gap1_flag_form_routes_to_capabilities():
    args = build_parser().parse_args(["board", "--capabilities", "--board", "bluepill"])
    assert args.func is cmd_board_root          # the board-root dispatcher
    assert args.capabilities is True and args.board == "bluepill"


def test_gap1_subcommand_form_still_parses():
    args = build_parser().parse_args(["board", "capabilities", "--board", "STM32F103-BluePill"])
    assert args.func is cmd_board_capabilities


def test_gap1_board_subcommands_unaffected():
    args = build_parser().parse_args(["board", "list"])
    assert args.func.__name__ == "cmd_board_list"
