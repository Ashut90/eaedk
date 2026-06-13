"""Golden tests for v2.3.0 — datasheet intelligence: arch risks, similarity, the 7-section
report, and the confidence-rated query engine (with the post-filter on LLM elaboration)."""
import pytest

from eaedk.store.db import connect
from eaedk.store.migrate import migrate
from eaedk.seed import seed_all
from eaedk import repo
from eaedk.engines.ingest.arch_risks import risks_for_arch
from eaedk.engines.ingest.similarity import similar_with_guidance
from eaedk.engines.ingest.report import intelligence_report
from eaedk.engines.ingest.query import answer_query
from eaedk.engines.ingest.engine import ingest_datasheet
from eaedk.engines.ingest.extract import Page
from eaedk.llm.gateway import Gateway
from eaedk.llm.postfilter import REMOVED_MARKER


def _seeded(tmp_path):
    conn = connect(str(tmp_path / "t.db"))
    migrate(conn)
    seed_all(conn, force=True)
    return conn


def _unknown_m4(conn):
    """A fresh Cortex-M4 board with no geometry, plus an ingested synthetic datasheet."""
    conn.execute("INSERT INTO socs(name,vendor,arch) VALUES ('MYSOC','Acme','arm-cortex-m4')")
    sid = conn.execute("SELECT id FROM socs WHERE name='MYSOC'").fetchone()["id"]
    conn.execute("INSERT INTO boards(soc_id,name,confidence) VALUES (?,?,'LOW')", (sid, "Mystery"))
    conn.commit()
    pages = [Page(12, "1.1 Overview\nCortex-M4 core"),
             Page(47, "3.2 Memory map\nThe Flash memory base address is 0x08000000 with 512 "
                      "Kbytes of embedded Flash. SRAM base is 0x20000000 with 128 Kbytes of SRAM."),
             Page(23, "6.2 Clock tree\nThe core runs up to 168 MHz")]
    ingest_datasheet(conn, "fake.pdf", "Mystery", reader=lambda _p: pages)
    return "Mystery"


# --- Piece 3: arch-aware risk rules ----------------------------------------

def test_arch_risks_match_family_and_sort_by_severity():
    m4 = risks_for_arch("arm-cortex-m4", set())
    assert m4 and m4[0]["severity"] == "HIGH"
    titles = {r["title"] for r in m4}
    assert "Watchdog default state unknown" in titles      # inherited cortex-m match
    assert any("FPU" in t for t in titles)                 # cortex-m4 specific
    assert not risks_for_arch("avr", set()) == m4          # different family, different risks


def test_arch_risk_suppressed_when_required_fact_present():
    assert any("Flash latency" in r["title"] for r in risks_for_arch("arm-cortex-m4", set()))
    assert not any("Flash latency" in r["title"]
                   for r in risks_for_arch("arm-cortex-m4", {"sysclk_max_hz"}))


# --- Piece 4: similarity engine --------------------------------------------

def test_blue_pill_matches_nucleo_f103(tmp_path):
    conn = _seeded(tmp_path)
    sim = similar_with_guidance(conn, "STM32F103-BluePill")
    assert sim[0]["name"] == "Nucleo-F103RB"
    assert sim[0]["score"] >= 70 and sim[0]["confidence"] == "HIGH"
    assert sim[0]["match"]["arch"] and sim[0]["works_same"] and sim[0]["must_verify"]


def test_similarity_scoring_breakdown(tmp_path):
    conn = _seeded(tmp_path)
    _b, soc = repo.load_board(conn, "STM32F103-BluePill")
    board, _ = repo.load_board(conn, "STM32F103-BluePill")
    matches = repo.find_similar_boards(conn, soc["arch"], board["flash_bytes"],
                                       board["ram_bytes"],
                                       repo.board_capability_names(conn, "STM32F103-BluePill"),
                                       exclude="STM32F103-BluePill")
    top = matches[0]
    assert top["match"]["arch"] and top["match"]["vendor"]   # same arch + same vendor counted


# --- Piece 1: the 7-section report -----------------------------------------

def test_report_has_all_seven_sections(tmp_path):
    conn = _seeded(tmp_path)
    r = intelligence_report(conn, _unknown_m4(conn))
    for k in ("found", "missing", "priority", "risks", "similar", "can", "cannot", "next_step"):
        assert k in r
    found_keys = {f["key"] for f in r["found"]}
    assert {"flash_base", "flash_bytes", "ram_base", "ram_bytes", "sysclk_max_hz"} <= found_keys
    assert r["found"][0]["citation"].startswith("p.")        # cited with page
    # boot pins are mandatory + missing -> Section 2 + the immediate next step
    assert any("Boot pin" in m["label"] for m in r["missing"])
    assert "boot pin" in r["next_step"]["action"].lower() and r["next_step"]["where"]
    # geometry found -> linker generation is in "can"
    assert any("linker" in c for c in r["can"])


def test_report_arch_risks_present(tmp_path):
    conn = _seeded(tmp_path)
    r = intelligence_report(conn, _unknown_m4(conn))
    assert any(rk["severity"] == "HIGH" for rk in r["risks"])


# --- Piece 2: query engine + confidence ------------------------------------

def test_query_high_confidence_cited(tmp_path):
    conn = _seeded(tmp_path)
    res = answer_query(conn, "STM32F103-BluePill", "what is the flash size?")
    assert res["confidence"] == "HIGH" and "64KB" in res["answer"]
    assert "What this means" in res["answer"]                 # ends with an implication


def test_query_unknown_for_missing_mandatory(tmp_path):
    conn = _seeded(tmp_path)
    res = answer_query(conn, "STM32F103-BluePill", "what are the boot pin settings?")
    assert res["confidence"] == "UNKNOWN"
    assert "BOOT0" in res["answer"] and "Do not proceed" in res["answer"]   # search + hard warning


def test_query_architecture_high(tmp_path):
    conn = _seeded(tmp_path)
    res = answer_query(conn, "STM32F103-BluePill", "which core does this use?")
    assert res["confidence"] == "HIGH" and "cortex-m3" in res["answer"]


def test_query_mode_b_low_without_llm(tmp_path):
    conn = _seeded(tmp_path)
    res = answer_query(conn, "STM32F103-BluePill", "describe the DMA controller layout")
    assert res["confidence"] == "LOW" and "ingest" in res["answer"].lower()


class _InventingProvider:
    model = "fake"
    def available(self): return True
    def generate(self, system, prompt):
        return "The DMA base address is 0xCAFEBABE and it runs at 200 MHz."


def test_query_postfilter_strips_invented_value(tmp_path):
    conn = _seeded(tmp_path)
    res = answer_query(conn, "STM32F103-BluePill", "describe the DMA controller", use_llm=True,
                       gateway=Gateway(provider=_InventingProvider()))
    assert "0xCAFEBABE" not in res["answer"] and "200 MHz" not in res["answer"]
    assert REMOVED_MARKER in res["answer"] and res["confidence"] == "MEDIUM"
