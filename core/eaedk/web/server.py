"""FastAPI server for the EAEDK Web UI.

Every route is a thin wrapper: open a DB connection, call ONE existing engine function, serialise
the result. No business logic, no validation rules, no SQL — the engine is the single source of
truth (the CLI calls the same functions). Errors are returned as a plain-English {error, next}
envelope, never a traceback or a bare HTTP code.
"""
from __future__ import annotations

import io
import zipfile
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import (HTMLResponse, JSONResponse, RedirectResponse, Response)
from fastapi.staticfiles import StaticFiles

from .. import repo, mentor
from ..orchestrator import assess_project
from ..paths import default_db_path
from ..project_init import GOAL_LABELS, _GOAL_ORDER
from ..store.db import connect

_STATIC = Path(__file__).resolve().parent / "static"

app = FastAPI(title="EAEDK Web UI")


def _conn():
    """A fresh connection to the same local DB the CLI uses (honours EAEDK_DB)."""
    return connect(str(default_db_path()))


def _err(msg: str, nxt: str, status: int = 400) -> JSONResponse:
    """A user-facing error: what happened + what to do next. Never a traceback/HTTP code."""
    return JSONResponse(status_code=status, content={"error": msg, "next": nxt})


@app.exception_handler(Exception)
async def _on_error(request: Request, exc: Exception):  # noqa: ANN001
    return _err("Something went wrong on the server while handling that request.",
                "Try again. If it keeps happening, restart `eaedk web` in your terminal and "
                "reload this page.", status=500)


# --- pages -----------------------------------------------------------------

@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse(url="/static/boards.html")


# --- board confidence -> traffic light -------------------------------------

def _board_light(board: dict) -> tuple[str, str]:
    """(light, reason) for a board. RED = LOW confidence or missing geometry; GREEN = HIGH +
    full geometry; YELLOW otherwise."""
    if board is None:
        return "RED", "board not found"
    no_geo = board.get("flash_bytes") is None or board.get("ram_bytes") is None
    conf = (board.get("confidence") or "").upper()
    if no_geo or conf == "LOW":
        return "RED", "no flash/RAM geometry — needs datasheet values" if no_geo else "low confidence"
    if conf == "HIGH":
        return "GREEN", "verified geometry, high confidence"
    return "YELLOW", "some values unverified (medium confidence)"


# --- Page 1: Board Explorer ------------------------------------------------

@app.get("/api/boards")
def api_boards():
    conn = _conn()
    out = []
    for row in repo.list_boards(conn):
        board, soc = repo.load_board(conn, row["name"])
        light, reason = _board_light(board)
        out.append({
            "name": row["name"], "soc": row["soc"], "arch": row["arch"],
            "flash_bytes": board["flash_bytes"], "ram_bytes": board["ram_bytes"],
            "confidence": board["confidence"], "light": light, "light_reason": reason,
        })
    return {"boards": out}


@app.get("/api/boards/{name}")
def api_board_detail(name: str):
    conn = _conn()
    board, soc = repo.load_board(conn, name)
    if board is None:
        return _err(f"Board not found: {name!r}.",
                    "Go back to Boards and pick one from the list.", status=404)
    caps = [{"capability": c["capability"], "summary": c["summary"]}
            for c in mentor.capability_map(conn, name)]
    cap_names = {c["capability"] for c in caps}
    path = mentor.learning_path_for(conn, cap_names)
    dropped = mentor.dropped_steps_for(conn, cap_names)
    light, reason = _board_light(board)
    return {
        "name": name, "soc": soc, "board": board, "light": light, "light_reason": reason,
        "capabilities": caps,
        "learning_path": [{"step": s["step"], "title": s["title"], "why": s["why"],
                           "before_you_start": s["before_you_start"]} for s in path],
        "dropped": [{"step": d["step"], "title": d["title"], "missing": d["missing"]}
                    for d in dropped],
        "driver_path": mentor.driver_path(conn, name),   # v2.1.0: empty unless the board runs Linux
    }


# --- Datasheet Intelligence + Query (v2.3.0) -------------------------------

