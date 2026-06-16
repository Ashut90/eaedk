# 31 — LLM-voiced proof path with verifier

Milestone 2 over the problem-pattern engine ([30](30-problem-patterns.md)): keep the engine as the
authority, but let the model give the proof path a human mentor voice — safely.

```
DecisionNode selected (deterministic)
  → build_packet()            approved, board-AGNOSTIC packet (the only thing the LLM may voice)
  → LLM voices the packet     single pass, no Actor-Critic — tone / reassurance / wording / follow-up
  → verify_voiced()           blocks invented board-specific facts
  → shown only if safe        otherwise fall back to the deterministic render (always safe)
```

The model **may** control: tone, explanation style, a short reassurance, wording, follow-up phrasing.

The model **may not** control: which pattern, which branch, which proof step, or any board-specific
fact (pins, registers, UART/USART instance, clock frequency, address, MCU setup) — those come from the
engine, and the packet states none of them, so a correct voice has nothing to invent.

`verify_voiced()` flags/blocks invented pins (`PA9`), GPIO ports (`GPIOA`), instances (`USART1`),
registers/buses (`RCC`, `APB2`, …), clock frequencies (`72MHz`), and addresses (`0x40013800`) that are
not present verbatim in the packet. On any violation the caller (`mentor_llm._voice_proof_path`) drops
the voiced answer and shows the deterministic render — so an invented fact never reaches the learner.

Offline, or when the model is unavailable or times out, the deterministic render is used directly.
This layer adds voice only; it changes none of the engineering. Reversible: the voicing call falls
back to exactly the Milestone-1 behaviour.
