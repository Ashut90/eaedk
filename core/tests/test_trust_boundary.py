"""Golden evals for the v2.7 trust-hardening refactor (P1, P2.5, P4B)."""
from eaedk.store.db import connect
from eaedk.store.migrate import migrate
from eaedk.seed import seed_all
from eaedk import repo
from eaedk.orchestrator.orchestrator import assess
from eaedk.engines.risk.engine import RiskRule, evaluate_risks, eval_condition
from eaedk.mentor_llm import mentor_chat, _NOT_FEASIBLE_BANNER


def _seeded(tmp_path):
    conn = connect(str(tmp_path / "t.db"))
    migrate(conn)
    seed_all(conn, force=True)
    return conn


# --- P1: the chat acknowledges a NOT-FEASIBLE hard limit BEFORE any optimisation prose -----

def test_p1_not_feasible_banner_precedes_optimization(tmp_path):
    conn = _seeded(tmp_path)
    repo.create_project(conn, "nn", "bare_metal_app", "STM32F103-BluePill")
    p = repo.get_project(conn, "nn")
    repo.set_input(conn, p["id"], "estimated_image_size", "200000", confidence="HIGH")
    out = mentor_chat(conn, "STM32F103-BluePill",
                      [{"role": "user", "content": "Can I run a gesture recognition neural network "
                        "on this board? I'll use a small model, maybe 200KB."}],
                      use_llm=False, project="nn")
    line1 = out.strip().splitlines()[0]
    assert "NOT FEASIBLE" in line1                         # the hard failure ack is line 1
    assert "FLASH_CAPACITY" in out                         # names the blocking rule, deterministically
    low = out.lower()
    assert "quantiz" not in low or low.index("not feasible") < low.index("quantiz")


def test_p1_no_banner_when_feasible(tmp_path):
    conn = _seeded(tmp_path)
    repo.create_project(conn, "ok", "bare_metal_app", "STM32F103-BluePill")
    out = mentor_chat(conn, "STM32F103-BluePill",
                      [{"role": "user", "content": "how do I blink an LED?"}],
                      use_llm=False, project="ok")
    assert _NOT_FEASIBLE_BANNER not in out                 # a feasible project gets no scary banner


# --- P2.5: FLASH endurance — high write rate to internal flash is a HIGH wear risk --------

def _risk(resp, key):
    return next((r for r in resp.risks if r["rule_key"] == key), None)


def test_p25_flash_endurance_internal_high(tmp_path):
    """write_rate=1000 to internal flash -> WARN HIGH, with erase-cycle math (the golden)."""
    conn = _seeded(tmp_path)
    resp = assess(conn, "bare_metal_app",
                  {"write_rate": 1000, "storage_target": "internal_flash"},
                  board_name="STM32F103-BluePill")
    internal = _risk(resp, "FLASH_ENDURANCE_INTERNAL")
    assert internal is not None and internal["severity"] == "HIGH"   # deterministic HIGH

    endurance = _risk(resp, "FLASH_ENDURANCE")
    assert endurance is not None and endurance["severity"] == "HIGH"  # 10K rating exceeded
    expl = endurance["explanation"].lower()
    assert "erase cycle" in expl and "10000" in endurance["explanation"]  # the math is shown


def test_p25_flash_endurance_silent_without_write_rate(tmp_path):
    """The endurance rules are gated on write_rate — no write_rate, no finding (no noise)."""
    conn = _seeded(tmp_path)
    resp = assess(conn, "bare_metal_app", {}, board_name="STM32F103-BluePill")
    assert _risk(resp, "FLASH_ENDURANCE") is None
    assert _risk(resp, "FLASH_ENDURANCE_INTERNAL") is None


def test_p25_flash_endurance_unknown_rating_is_medium(tmp_path):
    """When the board's endurance is UNCONFIRMED, a set write_rate still warns — at MEDIUM,
    not a dropped UNKNOWN."""
    conn = _seeded(tmp_path)
    resp = assess(conn, "bare_metal_app",
                  {"write_rate": 1000}, board_name="ESP32-DevKitC")   # esp32 rating = NULL
    endurance = _risk(resp, "FLASH_ENDURANCE")
    assert endurance is not None and endurance["severity"] == "MEDIUM"
    assert "unconfirmed" in endurance["explanation"].lower()


def test_p25_dsl_multi_operand_chain():
    """The grammar extension: a term is a left-assoc chain of any length (no precedence)."""
    ctx = {"a": 2, "b": 3, "c": 4}
    assert eval_condition("a * b * c == 24", ctx) is True          # 3 operands
    assert eval_condition("a + b + c == 9", ctx) is True
    assert eval_condition("a * b + c == 10", ctx) is True          # left-assoc: (2*3)+4
    # Existing single-op rules are unchanged.
    assert eval_condition("a * b > 5", ctx) is True


def test_p25_requires_gate_and_unknown_severity():
    """requires-gate skips entirely; severity_on_unknown turns an unresolved fact into a
    fired, lower-confidence warning rather than a dropped UNKNOWN."""
    rule = RiskRule("R", None, "x > board.cap", "HIGH", "exceeds {board.cap}",
                    "mit", requires=("x",), severity_on_unknown="MEDIUM")
    # No x -> gated out entirely (not even UNKNOWN).
    assert evaluate_risks({}, [rule], "g") == []
    # x present but board.cap unresolved -> fired MEDIUM, not dropped.
    fired = evaluate_risks({"x": 10}, [rule], "g")
    assert len(fired) == 1 and fired[0].severity == "MEDIUM" and fired[0].fired is True
