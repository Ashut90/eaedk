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
    args.name = _resolve_board_or_exit(conn, args.name)    # v3.1 Gap 2: auto-coerce board name
    board, soc = repo.load_board(conn, args.name)
    if board is None:
        _unknown_board(conn, args.name)
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


def cmd_board_fill_geometry(args):
    """Fix 4: fill a board's missing flash/RAM from its SoC's standard geometry."""
    conn = _conn(args)
    d = repo.apply_soc_defaults(conn, args.name)
    if d is None:
        if repo.load_board(conn, args.name)[0] is None:
            _unknown_board(conn, args.name)
        else:
            print(f"no standard geometry on file for {args.name!r}'s SoC — onboard it with "
                  "`eaedk ingest` or `eaedk board add --interactive`.", file=sys.stderr)
        sys.exit(2)
    print(f"filled {args.name} from {d['soc_name']} standard geometry: "
          f"{d['flash_bytes'] // 1024}KB flash @ {hex(d['flash_base'])}, "
          f"{d['ram_bytes'] // 1024}KB RAM @ {hex(d['ram_base'])}.")


def cmd_board_capability_add(args):
    """Fix 6: add a capability to a board so its learning-path steps unlock."""
    conn = _conn(args)
    cap = args.capability.lower()
    with conn:
        ok = repo.add_board_capability(conn, args.name, cap)
    if not ok:
        _unknown_board(conn, args.name)
        sys.exit(2)
    print(f"added capability {cap!r} to {args.name}.")


def _run_wizard_safely(fn, *a):
    """Run an interactive wizard so a traceback never reaches a beginner."""
    try:
        return fn(*a)
    except (EOFError, KeyboardInterrupt):
        print("\nonboarding cancelled — nothing was saved. Run it again any time.",
              file=sys.stderr)
        return None
    except Exception as e:  # last-resort guard: a plain message, never a stack trace
        print(f"\nsomething went wrong in the wizard ({e}); nothing was saved. "
              "Please re-run, or use a seeded board (`eaedk board list`).", file=sys.stderr)
        return None


def cmd_board_add(args):
    conn = _conn(args)
    if args.interactive:
        from .onboard import run_wizard
        name = _run_wizard_safely(run_wizard, conn, input, print)
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


def _unknown_board(conn, name: str) -> None:
    """Never a dead end — always point at `board list`, plus a near-match when there is one."""
    matches = [m for m in repo.near_match_boards(conn, name) if m["flash_bytes"] is not None]
    print(f"Board not found: {name!r}.", file=sys.stderr)
    if matches:
        m = matches[0]
        print(f"  Did you mean '{m['name']}'? It's already set up — use that exact name.",
              file=sys.stderr)
    print("  Run `eaedk board list` to see all 14 available boards, or run "
          "`eaedk board add --interactive` to add your own.", file=sys.stderr)


def _resolve_board_or_exit(conn, name: str) -> str:
    """Resolve a (possibly fuzzy) board name to its canonical form, printing a coercion notice when
    it wasn't an exact match. Exits 2 if nothing close is found (v3.1 Gap 2)."""
    resolved, exact = repo.resolve_board_name(conn, name)
    if resolved is None:
        _unknown_board(conn, name)
        sys.exit(2)
    if not exact:
        print(f"Note: assuming '{resolved}' from input '{name}'.", file=sys.stderr)
    return resolved


def cmd_project_init(args):
    conn = _conn(args)
    from .project_init import run_project_init
    name = _run_wizard_safely(run_project_init, conn, input, print)
    sys.exit(0 if name else 1)


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
    tracked = repo.list_risks_by_status(conn, p["id"], "tracked")
    resolved = repo.list_risks_by_status(conn, p["id"], "resolved")
    if tracked:
        print("\n## Tracked Risks (open — surfaced from log triage)")
        for t in tracked:
            print(f"- #{t['id']} [{t['severity']}] {t['rule_key']}: "
                  f"{t['explanation'].splitlines()[0]}")
    if resolved:
        print("\n## Resolved Risks")
        for t in resolved:
            note = f" — {t['resolution_note']}" if t["resolution_note"] else ""
            print(f"- #{t['id']} [{t['severity']}] {t['rule_key']}  "
                  f"(resolved {t['resolved_at']}){note}")
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


