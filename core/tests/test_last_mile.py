"""Golden tests for v1.7.0-last-mile — the six fixes that close the gap between "EAEDK gave me
files" and "I have running code on the board." Each fix is a beginner quit-point from the
v1.6.0 audit; each has at least one case here.
"""
import platform

from eaedk.store.db import connect
from eaedk.store.migrate import migrate
from eaedk.seed import seed_all
from eaedk import repo, project_init
from eaedk.engines.output import export, codegen, generators
from eaedk.engines.output.install import install_block, normalize_os
from eaedk.engines.logs.engine import crash_locate_hint
from eaedk.engines.logs.parser import SignatureMatch
from eaedk.schemas.response import AssessResponse
from eaedk.orchestrator import assess_project
from eaedk.cli import build_parser


def _seeded(tmp_path):
    conn = connect(str(tmp_path / "t.db"))
    migrate(conn)
    seed_all(conn, force=True)
    return conn


def _blue_pill_project(conn):
    repo.create_project(conn, "p", "bare_metal_app", "STM32F103-BluePill")
    return repo.get_project(conn, "p")


# --- Fix 1: host-OS-aware install commands --------------------------------

def test_install_block_linux_is_apt_oneliner():
    out = "\n".join(install_block("Linux", ["arm-none-eabi-gcc", "cmake", "openocd"]))
    assert "sudo apt install gcc-arm-none-eabi cmake openocd" in out


def test_install_block_macos_is_brew():
    out = "\n".join(install_block("Darwin", ["arm-none-eabi-gcc", "cmake", "openocd"]))
    assert "brew install arm-none-eabi-gcc cmake openocd" in out


def test_install_block_windows_links_not_packages():
    out = "\n".join(install_block("Windows", ["arm-none-eabi-gcc", "cmake"]))
    assert "developer.arm.com" in out and "apt" not in out and "brew" not in out


def test_install_block_avr_uses_correct_package():
    # Not Blue-Pill-only: an AVR board maps to the right package, never arm-*.
    out = "\n".join(install_block("Linux", ["avr-gcc", "cmake"]))
    assert "gcc-avr" in out and "gcc-arm-none-eabi" not in out


def test_normalize_os():
    assert normalize_os("Linux") == "linux"
    assert normalize_os("Darwin") == "macos"
    assert normalize_os("Windows") == "windows"
    assert normalize_os(None) == "other"


def test_start_here_emits_install_command(tmp_path):
    conn = _seeded(tmp_path)
    data = export.gather(conn, _blue_pill_project(conn))
    out = codegen.render_start_here(data)
    assert "Install the tools first" in out
    # The concrete command for the host OS — never just "a tool is missing".
    host = normalize_os(platform.system())
    if host == "linux":
        assert "sudo apt install" in out and "gcc-arm-none-eabi" in out
    elif host == "macos":
        assert "brew install" in out


# --- Fix 2: real flash command from the seeded probe map ------------------

def test_flash_profile_seeded_for_blue_pill(tmp_path):
    conn = _seeded(tmp_path)
    prof = repo.soc_flash_profile_for(conn, "STM32F103-BluePill")
    assert prof is not None
    assert prof["openocd_target"] == "target/stm32f1x.cfg"
    assert prof["default_probe"] == "st-link"
    assert prof["interface_cfg"] == "interface/stlink.cfg"


def test_flash_md_fills_in_blue_pill_stlink_command(tmp_path):
    conn = _seeded(tmp_path)
    data = export.gather(conn, _blue_pill_project(conn))
    out = generators.render_flash(data)
    # The real command — no placeholders for the most common setup on Earth.
    assert "openocd -f interface/stlink.cfg -f target/stm32f1x.cfg" in out
    assert "program build/p.elf verify reset exit" in out
    assert "<probe>" not in out and "<target>" not in out
    # Alternatives table is present so a different adapter isn't a dead end.
    assert "j-link" in out and "cmsis-dap" in out


