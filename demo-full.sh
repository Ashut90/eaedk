#!/usr/bin/env bash
#
# EAEDK full-chain demo — the complete story in one run, on an STM32F103.
#   board add -> ingest datasheet -> project init -> toolchain -> validate -> export
#   -> feed a HardFault log -> deterministic signature match WITH teach.
#
# Record it:  asciinema rec eaedk-full.cast -c "DEMO_PAUSE=3 ./demo-full.sh"
# Needs: python3 + PyYAML; PyMuPDF for the ingest step (pip install pymupdf); arm gcc optional.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
export PYTHONPATH="$ROOT/core"
export EAEDK_DB="${EAEDK_DB:-/tmp/eaedk-full.db}"
PAUSE="${DEMO_PAUSE:-0}"

eaedk() { python3 -m eaedk.cli "$@"; }
say()  { printf '\n\033[1;36m# %s\033[0m\n' "$*"; sleep "$PAUSE"; }
run()  { printf '\033[0;32m$ eaedk %s\033[0m\n' "$*"; eaedk "$@"; sleep "$PAUSE"; }

rm -f "$EAEDK_DB" "$EAEDK_DB"-wal "$EAEDK_DB"-shm 2>/dev/null || true

say "EAEDK: a local-first tool where the LLM can never assert a hardware fact."
say "1) Initialize the offline truth database."
run db init
run db seed

say "2) Onboard a real board (STM32F103 Blue Pill) — guided, with live validation."
printf '%s\n' \
  "STM32F103-BluePill" "ST" "STM32F103C8" "2" \
  "64KB" "0x08000000" "20KB" "0x20000000" "HIGH" \
  "" "" "" "" "" "" "" "" "n" \
| eaedk board add --interactive | tail -8
sleep "$PAUSE"

say "3) Back the board with its datasheet — extract CITED fact candidates from a PDF."
python3 - <<'PY'
import fitz
lines = ["STM32F103 Example Datasheet  DS0000", "",
         "3.2 Memory mapping",
         "Flash memory   0x08000000   0x0800FFFF",
         "The device embeds 64 Kbytes of embedded Flash memory and 20 Kbytes of SRAM.", "",
         "4.1 Clock tree",
         "The system clock (SYSCLK) can run up to 72 MHz."]
doc = fitz.open(); p = doc.new_page(); y = 72
for ln in lines:
    p.insert_text((72, y), ln); y += 16
doc.save("/tmp/f103-ds.pdf"); doc.close()
PY
run ingest --file /tmp/f103-ds.pdf --board STM32F103-BluePill
say "   Review the candidates — each cited to page + section + snippet (nothing auto-written):"
run ingest --board STM32F103-BluePill --review
CID="$(python3 - <<PY
from eaedk.store.db import connect; import os
print(connect(os.environ["EAEDK_DB"]).execute(
  "select id from fact_candidates where fact_key='flash_base'").fetchone()["id"])
PY
)"
say "   Confirm flash_base — the only path that writes to the knowledge base:"
run ingest --confirm "$CID"

say "4) Start the beginner's first project (blink/UART) — auto-selects the template, assesses now."
printf '%s\n' "blink" "STM32F103-BluePill" "1" | eaedk project init
sleep "$PAUSE"

say "5) Check the BUILD ENVIRONMENT — most bring-up failures are environment failures."
run toolchain detect
run toolchain validate --project blink

say "6) Fill the project inputs an engineer knows, then validate deterministically."
run input set blink vector_table_addr 0x08000000 --confidence HIGH
run input set blink console_uart USART1 --confidence HIGH
run input set blink estimated_image_size 8192 --confidence HIGH
run input set blink stack_size 4096
run input set blink heap_size 2048
run input set blink static_size 2048
run validate blink

say "7) Export real build artifacts — only when feasible. (memory.ld below built real firmware.)"
rm -rf /tmp/blink-out
run export blink --out /tmp/blink-out
sed -n '4,7p' /tmp/blink-out/linker/memory.ld

say "8) The board boots, then crashes. Feed the log — a real Cortex-M HardFault:"
cat > /tmp/f103-hardfault.log <<'EOF'
EAEDK boot: STM32F103 alive
app: starting UART logger
[FAULT] HardFault_Handler: forced exception, halting
HFSR=0x40000000 CFSR=0x00000082 stacked PC=0x08000412 LR=0xFFFFFFF9
EOF
cat /tmp/f103-hardfault.log
sleep "$PAUSE"
run log analyze --file /tmp/f103-hardfault.log --project blink

say "That's the whole loop: datasheet -> cited facts -> validated project -> real build files,"
say "and when it crashed, EAEDK didn't guess — it matched the fault and taught what to check."
