# 27 — The engineering-reasoning framework (v2.6.0): mentor, not answer engine

EAEDK's mentor was operating as an Answer Engine — question in, answer out. It must operate as an
Engineering Mentor: teach the *thinking*, so the learner can solve future problems independently.
This release re-centres the design/teaching path on a board-agnostic reasoning framework.

## The response contract (the design path)

A design / concept / feasibility question no longer maps to an answer. It maps to:

```
What problem is being solved?  ->  Why does it exist?  ->  When does it apply?
->  What approaches exist?  ->  What are the trade-offs?  ->  How does an engineer decide?
->  (board facts enrich here)  ->  Next step to explore.
```

The literal recommendation is the small last step. The mentor **never opens with implementation**
(code, registers, APIs, SDKs, board-specific detail) — it establishes the problem and the options
first.

## Domain-driven, not board-driven

The reasoning is authored board-agnostically and is valid on STM32 / RP2040 / ESP32 / AVR / Linux
SBCs. Board facts (RAM, timers, cores, peripherals) **enrich** a trade-off — they do not drive the
answer. The educational value survives a change of board.

## The deterministic reasoning library (`reasoning.py`)

A curated, offline library — `REASONING_TOPICS`, one entry per engineering decision — each carrying
the framework axes:

| Field | The framework axis |
|---|---|
| `what` | What is it? |
| `why` | Why does it exist? (the real problem) |
| `when` | When should it be used? |
| `alternatives` | What other options exist? |
| `tradeoffs` | The costs and benefits of each |
| `how_to_think` | The questions an engineer asks before deciding |
| `next_step` | What to explore next |
| `enrich(board)` | optional: how *this* board's facts sharpen the trade-off |

Starter topics: HAL vs bare-metal, polling vs interrupt vs DMA, RTOS vs super-loop, fail-safe
bootloader / updates, board selection, watchdog strategy, and the "nothing happens" diagnostic
*method*. `detect_topic(text)` maps a question to a topic; `render(topic, board_ctx)` produces the
framework as readable prose, board-enriched.

Because the library is pure Python data + a renderer, the framework holds **fully offline** — an
air-gapped mentor still teaches the reasoning, not just a stored answer.

## Integration (Architect path only)

- **Offline backbone:** a detected design topic renders the framework deterministically (problem →
  why → options → trade-offs → how to decide → next), board-enriched. This is what the user sees
  with no model running.
- **Online:** the Architect system prompt is rewritten to *be* the framework — its rule is
  problem-before-implementation, its examples demonstrate WHAT→WHY→ALTERNATIVES→TRADEOFFS→HOW→NEXT
  board-agnostically, and the rendered topic reasoning is injected as grounding so the model
  elaborates the framework rather than inventing an answer.
- **Other roles unchanged:** Peer still teaches the *diagnostic reasoning process* for a live bug
  (its per-family examples already ask "which of these did you check first?"); Reverse keeps
  simulator scope; Sponsor defers to the Validation Engine. The board-specific domain reasoning
  (robotics/sensor/…) from v2.4.1/v2.5.1 is preserved.

## Success criterion (and the test for it)

After a design exchange the user should be able to explain the problem, the options, the trade-offs,
and the decision process — not repeat an answer. Golden tests assert the rendered framework contains
the axes (options, trade-offs, how-to-decide, next), is board-agnostic (same core text across
boards, only the enrichment line differs), and that the design backbone leads with the problem, not
an implementation detail. Full suite stays green; eval 14/14; deterministic validation core untouched.
