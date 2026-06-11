"""v1.4.0 mentor-UX: seeded-board nudge, per-rule validation teach, no_geometry feasibility,
and the export geometry banner."""
from eaedk.store.db import connect
from eaedk.store.migrate import migrate
from eaedk.seed import seed_all
from eaedk import repo
from eaedk.orchestrator import assess_project
from eaedk.onboard import run_wizard
from eaedk.engines.output import export_project
from eaedk.engines.validation.rules import run_validations, RULE_TEACH
from eaedk.context import build_context

_NONE_BOARD = {"flash_base": None, "flash_bytes": None, "ram_base": None, "ram_bytes": None}


def _seeded(tmp_path):
    conn = connect(str(tmp_path / "t.db"))
    migrate(conn)
    seed_all(conn, force=True)
    return conn


def _onboard_geometryless(conn, name):
    # name, vendor, soc(blank), arch=3(M4), flash size(unparseable), flash base, ram, ram base,
    # confidence, 8 partition blanks, facts=n  -> a board with no geometry
    answers = [name, "ST", "", "3", "I dont know", "", "", "", "",
               "", "", "", "", "", "", "", "",
               "",  # capabilities -> common MCU set
               "n"]
    it = iter(answers)
    out: list[str] = []
    run_wizard(conn, lambda p: next(it), out.append)
    return "\n".join(out)


def test_seeded_board_nudge_fires(tmp_path):
    conn = _seeded(tmp_path)
    blob = _onboard_geometryless(conn, "my f411")          # ~ matches seeded Nucleo-F411RE
    assert "already in the database" in blob
    assert "Nucleo-F411RE" in blob and "ingest" in blob


def test_validation_rules_carry_teach(tmp_path):
    ctx = build_context({}, dict(_NONE_BOARD), {"arch": "arm-cortex-m4"}, "bare_metal_app")
    res = {r.check: r for r in run_validations(ctx, "bare_metal_app")}
    assert res["FLASH_CAPACITY"].status == "UNKNOWN"
    assert "datasheet" in res["FLASH_CAPACITY"].teach and "bytes" in res["FLASH_CAPACITY"].teach
    assert res["VECTOR_TABLE_PLACEMENT"].teach                # every non-PASS rule teaches
    assert "FLASH_CAPACITY" in RULE_TEACH and "DDR_TIMING_VERIFIED" in RULE_TEACH


def test_no_geometry_feasibility_not_misleading(tmp_path):
    conn = _seeded(tmp_path)
    _onboard_geometryless(conn, "blankboard")
    repo.create_project(conn, "p", "bare_metal_app", "blankboard")
    resp = assess_project(conn, repo.get_project(conn, "p"))
    assert resp.feasibility == "no_geometry"                 # not "feasible"
    assert "geometry" in resp.next_step.lower()
    # a board WITH geometry is not falsely flagged
    repo.create_project(conn, "q", "bare_metal_app", "Nucleo-F411RE")
    assert assess_project(conn, repo.get_project(conn, "q")).feasibility == "feasible"


def test_export_refused_or_warned_on_no_geometry(tmp_path):
    conn = _seeded(tmp_path)
    _onboard_geometryless(conn, "blankboard")
    repo.create_project(conn, "p", "bare_metal_app", "blankboard")
    p = repo.get_project(conn, "p")

    res = export_project(conn, p, str(tmp_path / "out"))
    assert res.refused and res.feasibility == "no_geometry"
    assert any("no flash/RAM geometry" in b for b in res.blockers)
    assert not (tmp_path / "out").exists()                   # nothing written

    forced = export_project(conn, p, str(tmp_path / "out2"), force=True)
    assert forced.written and forced.geometry_unknown        # emits files but flags they won't build
