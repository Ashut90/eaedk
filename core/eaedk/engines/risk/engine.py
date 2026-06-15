"""Risk Engine — data-driven rules over a sandboxed mini-DSL (spec §2.4).

Grammar (MVP, no parentheses):
    condition  := comparison (("and"|"or") comparison)*
    comparison := term op term
    term       := operand (("*"|"/"|"+"|"-") operand)*      # left-assoc arithmetic chain
    operand    := number | dotted.ident
    op         := ">" | ">=" | "<" | "<=" | "==" | "!="

A ``term`` is a left-associative chain of any length (no operator precedence): the
FLASH_ENDURANCE rule needs ``write_rate * projected_device_lifetime_seconds`` and the
grammar must compose multiple operands, not just two.

The evaluator never uses Python ``eval``. An unknown/non-numeric ident raises
``UnknownIdent``; the rule then yields a finding with severity ``UNKNOWN`` (surfaced as
missing info), never silently skipped.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from ...context import as_int

_TOKEN_RE = re.compile(r"""
    \s*(?:
        (?P<num>0[xX][0-9a-fA-F]+|\d+\.\d+|\d+) |
        (?P<op>>=|<=|==|!=|>|<) |
        (?P<arith>[*/+\-]) |
        (?P<ident>[A-Za-z_][A-Za-z0-9_.]*)
    )
