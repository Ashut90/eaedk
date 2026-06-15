"""Golden evals for the v3.0 P1 Beginner Mentor Layer (capabilities / recommendation / roadmap)."""
from eaedk.store.db import connect
from eaedk.store.migrate import migrate
from eaedk.seed import seed_all
from eaedk import repo
from eaedk.mentor_beginner import (
    render_board_capabilities, render_recommendation, render_roadmap,
    recommend_projects, arch_family_meaning, closest_board,
)


def _seeded(tmp_path):
    conn = connect(str(tmp_path / "t.db"))
    migrate(conn)
    seed_all(conn, force=True)
    return conn


def _order(text, *parts):
    """Assert the substrings appear in the given order (and all are present)."""
    idx = [text.index(p) for p in parts]          # raises if any missing
    assert idx == sorted(idx), (parts, idx)


# --- 1A: board capability summary, fully grounded -----------------------------------------

def test_1a_capabilities_grounded_in_facts(tmp_path):
    conn = _seeded(tmp_path)
    out = render_board_capabilities(conn, "STM32F103-BluePill")
    assert "Cortex-M3" in out and "no FPU" in out          # arch meaning for a beginner
    assert "64KB flash" in out and "20KB RAM" in out        # memory in human terms
    assert "UART" in out and "SPI" in out                   # confirmed peripherals
    # what it CAN validate
    assert "Flash capacity" in out and "RAM budget" in out and "Vector-table placement" in out
    # closest board by arch family + memory bracket
    assert "Nucleo-F103RB" in out
    assert "does not guess" in out                          # the honesty contract


def test_1a_unknown_facts_listed_explicitly(tmp_path):
    conn = _seeded(tmp_path)
    out = render_board_capabilities(conn, "RTL8722DM")       # flash + RAM geometry is UNKNOWN
    assert "CANNOT yet validate" in out
    assert "Flash capacity — flash size is UNKNOWN" in out
    assert "RAM budget — RAM size is UNKNOWN" in out


def test_1a_arch_meaning_distinguishes_m0_m3_m4():
    assert "no hardware floating point" in arch_family_meaning("arm-cortex-m0plus")[1]
    assert "no FPU" in arch_family_meaning("arm-cortex-m3")[1]
    assert "FPU" in arch_family_meaning("arm-cortex-m4")[1] and "DSP" in arch_family_meaning("arm-cortex-m4")[1]


def test_1a_closest_board_same_family(tmp_path):
    conn = _seeded(tmp_path)
    near = closest_board(conn, "STM32F103-BluePill")
    assert near and near["name"] == "Nucleo-F103RB"          # same M3, nearest flash


# --- 1B: project recommendation, dependency-ordered ---------------------------------------

def test_1b_blue_pill_level0_dependency_ordered(tmp_path):
    conn = _seeded(tmp_path)
    out = render_recommendation(conn, "STM32F103-BluePill", 0)
    # GOLDEN: first project Blink, then UART -> SPI -> interrupt, in dependency order
    _order(out, "Blink an LED", "UART Logger", "Read an SPI Sensor", "Handle an Interrupt")
    assert "depends on:" in out                              # explicit dependency justification
    low = out.lower()
    assert "tflite" not in low and "grpc" not in low         # no fantasy projects for a 64KB MCU


def test_1b_unknown_peripheral_not_recommended(tmp_path):
    """A board whose SPI/UART presence is NOT confirmed must not get an SPI/UART project — it is
    flagged as unconfirmed, never assumed (golden: scoped to confirmed peripherals only)."""
    conn = _seeded(tmp_path)
    # A minimal board with ONLY gpio confirmed (test-local fixture).
    src = conn.execute("INSERT INTO sources(type,title,uri,hash,created_at) "
                       "VALUES('user','t',NULL,NULL,'now')").lastrowid
    soc = conn.execute("INSERT INTO socs(name,vendor,arch,notes) "
                       "VALUES('BareSoC','x','arm-cortex-m3',NULL)").lastrowid
    conn.execute("INSERT INTO boards(soc_id,name,flash_base,flash_bytes,ram_base,ram_bytes,"
                 "ddr_type,ddr_bytes,primary_storage,boot_modes_json,source_id,confidence,"
                 "flash_endurance_cycles) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                 (soc, "BareM3", 0x08000000, 65536, 0x20000000, 20480, None, None,
                  "internal_flash", "[]", src, "HIGH", 10000))
    repo.add_board_capability(conn, "BareM3", "gpio")
    conn.commit()

    rec = recommend_projects(conn, "BareM3", 0)
    rec_titles = [s["title"] for s in rec["recommended"]]
    not_titles = [d["title"] for d in rec["not_recommended"]]
    assert "Blink an LED" in rec_titles                      # confirmed (needs nothing)
    assert "Read an SPI Sensor" not in rec_titles            # SPI not confirmed -> not recommended
    assert "UART Logger (print over serial)" not in rec_titles
    assert "Read an SPI Sensor" in not_titles and "UART Logger (print over serial)" in not_titles

    out = render_recommendation(conn, "BareM3", 0)
    assert "NOT recommended yet" in out and "not assume" in out.lower()


# --- 1C: job-focused roadmap, dependency graph, multi-board -------------------------------

def test_1c_roadmap_golden_sequence_blue_pill(tmp_path):
    conn = _seeded(tmp_path)
    out = render_roadmap(conn, ["STM32F103-BluePill"], "firmware-job")
    # GOLDEN: Blink -> UART logger -> SPI sensor -> interrupt -> bootloader, in order
    _order(out, "Blink an LED", "UART Logger", "Read an SPI Sensor",
           "Handle an Interrupt", "Write a Bootloader")
    assert "Proves to an interviewer" in out                 # job framing, not just "teaches"
    low = out.lower()
    assert "tflite" not in low and "grpc" not in low


def test_1c_roadmap_multiboard_splits_tiers(tmp_path):
    conn = _seeded(tmp_path)
    out = render_roadmap(conn, ["STM32F103-BluePill", "Nucleo-F411RE"], "firmware-job")
    # GOLDEN: uses both boards, Blue Pill first (fundamentals), Nucleo-64 for RTOS/HAL
    assert "STM32F103-BluePill" in out and "Nucleo-F411RE" in out
    fundamentals_line = next(l for l in out.splitlines() if "Bare-metal fundamentals" in l)
    assert "STM32F103-BluePill" in fundamentals_line        # Blue Pill = fundamentals board
    # the bootloader / RTOS tier is assigned to the more capable board
    boot_line = next(l for l in out.splitlines() if "Write a Bootloader" in l)
    rtos_line = next(l for l in out.splitlines() if "Run an RTOS" in l)
    assert "Nucleo-F411RE" in boot_line and "Nucleo-F411RE" in rtos_line
    # fundamentals stay on the Blue Pill
    blink_line = next(l for l in out.splitlines() if "Blink an LED" in l)
    assert "STM32F103-BluePill" in blink_line
