"""Strip chain-of-thought from reasoning models (DeepSeek-R1 and friends).

R1-style models emit their reasoning inside <think>…</think> before the actual answer. EAEDK only
wants the answer — the reasoning must never reach the post-filter or the user.
"""
from __future__ import annotations

import re

_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.S | re.I)


def strip_think(text: str) -> str:
    """Remove <think>…</think> reasoning blocks. Also handles the common case where the opening tag
    was swallowed by the chat template and only a trailing </think> separates reasoning from answer."""
    t = _THINK_BLOCK.sub("", text or "")
    if "</think>" in t.lower():                      # stray close tag → keep only what follows it
        idx = t.lower().rfind("</think>")
        t = t[idx + len("</think>"):]
    return t.strip()
