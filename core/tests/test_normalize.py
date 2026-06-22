"""Front-door term normalization (#3) — obvious typos/aliases are fixed BEFORE routing so a
misspelled domain term still grounds and reaches the answer-shape contract, while ordinary words are
never rewritten. Includes the end-to-end 'bootlaoder' case from the live run."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eaedk import normalize as nz, answer_contract as ac
from eaedk.store.db import connect
from eaedk.store.migrate import migrate
from eaedk.seed import seed_all
from eaedk.mentor_llm import mentor_chat, decide_purpose
from eaedk.llm.gateway import Gateway

BOARD = "STM32F103-BluePill"


def _seeded(tmp_path):
    conn = connect(str(tmp_path / "t.db")); migrate(conn); seed_all(conn, force=True)
    return conn


# ── It corrects obvious domain typos / aliases ────────────────────────────────────────────────────
def test_corrects_obvious_typos_and_aliases():
    assert nz.normalize_terms("folder structure for bootlaoder") == "folder structure for bootloader"
    assert nz.normalize_terms("bootloder layout") == "bootloader layout"
    assert nz.normalize_terms("write a firmare") == "write a firmware"
    assert nz.normalize_terms("interupt latency") == "interrupt latency"
    assert nz.normalize_terms("boot loader project") == "bootloader project"      # phrase collapse
    assert nz.normalize_terms("a yokto image") == "a yocto image"


# ── It is meaning-preserving: real words near a canonical term are NOT rewritten ───────────────────
def test_does_not_rewrite_ordinary_words():
    for clean in ("schedule the build pipeline", "the driver works fine", "a river runs through it",
                  "should I use HAL or bare metal?", "what is SPI?", "how do I set up UART?"):
        assert nz.normalize_terms(clean) == clean


def test_empty_and_noop():
    assert nz.normalize_terms("") == ""
    assert nz.normalize_terms("folder structure for a bootloader") == "folder structure for a bootloader"


# ── End-to-end: the typo now GROUNDS and reaches the concrete-structure contract ──────────────────
def test_typo_grounds_instead_of_being_declined(tmp_path):
    conn = _seeded(tmp_path)
    norm = nz.normalize_terms("folder structure for bootlaoder")
    pd = decide_purpose(conn, BOARD, norm, {})
    assert pd.purpose == "ANSWER_NOW"                                    # not ASK_CLARIFICATION/DECLINE
    assert ac.detect_answer_shape(norm) == ac.CONCRETE_STRUCTURE


class _Seq:
    def __init__(self, *outs): self.provider = self; self.outs = list(outs); self.systems = []
    def available(self): return True
    def generate(self, system, prompt):
        self.systems.append(system); return self.outs[min(len(self.systems) - 1, len(self.outs) - 1)]


def test_typo_reaches_the_contract_and_returns_a_tree(tmp_path):
    conn = _seeded(tmp_path)
    tree = "```\nproject/\n├── bootloader/\n├── app/\n└── platform/ports/\n```"
    out = mentor_chat(conn, BOARD, [{"role": "user", "content": "folder structure for bootlaoder"}],
                      use_llm=True, gateway=Gateway(provider=_Seq(tree)))
    assert "project/" in out and "platform/ports/" in out               # the contract answer, not a decline
    assert "don't have enough to answer" not in out.lower()
