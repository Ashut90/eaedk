"""The fixed response schema every engineering answer carries (spec §4.2).

Facts / Assumptions / Unknowns are structural fields, not prose — so the truth-hierarchy
contract is machine-checkable and is exactly what the eval harness compares.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any


@dataclass
class AssessResponse:
    goal_type: str
    feasibility: str                       # feasible | blocked | not_feasible
    template: str | None = None            # "name@version"
    checklist_counts: dict[str, int] = field(default_factory=dict)
    validations: list[dict[str, Any]] = field(default_factory=list)
    risks: list[dict[str, Any]] = field(default_factory=list)
    facts: list[dict[str, Any]] = field(default_factory=list)
    assumptions: list[dict[str, Any]] = field(default_factory=list)
    unknowns: list[str] = field(default_factory=list)
    next_step: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_markdown(self) -> str:
        L: list[str] = []
        verdict = {"feasible": "FEASIBLE", "blocked": "BLOCKED (unknowns present)",
                   "not_feasible": "NOT FEASIBLE",
                   "no_geometry": "INCOMPLETE — board has no flash/RAM geometry; "
                                  "complete board facts before relying on this"}.get(
                       self.feasibility, self.feasibility)
        L.append("## Feasibility Assessment")
        L.append(verdict)
        L.append("")
        L.append("## Risk Summary")
        if self.risks:
            for r in self.risks:
                m = f" — mitigate: {r['mitigation']}" if r.get("mitigation") else ""
                L.append(f"- [{r['severity']}] {r['rule_key']}: {r['explanation']}{m}")
        else:
            L.append("- none identified")
        L.append("")
        if self.template:
            L.append("## Template Applied")
            counts = ", ".join(f"{k}={v}" for k, v in self.checklist_counts.items())
            L.append(f"{self.template}" + (f" ({counts})" if counts else ""))
            L.append("")
        L.append("## Validation Results")
        for v in self.validations:
            L.append(f"- {v['status']:7s} {v['check']}: {v['reason']}")
            if v.get("teach") and v["status"] != "PASS":
                L.append(f"          ↳ why: {v['teach']}")
        L.append("")
        L.append("## Facts Confirmed")
        L.extend([f"- {f['key']} = {f['value']}  [{f.get('confidence','HIGH')}]"
                  for f in self.facts] or ["- (none)"])
        L.append("")
        L.append("## Assumptions Made")
        L.extend([f"- {a['key']} = {a['value']}  [{a.get('confidence','LOW')}]"
                  for a in self.assumptions] or ["- (none)"])
        L.append("")
        L.append("## Missing Information")
        L.extend([f"- {u}" for u in self.unknowns] or ["- (none)"])
        L.append("")
        L.append("## Recommended Next Step")
        L.append(self.next_step or "Proceed.")
        return "\n".join(L)
