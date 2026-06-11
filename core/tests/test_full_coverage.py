"""Golden tests for v1.8.0-full-coverage — the beginner + mid-level coverage gaps.

Every addition is exercised here: the guided UART-debug flow, the silent-boot signature, the
per-family common-mistakes catalogue, the between-projects --next guidance, the multicore and
low_power templates, the secure-boot validation rules, and the RTOS log signatures. All
deterministic; no LLM.
"""
from eaedk.store.db import connect
from eaedk.store.migrate import migrate
from eaedk.seed import seed_all
from eaedk import repo, mentor
from eaedk.orchestrator import assess
from eaedk.engines.logs import analyze_log
from eaedk.mentor_llm import mentor_explain


def _seeded(tmp_path):
    conn = connect(str(tmp_path / "t.db"))
    migrate(conn)
    seed_all(conn, force=True)
    return conn


def _write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return str(p)


# --- B1: UART-debug guided flow -------------------------------------------

def test_uart_debug_concept_is_a_guided_flow(tmp_path):
    conn = _seeded(tmp_path)
    out = mentor_explain(conn, "STM32F103-BluePill", "UART-debug", use_llm=False)
    # The ordered checklist a beginner needs when serial is dead.
    assert "baud" in out.lower() and "115200" in out
    assert "init" in out.lower()                          # clock/init order
    assert "TX" in out                                    # wiring


def test_board_mentor_points_to_debug_flows(tmp_path):
    conn = _seeded(tmp_path)
    out = mentor.render_board_mentor(conn, "STM32F103-BluePill")
    assert "--explain UART-debug" in out
    assert "--common-mistakes" in out


# --- B2: silent-boot signature --------------------------------------------

def test_empty_log_is_diagnosed_as_silent_boot(tmp_path):
    conn = _seeded(tmp_path)
    log = _write(tmp_path, "silent.log", "")            # no output at all
    res = analyze_log(conn, log)
    assert res.format == "silent"
    assert len(res.matches) == 1
    assert "BOOT0" in res.matches[0].fix                  # the curated STM32 hint
    assert res.matches[0].severity == "HIGH"


def test_whitespace_only_log_is_silent(tmp_path):
    conn = _seeded(tmp_path)
    res = analyze_log(conn, _write(tmp_path, "ws.log", "   \n\n  \n"))
    assert res.format == "silent" and res.matches


def test_nonempty_gibberish_still_not_silent(tmp_path):
    # Regression guard: the existing "no match -> suggest --llm" behaviour is unchanged for
    # a log that actually has content.
    conn = _seeded(tmp_path)
    res = analyze_log(conn, _write(tmp_path, "g.log", "totally unrecognizable\nno markers\n"))
    assert not res.matches
    assert "Run with --llm for triage." in res.to_markdown()


# --- B3: common first mistakes per family ---------------------------------

def test_family_mapping():
    assert mentor.family_of("STM32F103C8") == "stm32"
    assert mentor.family_of("RP2040") == "rp2040"
    assert mentor.family_of("ESP32") == "esp32"
    assert mentor.family_of("ATmega328P") == "avr"
    assert mentor.family_of("BCM2711") is None


def test_common_mistakes_stm32_mentions_boot0(tmp_path):
    conn = _seeded(tmp_path)
    out = mentor.render_common_mistakes(conn, "STM32F103-BluePill")
    assert "BOOT0" in out and "clock" in out.lower()


def test_common_mistakes_rp2040_mentions_boot2(tmp_path):
    conn = _seeded(tmp_path)
    out = mentor.render_common_mistakes(conn, "Raspberry-Pi-Pico")
    assert "boot2" in out.lower() or "0x10000000" in out


def test_common_mistakes_esp32_mentions_partition_and_nvs(tmp_path):
    conn = _seeded(tmp_path)
    out = mentor.render_common_mistakes(conn, "ESP32-DevKitC")
    assert "partition" in out.lower() and "nvs" in out.lower()


def test_common_mistakes_unknown_family_is_graceful(tmp_path):
    conn = _seeded(tmp_path)
    out = mentor.render_common_mistakes(conn, "BeagleBone-Black")   # no family seeded
    assert out is not None and "general rule" in out.lower()


# --- B4: mentor --next ----------------------------------------------------

def test_next_default_is_first_step_with_intro_and_concept(tmp_path):
    conn = _seeded(tmp_path)
    out = mentor.render_next_step(conn, "STM32F103-BluePill")
    assert "Blink an LED" in out
    assert "What it introduces" in out
    assert "New concept" in out                           # cross-linked concept surfaced


def test_next_after_completed_step_advances(tmp_path):
    conn = _seeded(tmp_path)
    out = mentor.render_next_step(conn, "STM32F103-BluePill", completed_key="blink")
    assert "UART Logger" in out                           # the step after blink


def test_next_after_last_unlocked_step_congratulates(tmp_path):
    conn = _seeded(tmp_path)
    path = mentor.learning_path_for(conn, repo.board_capability_names(conn, "STM32F103-BluePill"))
    out = mentor.render_next_step(conn, "STM32F103-BluePill", completed_key=path[-1]["key"])
    assert "finished" in out.lower()


# --- M1: multicore template -----------------------------------------------

