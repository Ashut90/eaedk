"""Web hook (Step 5) — a live field-experience source for the CONFIRM/COMPARE slot.

OPT-IN, OFF by default (``EAEDK_WEB_HOOK=1``). EAEDK is local-first: with the hook off (or
offline, or on any failure) every proof path is byte-identical to local-only.

This is **not** a RAG path. It reuses the already-safe community pipeline end to end:

    Stack Exchange API  →  community_cases.extract_community_case_from_text()   (deterministic parse)
                        →  deterministic filter (confidence + dedupe)
                        →  community_cases.rank_community_cases()               (confirm/compare/reject)
                        →  problem_patterns.build_packet(community_report=…)    (EXISTING safe slot)

The existing slot already: redacts every hardware token from web snippets (they can never become
verifier grounding or widen the allowlist), renders cases as CONFIRM/COMPARE only — **never** the
proof step (HELP) — and labels them unverified field experience with a source link. The LLM only
*voices* the already-selected, already-verified cases; it never decides which matter and never
drives the fix. The deterministic proof path stays authoritative.

The network client is injectable: tests pass a fake ``source`` and never touch the network.
"""
from __future__ import annotations

import gzip
import json
import os
import urllib.parse
import urllib.request
from typing import Protocol

from . import community_cases

__all__ = ["enabled", "community_report_for", "StackExchangeSource", "Source"]


# ── Opt-in gate ───────────────────────────────────────────────────────────────────────
# EAEDK stays local-first. The web hook only runs when the operator explicitly enables it.

def enabled() -> bool:
    return os.environ.get("EAEDK_WEB_HOOK", "").strip().lower() in ("1", "true", "yes", "on")


# ── Channel registry — curated (site, tags) per pattern/domain, all via one API ─────────
# Adding a source later is one row here, not new code. Stack Exchange spans every domain
# (electronics, arduino, stackoverflow tags, unix) so a single client covers all of them.

_CHANNELS: dict[str, list[tuple[str, str]]] = {
    "uart_bringup": [("electronics", "uart;serial"), ("stackoverflow", "uart;embedded;stm32")],
    "i2c_bringup":  [("electronics", "i2c;microcontroller"), ("stackoverflow", "i2c;embedded;stm32")],
    "spi_bringup":  [("electronics", "spi;microcontroller"), ("stackoverflow", "spi;embedded;stm32")],
    "power_reset":  [("electronics", "power;reset;microcontroller")],
    "hardfault":    [("stackoverflow", "arm;cortex-m;embedded"), ("electronics", "arm;microcontroller")],
}
# Reserved for learning-domain queries (Linux driver dev, Yocto) — extensible, same client.
_DOMAIN_CHANNELS: dict[str, list[tuple[str, str]]] = {
    "embedded_linux": [("stackoverflow", "linux-kernel;linux-device-driver"), ("unix", "kernel;drivers")],
    "build_systems":  [("stackoverflow", "yocto;bitbake")],
}
_DEFAULT_CHANNELS: list[tuple[str, str]] = [("electronics", "microcontroller;embedded")]

# A Stack Exchange "site" → the SOURCE_TYPES bucket used for confidence scoring.
_SITE_SOURCE_TYPE = {
    "electronics": "stackexchange",
    "arduino":     "stackexchange",
    "unix":        "stackexchange",
    "stackoverflow": "stackoverflow",
}
_SITE_LABEL = {
    "electronics": "EE Stack Exchange",
    "arduino":     "Arduino Stack Exchange",
    "unix":        "Unix & Linux Stack Exchange",
    "stackoverflow": "Stack Overflow",
}

# Deterministic-filter knobs.
_MAX_PER_CHANNEL = 3         # politeness + signal
_MAX_CASES = 4               # what reaches the ranker


# ── Source protocol + the real Stack Exchange client ────────────────────────────────────

class Source(Protocol):
    def search(self, query: str, site: str, tags: str) -> list[dict]:
        """Return a list of {'body','link','site'} dicts. Must be best-effort: any failure → []."""
        ...


