"""LLM gateway: provider selection + the cited-explanation flow.

Single entry point used by the CLI's `ask`/`explain`. Runs the deterministic assessment
first (caller supplies it), sends only the cited context to the model, then applies the
post-filter so no uncited hardware fact survives.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Protocol

from ..schemas.response import AssessResponse
from .ollama import OllamaProvider
from .postfilter import build_allowlist, filter_text
from . import prompts


class Provider(Protocol):
    model: str
    def available(self) -> bool: ...
    def generate(self, system: str, prompt: str) -> str: ...


@dataclass
class LLMOutput:
    text: str
    removed: int
    model: str


def build_provider() -> Provider:
    # Only Ollama in the MVP (offline-only, D1). Pluggable point for future backends.
    return OllamaProvider()


class Gateway:
    def __init__(self, provider: Provider | None = None):
        self.provider = provider or build_provider()

    def available(self) -> bool:
        return self.provider.available()

    @property
    def model(self) -> str:
        return self.provider.model

    def _run(self, conn: sqlite3.Connection, project: sqlite3.Row,
             resp: AssessResponse, prompt: str) -> LLMOutput:
        raw = self.provider.generate(prompts.SYSTEM, prompt)
        allow = build_allowlist(conn, project)
        filtered, removed = filter_text(raw, allow)
        return LLMOutput(text=filtered, removed=removed, model=self.provider.model)

    def ask(self, conn, project, resp: AssessResponse, question: str | None) -> LLMOutput:
        return self._run(conn, project, resp, prompts.build_ask_prompt(resp, question))

    def explain(self, conn, project, resp: AssessResponse, rule_key: str) -> LLMOutput:
        return self._run(conn, project, resp, prompts.build_explain_prompt(resp, rule_key))
