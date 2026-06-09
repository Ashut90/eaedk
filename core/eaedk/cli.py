"""EAEDK command-line interface (spec §4.1).

Implemented on argparse (stdlib) to keep the offline-first tool dependency-light; the
command surface matches the spec. The LLM is off by default (`--no-llm`); `ask`/`explain`
return the deterministic assembly and only consult the (deferred) LLM gateway with `--llm`.
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from . import repo
from .store.db import connect
from .store.migrate import migrate
from .seed import seed_all
from .orchestrator import assess_project
from .eval_runner import run_eval


# --- helpers ---------------------------------------------------------------

def _conn(args):
    return connect(getattr(args, "db", None))


def _emit(args, payload_dict: dict[str, Any], text: str) -> None:
    if getattr(args, "json", False):
        print(json.dumps(payload_dict, indent=2, default=str))
    else:
        print(text)


def _coerce_value(raw: str) -> Any:
    s = raw.strip()
    if s and s[0] in "[{":
        try:
            return json.loads(s)
        except json.JSONDecodeError:
            pass
    return raw


def _require_project(conn, name: str):
    p = repo.get_project(conn, name)
    if p is None:
        print(f"error: no project named {name!r}", file=sys.stderr)
        sys.exit(2)
    return p


# --- db --------------------------------------------------------------------

def cmd_db_init(args):
    conn = _conn(args)
    applied = migrate(conn)
    print(f"migrations applied: {applied or 'none (up to date)'}")


def cmd_db_seed(args):
    conn = _conn(args)
    migrate(conn)
    try:
        counts = seed_all(conn, force=args.force)
    except RuntimeError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(2)
    print("seeded: " + ", ".join(f"{k}={v}" for k, v in counts.items()))


# --- boards ----------------------------------------------------------------

def cmd_board_list(args):
    conn = _conn(args)
    rows = repo.list_boards(conn, args.query)
    if args.json:
        print(json.dumps([dict(r) for r in rows], indent=2))
        return
    for r in rows:
        print(f"{r['name']:26s} {r['soc']:14s} {r['arch']}")


def cmd_board_show(args):
    conn = _conn(args)
    board, soc = repo.load_board(conn, args.name)
    if board is None:
        print(f"error: unknown board {args.name!r}", file=sys.stderr)
        sys.exit(2)
    caps = [r["capability"] for r in conn.execute(
        "SELECT capability FROM board_capabilities bc JOIN boards b ON b.id=bc.board_id "
        "WHERE b.name=?", (args.name,)).fetchall()]
    payload = {"board": board, "soc": soc, "capabilities": caps}
    if args.json:
        print(json.dumps(payload, indent=2, default=str))
        return
    print(f"{board['name']}  ({soc['name']}, {soc['arch']})")
    for k in ("flash_base", "flash_bytes", "ram_base", "ram_bytes", "ddr_type",
              "ddr_bytes", "primary_storage", "confidence"):
        v = board[k]
        if isinstance(v, int) and k.endswith(("_base",)):
            v = f"0x{v:08X}"
        print(f"  {k:16s} {v}")
    print(f"  capabilities     {', '.join(caps)}")


def cmd_board_add(args):
    conn = _conn(args)
    if args.interactive:
        from .onboard import run_wizard
        name = run_wizard(conn, input, print)
        sys.exit(0 if name else 1)
    if not (args.name and args.soc and args.arch):
        print("error: board add requires NAME --soc --arch (or use --interactive)",
              file=sys.stderr)
        sys.exit(2)
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    with conn:
        src = conn.execute(
            "INSERT INTO sources(type,title,uri,hash,created_at) VALUES ('user',?,?,NULL,?)",
            (f"manual board entry: {args.name}", args.uri, now)).lastrowid
        soc_id = conn.execute(
            "INSERT INTO socs(name,vendor,arch,notes) VALUES (?,?,?,?)",
            (args.soc, args.vendor, args.arch, None)).lastrowid
        conn.execute(
            "INSERT INTO boards(soc_id,name,flash_base,flash_bytes,ram_base,ram_bytes,"
            "ddr_type,ddr_bytes,primary_storage,boot_modes_json,source_id,confidence) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (soc_id, args.name, _maybe_int(args.flash_base), _maybe_int(args.flash_bytes),
             _maybe_int(args.ram_base), _maybe_int(args.ram_bytes), args.ddr_type,
             _maybe_int(args.ddr_bytes), args.primary_storage, "[]", src, "MEDIUM"))
    print(f"added board {args.name!r} (confidence MEDIUM — manual entry)")


def _maybe_int(s):
    if s is None:
        return None
    return int(s, 0)


# --- projects --------------------------------------------------------------

def cmd_project_new(args):
    conn = _conn(args)
    try:
        pid = repo.create_project(conn, args.name, args.goal, args.board)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(2)
    print(f"created project {args.name!r} (id={pid}, goal={args.goal}, board={args.board})")


def cmd_project_list(args):
    conn = _conn(args)
    rows = conn.execute(
        "SELECT p.name,p.goal_type,p.status,b.name AS board FROM projects p "
        "LEFT JOIN boards b ON b.id=p.board_id ORDER BY p.updated_at DESC").fetchall()
    if args.json:
        print(json.dumps([dict(r) for r in rows], indent=2))
        return
    for r in rows:
        print(f"{r['name']:24s} {r['goal_type']:10s} {r['status']:9s} {r['board'] or '-'}")


def cmd_project_show(args):
    conn = _conn(args)
    p = _require_project(conn, args.name)
    resp = assess_project(conn, p)
    inputs, _ = repo.load_inputs(conn, p["id"])
    decisions = [dict(d) for d in repo.list_decisions(conn, p["id"])]
    if args.json:
        out = resp.to_dict()
        out["inputs"] = inputs
        out["decisions"] = decisions
        print(json.dumps(out, indent=2, default=str))
        return
    print(resp.to_markdown())
    if decisions:
        print("\n## Decisions")
        for d in decisions:
            print(f"- {d['title']}: {d['rationale'] or ''}")


def cmd_project_archive(args):
    conn = _conn(args)
    _require_project(conn, args.name)
    with conn:
        conn.execute("UPDATE projects SET status='archived' WHERE name=?", (args.name,))
    print(f"archived {args.name!r}")


# --- inputs ----------------------------------------------------------------

def cmd_input_set(args):
    conn = _conn(args)
    p = _require_project(conn, args.name)
    value = _coerce_value(args.value)
    stored = json.dumps(value) if isinstance(value, (list, dict)) else str(value)
    repo.set_input(conn, p["id"], args.key, stored, confidence=args.confidence,
                   section=args.cite)
    print(f"set {args.key} = {args.value}  [{args.confidence}]")


def cmd_input_list(args):
    conn = _conn(args)
    p = _require_project(conn, args.name)
    inputs, conf = repo.load_inputs(conn, p["id"])
    if args.json:
        print(json.dumps({k: {"value": v, "confidence": conf[k]} for k, v in inputs.items()},
                         indent=2, default=str))
        return
    for k, v in inputs.items():
        print(f"{k:24s} {v}  [{conf[k]}]")


# --- checklist -------------------------------------------------------------

def cmd_checklist_show(args):
    conn = _conn(args)
    p = _require_project(conn, args.name)
    resp = assess_project(conn, p)
    status_by_check = {v["check"]: v["status"] for v in resp.validations}
    rows = repo.checklist(conn, p["id"])
    if args.json:
        print(json.dumps([dict(r) for r in rows], indent=2))
        return
    for r in rows:
        rules = json.loads(r["validation_rule_keys_json"])
        rstat = ",".join(f"{rk}={status_by_check.get(rk,'-')}" for rk in rules) or "manual"
        print(f"[{r['status']:7s}] {r['item_key']:24s} {rstat}")


def cmd_checklist_set(args):
    conn = _conn(args)
    p = _require_project(conn, args.name)
    if args.status == "done":
        resp = assess_project(conn, p)
        status_by_check = {v["check"]: (v["status"], v["engaged"]) for v in resp.validations}
        row = next((r for r in repo.checklist(conn, p["id"]) if r["item_key"] == args.item), None)
        if row is None:
            print(f"error: no checklist item {args.item!r}", file=sys.stderr)
            sys.exit(2)
        for rk in json.loads(row["validation_rule_keys_json"]):
            st, engaged = status_by_check.get(rk, ("UNKNOWN", True))
            if st == "FAIL" or (st == "UNKNOWN" and engaged):
                print(f"refused: cannot mark {args.item!r} done — {rk} is {st}", file=sys.stderr)
                sys.exit(3)
    repo.set_checklist_status(conn, p["id"], args.item, args.status, args.note)
    print(f"{args.item} -> {args.status}")


# --- validate / risk -------------------------------------------------------

def cmd_validate(args):
    conn = _conn(args)
    p = _require_project(conn, args.name)
    only = [args.rule] if args.rule else None
    resp = assess_project(conn, p, only=only)
    _emit(args, resp.to_dict(), resp.to_markdown())


def cmd_risk(args):
    conn = _conn(args)
    p = _require_project(conn, args.name)
    resp = assess_project(conn, p)
    if args.json:
        print(json.dumps({"feasibility": resp.feasibility, "risks": resp.risks}, indent=2))
        return
    print(f"Feasibility: {resp.feasibility}")
    if not resp.risks:
        print("No risks identified.")
    for r in resp.risks:
        m = f"\n      mitigate: {r['mitigation']}" if r.get("mitigation") else ""
        print(f"[{r['severity']}] {r['rule_key']}: {r['explanation']}{m}")


# --- decision --------------------------------------------------------------

def cmd_decision_add(args):
    conn = _conn(args)
    p = _require_project(conn, args.name)
    alts = json.loads(args.alt) if args.alt else None
    repo.add_decision(conn, p["id"], args.title, args.rationale, alts)
    print(f"recorded decision: {args.title}")


# --- ask / explain (LLM-facing; off by default) ----------------------------

def _llm_section(conn, project, resp, kind: str, **kw) -> None:
    """Shared LLM path for ask/explain. Deterministic output is always printed first."""
    import urllib.error
    from .llm.gateway import Gateway
    gw = Gateway()
    if not gw.available():
        print("\n[LLM] gateway unavailable — Ollama not reachable or model "
              f"'{gw.model}' not pulled. Run `ollama pull {gw.model}`. "
              "Deterministic assessment above is unaffected.")
        return
    try:
        out = gw.ask(conn, project, resp, kw.get("question")) if kind == "ask" \
            else gw.explain(conn, project, resp, kw["rule"])
    except (urllib.error.URLError, OSError) as e:
        print(f"\n[LLM] generation failed ({e}); deterministic assessment above is unaffected.")
        return
    print(f"\n## LLM Explanation  ({out.model}, post-filtered)")
    print(out.text or "(model returned nothing)")
    print(f"\n[LLM] {out.removed} uncited hardware claim(s) removed by post-filter.")


def cmd_ask(args):
    conn = _conn(args)
    p = _require_project(conn, args.name)
    resp = assess_project(conn, p)
    print(resp.to_markdown())
    if args.llm:
        _llm_section(conn, p, resp, "ask", question=args.question)
    else:
        print("\n[note] LLM disabled (--no-llm default). Deterministic assessment only.")


def cmd_explain(args):
    conn = _conn(args)
    p = _require_project(conn, args.name)
    resp = assess_project(conn, p, only=[args.rule] if args.rule else None)
    target = next((v for v in resp.validations if v["check"] == args.rule), None)
    if target is None:
        print(f"error: rule {args.rule!r} not applicable to this project", file=sys.stderr)
        sys.exit(2)
    print(f"{target['check']}: {target['status']}\n  {target['reason']}")
    if args.llm:
        # explain wants the full validation set in context, not the single-rule view.
        full = assess_project(conn, p)
        _llm_section(conn, p, full, "explain", rule=args.rule)


# --- eval ------------------------------------------------------------------

def cmd_eval_run(args):
    conn = _conn(args)
    res = run_eval(conn, args.case)
    if args.json:
        print(json.dumps(res, indent=2))
    else:
        print(f"PASSED {res['passed']}/{res['total']}")
        for c in res["cases"]:
            print(f"  [{'ok' if c['passed'] else 'FAIL'}] {c['name']}")
            for d in c["diffs"]:
                print(f"       - {d}")
    sys.exit(0 if res["failed"] == 0 else 1)


# --- parser ----------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="eaedk",
                                description="Embedded AI Engineering Development Kit")
    p.add_argument("--db", help="database path (default ~/.eaedk/eaedk.db)")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    llm = p.add_mutually_exclusive_group()
    llm.add_argument("--llm", dest="llm", action="store_true", help="enable LLM (deferred)")
    llm.add_argument("--no-llm", dest="llm", action="store_false")
    p.set_defaults(llm=False)
    sub = p.add_subparsers(dest="cmd", required=True)

    db = sub.add_parser("db").add_subparsers(dest="sub", required=True)
    db.add_parser("init").set_defaults(func=cmd_db_init)
    s = db.add_parser("seed"); s.add_argument("--force", action="store_true")
    s.set_defaults(func=cmd_db_seed)

    bd = sub.add_parser("board").add_subparsers(dest="sub", required=True)
    bl = bd.add_parser("list"); bl.add_argument("--query"); bl.set_defaults(func=cmd_board_list)
    bs = bd.add_parser("show"); bs.add_argument("name"); bs.set_defaults(func=cmd_board_show)
    ba = bd.add_parser("add")
    ba.add_argument("name", nargs="?"); ba.add_argument("--interactive", action="store_true")
    ba.add_argument("--soc"); ba.add_argument("--arch")
    ba.add_argument("--vendor"); ba.add_argument("--flash-base", dest="flash_base")
    ba.add_argument("--flash-bytes", dest="flash_bytes"); ba.add_argument("--ram-base", dest="ram_base")
    ba.add_argument("--ram-bytes", dest="ram_bytes"); ba.add_argument("--ddr-type", dest="ddr_type")
    ba.add_argument("--ddr-bytes", dest="ddr_bytes"); ba.add_argument("--primary-storage", dest="primary_storage")
    ba.add_argument("--uri"); ba.set_defaults(func=cmd_board_add)

    pr = sub.add_parser("project").add_subparsers(dest="sub", required=True)
    pn = pr.add_parser("new"); pn.add_argument("name"); pn.add_argument("--board")
    pn.add_argument("--goal", required=True); pn.set_defaults(func=cmd_project_new)
    pr.add_parser("list").set_defaults(func=cmd_project_list)
    ps = pr.add_parser("show"); ps.add_argument("name"); ps.set_defaults(func=cmd_project_show)
    pa = pr.add_parser("archive"); pa.add_argument("name"); pa.set_defaults(func=cmd_project_archive)

    inp = sub.add_parser("input").add_subparsers(dest="sub", required=True)
    iset = inp.add_parser("set")
    iset.add_argument("name"); iset.add_argument("key"); iset.add_argument("value")
    iset.add_argument("--confidence", default="MEDIUM", choices=["HIGH", "MEDIUM", "LOW"])
    iset.add_argument("--cite"); iset.set_defaults(func=cmd_input_set)
    il = inp.add_parser("list"); il.add_argument("name"); il.set_defaults(func=cmd_input_list)

    cl = sub.add_parser("checklist").add_subparsers(dest="sub", required=True)
    cs = cl.add_parser("show"); cs.add_argument("name"); cs.set_defaults(func=cmd_checklist_show)
    cset = cl.add_parser("set")
    cset.add_argument("name"); cset.add_argument("item")
    cset.add_argument("status", choices=["todo", "done", "na", "blocked"])
    cset.add_argument("--note"); cset.set_defaults(func=cmd_checklist_set)

    v = sub.add_parser("validate"); v.add_argument("name"); v.add_argument("--rule")
    v.set_defaults(func=cmd_validate)
    rk = sub.add_parser("risk"); rk.add_argument("name"); rk.set_defaults(func=cmd_risk)

    dc = sub.add_parser("decision").add_subparsers(dest="sub", required=True)
    da = dc.add_parser("add"); da.add_argument("name"); da.add_argument("--title", required=True)
    da.add_argument("--rationale"); da.add_argument("--alt"); da.set_defaults(func=cmd_decision_add)

    ak = sub.add_parser("ask"); ak.add_argument("name"); ak.add_argument("question", nargs="?")
    ak.set_defaults(func=cmd_ask)
    ex = sub.add_parser("explain"); ex.add_argument("name"); ex.add_argument("--rule", required=True)
    ex.set_defaults(func=cmd_explain)

    ev = sub.add_parser("eval").add_subparsers(dest="sub", required=True)
    er = ev.add_parser("run"); er.add_argument("--case"); er.set_defaults(func=cmd_eval_run)

    return p


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
