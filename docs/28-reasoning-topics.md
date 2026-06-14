# 28 — Three more reasoning topics (v2.6.1)

Pure-data additions to `reasoning.py` (docs/27) — no migration, no code changes. Three of the most
common beginner failure modes, each authored in the same framework as the existing topics
(what / why / when / alternatives / trade-offs / how-to-think / next), board-enriched.

> Filename note: the brief said `docs/26-mentor-backbone.md`, but `docs/26` is already
> `26-family-examples.md` (v2.5.1) and the backbone framework itself is `27-reasoning-framework.md`.
> To avoid a duplicate `26`, these three topics are documented here as `28`.

## 1. `clock_tree` — clock tree and peripheral clocking

The #1 silent failure on STM32 and most ARM MCUs: every peripheral starts OFF (its clock is gated to
save power) and does nothing until enabled. Teaches what a clock domain is, why peripherals are
gated, that configuring before enabling writes to dead hardware with **no error**, the correct
sequence (clock enable → GPIO config → peripheral config → enable), and that a missing clock always
looks like "nothing happens". **Family-aware enrichment:** STM32 → `RCC->APBxENR/AHBxENR`; AVR → the
Power Reduction Register (PRR); ESP32 → the IDF driver enables it; RP2040 → the RESETS/CLOCKS block.

## 2. `interrupt_vs_polling` — and the hazards

Not "interrupts are faster". The real decision plus the bugs interrupts introduce that polling does
not: a variable shared with an ISR must be `volatile` (or the compiler caches a stale copy), a
multi-byte read can tear unless atomic, a long ISR can nest and overflow the stack. Teaches when
polling is fine (simple, single-task, timing loose), when interrupts are necessary (async, power
saving, real-time), and the safe pattern (ISR sets a flag, the main loop acts). Keyworded on the
*decision* and *hazard* phrasing so it doesn't collide with the broader "polling vs interrupt vs DMA"
data-movement topic — "polling or interrupts?" still routes there.

## 3. `memory_layout` — stack, heap, static, flash

A beginner has no model of where variables live. Teaches what's in flash (code, `const`) vs RAM
(stack growing down, heap growing up, static/global at fixed addresses), what a stack overflow looks
like and why it's silent (no MMU; stack and heap collide in the same RAM), why globals are dangerous
in interrupts, and how to check RAM use (linker map / stack high-water mark) before it crashes.
**RAM-aware enrichment:** Arduino 2KB → "a stack overflow is your FIRST enemy"; STM32F411 128KB →
"comfortable, but still set a stack guard and watch the high-water mark".

## Tests

Golden: each new topic is detected and renders every framework axis; the interrupt topic does not
steal the broad polling topic; the memory enrichment scales with RAM; the clock enrichment is
family-aware (RCC on STM32, PRR on AVR). Library is now 10 topics. Full suite 300 passing; eval 14/14.
