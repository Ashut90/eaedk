# 15 — The README Door (v1.9.2-readme-door)

A real first-time-user test (zero CS background, zero firmware experience, fresh `git clone`)
found six walls that block a beginner from ever reaching the mentor screen. The mentor screen
itself is good; the path *to* it and the recipe *from* it are broken. EAEDK has a beautiful
living room and no front door. This release builds the door.

**Preservation contract:** baseline is `pytest` 171 passing, `eval 14/14` — confirmed before and
after. Every change is **additive or corrective only**: no existing CLI verb, schema, seed,
validation rule, template, or test behaviour changes. The export tests all use ARM boards
(Nucleo-F411RE, RTL8722DM), so the new AVR export path touches nothing they assert.

**Standing rule adopted this release (applies to all future work):** before committing, read
every error/warning/status line and ask — *would a person with 6 months of electronics and no CS
degree know what to type next?* If not, rewrite it in plain English.

## The six walls (from the first-time-user audit)

1. **README opens with a research abstract; its first command errors.** `pip install pyyaml`
   fails on modern Ubuntu/Debian with `externally-managed-environment … may break your OS`. No
   venv guidance. A non-technical user quits at the first line.
2. **The README tells users to run `eaedk …`, but the install it gives never creates it.** The
   "Try it" block uses `export PYTHONPATH=core` + `python3 -m eaedk.cli`; the "Commands" section
   and every mentor example use `eaedk`. `command not found: eaedk`, three times.
3. **No way to discover board names.** `mentor --board <name>` needs a name; the README never
   lists one and never shows `eaedk board list` exists. Found only by guessing.
4. **`db seed` dead-ends on re-run** with `pass force=True to reseed` — and the real flag is
   `--force`, not `force=True`.
5. **The payoff recipe ends in a missing file — for the #1 beginner board.** Picking Arduino-Uno
   (AVR), export produces no `START_HERE.md` and no `src/main.c` (only 4 files); the mentor's
   copy-paste recipe ends at `cat START_HERE.md` → "No such file or directory".
6. **Export silently cross-contaminates a reused folder.** The mentor tells everyone to export to
   `~/blink-fw`; export writes its files but leaves stale ones, so a second project inherits a
   `START_HERE.md` for the wrong board with the wrong toolchain.

## Fixes

### Fix 1 — README getting-started block (CRITICAL)
Replace the entire top install block with the exact 9-line sequence: `git clone` → `cd` →
`python3 -m venv .venv` → `source .venv/bin/activate` → `pip install -e .` → `eaedk db init` →
`eaedk db seed` → `eaedk board list` → `eaedk mentor --board STM32F103-BluePill`. One plain-English
sentence above each command. No `PYTHONPATH`, no `python3 -m eaedk.cli`, no jargon. The dense
abstract moves below the quickstart so the door comes first.

### Fix 2 — `eaedk` command exists after `pip install -e .` (CRITICAL)
Root cause of wall 2 was the README's *install method*, not the entry point:
`[project.scripts] eaedk = "eaedk.cli:main"` is already correct. Fix 1 switches the README to
`pip install -e .`, which creates the `eaedk` console script. Verified on a clean throwaway venv
(`pip install -e .` → `eaedk --help` works) and pinned by a golden test that asserts the entry
point is declared.

### Fix 3 — Board discovery (HIGH)
- The board-not-found message becomes: *"Board not found. Run `eaedk board list` to see all 14
  available boards, or run `eaedk board add --interactive` to add your own."* (The existing
  near-match "did you mean…" suggestion is kept — it only helps — with the board-list line always
  shown.)
- `eaedk board list` is a step in the README quickstart (Fix 1), before `mentor`.

### Fix 4 — db seed message (MEDIUM)
`database already seeded; pass force=True to reseed` → *"Database already seeded. To reseed:
`eaedk db seed --force`"*.

### Fix 5 — AVR export (HIGH)
Arduino is the most recognizable beginner board, so option (a): a **real AVR scaffold**. A new
`render_avr_main_c` emits a working ATmega328P/2560 blink + UART program (idiomatic `<avr/io.h>`
register names — standard avr-libc, not invented addresses), and `render_avr_start_here` gives the
correct `avr-gcc` → `avr-objcopy` → `avrdude` flow with an OS-aware install line
(`sudo apt install gcc-avr avr-libc avrdude`). Export detects `arch == "avr"` and emits the AVR
scaffold + `START_HERE.md` instead of the ARM CMake files (which were wrong for AVR anyway — a
corrective change). The mentor recipe's `cat START_HERE.md` now succeeds for Arduino. Non-AVR
boards are byte-for-byte unchanged.

### Fix 6 — Export cross-contamination (MEDIUM)
On a successful export, write a hidden `.eaedk-export` marker (`project|board`). Before writing,
if the target folder already holds a marker for a *different* project/board — or EAEDK-generated
files with no marker (a legacy/foreign export) — refuse with: *"This folder already contains files
from a different project (was: <X>). Use a new folder name, or add `--force` to overwrite."*
Re-exporting the same project to the same folder is allowed (marker matches). `--force` overrides.
The marker is not counted in the written-files list, so file-set assertions are unaffected.

## Testing
New `core/tests/test_readme_door.py`: entry point declared (F2); board-not-found names
`board list` (F3); seed message names `--force` (F4); AVR export produces a real `START_HERE.md`
+ `src/main.c` with avr-gcc/avrdude content and no ARM cross-compiler (F5); a second board's
export into an occupied folder is refused and `--force` overrides (F6). README contains the exact
quickstart sequence (F1). Existing 171 tests stay green.