def cmd_checklist_done(args):
    """The USER completion path for the State Engine — you confirm an item is done."""
    conn = _conn(args)
    p = _require_project(conn, args.name)
    row = next((r for r in repo.checklist(conn, p["id"]) if r["item_key"] == args.item), None)
    if row is None:
        print(f"Couldn't find a checklist item called {args.item!r} in project {args.name!r}.",
              file=sys.stderr)
        print(f"  See the item names with:  eaedk project status {args.name}", file=sys.stderr)
        sys.exit(2)
    repo.set_checklist_status(conn, p["id"], args.item, "done", "confirmed by engineer")
    print(f"Marked '{row['text']}' complete (confirmed by you).")
    print(f"See your progress:  eaedk project status {args.name}")


def cmd_project_status(args):
    conn = _conn(args)
    p = _require_project(conn, args.name)
    from .engines.state import project_status
    board = repo.project_board_name(conn, p)
    s = project_status(conn, p)
    if args.json:
        print(json.dumps(s, indent=2)); return
    print(f"Project: {s['project']}" + (f" ({board})" if board else ""))
    print(f"Progress: {s['complete']}/{s['total']} items complete ({s['percent']}%)")
    if s.get("note"):
        print(s["note"])
    print()
    mark = {"COMPLETE": "✓", "IN_PROGRESS": "•", "NOT_STARTED": "✗"}
    for it in s["items"]:
        line = f"{mark.get(it['status'], '?')} {it['title']}"
        if it["status"] == "COMPLETE":
            src = {"VALIDATION_ENGINE": "verified by the engine", "USER": "confirmed by you",
                   "LOG_TRIAGE": "resolved from a log"}.get(it["verified_by"], "complete")
            print(f"{line}  — {src}")
        else:
            label = "IN PROGRESS" if it["status"] == "IN_PROGRESS" else "NOT STARTED"
            print(f"{line}  — {label}")
            print(f"    Why it matters: {it['why_it_matters']}")
    if s["next"]:
        print(f"\nNext recommended task: {s['next']['title']}")
        print(f"Why: {s['next']['why_it_matters']}")
    else:
        print("\nAll items complete — nice work.")


# --- validate / risk -------------------------------------------------------

def cmd_validate(args):
    conn = _conn(args)
    intent = getattr(args, "intent", None)
    # v3.1 Gap 5: a project (+ optional --intent) runs structural validation AND behavioural intent
    # as ONE unified report and a single aggregated verdict.
    if args.name:
        p = _require_project(conn, args.name)
        only = [args.rule] if args.rule else None
        resp = assess_project(conn, p, only=only, intent=intent)
        _emit(args, resp.to_dict(), resp.to_markdown())
        sys.exit(1 if resp.feasibility == "not_feasible" else 0)
    # Board + intent only (no project): the quick stand-alone intent check.
    if intent:
        if not args.board:
            print("error: validate needs a project NAME, or --board NAME with --intent",
                  file=sys.stderr); sys.exit(2)
        resolved = _resolve_board_or_exit(conn, args.board)
        from .semantic_cost import classify_terms, assess as sc_assess, render
        known, unknown = classify_terms(intent)
        res = sc_assess(conn, resolved, known, unknown)
        if args.json:
            print(json.dumps(res, indent=2, default=str))
        else:
            print(render(res), end="")
        sys.exit(1 if res["verdict"] == "FAIL" else 0)
    print("error: validate needs a project NAME, or --board NAME --intent \"...\"",
          file=sys.stderr); sys.exit(2)


def cmd_risk_show(args):
    conn = _conn(args)
    p = _require_project(conn, args.name)
    resp = assess_project(conn, p)
    tracked = repo.list_risks_by_status(conn, p["id"], "tracked")
    resolved = repo.list_risks_by_status(conn, p["id"], "resolved")
    if args.json:
        print(json.dumps({"feasibility": resp.feasibility, "risk_engine": resp.risks,
                          "tracked": [dict(r) for r in tracked],
                          "resolved": [dict(r) for r in resolved]}, indent=2, default=str))
        return
    print(f"Feasibility: {resp.feasibility}")
    print("\nRisk-engine findings (live, ephemeral):")
    for r in resp.risks:
        m = f"  mitigate: {r['mitigation']}" if r.get("mitigation") else ""
        print(f"  [{r['severity']}] {r['rule_key']}: {r['explanation']}{m}")
    if not resp.risks:
        print("  (none)")
    print("\nTracked risks (surfaced from log triage):")
    for r in tracked:
        print(f"  #{r['id']} [{r['severity']}] {r['rule_key']}: {r['explanation'].splitlines()[0]}")
    if not tracked:
        print("  (none)")
    if resolved:
        print("\nResolved risks:")
        for r in resolved:
            note = f" — {r['resolution_note']}" if r["resolution_note"] else ""
            print(f"  #{r['id']} [{r['severity']}] {r['rule_key']}  resolved {r['resolved_at']}{note}")


