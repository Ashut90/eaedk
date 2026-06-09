"""Constrained prompts for the explanation layer.

The system prompt encodes EAEDK identity and the hard rule: explain validated results,
never assert an uncited hardware fact. The user prompt carries ONLY the deterministic,
already-cited context plus the engineer's question. The post-filter is the real enforcement;
the prompt is belt-and-suspenders.
"""
from __future__ import annotations

from ..schemas.response import AssessResponse

SYSTEM = """You are EAEDK's explanation layer for embedded bring-up.
You explain results that the deterministic engines have already computed. Hard rules:
1. Explain the reasoning, trade-offs, and the single best next step, in concise prose.
2. NEVER state a hardware fact — a memory address, a memory/flash/RAM size, a clock
   frequency, a timing value, or a register value — unless it appears verbatim in the
   CONTEXT below. If you need a value you were not given, say it is unknown and tell the
   engineer to verify it against the datasheet/TRM.
3. Do not invent register addresses, DDR timings, or clock values.
4. If the deterministic layer marked something UNKNOWN, surface it as UNKNOWN — do not fill
   the gap.
Be brief. Reason first, build second, verify always."""


def _context_block(resp: AssessResponse) -> str:
    lines = [f"FEASIBILITY: {resp.feasibility}"]
    if resp.template:
        lines.append(f"TEMPLATE: {resp.template}")
    lines.append("VALIDATIONS:")
    for v in resp.validations:
        lines.append(f"  - {v['status']} {v['check']}: {v['reason']}")
    if resp.risks:
        lines.append("RISKS:")
        for r in resp.risks:
            lines.append(f"  - [{r['severity']}] {r['rule_key']}: {r['explanation']}")
    if resp.facts:
        lines.append("CONFIRMED FACTS (the only hardware values you may cite):")
        for f in resp.facts:
            lines.append(f"  - {f['key']} = {f['value']} [{f.get('confidence')}]")
    if resp.assumptions:
        lines.append("ASSUMPTIONS (lower confidence):")
        for a in resp.assumptions:
            lines.append(f"  - {a['key']} = {a['value']} [{a.get('confidence')}]")
    if resp.unknowns:
        lines.append("UNKNOWNS (must stay unknown):")
        for u in resp.unknowns:
            lines.append(f"  - {u}")
    return "\n".join(lines)


def build_ask_prompt(resp: AssessResponse, question: str | None) -> str:
    q = question or "Explain the current assessment and the best next step."
    return f"CONTEXT:\n{_context_block(resp)}\n\nQUESTION: {q}\n\nAnswer:"


def build_explain_prompt(resp: AssessResponse, rule_key: str) -> str:
    target = next((v for v in resp.validations if v["check"] == rule_key), None)
    detail = f"{target['status']} — {target['reason']}" if target else "rule not applicable"
    return (f"CONTEXT:\n{_context_block(resp)}\n\n"
            f"Explain validation rule {rule_key} ({detail}) in plain terms for the engineer, "
            f"including why it matters and what to do next. Answer:")
