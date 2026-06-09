"""Unit tests for the deterministic engines and the golden eval suite."""
import pytest

from eaedk.context import build_context, as_int
from eaedk.engines.validation.rules import run_validations, feasibility
from eaedk.engines.risk.engine import eval_condition, UnknownIdent, DSLSyntaxError
from eaedk.store.db import connect
from eaedk.store.migrate import migrate
from eaedk.seed import seed_all
from eaedk.eval_runner import run_eval


F411 = {"flash_base": 0x08000000, "flash_bytes": 524288,
        "ram_base": 0x20000000, "ram_bytes": 131072}
M4 = {"arch": "arm-cortex-m4"}
M0 = {"arch": "arm-cortex-m0plus"}


def _statuses(inputs, board, soc, goal):
    ctx = build_context(inputs, board, soc, goal)
    return {r.check: r.status for r in run_validations(ctx, goal)}, ctx


def test_as_int_hex_and_dec():
    assert as_int("0x10000000") == 0x10000000
    assert as_int("123") == 123
    assert as_int(456) == 456
    assert as_int("nope") is None


def test_flash_capacity_fail_and_pass():
    s, _ = _statuses({"estimated_image_size": 600000}, F411, M4, "bootloader")
    assert s["FLASH_CAPACITY"] == "FAIL"
    s, _ = _statuses({"estimated_image_size": 32768}, F411, M4, "bootloader")
    assert s["FLASH_CAPACITY"] == "PASS"


def test_vector_table_alignment_is_arch_dependent():
    # 0x...100 (256-aligned) passes on M0+, fails on M4 (needs 512).
    s, _ = _statuses({"vector_table_addr": 0x10000100},
                     {"flash_base": 0x10000000, "flash_bytes": 0x200000}, M0, "bootloader")
    assert s["VECTOR_TABLE_PLACEMENT"] == "PASS"
    s, _ = _statuses({"vector_table_addr": 0x08000100}, F411, M4, "bootloader")
    assert s["VECTOR_TABLE_PLACEMENT"] == "FAIL"


def test_unknown_blocks_only_when_engaged():
    # No toolchain provided -> TOOLCHAIN UNKNOWN but un-engaged -> still feasible.
    inputs = {"estimated_image_size": 1024, "vector_table_addr": 0x08000000,
              "bl_region": {"base": 0x08000000, "size": 0x4000},
              "app_region": {"base": 0x08004000, "size": 0x1000},
              "stack_size": 1024, "heap_size": 1024, "static_size": 1024}
    ctx = build_context(inputs, F411, M4, "bootloader")
    res = run_validations(ctx, "bootloader")
    assert feasibility(res) == "feasible"


def test_ddr_unverified_is_unknown_not_fail():
    ctx = build_context({}, {"ram_base": 0xC0000000, "ram_bytes": 0x20000000}, {"arch": "arm-cortex-a7"}, "uboot")
    res = {r.check: r.status for r in run_validations(ctx, "uboot")}
    assert res["DDR_TIMING_VERIFIED"] == "UNKNOWN"


def test_dsl_eval_and_errors():
    assert eval_condition("estimated_image_size > board.flash_bytes * 0.9",
                          {"estimated_image_size": 600000, "board.flash_bytes": 524288}) is True
    assert eval_condition("watchdog_enabled == 0", {"watchdog_enabled": 0}) is True
    with pytest.raises(UnknownIdent):
        eval_condition("missing_key > 1", {})
    with pytest.raises(DSLSyntaxError):
        eval_condition("1 1 1", {})


def test_pinmux_conflict_detection():
    # Same pin claimed by two signals -> FAIL; distinct pins -> PASS.
    s, _ = _statuses({"pin_assignments": [
        {"pin": "PA9", "signal": "USART1_TX"},
        {"pin": "PA9", "signal": "TIM1_CH2"}]}, None, M4, "driver")
    assert s["PINMUX_CONFLICT"] == "FAIL"
    s, _ = _statuses({"pin_assignments": [
        {"pin": "PA9", "signal": "USART1_TX"},
        {"pin": "PA10", "signal": "USART1_RX"}]}, None, M4, "driver")
    assert s["PINMUX_CONFLICT"] == "PASS"


def test_power_sequence_dependency_ordering():
    # Dependency must power up strictly first.
    bad = {"power_rails": [
        {"name": "VDD_IO", "order": 1, "depends_on": "VDD_CORE"},
        {"name": "VDD_CORE", "order": 2}]}
    s, _ = _statuses(bad, None, M4, "bootloader")
    assert s["POWER_SEQUENCE"] == "FAIL"
    good = {"power_rails": [
        {"name": "VDD_CORE", "order": 1},
        {"name": "VDD_IO", "order": 2, "depends_on": "VDD_CORE"}]}
    s, _ = _statuses(good, None, M4, "bootloader")
    assert s["POWER_SEQUENCE"] == "PASS"
    # Duplicate order indices are ambiguous -> FAIL.
    dup = {"power_rails": [{"name": "A", "order": 1}, {"name": "B", "order": 1}]}
    s, _ = _statuses(dup, None, M4, "bootloader")
    assert s["POWER_SEQUENCE"] == "FAIL"


def test_golden_eval_suite_all_pass(tmp_path):
    db = tmp_path / "t.db"
    conn = connect(str(db))
    migrate(conn)
    seed_all(conn, force=True)
    res = run_eval(conn)
    assert res["failed"] == 0, res["cases"]
    assert res["total"] == 11
