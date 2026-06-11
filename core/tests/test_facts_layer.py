"""Tests for the canonical engineering-fact layer: record_fact() write-through, the
engineering_facts VIEW, preserved provenance, and postfilter reading through the VIEW."""
from eaedk.store.db import connect
from eaedk.store.migrate import migrate
from eaedk.seed import seed_all
from eaedk import repo
from eaedk.llm.postfilter import build_allowlist


def _seeded(tmp_path):
    conn = connect(str(tmp_path / "t.db"))
    migrate(conn)
    seed_all(conn, force=True)
    return conn


def _board_id(conn, name):
    return conn.execute("SELECT id FROM boards WHERE name=?", (name,)).fetchone()["id"]


def test_record_fact_writes_through_with_structured_provenance(tmp_path):
    conn = _seeded(tmp_path)
    bid = _board_id(conn, "STM32H743")
    with conn:
        fid = repo.record_fact(
            conn, board_id=bid, domain="CLOCK", kind="clock",
            fact_key="sysclk_hz", fact_value=480000000, source_type="DATASHEET",
            confidence="HIGH", citation_page=42, citation_section="Table 12, RCC",
            snippet="SYSCLK max 480 MHz")
    row = conn.execute(
        "SELECT domain, source_type, fact_key, fact_value, citation_page, citation_detail, "
        "source_doc_type, verified_by_human FROM engineering_facts WHERE id=?", (fid,)).fetchone()
    assert row["domain"] == "CLOCK"
    assert row["source_type"] == "DATASHEET"
    assert row["fact_value"] == "480000000"
    # provenance is preserved, not flattened:
    assert row["citation_page"] == 42
    assert row["citation_detail"] == "Table 12, RCC"
    assert row["source_doc_type"] == "datasheet"
    assert row["verified_by_human"] == 1            # HIGH -> verified


def test_unknown_source_type_rejected(tmp_path):
    conn = _seeded(tmp_path)
    bid = _board_id(conn, "STM32H743")
    import pytest
    with pytest.raises(ValueError):
        with conn:
            repo.record_fact(conn, board_id=bid, domain="MEMORY", fact_key="x",
                             fact_value=1, source_type="BOGUS", confidence="LOW")


def test_onboarding_partitions_land_in_engineering_facts(tmp_path):
    conn = _seeded(tmp_path)
    from eaedk.onboard import run_wizard
    answers = [
        "F407", "ST", "STM32F407", "3", "1MB", "0x08000000", "192KB", "0x20000000", "",
        "0x0", "64KB", "0x10000", "320KB", "0x60000", "320KB", "0xB0000", "320KB",
        "",  # capabilities -> common MCU set
        "n",
    ]
    it = iter(answers)
    run_wizard(conn, lambda p: next(it), lambda s: None)

    rows = conn.execute(
        "SELECT fact_key, domain, source_type FROM engineering_facts ef "
        "JOIN boards b ON b.id=ef.board_id WHERE b.name='F407' ORDER BY fact_key").fetchall()
    assert {r["fact_key"] for r in rows} == {"bootloader", "slot_a", "slot_b", "slot_c"}
    assert all(r["domain"] == "MEMORY" for r in rows)
    assert all(r["source_type"] == "USER_INPUT" for r in rows)


def test_postfilter_allowlist_reads_through_view(tmp_path):
    conn = _seeded(tmp_path)
    from eaedk.onboard import run_wizard
    answers = [
        "F407b", "ST", "STM32F407b", "3", "1MB", "0x08000000", "192KB", "0x20000000", "",
        "0x0", "64KB", "0x10000", "320KB", "0x60000", "320KB", "0xB0000", "320KB",
        "",  # capabilities -> common MCU set
        "n",
    ]
    it = iter(answers)
    run_wizard(conn, lambda p: next(it), lambda s: None)
    repo.create_project(conn, "proj", "ota", "F407b")
    allow = build_allowlist(conn, repo.get_project(conn, "proj"))
    # offsets from the facts AND the absolute slot-A address (flash_base + 0x10000):
    assert 0x10000 in allow.numbers
    assert 0x08000000 + 0x10000 in allow.numbers
