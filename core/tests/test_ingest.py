"""Golden tests for datasheet ingestion: pure extraction, a real PDF round-trip (fitz),
no-silent-writes staging, and confirm/reject. Confidence by method; UNKNOWN over guessing."""
import pytest

from eaedk.store.db import connect
from eaedk.store.migrate import migrate
from eaedk.seed import seed_all
from eaedk import repo
from eaedk.engines.ingest import ingest_datasheet, confirm_candidate, reject_candidate
from eaedk.engines.ingest.extract import Page, extract_from_pages

# A synthetic datasheet excerpt with the values we expect to extract.
DATASHEET_LINES = [
    "3.2 Memory mapping",
    "Flash memory  0x08000000  0x0807FFFF",
    "SRAM          0x20000000  0x2001FFFF",
    "The device embeds 512 Kbytes of embedded Flash memory",
    "and 128 Kbytes of SRAM for data storage.",
    "4.1 Clocks",
    "The system clock can run up to 100 MHz.",
    "This paragraph mentions nothing extractable at all.",
]


def _by_key(cands):
    return {c.fact_key: c for c in cands}


def test_extract_assigns_method_and_confidence():
    pages = [Page(number=7, text="\n".join(DATASHEET_LINES))]
    c = _by_key(extract_from_pages(pages))
    # structured memory-map lines -> HIGH (table)
    assert c["flash_base"].fact_value == "0x08000000" and c["flash_base"].confidence == "HIGH"
    assert c["ram_base"].fact_value == "0x20000000" and c["ram_base"].method == "table"
    # prose sizes -> MEDIUM (text)
    assert c["flash_bytes"].fact_value == str(512 * 1024) and c["flash_bytes"].confidence == "MEDIUM"
    assert c["ram_bytes"].fact_value == str(128 * 1024)
    # clock ceiling -> MEDIUM, in Hz
    assert c["sysclk_max_hz"].fact_value == str(100 * 1_000_000)
    # provenance: page + nearest section heading + snippet
    assert c["flash_base"].page == 7 and c["flash_base"].section == "3.2 Memory mapping"
    assert "0x08000000" in c["flash_base"].snippet
    assert c["sysclk_max_hz"].section == "4.1 Clocks"


def test_no_guess_when_absent():
    pages = [Page(number=1, text="An MCU with great peripherals and low power modes.\n")]
    assert extract_from_pages(pages) == []     # nothing extractable -> nothing, not a guess


def _make_pdf(tmp_path):
    fitz = pytest.importorskip("fitz")
    doc = fitz.open()
    page = doc.new_page()
    y = 72
    for line in DATASHEET_LINES:
        page.insert_text((72, y), line)
        y += 16
    path = str(tmp_path / "ds.pdf")
    doc.save(path)
    doc.close()
    return path


def _seeded(tmp_path):
    conn = connect(str(tmp_path / "t.db"))
    migrate(conn)
    seed_all(conn, force=True)
    return conn


def _facts_for(conn, board):
    return conn.execute(
        "SELECT COUNT(*) FROM facts f JOIN boards b ON b.id=f.board_id WHERE b.name=?",
        (board,)).fetchone()[0]


def test_ingest_pdf_stages_candidates_without_writing_facts(tmp_path):
    conn = _seeded(tmp_path)
    pdf = _make_pdf(tmp_path)
    res = ingest_datasheet(conn, pdf, "Nucleo-F411RE")
    assert res.counts.get("HIGH") == 2 and res.counts.get("MEDIUM") == 3
    # staged, but NOTHING written to the knowledge base yet (human-in-the-loop)
    assert _facts_for(conn, "Nucleo-F411RE") == 0
    pending = repo.list_fact_candidates(conn, "Nucleo-F411RE", "pending")
    assert len(pending) == 5
    # the datasheet source carries the filename + hash
    src = conn.execute("SELECT type,title,hash FROM sources WHERE id=?",
                       (res.source_id,)).fetchone()
    assert src["type"] == "datasheet" and src["title"] == "ds.pdf" and src["hash"]


def test_confirm_commits_with_datasheet_provenance(tmp_path):
    conn = _seeded(tmp_path)
    ingest_datasheet(conn, _make_pdf(tmp_path), "Nucleo-F411RE")
    cand = next(c for c in repo.list_fact_candidates(conn, "Nucleo-F411RE", "pending")
                if c["fact_key"] == "flash_base")
    assert confirm_candidate(conn, cand["id"]) == "confirmed"

    row = conn.execute(
        "SELECT ef.fact_value, ef.source_type, ef.citation_page, ef.citation_detail, "
        "ef.verified_by_human FROM engineering_facts ef JOIN boards b ON b.id=ef.board_id "
        "WHERE b.name='Nucleo-F411RE' AND ef.fact_key='flash_base'").fetchone()
    assert row["fact_value"] == "0x08000000"
    assert row["source_type"] == "DATASHEET"
    assert row["citation_page"] == 1                      # synthetic PDF is one page
    assert row["citation_detail"] == "3.2 Memory mapping"
    assert row["verified_by_human"] == 1                 # confirmation == verification
    # candidate is no longer pending; re-confirm is a no-op
    assert confirm_candidate(conn, cand["id"]) == "already"


def test_reject_discards_without_committing(tmp_path):
    conn = _seeded(tmp_path)
    ingest_datasheet(conn, _make_pdf(tmp_path), "Nucleo-F411RE")
    cand = repo.list_fact_candidates(conn, "Nucleo-F411RE", "pending")[0]
    assert reject_candidate(conn, cand["id"]) == "rejected"
    assert _facts_for(conn, "Nucleo-F411RE") == 0
    assert repo.get_fact_candidate(conn, cand["id"])["status"] == "rejected"
