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


# --- P3: topic-aware 'Try this' — curated experiments, suppression, no repeats -------------

def test_p3_career_suppresses_try_this_and_gives_roadmap(tmp_path):
    conn = _seeded(tmp_path)
    out = mentor_chat(conn, "STM32F103-BluePill",
                      [{"role": "user", "content": "how do I get a job in embedded systems?"}],
                      use_llm=False)
    assert "Try this:" not in out                       # no canned experiment for a career question
    assert "sequence" in out.lower() or "roadmap" in out.lower()   # a learning roadmap instead


def test_p3_ml_inference_gets_curated_experiment_not_blink(tmp_path):
    conn = _seeded(tmp_path)
    out = mentor_chat(conn, "STM32F103-BluePill",
                      [{"role": "user", "content": "I want to run a tflite neural network on this board"}],
                      use_llm=False)
    low = out.lower()
    assert "try this:" in low
    assert "model" in low and "ram" in low and "flash" in low      # size-vs-budget, the real first step
    assert "blink project" not in low                              # NOT the generic fallback


def test_p3_linker_gets_curated_experiment_not_blink(tmp_path):
    conn = _seeded(tmp_path)
    out = mentor_chat(conn, "STM32F103-BluePill",
                      [{"role": "user", "content": "how does the linker script decide where code goes?"}],
                      use_llm=False)
    low = out.lower()
    assert "try this:" in low and ".ld" in low
    assert "blink project" not in low


def test_p3_never_repeats_the_same_try_this_in_a_session(tmp_path):
    conn = _seeded(tmp_path)
    first = mentor_chat(conn, "STM32F103-BluePill",
                        [{"role": "user", "content": "where do I start on this board?"}], use_llm=False)
    assert "Try this:" in first                          # first time: the board's blink experiment
    prior = first[first.index("Try this:"):]
    out = mentor_chat(conn, "STM32F103-BluePill",
                      [{"role": "user", "content": "where do I start on this board?"},
                       {"role": "assistant", "content": prior},
                       {"role": "user", "content": "what else should I try first on this board?"}],
                      use_llm=False)
    assert "Try this:" not in out                        # same experiment already given -> suppressed


def test_p3_first_project_still_gets_board_experiment(tmp_path):
    """Regression: a plain first-project question (no prior turns) still gets the family experiment."""
    conn = _seeded(tmp_path)
    out = mentor_chat(conn, "Arduino-Uno",
                      [{"role": "user", "content": "where do I start on this board?"}], use_llm=False)
    assert "F_CPU" in out                                # AVR family experiment, unchanged


# --- P4A: multi-board context retention ---------------------------------------------------

def test_p4a_career_query_sequences_across_all_mentioned_boards(tmp_path):
    conn = _seeded(tmp_path)
    out = mentor_chat(conn, "STM32F103-BluePill",
                      [{"role": "user", "content": "I also have an ESP32 and an Arduino Uno. "
                        "What should I learn for an embedded career?"}],
                      use_llm=False)
    assert "Try this:" not in out                        # career -> roadmap, no canned experiment
    low = out.lower()
    assert "esp32" in low and "uno" in low               # the other two boards are retained
    assert "stm32f103" in low or "bluepill" in low       # and the selected one
    assert "transfer" in low                             # reasoning-transfers framing


def test_p4a_mentioned_boards_detects_colloquial_names(tmp_path):
    conn = _seeded(tmp_path)
    from eaedk.mentor_llm import _mentioned_boards
    msgs = [{"role": "user", "content": "I have a Blue Pill, a Pico, and an Arduino Mega."}]
    active = _mentioned_boards(conn, msgs, "STM32F103-BluePill")
    assert active[0] == "STM32F103-BluePill"             # selected stays first
    assert "Raspberry-Pi-Pico" in active and "Arduino-Mega" in active


def test_p4a_no_false_positive_board_mentions(tmp_path):
    conn = _seeded(tmp_path)
    from eaedk.mentor_llm import _mentioned_boards
    # "announce" must not match "uno"; ordinary prose adds no boards.
    active = _mentioned_boards(conn, [{"role": "user", "content": "I want to announce a megabyte buffer."}],
                               "STM32F103-BluePill")
    assert active == ["STM32F103-BluePill"]


# --- P4B: validation key transparency -----------------------------------------------------

def test_p4b_unrecognized_input_key_warns(tmp_path):
    conn = _seeded(tmp_path)
    resp = assess(conn, "bootloader",
                  {"estimated_image_size": 32768, "vector_table_addr": 0x08000000,
                   "flsah_size": 99999},                 # typo -> recognised by nothing
                  board_name="Nucleo-F411RE")
    assert any("flsah_size" in w for w in resp.input_warnings)
    assert all("estimated_image_size" not in w for w in resp.input_warnings)  # real key: no warn


def test_p4b_recognized_inputs_produce_no_warnings(tmp_path):
    conn = _seeded(tmp_path)
    resp = assess(conn, "bootloader",
                  {"estimated_image_size": 32768, "vector_table_addr": 0x08000000,
                   "stack_size": 8192, "heap_size": 16384, "static_size": 16384},
                  board_name="Nucleo-F411RE")
    assert resp.input_warnings == []                     # no false positives on real inputs


def test_p4b_unknown_rule_names_missing_dependent_keys(tmp_path):
    conn = _seeded(tmp_path)
    resp = assess(conn, "uboot", {"kernel_load_addr": 0xC2000000}, board_name="STM32MP157")
    needs = [u for u in resp.unknowns if "needs:" in u]
    assert needs, resp.unknowns                          # UNKNOWN is never a dead end
    assert any("dtb_load_addr" in u for u in needs)      # names a specific missing dependent key


def test_p4b_warnings_render_in_markdown(tmp_path):
    conn = _seeded(tmp_path)
    resp = assess(conn, "bootloader", {"xyz_bogus": 1}, board_name="Nucleo-F411RE")
    md = resp.to_markdown()
    assert "## Input Warnings" in md and "xyz_bogus" in md
