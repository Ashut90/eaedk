"""ISOLATED, REVERSIBLE demo — one path only: board recommendation.

Proves the intended architecture WITHOUT touching any live path (mentor_chat, the Purpose gate,
the engines are all untouched — delete this file to revert completely):

    deterministic engine  ->  structured FACT PACKET  ->  LLM mentors conversationally  ->  verifier

This is NOT RAG. The packet is *computed facts* (board geometry, fit verdicts, architecture) — never
document chunks, never retrieved text. The LLM may choose tone / structure / reasoning / the follow-up
question, but it may state hardware facts ONLY from the packet; the verifier FLAGS (does not delete)
any hardware figure not in the packet.

Run:  PYTHONPATH=core EAEDK_LLM_TIMEOUT=300 python -m demo.board_mentor_demo
Needs Ollama running with llama3.1:8b pulled.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from eaedk.store.db import connect
from eaedk.store.migrate import migrate
from eaedk.seed import seed_all
from eaedk import semantic_cost as sc, repo, mentor
from eaedk.llm.ollama import OllamaProvider

MENTOR_MODEL = "llama3.1:8b"          # rule 2: this model is the mentor
QUESTION = "What is the best board to start with AI?"

_INTENT_LABEL = {"tflite_micro": "on-device tiny-ML (TensorFlow-Lite-Micro) inference"}


# ---------------------------------------------------------------------------------------------------
# STAGE 1 — the deterministic engine computes a structured FACT PACKET (rule 8). No LLM here.
# ---------------------------------------------------------------------------------------------------
def _kb(b):
    return f"{b // 1024}KB" if isinstance(b, int) else "UNKNOWN"


def _enrich(conn, entry: dict) -> dict:
    board, soc = repo.load_board(conn, entry["board"])
    return {"board": entry["board"],
            "flash": entry["flash"], "ram": entry["ram"],
            "arch": soc["arch"] if soc else None,
            "linux_class": mentor.supports_linux(soc),          # Cortex-A application processor?
            "capabilities": sorted(repo.board_capability_names(conn, entry["board"]))}


def build_fact_packet(conn, question: str) -> dict:
    terms = sc.parse_intent(question) or ["tflite_micro"]
    primary = terms[0]
    cost = repo.semantic_cost(conn, primary)
    rec = sc.recommend_boards(conn, terms)
    low = question.lower()
    learner = "beginner / starting out" if any(w in low for w in ("start", "begin", "new")) else "unstated"
    return {
        "learner_signal": learner,
        "goal": _INTENT_LABEL.get(primary, primary),
        "intent_cost": ({"term": primary,
                         "flash": f"{_kb(cost['flash_min_bytes'])}–{_kb(cost['flash_max_bytes'])}",
                         "ram": f"{_kb(cost['ram_min_bytes'])}–{_kb(cost['ram_max_bytes'])}"}
                        if cost else {"term": primary}),
        "fits": [_enrich(conn, e) for e in rec["fits"]],
        "maybe": [_enrich(conn, e) for e in rec["maybe"]],
        "too_small": [_enrich(conn, e) for e in rec["no"]],
    }


def render_packet_for_prompt(p: dict) -> str:
    def line(e):
        cls = "Linux-class (Cortex-A)" if e["linux_class"] else "MCU"
        return (f"  - {e['board']}: {e['arch']}, {cls}, flash {_kb(e['flash'])}, RAM {_kb(e['ram'])}, "
                f"peripherals: {', '.join(e['capabilities']) or 'n/a'}")
    L = ["VERIFIED FACT PACKET (engine-computed — the ONLY hardware facts you may state):",
         f"  learner: {p['learner_signal']}",
         f"  goal: {p['goal']}  (needs ~{p['intent_cost'].get('flash','?')} flash / "
         f"~{p['intent_cost'].get('ram','?')} RAM)",
         "  boards that FIT:"]
    L += [line(e) for e in p["fits"]]
    if p["maybe"]:
        L.append("  boards that MIGHT fit (geometry unknown / tight):")
        L += [line(e) for e in p["maybe"]]
    if p["too_small"]:
        L.append("  boards TOO SMALL: " + ", ".join(e["board"] for e in p["too_small"]))
    return "\n".join(L)


# ---------------------------------------------------------------------------------------------------
# STAGE 2 — the LLM MENTORS conversationally over the packet (rule 9). Facts only from the packet (10).
# ---------------------------------------------------------------------------------------------------
_MENTOR_SYSTEM = (
    "You are a senior embedded-firmware mentor talking to a beginner, one human to another. You are "
    "given a VERIFIED FACT PACKET computed by a deterministic engine. Mentor them: recommend ONE board "
    "to START with, and explain why it is the right STARTING point for a beginner — not merely which "
    "board has the most memory. Weigh approachability (ecosystem, how hard the architecture is to learn, "
    "MCU vs embedded-Linux) against the goal. Explain the key trade-off in plain language, give ONE "
    "concrete next step, and end with ONE follow-up question.\n"
    "HARD RULES: state hardware/board facts (flash, RAM, architecture, which boards fit) ONLY from the "
    "packet. Do NOT invent clock speeds, part numbers, addresses, or any spec not in the packet. Where "
    "it helps the learner, name the concrete flash/RAM figure from the packet. Write like a person — a "
    "few short paragraphs, no tables, no bullet dumps. Choose your own tone.")


def mentor_over_packet(question: str, packet: dict, timeout: float = 300.0) -> str:
    prov = OllamaProvider(model=MENTOR_MODEL, timeout=timeout)
    prompt = (f"LEARNER ASKED: \"{question}\"\n\n{render_packet_for_prompt(packet)}\n\n"
              "Mentor them now, conversationally:")
    return prov.generate(_MENTOR_SYSTEM, prompt).strip()


# ---------------------------------------------------------------------------------------------------
# STAGE 3 — the verifier checks every hardware claim against the packet and produces a REPORT with a
# per-claim PASS / UNSUPPORTED / CONTRADICTION status and an explicit ALLOW / BLOCK decision (rule 11).
#
# Authority model:
#   - MEMORY (flash/RAM) the engine OWNS  -> a figure not in the packet is a CONTRADICTION (blocks).
#   - CLOCK / ADDRESS the engine does NOT have -> UNSUPPORTED (unverifiable: flagged, not deleted).
# Nothing is ever silently removed.
# ---------------------------------------------------------------------------------------------------
_HW_FIGURE = re.compile(r"\b\d+(?:\.\d+)?\s?(?:mhz|ghz|gb|mb|kb|kib|mib)\b|\b0x[0-9a-f]+\b", re.I)
_UNIT = {"kb": 1024, "kib": 1024, "mb": 1024 ** 2, "mib": 1024 ** 2, "gb": 1024 ** 3}


@dataclass
class Claim:
    text: str        # the figure exactly as the mentor wrote it
    kind: str        # "memory" | "clock" | "address"
    status: str      # "PASS" | "UNSUPPORTED" | "CONTRADICTION"
    source: str      # which packet fact it matched, or why it failed


def _to_bytes(figure: str):
    m = re.match(r"(\d+(?:\.\d+)?)\s?(kb|kib|mb|mib|gb)", figure.lower())
    return int(float(m.group(1)) * _UNIT[m.group(2)]) if m else None


def _board_specs(packet: dict):
    """Per-board (flash,ram) bytes + lowercase name tokens, the goal-cost byte values, and the set of
    every memory value any board has — so a figure can be checked against the board it is ATTRIBUTED
    to, not just 'is it anywhere in the packet'."""
    specs: dict[str, tuple] = {}
    tokens: dict[str, list[str]] = {}
    all_bytes: set[int] = set()
    for grp in ("fits", "maybe", "too_small"):
        for e in packet[grp]:
            specs[e["board"]] = (e["flash"], e["ram"])
            toks = [t for t in re.findall(r"[a-z0-9]+", e["board"].lower()) if len(t) >= 4]
            toks.append(e["board"].lower().replace("-", ""))
            tokens[e["board"]] = toks
            all_bytes |= {b for b in (e["flash"], e["ram"]) if isinstance(b, int)}
    goal_bytes = {b for k in ("flash", "ram")
                  for fig in re.findall(r"\d+kb|\d+mb", packet["intent_cost"].get(k, "").lower())
                  for b in (_to_bytes(fig),) if b is not None}
    return specs, tokens, all_bytes, goal_bytes


def verify(text: str, packet: dict) -> tuple[list[Claim], str, str]:
    """Return (per-claim results, annotated answer, decision) — ALLOWED / ALLOWED_WITH_FLAGS / BLOCKED.
    Memory is checked against the board the figure is ATTRIBUTED to (the nearest board named before it);
    a value belonging to a DIFFERENT board is a CONTRADICTION, not a pass. Nothing is deleted."""
    specs, tokens, all_bytes, goal_bytes = _board_specs(packet)
    claims: list[Claim] = []

    def attributed_board(pos: int) -> str | None:
        window = text[max(0, pos - 140):pos].lower()
        best, best_at = None, -1
        for name, toks in tokens.items():
            at = max((window.rfind(t) for t in toks), default=-1)
            if at > best_at:
                best, best_at = name, at
        return best

    def classify(tok: str, pos: int) -> Claim:
        norm = tok.lower().replace(" ", "")
        if norm.startswith("0x"):
            return Claim(tok, "address", "UNSUPPORTED", "engine packet carries no addresses")
        if re.search(r"(mhz|ghz)$", norm):
            return Claim(tok, "clock", "UNSUPPORTED", "engine packet carries no clock speeds")
        b = _to_bytes(norm)
        if b in goal_bytes:
            return Claim(tok, "memory", "PASS", "matches the goal's model-size estimate")
        board = attributed_board(pos)
        if board:
            bf, br = specs[board]
            if b in (bf, br):
                return Claim(tok, "memory", "PASS", f"matches {board}")
            other = b in all_bytes
            why = (f"attributed to {board} (flash {_kb(bf)} / RAM {_kb(br)}) but that value belongs to "
                   "a DIFFERENT board" if other else f"{board} has no such memory value")
            return Claim(tok, "memory", "CONTRADICTION", why)
        if b in all_bytes:
            return Claim(tok, "memory", "PASS", "matches a board in the packet (unattributed)")
        return Claim(tok, "memory", "CONTRADICTION", "no board/goal in the packet has this value")

    def annotate(m: re.Match) -> str:
        c = classify(m.group(0), m.start())
        claims.append(c)
        if c.status == "PASS":
            return c.text
        tag = "unverifiable" if c.status == "UNSUPPORTED" else "CONTRADICTS engine facts"
        return f"{c.text} ⚠️[{tag}]"

    annotated = _HW_FIGURE.sub(annotate, text)
    if any(c.status == "CONTRADICTION" for c in claims):
        decision = "BLOCKED"
    elif any(c.status == "UNSUPPORTED" for c in claims):
        decision = "ALLOWED_WITH_FLAGS"
    else:
        decision = "ALLOWED"
    return claims, annotated, decision


def render_verifier_report(claims: list[Claim], decision: str) -> str:
    L = [f"Hardware claims checked: {len(claims)}"]
    passed = [c for c in claims if c.status == "PASS"]
    unsup = [c for c in claims if c.status == "UNSUPPORTED"]
    contra = [c for c in claims if c.status == "CONTRADICTION"]
    L.append(f"  PASS ({len(passed)}):")
    L += [f"    ✓ {c.text:<10} {c.kind:<8} — {c.source}" for c in passed] or ["    (none)"]
    L.append(f"  UNSUPPORTED — unverifiable, flagged not deleted ({len(unsup)}):")
    L += [f"    ⚠ {c.text:<10} {c.kind:<8} — {c.source}" for c in unsup] or ["    (none)"]
    L.append(f"  CONTRADICTION — conflicts with a verified fact ({len(contra)}):")
    L += [f"    ✗ {c.text:<10} {c.kind:<8} — {c.source}" for c in contra] or ["    (none)"]
    verdict = {
        "ALLOWED": "✅ ALLOWED — every hardware claim traces to the fact packet; answer shown as-is.",
        "ALLOWED_WITH_FLAGS": "⚠️ ALLOWED WITH FLAGS — shown to the learner with the unverifiable "
                              "figures marked (not deleted).",
        "BLOCKED": "⛔ BLOCKED — a claim contradicts a verified engine fact; answer must not be shown as-is.",
    }[decision]
    L.append("")
    L.append(f"DECISION: {verdict}")
    return "\n".join(L)


# ---------------------------------------------------------------------------------------------------
# Output: a BEFORE contrast, then the THREE separate artifacts proving engine -> LLM -> verifier.
# ---------------------------------------------------------------------------------------------------
def _hr(title):
    print("\n" + "=" * 90 + f"\n{title}\n" + "=" * 90)


def main():
    conn = connect(":memory:"); migrate(conn); seed_all(conn, force=True)
    print("#" * 90)
    print(f"# QUESTION:  {QUESTION}")
    print("#" * 90)

    print("\n----- BEFORE (today — the deterministic engine WRITES the answer: a sorted table) -----\n")
    print(sc.recommend_chat(conn, sc.parse_intent(QUESTION) or ["tflite_micro"]))

    # ---- ARTIFACT 1: the deterministic engine's structured fact packet (no LLM, no documents) ----
    packet = build_fact_packet(conn, QUESTION)
    _hr("ARTIFACT 1  —  FACT PACKET  (produced by the deterministic engine; computed facts, not RAG)")
    print(render_packet_for_prompt(packet))

    # ---- ARTIFACT 2: the LLM mentor's answer, generated ONLY from that packet ----
    _hr(f"ARTIFACT 2  —  LLM MENTOR ANSWER  (generated by {MENTOR_MODEL} from ARTIFACT 1, verbatim)")
    try:
        answer = mentor_over_packet(QUESTION, packet)
    except Exception as e:
        print(f"[mentor model unavailable: {e!r}]  Start Ollama + `ollama pull {MENTOR_MODEL}`, then re-run.")
        return
    print(answer)

    # ---- ARTIFACT 3: the verifier's per-claim report + allow/deny decision ----
    claims, annotated, decision = verify(answer, packet)
    _hr("ARTIFACT 3  —  VERIFIER REPORT  (each claim checked against ARTIFACT 1; allow/deny decision)")
    print(render_verifier_report(claims, decision))
    if decision != "ALLOWED":
        print("\nAnswer as shown to the learner (flags inline, nothing deleted):\n")
        print(annotated)

    # ---- Proof the verifier is a real gate, not a rubber stamp: feed it a planted bad answer ----
    _hr("VERIFIER SELF-TEST  —  a PLANTED bad answer (proves the gate truly rejects, not always-pass)")
    planted = ("Go with the ESP32 — it runs at 240MHz and has 256KB of RAM, plenty for your model "
               "at 0x20000000.")
    print(f"planted answer: {planted}\n")
    p_claims, p_annotated, p_decision = verify(planted, packet)
    print(render_verifier_report(p_claims, p_decision))
    print("\nplanted answer after verify (flags inline):\n" + p_annotated)


if __name__ == "__main__":
    main()
