"""Golden tests for the Toolchain Engine: detection parsing, pure PASS/FAIL/UNKNOWN
validation, and the assess_project feasibility integration."""
from eaedk.store.db import connect
from eaedk.store.migrate import migrate
from eaedk.seed import seed_all
from eaedk import repo
from eaedk.orchestrator import assess_project
from eaedk.engines.toolchain.detect import detect_all
from eaedk.engines.toolchain.engine import validate_toolchain

# --- reusable reqs (a Cortex-A style HIGH compiler + MEDIUM tools) ---
REQ_CC = {"kind": "compiler", "name": "arm-none-eabi-gcc", "target_triple": "arm-none-eabi",
          "min_version": "9.0", "severity": "HIGH", "why": "needs arm cross-compiler"}
REQ_OOCD = {"kind": "flash_tool", "name": "openocd", "target_triple": None,
            "min_version": "0.11", "severity": "MEDIUM", "why": "flashes the target"}
REQ_MAKE = {"kind": "build_system", "name": "make", "target_triple": None,
            "min_version": None, "severity": "MEDIUM", "why": "builds the firmware"}
REQ_SDK = {"kind": "sdk", "name": "pioasm", "target_triple": None, "min_version": None,
           "severity": "LOW", "why": "assembles PIO"}


def _by_check(results):
    return {r.check: r for r in results}


# --- detection -------------------------------------------------------------

def test_detect_all_parses_version_and_triple():
    present = {"arm-none-eabi-gcc", "cmake", "openocd"}
    which = lambda b: f"/usr/bin/{b}" if b in present else None  # noqa: E731

    def runner(cmd):
        b = cmd[0]
        if cmd[1:] == ["-dumpmachine"]:
            return "arm-none-eabi" if b == "arm-none-eabi-gcc" else None
        return {"arm-none-eabi-gcc": "arm-none-eabi-gcc (GNU) 12.2.0",
                "cmake": "cmake version 3.25.1",
                "openocd": "Open On-Chip Debugger 0.12.0"}.get(b)

    comps = {(c.kind, c.name): c for c in detect_all(which=which, runner=runner)}
    cc = comps[("compiler", "arm-none-eabi-gcc")]
    assert cc.version == "12.2.0" and cc.target_triple == "arm-none-eabi"
    assert comps[("build_system", "cmake")].version == "3.25.1"
    assert ("flash_tool", "openocd") in comps and ("debugger", "openocd") in comps


# --- pure validation: PASS / FAIL / UNKNOWN --------------------------------

def test_pass_when_everything_matches():
    detected = [
        {"kind": "compiler", "name": "arm-none-eabi-gcc", "version": "12.2", "target_triple": "arm-none-eabi"},
        {"kind": "flash_tool", "name": "openocd", "version": "0.12", "target_triple": None},
        {"kind": "build_system", "name": "make", "version": "4.3", "target_triple": None},
    ]
    res = _by_check(validate_toolchain(detected, [REQ_CC, REQ_OOCD, REQ_MAKE], "arm", "bootloader", True))
    assert res["TOOLCHAIN_COMPILER"].status == "PASS"
    assert res["TOOLCHAIN_FLASH_TOOL"].status == "PASS"
    assert res["TOOLCHAIN_BUILD_SYSTEM"].status == "PASS"


def test_wrong_target_triple_is_fail_and_gating():
    detected = [{"kind": "compiler", "name": "gcc", "version": "13.2", "target_triple": "x86_64-linux-gnu"}]
    res = _by_check(validate_toolchain(detected, [REQ_CC], "arm", "bootloader", True))
    r = res["TOOLCHAIN_TARGET_TRIPLE"]
    assert r.status == "FAIL" and r.gating and r.severity_on_fail == "HIGH"
    assert r.teach                                  # mentor explanation present


def test_missing_compiler_is_unknown_engaged_and_gating():
    detected = [{"kind": "flash_tool", "name": "openocd", "version": "0.12", "target_triple": None}]
    res = _by_check(validate_toolchain(detected, [REQ_CC], "arm", "bootloader", True))
    r = res["TOOLCHAIN_COMPILER"]
    assert r.status == "UNKNOWN" and r.engaged and r.gating
    assert "not found" in r.reason and r.teach


def test_debugger_below_min_is_fail_but_not_gating():
    detected = [{"kind": "flash_tool", "name": "openocd", "version": "0.9", "target_triple": None}]
    res = _by_check(validate_toolchain(detected, [REQ_OOCD], "arm", "bootloader", True))
    r = res["TOOLCHAIN_FLASH_TOOL"]
    assert r.status == "FAIL" and r.gating is False and r.severity_on_fail == "MEDIUM"


def test_build_system_missing_is_unknown_non_gating():
    res = _by_check(validate_toolchain([], [REQ_MAKE], "arm", "bootloader", True))
    r = res["TOOLCHAIN_BUILD_SYSTEM"]
    assert r.status == "UNKNOWN" and r.gating is False


