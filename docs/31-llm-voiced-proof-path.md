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

`verify_voiced()` is **peripheral- and vendor-agnostic** — it blocks invented claims by CATEGORY, not
by a UART/STM32 token list:

- **peripheral instance** — any peripheral family + a number (`SPI2`, `I2C1`, `ADC1`, `TIM3`, `USART1`)
- **pin** — across conventions: `PA9` / `PB6` (STM32/AVR), `GPIO21` (ESP32), `D13` (Arduino)
- **register** — vendor underscore form (`SPI_CR1`, `I2C_CR2`), struct access (`GPIOA->ODR`), AVR ports
  (`PORTB`, `DDRB`), and common bare register/bus names (`RCC`, `MODER`, `APB2`, …)
- **clock frequency** — `72MHz`, `10MHz`, `80kHz`
- **address** — `0x40013800`

Anything matched that is not present verbatim in the packet (or a verified board fact carried there) is
a violation, and the caller (`mentor_llm._voice_proof_path`) drops the voiced answer and shows the
deterministic render — so an invented fact never reaches the learner. A pattern may declare extra
forbidden claims (a vendor API, a board-specific setup string) via `ProblemPattern.sensitive_terms`,
which `build_packet` carries into the packet; the verifier needs no per-pattern code. The next pattern
(SPI/I2C/HardFault) is protected with no verifier changes.

Offline, or when the model is unavailable or times out, the deterministic render is used directly.
This layer adds voice only; it changes none of the engineering. Reversible: the voicing call falls
back to exactly the Milestone-1 behaviour.
