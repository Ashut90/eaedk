# 25 — Role detection (v2.5.0): switch behaviour, not vocabulary

v2.4.x listed four mentor roles in one combined prompt and asked a 3B model to pick the right one.
It can't do that reliably — it collapses to one flat voice and pattern-matches. This release fixes it
structurally: **Python detects the role before the model runs, and the model receives a prompt that
already *is* that role** (its own few-shot examples), with the board's real peripherals injected
*before* the examples.

## Fix 1 — `detect_mentor_role(user_message, page_context)` (deterministic, before the LLM)

A pure function in `mentor_llm.py` returns one of `SPONSOR` / `REVERSE_MENTOR` / `PEER_MENTOR` /
`SYSTEM_ARCHITECT`, in that priority order:

- **SPONSOR** when `page_type` is `validate`/`export` — deterministic only, never the model (the
  engine's findings are explained by the existing validate/export routes; the chat just points there).
- **REVERSE_MENTOR** when `wokwi_flag` is set — the simulator-aware mentor.
- **PEER_MENTOR** when the user shared code (`current_code` > 50 chars) or is debugging
  (`"compiles but"`, `"nothing happens"`, `"what's wrong"`, …).
- **SYSTEM_ARCHITECT** for design/feasibility questions (`"should i use"`, `"which board"`,
  `"hal or"`, `"can i do"`, `"robotics"`, `"bootloader"`, …) — and as the default.

## Fix 2 — one self-contained few-shot prompt per role

Three builder functions (`build_architect_prompt`, `build_peer_mentor_prompt`,
`build_reverse_mentor_prompt`) each return a complete system prompt: the role's identity, then the
**board block** (name, arch, peripherals from SQLite, flash/RAM, project, stage), then 2–3
WRONG/RIGHT examples, then the closing instruction. The model never chooses a role — it is one.

The board block comes **before** the examples so the model grounds in real data first.

## Fix 3 — route to the prompt for the detected role

`mentor_chat` detects the role, and on the online path selects that role's system prompt; the offline
path returns the deterministic backbone (domain reasoning + a domain-specific "Try this" + a
question), unchanged in spirit from v2.4.1, so offline behaviour stays deterministic and tested.

## Fix 4 — domain-aware "Try this", chosen in Python

`DOMAIN_TRY_THIS` maps a detected project type (robotics, motor, sensor, audio, iot, bootloader,
driver, default) to a concrete first experiment, injected into the prompt and used as the backbone's
"Try this". The robotics/motor experiment is **family-gated**: it names TIM1 only on STM32, a generic
timer output elsewhere — so a non-STM32 board never gets an STM32-specific claim.

## Trust boundary (the residual risk, stated honestly)

The few-shot examples are STM32 (AF7, `RCC->APB2ENR`, `Table 9`) because that is the project's primary
family. Two guards keep this honest:

1. The **board block is injected before the examples**, and each role prompt carries a one-line rule:
   *the example registers/AF numbers are STM32 teaching values — reason for the board above, never
   assert a register/clock/timing you were not given, and tell the user to confirm specifics in the
   datasheet.*
2. The **post-filter still runs on every reply** — it strips any hex address, memory size, clock, or
   timing not in the cited allowlist. Register *names* and bare AF *numbers* pass (they are not
   sized/clock/timing values), framed as "verify in Table 9"; the dangerous category (invented
   addresses/clocks/sizes) cannot get through. This is the same trade-off accepted in v2.4.0/22, made
   explicit here.

## Tests

Golden (deterministic): role detection per the four trigger sets; the domain "Try this" matches the
detected project type; role→prompt selection; the board block precedes the examples in each prompt.
The four behavioural acceptance checks (output quality for robotics / debugging / Wokwi / HAL-vs-bare
questions) are verified manually against the live model — CI runs offline. Full suite must stay green;
eval 14/14. Additive only; deterministic core untouched.
