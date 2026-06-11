# EAEDK — Mentor Layer (v1.5.0) — Design

**Status:** Design (doc-first, before code). Builds **on top of** v1.4.0 — nothing is replaced.
**Goal:** a 0-experience engineer picks up a board, says "I want to learn firmware," and EAEDK
responds like a senior engineer next to them: plain-language capabilities, a learning path with
*reasons*, working teach-commented starter code, and an LLM that **explains** (never asserts a
hardware fact).

The same trust boundary holds throughout: **curated data and deterministic engines hold the
truth; the LLM only explains.** Capability text and project recommendations are seeded YAML
(not hardcoded, not LLM-generated). Generated code values are either board facts from SQLite or
family-reference constants cited in the template — never invented. The post-filter governs every
LLM output.

---

## Part 1 — `eaedk mentor --board <name>`

Plain-language onboarding for a board. Three sections, all from seeded data.

### Data (seeded YAML → SQLite, same pattern as board seeds)
- `packages/knowledge-seed/capabilities.yaml` → table `capabilities(name, summary)`.
  One plain sentence per peripheral: *"UART — serial communication; used to print debug
  messages to your PC."* Generic (a UART is a UART), joined to a board via `board_capabilities`.
- `packages/knowledge-seed/learning_path.yaml` → table `learning_steps(step, key, title,
  goal_type, requires_json, why, before_you_start_json)`. A deliberate ordered sequence:
  Blink → UART Logger → SPI Sensor → Interrupt Handler → Bootloader → RTOS. Each step carries:
  - `why` — why it comes *before* the next (the order is pedagogy, not arbitrary).
  - `requires` — capabilities the board must have (SPI Sensor only shows if the board has SPI).
  - `before_you_start` — a plain checklist of what to know first (memory map, toolchain, debug
    method, flash layout), written for someone who doesn't know these questions exist.

### Command
`eaedk mentor --board <name>` prints:
1. **What this board can do** — each `board_capabilities` entry + its plain `summary`.
2. **Learning path** — the ordered steps whose `requires` are satisfied by the board, each with
   its `why`.
3. **Before you write code** (per step) — the `before_you_start` checklist.

Migration `0007_mentor.sql` adds `capabilities` + `learning_steps`. Seed loader extends
`seed.py`. No raw SQL outside repo helpers.

---

## Part 2 — Teach-commented starter code (extends the export engine)

Today `export` emits a minimal `src/main.c` stub. For `bare_metal_app`, emit a **working
starting point** with teach comments on every non-obvious line.

### Honesty rule (the crux)
Register-level code needs addresses. We never invent them. Two sources only:
- **Board facts from SQLite** (clock frequency from `sysclk_max_hz`, flash/RAM geometry, UART
  peripheral, GPIO port) — substituted into the template, comment cites the fact.
- **Family-reference constants** (e.g. STM32F1 `USART1 = 0x40013800`) live in a **curated,
  cited code template** in `packages/code_templates/` — seed data with a header citing the
  family Reference Manual (RM0008 for STM32F1). This is the same standard as the board seeds:
  curated + cited, not LLM-generated.

### What's generated (for a matching family)
- **Working UART hello-world + blink** `main.c`: clock init, UART config, prints a boot banner,
  toggles an LED on a loop. Every non-obvious line teaches:
  `/* Enable GPIOA clock — peripherals are off by default to save power (RM0008 §7) */`.
  Board-specific values (SYSCLK, UART, GPIO port) are filled from the board's facts and the
  comment says where they came from.
- For a board with **no matching family template**, fall back to a **teach skeleton**: a
  structurally-correct `main()` with comments and clearly-marked `/* TODO: <MCU> register init —
  from your datasheet register map, or run \`eaedk ingest\` */` placeholders. Honest, never a guess.

### `START_HERE.md` in the export bundle
Plain language for someone who has never seen a CMakeLists: what each file is, how to build
(`cmake -B build … && cmake --build build`), what to change first, what to try next (the learning
path). Generated from the board facts + the learning path.

---

## Part 3 — LLM reasoning for the mentor (explain, don't assert)

The LLM's role is explanation and guidance only — reusing the existing `Gateway` + post-filter.

- `eaedk mentor --board <name> --ask "what should I build first"` — the LLM is given the board's
  capability map + learning path (from SQLite) as context and explains the recommendation in
  plain language. It cannot invent peripheral addresses or register values.
- `eaedk mentor --board <name> --explain HardFault` — the LLM explains the concept in **two
  sentences max** (what it is, what to check next), using the board's architecture as context.
- **Post-filter applies** (same guardrail): any hex address / memory size / clock / timing not in
  the SQLite-cited allowlist is stripped. Off by default; only these mentor verbs consult it.

A small curated `concepts.yaml` (e.g. HardFault, watchdog, DMA) seeds a one-line factual anchor
per concept so the explanation has a deterministic backbone even if the model is weak/offline; the
LLM elaborates, the post-filter trims.

---

## Part 4 — Actor-Critic multi-agent loop (V2, same branch)

On top of the mentor, an optional `--actor-critic` pass to harden the generated scaffold.

- **Actor** — given board facts + the selected template, generates/annotates a code scaffold with
  teach comments (LLM, post-filtered).
- **Critic** — reviews the Actor's output for **beginner mistakes only**: wrong peripheral init
  order, missing clock enable, stack too small, buffer larger than available RAM. The Critic
  **cannot invent hardware facts** — it reasons about code structure + memory usage against the
  verified board data; its output is post-filtered.
- **Validation Engine as arbiter** — the Critic's *concrete* claims are checked **deterministically**
  before being shown: e.g. "stack 64 KB" vs the board's RAM (`RAM_BUDGET`), "buffer 8 KB" vs RAM.
  Only checks that pass the deterministic arbiter are surfaced as confirmed; the rest are advisory.
- **Loop:** max **2 epochs**; if Actor and Critic don't converge, show the best result + the
  arbiter-confirmed notes. Never an infinite loop, never an unverified hardware claim.
- **Runtime:** time-sliced on a **single Ollama model instance** — same model, swapped system
  prompt per role (Actor prompt → generate; Critic prompt → review). Default `qwen2.5-coder:3b`;
  CPU/GPU layer offloading via Ollama for larger models (RTX 2060 + 32 GB). Graceful if offline.

### Why this stays inside the trust boundary
The Actor may draft code, but every hardware *value* in it is post-filtered against the cited
allowlist, and every structural claim the Critic makes is checked by the deterministic Validation
Engine before the engineer sees it as confirmed. The agents propose; the engines decide.

---

## Build order & testing
1. Part 1 (data + command) — golden tests: capability map + path filtered by board capabilities.
2. Part 2 (codegen + START_HERE) — golden tests: generated `main.c` contains the board's cited
   facts + teach comments; skeleton fallback for unknown families.
3. Part 3 (LLM mentor) — tests with a fake provider: post-filter strips an invented clock.
4. Part 4 (Actor-Critic) — tests: arbiter rejects an over-budget stack deterministically; loop
   terminates in ≤2 epochs; fake provider for Actor/Critic.

Feature branch `feature/mentor`, tag `v1.5.0-mentor`. No raw SQL outside repo helpers.