def cmd_risk_resolve(args):
    conn = _conn(args)
    risk = repo.get_risk(conn, args.risk_id)
    if risk is None:
        print(f"error: no risk with id {args.risk_id}", file=sys.stderr)
        sys.exit(2)
    if risk["status"] == "resolved":
        print(f"risk #{args.risk_id} is already resolved ({risk['resolved_at']}).")
        return
    if risk["status"] != "tracked":
        print(f"error: risk #{args.risk_id} is a '{risk['status']}' risk-engine finding "
              "(ephemeral); only tracked risks can be resolved. Fix the underlying validation "
              "instead.", file=sys.stderr)
        sys.exit(2)

    # Warn (don't block) if the implicated checklist rule is still UNKNOWN/FAIL.
    project = repo.get_project_by_id(conn, risk["project_id"])
    if project is not None:
        resp = assess_project(conn, project)
        v = next((x for x in resp.validations if x["check"] == risk["rule_key"]), None)
        if v and (v["status"] == "FAIL" or (v["status"] == "UNKNOWN" and v["engaged"])):
            print(f"WARNING: {risk['rule_key']} is still {v['status']} ({v['reason']}). "
                  "You are closing this risk against an unverified item.", file=sys.stderr)

    outcome = repo.resolve_risk(conn, args.risk_id, args.note)
    if outcome == "resolved":
        print(f"resolved risk #{args.risk_id} ({risk['rule_key']})"
              + (f" — note: {args.note}" if args.note else ""))


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
    # v2.3.0: `eaedk ask --board <name> "question"` -> the Board Query Engine. The project form
    # (`eaedk ask <project>`) is unchanged.
    if args.board:
        question = args.question or args.name
        if not question:
            print("error: ask --board <name> needs a question, e.g. "
                  "eaedk ask --board X \"what is the default clock?\"", file=sys.stderr)
            sys.exit(2)
        if repo.load_board(conn, args.board)[0] is None:
            _unknown_board(conn, args.board); sys.exit(2)
        if getattr(args, "file", None):
            from .engines.ingest import ingest_datasheet
            try:
                ingest_datasheet(conn, args.file, args.board, use_llm=args.llm)
            except Exception as e:
                print(f"error reading datasheet: {e}", file=sys.stderr); sys.exit(2)
        from .engines.ingest.query import answer_query
        res = answer_query(conn, args.board, question, use_llm=args.llm)
        print(res["answer"])
        return
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

def cmd_bughunt(args):
    from .engines.bughunt import scan_directory
    result = scan_directory(args.path, max_files=args.max_files)
    if args.json:
        print(json.dumps(result.to_dict(), indent=2, default=str))
        return
    s = result.summary
    print(result.to_markdown())
    if s["total"] == 0:
        print("No issues found.")


def cmd_log_analyze(args):
    conn = _conn(args)
    from .engines.logs import analyze_log
    project_name = args.project
    if args.project_aware and not project_name:
        active = repo.active_project(conn)
        if active is None:
            print("error: --project-aware needs a project; none active. Use --project NAME.",
                  file=sys.stderr)
            sys.exit(2)
        project_name = active["name"]
    if args.project_aware and not args.llm:
        print("[note] --project-aware enriches LLM triage; add --llm to engage it.")
    res = analyze_log(conn, args.file, project_name, args.llm,
                      project_aware=args.project_aware, deep=args.deep)
    if args.json:
        print(json.dumps(res.to_dict(), indent=2, default=str))
    else:
        print(res.to_markdown())


def cmd_toolchain_detect(args):
    conn = _conn(args)
    from .engines.toolchain import detect_all
    components = [c.to_dict() for c in detect_all()]
    repo.replace_toolchain(conn, components)
    if args.json:
        print(json.dumps(components, indent=2))
        return
    if not components:
        print("No toolchain components detected on this host.")
        return
    print(f"Detected {len(components)} toolchain component(s):")
    for c in components:
        triple = f"  [{c['target_triple']}]" if c.get("target_triple") else ""
        ver = c.get("version") or "present"
        print(f"  {c['kind']:13s} {c['name']:24s} {ver}{triple}")


