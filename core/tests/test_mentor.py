"""Golden tests for the mentor layer (v1.5.0): capability map + learning path (Part 1),
teach-commented codegen + START_HERE (Part 2), LLM ask/explain post-filter (Part 3), and the
Actor-Critic arbiter + loop (Part 4)."""
import json

from eaedk.store.db import connect
from eaedk.store.migrate import migrate
from eaedk.seed import seed_all
from eaedk import repo, mentor
from eaedk.engines.output import export, codegen
from eaedk.mentor_llm import mentor_explain, mentor_ask
from eaedk.actor_critic import arbitrate, run_actor_critic
from eaedk.llm.gateway import Gateway


def _seeded(tmp_path):
    conn = connect(str(tmp_path / "t.db"))
    migrate(conn)
    seed_all(conn, force=True)
    return conn


# --- Part 1 ---------------------------------------------------------------

def test_capability_map_and_path_filtered_by_board(tmp_path):
    conn = _seeded(tmp_path)
    caps = {c["capability"] for c in mentor.capability_map(conn, "WIZnet-W5500-EVB-Pico")}
    assert {"spi", "uart", "ethernet"} <= caps
    keys = [s["key"] for s in mentor.learning_path_for(conn, caps)]
    assert "spi_sensor" in keys                       # board has SPI -> SPI step shows
    # a board WITHOUT spi must not get the SPI sensor step
    mp_caps = mentor.capability_map(conn, "STM32MP157")
    mp_keys = [s["key"] for s in mentor.learning_path_for(conn, {c["capability"] for c in mp_caps})]
    assert "spi_sensor" not in mp_keys and "blink" in mp_keys


def test_render_board_mentor_is_plain_language(tmp_path):
    conn = _seeded(tmp_path)
    out = mentor.render_board_mentor(conn, "Nucleo-F411RE")
    assert "What this board can do" in out and "learning path" in out.lower()
    assert "Blink an LED" in out and "why:" in out


# --- Part 2 ---------------------------------------------------------------

def _export(conn, board, tmp_path, name="p"):
    repo.create_project(conn, name, "bare_metal_app", board)
    out = tmp_path / name
    export.export_project(conn, repo.get_project(conn, name), str(out), force=True)
    return out


def test_bare_metal_app_codegen_is_working_with_teach_comments(tmp_path):
    conn = _seeded(tmp_path)
    out = _export(conn, "Nucleo-F411RE", tmp_path)          # STM32F4 family
    main = (out / "src" / "main.c").read_text()
    assert "USART2EN" in main and "toggle PA5" in main       # working code
    assert "/*" in main and "GPIOA holds the LED" in main    # teach comment present
    assert (out / "START_HERE.md").exists()
    sh = (out / "START_HERE.md").read_text()
    assert "Build it" in sh and "learning path" in sh.lower()


def test_unknown_family_gets_teach_skeleton(tmp_path):
    conn = _seeded(tmp_path)
    out = _export(conn, "WIZnet-W5500-EVB-Pico", tmp_path)   # RP2040 -> skeleton
    main = (out / "src" / "main.c").read_text()
    assert "TODO" in main and "never guess" in main          # honest skeleton, no invented regs


# --- Part 3 ---------------------------------------------------------------

class _FakeProvider:
    model = "fake"
    def __init__(self, text): self.text = text
    def available(self): return True
    def generate(self, system, prompt): return self.text


def test_explain_deterministic_anchor_offline(tmp_path):
    conn = _seeded(tmp_path)
    out = mentor_explain(conn, "Nucleo-F411RE", "HardFault", use_llm=False)
    assert "HardFault" in out and "unrecoverably wrong" in out  # seeded anchor, no LLM


def test_mentor_llm_post_filters_invented_value(tmp_path):
    conn = _seeded(tmp_path)
    fake = Gateway(provider=_FakeProvider(
        "A HardFault stops the CPU. It often runs at 168 MHz when this happens."))
    out = mentor_explain(conn, "Nucleo-F411RE", "HardFault", use_llm=True, gateway=fake)
    assert "168 MHz" not in out                              # invented clock stripped
    assert "removed" in out


# --- Part 4 ---------------------------------------------------------------

def test_arbiter_confirms_overbudget_stack_rejects_within_budget(tmp_path):
    conn = _seeded(tmp_path)
    board, soc = repo.load_board(conn, "Nucleo-F411RE")     # RAM = 131072
    over = [{"kind": "stack_too_small", "message": "stack huge", "check": {"stack": 1_000_000}}]
    within = [{"kind": "x", "message": "ok", "check": {"stack": 4096, "heap": 2048, "static": 2048}}]
    structural = [{"kind": "missing_clock", "message": "no RCC enable"}]
    c1, _ = arbitrate(over, board, soc)
    c2, a2 = arbitrate(within, board, soc)
    c3, a3 = arbitrate(structural, board, soc)
    assert len(c1) == 1 and "verified" in c1[0]              # over-budget CONFIRMED by RAM_BUDGET
    assert not c2 and a2                                     # within budget -> advisory, not confirmed
    assert not c3 and a3                                     # structural reasoning -> advisory only


class _LoopProvider:
    """Critic always flags an over-budget stack; Actor returns a fix. Loop must terminate."""
    model = "fake"
    def __init__(self): self.calls = 0
    def available(self): return True
    def generate(self, system, prompt):
        self.calls += 1
        if "review" in system.lower() or "JSON" in system:
            return json.dumps({"issues": [{"kind": "stack_too_small",
                               "message": "stack exceeds RAM", "check": {"stack": 9_000_000}}]})
        return "Reduce the stack to a few KB so it fits in RAM."


def test_actor_critic_loop_terminates_and_arbiter_governs(tmp_path):
    conn = _seeded(tmp_path)
    repo.create_project(conn, "blink", "bare_metal_app", "Nucleo-F411RE")
    res = run_actor_critic(conn, repo.get_project(conn, "blink"),
                           gateway=Gateway(provider=_LoopProvider()), max_epochs=2)
    assert res.available and res.epochs <= 2                 # bounded
    assert res.confirmed and "verified" in res.confirmed[0]  # arbiter confirmed via RAM_BUDGET
    assert res.fixes                                         # Actor proposed a fix
