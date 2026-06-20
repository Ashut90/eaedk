"""OpenAI-compatible provider + mentor provider selection — so users can switch models freely."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import json, io
from eaedk.llm.openai_compat import OpenAICompatProvider
from eaedk.mentor_llm import _mentor_provider
from eaedk.llm.ollama import OllamaProvider


def test_selection_local_vs_endpoint(monkeypatch):
    monkeypatch.delenv("EAEDK_LLM_BASE_URL", raising=False)
    assert isinstance(_mentor_provider(), OllamaProvider)
    monkeypatch.setenv("EAEDK_LLM_BASE_URL", "https://opencode.ai/zen/v1")
    monkeypatch.setenv("EAEDK_LLM_API_KEY", "sk-test")
    monkeypatch.setenv("EAEDK_MENTOR_MODEL", "deepseek-v4")
    p = _mentor_provider()
    assert isinstance(p, OpenAICompatProvider) and p.model == "deepseek-v4"
    assert p.base_url == "https://opencode.ai/zen/v1" and p.available()


def test_available_requires_config():
    assert OpenAICompatProvider("m", "https://x/v1", "k").available()
    assert not OpenAICompatProvider("m", "").available()       # no base url → not configured


def test_generate_parses_openai_response(monkeypatch):
    captured = {}

    class _Resp(io.BytesIO):
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["auth"] = req.headers.get("Authorization")
        captured["body"] = json.loads(req.data.decode())
        return _Resp(json.dumps({"choices": [{"message": {"content": "  hello from the endpoint  "}}]}).encode())

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    out = OpenAICompatProvider("gemini-2.5-flash", "https://api/v1", "sk-abc").generate("SYS", "USER")
    assert out == "hello from the endpoint"
    assert captured["url"].endswith("/chat/completions")
    assert captured["auth"] == "Bearer sk-abc"
    assert captured["body"]["model"] == "gemini-2.5-flash"
    assert captured["body"]["messages"][0] == {"role": "system", "content": "SYS"}
    assert captured["body"]["messages"][1] == {"role": "user", "content": "USER"}
