# 32 — The Navigator (central confusion-type router)

EAEDK is an **embedded-engineering pattern navigator**, not an answer box. Its job is to take a
confused learner's messy question and turn it into *direction*: which kind of engineering problem this
is, what's known, what's missing, and the first proof step. That mission is `MATCH → SORT → CONFIRM →
ORGANIZE → COMPARE → HELP`.

`navigator.py` is the one brain that classifies the **confusion type** and routes to the matching kind
of guidance. `mentor_chat` is now a dispatcher over `navigator.classify()`, not a pile of `if/elif`.

```
user message → Purpose gate (coarse) → navigator.classify() → Route(mode, payload)
```

| Mode | Confusion type | Backing data | Output style |
|---|---|---|---|
| `PROOF_PATH` | broken system / bring-up / debug | `ProblemPattern` ([30](30-problem-patterns.md)) | pattern → evidence → first proof step → branch |
| `DECISION_MAP` | engineering choice / trade-off | `reasoning.Topic` (the decision library) | options → trade-offs → criteria → recommendation → next |
| `LEARNING_MAP` | broad learning-direction confusion | `LearningMap` (new, in `navigator.py`) | map the field → meanings → route → first learning step |
| `CLARIFY` | intent too vague | the Purpose gate | one real question |
| `DECLINE` | cannot be grounded/verified | the Purpose gate | honest limitation |
| `TEACH` | a grounded concept to explain | concept/domain/path pipeline | explain, grounded |

**Precedence:** a live proof path wins (it's conversation-aware and replays the transcript); then a
seeded decision topic; then a learning area; then the Purpose gate's foundation/decline/clarify; else
teach. `LearningMap` matching is the same AND-of-ORs the patterns use.

**Generality (the point):** adding a future topic is **data**, not router code — a `ProblemPattern`, a
`reasoning.Topic`, or a `LearningMap` in its registry. `mentor_chat` does not change. The kernel owns
classification, selection, evidence state, proof steps and verification; the LLM only voices and never
picks the route or invents board facts.

**Seeded so far:** UART bring-up (`PROOF_PATH`); the `reasoning` decision library (`DECISION_MAP`); two
`LearningMap`s — `embedded_linux` (kernel/driver) and `build_systems` (Yocto/Buildroot). A learning map
is grounded too: asked about kernel/Yocto on a microcontroller, it states the board cannot run Linux.

**Honest current limits:** `LearningMap` rendering is deterministic (LLM-voicing + verifier, as in
[31](31-llm-voiced-proof-path.md), is the next layer). `DECISION_MAP` reuses `reasoning.Topic` rather
than a separate `DecisionMap` type, so an un-seeded pair (e.g. "RTOS or Linux") classifies correctly
but may render the nearest seeded topic (RTOS vs super-loop) — a content gap, not a routing one. No web
extractor, no RAG.
