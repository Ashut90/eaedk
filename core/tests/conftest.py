"""Shared pytest configuration.

Hermetic LLM: the test suite must never depend on a live model. Several endpoints
default to ``use_llm=True`` and degrade to a deterministic fallback when no model is
reachable — but if a developer happens to have Ollama running locally, those tests
make real, non-deterministic model calls and flake (observed on the studio/review
endpoints). We point the Ollama host at an unreachable address so ``gateway.available()``
is deterministically False everywhere and every LLM path takes its grounded
deterministic branch.

A developer can still run live integration on purpose by exporting EAEDK_OLLAMA_HOST
themselves before invoking pytest; we only set it when it is not already set.
"""
import os

import pytest


@pytest.fixture(autouse=True)
def _hermetic_offline_llm(monkeypatch):
    if "EAEDK_OLLAMA_HOST" not in os.environ:
        # 127.0.0.1:1 refuses immediately (no slow timeout) → available() == False.
        monkeypatch.setenv("EAEDK_OLLAMA_HOST", "http://127.0.0.1:1")