def cmd_toolchain_validate(args):
    conn = _conn(args)
    p = _require_project(conn, args.name)
    board_name = repo.project_board_name(conn, p)
    from .engines.toolchain import toolchain_checks
    board, soc = repo.load_board(conn, board_name) if board_name else (None, None)
    checks = toolchain_checks(conn, board_name, soc, p["goal_type"])
    if args.json:
        print(json.dumps([{"check": c.check, "status": c.status, "reason": c.reason,
                           "severity": c.severity_on_fail, "gating": c.gating,
                           "teach": c.teach} for c in checks], indent=2))
        return
    if not board_name:
        print("project has no board; nothing to validate.")
        return
    if not checks:
        print(f"No toolchain profile defined for board {board_name!r}.")
        return
    detected = repo.load_toolchain(conn)
    if not detected:
        print("[note] no toolchain detected yet — run `eaedk toolchain detect` first.\n")
    print(f"Toolchain validation for {p['name']} (board {board_name}):")
    for c in checks:
        gate = "" if c.gating else "  (non-gating)"
        print(f"  {c.status:7s} [{c.severity_on_fail}] {c.check}: {c.reason}{gate}")
        if c.teach and c.status != "PASS":
            print(f"          ↳ {c.teach}")


def cmd_ingest(args):
    conn = _conn(args)
    from .engines.ingest import (ingest_datasheet, confirm_candidate, reject_candidate)
    if args.confirm is not None:
        outcome = confirm_candidate(conn, args.confirm, confidence=args.confidence)
        if outcome == "not_found":
            print(f"error: no candidate #{args.confirm}", file=sys.stderr); sys.exit(2)
        if outcome == "already":
            print(f"candidate #{args.confirm} is not pending."); return
        print(f"confirmed candidate #{args.confirm} → committed to the knowledge base (DATASHEET).")
        return
    if args.reject is not None:
        outcome = reject_candidate(conn, args.reject)
        if outcome == "not_found":
            print(f"error: no candidate #{args.reject}", file=sys.stderr); sys.exit(2)
        print(f"rejected candidate #{args.reject}."); return
    if args.review:
        if not args.board:
            print("error: --review needs --board", file=sys.stderr); sys.exit(2)
        rows = repo.list_fact_candidates(conn, args.board, status="pending")
        if args.json:
            print(json.dumps([dict(r) for r in rows], indent=2)); return
        if not rows:
            print(f"No pending candidates for {args.board}."); return
        print(f"Pending datasheet candidates for {args.board} (confirm/reject by id):")
        for r in rows:
            cite = f"p.{r['page']}" + (f" §{r['section']}" if r["section"] else "")
            print(f"  #{r['id']} [{r['confidence']}/{r['method']}] {r['domain']}.{r['fact_key']}"
                  f" = {r['fact_value']}   ({cite})")
            if r["snippet"]:
                print(f"        “{r['snippet']}”")
        return
    # Full-datasheet digest: read the WHOLE PDF, extract salient facts from every page, and (with
    # --llm) synthesise a grounded "what to remember" briefing. Needs only --file (board-agnostic).
    if args.digest:
        if not args.file:
            print("error: --digest needs --file <pdf>", file=sys.stderr); sys.exit(2)
        from pathlib import Path
        from .engines.ingest.pdf import pdf_to_pages, PdfUnavailable
        from .engines.ingest.digest import analyze, render
        try:
            pages = pdf_to_pages(args.file)
        except PdfUnavailable as e:
            print(f"error: {e}", file=sys.stderr); sys.exit(2)
        gw = None
        if args.llm:
            from .llm.gateway import Gateway
            from .mentor_llm import _mentor_provider
            gw = Gateway(provider=_mentor_provider())
        dg = analyze(pages, Path(args.file).name, gw)
        print(render(dg))
        return

    # default action: ingest a PDF
    if not args.file or not args.board:
        print("error: ingest needs --file <pdf> --board <name> (or --review/--confirm/--reject)",
              file=sys.stderr); sys.exit(2)
    # Fix 1 (v2.3.1): an unknown board is never a dead end — auto-create a skeleton from --arch
    # (or prompt for the architecture), so the datasheet can be analysed against it.
    if repo.load_board(conn, args.board)[0] is None:
        from .engines.ingest.labels import normalize_arch, ARCH_CHOICES
        arch = getattr(args, "arch", None)
        if not arch:
            print(f"'{args.board}' isn't in the database yet. What architecture is it?")
            print("  " + ", ".join(label for label, _ in ARCH_CHOICES))
            try:
                arch = input("Architecture: ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\nNo architecture given — re-run with --arch <arch>. Nothing changed.",
                      file=sys.stderr); sys.exit(2)
            if not arch:
                print("error: an architecture is required to create the board.", file=sys.stderr)
                sys.exit(2)
        repo.create_skeleton_board(conn, args.board, normalize_arch(arch))
        print(f"created a new board '{args.board}' ({normalize_arch(arch)}) — analysing its "
              "datasheet now.")
    try:
        res = ingest_datasheet(conn, args.file, args.board, use_llm=args.llm)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr); sys.exit(2)
    except Exception as e:  # PdfUnavailable etc.
        print(f"error: {e}", file=sys.stderr); sys.exit(2)
    counts = ", ".join(f"{k}={v}" for k, v in sorted(res.counts.items())) or "none"
    print(f"staged {len(res.candidates)} candidate(s) from {args.file} for {args.board} "
          f"({counts}). Review with `eaedk ingest --board {args.board} --review`.")
    for c in res.candidates:
        print(f"  #{c['id']} [{c['confidence']}/{c['method']}] {c['key']} = {c['value']} (p.{c['page']})")
    if getattr(args, "analyze", False):
        from .engines.ingest.report import intelligence_report, render_report_text
        print()
        print(render_report_text(intelligence_report(conn, args.board)))


