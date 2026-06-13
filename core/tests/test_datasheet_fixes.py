"""Golden tests for v2.3.1 — the five datasheet fixes found in the STM32F303RE dogfood."""
import pytest

from eaedk.store.db import connect
from eaedk.store.migrate import migrate
from eaedk.seed import seed_all
from eaedk import repo
from eaedk.engines.ingest.extract import Page, extract_from_pages
from eaedk.engines.ingest.engine import ingest_datasheet
from eaedk.engines.ingest.report import intelligence_report
from eaedk.engines.ingest.query import answer_query
from eaedk.engines.ingest.similarity import similar_with_guidance
from eaedk.engines.ingest.labels import label_for, normalize_arch


def _seeded(tmp_path):
    conn = connect(str(tmp_path / "t.db"))
    migrate(conn)
    seed_all(conn, force=True)
    return conn


# The STM32F303RE-style prose (a board not in the DB), as the extractor receives it.
_F303 = [
    Page(2, "3.2 Memory mapping\nThe Flash memory base address is 0x08000000 and the device "
            "provides 512 Kbytes of embedded Flash memory. The main SRAM base address is "
            "0x20000000 with 64 Kbytes of SRAM."),
    Page(3, "5.3 Clock characteristics\nThe HSI provides an 8 MHz default clock at reset. The "
            "system clock can run up to 72 MHz maximum using the PLL."),
]


# --- Fix 2: extractor handles multi-fact prose -----------------------------

def test_extractor_gets_all_five_facts_from_prose():
    c = {x.fact_key: x for x in extract_from_pages(_F303)}
    assert {"flash_base", "flash_bytes", "ram_base", "ram_bytes", "sysclk_max_hz"} <= set(c)
    assert c["ram_base"].fact_value == "0x20000000"          # not mislabelled as flash
    assert c["ram_bytes"].fact_value == str(64 * 1024)
    assert c["sysclk_max_hz"].fact_value == str(72_000_000)
    # prose is MEDIUM, never upgraded to HIGH
    assert all(c[k].confidence == "MEDIUM" for k in c)


def test_extractor_unit_conversions():
    c = {x.fact_key: x for x in extract_from_pages(
        [Page(1, "It has 512 KB of Flash and 64 Kbytes of SRAM.")])}
    assert c["flash_bytes"].fact_value == str(512 * 1024)    # "512 KB" -> 524288
    assert c["ram_bytes"].fact_value == str(64 * 1024)


def test_extractor_fixture_unchanged():
    # The existing table-style fixture must still be 2 HIGH + 3 MEDIUM (no behaviour change).
    lines = ["3.2 Memory mapping", "Flash memory  0x08000000  0x0807FFFF",
             "SRAM          0x20000000  0x2001FFFF",
             "The device embeds 512 Kbytes of embedded Flash memory",
             "and 128 Kbytes of SRAM for data storage.", "4.1 Clocks",
             "The system clock can run up to 100 MHz."]
    c = {x.fact_key: x for x in extract_from_pages([Page(7, "\n".join(lines))])}
    assert c["flash_base"].confidence == "HIGH" and c["ram_base"].confidence == "HIGH"
    assert c["flash_bytes"].confidence == "MEDIUM" and c["ram_bytes"].fact_value == str(128 * 1024)
    highs = sum(1 for x in c.values() if x.confidence == "HIGH")
    meds = sum(1 for x in c.values() if x.confidence == "MEDIUM")
    assert highs == 2 and meds == 3


# --- Fix 5: human-readable labels ------------------------------------------

def test_labels_are_human_readable():
    assert label_for("flash_base") == "Flash start address"
    assert label_for("ram_bytes") == "RAM size"
    assert label_for("sysclk_max_hz") == "Maximum system clock"


def test_report_section1_uses_labels(tmp_path):
    conn = _seeded(tmp_path)
    repo.create_skeleton_board(conn, "F303", "arm-cortex-m4")
    ingest_datasheet(conn, "f.pdf", "F303", reader=lambda _p: _F303)
    r = intelligence_report(conn, "F303")
    labels = {f["label"] for f in r["found"]}
    assert "Flash start address" in labels and "RAM size" in labels
    assert "flash_base" not in labels                        # never a raw key


# --- Fix 4: similarity uses extracted facts --------------------------------

def test_similarity_uses_extracted_geometry(tmp_path):
    conn = _seeded(tmp_path)
    repo.create_skeleton_board(conn, "F303", "arm-cortex-m4")   # null stored geometry
    ingest_datasheet(conn, "f.pdf", "F303", reader=lambda _p: _F303)
    sim = similar_with_guidance(conn, "F303")
    assert sim[0]["score"] > 60                              # flash+RAM from candidates count
    assert sim[0]["geometry_unconfirmed"] is True            # labelled as unconfirmed


# --- Fix 3: ingested-but-not-extracted vs no-datasheet ---------------------

def test_query_message_when_ingested_but_not_extracted(tmp_path):
    conn = _seeded(tmp_path)
    repo.create_skeleton_board(conn, "F303", "arm-cortex-m4")
    ingest_datasheet(conn, "f.pdf", "F303", reader=lambda _p: _F303)   # a datasheet WAS ingested
    res = answer_query(conn, "F303", "what is the ADC resolution?")    # not an extracted fact
    assert "ingested" in res["answer"].lower() and "couldn't extract" in res["answer"].lower()
    assert "--review" in res["answer"]


def test_query_message_when_no_datasheet(tmp_path):
    conn = _seeded(tmp_path)
    res = answer_query(conn, "STM32F103-BluePill", "describe the DMA controller layout")
    assert "No datasheet has been ingested" in res["answer"]


# --- bootloader/OTA feasibility no longer mis-routes to "boot pins" ---------

def test_query_bootloader_feasibility_not_boot_pins(tmp_path):
    conn = _seeded(tmp_path)
    res = answer_query(conn, "Nucleo-F411RE", "can a fail safe bootloader be done?")
    assert res["confidence"] == "HIGH"
    assert "feasible" in res["answer"].lower()
    assert "RECOVERY_PRESENT" in res["answer"] and "PARTITION_AB_SYMMETRY" in res["answer"]
    assert "programming mode" not in res["answer"]      # NOT the boot-pin UNKNOWN deflection


def test_query_boot_pins_still_unknown(tmp_path):
    # A genuine boot-pin question (no bootloader/ota keyword) must still return the UNKNOWN.
    conn = _seeded(tmp_path)
    res = answer_query(conn, "Nucleo-F411RE", "what are the boot pin settings?")
    assert res["confidence"] == "UNKNOWN" and "BOOT0" in res["answer"]


# --- Fix 1: unknown-board on-ramp ------------------------------------------

def test_create_skeleton_board_and_normalize_arch(tmp_path):
    conn = _seeded(tmp_path)
    assert normalize_arch("Cortex-M4") == "arm-cortex-m4"
    assert normalize_arch("m7") == "arm-cortex-m7"
    assert normalize_arch("Xtensa-LX6") == "xtensa-lx6"
    bid = repo.create_skeleton_board(conn, "BrandNewBoard", normalize_arch("Cortex-M4"))
    assert bid
    board, soc = repo.load_board(conn, "BrandNewBoard")
    assert board["flash_bytes"] is None and soc["arch"] == "arm-cortex-m4"   # skeleton, null geo
