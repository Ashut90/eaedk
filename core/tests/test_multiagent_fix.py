"""Golden tests for v1.9.0-multiagent-fix — the seven stress-test findings (docs/14).

Deterministic: a fake provider stands in for the LLM, so the agent paths run without Ollama.
Covers grounding the loop in real project inputs (F1/F2), goal-aware artifacts (F3),
post-filtering the Actor (F5), the broadened Critic scope (F4), opt-in --deep triage (F6), and
the goal-neutral validate copy (F7).
"""
import json

from eaedk.store.db import connect
from eaedk.store.migrate import migrate
from eaedk.seed import seed_all
from eaedk import repo
from eaedk import actor_critic as ac
from eaedk.actor_critic import run_actor_critic, grounded_confirmations
from eaedk.engines.output import export, codegen
from eaedk.engines.logs import analyze_log
from eaedk.llm.gateway import Gateway
from eaedk.llm.postfilter import REMOVED_MARKER
from eaedk.schemas.response import AssessResponse


def _seeded(tmp_path):
    conn = connect(str(tmp_path / "t.db"))
    migrate(conn)
    seed_all(conn, force=True)
    return conn


class _Fake:
    """Critic returns a configured JSON; Actor returns configured text. Switches on the same
    tokens the production prompts carry (Critic has 'review'/'JSON', Actor has neither)."""
    model = "fake"

    def __init__(self, critic_json="{\"issues\":[]}", actor_text="ok"):
        self.critic_json, self.actor_text = critic_json, actor_text

    def available(self):
        return True

    def generate(self, system, prompt):
        if "review" in system.lower() or "JSON" in system:
            return self.critic_json
        return self.actor_text


def _pico_oversized_stack(conn):
    repo.create_project(conn, "p", "bare_metal_app", "Raspberry-Pi-Pico")  # RAM 270336
    pid = repo.get_project(conn, "p")["id"]
    for k, v in {"stack_size": "524288", "heap_size": "0", "static_size": "0"}.items():
        repo.set_input(conn, pid, k, v, confidence="HIGH")
    return repo.get_project(conn, "p")


def _esp32_overlapping_ota(conn):
    repo.create_project(conn, "o", "ota", "ESP32-DevKitC")
    pid = repo.get_project(conn, "o")["id"]
    repo.set_input(conn, pid, "primary_storage_bytes", "4194304", confidence="HIGH")
    parts = [{"name": "slot_a", "role": "slot_a", "base": 65536, "size": 1048576},
             {"name": "slot_b", "role": "slot_b", "base": 1048576, "size": 1048576}]
    repo.set_input(conn, pid, "partitions", json.dumps(parts), confidence="HIGH")
    return repo.get_project(conn, "o")


# --- F1 + F2: the loop is grounded in the engineer's real inputs ----------

def test_grounded_confirmations_catch_oversized_stack(tmp_path):
    conn = _seeded(tmp_path)
    g = grounded_confirmations(conn, _pico_oversized_stack(conn))
    kinds = {c["kind"] for c in g}
    assert "RAM_BUDGET" in kinds
    ram = next(c for c in g if c["kind"] == "RAM_BUDGET")
    assert "exceeds" in ram["message"] and ram["verified"]


def test_grounded_confirmations_catch_partition_overlap(tmp_path):
    conn = _seeded(tmp_path)
    g = grounded_confirmations(conn, _esp32_overlapping_ota(conn))
    assert "PARTITION_NO_OVERLAP" in {c["kind"] for c in g}


def test_loop_confirms_real_fault_even_when_critic_is_silent(tmp_path):
    # F2: a Critic that flags NOTHING must not stop the loop from surfacing the real,
    # deterministically-provable overflow — that was the stress-test failure.
    conn = _seeded(tmp_path)
    project = _pico_oversized_stack(conn)
    res = run_actor_critic(conn, project, gateway=Gateway(provider=_Fake(critic_json='{"issues":[]}')))
    assert res.available
    assert any(c["kind"] == "RAM_BUDGET" for c in res.confirmed)   # grounded, not LLM-derived
    assert res.fixes                                               # Actor explained the real fix


def test_loop_does_not_confirm_false_critic_claim(tmp_path):
    # F1: a Critic inventing a structural defect on a clean project must NOT appear as CONFIRMED;
    # only the deterministic engine confirms. With no real fault, confirmed stays empty.
    conn = _seeded(tmp_path)
    repo.create_project(conn, "clean", "bare_metal_app", "STM32F103-BluePill")
    false_claim = json.dumps({"issues": [{"kind": "missing_clock_enable",
                                          "message": "GPIOC clock not enabled"}]})
    res = run_actor_critic(conn, repo.get_project(conn, "clean"),
                           gateway=Gateway(provider=_Fake(critic_json=false_claim)))
    assert not res.confirmed                       # false structural claim is not confirmed
    assert any(a.get("kind") == "missing_clock_enable" for a in res.advisory)  # shown as advisory


