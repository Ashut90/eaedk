"""Conceptual-guard tests — precision FIRST, then recall.

Precision is the contract: a guard must never flag a correct statement, because
a false positive blocks a good answer and erodes the trust EAEDK exists to
protect. Recall is secondary: we accept that the curated guards miss phrasings,
but every trap we *do* claim to catch must be caught.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eaedk.conceptual_guards import flag_conceptual_errors, GUARDS


# ── PRECISION: correct statements must produce ZERO violations ──────────────────────────

CLEAN = [
    "",
    "SPI runs at up to 10 MHz on this board.",
    "SPI uses chip-select, not pull-up resistors like I2C.",
    "Unlike I2C, SPI does not need pull-ups.",
    "SPI is push-pull, so no pull-up resistors are required.",
    "I2C is open-drain, so it requires pull-up resistors on SDA and SCL.",
    "I2C needs pull-up resistors; 4.7k is a reasonable starting value.",
    "For I2C with 10 sensors, 4.7k pull-ups are reasonable.",
    "UART is asynchronous; there is no shared clock line, just an agreed baud rate.",
    "Set the UART peripheral clock via RCC before you transmit.",
    "Configure the UART internal clock so the baud rate is accurate.",
    "Communication systems: wired buses (UART/SPI/I2C/CAN) and wireless links (BLE/Wi-Fi/LoRa).",
    "SPI is fast. I2C needs pull-up resistors.",          # cross-sentence: must NOT combine
    "Add a decoupling capacitor near the SPI flash chip.",  # 'add' + spi but no pull-up
]


def test_precision_clean_statements_are_never_flagged():
    for text in CLEAN:
        assert flag_conceptual_errors(text) == [], f"FALSE POSITIVE on: {text!r}"


# ── RECALL: the curated traps must be caught ────────────────────────────────────────────

SPI_PULLUP_ERRORS = [
    "For your SPI bus, add 4.7k pull-up resistors on MOSI and MISO.",
    "You should put pull-ups on the SPI clock line.",
    "Wire pull-up resistors to your SPI lines for reliability.",
    "Your SPI bus needs pull-up resistors to work.",
]

I2C_DENY_ERRORS = [
    "I2C doesn't need pull-up resistors, they're optional.",
    "On I2C, pull-ups are unnecessary.",
    "You can run I2C without pull-up resistors.",
    "For I2C, the pull-ups are optional if the bus is short.",
]

UART_CLOCK_ERRORS = [
    "Connect a clock line for your UART so both sides stay in sync.",
    "UART needs a shared clock signal between the two devices.",
    "Add an SCLK wire so your UART stays synchronised.",
]


def test_recall_spi_pullup_trap_is_flagged():
    for text in SPI_PULLUP_ERRORS:
        v = flag_conceptual_errors(text)
        assert v, f"MISSED SPI pull-up error: {text!r}"
        assert "push-pull" in v[0]


def test_recall_i2c_denial_trap_is_flagged():
    for text in I2C_DENY_ERRORS:
        v = flag_conceptual_errors(text)
        assert v, f"MISSED I2C denial error: {text!r}"
        assert "open-drain" in v[0]


def test_recall_uart_clock_trap_is_flagged():
    for text in UART_CLOCK_ERRORS:
        v = flag_conceptual_errors(text)
        assert v, f"MISSED UART clock-line error: {text!r}"
        assert "asynchronous" in v[0]


# ── Properties ──────────────────────────────────────────────────────────────────────────

def test_each_guard_reports_at_most_once():
    text = ("Add pull-ups to your SPI bus. Also put pull-up resistors on the SPI lines. "
            "And wire more pull-ups onto SPI.")
    v = flag_conceptual_errors(text)
    assert len(v) == 1               # spi_pullups fires once, not three times


def test_deterministic_same_text_same_result():
    text = "Add 4.7k pull-up resistors to your SPI bus."
    assert flag_conceptual_errors(text) == flag_conceptual_errors(text)


def test_guard_registry_names_are_unique():
    names = [g.name for g in GUARDS]
    assert len(names) == len(set(names))
