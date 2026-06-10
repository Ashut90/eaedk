"""Golden tests for the Engineering Output Engine: generator content, feasibility gating,
honest UNKNOWN placeholders, and real file export."""
from eaedk.store.db import connect
from eaedk.store.migrate import migrate
from eaedk.seed import seed_all
from eaedk import repo
from eaedk.engines.output import export_project, gather
from eaedk.engines.output import generators as gen


def _project(tmp_path, board="Nucleo-F411RE", goal="bootloader"):
    conn = connect(str(tmp_path / "t.db"))
    migrate(conn)
    seed_all(conn, force=True)
    repo.create_project(conn, "p", goal, board)
    return conn, repo.get_project(conn, "p")


def test_cpu_flag_and_mcu_detection():
    assert gen.cpu_flag("arm-cortex-m4") == "cortex-m4"
    assert gen.cpu_flag("arm-cortex-m0plus") == "cortex-m0plus"
    assert gen.is_mcu("arm-cortex-m7") is True
    assert gen.is_mcu("arm-cortex-a53") is False
    assert gen.cpu_flag("arm-cortex-a53") is None


def test_cmake_and_linker_use_verified_board_data(tmp_path):
    conn, p = _project(tmp_path)              # STM32F411RE = Cortex-M4, flash 0x08000000/512K
    data = gather(conn, p)
    tc = gen.render_toolchain_cmake(data)
    assert "arm-none-eabi-gcc" in tc and "-mcpu=cortex-m4" in tc
    ld = gen.render_linker(data)
    assert "ORIGIN = 0x08000000" in ld and "LENGTH = 524288" in ld
    # all geometry known for this board -> no placeholder tokens in the MEMORY block
    for tok in ("<UNKNOWN flash_base>", "<UNKNOWN flash_bytes>",
                "<UNKNOWN ram_base>", "<UNKNOWN ram_bytes>"):
        assert tok not in ld


def test_linker_marks_unknown_when_geometry_missing(tmp_path):
    # RTL8722DM has NULL flash/ram in the seed (honest-partial board)
    conn, p = _project(tmp_path, board="RTL8722DM")
    data = gather(conn, p)
    ld = gen.render_linker(data)
    assert "<UNKNOWN flash_base>" in ld and "<UNKNOWN flash_bytes>" in ld


def test_flash_instructions_are_board_aware(tmp_path):
    conn, p = _project(tmp_path)              # board profile flash_tool = openocd
    data = gather(conn, p)
    fl = gen.render_flash(data)
    assert "openocd" in fl and "0x08000000" in fl
    assert "program build/p.elf" in fl


def test_export_refused_when_not_feasible(tmp_path):
    conn, p = _project(tmp_path)
    # wrong-triple toolchain -> TOOLCHAIN_TARGET_TRIPLE FAIL -> not_feasible
    repo.replace_toolchain(conn, [
        {"kind": "compiler", "name": "gcc", "version": "13", "target_triple": "x86_64-linux-gnu"}])
    res = export_project(conn, p, str(tmp_path / "out"))
    assert res.refused and res.feasibility == "not_feasible"
    assert any("TOOLCHAIN_TARGET_TRIPLE" in b for b in res.blockers)
    assert not (tmp_path / "out").exists()    # nothing written


def test_export_writes_files_when_feasible(tmp_path):
    conn, p = _project(tmp_path)              # fresh bootloader project is feasible
    out = tmp_path / "out"
    res = export_project(conn, p, str(out))
    assert not res.refused and res.feasibility == "feasible"
    for rel in ("BRINGUP_CHECKLIST.md", "FLASH.md", "CMakeLists.txt",
                "cmake/toolchain.cmake", "linker/memory.ld", "src/main.c"):
        assert (out / rel).exists(), rel
    assert "Bring-up Checklist" in (out / "BRINGUP_CHECKLIST.md").read_text()


def test_force_export_marks_draft(tmp_path):
    conn, p = _project(tmp_path)
    repo.replace_toolchain(conn, [
        {"kind": "compiler", "name": "gcc", "version": "13", "target_triple": "x86_64-linux-gnu"}])
    out = tmp_path / "out"
    res = export_project(conn, p, str(out), force=True)
    assert not res.refused and res.written
    assert "DRAFT" in (out / "BRINGUP_CHECKLIST.md").read_text()


def test_only_filter_limits_files(tmp_path):
    conn, p = _project(tmp_path)
    out = tmp_path / "out"
    export_project(conn, p, str(out), only="flash")
    assert (out / "FLASH.md").exists()
    assert not (out / "CMakeLists.txt").exists()
    assert not (out / "BRINGUP_CHECKLIST.md").exists()
