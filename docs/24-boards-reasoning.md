# 24 — Boards chat: project-type reasoning (v2.4.1)

A dogfood found the Boards chat still *retrieving* on application questions: "can I do robotics?" on
a Nucleo-F411RE returned a generic capability list and "blink an LED" — zero connection to what
robotics needs. This release makes the mentor reason about **which of the board's specific
peripherals a named project type requires, and why** — never a generic peripheral dump.

## The change (two places, nothing else)

- **`mentor_chat` (mentor_llm.py).** A project-type detector (`robotics` / `sensor` / `motor` /
  `audio` / `iot`) plus a curated, **family-aware** reasoning block that is gated on the board's
  actual capabilities and chip family. For a `robotics` question on an STM32 board with a timer, it
  reasons: motor speed is PWM from a TIMER; the advanced-control timer (TIM1) has **complementary
  outputs + dead-time** to drive both sides of an **H-bridge** (a basic timer cannot); a timer in
  encoder mode gives odometry; an IMU over I2C/SPI gives orientation; and a motor must go through a
  driver, never a GPIO pin. On a non-STM32 board the same question gets the motor-driver/H-bridge
  safety reasoning without the STM32-specific TIM1 claim. This block becomes the deterministic
  backbone head (so it holds even offline) and is injected into the online prompt to build on. The
  system prompt gains one rule: name a project type → reason about the specific peripherals it needs.
- **Boards-page context injection (web/server.py).** The `boards` branch now tells the mentor to map
  a named project type to THIS board's specific peripherals rather than list them all.

## Trust boundary

The peripheral reasoning is curated, family-gated guidance — the same class as the existing
think-before-code hints (e.g. "STM32 peripherals are OFF at reset → enable the clock"). It states
architectural facts that are true by construction for the gated family (STM32 advanced-control
timers have complementary outputs) and tells the engineer to confirm the exact timer/pin in the
datasheet. The post-filter still runs on every reply.

## Golden tests

- `can I do robotics?` on a board with TIM1 (STM32 + timer) → the answer mentions **TIM1**,
  **complementary** outputs, **H-bridge**, and **encoder** — not generic GPIO.
- The same question on an AVR board → **no TIM1** claim (family-gated), but still the
  motor-driver/H-bridge safety reasoning.

Full suite 278 passing; eval 14/14. Additive only; deterministic core untouched.
