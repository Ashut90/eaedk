"""Full-datasheet digest: the deterministic scan reads EVERY page, categorises salient facts with
page cites, captures identity so the core is never guessed, and stays grounded offline."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eaedk.engines.ingest.extract import Page
from eaedk.engines.ingest import digest

_PAGES = [
    Page(1, "STM32F103xB Datasheet\n1 Introduction\nThe STM32F103xB is an ARM Cortex-M3 microcontroller."),
    Page(2, "2 Memory\nFlash memory: 128 Kbytes.\nSRAM: 20 Kbytes.\nFlash base address 0x08000000."),
    Page(3, "3 Clocks\nThe CPU runs up to 72 MHz maximum.\nHSI internal oscillator is 8 MHz."),
    Page(4, "4 Power\nOperating voltage 2.0 V to 3.6 V.\nOperating temperature -40 to 85 °C."),
    Page(5, "5 Peripherals\n3x USART, 2x SPI, 2x I2C, 1x USB, 2x ADC, 7-channel DMA."),
    Page(6, "6 Absolute maximum ratings\nCaution: exceeding VDD 4.0 V will cause permanent damage."),
]


def test_scan_reads_every_page_and_categorises():
    dg = digest.scan(_PAGES)
    assert dg.scanned_pages == 6                              # every page with text was read
    assert dg.facts.get("Memory & map") and dg.facts.get("Clocks & timing")
    assert dg.facts.get("Cautions & limits")                 # the caution was surfaced
    assert {"USART", "SPI", "I2C", "USB", "ADC", "DMA"} <= set(dg.peripherals)
    assert len(dg.sections) >= 5                              # section coverage map


def test_identity_is_captured_so_core_is_grounded_not_guessed():
    dg = digest.scan(_PAGES)
    ident = " ".join(line for line, _ in dg.facts.get("Identity & core", []))
    assert "Cortex-M3" in ident                              # the real core is in the extracted facts
    # the deterministic 'what to remember' reports the real core, never a guess
    assert any("Cortex-M3" in line for line in digest.key_facts(dg))
    # the synthesis prompt also forbids guessing / describing another part
    assert "never guess" in digest._SYNTH_SYSTEM.lower()
    assert "NOT this chip" in digest._SYNTH_SYSTEM


def test_verified_geometry_has_page_cites():
    dg = digest.scan(_PAGES)
    keys = {v["key"]: v for v in dg.verified}
    assert "flash_bytes" in keys and keys["flash_bytes"]["value"] == "131072"
    assert all(isinstance(v["page"], int) for v in dg.verified)


def test_render_offline_is_grounded_no_model_needed():
    dg = digest.analyze(_PAGES, "stm32f103.pdf", gw=None)     # no model
    out = digest.render(dg)
    assert "Datasheet digest — stm32f103.pdf" in out
    # the deterministic 'what to remember' stands on its own — grounded, page-cited, no model
    assert "What to remember (grounded" in out
    assert "Cortex-M3" in out and "0x08000000" in out
    assert "permanent damage" in out                          # the caution was surfaced
    assert dg.summary == ""                                   # no model → no AI briefing, no fabrication
