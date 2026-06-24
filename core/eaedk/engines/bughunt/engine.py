"""Bug Hunt Engine — deterministic static scan of embedded C/C++ firmware.

Pipeline:
  1. Find all *_IRQHandler function ranges in each file (two-pass, pure brace counting).
  2. Scan line by line: apply rules from rules.py, passing ISR context and brace depth.
  3. Detect switch() blocks without a default: case (multi-line tracking).
  4. Aggregate into BugHuntResult.

No LLM is involved.  The engine never writes to the filesystem.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

from .rules import (
    PATTERN_MAP, PATTERNS, match_line,
    ADC_START_RE, ADC_CALIB_RE,
    GPIO_INPUT_RE, GPIO_NOPULL_RE, GPIO_PULLUP_RE,
    DMA_INIT_RE, RCC_DMA_RE,
    _GPIO_RESET_RE, _HAL_COMM_RE,
)

_ISR_RE = re.compile(r'\b\w+_IRQHandler\s*\(')
_SWITCH_RE = re.compile(r'\bswitch\s*\(')
_DEFAULT_RE = re.compile(r'\bdefault\s*:')

C_EXTENSIONS = {".c", ".cpp", ".cc", ".cxx", ".h", ".hpp"}


@dataclass
class BugFinding:
    file: str
    line_no: int
    rule_key: str
    severity: str
    description: str
    fix: str
    snippet: str
    category: str = "code"   # "code" = crash risk  |  "behavioral" = silent wrong behavior

    def to_dict(self) -> dict:
        return {
            "file": self.file, "line_no": self.line_no,
            "rule_key": self.rule_key, "severity": self.severity,
            "description": self.description, "fix": self.fix,
            "snippet": self.snippet, "category": self.category,
        }


@dataclass
class BugHuntResult:
    path: str
    files_scanned: int
    findings: list[BugFinding] = field(default_factory=list)

    @property
    def summary(self) -> dict[str, int]:
        counts: dict[str, int] = {"HIGH": 0, "MEDIUM": 0, "LOW": 0,
                                   "total": 0, "code": 0, "behavioral": 0}
        for f in self.findings:
            counts[f.severity] = counts.get(f.severity, 0) + 1
            counts[f.category] = counts.get(f.category, 0) + 1
            counts["total"] += 1
        return counts

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "files_scanned": self.files_scanned,
            "summary": self.summary,
            "findings": [f.to_dict() for f in self.findings],
        }

    def to_markdown(self) -> str:
        s = self.summary
        lines = [
            f"## Bug Hunt: {self.path}",
            f"{self.files_scanned} file(s) scanned — "
            f"{s['total']} finding(s): "
            f"{s['HIGH']} HIGH  {s['MEDIUM']} MEDIUM  {s['LOW']} LOW",
            "",
        ]
        if not self.findings:
            lines.append("No issues found.")
            return "\n".join(lines)

        for sev in ("HIGH", "MEDIUM", "LOW"):
            group = [f for f in self.findings if f.severity == sev]
            if not group:
                continue
            lines.append(f"### {sev}")
            for f in group:
                lines.append(
                    f"- **{f.rule_key}** `{f.file}:{f.line_no}`")
                lines.append(f"  {f.description}")
                lines.append(f"  *Fix:* {f.fix}")
                if f.snippet:
                    lines.append(f"  ```c\n  {f.snippet}\n  ```")
                lines.append("")
        return "\n".join(lines)


# ── ISR range detection ───────────────────────────────────────────────────────

def _isr_line_set(lines: list[str]) -> set[int]:
    """Return the set of 1-based line numbers that are inside an IRQHandler body."""
    inside: set[int] = set()
    depth = 0
    in_isr = False
    isr_depth = 0
    pending = False

    for i, line in enumerate(lines, 1):
        if _ISR_RE.search(line):
            pending = True

        opens  = line.count("{")
        closes = line.count("}")

        if opens and pending:
            in_isr = True
            isr_depth = depth      # depth BEFORE opens = function scope level
            pending = False

        depth += opens

        if in_isr:
            inside.add(i)

        depth -= closes

        if in_isr and depth <= isr_depth:
            in_isr = False

    return inside


# ── switch / default tracking ─────────────────────────────────────────────────

def _switch_findings(lines: list[str], path: str) -> list[BugFinding]:
    """Scan for switch() blocks that have no default: case."""
    findings: list[BugFinding] = []
    depth = 0
    # stack entries: [switch_lineno, depth_after_open, has_default]
    stack: list[list] = []

    for i, line in enumerate(lines, 1):
        s = line.strip()
        if s.startswith(("//", "/*", "*", "#")):
            opens  = 0
            closes = 0
        else:
            opens  = line.count("{")
            closes = line.count("}")

        depth += opens

        if _SWITCH_RE.search(line) and not s.startswith(("//", "/*", "*")):
            stack.append([i, depth, False])

        if stack and _DEFAULT_RE.search(line) and not s.startswith(("//", "/*", "*")):
            stack[-1][2] = True

        depth -= closes

        # Close switches whose opening brace is now unmatched
        while stack and depth < stack[-1][1]:
            sw = stack.pop()
            if not sw[2]:
                pat = PATTERN_MAP.get("SWITCH_NO_DEFAULT")
                findings.append(BugFinding(
                    file=path,
                    line_no=sw[0],
                    rule_key="SWITCH_NO_DEFAULT",
                    severity="LOW",
                    description=(
                        "switch statement has no default: case — unexpected values "
                        "fall through silently"
                    ),
                    fix=(
                        "Add default: break;  or  default: __builtin_unreachable();  "
                        "to make intent explicit."
                    ),
                    snippet=lines[sw[0] - 1].strip()[:120],
                ))

    return findings


# ── combinatorial behavioral scanner ─────────────────────────────────────────
# These checks require looking at the whole file — a missing pattern somewhere
# in combination with a present pattern elsewhere.  Single-line matching can't
# catch them.

_STRIP_COMMENT_RE = re.compile(r'//.*')


def _behavioral_findings(lines: list[str], path: str) -> list[BugFinding]:
    """File-level silent-behavior checks: combinations of present/absent patterns."""
    findings: list[BugFinding] = []
    # Strip single-line comments so patterns like __HAL_RCC_DMA1_CLK_ENABLE
    # mentioned inside a comment don't count as "present".
    stripped_lines = [_STRIP_COMMENT_RE.sub("", ln) for ln in lines]
    text = "\n".join(stripped_lines)

    def _first_line(rx: re.Pattern) -> tuple[int, str]:
        for i, (ln, sl) in enumerate(zip(lines, stripped_lines), 1):
            if rx.search(sl):
                return i, ln.strip()[:120]
        return 0, ""

    # ── ADC started but never calibrated ─────────────────────────────────────
    if ADC_START_RE.search(text) and not ADC_CALIB_RE.search(text):
        lineno, snippet = _first_line(ADC_START_RE)
        pat = PATTERN_MAP["ADC_MISSING_CALIBRATION"]
        findings.append(BugFinding(
            file=path, line_no=lineno, rule_key="ADC_MISSING_CALIBRATION",
            severity=pat.severity, description=pat.description, fix=pat.fix,
            snippet=snippet, category="behavioral",
        ))

    # ── GPIO input configured as floating (no pull anywhere near the init) ───
    if GPIO_INPUT_RE.search(text) and GPIO_NOPULL_RE.search(text):
        # Confirm they appear in the same init block (within 10 lines of each other)
        for i, ln in enumerate(stripped_lines):
            if GPIO_INPUT_RE.search(ln):
                window = stripped_lines[max(0, i - 5): i + 10]
                if any(GPIO_NOPULL_RE.search(wl) for wl in window):
                    pat = PATTERN_MAP["GPIO_FLOATING_INPUT"]
                    findings.append(BugFinding(
                        file=path, line_no=i + 1, rule_key="GPIO_FLOATING_INPUT",
                        severity=pat.severity, description=pat.description, fix=pat.fix,
                        snippet=lines[i].strip()[:120], category="behavioral",
                    ))
                    break   # one finding per file is enough

    # ── DMA initialised but RCC clock never enabled ──────────────────────────
    if DMA_INIT_RE.search(text) and not RCC_DMA_RE.search(text):
        lineno, snippet = _first_line(DMA_INIT_RE)
        pat = PATTERN_MAP["DMA_CLOCK_NOT_ENABLED"]
        findings.append(BugFinding(
            file=path, line_no=lineno, rule_key="DMA_CLOCK_NOT_ENABLED",
            severity=pat.severity, description=pat.description, fix=pat.fix,
            snippet=snippet, category="behavioral",
        ))

    # ── GPIO reset immediately followed by HAL communication (no delay) ──────
    prev_reset_line = 0
    for i, ln in enumerate(stripped_lines, 1):
        if _GPIO_RESET_RE.search(ln):
            prev_reset_line = i
        elif prev_reset_line and (i - prev_reset_line) <= 3:
            if _HAL_COMM_RE.search(ln):
                pat = PATTERN_MAP["RESET_WITHOUT_DELAY"]
                findings.append(BugFinding(
                    file=path, line_no=prev_reset_line,
                    rule_key="RESET_WITHOUT_DELAY",
                    severity=pat.severity, description=pat.description, fix=pat.fix,
                    snippet=lines[prev_reset_line - 1].strip()[:120],
                    category="behavioral",
                ))
                prev_reset_line = 0   # one finding per reset/comm pair

    return findings


# ── file scanner ──────────────────────────────────────────────────────────────

def scan_file(path: Path) -> list[BugFinding]:
    """Scan one C/C++ source file and return all findings."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []

    lines   = text.splitlines()
    isr_set = _isr_line_set(lines)
    findings: list[BugFinding] = []
    depth   = 0

    for lineno, line in enumerate(lines, 1):
        s = line.strip()
        opens  = line.count("{")
        closes = line.count("}")
        depth += opens

        in_isr = lineno in isr_set
        for key in match_line(line, in_isr, depth):
            pat = PATTERN_MAP[key]
            findings.append(BugFinding(
                file=str(path),
                line_no=lineno,
                rule_key=key,
                severity=pat.severity,
                description=pat.description,
                fix=pat.fix,
                snippet=s[:120],
                category=pat.category,
            ))

        depth -= closes

    findings.extend(_switch_findings(lines, str(path)))
    findings.extend(_behavioral_findings(lines, str(path)))
    return findings


