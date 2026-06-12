-- Board blink facts (v2.2.0): the on-board LED pin + clock-enable hint per board, so the
-- think-before-code checklist can show the concrete board-specific answer FROM SQLite (not
-- hardcoded in logic). Seed data; cleared/reloaded by `db seed --force`.

CREATE TABLE board_blink_facts (
  id          INTEGER PRIMARY KEY,
  board_name  TEXT NOT NULL UNIQUE,
  led_pin     TEXT,          -- e.g. "PC13", "GP25", "pin 13 (PB5)"
  led_domain  TEXT,          -- e.g. "GPIOC on the APB2 bus"
  clock_hint  TEXT           -- e.g. "RCC->APB2ENR |= RCC_APB2ENR_IOPCEN"
);
