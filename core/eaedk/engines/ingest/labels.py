"""Human-readable labels for fact keys (v2.3.1). The report and query engine show these to the
user — never a raw database key."""
from __future__ import annotations

FACT_LABELS = {
    "flash_base": "Flash start address",
    "flash_bytes": "Flash size",
    "ram_base": "RAM start address",
    "ram_bytes": "RAM size",
    "sysclk_max_hz": "Maximum system clock",
    "vtor_offset": "Vector table offset",
}


def label_for(key: str) -> str:
    """Plain-English label for a fact key, falling back to a tidied version of the key."""
    if key in FACT_LABELS:
        return FACT_LABELS[key]
    return key.replace("_", " ").replace(" bytes", " size").replace(" base", " start address")


# Architecture choices for the unknown-board on-ramp (friendly label -> EAEDK arch string).
ARCH_CHOICES = [
    ("Cortex-M0", "arm-cortex-m0"), ("Cortex-M0+", "arm-cortex-m0plus"),
    ("Cortex-M3", "arm-cortex-m3"), ("Cortex-M4", "arm-cortex-m4"),
    ("Cortex-M7", "arm-cortex-m7"), ("Cortex-A7", "arm-cortex-a7"),
    ("Cortex-A53", "arm-cortex-a53"), ("Xtensa-LX6", "xtensa-lx6"),
    ("AVR", "avr"), ("other", "unknown"),
]


def normalize_arch(s: str) -> str:
    """Accept a friendly label, a short form, or a full arch string -> EAEDK arch string."""
    low = (s or "").strip().lower().replace(" ", "")
    for label, arch in ARCH_CHOICES:
        if low in (label.lower().replace(" ", ""), arch, arch.replace("arm-", ""),
                   arch.replace("arm-cortex-", "")):
            return arch
    if low.startswith("cortex-") or low.startswith("cortex"):
        return "arm-" + low if not low.startswith("arm-") else low
    return s or "unknown"
