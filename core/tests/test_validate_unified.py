"""v3.1 Gap 5 — project structural validation + behavioural intent in one unified report."""
import json
from eaedk.store.db import connect
from eaedk.store.migrate import migrate
from eaedk.seed import seed_all
from eaedk import repo
from eaedk.orchestrator.orchestrator import assess_project, assess


def _seeded(tmp_path):
    conn = connect(str(tmp_path / "t.db"))
    migrate(conn)
    seed_all(conn, force=True)
    return conn


def _project(conn, name="p", board="STM32F103-BluePill"):
    repo.create_project(conn, name, "bare_metal_app", board)
    return repo.get_project(conn, name)


def test_gap5_project_plus_intent_unified_fail(tmp_path):
    conn = _seeded(tmp_path)
    p = _project(conn)
    repo.set_input(conn, p["id"], "estimated_image_size", "45000", confidence="HIGH")
    resp = assess_project(conn, p, intent="grpc")
    assert resp.intent is not None and resp.intent["verdict"] == "FAIL"
    assert resp.feasibility == "not_feasible"            # intent FAIL aggregates into the verdict
    # structural facts are STILL in the same report
    assert any(v["check"] == "FLASH_CAPACITY" for v in resp.validations)
    assert "intent" in resp.next_step.lower()


def test_gap5_intent_that_fits_does_not_block(tmp_path):
    conn = _seeded(tmp_path)
    p = _project(conn)
    resp = assess_project(conn, p, intent="freertos")
    assert resp.intent["verdict"] == "PASS"
    assert resp.feasibility != "not_feasible"            # a fitting intent doesn't fail the project


def test_gap5_no_intent_means_no_section(tmp_path):
    conn = _seeded(tmp_path)
    p = _project(conn)
    resp = assess_project(conn, p)
    assert resp.intent is None                           # unchanged behaviour when no intent given


def test_gap5_intent_can_come_from_stored_input(tmp_path):
    conn = _seeded(tmp_path)
    p = _project(conn)
    repo.set_input(conn, p["id"], "intent", "grpc", confidence="HIGH")
    resp = assess_project(conn, p)                       # no --intent flag; read from the project input
    assert resp.intent is not None and resp.intent["verdict"] == "FAIL"
    assert resp.feasibility == "not_feasible"


def test_gap5_unified_response_is_json_serializable(tmp_path):
    conn = _seeded(tmp_path)
    p = _project(conn)
    resp = assess_project(conn, p, intent="mqtt freertos")
    blob = json.dumps(resp.to_dict())                    # must not raise (P7)
    assert '"intent"' in blob
    md = resp.to_markdown()
    assert "## Intent Feasibility" in md


def test_gap5_stateless_assess_supports_intent(tmp_path):
    conn = _seeded(tmp_path)
    resp = assess(conn, "bare_metal_app", {}, board_name="STM32F103-BluePill", intent="grpc")
    assert resp.intent["verdict"] == "FAIL" and resp.feasibility == "not_feasible"


# --- v3.1 Gap 7: --json accepted after the subcommand, output serializable -----------------

def test_gap7_json_flag_accepted_after_validate_subcommand():
    from eaedk.cli import build_parser
    args = build_parser().parse_args(["validate", "--board", "bluepill", "--intent", "grpc", "--json"])
    assert args.json is True
    args2 = build_parser().parse_args(["--json", "validate", "prod"])   # global form still works
    assert args2.json is True
    args3 = build_parser().parse_args(["validate", "prod"])             # default stays False
    assert args3.json is False


def test_gap7_intent_result_is_json_serializable(tmp_path):
    conn = _seeded(tmp_path)
    resp = assess(conn, "bare_metal_app", {}, board_name="STM32F103-BluePill", intent="grpc tls")
    json.dumps(resp.to_dict())                              # must not raise; nested intent dict is clean
