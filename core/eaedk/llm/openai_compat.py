"""OpenAI-compatible provider — lets EAEDK use ANY chat-completions endpoint so the user can pick a
stronger reasoning model than a local 8B: OpenCode Zen, Gemini's OpenAI-compatible API, OpenRouter,
a hosted vLLM, etc. Stdlib urllib only — no extra dependency.

Configured by environment (so a user switches model/provider without touching code):
    EAEDK_LLM_BASE_URL   the endpoint base, e.g. https://opencode.ai/zen/v1  (required to enable it)
    EAEDK_LLM_API_KEY    bearer token (if the endpoint needs one)
    EAEDK_MENTOR_MODEL   the model id at that endpoint, e.g. deepseek-v4 / gemini-2.5-flash
    EAEDK_LLM_TIMEOUT    generation timeout in seconds (default 120)
"""
from __future__ import annotations

import json
import os
import urllib.request

DEFAULT_TIMEOUT = 120.0


class OpenAICompatProvider:
    def __init__(self, model: str, base_url: str, api_key: str = "", timeout: float | None = None):
        self.model = model
        self.base_url = (base_url or "").rstrip("/")
        self.api_key = api_key or ""
        try:
            self.timeout = float(os.environ.get("EAEDK_LLM_TIMEOUT", "") or (timeout or DEFAULT_TIMEOUT))
        except ValueError:
            self.timeout = timeout or DEFAULT_TIMEOUT

    def available(self) -> bool:
        # Configured == usable. A dead/forbidden endpoint surfaces at generate() and the caller
        # (mentor_chat) degrades to its grounded deterministic answer.
        return bool(self.base_url and self.model)

    def generate(self, system: str, prompt: str) -> str:
        body = json.dumps({
            "model": self.model,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": prompt}],
            "temperature": 0.2,
            "stream": False,
        }).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        req = urllib.request.Request(f"{self.base_url}/chat/completions", data=body, headers=headers)
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
        choices = data.get("choices") or [{}]
        return (choices[0].get("message", {}).get("content", "") or "").strip()