def _mentor_chat_repl(conn, board: str, use_llm: bool) -> None:
    """A crash-safe terminal chat with the mentor (v2.1.0). Ctrl-D / blank line exits."""
    from .mentor_llm import mentor_chat
    print(f"Mentor chat for {board}. Ask anything; press Enter on an empty line (or Ctrl-D) to "
          "exit.\n")
    messages: list[dict] = []
    while True:
        try:
            q = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye — come back any time.")
            return
        if not q:
            print("Bye — come back any time.")
            return
        messages.append({"role": "user", "content": q})
        answer = mentor_chat(conn, board, messages, use_llm=use_llm)
        messages.append({"role": "assistant", "content": answer})
        print(f"\nmentor> {answer}\n")


def cmd_board_root(args):
    """v3.1 Gap 1: the `board` group with no subcommand — `--capabilities` routes to the summary."""
    if getattr(args, "capabilities", False):
        cmd_board_capabilities(args)
        return
    print("usage: eaedk board {list,show,add,fill-geometry,capability,capabilities} ...\n"
          "       eaedk board --capabilities --board NAME", file=sys.stderr)
    sys.exit(2)


def cmd_board_capabilities(args):
    """P1A: plain-language board capability summary, sourced entirely from the database."""
    conn = _conn(args)
    name = args.board or getattr(args, "name", None)
    if not name:
        print("error: board capabilities needs --board NAME", file=sys.stderr); sys.exit(2)
    name = _resolve_board_or_exit(conn, name)         # v3.1 Gap 2: auto-coerce "bluepill" etc.
    from .mentor_beginner import render_board_capabilities
    print(render_board_capabilities(conn, name), end="")


def cmd_roadmap(args):
    """P1C: a job-focused learning roadmap grounded in the board(s)' confirmed peripherals."""
    conn = _conn(args)
    from .mentor_beginner import render_roadmap
    boards = [_resolve_board_or_exit(conn, n) for n in args.boards]   # v3.1 Gap 2
    print(render_roadmap(conn, boards, args.goal), end="")


def cmd_mentor(args):
    conn = _conn(args)
    args.board = _resolve_board_or_exit(conn, args.board)   # v3.1 Gap 2: auto-coerce board name
    board, _soc = repo.load_board(conn, args.board)
    if board is None:
        _unknown_board(conn, args.board)
        sys.exit(2)
    if getattr(args, "level", None) is not None:
        from .mentor_beginner import render_recommendation
        print(render_recommendation(conn, args.board, args.level), end="")
        return
    if getattr(args, "think", False):
        from .mentor import think_before_code
        goal = "bare_metal_app"
        if args.project:
            p = repo.get_project(conn, args.project)
            if p is not None:
                goal = p["goal_type"]
        items = think_before_code(conn, args.board, goal)
        print(f"Before you write code for {args.board} ({goal}), can you answer these?\n")
        for it in items:
            print(f"  ✓ {it['question']}")
            print(f"      → {it['hint']}\n")
    elif getattr(args, "chat", False):
        _mentor_chat_repl(conn, args.board, use_llm=args.llm)
    elif args.common_mistakes:
        from .mentor import render_common_mistakes
        print(render_common_mistakes(conn, args.board))
    elif args.next_step is not None:
        from .mentor import render_next_step
        print(render_next_step(conn, args.board, args.next_step or None))
    elif args.ask:
        from .mentor_llm import mentor_ask
        print(mentor_ask(conn, args.board, args.ask, use_llm=args.llm))
    elif args.explain:
        from .mentor_llm import mentor_explain
        print(mentor_explain(conn, args.board, args.explain, use_llm=args.llm))
    elif args.review_code:
        if not args.project:
            print("error: --review-code needs --project NAME", file=sys.stderr); sys.exit(2)
        p = _require_project(conn, args.project)
        from .actor_critic import run_actor_critic
        res = run_actor_critic(conn, p)
        if not res.available:
            print(f"Actor-Critic needs the LLM ({res.reason}). The deterministic scaffold is "
                  "already in your export (`eaedk export`).")
            return
        _ARTIFACT_LABEL = {"bare_metal_c": "bare-metal C scaffold",
                           "devicetree": "device-tree node skeleton",
                           "partition_table": "OTA partition table"}
        print(f"Actor-Critic review of '{args.project}' ({res.epochs} epoch(s)) — reviewed: "
              f"{_ARTIFACT_LABEL.get(res.artifact_kind, res.artifact_kind)}:")
        print("\nCONFIRMED by the Validation Engine (real problems):")
        for c in res.confirmed or [{"message": "(none)"}]:
            print(f"  - {c.get('kind','')}: {c.get('message','')}"
                  + (f"  [{c['verified']}]" if c.get("verified") else ""))
        print("\nAdvisory (Critic reasoning, not deterministically proven):")
        for a in res.advisory or [{"message": "(none)"}]:
            print(f"  - {a.get('kind','')}: {a.get('message','')}")
        for f in res.fixes:
            print(f"\nActor fix (epoch {f['epoch']}): {f['fix']}")
    else:
        from .mentor import render_board_mentor
        print(render_board_mentor(conn, args.board))