@app.get("/api/similar/{name}")
def api_similar(name: str):
    from ..engines.ingest.similarity import similar_with_guidance
    conn = _conn()
    if repo.load_board(conn, name)[0] is None:
        return _err(f"Board not found: {name!r}.", "Pick a board from the Boards list.", status=404)
    return {"board": name, "similar": similar_with_guidance(conn, name, top=3)}


@app.post("/api/ask")
async def api_ask(request: Request):
    from ..engines.ingest.query import answer_query
    body = await request.json()
    board = body.get("board") or ""
    question = (body.get("question") or "").strip()
    use_llm = bool(body.get("use_llm"))
    if not question:
        return _err("Type a question first.", "Ask anything about the board, e.g. "
                    "\"what is the default clock?\"")
    conn = _conn()
    if repo.load_board(conn, board)[0] is None:
        return _err(f"Board not found: {board!r}.", "Pick a board first.")
    return {"board": board, **answer_query(conn, board, question, use_llm=use_llm)}


@app.get("/api/report/{name}")
def api_report(name: str):
    from ..engines.ingest.report import intelligence_report
    conn = _conn()
    if repo.load_board(conn, name)[0] is None:
        return _err(f"Board not found: {name!r}.", "Pick a board first.")
    return intelligence_report(conn, name)


@app.post("/api/ingest")
async def api_ingest(request: Request):
    """Ingest a datasheet (PDF bytes or pasted text) and return the full 7-section report."""
    import base64
    import tempfile
    from ..engines.ingest import ingest_datasheet
    from ..engines.ingest.report import intelligence_report
    from ..engines.ingest.extract import Page
    body = await request.json()
    board = body.get("board") or ""
    conn = _conn()
    if repo.load_board(conn, board)[0] is None:
        return _err(f"Board not found: {board!r}.", "Pick a board from the dropdown first.")
    try:
        if body.get("pdf_base64"):
            raw = base64.b64decode(body["pdf_base64"])
            with tempfile.NamedTemporaryFile("wb", suffix=".pdf", delete=False) as f:
                f.write(raw)
                tmp = f.name
            ingest_datasheet(conn, tmp, board)
            Path(tmp).unlink(missing_ok=True)
        elif body.get("text"):
            # pasted datasheet text -> a single synthetic page (no PDF reader needed)
            text = body["text"]
            ingest_datasheet(conn, "pasted.txt", board, reader=lambda _p: [Page(1, text)])
        else:
            return _err("No datasheet provided.", "Upload a PDF or paste datasheet text, then "
                        "click Analyze.")
    except Exception as e:
        return _err(f"Couldn't read that datasheet ({e}).",
                    "Make sure it's a real PDF or plain text, then try again.")
    return {"board": board, "report": intelligence_report(conn, board)}


# --- Page 2: Project Setup -------------------------------------------------

# Feasibility (engine) -> UI traffic light + plain-English label/explanation.
_FEAS_UI = {
    "feasible": ("GREEN", "Feasible", "Ready to export — you can generate the build files now."),
    "no_geometry": ("YELLOW", "Incomplete",
                    "This board is missing its flash/RAM sizes. Fill those in before building."),
    "blocked": ("YELLOW", "Incomplete",
                "Some required details are missing. The items below tell you what to provide."),
    "not_feasible": ("RED", "Blocked",
                     "Something doesn't fit or match. Fix the items below before going further."),
}


def _feasibility_ui(feas: str) -> dict:
    light, label, explain = _FEAS_UI.get(feas, ("YELLOW", feas, ""))
    return {"feasibility": feas, "light": light, "label": label, "explain": explain}


@app.get("/api/goals")
def api_goals():
    """The goal types you can pick, with plain-English labels (the templated ones, in order)."""
    return {"goals": [{"value": g, "label": GOAL_LABELS.get(g, g)} for g in _GOAL_ORDER]}


@app.get("/api/projects")
def api_projects():
    conn = _conn()
    return {"projects": [{"name": r["name"], "goal": r["goal_type"], "status": r["status"],
                          "board": r["board"]} for r in repo.list_projects(conn)]}


