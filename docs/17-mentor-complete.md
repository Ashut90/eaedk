# 17 — Mentor Complete (v2.1.0-mentor-complete)

Four additive pieces from the original architecture diagram. Everything built through v2.0.0
stays intact. **Baseline: `pytest` 196, `eval 14/14` — confirmed before and after every change.**
No existing CLI verb, schema, seed, validation rule, template, or test behaviour changes; the
deterministic core (orchestrator, validation/risk/log engines, gateway, post-filter) is untouched.
Built in the mandated order (lowest risk first).

## Piece 1 — Driver development learning path (seed data)
A first-class Linux-driver path, surfaced for boards that run Linux (deterministic signal:
SoC arch contains `cortex-a` → BeagleBone-Black, Raspberry-Pi-4, STM32MP157, i.MX8M-Mini-EVK).
- New `packages/knowledge-seed/driver_path.yaml`: 6 steps (character device → platform → I2C
  client → SPI → interrupt-driven → DMA). Each carries what-it-is, why-before-the-next, the
  kernel function signatures introduced, and a build-this exercise. Loaded into the **existing**
  `learning_steps` table with `goal_type: driver` (the seed loader is extended to read the new
  file — additive). No schema change.
- `mentor.driver_path(conn, board)` returns the steps when `supports_linux(soc)` else `[]`;
  `learning_path_for` / `dropped_steps_for` gain a one-line guard so the new `driver` rows never
  leak into the bare-metal path or the "not shown yet" list (existing output unchanged).
- Surfaced in `eaedk mentor --board <name>` as a "Driver development path" section (Linux boards
  only) and in the Web UI Mentor tab as a collapsible "Driver Development" section.

## Piece 2 — Wokwi simulation file generation
`eaedk export <project> --wokwi` (default off; normal export byte-for-byte unchanged) also writes:
- `wokwi/diagram.json` — board wiring (the Wokwi `board-*` part for the SoC + the on-board LED
  wired, derived from the board's arch/SoC in SQLite, never hardcoded assumptions).
- `wokwi/wokwi.toml` — points at the compiled firmware path.

Board map (Wokwi's native parts): Blue Pill→`board-blue-pill`, Pico→`board-pi-pico`,
ESP32-DevKitC→`board-esp32-devkit-c`, Arduino-Uno→`board-arduino-uno`, Mega→`board-arduino-mega`.
Unsupported boards get `wokwi/README.txt` explaining it's not available + the supported list —
never a silent gap. Web UI Export tab shows a "Wokwi Simulation" section with the two files, the
plain-English "compile first, then drag these into Wokwi" instruction, and the exact compile
command from START_HERE.

## Piece 3 — Think-before-code checklist (deterministic, board-aware)
`mentor.think_before_code(conn, board, goal)` builds beginner questions from the board's verified
facts + capabilities + goal — **not hardcoded, not LLM**. e.g. STM32F103 + bare_metal_app asks
about the LED pin/clock domain, clock-before-use, init order, and CPU-before-clock-stable. Shown
above the Code Studio editor.

## Piece 4 — Code Studio tab (Web UI surface for Actor-Critic)
A new tab that surfaces the **existing** Actor-Critic loop (`actor_critic.run_actor_critic`) with
no new logic: the think-before-code checklist, a starting template in a plain monospace
`<textarea>` (the existing goal-aware `codegen.render_review_artifact`, cited in comments), and a
**Review** button that runs the loop and shows CONFIRMED issues (RED, from the Validation Engine —
what's wrong / why it matters / what to change) separately from Advisory (YELLOW, Critic). When
goal = `driver`, the driver learning path is shown inside the tab.

## Piece 5 — Conversational Mentor (2-way chat)
`mentor_llm.mentor_chat(conn, board, messages, use_llm, gateway)` turns the one-way answer into a
conversation. Each turn injects the board's **verified** facts (flash/RAM/arch/capabilities/
learning path) and, when the question names a known concept, that concept's anchor. The response
is shaped to always carry: a plain answer, one board-tied practical example, and a follow-up
question or "try this" — never a bare definition, never "I don't know" without a next action. The
**post-filter runs on every response** (uncited addresses/sizes/clocks stripped — the trust rule
is absolute, so frequencies the DB doesn't hold are removed even from a chat). Works offline: a
deterministic backbone (concept anchor + learning-path guidance + a board-specific "try this" +
a question) is always returned; the LLM elaborates on top when available. History persists in the
browser session and is replayed into each prompt (server stays stateless). Web UI Mentor tab
becomes a chat; CLI adds `eaedk --llm mentor --board <name> --chat` (a crash-safe REPL).

## The governing rule (applied to every line of new output)
Would a person with 6 months of electronics and zero CS degree understand this and know what to
do next? Applied to the LLM responses, the Code Studio checklist, the Wokwi instructions, and the
driver path — each must be beginner-clear and mid-level-useful.

## Testing
`core/tests/test_mentor_complete.py` (+ additions to `test_web.py`): driver path appears only for
Linux boards and never pollutes the bare-metal path; `--wokwi` emits board-correct files and a
clear message for unsupported boards; the checklist is board-derived; the chat backbone always
ends with an action and is post-filtered; Code Studio + chat routes round-trip. Existing 196 stay
green.
