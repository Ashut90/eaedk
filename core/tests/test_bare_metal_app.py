"""The bare_metal_app template — the beginner's first project (finding #4)."""
from eaedk.store.db import connect
from eaedk.store.migrate import migrate
from eaedk.seed import seed_all
from eaedk import repo
from eaedk.orchestrator import assess_project


def _seeded(tmp_path):
    conn = connect(str(tmp_path / "t.db"))
    migrate(conn)
    seed_all(conn, force=True)
    return conn


def test_bare_metal_app_template_seeded_and_selectable(tmp_path):
    conn = _seeded(tmp_path)
    tpl = repo.find_template(conn, "bare_metal_app")
    assert tpl is not None and tpl["name"] == "Bare-Metal Application"
    items = {r["item_key"] for r in repo.template_items(conn, tpl["id"])}
    assert {"vector_table_placement", "flash_fits", "ram_budget", "console_uart"} <= items


def test_bare_metal_app_engages_the_right_rules(tmp_path):
    conn = _seeded(tmp_path)
    repo.create_project(conn, "blink", "bare_metal_app", "Nucleo-F411RE")
    p = repo.get_project(conn, "blink")
    # provide the beginner-relevant inputs
    repo.set_input(conn, p["id"], "vector_table_addr", "0x08000000", confidence="HIGH")
    repo.set_input(conn, p["id"], "console_uart", "USART2", confidence="HIGH")
    resp = assess_project(conn, repo.get_project(conn, "blink"))
    checks = {v["check"]: v["status"] for v in resp.validations}
    assert checks.get("VECTOR_TABLE_PLACEMENT") == "PASS"   # 0x08000000 valid for F411 (M4, 512-align)
    assert checks.get("CONSOLE_UART_DEFINED") == "PASS"
