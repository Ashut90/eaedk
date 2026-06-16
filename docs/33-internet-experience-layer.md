# 33 — The Internet Experience Layer (Community Case Miner)

**Status: design only — NOT implemented. Do not build until the Navigator is stable.** This document
exists so the architecture does not drift into either failure mode while the web layer is unbuilt.

## The correction this captures

EAEDK is **not** an offline-only toy, and it is **not** a RAG chatbot. The web/community layer is part
of the core vision — but only as an **experience-mining layer**, never as retrieval-and-summary.

Two failure modes this layer must avoid:

1. **Offline-only toy.** Pretending the world's engineering experience doesn't exist. A real captain
   draws on what other engineers have already hit.
2. **RAG chatbot.** `search → retrieve chunks → summarize an answer`. This is explicitly forbidden. It
   makes the model the source of truth and dumps pages instead of giving direction.

## The role: CONFIRM and COMPARE, not "answer"

EAEDK's loop is `MATCH → SORT → CONFIRM → ORGANIZE → COMPARE → HELP`. The Navigator
([32](32-navigator.md)) already does MATCH/SORT/ORGANIZE/HELP from local patterns. The web layer feeds
the **CONFIRM** and **COMPARE** stages with *field experience*:

- **CONFIRM** — "engineers who hit this symptom found it was usually X; the proof that confirms or
  rejects X is Y." Community cases supply candidate causes and the *verification step*, never a verdict.
- **COMPARE** — line the user's current problem/pattern up against what others have actually seen and
  confirmed, and weight the proof path accordingly.

The final answer still says **what direction to follow and what proof step to run** — never a dump of
links or a summary of pages.

## What it extracts: the `CommunityCase` (structured, not chunks)

The miner turns messy forum/issue/list text into structured engineering cases. This is the anti-RAG
core: we extract *signals*, not paragraphs.

```
CommunityCase:
  source            e.g. "EE Stack Exchange", a forum/repo name
  source_type       stackexchange | stackoverflow | vendor_forum | github_issue |
                    mailing_list | rtos_forum | official_doc
  symptom           the observable, normalised toward our EvidenceVar vocabulary
  context           board/MCU/peripheral/toolchain it occurred on
  suspected_cause   what was hypothesised
  confirmed_cause   the cause actually proven (may be empty)
  verification_step the proof that confirmed/rejected it  <-- the most valuable field
  fix               what resolved it
  evidence_quality  did someone PROVE it, or just speculate?
  confidence        derived score (source quality × confirmation × corroboration)
  reference_link    provenance, for the human — not pasted into the answer
```

A `CommunityCase` is **unverified field experience**, not truth. It can only *suggest* a candidate
cause or a verification step that the user then PROVES through the local proof path. It never becomes a
"verified board fact," and the verifier ([31](31-llm-voiced-proof-path.md)) still blocks any
board-specific claim it would inject that isn't grounded.

## The flow (long-term)

```
user message
  → Navigator: local Pattern/Map MATCH                (docs 30/32 — always first, offline)
  → decide whether external experience is NEEDED      (gate: only when local is thin/uncertain)
  → Web Experience Miner: fetch candidate sources
  → Actor (LLM): extract structured CommunityCases from messy text
  → Critic (LLM): challenge weak/speculative cases — "was this PROVEN or guessed?"
  → Kernel: organise + SCORE cases, map them onto the current pattern's decision tree
  → Mentor: voice ONE direction + the next proof step, grounded and verified
```

Key boundaries:

- **Local first, web only when needed.** The Navigator answers from local patterns/maps by default.
  The web is consulted only when local knowledge is thin or a CONFIRM/COMPARE would genuinely help —
  never as the default path. EAEDK stays fully useful offline.
- **The kernel decides, not the model.** The Actor proposes cases, the Critic challenges them, but the
  deterministic kernel scores confidence and decides what (if anything) reaches the user.
- **Field experience, not final truth.** A high-confidence case raises a candidate cause and a proof
  step; it never asserts a hardware fact. The user still proves it on their bench.
- **Direction, not links.** Output is "this matches a known pattern; others found it was usually X;
  run this proof step to confirm" — not a link list or a page summary.

## Candidate sources (when built)

Electrical Engineering Stack Exchange · Stack Overflow · vendor forums (ST/NXP/TI/Microchip) ·
GitHub issues · Linux/kernel/Yocto mailing lists · FreeRTOS/Zephyr forums · official docs/app notes.

Source quality feeds `confidence`: a confirmed fix on a vendor forum or an accepted SE answer outweighs
an unanswered post.

## Relationship to what exists today

- The `ProblemPattern` decision tree ([30](30-problem-patterns.md)) is where mined cases attach — a
  case's `symptom`/`verification_step` map onto existing `EvidenceVar`s and `DecisionNode`s.
- The `engine → LLM → verifier` discipline ([31](31-llm-voiced-proof-path.md)) extends directly: the
  Actor/Critic extract+challenge, the kernel scores, the verifier guards board claims.
- The Navigator ([32](32-navigator.md)) gains one branch: "decide whether external experience is
  needed" — it does not change how local routing works.

When this is built, it must be built as **case extraction feeding CONFIRM/COMPARE**, never as
retrieve-and-summarize. If a future change starts pasting page text into the answer, it has drifted
into RAG and is wrong.
