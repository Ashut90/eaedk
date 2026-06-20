# 35 — The "LLM-generates / deterministic-verifies" pivot

## The problem this fixes

EAEDK started as a **classifier + template-retriever**:

1. keyword-match the user's message to a bucket (proof-pattern / decision *topic* / learning map / concept);
2. render a **pre-written, curated template** for that bucket (the decision frameworks in `reasoning.py`,
   the concept anchors in `concepts.yaml`, the proof-path trees);
3. let the LLM only **"voice"** that template — never deviate.

This is excellent for *grounding* (it can't hallucinate) but it does **not understand the question**.
Ask *"what folder structure should I use for a bootloader?"* and it saw the word **bootloader**,
dropped you in the bootloader-decision bucket, and **played back the stored trade-off essay** — never
reading "folder structure." The answer felt "stored in a database," because it was.

## The inversion

The roles are flipped: **the LLM generates the answer to the specific question; the deterministic
layer grounds it and verifies it.** That is the design the project always wanted ("the LLM explains;
the deterministic layer is the arbiter") — it had just been built backwards (deterministic generates,
LLM voices).

```
User question
   ↓
Intent router decides the answer mode
   ↓
  debugging issue  → deterministic proof-path        (the template IS the best answer here — kept)
  open / general   → LLM reads the EXACT question
   ↓
System injects verified board/project facts (+ curated framework/anchor as REFERENCE, not the answer)
   ↓
LLM answers the specific question
   ↓
Deterministic verifier:
   • Did it invent board facts?      ✅ deterministic — post-filter allowlist / flag_invented_claims
   • Did it claim unsupported things?✅ deterministic — allowlist + semantic-cost
   • Did it go out of scope?         ✅ deterministic — the purpose / decline gate
   • Did it ignore safety/risk?      ✅ deterministic — feasibility arbiter + conceptual guards
   • Did it answer the question?     ⚠️  NOT deterministic — a bounded LLM relevance critic (advisory)
   ↓
Final answer
```

**Four hard guardrails + one soft self-check.** "Did it answer the question?" can't be checked against
a table — it's a judgment — so it's a bounded LLM critic (`arbiter.answer_check`), advisory only; the
four deterministic checks keep final say.

## What changed in code

- **`_ARCHITECT_TEMPLATE`** (`mentor_llm.py`): no longer forces a rigid 6-step "problem → trade-offs"
  contract onto every question. It reads the question first: **concrete** questions (folder layout, a
  specific how-to, "what is X", a direct comparison) are answered **directly**; the 6-step *Socratic*
  shape is reserved for **open "should I" / design** questions, and there it must **not** open with a
  blunt recommendation.
- **Framework as reference, not mandate**: the decision-topic reasoning is injected as *"REFERENCE you
  MAY draw on — answer the user's ACTUAL question, don't just recite it"* (was "elaborate, never
  contradict").
- **`arbiter.answer_check`** — the relevance critic. One bounded pass that rewrites an answer which
  answered a *different* question or is generic filler. It **skips open "X vs Y / should I … or …"
  decisions** (Socratic teaching is valid there, not a dodge) and **strips any leaked meta-preamble**
  ("here's a rewritten answer…") the small model sometimes emits.
- **Honest offline fallback**: with no model, EAEDK returns its grounded deterministic reference
  **labeled as such** — `[offline reference] … run with --llm for a reply tailored to your exact
  question` — instead of passing a stored template off as a custom answer. The note is placed *after*
  the head so the feasibility banner still comes first and the answer still ends on a question.

## What stays a template (on purpose)

**Debugging proof-paths.** For "my UART is dead," a deterministic decision tree that walks you from
symptom to a checkable cause genuinely *is* the best answer. Those are untouched — the LLM only voices
them, and the verifier still blocks invented board facts.

## ⚠️ Caveat — model quality is the ceiling

The architecture is correct and tested, but **answer consistency is bounded by the model**. With the
default local **`llama3.1:8b`**, the same concrete question may be answered cleanly in one run and
drift into a trade-off discussion in another; the relevance critic nudges it but a small model does not
always comply. **A stronger mentor model is recommended** for production-quality answers (set
`EAEDK_MENTOR_MODEL`, e.g. a larger local model or the cloud model you have pulled). The deterministic
guardrails hold regardless of model — only the *fluency and relevance* of the generated answer scale
with it.

## Regression guards

`core/tests/test_pivot_regression.py` locks the four guarantees (testing the *mechanism*, since the
LLM output is non-deterministic):

1. a **concrete** question (folder structure) is answered directly — the critic runs on it and the
   prompt instructs a direct answer (it must **not** become a trade-off essay);
2. **HAL vs bare metal stays Socratic** — it's classified as an open decision, so the critic skips it
   and the prompt's open branch forbids opening with a recommendation;
3. **offline does not pretend** — the offline answer carries the `[offline reference]` honesty note;
4. **proof-paths still work** — a fault still routes to the deterministic proof-path.
