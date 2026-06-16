# 30 — Problem-pattern engine (fine MATCH + proof path)

EAEDK is a **pattern navigator**, not an answer box. A beginner is lost not because they lack
information but because they lack experience: *what kind of problem is this, what do I check first,
what is noise, what result proves I'm moving the right way.* This engine turns confusion into a known
**problem pattern** and walks the learner down a **proof path**, one verified step at a time.

`problem_patterns.py` is a generic engine over curated data. UART bring-up is the **first** seeded
pattern; SPI / I2C / HardFault-after-bootloader / DMA / Linux-probe / Yocto-boot are meant to slot in
as further `ProblemPattern` instances with no engine change.

## The objects

- **`ProblemPattern`** — curated: `match_groups`, `zones`, `required_evidence`, `beginner_traps`,
  an `entry` node, the `nodes` (decision tree), and `evidence_vars`.
- **`EvidenceVar`** — a normalised observation the tree branches on. Its `values` map a normalised
  value to the messy phrases that mean it, so *"TX is silent" / "no waveform on TX" / "pin is not
  toggling" / "logic analyzer shows nothing"* all collapse to `tx_activity = absent`.
- **`DecisionNode`** — `proof_step`, `why`, what it `rules_out`, the remaining `candidates`, the
  evidence it `expects`, and `branches` (normalised value → next node id).
- **`ProofPathState`** — where the learner is in the tree, rebuilt by `resolve(messages)` from the
  whole transcript each turn (minimal session state — no new storage, survives restarts).

## The loop (per turn)

```
user message
  → Purpose gate          (coarse MATCH — docs/29)
  → match_pattern         (fine MATCH: which problem family)
  → resolve(messages)     (replay transcript → ProofPathState)
      · extract_evidence  (messy reply → normalised EvidenceEvent)
      · advance DecisionNode on the normalised value, never the phrase
  → render_proof_path     (the mentor explains the current node + next proof step)
```

The pattern engine is **conversation-aware**, so it takes precedence over the Purpose gate, which only
sees the latest message in isolation — mid proof-path a bare *"TX is silent"* looks out-of-scope to the
gate but is exactly the branch signal here.

## Why no hallucination

The proof path is **deterministic and board-agnostic**: it never asserts a pin, register, or clock —
it *asks* for them in `required_evidence`. There is nothing to invent. When an LLM later voices a node
(the `engine-facts → mentor → verifier` pattern proven in `demo/board_mentor_demo.py`), the verifier
guards board-specific claims; today the render is its own guarantee.

## Not in scope yet

No web case extractor, no RAG, no Actor-Critic over patterns, no LLM-authored patterns, no second
pattern. Patterns are curated by hand — that is the moat. Add the next one to the `PATTERNS` registry.