@app.post("/api/projects")
async def api_create_project(request: Request):
    body = await request.json()
    name = (body.get("name") or "").strip()
    board = body.get("board") or None
    goal = body.get("goal") or ""
    if not name:
        return _err("Please enter a project name.", "Type a short name like 'blink' and submit.")
    conn = _conn()
    if repo.get_project(conn, name) is not None:
        return _err(f"A project named {name!r} already exists.",
                    "Pick a different name, or use the existing one on the Validate page.")
    try:
        repo.create_project(conn, name, goal, board)
    except ValueError as e:
        return _err(f"Couldn't create the project: {e}.",
                    "Pick a board from the dropdown and a goal, then submit again.")
    resp = assess_project(conn, repo.get_project(conn, name))
    d = resp.to_dict()
    risks = [{"name": r["rule_key"], "severity": r["severity"],
              "explanation": r["explanation"], "mitigation": r.get("mitigation")}
             for r in d["risks"]]
    return {"name": name, "board": board, "goal": goal,
            **_feasibility_ui(d["feasibility"]), "risks": risks, "next_step": d["next_step"]}


# --- Engineering State Engine (progress) -----------------------------------

_PROGRESS_LIGHT = {"COMPLETE": "GREEN", "IN_PROGRESS": "YELLOW", "NOT_STARTED": "GREY"}


@app.get("/api/progress/{project}")
def api_progress(project: str):
    from ..engines.state import project_status
    conn = _conn()
    p = repo.get_project(conn, project)
    if p is None:
        return _err(f"Project not found: {project!r}.", "Pick a project from the dropdown.")
    s = project_status(conn, p)
    for it in s["items"]:
        it["light"] = _PROGRESS_LIGHT.get(it["status"], "GREY")
    return s


# --- Page 3: Validate ------------------------------------------------------

@app.get("/api/validate/{project}")
def api_validate(project: str):
    conn = _conn()
    p = repo.get_project(conn, project)
    if p is None:
        return _err(f"Project not found: {project!r}.",
                    "Pick a project from the dropdown (or create one on the New Project page).")
    d = assess_project(conn, p).to_dict()
    checks = [{"check": v["check"], "status": v["status"], "reason": v["reason"],
               "teach": v.get("teach", ""), "gating": v.get("gating", True),
               "engaged": v.get("engaged", True)} for v in d["validations"]]
    return {"project": project, **_feasibility_ui(d["feasibility"]),
            "checks": checks, "next_step": d["next_step"]}


# --- Page 4: Export --------------------------------------------------------

def _export_dir(project: str) -> Path:
    """A stable per-project export folder next to the local DB."""
    return Path(default_db_path()).parent / "web-exports" / project


def _tree(out: Path) -> list[str]:
    """Relative paths of generated files (skips hidden bookkeeping like the .eaedk-export marker)."""
    files = []
    for p in sorted(out.rglob("*")):
        if p.is_file() and not any(part.startswith(".") for part in p.relative_to(out).parts):
            files.append(str(p.relative_to(out)))
    return files


@app.post("/api/export/{project}")
def api_export(project: str):
    from ..engines.output import export_project
    conn = _conn()
    p = repo.get_project(conn, project)
    if p is None:
        return _err(f"Project not found: {project!r}.", "Pick a project from the dropdown.")
    from ..engines.output.wokwi import WOKWI_BOARDS, supported_boards
    out = _export_dir(project)
    res = export_project(conn, p, str(out), wokwi=True)   # the web always offers simulation files
    if res.refused:
        return _err("This project isn't ready to export yet.",
                    "Open the Validate page to see what's missing, fill it in, then export again. "
                    "(" + "; ".join(res.blockers[:2]) + ")")
    if res.contaminated:
        return _err("That export folder already holds a different project's files.",
                    "This shouldn't normally happen from the web app — restart `eaedk web`.")
    files = _tree(out)
    board_name = repo.project_board_name(conn, p)
    return {"project": project, "out_dir": str(out), "files": files,
            "geometry_unknown": res.geometry_unknown,
            "wokwi_supported": board_name in WOKWI_BOARDS,
            "wokwi_files": [f for f in files if f.startswith("wokwi/")],
            "wokwi_supported_list": supported_boards(),
            "compile_command": _compile_command(out)}