def cmd_export(args):
    conn = _conn(args)
    p = _require_project(conn, args.name)
    from .engines.output import export_project
    out_dir = args.out or f"{args.name}-bringup"
    res = export_project(conn, p, out_dir, force=args.force, only=args.only,
                         wokwi=getattr(args, "wokwi", False))
    if args.json:
        print(json.dumps(res.__dict__, indent=2, default=str))
        return
    if res.contaminated:
        print(f"Stopped: the folder '{res.out_dir}' already contains files from a different "
              f"project ({res.existing_label}). Mixing two projects' files would be confusing "
              "and could mislead you when you build.", file=sys.stderr)
        print(f"  Fix: pick a new folder, e.g.  eaedk export {args.name} --out {args.name}-fw",
              file=sys.stderr)
        print("  Or overwrite this folder:      add --force to your command", file=sys.stderr)
        sys.exit(2)
    if res.refused:
        print(f"refused: project feasibility is {res.feasibility.upper()} — export needs a "
              "feasible project (or pass --force to emit a DRAFT). Blockers:", file=sys.stderr)
        for b in res.blockers:
            print(f"  - {b}", file=sys.stderr)
        _offer_geometry(conn, p)
        sys.exit(3)
    banner = "" if res.feasibility == "feasible" else "  [DRAFT — not yet feasible]"
    print(f"exported {len(res.written)} file(s) to {res.out_dir}/{banner}")
    for f in res.written:
        print(f"  {f}")
    if res.geometry_unknown:
        print("\n⚠  Your board has no flash geometry — these files will NOT build "
              "(linker shows <UNKNOWN ...>).", file=sys.stderr)
        _offer_geometry(conn, p)


def _offer_geometry(conn, project):
    """Fix 4: a recognized SoC reaches working geometry without a datasheet — offer its
    standard values and the one command that applies them, instead of a silent dead end."""
    bn = repo.project_board_name(conn, project)
    d = repo.soc_defaults_for(conn, bn) if bn else None
    if d:
        print(f"\n   For {d['soc_name']} the standard values are "
              f"{d['flash_bytes'] // 1024}KB flash at {hex(d['flash_base'])}, "
              f"{d['ram_bytes'] // 1024}KB RAM at {hex(d['ram_base'])}.", file=sys.stderr)
        print(f"   Use them:  eaedk board fill-geometry {bn}", file=sys.stderr)
    elif bn:
        print(f"\n   No standard geometry on file for {bn}'s SoC — run `eaedk ingest` on its "
              "datasheet, or onboard real flash/RAM values.", file=sys.stderr)


def cmd_web(args):
    """Launch the Web UI — a thin FastAPI skin over the same engine the CLI uses."""
    try:
        import uvicorn  # noqa: F401
        from .web.server import app
    except ImportError:
        print("The Web UI needs FastAPI + uvicorn, which aren't installed yet.", file=sys.stderr)
        print("Install them with:  pip install -e '.[web]'", file=sys.stderr)
        sys.exit(2)
    url = f"http://{args.host if args.host != '0.0.0.0' else 'localhost'}:{args.port}"
    print(f"EAEDK Web UI running at {url}")
    print("Open that address in your browser. Press Ctrl+C here to stop the server.")
    import uvicorn
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


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