def test_flash_md_keeps_placeholder_when_soc_unknown(tmp_path):
    conn = _seeded(tmp_path)
    # A board whose SoC has no flash profile must NOT get an invented target cfg.
    data = export.gather(conn, _blue_pill_project(conn))
    data["flash_profile"] = None              # simulate unknown SoC
    out = generators.render_flash(data)
    assert "<probe>" in out and "<target>" in out

    probes = repo.debug_probes(conn)
    assert {"st-link", "j-link", "cmsis-dap"} <= {p["name"] for p in probes}


# --- Fix 3: --llm after the subcommand ------------------------------------

def test_llm_flag_accepted_after_subcommand():
    p = build_parser()
    args = p.parse_args(["mentor", "--board", "X", "--ask", "hi", "--llm"])
    assert args.llm is True


def test_llm_flag_still_works_before_subcommand():
    p = build_parser()
    args = p.parse_args(["--llm", "mentor", "--board", "X", "--ask", "hi"])
    assert args.llm is True


def test_llm_defaults_false_when_absent():
    p = build_parser()
    args = p.parse_args(["mentor", "--board", "X", "--ask", "hi"])
    assert args.llm is False


def test_no_llm_after_subcommand_overrides():
    p = build_parser()
    args = p.parse_args(["mentor", "--board", "X", "--ask", "hi", "--no-llm"])
    assert args.llm is False


# --- Fix 4: validate leads with one clear status --------------------------

def _resp(feasibility, unknowns):
    return AssessResponse(goal_type="bare_metal_app", feasibility=feasibility,
                          unknowns=unknowns, next_step="x")


def test_feasible_with_unknowns_leads_with_reassurance():
    md = _resp("feasible", ["FLASH_CAPACITY: missing"]).to_markdown()
    assert "ready to export" in md
    assert "optional" in md
    # The contradiction is gone: never "Validation clean" while unknowns are shown.
    assert "Validation clean" not in md


def test_feasible_status_appears_before_the_unknown_list():
    md = _resp("feasible", ["FLASH_CAPACITY: missing"]).to_markdown()
    assert md.index("ready to export") < md.index("Missing Information")


def test_orchestrator_next_step_not_clean_with_unknowns(tmp_path):
    conn = _seeded(tmp_path)
    resp = assess_project(conn, _blue_pill_project(conn))
    assert resp.feasibility == "feasible" and resp.unknowns
    assert "Validation clean" not in resp.next_step
    assert "export" in resp.next_step.lower()


# --- Fix 5: goal prompt Enter = default 1 ---------------------------------

def test_goal_prompt_enter_picks_start_here():
    goals = ["bare_metal_app", "bootloader", "uboot", "ota", "driver"]
    goal, is_custom = project_init._select_goal(lambda _p: "", lambda _s: None, goals)
    assert goal == "bare_metal_app" and is_custom is False


def test_goal_prompt_still_accepts_explicit_number():
    goals = ["bare_metal_app", "bootloader", "uboot"]
    goal, _ = project_init._select_goal(lambda _p: "2", lambda _s: None, goals)
    assert goal == "bootloader"


# --- Fix 6: log analyze concrete next action ------------------------------

def _fault_match():
    return SignatureMatch(signature_id=1, format="mcu", line_no=4, line="HardFault!",
                          cause="Cortex-M HardFault", fix="Decode CFSR", severity="HIGH")


def test_crash_locate_hint_emits_addr2line():
    text = "HardFault!\n  R0=0x0 PC=0x08000abc LR=0xfffffff9\n"
    hint = crash_locate_hint(text, [_fault_match()], "myblink")
    assert "arm-none-eabi-addr2line -e build/myblink.elf 0x08000abc" in hint


def test_crash_locate_hint_none_without_fault():
    text = "PC=0x08000abc\n"
    # No fault signature matched -> no spurious next action.
    assert crash_locate_hint(text, [], "myblink") is None


def test_crash_locate_hint_none_without_address():
    assert crash_locate_hint("HardFault!\nsystem halted.\n", [_fault_match()], "myblink") is None


def test_crash_locate_hint_handles_missing_project():
    text = "HardFault!\nPC=0x08000abc\n"
    hint = crash_locate_hint(text, [_fault_match()], None)
    assert "build/<your-firmware>.elf" in hint
