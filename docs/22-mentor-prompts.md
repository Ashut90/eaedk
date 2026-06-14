# 22 — Mentor prompt rewrite (v2.4.0): reason, don't retrieve

This release rewrites the mentor LLM prompt layer so the model **teaches a person to think like a
firmware engineer** — what the problem is, why it exists on real hardware, what must be decided
before any code, the trade-offs, and what the engineer has not considered yet. It does not hand out
stored answers with a board name pasted in. Additive only; the deterministic core is untouched.

## Where the prompts actually live (correction to the brief)

The brief lists the prompts as living in `llm/prompts.py`. They do not. The mentor prompts live in
**`mentor_llm.py`** and the Actor/Critic prompts live in **`actor_critic.py`**. `llm/prompts.py`
holds the *validate / explain / log-triage* prompts (the `ask`/`explain`/`log` CLI verbs), which are
out of scope here. This release rewrites the prompts where they live:

| Prompt | File | Role(s) |
|---|---|---|
| `_CHAT_SYSTEM` (`mentor_chat`) | `mentor_llm.py` | A (System Architect) + D (Reverse Mentor) |
| `_ASK_SYSTEM` (`mentor_ask`) | `mentor_llm.py` | A (System Architect) |
| `_EXPLAIN_SYSTEM` (`mentor_explain`) | `mentor_llm.py` | A (System Architect) |
| `_CRITIC_SYSTEM` (Critic) | `actor_critic.py` | B (Peer Mentor) + Job 5 |
| `_ACTOR_SYSTEM` (Actor) | `actor_critic.py` | Job 4 |
| review-code flow (Code Studio) | `actor_critic.py` (`run_actor_critic`) | B (Peer Mentor) |

## The hard constraint this design respects: the trust boundary

EAEDK exists so the LLM **never asserts a hardware fact the database has not verified.** The brief's
examples have the mentor assert specific values it cannot cite — e.g. "PA9 is AF7 for USART1_TX, AF1
for TIM1_CH2." SQLite does **not** hold AF-number or timer-variant data; it holds capability names
(`gpio`, `timer`, `uart`, `spi`, `i2c`, `usb`), board geometry, project state, and learning steps.

The reconciliation — which is also better teaching, and matches the brief's own examples (every one
ends "Check Table 9 — what AF did you write?"):

- The mentor reasons about the **concern**, not the constant: "an alternate-function mismatch makes
  the pin a timer output, your UART is silently dead, and the compiler says nothing — so before you
  flash, find the AF column for this pin in the datasheet's alternate-function table and confirm it
  matches the function you want."
- It **injects what SQLite verifies** (board, architecture, capabilities, flash/RAM, project,
  learning step, Wokwi-vs-hardware) and reasons concretely from that.
- For a specific number EAEDK has **not** verified, it routes the engineer to the exact datasheet
  table and **asks what value they used** — it does not state the number.
- The **post-filter stays on every mentor reply** as the structural backstop: any hex / size / clock
  / timing not in the cited allowlist is stripped regardless of what the prompt says.

So the mentor is allowed to be specific about *which concern applies to this board* (derived from
the capability map) while never asserting an uncited value. That is the firmware-engineer habit
worth teaching: know what to check, and verify it in the source.

## The four roles (selected by what the user is doing)

- **A — System Architect.** New project, architecture/board/HAL-vs-bare-metal/design questions.
  Forces goal, scope, and trade-offs before recommending anything.
- **B — Peer Mentor.** Code submitted or a "how do I write…" question. Finds the one hardware
  consequence not yet considered — never style, never a linter.
- **C — Sponsor (Validation Gate).** `validate`/`export`. Already built; unchanged. The model
  explains what the engine found and never overrides a CONFIRMED finding.
- **D — Reverse Mentor.** Wokwi simulation path. Downgrades physical-only concerns (boot pins,
  factory ROM) to advisory; never blocks a beginner over a constraint the simulator does not model.

## The five jobs (every prompt, every page)

1. **Build thinking before suggesting.** Start with a consequence or a question, never a definition.
2. **Connect board hardware to code decisions.** Read the capability map first; reason from the
   board's actual peripherals (it has `timer`/`uart`/`spi`/`i2c` or it does not), and route exact
   variant/AF questions to the datasheet.
3. **Explain why every choice, not the alternatives.** Why this, what breaks if you pick the other.
4. **Teach to code, not generate code.** The sequence: why must this exist → the four goal questions
   (what / why / how / when-done) → trade-offs → code with every non-obvious line explained → what
   could go wrong on *this* board. The four goal questions are **asked**, not answered for the user.
5. **Reality check.** Name the one hardware consequence not yet considered.

## Conversational rules (enforced in the prompts)

Never open with a definition; never give code without what-to-think-about-first; never recommend
without why-not-the-alternatives; never end without a question or concrete action; one "Try this"
per reply tied to the visible board/project; on a vague question ask one clarifying question first;
never "it depends" without naming what it depends on; never filler ("great question", "certainly").

## Context injected into every mentor prompt (from SQLite only)

board name, architecture, capability list (with the timer/uart/spi/i2c presence the board actually
has), flash size, RAM size, current project, current learning step, and the Wokwi-vs-physical flag.
The deterministic backbone (curated learning path / concept anchor / State-Engine progress) is still
computed and still shown when the model is off — the rewrite changes how the model *reasons over*
this context, not what is trusted.

## Tests (Part 1)

Golden tests assert prompt *shape*, deterministically, with no live model:
- Job 1 — the system prompts instruct "open with a consequence or question, never a definition."
- Job 2 — the chat context injects the board's capability map (peripheral presence).
- Job 3 — the Actor prompt carries the four goal questions before any code.
- Job 4 — the Critic prompt checks hardware consequences, not style.
- Job 5 — the Wokwi flag is injected and the Reverse-Mentor (Role D) instruction is present.

All existing mentor/actor-critic tests must continue to pass unchanged.
