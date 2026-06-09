# EAEDK — Embedded AI Engineering Development Kit

Local-first structured engineering reasoning for embedded bring-up. The trust comes from
**deterministic engines** (validation, risk), not the LLM. See [docs/](docs/) for the
architecture review and the signed-off MVP specification.

> Reason first. Build second. Verify always.

## Status

MVP implemented and green (offline, LLM-optional). Validation Engine (18 rules), Risk
Engine, Board Knowledge (9 boards), Templates/Checklists, Project state, and the golden
eval suite all work **without any LLM**. The offline LLM gateway (Ollama
`qwen2.5-coder:3b`) is wired for `ask`/`explain` only, **off by default** (`--llm` to
enable), with an uncited-claim post-filter that strips any hardware fact not cited from
SQLite.

```bash
ollama pull qwen2.5-coder:3b          # optional; LLM features are opt-in
eaedk --llm ask <project> "..."       # deterministic answer first, then post-filtered prose
```

## Install / run (no network needed beyond PyYAML)

```bash
python3 -m pip install pyyaml          # only runtime dependency
export PYTHONPATH=core                 # or: pip install -e .

python3 -m eaedk.cli db init           # apply migrations
python3 -m eaedk.cli db seed           # load 5 templates, 7 boards, risk rules, eval cases
python3 -m eaedk.cli eval run          # -> PASSED 6/6
```

## Example session

```bash
eaedk board add --interactive          # guided onboarding with live fitment + VTOR checks
eaedk board show WIZnet-W5500-EVB-Pico
eaedk project new picoboot --board WIZnet-W5500-EVB-Pico --goal bootloader
eaedk input set picoboot estimated_image_size 262144 --confidence HIGH
eaedk input set picoboot vector_table_addr 0x10000100 --confidence HIGH
eaedk input set picoboot bl_region '{"base":268435456,"size":4096}' --confidence HIGH
eaedk input set picoboot app_region '{"base":268439552,"size":2093056}' --confidence HIGH
eaedk validate picoboot                # feasibility + cited validation table + facts/assumptions/unknowns
eaedk risk picoboot
```

A checklist item cannot be marked `done` while its linked validation rule is `FAIL` or an
engaged `UNKNOWN` — the trust core is enforced structurally, not by convention.

## Goal types

`bootloader` · `uboot` · `linux` · `ota` · `driver` (the five system-prompt templates,
encoded as versioned YAML in `packages/templates/`).

## Layout

```
core/eaedk/          # the brain (Python package)
  store/             # SQLite + forward-only migrations
  context.py         # resolves the evaluation context (board flatten, coercion, goal defaults)
  engines/
    validation/      # 16 pure-function rules — PASS/FAIL/UNKNOWN (the trust core)
    risk/            # data-driven rules over a sandboxed mini-DSL (no eval())
  orchestrator/      # deterministic-first assembly of the fixed response schema
  schemas/           # AssessResponse (Facts / Assumptions / Unknowns)
  seed.py            # loads packages/ YAML into the DB
  eval_runner.py     # golden eval harness
  cli.py             # argparse command surface
packages/
  templates/         # 5 versioned templates
  knowledge-seed/    # 7 boards, risk_rules.yaml, eval_cases.yaml
core/tests/          # engine + eval unit tests
```

## Tests

```bash
PYTHONPATH=core python3 -m pytest -q
```
