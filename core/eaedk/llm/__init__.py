"""Pluggable LLM gateway (spec §4.10, §4.3).

The LLM is the convenience layer (decision D1: offline-only). It may explain, draft, and
triage — it may NEVER assert a hardware fact that isn't cited from the knowledge base. That
contract is enforced structurally by the post-filter, not by prompt wording alone.
"""
