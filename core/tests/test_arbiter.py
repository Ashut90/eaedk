"""Golden evals for v3.0 P4 — Actor-Critic-Arbiter (Validation Engine has final say)."""
from eaedk.store.db import connect
from eaedk.store.migrate import migrate
from eaedk.seed import seed_all
from eaedk import repo
from eaedk.arbiter import arbitrate, critic_review
from eaedk.mentor_llm import mentor_chat


def _seeded(tmp_path):
    conn = connect(str(tmp_path / "t.db"))
    migrate(conn)
    seed_all(conn, force=True)
    return conn


# --- GOLDEN: an optimistic Actor claim is discarded by the deterministic arbiter -----------

def test_p4_arbiter_discards_infeasible_grpc_claim(tmp_path):
    conn = _seeded(tmp_path)
    actor = ("You can run gRPC on Blue Pill with optimization. Just enable -Os and it should "
             "fit fine.")
    res = arbitrate(conn, "STM32F103-BluePill", None, "I want to add gRPC to this", actor)
    assert res.overridden is True
    assert "should fit fine" not in res.text                 # the Actor's prose is discarded
    assert "discarded" in res.text.lower()
    assert "400KB" in res.text and "64KB" in res.text        # the math is shown
    assert "NOT fit" in res.text


def test_p4_arbiter_passes_a_feasible_answer_through(tmp_path):
    conn = _seeded(tmp_path)
    actor = "FreeRTOS fits comfortably on this board; start with two tasks and a queue."
    res = arbitrate(conn, "STM32F103-BluePill", None, "can I use freertos here?", actor)
    assert res.overridden is False                            # freertos fits -> Actor text kept
    assert res.text == actor


def test_p4_arbiter_overrides_not_feasible_project(tmp_path):
    conn = _seeded(tmp_path)
    repo.create_project(conn, "nn", "bare_metal_app", "STM32F103-BluePill")
    p = repo.get_project(conn, "nn")
    repo.set_input(conn, p["id"], "estimated_image_size", "200000", confidence="HIGH")
    actor = "Sure, with quantization that neural network will run great on the Blue Pill."
    res = arbitrate(conn, "STM32F103-BluePill", "nn", "can I run a neural net?", actor)
    assert res.overridden is True
    assert "feasibility" in res.reason
    assert "run great" not in res.text                        # discarded
    assert "FLASH_CAPACITY" in res.text                       # the gating rule named, deterministically


def test_p4_arbiter_is_deterministic_audit_trail(tmp_path):
    conn = _seeded(tmp_path)
    res = arbitrate(conn, "STM32F103-BluePill", None, "tell me about blink", "Blink toggles a GPIO.")
    # both arbiter checks ran and are recorded, even when nothing failed
    names = {c["check"] for c in res.checks}
    assert names == {"feasibility", "semantic_cost"}
    assert res.overridden is False


def test_p4_critic_degrades_gracefully_without_model(tmp_path):
    """The Critic pass must never crash a response when the model is unavailable."""
    class _BadGw:
        class provider:
            @staticmethod
            def generate(system, prompt):
                raise RuntimeError("model down")
    out = critic_review(_BadGw(), "sys", "the actor answer", "context")
    assert out == "the actor answer"                          # falls back to the Actor text


def test_p4_chat_offline_arbiter_still_grounds(tmp_path):
    """Even with no model (offline), a gRPC request on the Blue Pill is grounded as infeasible by
    the deterministic prefix — the arbiter path never makes it worse."""
    conn = _seeded(tmp_path)
    out = mentor_chat(conn, "STM32F103-BluePill",
                      [{"role": "user", "content": "I want gRPC on this board"}], use_llm=False)
    assert "will NOT fit" in out