""", re.VERBOSE)

_KEYWORDS = {"and", "or"}
_COMPARATORS = {">", ">=", "<", "<=", "==", "!="}


class UnknownIdent(Exception):
    pass


class DSLSyntaxError(Exception):
    pass


@dataclass
class RiskRule:
    key: str
    goal_type: str | None
    condition_dsl: str
    severity: str
    explanation_tmpl: str
    mitigation_tmpl: str | None = None
    # Inputs that MUST be present (and non-None) for the rule to be applicable. If any is
    # missing the rule is skipped entirely — it does not apply to this project, so it must
    # not emit a noisy UNKNOWN. Gating inputs (e.g. write_rate) live here.
    requires: tuple[str, ...] = ()
    # Severity to report when the condition references an ident that cannot be resolved
    # (e.g. an unconfirmed board fact) *after* the requires-gate has passed. "UNKNOWN"
    # keeps the legacy "missing info" behaviour; any real severity turns the unresolved
    # case into a fired, lower-confidence warning instead of silently dropping it.
    severity_on_unknown: str = "UNKNOWN"


@dataclass
class RiskFinding:
    rule_key: str
    severity: str
    explanation: str
    mitigation: str | None
    fired: bool


def _tokenize(expr: str) -> list[str]:
    tokens: list[str] = []
    pos = 0
    while pos < len(expr):
        if expr[pos].isspace():
            pos += 1
            continue
        m = _TOKEN_RE.match(expr, pos)
        if not m or m.start(m.lastgroup) != pos and m.group().strip() == "":
            raise DSLSyntaxError(f"bad token near: {expr[pos:pos+10]!r}")
        tok = m.group().strip()
        if not tok:
            raise DSLSyntaxError(f"bad token near: {expr[pos:pos+10]!r}")
        tokens.append(tok)
        pos = m.end()
    return tokens


def _num(tok: str) -> float:
    return float(int(tok, 16)) if tok.lower().startswith("0x") else float(tok)


def _resolve(ident: str, ctx: dict[str, Any]) -> float:
    if ident not in ctx:
        raise UnknownIdent(ident)
    v = ctx[ident]
    iv = as_int(v)
    if iv is not None:
        return float(iv)
    try:
        return float(v)
    except (TypeError, ValueError):
        raise UnknownIdent(ident)


class _Parser:
    def __init__(self, tokens: list[str], ctx: dict[str, Any]):
        self.t = tokens
        self.i = 0
        self.ctx = ctx

    def peek(self) -> str | None:
        return self.t[self.i] if self.i < len(self.t) else None

    def next(self) -> str:
        tok = self.t[self.i]
        self.i += 1
        return tok

    def parse(self) -> bool:
        val = self.comparison()
        while self.peek() in _KEYWORDS:
            op = self.next()
            rhs = self.comparison()
            val = (val and rhs) if op == "and" else (val or rhs)
        if self.i != len(self.t):
            raise DSLSyntaxError(f"trailing tokens: {self.t[self.i:]}")
        return val

    def comparison(self) -> bool:
        left = self.term()
        op = self.peek()
        if op not in _COMPARATORS:
            raise DSLSyntaxError(f"expected comparator, got {op!r}")
        self.next()
        right = self.term()
        return {
            ">": left > right, ">=": left >= right,
            "<": left < right, "<=": left <= right,
            "==": left == right, "!=": left != right,
        }[op]

    def term(self) -> float:
        val = self.operand()
        while self.peek() in {"*", "/", "+", "-"}:
            op = self.next()
            rhs = self.operand()
            val = {"*": val * rhs, "/": val / rhs if rhs else float("inf"),
                   "+": val + rhs, "-": val - rhs}[op]
        return val

    def operand(self) -> float:
        tok = self.next()
        if tok in _COMPARATORS or tok in {"*", "/", "+", "-"} or tok in _KEYWORDS:
            raise DSLSyntaxError(f"expected operand, got {tok!r}")
        if re.fullmatch(r"0[xX][0-9a-fA-F]+|\d+\.\d+|\d+", tok):
            return _num(tok)
        return _resolve(tok, self.ctx)


def eval_condition(condition: str, ctx: dict[str, Any]) -> bool:
    """Evaluate a DSL condition. Raises UnknownIdent / DSLSyntaxError on failure."""
    return _Parser(_tokenize(condition), ctx).parse()


_FIELD_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_.]*)\}")


def _render(tmpl: str, ctx: dict[str, Any]) -> str:
    def sub(m: "re.Match[str]") -> str:
        v = ctx.get(m.group(1))
        return "?" if v is None else str(v)
    return _FIELD_RE.sub(sub, tmpl)


def evaluate_risks(ctx: dict[str, Any], rules: list[RiskRule],
                   goal_type: str) -> list[RiskFinding]:
    """Return findings for rules that fired (or that referenced unknown idents)."""
    findings: list[RiskFinding] = []
    for r in rules:
        if r.goal_type not in (None, goal_type):
            continue
        # requires-gate: a rule whose gating inputs are absent simply does not apply to this
        # project. Skipping (rather than emitting UNKNOWN) keeps gated rules silent until the
        # engineer supplies the trigger input (e.g. write_rate for endurance).
        if any(req not in ctx or ctx.get(req) is None for req in r.requires):
            continue
        try:
            fired = eval_condition(r.condition_dsl, ctx)
        except UnknownIdent as e:
            if r.severity_on_unknown == "UNKNOWN":
                findings.append(RiskFinding(
                    r.key, "UNKNOWN",
                    f"cannot evaluate risk '{r.key}': missing input {e}",
                    r.mitigation_tmpl, fired=False))
            else:
                # The gating inputs are present but a board fact (e.g. the flash endurance
                # rating) is unconfirmed. That is itself a real, lower-confidence warning —
                # surface it instead of dropping it. No silent failure.
                findings.append(RiskFinding(
                    r.key, r.severity_on_unknown,
                    f"{_render(r.explanation_tmpl, ctx)} (Worst case assumed: {e} is "
                    f"UNCONFIRMED for this board — verify it in the datasheet.)",
                    _render(r.mitigation_tmpl, ctx) if r.mitigation_tmpl else None,
                    fired=True))
            continue
        if fired:
            findings.append(RiskFinding(
                r.key, r.severity, _render(r.explanation_tmpl, ctx),
                _render(r.mitigation_tmpl, ctx) if r.mitigation_tmpl else None,
                fired=True))
    return findings
