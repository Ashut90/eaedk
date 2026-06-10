"""Tests for the Log Analysis Engine: format detection, deterministic signature matching,
crash-window slicing, and the post-filtered degraded LLM triage (fake provider)."""
import json

from eaedk.store.db import connect
from eaedk.store.migrate import migrate
from eaedk.seed import seed_all
from eaedk import repo
from eaedk.engines.logs import analyze_log
from eaedk.engines.logs.engine import build_correlation
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


def test_pll_lock_signature_match(tmp_path):
    conn = _seeded(tmp_path)
    log = _write(tmp_path, "pll.log",
                 "U-Boot SPL 2023.04\nClock init...\nERROR: PLL lock timeout\n")
    res = analyze_log(conn, log)
    assert res.matches and any("PLL" in m.line for m in res.matches)
    assert any("pll" in m.cause.lower() for m in res.matches)


def test_secure_boot_signature_match(tmp_path):
    conn = _seeded(tmp_path)
    log = _write(tmp_path, "sb.log",
                 "U-Boot 2023.04\n## Loading kernel from FIT Image\n"
                 "   Verifying Hash Integrity ... sha256,rsa2048 Failed\nBad signature\n")
    res = analyze_log(conn, log)
    assert res.matches and any("signature" in m.cause.lower() for m in res.matches)


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


def _uboot_project(conn, name="bringup"):
    # A U-Boot project with an engaged-UNKNOWN (DDR timing) gap and a fired risk.
    repo.create_project(conn, name, "uboot", "STM32MP157")
    p = repo.get_project(conn, name)
    repo.set_input(conn, p["id"], "console_uart", "UART4", confidence="HIGH")
    return repo.get_project(conn, name)


def test_build_correlation_collects_gaps_and_unverified(tmp_path):
    conn = _seeded(tmp_path)
    p = _uboot_project(conn)
    # add an unverified (MEDIUM) board fact via the canonical write-through
    bid = conn.execute("SELECT id FROM boards WHERE name='STM32MP157'").fetchone()["id"]
    with conn:
        repo.record_fact(conn, board_id=bid, domain="CLOCK", kind="clock",
                         fact_key="pll_cfg", fact_value="assumed-default",
                         source_type="USER_INPUT", confidence="MEDIUM")
    corr = build_correlation(conn, p)
    checks = {g["check"]: g["status"] for g in corr["validation_gaps"]}
    assert checks.get("DDR_TIMING_VERIFIED") == "UNKNOWN"      # engaged-unknown gap surfaced
    assert any(r["rule_key"] == "DDR_GUESSED" for r in corr["risks"])
    assert any(f["key"] == "pll_cfg" and f["confidence"] == "MEDIUM"
               for f in corr["unverified_facts"])


def test_project_aware_triage_injects_correlation(tmp_path):
    conn = _seeded(tmp_path)
    p = _uboot_project(conn)
    log = _write(tmp_path, "boot.log",
                 "U-Boot 2023.04\nDRAM:  512 MiB\nsome unrecognized failure here\n")
    captured = {}

    class _CapturingProvider:
        model = "fake"
        def available(self):
            return True
        def generate(self, system, prompt):
            captured["prompt"] = prompt
            return json.dumps({"hypotheses": [{"cause": "unclear",
                               "evidence_line": "some unrecognized failure here",
                               "suggested_check": "review"}], "confidence": "LOW"})

    res = analyze_log(conn, log, project_name="bringup", use_llm=True,
                      project_aware=True, gateway=Gateway(provider=_CapturingProvider()))
    # the correlation block reached the model and is attached to the result
    assert "PROJECT CONTEXT" in captured["prompt"]
    assert "DDR_TIMING_VERIFIED" in captured["prompt"]
    assert res.triage["correlation"]["project"] == "bringup"


class _DDRProvider:
    model = "fake"
    def available(self):
        return True
    def generate(self, system, prompt):
        return json.dumps({"hypotheses": [{
            "cause": "DDR timing not verified, likely stalling early relocation",
            "evidence_line": "some unrecognized failure here",
            "suggested_check": "verify DDR timing"}], "confidence": "MEDIUM"})


def test_write_back_opens_note_and_tracked_risk(tmp_path):
    conn = _seeded(tmp_path)
    _uboot_project(conn, "wb")
    log = _write(tmp_path, "b.log", "U-Boot 2023.04\nDRAM:  512 MiB\nunrecognized failure\n")

    res = analyze_log(conn, log, project_name="wb", use_llm=True, project_aware=True,
                      gateway=Gateway(provider=_DDRProvider()))
    # implicated DDR_TIMING_VERIFIED (an engaged-UNKNOWN gap) -> note + tracked risk
    wb = {w["rule"]: w for w in res.write_backs}
    assert "DDR_TIMING_VERIFIED" in wb
    assert wb["DDR_TIMING_VERIFIED"]["severity"] == "HIGH"   # inherited from the rule
    assert wb["DDR_TIMING_VERIFIED"]["risk"] == "opened"
    assert "ddr_init" in wb["DDR_TIMING_VERIFIED"]["items"]

    p = repo.get_project(conn, "wb")
    note = conn.execute(
        "SELECT pc.note FROM project_checklist pc JOIN template_items ti "
        "ON ti.id=pc.template_item_id WHERE pc.project_id=? AND ti.item_key='ddr_init'",
        (p["id"],)).fetchone()["note"]
    assert note and "log-triage" in note and "b.log" in note

    risk = conn.execute(
        "SELECT severity, status FROM risks WHERE project_id=? AND rule_key='DDR_TIMING_VERIFIED'",
        (p["id"],)).fetchall()
    assert len(risk) == 1 and risk[0]["severity"] == "HIGH" and risk[0]["status"] == "tracked"

    # Re-run: appends to the SAME tracked risk (no duplicate row), and survives the
    # replace_risks() wipe that assess_project triggers.
    res2 = analyze_log(conn, log, project_name="wb", use_llm=True, project_aware=True,
                       gateway=Gateway(provider=_DDRProvider()))
    assert res2.write_backs[0]["risk"] == "appended"
    n = conn.execute(
        "SELECT COUNT(*) FROM risks WHERE project_id=? AND rule_key='DDR_TIMING_VERIFIED'",
        (p["id"],)).fetchone()[0]
    assert n == 1