def _add_llm(sp):
    """Fix 3 (v1.7.0): accept --llm/--no-llm *after* the subcommand too, so the hint the tool
    prints ('add --llm …') matches what actually works. SUPPRESS keeps the subparser from
    clobbering the global default unless the flag is explicitly given after the subcommand."""
    g = sp.add_mutually_exclusive_group()
    g.add_argument("--llm", dest="llm", action="store_true", default=argparse.SUPPRESS,
                   help="enable LLM (deferred)")
    g.add_argument("--no-llm", dest="llm", action="store_false", default=argparse.SUPPRESS)
    return sp


def _add_json(sp):
    """v3.1 Gap 7: accept --json *after* the subcommand too (the global --json must precede it).
    SUPPRESS keeps the subparser from clobbering the global default unless explicitly given."""
    sp.add_argument("--json", dest="json", action="store_true", default=argparse.SUPPRESS,
                    help="machine-readable JSON output")
    return sp


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

    board_p = sub.add_parser("board")
    # v3.1 Gap 1: accept the flag form `board --capabilities --board NAME` as an alias for the
    # `board capabilities` subcommand — both route to the same handler.
    board_p.add_argument("--capabilities", action="store_true",
                         help="alias for `board capabilities` — the beginner capability summary")
    board_p.add_argument("--board", dest="board", help="board name (with --capabilities)")
    board_p.set_defaults(func=cmd_board_root)
    bd = board_p.add_subparsers(dest="sub", required=False)
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
    bf = bd.add_parser("fill-geometry",
                       help="fill missing flash/RAM from the board's SoC standard geometry")
    bf.add_argument("name"); bf.set_defaults(func=cmd_board_fill_geometry)
    bc = bd.add_parser("capability").add_subparsers(dest="sub2", required=True)
    bca = bc.add_parser("add", help="add a capability (e.g. UART) so its learning steps unlock")
    bca.add_argument("name"); bca.add_argument("capability")
    bca.set_defaults(func=cmd_board_capability_add)
    bcap = bd.add_parser("capabilities",
                         help="plain-language capability summary for a beginner (arch, memory, "
                              "what EAEDK can and cannot validate)")
    bcap.add_argument("--board", dest="board")
    bcap.add_argument("name", nargs="?", help="board name (or use --board)")
    bcap.set_defaults(func=cmd_board_capabilities)

    pr = sub.add_parser("project").add_subparsers(dest="sub", required=True)
    pr.add_parser("init").set_defaults(func=cmd_project_init)
    pn = pr.add_parser("new"); pn.add_argument("name"); pn.add_argument("--board")
    pn.add_argument("--goal", required=True); pn.set_defaults(func=cmd_project_new)
    pr.add_parser("list").set_defaults(func=cmd_project_list)
    ps = pr.add_parser("show"); ps.add_argument("name"); ps.set_defaults(func=cmd_project_show)
    pa = pr.add_parser("archive"); pa.add_argument("name"); pa.set_defaults(func=cmd_project_archive)
    pst = pr.add_parser("status", help="progress derived from evidence (Validation Engine / you)")
    pst.add_argument("name"); pst.set_defaults(func=cmd_project_status)

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
    cdn = cl.add_parser("done", help="mark a checklist item complete (you confirm it)")
    cdn.add_argument("name"); cdn.add_argument("item"); cdn.set_defaults(func=cmd_checklist_done)

    v = sub.add_parser("validate"); v.add_argument("name", nargs="?"); v.add_argument("--rule")
    v.add_argument("--board", help="check an intent against this board (with --intent)")
    v.add_argument("--intent", help="high-level capabilities to cost out, e.g. \"grpc tls freertos\"")
    _add_json(v); v.set_defaults(func=cmd_validate)
    rk = sub.add_parser("risk").add_subparsers(dest="sub", required=True)
    rks = rk.add_parser("show"); rks.add_argument("name"); rks.set_defaults(func=cmd_risk_show)
    rkr = rk.add_parser("resolve")
    rkr.add_argument("risk_id", type=int); rkr.add_argument("--note")
    rkr.set_defaults(func=cmd_risk_resolve)

    dc = sub.add_parser("decision").add_subparsers(dest="sub", required=True)
    da = dc.add_parser("add"); da.add_argument("name"); da.add_argument("--title", required=True)
    da.add_argument("--rationale"); da.add_argument("--alt"); da.set_defaults(func=cmd_decision_add)

    ak = sub.add_parser("ask"); ak.add_argument("name", nargs="?")
    ak.add_argument("question", nargs="?")
    ak.add_argument("--board", help="ask the Board Query Engine about a board (with confidence)")
    ak.add_argument("--file", help="a datasheet PDF to ingest first, then answer from it")
    _add_llm(ak); ak.set_defaults(func=cmd_ask)
    ex = sub.add_parser("explain"); ex.add_argument("name"); ex.add_argument("--rule", required=True)
    _add_llm(ex); ex.set_defaults(func=cmd_explain)

    mt = sub.add_parser("mentor")
    mt.add_argument("--board", required=True)
    mt.add_argument("--ask"); mt.add_argument("--explain")
    mt.add_argument("--chat", action="store_true",
                    help="open a back-and-forth mentor conversation in the terminal")
    mt.add_argument("--think", action="store_true",
                    help="show the think-before-you-code checklist for this board + goal")
    mt.add_argument("--review-code", dest="review_code", action="store_true")
    mt.add_argument("--project")
    mt.add_argument("--common-mistakes", dest="common_mistakes", action="store_true",
                    help="common first mistakes for this board's chip family")
    mt.add_argument("--next", dest="next_step", nargs="?", const="", default=None,
                    metavar="COMPLETED_STEP",
                    help="the next project to build (optionally name the step you just finished)")
    mt.add_argument("--level", type=int, default=None, metavar="N",
                    help="beginner project recommendation for an experience level (0 = complete "
                         "beginner): what to build first and the next projects, in dependency order")
    _add_llm(mt); mt.set_defaults(func=cmd_mentor)

    rm = sub.add_parser("roadmap",
                        help="a job-focused learning roadmap grounded in this board's confirmed "
                             "peripherals, sequenced as a dependency graph")
    rm.add_argument("--board", dest="boards", action="append", required=True,
                    help="board name (repeat --board to sequence a roadmap across several boards)")
    rm.add_argument("--goal", default="firmware-job")
    rm.set_defaults(func=cmd_roadmap)

    ing = sub.add_parser("ingest")
    ing.add_argument("--file"); ing.add_argument("--board")
    ing.add_argument("--review", action="store_true")
    ing.add_argument("--confirm", type=int); ing.add_argument("--reject", type=int)
    ing.add_argument("--confidence", choices=["HIGH", "MEDIUM", "LOW"])
    ing.add_argument("--analyze", action="store_true",
                     help="after ingest, print the full datasheet intelligence report")
    ing.add_argument("--digest", action="store_true",
                     help="read the WHOLE PDF and print a grounded 'what to remember' digest "
                          "(needs only --file; add --llm for the synthesised briefing)")
    ing.add_argument("--arch", help="architecture for a new (unknown) board, e.g. arm-cortex-m4")
    _add_llm(ing); ing.set_defaults(func=cmd_ingest)

    ex = sub.add_parser("export"); ex.add_argument("name"); ex.add_argument("--out")
    ex.add_argument("--force", action="store_true")
    ex.add_argument("--only", choices=["checklist", "cmake", "flash"])
    ex.add_argument("--wokwi", action="store_true",
                    help="also generate Wokwi simulator files (diagram.json + wokwi.toml)")
    ex.set_defaults(func=cmd_export)

    ev = sub.add_parser("eval").add_subparsers(dest="sub", required=True)
    er = ev.add_parser("run"); er.add_argument("--case"); er.set_defaults(func=cmd_eval_run)

    wb = sub.add_parser("web", help="launch the Web UI (a browser interface to the same engine)")
    wb.add_argument("--host", default="127.0.0.1")
    wb.add_argument("--port", type=int, default=8080)
    wb.set_defaults(func=cmd_web)

    tc = sub.add_parser("toolchain").add_subparsers(dest="sub", required=True)
    tc.add_parser("detect").set_defaults(func=cmd_toolchain_detect)
    tcv = tc.add_parser("validate"); tcv.add_argument("--project", dest="name", required=True)
    tcv.set_defaults(func=cmd_toolchain_validate)

    lg = sub.add_parser("log").add_subparsers(dest="sub", required=True)
    la = lg.add_parser("analyze")
    la.add_argument("--file", required=True); la.add_argument("--project")
    la.add_argument("--deep", action="store_true",
                    help="also run LLM triage even when a deterministic signature matched "
                         "(so a generic match can't hide the root cause)")
    la.add_argument("--project-aware", dest="project_aware", action="store_true",
                    help="enrich LLM triage with project validation gaps + unverified facts")
    _add_llm(la); la.set_defaults(func=cmd_log_analyze)

    bh = sub.add_parser("bughunt",
                         help="scan firmware C/C++ source for common embedded bug patterns")
    bh.add_argument("path", help="path to a firmware source directory or single .c/.h file")
    bh.add_argument("--max-files", type=int, default=500, dest="max_files",
                    help="stop after scanning this many files (default 500)")
    _add_json(bh); bh.set_defaults(func=cmd_bughunt)

    return p


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
