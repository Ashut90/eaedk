"""Web hook (Step 5) — opt-in gating, deterministic adapter, offline-safety, and the
CONFIRM/COMPARE-only + redaction guarantees. A fake source; no test ever hits the network."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eaedk import web_source, problem_patterns as pp


# A fake Stack Exchange source — returns canned answer bodies, never touches the network.
class _FakeSource:
    def __init__(self, bodies):
        self._bodies = bodies
        self.queries = []

    def search(self, query, site, tags):
        self.queries.append((query, site, tags))
        return [{"body": b, "link": f"https://example/{i}", "site": site}
                for i, b in enumerate(self._bodies)]


class _OfflineSource:
    def search(self, query, site, tags):
        raise RuntimeError("network down")


def _i2c_state():
    # A matched I2C proof path, advanced one step (lines low → pull-up zone).
    return pp.resolve([{"role": "user", "content": "my i2c is not working, no ack"},
                       {"role": "user", "content": "sda is stuck low at idle"}])


# A realistic community answer: clear symptom + verification + fix (parses to a usable case).
_GOOD_BODY = ("I had I2C with no ACK and the bus stuck low. I measured SDA with a scope and it never "
              "went high. The fix was adding 4.7k pull-up resistors on SDA and SCL — after that the "
              "scan found the device. Verified by probing the lines at idle.")


# ── Opt-in gating ───────────────────────────────────────────────────────────────────────

def test_disabled_by_default(monkeypatch):
    monkeypatch.delenv("EAEDK_WEB_HOOK", raising=False)
    assert web_source.enabled() is False
    # With no injected source and the hook off, it must return None (local-only).
    assert web_source.community_report_for(pp.PATTERNS["i2c_bringup"], _i2c_state(), "STM32F103") is None


def test_enabled_flag_parsing(monkeypatch):
    monkeypatch.setenv("EAEDK_WEB_HOOK", "1")
    assert web_source.enabled() is True
    monkeypatch.setenv("EAEDK_WEB_HOOK", "off")
    assert web_source.enabled() is False


# ── Adapter behaviour (injected fake source — bypasses the env gate) ───────────────────────

def test_injected_source_produces_a_report():
    rep = web_source.community_report_for(
        pp.PATTERNS["i2c_bringup"], _i2c_state(), "STM32F103",
        symptom_text="my i2c is not working, no ack, bus stuck low",
        source=_FakeSource([_GOOD_BODY]))
    assert rep is not None
    assert rep.confirm_cases or rep.compare_cases       # produced usable field cases


def test_offline_source_returns_none():
    rep = web_source.community_report_for(
        pp.PATTERNS["i2c_bringup"], _i2c_state(), "STM32F103", source=_OfflineSource())
    assert rep is None                                  # any failure → local-only


def test_unparseable_text_returns_none():
    rep = web_source.community_report_for(
        pp.PATTERNS["i2c_bringup"], _i2c_state(), "STM32F103",
        source=_FakeSource(["lol nice board", "thanks!"]))     # no symptom → no case
    assert rep is None


def test_query_targets_the_patterns_channels():
    fake = _FakeSource([_GOOD_BODY])
    web_source.community_report_for(pp.PATTERNS["i2c_bringup"], _i2c_state(), "STM32F103", source=fake)
    sites = {site for (_q, site, _t) in fake.queries}
    assert "electronics" in sites and "stackoverflow" in sites   # the i2c channel registry


# ── Safety: feeding the report through the packet keeps it CONFIRM/COMPARE-only + redacted ──

def test_web_report_is_compare_only_and_redacted_in_packet():
    # A web case that mentions a specific pin (PA9) must NOT become verifier grounding / allowlist,
    # and must never drive the proof step. Reuses the existing packet machinery.
    body = ("UART no output, TX silent. On my board PA9 was the issue — it wasn't on the alternate "
            "function. I scoped the pin and fixed the pinmux. Verified with a logic analyzer.")
    state = pp.resolve([{"role": "user", "content": "my uart prints nothing"}])
    rep = web_source.community_report_for(
        pp.PATTERNS["uart_bringup"], state, "STM32F103",
        symptom_text="my uart prints nothing, tx is silent, no output",
        source=_FakeSource([body]))
    assert rep is not None
    packet = pp.build_packet(state, "STM32F103", community_report=rep)
    # The proof step (HELP) comes from the engine, never from a community case.
    assert packet.get("proof_step")
    # Verifier allowlist must NOT have learned PA9 from the unverified web case.
    allow = pp._packet_for_verifier_allowlist(packet)
    import json as _json
    assert "PA9" not in _json.dumps(allow)