# --- F3: goal-aware review artifact ---------------------------------------

def test_artifact_is_bare_metal_c_for_app(tmp_path):
    conn = _seeded(tmp_path)
    repo.create_project(conn, "a", "bare_metal_app", "STM32F103-BluePill")
    data = export.gather(conn, repo.get_project(conn, "a"))
    kind, content = codegen.render_review_artifact(data)
    assert kind == "bare_metal_c"
    assert content == codegen.render_main_c(data)   # byte-for-byte the old behaviour


def test_artifact_is_devicetree_for_linux(tmp_path):
    conn = _seeded(tmp_path)
    repo.create_project(conn, "l", "linux", "STM32MP157")
    data = export.gather(conn, repo.get_project(conn, "l"))
    kind, content = codegen.render_review_artifact(data)
    assert kind == "devicetree"
    assert "compatible" in content and "interrupt-parent" in content
    assert "<TODO" in content                       # never an invented address


def test_artifact_is_partition_table_for_ota(tmp_path):
    conn = _seeded(tmp_path)
    project = _esp32_overlapping_ota(conn)
    data = export.gather(conn, project)
    data["inputs"] = repo.load_inputs(conn, project["id"])[0]
    kind, content = codegen.render_review_artifact(data)
    assert kind == "partition_table"
    assert "slot_a" in content and "slot_b" in content


def test_run_actor_critic_records_artifact_kind(tmp_path):
    conn = _seeded(tmp_path)
    repo.create_project(conn, "l", "linux", "STM32MP157")
    res = run_actor_critic(conn, repo.get_project(conn, "l"), gateway=Gateway(provider=_Fake()))
    assert res.artifact_kind == "devicetree"


# --- F5: the Actor's fix text is post-filtered ----------------------------

def test_actor_invented_address_is_stripped(tmp_path):
    conn = _seeded(tmp_path)
    project = _pico_oversized_stack(conn)            # a real fault -> Actor runs
    actor = "Reduce the stack size. Set the vector base to 0xCAFEBABE for safety."
    res = run_actor_critic(conn, project, gateway=Gateway(provider=_Fake(actor_text=actor)))
    blob = " ".join(f["fix"] for f in res.fixes)
    assert "0xCAFEBABE" not in blob                  # uncited address removed
    assert REMOVED_MARKER in blob


# --- F4: broadened Critic scope (and preserved fake-provider contract) ----

def test_critic_prompt_covers_new_scope():
    s = ac._CRITIC_SYSTEM.lower()
    assert "partition" in s and "compatible" in s and "baud" in s   # F4 additions
    # contract for the test fake providers: Critic keeps review/JSON, Actor keeps neither
    assert "review" in s or "json" in ac._CRITIC_SYSTEM.lower() or "JSON" in ac._CRITIC_SYSTEM
    assert "review" not in ac._ACTOR_SYSTEM.lower() and "json" not in ac._ACTOR_SYSTEM.lower()


# --- F6: opt-in --deep triage even when a signature matched ---------------

class _TriageFake:
    model = "fake"
    def available(self):
        return True
    def generate(self, system, prompt):
        return json.dumps({"hypotheses": [{"cause": "root cause from deep triage",
                                           "evidence_line": "x", "suggested_check": "y"}],
                           "confidence": "MEDIUM"})


def _hardfault_log(tmp_path):
    p = tmp_path / "hf.log"
    p.write_text("app: running\n[FAULT] HardFault_Handler: forced exception\nHFSR=0x40000000\n",
                 encoding="utf-8")
    return str(p)


def test_match_without_deep_skips_triage(tmp_path):
    conn = _seeded(tmp_path)
    res = analyze_log(conn, _hardfault_log(tmp_path), use_llm=True,
                      gateway=Gateway(provider=_TriageFake()))
    assert res.matches and res.triage is None        # default: match short-circuits triage


def test_deep_runs_triage_alongside_match(tmp_path):
    conn = _seeded(tmp_path)
    res = analyze_log(conn, _hardfault_log(tmp_path), use_llm=True, deep=True,
                      gateway=Gateway(provider=_TriageFake()))
    assert res.matches                               # deterministic match still present
    assert res.triage is not None and res.triage["available"]   # AND the root-cause triage ran


# --- F7: goal-neutral validate copy ---------------------------------------

def test_validate_reassurance_is_goal_neutral():
    md = AssessResponse(goal_type="linux", feasibility="feasible",
                        unknowns=["X: missing"]).to_markdown()
    assert "for a first build" in md
    assert "blink an LED" not in md