class StackExchangeSource:
    """Read-only Stack Exchange API v2.3 client. Best-effort: any error → []."""

    _ENDPOINT = "https://api.stackexchange.com/2.3/search/advanced"

    def __init__(self, timeout: float = 4.0) -> None:
        self._timeout = timeout

    def search(self, query: str, site: str, tags: str) -> list[dict]:
        params = {
            "order": "desc", "sort": "relevance", "q": query,
            "tagged": tags.replace(";", ";"), "site": site,
            "filter": "withbody", "pagesize": str(_MAX_PER_CHANNEL),
        }
        url = self._ENDPOINT + "?" + urllib.parse.urlencode(params)
        try:
            req = urllib.request.Request(url, headers={"Accept-Encoding": "gzip",
                                                       "User-Agent": "eaedk-webhook"})
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                raw = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
                data = json.loads(raw.decode("utf-8", "replace"))
            out = []
            for item in data.get("items", [])[:_MAX_PER_CHANNEL]:
                body = item.get("body", "") or item.get("title", "")
                if body:
                    out.append({"body": body, "link": item.get("link", ""), "site": site})
            return out
        except Exception:
            return []          # offline / rate-limited / parse error → local-only behaviour


_default_source: Source | None = None


def _get_default_source() -> Source:
    global _default_source
    if _default_source is None:
        _default_source = StackExchangeSource()
    return _default_source


# ── Query + filtering (deterministic) ───────────────────────────────────────────────────

def _query_for(pattern, state) -> str:
    """Build a search query from the pattern title + the evidence labels gathered so far."""
    bits = [pattern.title.replace(" problem", "")]
    for var_name, value in (state.evidence or {}).items():
        var = pattern.evidence_vars.get(var_name)
        if var is not None:
            bits.append(var.labels.get(value, value))
    return " ".join(bits)[:140]


def _safe_search(src: Source, query: str, site: str, tags: str) -> list[dict]:
    """Any source failure (offline, rate-limited, raising) → [] → local-only behaviour."""
    try:
        return src.search(query, site, tags) or []
    except Exception:
        return []


def _local_symptoms(pattern, state, symptom_text: str) -> tuple[str, ...]:
    """The local symptom vocabulary the ranker compares cases against. Prefer the user's own
    fault text; fall back to the matched evidence labels, then the pattern title."""
    if symptom_text.strip():
        return (symptom_text.strip(),)
    labels = []
    for var_name, value in (state.evidence or {}).items():
        var = pattern.evidence_vars.get(var_name)
        if var is not None and var.labels.get(value):
            labels.append(var.labels[value])
    return tuple(labels) or (pattern.title,)


# ── Public API ──────────────────────────────────────────────────────────────────────────

def community_report_for(pattern, state, board_name: str | None,
                         symptom_text: str = "", source: Source | None = None):
    """Fetch + rank live field cases for a matched proof pattern, reusing the existing
    ``mine_community_experience`` pipeline (parse → score → confirm/compare/reject).

    Returns a ``CommunityExperienceReport`` with confirm/compare cases, or ``None`` (no section)
    on any of: hook disabled, no source, nothing fetched, nothing usable. ``None`` means the proof
    path renders byte-identical to local-only.

    ``source`` is injectable for tests; when omitted, the real Stack Exchange client is used and
    only if the hook is enabled.
    """
    src = source
    if src is None:
        if not enabled():
            return None
        src = _get_default_source()

    channels = _CHANNELS.get(getattr(pattern, "name", ""), _DEFAULT_CHANNELS)
    query = _query_for(pattern, state)

    snippets: list[dict] = []
    seen: set[str] = set()
    for site, tags in channels:
        for item in _safe_search(src, query, site, tags)[:_MAX_PER_CHANNEL]:
            body = (item.get("body") or "").strip()
            key = body[:120].lower()
            if not body or key in seen:
                continue
            seen.add(key)
            snippets.append({"text": body, "source": _SITE_LABEL.get(site, site),
                             "source_type": _SITE_SOURCE_TYPE.get(site, "unknown"),
                             "reference_link": item.get("link", "")})
            if len(snippets) >= _MAX_CASES:
                break
        if len(snippets) >= _MAX_CASES:
            break
    if not snippets:
        return None

    report = community_cases.mine_community_experience(
        snippets,
        local_evidence=dict(state.evidence or {}),
        local_context=board_name,
        local_symptoms=_local_symptoms(pattern, state, symptom_text),
    )
    if not (report.confirm_cases or report.compare_cases):
        return None
    return report