def test_multicore_template_seeded_with_required_coverage(tmp_path):
    conn = _seeded(tmp_path)
    row = conn.execute("SELECT id FROM templates WHERE goal_type='multicore'").fetchone()
    assert row is not None
    items = [r["item_key"] for r in conn.execute(
        "SELECT item_key FROM template_items WHERE template_id=?", (row["id"],)).fetchall()]
    # the four mandated concerns: boot order, inter-core comms, shared memory, peripheral ownership
    assert {"core_boot_order", "intercore_comms", "shared_memory_regions",
            "peripheral_ownership"} <= set(items)


# --- M2: secure boot chain rules ------------------------------------------

def test_secure_boot_all_present_passes(tmp_path):
    conn = _seeded(tmp_path)
    resp = assess(conn, "bootloader", {
        "secure_boot_sig_verify": True, "secure_boot_key_storage": "efuse",
        "secure_boot_rollback_counter": True, "secure_boot_debug_locked": True},
        board_name="STM32H743")
    by = {v["check"]: v["status"] for v in resp.validations}
    assert by["SECURE_BOOT_SIGNATURE_VERIFY"] == "PASS"
    assert by["SECURE_BOOT_KEY_STORAGE"] == "PASS"
    assert by["SECURE_BOOT_ROLLBACK_COUNTER"] == "PASS"
    assert by["SECURE_BOOT_DEBUG_LOCKED"] == "PASS"
    assert resp.feasibility == "feasible"


def test_secure_boot_debug_open_fails(tmp_path):
    conn = _seeded(tmp_path)
    resp = assess(conn, "bootloader", {"secure_boot_debug_locked": False},
                  board_name="STM32H743")
    by = {v["check"]: v["status"] for v in resp.validations}
    assert by["SECURE_BOOT_DEBUG_LOCKED"] == "FAIL"
    assert resp.feasibility == "not_feasible"


def test_secure_boot_unconfigured_is_unknown_not_engaged(tmp_path):
    conn = _seeded(tmp_path)
    # A plain bootloader project that doesn't mention secure boot: the rules are UNKNOWN but
    # NOT engaged, so they don't block — exactly how the existing bootloader cases stay feasible.
    resp = assess(conn, "bootloader", {"secure_boot_sig_verify": True}, board_name="STM32H743")
    by = {v["check"]: v for v in resp.validations}
    assert by["SECURE_BOOT_DEBUG_LOCKED"]["status"] == "UNKNOWN"
    assert by["SECURE_BOOT_DEBUG_LOCKED"]["engaged"] is False
    assert resp.feasibility == "feasible"


def test_secure_boot_rules_absent_for_non_secure_goals(tmp_path):
    conn = _seeded(tmp_path)
    # Scoped to bootloader/ota only — a bare_metal_app must not suddenly carry secure-boot rules.
    resp = assess(conn, "bare_metal_app", {}, board_name="STM32F103-BluePill")
    checks = {v["check"] for v in resp.validations}
    assert not any(c.startswith("SECURE_BOOT_") for c in checks)


# --- M3: RTOS log signatures ----------------------------------------------

def test_rtos_stack_overflow_signature(tmp_path):
    conn = _seeded(tmp_path)
    log = _write(tmp_path, "so.log",
                 "boot ok\nvApplicationStackOverflowHook: task 'sensor' overflowed its stack\n")
    res = analyze_log(conn, log)
    assert any("stack overflow" in m.cause.lower() for m in res.matches)
    assert any("high-water" in m.fix.lower() or "stack depth" in m.fix.lower() for m in res.matches)


def test_rtos_task_starvation_signature(tmp_path):
    conn = _seeded(tmp_path)
    log = _write(tmp_path, "wd.log",
                 "running\nE (5234) task_wdt: Task watchdog got triggered.\n")
    res = analyze_log(conn, log)
    assert any("starvation" in m.cause.lower() for m in res.matches)


def test_rtos_deadlock_signature(tmp_path):
    conn = _seeded(tmp_path)
    log = _write(tmp_path, "dl.log", "up\npotential deadlock detected on mutex held by task A\n")
    res = analyze_log(conn, log)
    assert any("deadlock" in m.cause.lower() for m in res.matches)


def test_existing_mcu_signatures_still_work(tmp_path):
    # Regression guard: adding RTOS/silent signatures didn't disturb the HardFault match.
    conn = _seeded(tmp_path)
    log = _write(tmp_path, "hf.log",
                 "app: running\n[FAULT] HardFault_Handler: forced exception\nHFSR=0x40000000\n")
    res = analyze_log(conn, log)
    assert res.format == "mcu"
    assert any("HardFault" in m.cause for m in res.matches)


# --- M4: low_power template -----------------------------------------------

def test_low_power_template_seeded_with_required_items(tmp_path):
    conn = _seeded(tmp_path)
    row = conn.execute("SELECT id FROM templates WHERE goal_type='low_power'").fetchone()
    assert row is not None
    items = [r["item_key"] for r in conn.execute(
        "SELECT item_key FROM template_items WHERE template_id=?", (row["id"],)).fetchall()]
    assert {"sleep_entry_exit_sequence", "peripheral_clock_gating",
            "wakeup_source_configured"} <= set(items)


def test_power_sequence_rule_unchanged(tmp_path):
    # M4 must NOT have altered POWER_SEQUENCE — the existing eval behaviour is preserved.
    conn = _seeded(tmp_path)
    resp = assess(conn, "low_power", {
        "power_rails": [{"name": "VDD_CORE", "order": 1},
                        {"name": "VDD_IO", "order": 2, "depends_on": "VDD_CORE"}]},
        board_name="STM32H743")
    by = {v["check"]: v["status"] for v in resp.validations}
    assert by["POWER_SEQUENCE"] == "PASS"
