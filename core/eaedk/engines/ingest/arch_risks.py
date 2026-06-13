"""Architecture-family bring-up risks (v2.3.0) — deterministic, read from seed YAML at runtime.

No new table/schema: the curated risks live in packages/knowledge-seed/arch_risks.yaml and are
loaded (cached) on first use. A risk is selected when the board's arch matches and its
``requires_fact`` (if any) is not yet confirmed. The LLM is never involved.
"""
from __future__ import annotations

import functools

import yaml

from ...paths import seed_dir

_SEVERITY_RANK = {"HIGH": 3, "MEDIUM": 2, "LOW": 1}


@functools.lru_cache(maxsize=1)
def _all_arch_risks() -> list[dict]:
    path = seed_dir() / "arch_risks.yaml"
    if not path.exists():
        return []
    return yaml.safe_load(path.read_text(encoding="utf-8")) or []


def risks_for_arch(arch: str | None, confirmed_keys: set[str] | None = None) -> list[dict]:
    """Return the arch-family risks that apply to this board, highest severity first.

    ``confirmed_keys`` are the fact keys already extracted/confirmed for the board; a risk whose
    ``requires_fact`` is in that set is suppressed (the data that would resolve it is present).
    """
    a = (arch or "").lower()
    confirmed = confirmed_keys or set()
    out = []
    for r in _all_arch_risks():
        if r["match"] in a and r.get("requires_fact") not in confirmed:
            out.append({"title": r["title"], "severity": r["severity"],
                        "explanation": r["explanation"]})
    out.sort(key=lambda r: _SEVERITY_RANK.get(r["severity"], 0), reverse=True)
    return out
