"""Ollama provider adapter (offline, local). Uses stdlib urllib — no extra dependency.

Default model: qwen2.5-coder:3b. Host via EAEDK_OLLAMA_HOST (default localhost:11434),
model via EAEDK_LLM_MODEL, generation timeout (seconds) via EAEDK_LLM_TIMEOUT (default 120) —
raise it on a slow machine where a cold model load exceeds the default.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

DEFAULT_MODEL = "qwen2.5-coder:3b"
DEFAULT_HOST = "http://localhost:11434"
DEFAULT_TIMEOUT = 120.0


def _env_timeout(default: float) -> float:
    try:
        return float(os.environ.get("EAEDK_LLM_TIMEOUT", "") or default)
    except ValueError:
        return default


class OllamaProvider:
    def __init__(self, model: str | None = None, host: str | None = None,
                 timeout: float | None = None):
        self.model = model or os.environ.get("EAEDK_LLM_MODEL", DEFAULT_MODEL)
        self.host = (host or os.environ.get("EAEDK_OLLAMA_HOST", DEFAULT_HOST)).rstrip("/")
        self.timeout = timeout if timeout is not None else _env_timeout(DEFAULT_TIMEOUT)

    def available(self) -> bool:
        """True if the Ollama server is reachable and the target model is present."""
        try:
            with urllib.request.urlopen(f"{self.host}/api/tags", timeout=3.0) as r:
                tags = json.loads(r.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, ValueError):
            return False
        names = {m.get("name", "") for m in tags.get("models", [])}
        # Exact tag match only. A sibling tag (e.g. :7b) is NOT the requested model and
        # would 404 at generate time, so it must not count as available.
        return self.model in names

    def generate(self, system: str, prompt: str) -> str:
        body = json.dumps({
            "model": self.model, "system": system, "prompt": prompt,
            "stream": False, "options": {"temperature": 0.2},
        }).encode("utf-8")
        req = urllib.request.Request(
            f"{self.host}/api/generate", data=body,
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
        return data.get("response", "").strip()
