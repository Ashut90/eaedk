"""Host toolchain detection — probe what's actually installed.

The only impure part of the engine (it shells out). Detection is explicit (`toolchain detect`)
and its results are stored; validation later reads the stored results deterministically. The
host probes go through injected ``which`` / ``runner`` callables so the whole thing is unit-
testable without any real tools installed.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass, asdict
from typing import Any, Callable

_VERSION_RE = re.compile(r"(\d+\.\d+(?:\.\d+)?)")

# Binaries to probe per kind. openocd legitimately serves as both debugger and flash tool.
PROBES: list[tuple[str, list[str]]] = [
    ("compiler", ["arm-none-eabi-gcc", "aarch64-linux-gnu-gcc", "arm-linux-gnueabihf-gcc",
                  "xtensa-esp32-elf-gcc", "riscv64-unknown-elf-gcc", "clang", "gcc"]),
    ("debugger", ["openocd", "st-util", "JLinkExe"]),
    ("flash_tool", ["openocd", "st-flash", "dfu-util", "esptool.py", "esptool", "picotool"]),
    ("build_system", ["cmake", "make", "meson", "ninja"]),
    ("sdk", ["idf.py", "west", "bitbake", "pioasm"]),
]
# Tools that hang or have no safe --version: record presence only.
_PRESENCE_ONLY = {"JLinkExe", "pioasm"}


@dataclass
class DetectedComponent:
    kind: str
    name: str
    version: str | None
    target_triple: str | None
    path: str | None
    raw: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _default_runner(cmd: list[str], timeout: float = 5.0) -> str | None:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return ((p.stdout or "") + (p.stderr or "")).strip()
    except (OSError, subprocess.SubprocessError):
        return None


def _parse_version(text: str | None) -> str | None:
    if not text:
        return None
    m = _VERSION_RE.search(text)
    return m.group(1) if m else None


def detect_all(which: Callable[[str], str | None] = shutil.which,
               runner: Callable[[list[str]], str | None] = _default_runner
               ) -> list[DetectedComponent]:
    """Probe the host and return detected components. ``which``/``runner`` are injectable."""
    out: list[DetectedComponent] = []
    seen: set[tuple[str, str]] = set()
    for kind, binaries in PROBES:
        for binary in binaries:
            path = which(binary)
            if not path or (kind, binary) in seen:
                continue
            seen.add((kind, binary))
            version, triple, raw = None, None, None
            if binary not in _PRESENCE_ONLY:
                raw = runner([binary, "--version"])
                version = _parse_version(raw)
            if kind == "compiler":
                triple = runner([binary, "-dumpmachine"])
                triple = triple.strip() if triple else None
            out.append(DetectedComponent(kind=kind, name=binary, version=version,
                                         target_triple=triple, path=path, raw=raw))
    return out
