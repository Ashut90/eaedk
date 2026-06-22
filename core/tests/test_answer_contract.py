"""Answer-shape routing + deterministic Answer Contracts (step #1+#2).

Proves the contract is GENERAL, not a stored bootloader template: structure questions across
bootloader (incl. the typo 'bootlaoder'), Yocto, Linux drivers and generic firmware all route to the
concrete_structure shape and are validated by SHAPE, while open decisions stay Socratic and debugging
still uses the deterministic proof-path. Also proves the Actor REGENERATES on a contract miss (capped)
and that a passing concrete answer ships UNCHANGED — no LLM critic rewrite.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eaedk import answer_contract as ac
from eaedk import arbiter, navigator as nav, problem_patterns as pp
from eaedk.store.db import connect
from eaedk.store.migrate import migrate
from eaedk.seed import seed_all
from eaedk.mentor_llm import mentor_chat, decide_purpose
from eaedk.llm.gateway import Gateway

BOARD = "STM32F103-BluePill"


def _seeded(tmp_path):
    conn = connect(str(tmp_path / "t.db")); migrate(conn); seed_all(conn, force=True)
    return conn


# ── Shape detection is general, not bootloader-specific ──────────────────────────────────────────
def test_structure_questions_route_to_concrete_shape_across_domains():
    for q in ("folder structure for bootloader",
              "folder structure for bootlaoder",                 # typo must still route
              "how should I structure a yocto application?",
              "project structure for a linux kernel driver",
              "how do I organize a generic firmware project?"):
        assert ac.detect_answer_shape(q) == ac.CONCRETE_STRUCTURE, q
        assert ac.build_contract(ac.detect_answer_shape(q), q).soft_critics is False  # deterministic only


# ── Per-domain shape requirements (validate the GENERATED shape, never a stored answer) ───────────
def test_bootloader_requires_boot_app_platform_separation():
    c = ac.build_contract(ac.CONCRETE_STRUCTURE, "folder structure for bootloader")
    assert c.validate("A bootloader verifies the app and jumps to it. Here are the trade-offs...")  # prose fails
    ok = "```\nproject/\n├── bootloader/\n├── app/\n└── platform/ports/stm32/\n```"
    assert c.validate(ok) == []


def test_typo_bootlaoder_keeps_the_separation_requirement():
    c = ac.build_contract(ac.CONCRETE_STRUCTURE, "folder structure for bootlaoder")
    assert any("bootloader project must separate" in f for f in c.validate("```\nsrc/\ninclude/\n```"))


def test_yocto_requires_layer_and_recipe_and_source():
    c = ac.build_contract(ac.CONCRETE_STRUCTURE, "how should I structure a yocto application?")
    assert c.validate("```\nsrc/\ninclude/\n```")                # a plain tree is not a Yocto layout
    ok = "```\nmeta-myapp/\n├── recipes-app/myapp/myapp_1.0.bb\n└── sources/src/main.c\n```"
    assert c.validate(ok) == []


def test_linux_driver_requires_makefile_and_source():
    c = ac.build_contract(ac.CONCRETE_STRUCTURE, "project structure for a linux kernel driver")
    assert c.validate("```\nsrc/\ninclude/\n```")                # no Makefile/Kbuild -> fail
    ok = "```\nmydrv/\n├── Makefile\n├── mydrv.c\n└── include/mydrv.h\n```"
    assert c.validate(ok) == []


def test_generic_firmware_only_requires_a_real_tree():
    c = ac.build_contract(ac.CONCRETE_STRUCTURE, "how do I organize a generic firmware project?")
    assert c.validate("Think about separating drivers from application logic.")      # prose fails
    assert c.validate("```\nsrc/\ndrivers/\ninclude/\n```") == []                     # a tree passes


# ── Open decisions stay Socratic; debugging stays a deterministic proof-path ──────────────────────
def test_open_decision_stays_socratic():
    q = "should I use HAL or bare metal?"
    assert ac.detect_answer_shape(q) == ac.OPEN_DECISION
    assert arbiter._is_open_decision(q)
    c = ac.build_contract(ac.OPEN_DECISION, q)
    assert c.soft_critics is True                                # the relevance critic still leaves it Socratic
    assert c.validate("Use HAL, it's easier.")                  # blunt + no question -> contract miss
    assert c.validate("It depends — learn or ship? Question: which matters more?") == []


def test_debug_question_uses_deterministic_proof_path(tmp_path):
    assert ac.detect_answer_shape("my uart prints nothing, no output") == ac.DEBUG_PROOF_PATH
    conn = _seeded(tmp_path)
    msgs = [{"role": "user", "content": "my uart prints nothing, no output"}]
    route = nav.classify(decide_purpose(conn, BOARD, msgs[0]["content"], {}, msgs), msgs)
    assert route.mode == nav.PROOF_PATH and route.proof_state.pattern.name == "uart_bringup"


# ── Live pipeline: regenerate on miss (capped), ship a passing answer unchanged (no rewrite) ──────
class _Seq:
    """Returns queued outputs in order, repeating the last; records the system prompts it saw."""
    def __init__(self, *outs): self.provider = self; self.outs = list(outs); self.systems = []
    def available(self): return True
    def generate(self, system, prompt):
        self.systems.append(system)
        return self.outs[min(len(self.systems) - 1, len(self.outs) - 1)]


def test_concrete_miss_triggers_capped_regeneration(tmp_path):
    conn = _seeded(tmp_path)
    gw = _Seq("just prose, no tree at all")                       # always misses the contract
    mentor_chat(conn, BOARD, [{"role": "user", "content": "folder structure for bootloader"}],
                use_llm=True, gateway=Gateway(provider=gw))
    assert len(gw.systems) == ac.MAX_REGEN + 1                    # 1 actor + MAX_REGEN regenerations
    assert all("CRITIC" not in s and "RELEVANCE" not in s for s in gw.systems)  # never a rewrite pass


def test_passing_concrete_ships_unchanged_in_one_call(tmp_path):
    conn = _seeded(tmp_path)
    tree = ("```\nproject/\n├── bootloader/\n├── app/\n└── platform/ports/stm32/\n```\n"
            "SENTINEL_KEY_PHRASE: the key folders.")
    gw = _Seq(tree)
    out = mentor_chat(conn, BOARD, [{"role": "user", "content": "folder structure for bootloader"}],
                      use_llm=True, gateway=Gateway(provider=gw))
    assert len(gw.systems) == 1                                   # passed first try: one call, no regen
    assert "SENTINEL_KEY_PHRASE" in out                          # Actor's exact words shipped, not rewritten
    assert all("CRITIC" not in s and "RELEVANCE" not in s for s in gw.systems)


def test_structure_followup_is_not_a_flash_experiment(tmp_path):
    conn = _seeded(tmp_path)
    tree = "```\nproject/\n├── bootloader/\n├── app/\n└── platform/ports/\n```"
    out = mentor_chat(conn, BOARD, [{"role": "user", "content": "folder structure for bootloader"}],
                      use_llm=True, gateway=Gateway(provider=_Seq(tree)))
    low = out.lower()
    assert "expand one part of the tree" in low                  # structure-relevant follow-up
    # the structure answer must not dangle a blink/flash/UART experiment as its next step
    assert "blink" not in low and "f_cpu" not in low
