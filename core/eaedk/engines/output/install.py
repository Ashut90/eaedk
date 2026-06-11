"""Host-OS-aware install commands for the build/flash toolchain (Fix 1, v1.7.0).

A beginner who has never used a terminal cannot self-serve "install an ARM cross-compiler."
Given the host OS and the tools their board's profile requires, emit the concrete one-liner
(apt / brew) or a labelled download link — never just the tool name. Pure: no I/O, the host OS
is passed in so the generators stay golden-testable.
"""
from __future__ import annotations

# Per-tool package name by OS package manager. None -> no clean package; fall back to a link.
_APT = {
    "arm-none-eabi-gcc": "gcc-arm-none-eabi", "avr-gcc": "gcc-avr",
    "cmake": "cmake", "make": "build-essential", "ninja": "ninja-build",
    "openocd": "openocd", "st-flash": "stlink-tools", "dfu-util": "dfu-util",
    "gdb-multiarch": "gdb-multiarch",
}
_BREW = {
    "arm-none-eabi-gcc": "arm-none-eabi-gcc", "avr-gcc": "avr-gcc",
    "cmake": "cmake", "make": "make", "ninja": "ninja",
    "openocd": "openocd", "st-flash": "stlink", "dfu-util": "dfu-util",
}
# Tools with no clean package on a given platform -> a download link a beginner can click.
_LINKS = {
    "arm-none-eabi-gcc": "ARM GNU Toolchain — https://developer.arm.com/downloads/-/arm-gnu-toolchain-downloads",
    "avr-gcc": "AVR-GCC (via WinAVR / MSYS2) — https://github.com/ZakKemble/avr-gcc-build/releases",
    "cmake": "CMake — https://cmake.org/download/",
    "openocd": "OpenOCD — https://gnutoolchains.com/arm-eabi/openocd/",
    "st-flash": "stlink (st-flash) — https://github.com/stlink-org/stlink/releases",
    "dfu-util": "dfu-util — https://dfu-util.sourceforge.net/",
    "esptool.py": "esptool — https://docs.espressif.com/projects/esptool/",
    "esptool": "esptool — https://docs.espressif.com/projects/esptool/",
    "picotool": "picotool — https://github.com/raspberrypi/picotool",
    "xtensa-esp32-elf-gcc": "ESP-IDF (installs the Xtensa toolchain) — https://docs.espressif.com/projects/esp-idf/",
}


def normalize_os(system: str | None) -> str:
    """platform.system() -> one of 'linux' | 'macos' | 'windows' | 'other'."""
    s = (system or "").lower()
    if s.startswith("lin"):
        return "linux"
    if s.startswith("darwin") or s == "macos":
        return "macos"
    if s.startswith("win"):
        return "windows"
    return "other"


def install_block(host_os: str, tools: list[str]) -> list[str]:
    """Markdown lines telling the user how to install ``tools`` on ``host_os``.

    Deduplicates and preserves order. A tool with no clean package on this OS degrades to a
    labelled link line, so we never claim a package that doesn't exist.
    """
    seen: list[str] = []
    for t in tools:
        if t and t not in seen:
            seen.append(t)
    if not seen:
        return []
    os_key = normalize_os(host_os)

    if os_key in ("linux", "macos"):
        table, mgr, prefix = (_APT, "apt", "sudo apt install") if os_key == "linux" \
            else (_BREW, "brew", "brew install")
        pkgs = [table[t] for t in seen if t in table]
        links = [_LINKS[t] for t in seen if t not in table and t in _LINKS]
        out: list[str] = []
        label = "Ubuntu/Debian" if os_key == "linux" else "macOS (Homebrew)"
        if pkgs:
            out += [f"On **{label}**:", "```bash", f"{prefix} {' '.join(dict.fromkeys(pkgs))}",
                    "```"]
        for ln in links:
            out.append(f"- {ln}")
        return out

    if os_key == "windows":
        out = ["On **Windows**, download and install:"]
        for t in seen:
            out.append(f"- {_LINKS.get(t, t)}")
        return out

    # Unknown OS: list links, never guess a package manager.
    out = ["Install these tools for your OS:"]
    for t in seen:
        out.append(f"- {_LINKS.get(t, t)}")
    return out
