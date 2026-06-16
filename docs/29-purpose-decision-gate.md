# 29 — Purpose Decision gate (coarse MATCH)

Before any answer is generated, `decide_purpose()` (in `mentor_llm.py`) chooses **what the turn is
for**, so the mentor is not an answer engine:

- `ANSWER_NOW` — grounded question with a resolvable intent. **Never the default.**
- `ASK_CLARIFICATION` — not enough context (a vague ask; a fault report with no evidence).
- `REDIRECT_TO_FOUNDATION` — a field-entry / career question → a learning path, not a board answer.
- `DECLINE_OUT_OF_SCOPE` — the named subject is outside EAEDK's grounded knowledge (e.g. "Nvidia
  Jetson"); honest decline, never a board default.

The user's question is the subject; the selected board is only context. Grounding reuses the existing
detectors (topic / concept / domain / capability + concept vocabulary / semantic-cost intents /
learning-path direction) — a decision layer over what the system already knows, **not** a keyword or
domain list. An out-of-scope *named subject* outranks a weak "where to start" direction phrase.

This is the **coarse** match. The **fine**, stateful match is the problem-pattern engine — see
[30-problem-patterns.md](30-problem-patterns.md).
