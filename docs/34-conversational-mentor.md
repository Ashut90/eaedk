# 34 — Conversational mentor (engine grounds + verifies, LLM teaches)

The earlier mentor *stored an answer and voiced it* — the deterministic engine wrote the reply and the
model paraphrased a fixed packet. That reads like retrieval, not tutoring. This layer inverts the
**answer** (not the routing): the deterministic engine still classifies and grounds, but the **LLM
teaches** the explanation, conversationally and multi-turn, the way a real tutor does. Modelled on the
pdf-tutor pattern (grounding context + a teaching prompt → the model teaches → follow-ups → history).

```
Navigator classifies (deterministic)  →  engine builds a VERIFIED FACT PACKET  →
LLM TEACHES from the packet, in the context of the whole conversation  →
verifier checks (no invented board facts)  →  follow-ups offered
```

What stays deterministic (the truth boundary): the Navigator's MATCH/route, the fact packet
(`navigator.teach_packet` — curated facts only), and the verifier
(`problem_patterns.flag_invented_claims` — flags any pin/register/instance/clock/address not in the
packet). What the LLM owns: the explanation, the tone, the depth, the comparison, the next step, the
follow-up phrasing — grounded in the packet, never inventing board specifics.

Safety / graceful degradation: when the model is unavailable, errors, or states an invented hardware
fact, the answer falls back to the deterministic render — it is always grounded and never a dead end.

Multi-turn: the whole conversation is passed to the model, so "go deeper", "compare X vs Y for my
case", and "explain that again" continue the thread. Each `LearningMap` may declare `followups` —
conversational next-moves offered after a teach.

**Scope so far — one path:** `communication_systems`. Only when `use_llm` is on; offline is still the
deterministic render. Model: `llama3.1:8b` by default (`EAEDK_MENTOR_MODEL` to change) — the 3B is too
small to teach. This is the proof-of-pattern; other routes (proof-path, other maps) adopt it next, each
keeping its own verifier.

Live example (`llama3.1:8b`): a learner asks about UART/SPI/…/LoRa for a "battery outdoor sensor"; the
mentor picks the wireless layer and compares BLE/Wi-Fi/LoRa from the packet, then on the follow-up
"BLE or LoRa, it sends a reading every 10 minutes" reasons "LoRa — low power for an infrequent send"
and continues — verifier reports zero invented facts.