def _compile_command(out: Path) -> str:
    """The first build command from START_HERE.md (so the Wokwi panel can show 'compile first')."""
    sh = out / "START_HERE.md"
    if not sh.exists():
        return ""
    in_build = False
    for line in sh.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("## Build"):
            in_build = True
            continue
        if in_build:
            s = line.strip()
            if s.startswith("```") or s == "" or s.startswith("#"):
                continue
            if s.startswith("##"):
                break
            return s
    return ""


@app.get("/api/export/{project}/file")
def api_export_file(project: str, path: str):
    out = _export_dir(project).resolve()
    target = (out / path).resolve()
    if out not in target.parents or not target.is_file():
        return _err("That file isn't part of this export.", "Pick a file from the list on the left.")
    return {"path": path, "content": target.read_text(encoding="utf-8", errors="replace")}


@app.get("/api/export/{project}/download")
def api_export_download(project: str):
    out = _export_dir(project)
    if not out.is_dir() or not _tree(out):
        return _err("Nothing to download yet.", "Click Export first, then download.")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for rel in _tree(out):
            z.write(out / rel, arcname=f"{project}/{rel}")
    return Response(content=buf.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{project}-export.zip"'})


# --- Page 5: Log Analyzer --------------------------------------------------

@app.post("/api/logs/analyze")
async def api_logs(request: Request):
    import tempfile
    from ..engines.logs.engine import analyze_log_async   # async engine fn (CLI uses the sync wrapper)
    body = await request.json()
    text = body.get("text")
    project = body.get("project") or None
    use_llm = bool(body.get("use_llm"))
    if not text or not text.strip():
        return _err("There's no log to analyze.",
                    "Paste some log text, or drag a log file onto the box, then click Analyze.")
    conn = _conn()
    if project and repo.get_project(conn, project) is None:
        return _err(f"Project not found: {project!r}.",
                    "Choose a project from the dropdown, or leave it on 'No project'.")
    with tempfile.NamedTemporaryFile("w", suffix=".log", delete=False, encoding="utf-8") as f:
        f.write(text)
        tmp = f.name
    try:
        res = await analyze_log_async(conn, tmp, project_name=project, use_llm=use_llm,
                                      project_aware=bool(project), deep=use_llm)
    finally:
        Path(tmp).unlink(missing_ok=True)
    d = res.to_dict()
    matches = [{"severity": m["severity"], "line_no": m["line_no"], "line": m["line"],
                "cause": m["cause"], "fix": m["fix"]} for m in d["matches"]]
    writebacks = [{"rule": w["rule"], "severity": w["severity"], "items": w["items"]}
                  for w in d.get("write_backs", [])]
    return {"format": d["format"], "n_lines": d["n_lines"], "matches": matches,
            "next_action": d.get("next_action"), "write_backs": writebacks,
            "triage": d.get("triage")}


# --- Page 6: Mentor (structure reuses /api/boards/{name}; this is the ask box) ---

@app.post("/api/mentor/ask")
async def api_mentor_ask(request: Request):
    from ..mentor_llm import mentor_ask
    body = await request.json()
    board = body.get("board") or ""
    question = (body.get("question") or "").strip()
    use_llm = bool(body.get("use_llm"))
    if not question:
        return _err("Type a question first.",
                    "Ask something like 'what should I build first?' and press Ask.")
    conn = _conn()
    if repo.load_board(conn, board)[0] is None:
        return _err(f"Board not found: {board!r}.", "Pick a board from the dropdown at the top.")
    answer = mentor_ask(conn, board, question, use_llm=use_llm)
    return {"board": board, "question": question, "answer": answer}


@app.post("/api/mentor/chat")
async def api_mentor_chat(request: Request):
    from ..mentor_llm import mentor_chat
    body = await request.json()
    board = body.get("board") or ""
    messages = body.get("messages") or []
    use_llm = bool(body.get("use_llm"))
    if not any(m.get("role") == "user" and (m.get("content") or "").strip() for m in messages):
        return _err("Type a message first.", "Ask the mentor anything about your board.")
    project = body.get("project") or None
    has_hardware = bool(body.get("has_hardware"))
    conn = _conn()
    if repo.load_board(conn, board)[0] is None:
        return _err(f"Board not found: {board!r}.", "Pick a board from the dropdown at the top.")
    answer = mentor_chat(conn, board, messages, use_llm=use_llm,
                         project=project, has_hardware=has_hardware)
    return {"board": board, "answer": answer}


# --- Page 7: Code Studio (surfaces the existing Actor-Critic loop) ----------

@app.get("/api/studio/{project}")
def api_studio(project: str):
    from ..engines.output import export
    from ..engines.output import codegen
    conn = _conn()
    p = repo.get_project(conn, project)
    if p is None:
        return _err(f"Project not found: {project!r}.", "Create a project on the New Project page.")
    board_name = repo.project_board_name(conn, p)
    goal = p["goal_type"]
    data = export.gather(conn, p)
    data["inputs"] = repo.load_inputs(conn, p["id"])[0]
    artifact_kind, template = codegen.render_review_artifact(data)
    return {
        "project": project, "board": board_name, "goal": goal, "artifact_kind": artifact_kind,
        "template": template,
        "checklist": mentor.think_before_code(conn, board_name, goal) if board_name else [],
        "driver_path": mentor.driver_path(conn, board_name) if board_name else [],
    }


@app.post("/api/studio/{project}/review")
async def api_studio_review(request: Request):
    from .. import actor_critic
    body = await request.json()
    project = body.get("project") or ""
    code = body.get("code")
    conn = _conn()
    p = repo.get_project(conn, project)
    if p is None:
        return _err(f"Project not found: {project!r}.", "Pick a project and try again.")

    def _issue(c):
        return {"check": c.get("kind", ""), "what": c.get("message", ""),
                "why": c.get("teach") or c.get("verified") or "", "note": c.get("note", "")}

    res = actor_critic.run_actor_critic(conn, p, code=code)
    if res.available:
        confirmed, advisory = res.confirmed, res.advisory
        ai_note = ""
    else:
        # The deterministic CONFIRMED issues don't need the model — show them regardless.
        confirmed = actor_critic.grounded_confirmations(conn, p)
        advisory = []
        ai_note = ("The deterministic checks below are complete. For extra AI suggestions, "
                   "install the local model (Ollama) — but you don't need it to fix what's listed.")
    # Piece 5 (v2.2.0): a clean review (no real problems) lets the engineer mark items complete
    # via the State Engine's USER path. Offer the not-yet-complete items.
    from ..engines.state import project_status
    incomplete = []
    if not confirmed:
        s = project_status(conn, p)
        incomplete = [{"item_key": i["item_key"], "title": i["title"]}
                      for i in s["items"] if i["status"] != "COMPLETE"]
    return {"project": project,
            "confirmed": [_issue(c) for c in confirmed],
            "advisory": [_issue(a) for a in advisory],
            "ai_note": ai_note, "can_complete": incomplete}


@app.post("/api/studio/{project}/complete")
async def api_studio_complete(request: Request):
    body = await request.json()
    project = body.get("project") or ""
    item_key = body.get("item_key") or ""
    conn = _conn()
    p = repo.get_project(conn, project)
    if p is None:
        return _err(f"Project not found: {project!r}.", "Pick a project and try again.")
    row = next((r for r in repo.checklist(conn, p["id"]) if r["item_key"] == item_key), None)
    if row is None:
        return _err("That checklist item doesn't exist.", "Reload the page and try again.")
    repo.set_checklist_status(conn, p["id"], item_key, "done", "confirmed by engineer")
    from ..engines.state import project_status
    s = project_status(conn, p)
    return {"project": project, "marked": row["text"], "percent": s["percent"],
            "complete": s["complete"], "total": s["total"]}


# --- static (mounted last so /api and / win) -------------------------------
app.mount("/static", StaticFiles(directory=str(_STATIC), html=True), name="static")
