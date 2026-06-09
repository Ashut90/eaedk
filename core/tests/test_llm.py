"""Tests for the LLM post-filter and gateway. No Ollama required (fake provider)."""
from eaedk.store.db import connect
from eaedk.store.migrate import migrate
from eaedk.seed import seed_all
from eaedk import repo
from eaedk.orchestrator import assess_project
from eaedk.llm.postfilter import build_allowlist, filter_text, REMOVED_MARKER
from eaedk.llm.gateway import Gateway


def _project(tmp_path):
    conn = connect(str(tmp_path / "t.db"))
    migrate(conn)
    seed_all(conn, force=True)
    repo.create_project(conn, "p", "bootloader", "STM32H743")
    p = repo.get_project(conn, "p")
    repo.set_input(conn, p["id"], "vector_table_addr", "0x08000000", confidence="HIGH")
    return conn, repo.get_project(conn, "p")


def test_allowlist_includes_cited_board_and_inputs(tmp_path):
    conn, p = _project(tmp_path)
    allow = build_allowlist(conn, p)
    assert 0x08000000 in allow.numbers       # flash_base (also the input)
    assert 2097152 in allow.numbers          # flash_bytes (2 MiB)
    assert 1048576 in allow.numbers          # ram_bytes (1 MiB)


def test_filter_keeps_cited_values(tmp_path):
    conn, p = _project(tmp_path)
    allow = build_allowlist(conn, p)
    text = "The image lives in 2 MB of flash at 0x08000000 and that fits."
    out, removed = filter_text(text, allow)
    assert removed == 0
    assert REMOVED_MARKER not in out


def test_filter_strips_uncited_address_and_frequency(tmp_path):
    conn, p = _project(tmp_path)
    allow = build_allowlist(conn, p)
    text = ("Enable the clock. Write to the register at 0x40021000 now. "
            "The CPU runs at 480 MHz.")
    out, removed = filter_text(text, allow)
    assert removed == 2                        # the 0x40021000 sentence and the MHz sentence
    assert "Enable the clock." in out
    assert "0x40021000" not in out
    assert "480 MHz" not in out


def test_filter_keeps_pure_prose(tmp_path):
    conn, p = _project(tmp_path)
    allow = build_allowlist(conn, p)
    text = "Validate the image integrity before jumping to the application."
    out, removed = filter_text(text, allow)
    assert removed == 0
    assert out == text


class _FakeProvider:
    model = "fake-model"

    def __init__(self, text):
        self.text = text

    def available(self):
        return True

    def generate(self, system, prompt):
        return self.text


def test_gateway_applies_postfilter(tmp_path):
    conn, p = _project(tmp_path)
    resp = assess_project(conn, p)
    fake = _FakeProvider("Looks feasible. The DDR timing is tCL 14 at 800 MHz.")
    gw = Gateway(provider=fake)
    out = gw.ask(conn, p, resp, "is this ok?")
    assert out.removed >= 1                    # the invented MHz/timing sentence is stripped
    assert "800 MHz" not in out.text
    assert out.model == "fake-model"
