"""Tests for the Log Analysis Engine: format detection, deterministic signature matching,
crash-window slicing, and the post-filtered degraded LLM triage (fake provider)."""
import json

from eaedk.store.db import connect
from eaedk.store.migrate import migrate
from eaedk.seed import seed_all
from eaedk.engines.logs import analyze_log
from eaedk.engines.logs.parser import detect_format, crash_window
from eaedk.llm.gateway import Gateway


def _seeded(tmp_path):
    conn = connect(str(tmp_path / "t.db"))
    migrate(conn)
    seed_all(conn, force=True)
    return conn


def _write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return str(p)


def test_detect_format():
    uboot = "U-Boot 2023.04 (Jun 2026)\nDRAM:  512 MiB\nHit any key to stop autoboot:  0\n"
    dmesg = "[    0.000000] Booting Linux\n[    1.2] init\n[    2.3] done\n[    3.4] x\n"
    assert detect_format(uboot) == "uboot"
    assert detect_format(dmesg) == "dmesg"
    assert detect_format("just some text\nwith no markers") == "unknown"


def test_crash_window_centres_on_crash():
    lines = "\n".join(f"line {i}" for i in range(200))
    lines += "\nKernel panic - not syncing here"
    start, window, crash = crash_window(lines, size=100)
    assert len(window) == 100
    assert any("panic" in ln for ln in window)
    assert crash == 201


def test_uboot_crc_signature_match(tmp_path):
    conn = _seeded(tmp_path)
    log = _write(tmp_path, "u.log",
                 "U-Boot 2023.04\nDRAM:  512 MiB\n   Verifying Checksum ... Bad Data CRC\n"
                 "ERROR: can't get kernel image!\n")
    res = analyze_log(conn, log)
    assert res.format == "uboot"
    assert len(res.matches) == 1
    assert res.matches[0].severity == "HIGH"
    assert "CRC" in res.matches[0].line
    # persisted as a HIGH, signature-linked analysis
    row = conn.execute("SELECT confidence, signature_id FROM log_analyses").fetchone()
    assert row["confidence"] == "HIGH" and row["signature_id"] is not None


def test_kernel_panic_rootfs_match(tmp_path):
    conn = _seeded(tmp_path)
    log = _write(tmp_path, "k.log",
                 "[    0.0] Booting Linux\n"
                 "[    1.2] Kernel panic - not syncing: VFS: Unable to mount root fs\n")
    res = analyze_log(conn, log)
    assert res.format == "dmesg"
    assert any("root" in m.cause.lower() for m in res.matches)


class _FakeProvider:
    model = "fake"

    def __init__(self, text):
        self.text = text

    def available(self):
        return True

    def generate(self, system, prompt):
        return self.text


def test_degraded_triage_strips_invented_keeps_log_evidence(tmp_path):
    conn = _seeded(tmp_path)
    # An unmatched log that quotes a concrete address (0xdeadbeef).
    log = _write(tmp_path, "w.log",
                 "[ 2.10] mychip: probe start\n"
                 "[ 2.12] mychip: FATAL: handshake register stuck at 0xdeadbeef\n"
                 "[ 2.13] mychip: aborting probe\n")
    # Fake model invents a clock (168 MHz) but quotes the real log address as evidence.
    fake = json.dumps({"hypotheses": [{
        "cause": "The chip runs at 168 MHz and the handshake stalls.",
        "evidence_line": "mychip: FATAL: handshake register stuck at 0xdeadbeef",
        "suggested_check": "Inspect the handshake register."}], "confidence": "MEDIUM"})
    res = analyze_log(conn, log, use_llm=True, gateway=Gateway(provider=_FakeProvider(fake)))

    assert not res.matches                       # nothing in the signature DB matched
    t = res.triage
    assert t["available"] and t["confidence"] == "MEDIUM"
    h = t["hypotheses"][0]
    assert "168 MHz" not in h["cause"]           # invented frequency stripped
    assert t["removed"] >= 1
    assert "0xdeadbeef" in h["evidence_line"]    # log-quoted evidence preserved
    # persisted as a triage row (no signature, MEDIUM)
    row = conn.execute("SELECT signature_id, confidence FROM log_analyses").fetchone()
    assert row["signature_id"] is None and row["confidence"] == "MEDIUM"