def test_sdk_not_detected_is_low_non_gating():
    res = _by_check(validate_toolchain([], [REQ_SDK], "arm", "bootloader", True))
    r = res["TOOLCHAIN_SDK"]
    assert r.status == "UNKNOWN" and r.gating is False and r.severity_on_fail == "LOW"


def test_no_detection_is_non_engaged_for_all():
    res = validate_toolchain([], [REQ_CC, REQ_OOCD], "arm", "bootloader", detection_ran=False)
    assert all(r.status == "UNKNOWN" and not r.engaged and not r.gating for r in res)


# --- integration with assess_project feasibility ---------------------------

def _project(tmp_path, board="Nucleo-F411RE", goal="bootloader"):
    conn = connect(str(tmp_path / "t.db"))
    migrate(conn)
    seed_all(conn, force=True)
    repo.create_project(conn, "p", goal, board)
    return conn, repo.get_project(conn, "p")


def test_assess_project_blocks_on_wrong_triple(tmp_path):
    conn, p = _project(tmp_path)
    repo.replace_toolchain(conn, [
        {"kind": "compiler", "name": "gcc", "version": "13", "target_triple": "x86_64-linux-gnu"}])
    resp = assess_project(conn, p)
    assert resp.feasibility == "not_feasible"
    assert any(v["check"] == "TOOLCHAIN_TARGET_TRIPLE" and v["status"] == "FAIL"
               for v in resp.validations)


def test_assess_project_blocked_on_missing_compiler(tmp_path):
    conn, p = _project(tmp_path)
    repo.replace_toolchain(conn, [
        {"kind": "flash_tool", "name": "openocd", "version": "0.12", "target_triple": None}])
    resp = assess_project(conn, p)
    assert resp.feasibility == "blocked"           # missing compiler == BLOCKED, like DDR


def test_assess_project_medium_issue_does_not_block(tmp_path):
    conn, p = _project(tmp_path)
    # matching compiler but old openocd (MEDIUM) -> surfaced but feasibility stays feasible
    repo.replace_toolchain(conn, [
        {"kind": "compiler", "name": "arm-none-eabi-gcc", "version": "12", "target_triple": "arm-none-eabi"},
        {"kind": "flash_tool", "name": "openocd", "version": "0.9", "target_triple": None},
        {"kind": "build_system", "name": "cmake", "version": "3.25", "target_triple": None}])
    resp = assess_project(conn, p)
    assert resp.feasibility == "feasible"
    ft = next(v for v in resp.validations if v["check"] == "TOOLCHAIN_FLASH_TOOL")
    assert ft["status"] == "FAIL" and ft["gating"] is False


def test_user_board_inherits_arch_default_toolchain_profile(tmp_path):
    # A board with NO seeded toolchain profile must still get checks (so teach fires).
    from eaedk.engines.toolchain.engine import arch_default_reqs, toolchain_checks
    assert any(r["name"] == "arm-none-eabi-gcc" for r in arch_default_reqs("arm-cortex-m4"))
    assert any(r["name"] == "aarch64-linux-gnu-gcc" for r in arch_default_reqs("arm-cortex-a53"))
    assert any(r["name"] == "arm-linux-gnueabihf-gcc" for r in arch_default_reqs("arm-cortex-a7"))
    assert any(r["name"] == "xtensa-esp32-elf-gcc" for r in arch_default_reqs("xtensa-lx6"))

    conn = connect(str(tmp_path / "t.db"))
    migrate(conn)
    seed_all(conn, force=True)
    # onboard a board with no toolchain profile (no board_toolchain_reqs rows)
    sid = repo.get_or_create_soc(conn, "MyM4", "Acme", "arm-cortex-m4")
    with conn:
        src = repo.create_manual_source(conn, "manual")
        repo.create_board(conn, soc_id=sid, name="MyBoard", flash_base=0x08000000,
                          flash_bytes=262144, ram_base=0x20000000, ram_bytes=65536,
                          source_id=src, confidence="HIGH")
        repo.replace_toolchain(conn, [
            {"kind": "compiler", "name": "gcc", "version": "13", "target_triple": "x86_64-linux-gnu"}])
    checks = toolchain_checks(conn, "MyBoard", {"arch": "arm-cortex-m4"}, "bare_metal_app")
    # inherited the arm-none-eabi requirement -> wrong host triple is flagged with teach
    assert any(c.check == "TOOLCHAIN_TARGET_TRIPLE" and c.status == "FAIL" and c.teach
               for c in checks)


def test_assess_project_clean_when_toolchain_matches(tmp_path):
    conn, p = _project(tmp_path)
    repo.replace_toolchain(conn, [
        {"kind": "compiler", "name": "arm-none-eabi-gcc", "version": "12", "target_triple": "arm-none-eabi"},
        {"kind": "flash_tool", "name": "openocd", "version": "0.12", "target_triple": None},
        {"kind": "build_system", "name": "cmake", "version": "3.25", "target_triple": None}])
    resp = assess_project(conn, p)
    assert resp.feasibility == "feasible"
    assert any(v["check"] == "TOOLCHAIN_COMPILER" and v["status"] == "PASS"
               for v in resp.validations)
