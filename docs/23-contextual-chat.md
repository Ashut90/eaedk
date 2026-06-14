# 23 — Contextual chat box on four pages (v2.4.0)

A persistent, context-aware chat box on **Boards**, **Mentor**, **Code Studio**, and **Datasheet**.
Same mentor reasoning engine on every page (docs/22); the context changes per page, the reasoning
standard never does. Additive only — no schema, no new tables, no new CLI verbs.

## One backend route, four contexts

A single new FastAPI route handles all four pages:

```
POST /api/chat
{
  "page_type":        "boards" | "mentor" | "studio" | "datasheet",
  "board_name":       "...",                # the board in view
  "project_name":     "...",                # optional
  "wokwi_flag":       true | false,         # Wokwi vs physical hardware
  "current_code":     "...",                # studio only — editor contents
  "extracted_facts":  [...],                # datasheet only — staged candidates + confidence
  "review_results":   {...},                # studio only — last Actor-Critic result, if any
  "conversation_history": [{role, content}, ...],
  "user_message":     "..."
}
```

The route builds a page-appropriate context block and calls the existing `mentor_chat` reasoning
function (docs/22). It does **not** fork the mentor logic — one engine, four context builders. The
post-filter runs on every reply, as everywhere else.

### Per-page context and roles

| Page | Injected context | Roles |
|---|---|---|
| **Boards** | the viewed board's full capability map (peripherals, flash, RAM, arch, confidence) + the list of all seeded boards for comparison | A + D |
| **Mentor** | board, project, current learning step, Wokwi flag, State-Engine progress | A + D |
| **Code Studio** | board, project, the **current editor code**, think-before-code status, last review result, Wokwi flag | B (→ A for architecture) |
| **Datasheet** | the board being analyzed, all extracted facts **with confidence**, mandatory-missing items, closest-board match | A (datasheet-aware) |

### Datasheet confidence rule

Every datasheet-page answer states where the information came from: **HIGH** (from the datasheet —
cited page + section), **MEDIUM** (general knowledge — verify before use), or **UNKNOWN** (not found
— search strings + a hard warning). This reuses the existing Board Query Engine's confidence
contract (`answer_query`); a hardware question is never answered without a confidence level.

## Offline degradation (never a dead end)

If Ollama is not running, the chat returns the **deterministic backbone** the mentor engine already
produces from SQLite (learning path / concept anchor / State-Engine progress / the datasheet query
engine), plus a one-line "Start Ollama for a more conversational answer." The box is never empty and
never errors.

## Frontend: one shared widget

A single `web/static/chat-widget.js`, included on all four pages. The page calls
`mountChat({page_type, getContext})` once; the widget owns expand/collapse, send/receive, the
"thinking…" indicator, per-page session history (in-memory for the page's lifetime), and the
collapsed-by-default "Ask EAEDK" launcher fixed above the footer. No chat logic is duplicated across
pages — each page supplies only a `getContext()` that returns its current state (selected board,
editor contents, etc.). Monospace for code spans, sans-serif for prose, matching the existing
stylesheet.

## Tests (Part 2)

- `/api/chat` with `page_type="boards"` injects the SQLite capability map into the context.
- `/api/chat` with `page_type="studio"` injects the editor `current_code` into the context.
- `/api/chat` with `page_type="datasheet"` returns a confidence level on a hardware question.
- `/api/chat` with `page_type="mentor"` injects State-Engine progress for a project.
- Offline (no model) returns the deterministic backbone, never an error.

## Delivery order (each step: full pytest before and after)

1. Rewrite the six prompts (docs/22). 2. `/api/chat` route. 3. `chat-widget.js`. 4. Boards page.
5. Verify Mentor page uses the new prompts + consistent widget. 6. Code Studio page. 7. Datasheet
page. Then full `pytest` + `eaedk eval run` + manual end-to-end on STM32F103-BluePill / bare_metal_app
on both the Wokwi and physical-hardware paths. Branch `feature/mentor-reasoning-and-chat`, ff-merge,
tag `v2.4.0-mentor-complete`.