# ── directory scanner ─────────────────────────────────────────────────────────

def _walk(root: Path, exts: set[str]) -> Iterator[Path]:
    for p in root.rglob("*"):
        if p.is_file() and p.suffix.lower() in exts:
            yield p


def scan_directory(
    path: str | Path,
    extensions: set[str] | None = None,
    max_files: int = 500,
) -> BugHuntResult:
    """Scan a directory (or single file) for embedded C/C++ bug patterns."""
    root = Path(path)
    exts = extensions or C_EXTENSIONS

    if root.is_file():
        findings = scan_file(root)
        return BugHuntResult(path=str(root), files_scanned=1, findings=findings)

    if not root.is_dir():
        return BugHuntResult(path=str(root), files_scanned=0, findings=[])

    all_findings: list[BugFinding] = []
    count = 0
    for fp in _walk(root, exts):
        if count >= max_files:
            break
        all_findings.extend(scan_file(fp))
        count += 1

    # Sort: code safety first, then behavioral; within each group HIGH → LOW
    _sev   = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    _cat   = {"code": 0, "behavioral": 1}
    all_findings.sort(
        key=lambda f: (_cat.get(f.category, 9), _sev.get(f.severity, 3), f.file, f.line_no)
    )

    return BugHuntResult(path=str(root), files_scanned=count, findings=all_findings)
