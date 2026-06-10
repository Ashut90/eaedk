#!/usr/bin/env bash
#
# EAEDK end-to-end demo — STM32MP157 DDR bring-up triage.
#
# The story: a vague U-Boot hang (no smoking gun) is triaged to a SPECIFIC unverified DDR
# timing assumption — with zero manual correlation — then surfaced into the project as a
# tracked risk and finally resolved once the engineer verifies it. The LLM never asserts a
# hardware fact; every number comes from the verified database.
#
# Record it:   asciinema rec eaedk-demo.cast -c "DEMO_PAUSE=2 ./demo.sh"
# Requires:    python3, PyYAML, and (for the triage step) `ollama pull qwen2.5-coder:3b`.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
export PYTHONPATH="$ROOT/core"
export EAEDK_DB="${EAEDK_DB:-/tmp/eaedk-demo.db}"
export EAEDK_LLM_MODEL="${EAEDK_LLM_MODEL:-qwen2.5-coder:3b}"
PAUSE="${DEMO_PAUSE:-0}"

eaedk() { python3 -m eaedk.cli "$@"; }
say()  { printf '\n\033[1;36m# %s\033[0m\n' "$*"; sleep "$PAUSE"; }
run()  { printf '\033[0;32m$ eaedk %s\033[0m\n' "$*"; eaedk "$@"; sleep "$PAUSE"; }

rm -f "$EAEDK_DB" "$EAEDK_DB"-wal "$EAEDK_DB"-shm 2>/dev/null || true

say "1) Initialize the local truth database — offline, one SQLite file."
run db init
run db seed

say "2) Start a U-Boot bring-up project on the STM32MP157 (guided)."
printf '%s\n' "mp157-ddr" "STM32MP157" "3" | eaedk project init   # goal 3 = U-Boot bring-up
sleep "$PAUSE"

say "   ^ Note the BLOCKERS banner: DDR_TIMING_VERIFIED is UNKNOWN — flagged at minute zero."
run input set mp157-ddr console_uart UART4 --confidence HIGH

say "3) The board hangs at boot. The log is vague — no smoking gun:"
cat > /tmp/mp157-hang.log <<'LOG'
U-Boot SPL 2023.04
Trying to boot from MMC1
hang: system stalled during early relocation, no console output after handoff
LOG
cat /tmp/mp157-hang.log
sleep "$PAUSE"

say "4) Analyze the log — project-aware + LLM triage. The signature DB does NOT match it,"
say "   so EAEDK correlates the hang against the project's own unverified gaps."
run --llm log analyze --file /tmp/mp157-hang.log --project mp157-ddr --project-aware

say "   ^ A vague hang was triaged to the SPECIFIC unverified DDR timing assumption, and"
say "     written back: a tracked HIGH risk + a note on the ddr_init checklist item."
run risk show mp157-ddr

say "5) The engineer verifies DDR timing against the TRM and records it (cited)."
run input set mp157-ddr ddr_timing_verified 1 --confidence HIGH --cite "RM0436 DDR chapter"
run validate mp157-ddr --rule DDR_TIMING_VERIFIED

say "6) Now close the tracked risk — provenance and timestamp recorded."
RID="$(python3 - <<'PY'
import os
from eaedk.store.db import connect
row = connect(os.environ["EAEDK_DB"]).execute(
    "SELECT id FROM risks WHERE status='tracked' ORDER BY id DESC LIMIT 1").fetchone()
print(row["id"] if row else "")
PY
)"
run risk resolve "$RID" --note "verified CL/tRCD/tRP against RM0436 DDR chapter"

say "7) Final project state — the surfaced risk now shows as Resolved, with provenance."
run project show mp157-ddr

say "Done. The deterministic engines carried the truth; the LLM only explained, only over"
say "verified data. Any hardware number the model couldn't cite would have been stripped."
