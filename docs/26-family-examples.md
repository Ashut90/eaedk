# 26 — Per-family Peer examples + role-detection reorder (v2.5.1)

Two follow-ups to the v2.5.0 role detection (docs/25).

## Fix 1 — per-family few-shot examples in the Peer prompt

The PEER_MENTOR prompt had STM32-only examples, so on a non-STM32 board the model could reach for
STM32 register names. The Peer prompt is now assembled as head + the **example block for the board's
chip family** + tail, selected from `family_of()`:

| Family | The #1 "compiles but nothing happens" trap the example teaches |
|---|---|
| `stm32` | clock-enable, alternate-function (AF7 vs AF1), baud divisor |
| `avr` | **F_CPU / fuse-bit mismatch** (wrong clock → wrong delays and baud), not peripheral clock gating |
| `esp32` | **blocking the WiFi core (Core 0) → task-watchdog reboot**, and NVS init order — not AF numbers |
| `rp2040` | **multicore races and the un-enabled PIO state machine** — not an APB2 enable (RP2040 has no APB2) |

The model sees only its own family's example before answering. Verified live: an AVR debug answer
talks F_CPU/UBRR (no APB2/AF7); an ESP32 debug answer talks Core 0 / watchdog (no AF7/APB2).

## Fix 2 — role-detection reorder

The Wokwi flag no longer overrides the reasoning role. New order:

1. **SPONSOR** — `page_type` is validate/export
2. **PEER_MENTOR** — code present or a debug trigger word
3. **SYSTEM_ARCHITECT** — a design/architecture trigger word
4. **REVERSE_MENTOR** — a *simulation-specific* trigger (`wokwi`, `simulator`, `virtual`, `blocking`,
   `boot pin`, `boot0`, `can't/cannot/won't export`) **and** `wokwi_flag`
5. **Default** — SYSTEM_ARCHITECT

So a Wokwi user asking "HAL or bare metal?" now gets the Architect, and "my code compiles but
nothing happens" gets the Peer — the Wokwi flag only informs the "Try this" (point at the simulator),
not the reasoning role. The Reverse Mentor is reserved for genuinely simulation-specific questions
("boot pin is blocking export").

## Tests

Golden: the AVR/ESP32/RP2040 Peer prompts carry their own family example (F_CPU not APB2; WiFi
watchdog/Core 0 not AF7; PIO/multicore); a Wokwi user's design question → ARCHITECT; a Wokwi user's
"boot pin blocking export" → REVERSE. Full suite 290 passing; eval 14/14. Additive; core untouched.
