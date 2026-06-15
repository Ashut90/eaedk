"""Golden evals for v3.0 P3 — behavioural hazard classes + the unset() DSL grammar extension."""
from eaedk.store.db import connect
from eaedk.store.migrate import migrate
from eaedk.seed import seed_all
from eaedk.orchestrator.orchestrator import assess
from eaedk.engines.risk.engine import eval_condition, DSLSyntaxError
import pytest


def _seeded(tmp_path):
    conn = connect(str(tmp_path / "t.db"))
    migrate(conn)
    seed_all(conn, force=True)
    return conn


def _risk(resp, key):
    return next((r for r in resp.risks if r["rule_key"] == key), None)


def _val(resp, key):
    return next((v for v in resp.validations if v["check"] == key), None)


# --- the grammar extension: unset()/set() presence predicates + parentheses ----------------

def test_p3_dsl_unset_predicate():
    assert eval_condition("unset(x) == 1", {}) is True             # absent -> unset
    assert eval_condition("unset(x) == 1", {"x": None}) is True    # None -> unset
    assert eval_condition("unset(x) == 1", {"x": 5}) is False      # present -> not unset
    assert eval_condition("set(x) == 1", {"x": 5}) is True
    assert eval_condition("set(x) == 1", {}) is False


def test_p3_dsl_unset_composes_with_and():
    ctx = {"isr_frequency_hz": 5000, "has_rtos": 1}                 # no isr_budget_us
    assert eval_condition(
        "isr_frequency_hz > 0 and has_rtos == 1 and unset(isr_budget_us) == 1", ctx) is True
    ctx["isr_budget_us"] = 40                                      # now it IS set
    assert eval_condition(
        "isr_frequency_hz > 0 and has_rtos == 1 and unset(isr_budget_us) == 1", ctx) is False


def test_p3_dsl_malformed_func_raises():
    with pytest.raises(DSLSyntaxError):
        eval_condition("unset(x == 1", {})                         # missing ')'


def test_p3_existing_rules_unaffected_by_grammar():
    # The single-op arithmetic rules still behave identically after the grammar extension.
    assert eval_condition("a * b > 5", {"a": 3, "b": 4}) is True
    assert eval_condition("a + b > 100", {"a": 3, "b": 4}) is False


# --- 3A: ISR timing deadline ---------------------------------------------------------------

def test_p3a_timing_deadline_high(tmp_path):
    conn = _seeded(tmp_path)
    resp = assess(conn, "bare_metal_app",
                  {"isr_frequency_hz": 10000, "isr_budget_us": 20}, board_name="STM32F103-BluePill")
    f = _risk(resp, "TIMING_DEADLINE")
    assert f is not None and f["severity"] == "HIGH"               # GOLDEN


def test_p3a_timing_deadline_rtos_medium_when_budget_unset(tmp_path):
    conn = _seeded(tmp_path)
    resp = assess(conn, "bare_metal_app",
                  {"isr_frequency_hz": 5000, "has_rtos": 1}, board_name="STM32F103-BluePill")
    f = _risk(resp, "TIMING_DEADLINE_RTOS")
    assert f is not None and f["severity"] == "MEDIUM"
    # and once a budget IS supplied, the RTOS prompt is silent (unset() is False)
    resp2 = assess(conn, "bare_metal_app",
                   {"isr_frequency_hz": 5000, "has_rtos": 1, "isr_budget_us": 200},
                   board_name="STM32F103-BluePill")
    assert _risk(resp2, "TIMING_DEADLINE_RTOS") is None


# --- 3B: power budget + supply voltage -----------------------------------------------------

def test_p3b_power_budget_high(tmp_path):
    conn = _seeded(tmp_path)
    resp = assess(conn, "bare_metal_app",
                  {"target_current_ma": 10}, board_name="STM32F103-BluePill")  # active ~50mA
    f = _risk(resp, "POWER_BUDGET")
    assert f is not None and f["severity"] == "HIGH"               # GOLDEN


def test_p3b_supply_below_min_voltage_fails(tmp_path):
    conn = _seeded(tmp_path)
    resp = assess(conn, "bare_metal_app",
                  {"supply_voltage_v": 2.5}, board_name="ESP32-DevKitC")        # min ~3.0V
    v = _val(resp, "POWER_SUPPLY_VOLTAGE")
    assert v is not None and v["status"] == "FAIL"                 # GOLDEN
    assert resp.feasibility == "not_feasible"                      # a hard, gating failure


def test_p3b_supply_voltage_ok_passes(tmp_path):
    conn = _seeded(tmp_path)
    resp = assess(conn, "bare_metal_app",
                  {"supply_voltage_v": 3.3}, board_name="STM32F103-BluePill")   # min 2.0V
    v = _val(resp, "POWER_SUPPLY_VOLTAGE")
    assert v is not None and v["status"] == "PASS"


# --- 3C: ISR-context stack overflow --------------------------------------------------------

def test_p3c_stack_overflow_isr_high(tmp_path):
    conn = _seeded(tmp_path)
    resp = assess(conn, "bare_metal_app",
                  {"isr_frequency_hz": 2000, "stack_size": 256}, board_name="STM32F103-BluePill")
    f = _risk(resp, "STACK_OVERFLOW_ISR")
    assert f is not None and f["severity"] == "HIGH"


# --- gate: no behavioural inputs -> all hazard rules silent --------------------------------

def test_p3_hazards_silent_without_inputs(tmp_path):
    conn = _seeded(tmp_path)
    resp = assess(conn, "bare_metal_app", {}, board_name="STM32F103-BluePill")
    for key in ("TIMING_DEADLINE", "TIMING_DEADLINE_RTOS", "POWER_BUDGET", "STACK_OVERFLOW_ISR"):
        assert _risk(resp, key) is None


# --- regression: boolean string inputs (has_rtos=true/false) must coerce, not error ---------

def test_p3_has_rtos_boolean_string_coerces(tmp_path):
    """has_rtos='false' (a string, as the CLI stores it) must read as 0 — not raise UnknownIdent
    and surface a contradictory 'missing input has_rtos' while it is plainly set."""
    conn = _seeded(tmp_path)
    # has_rtos=false -> the RTOS-tick rule simply does not apply (no UNKNOWN noise)
    resp = assess(conn, "bare_metal_app",
                  {"isr_frequency_hz": 3000, "isr_budget_us": 40, "has_rtos": "false",
                   "stack_size": 256}, board_name="STM32F103-BluePill")
    assert _risk(resp, "TIMING_DEADLINE_RTOS") is None
    # has_rtos=true (string) + no budget -> the rule fires MEDIUM (the boolean is honoured)
    resp2 = assess(conn, "bare_metal_app",
                   {"isr_frequency_hz": 5000, "has_rtos": "true"}, board_name="STM32F103-BluePill")
    f = _risk(resp2, "TIMING_DEADLINE_RTOS")
    assert f is not None and f["severity"] == "MEDIUM"
