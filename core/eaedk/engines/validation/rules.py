"""Validation Engine — the deterministic trust core (spec §2).

Each rule is a pure function ``ctx -> ValidationResult`` returning PASS / FAIL / UNKNOWN.
Rules never call the LLM and never invent a missing input. UNKNOWN means "insufficient
verified data" and is a hard blocker, not a soft pass (spec §7 Q3).

Feasibility (computed by the orchestrator over the run results):
  any FAIL                       -> not_feasible
  else any UNKNOWN on an ENGAGED rule -> blocked
  else                           -> feasible

A rule is "engaged" when the engineer has supplied at least one of its ``user_inputs``
(or the rule declares none). This distinguishes "not started" (surfaced as Missing
Information) from "started but unverified" (blocks).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from ...context import as_int

PASS, FAIL, UNKNOWN = "PASS", "FAIL", "UNKNOWN"

FLASH_MARGIN = 0.10
RAM_MARGIN = 0.10


@dataclass
class ValidationResult:
    check: str
    status: str
    reason: str
    severity_on_fail: str
    inputs_used: dict[str, Any] = field(default_factory=dict)
    engaged: bool = True
    citations: list[int] = field(default_factory=list)
    teach: str = ""            # one-line "why it matters / what to do" (mentor layer)
    gating: bool = True        # whether this result can gate feasibility


@dataclass
class _Rule:
    key: str
    func: Callable[[dict[str, Any]], ValidationResult]
    goals: tuple[str, ...] | None  # None = all goals
    user_inputs: tuple[str, ...]   # engineer-provided keys used for "engaged"
    severity: str


RULES: dict[str, _Rule] = {}


def rule(key: str, goals: tuple[str, ...] | None, user_inputs: tuple[str, ...], severity: str):
    def deco(func: Callable[[dict[str, Any]], ValidationResult]):
        RULES[key] = _Rule(key, func, goals, user_inputs, severity)
        return func
    return deco


# --- helpers ---------------------------------------------------------------

def _ints(ctx: dict[str, Any], *keys: str) -> tuple[list[int | None], list[str]]:
    vals = [as_int(ctx.get(k)) for k in keys]
    missing = [k for k, v in zip(keys, vals) if v is None]
    return vals, missing


def _region(ctx: dict[str, Any], key: str) -> tuple[int, int] | None:
    r = ctx.get(key)
    if not isinstance(r, dict):
        return None
    base, size = as_int(r.get("base")), as_int(r.get("size"))
    if base is None or size is None:
        return None
    return base, size


def _overlap(a: tuple[int, int], b: tuple[int, int]) -> bool:
    (ab, asz), (bb, bsz) = a, b
    return ab < bb + bsz and bb < ab + asz


# --- rules -----------------------------------------------------------------

@rule("FLASH_CAPACITY", None, ("estimated_image_size",), "HIGH")
def flash_capacity(ctx):
    (size, flash), missing = _ints(ctx, "estimated_image_size", "board.flash_bytes")
    used = {"estimated_image_size": size, "board.flash_bytes": flash}
    if missing:
        return ValidationResult("FLASH_CAPACITY", UNKNOWN, f"missing {missing}", "HIGH", used)
    margin = float(ctx.get("flash_margin", FLASH_MARGIN))
    pct = size / flash * 100 if flash else 0
    if size > flash:
        return ValidationResult("FLASH_CAPACITY", FAIL,
                                f"image {size} B exceeds flash {flash} B ({pct:.1f}%)", "HIGH", used)
    note = "" if size <= flash * (1 - margin) else f" — within {int(margin*100)}% reserve band"
    return ValidationResult("FLASH_CAPACITY", PASS,
                            f"image {size} B uses {pct:.1f}% of flash{note}", "HIGH", used)


@rule("RAM_BUDGET", None, ("stack_size", "heap_size", "static_size"), "HIGH")
def ram_budget(ctx):
    (stack, heap, static, ram), missing = _ints(
        ctx, "stack_size", "heap_size", "static_size", "board.ram_bytes")
    used = {"stack_size": stack, "heap_size": heap, "static_size": static, "board.ram_bytes": ram}
    if missing:
        return ValidationResult("RAM_BUDGET", UNKNOWN, f"missing {missing}", "HIGH", used)
    total = stack + heap + static
    margin = float(ctx.get("ram_margin", RAM_MARGIN))
    pct = total / ram * 100 if ram else 0
    if total > ram:
        return ValidationResult("RAM_BUDGET", FAIL,
                                f"RAM use {total} B exceeds {ram} B ({pct:.1f}%)", "HIGH", used)
    note = "" if total <= ram * (1 - margin) else f" — within {int(margin*100)}% reserve band"
    return ValidationResult("RAM_BUDGET", PASS,
                            f"RAM use {total} B is {pct:.1f}% of {ram} B{note}", "HIGH", used)


def _vt_align(ctx: dict[str, Any]) -> int:
    """Minimum vector-table (VTOR) alignment, by architecture.

    ARMv6-M (Cortex-M0/M0+) requires 256-byte alignment; ARMv7-M (M3/M4/M7) is commonly
    512 to cover larger vector tables. Overridable via ``vector_table_align``.
    """
    override = as_int(ctx.get("vector_table_align"))
    if override:
        return override
    arch = (ctx.get("soc.arch") or "").lower()
    if any(t in arch for t in ("m0", "m0plus", "m0+")):
        return 256
    if "cortex-m" in arch:
        return 512
    return 256


@rule("VECTOR_TABLE_PLACEMENT", ("bootloader", "bare_metal_app"), ("vector_table_addr",), "HIGH")
def vector_table_placement(ctx):
    (addr, base, size), missing = _ints(
        ctx, "vector_table_addr", "board.flash_base", "board.flash_bytes")
    used = {"vector_table_addr": addr, "board.flash_base": base, "board.flash_bytes": size}
    if missing:
        return ValidationResult("VECTOR_TABLE_PLACEMENT", UNKNOWN, f"missing {missing}", "HIGH", used)
    if not (base <= addr < base + size):
        return ValidationResult("VECTOR_TABLE_PLACEMENT", FAIL,
                                f"0x{addr:08X} outside flash [0x{base:08X},0x{base+size:08X})",
                                "HIGH", used)
    align = _vt_align(ctx)
    if addr % align != 0:
        return ValidationResult("VECTOR_TABLE_PLACEMENT", FAIL,
                                f"0x{addr:08X} not {align}-byte aligned (VTOR)", "HIGH", used)
    return ValidationResult("VECTOR_TABLE_PLACEMENT", PASS,
                            f"0x{addr:08X} within flash and {align}-byte aligned", "HIGH", used)


@rule("BOOTLOADER_APP_NO_OVERLAP", ("bootloader", "ota"), ("bl_region", "app_region"), "HIGH")
def bootloader_app_no_overlap(ctx):
    bl, app = _region(ctx, "bl_region"), _region(ctx, "app_region")
    (base, size), _ = _ints(ctx, "board.flash_base", "board.flash_bytes")
    used = {"bl_region": ctx.get("bl_region"), "app_region": ctx.get("app_region")}
    if bl is None or app is None:
        return ValidationResult("BOOTLOADER_APP_NO_OVERLAP", UNKNOWN,
                                "missing bl_region/app_region", "HIGH", used)
    if base is not None and size is not None:
        flash = (base, size)
        for name, r in (("bl_region", bl), ("app_region", app)):
            if not (base <= r[0] and r[0] + r[1] <= base + size):
                return ValidationResult("BOOTLOADER_APP_NO_OVERLAP", FAIL,
                                        f"{name} 0x{r[0]:08X}+0x{r[1]:X} not within flash",
                                        "HIGH", used)
    if _overlap(bl, app):
        return ValidationResult("BOOTLOADER_APP_NO_OVERLAP", FAIL,
                                "bootloader and app regions overlap", "HIGH", used)
    return ValidationResult("BOOTLOADER_APP_NO_OVERLAP", PASS,
                            "bootloader and app regions disjoint and within flash", "HIGH", used)


def _partitions(ctx):
    parts = ctx.get("partitions")
    if not isinstance(parts, list) or not parts:
        return None
    out = []
    for p in parts:
        base, size = as_int(p.get("base")), as_int(p.get("size"))
        if base is None or size is None:
            return None
        out.append({"name": p.get("name"), "role": p.get("role"), "base": base, "size": size})
    return out


@rule("PARTITION_LAYOUT_FITS", ("ota", "linux"), ("partitions", "primary_storage_bytes"), "HIGH")
def partition_layout_fits(ctx):
    parts = _partitions(ctx)
    (storage,), missing = _ints(ctx, "primary_storage_bytes")
    used = {"partitions": ctx.get("partitions"), "primary_storage_bytes": storage}
    if parts is None or storage is None:
        return ValidationResult("PARTITION_LAYOUT_FITS", UNKNOWN,
                                "missing partitions/primary_storage_bytes", "HIGH", used)
    total = sum(p["size"] for p in parts)
    for p in parts:
        if p["base"] < 0 or p["base"] + p["size"] > storage:
            return ValidationResult("PARTITION_LAYOUT_FITS", FAIL,
                                    f"partition {p['name']} exceeds storage {storage} B", "HIGH", used)
    if total > storage:
        return ValidationResult("PARTITION_LAYOUT_FITS", FAIL,
                                f"partitions total {total} B exceed storage {storage} B", "HIGH", used)
    return ValidationResult("PARTITION_LAYOUT_FITS", PASS,
                            f"partitions total {total} B fit storage {storage} B", "HIGH", used)


@rule("PARTITION_NO_OVERLAP", ("ota", "linux"), ("partitions",), "HIGH")
def partition_no_overlap(ctx):
    parts = _partitions(ctx)
    used = {"partitions": ctx.get("partitions")}
    if parts is None:
        return ValidationResult("PARTITION_NO_OVERLAP", UNKNOWN, "missing partitions", "HIGH", used)
    for i in range(len(parts)):
        for j in range(i + 1, len(parts)):
            a, b = parts[i], parts[j]
            if _overlap((a["base"], a["size"]), (b["base"], b["size"])):
                return ValidationResult("PARTITION_NO_OVERLAP", FAIL,
                                        f"{a['name']} overlaps {b['name']}", "HIGH", used)
    return ValidationResult("PARTITION_NO_OVERLAP", PASS, "no partition overlaps", "HIGH", used)


@rule("PARTITION_AB_SYMMETRY", ("ota",), ("partitions",), "MEDIUM")
def partition_ab_symmetry(ctx):
    parts = _partitions(ctx)
    used = {"partitions": ctx.get("partitions")}
    if parts is None:
        return ValidationResult("PARTITION_AB_SYMMETRY", UNKNOWN, "missing partitions", "MEDIUM", used)
    a = next((p for p in parts if p["role"] == "slot_a"), None)
    b = next((p for p in parts if p["role"] == "slot_b"), None)
    if a is None or b is None:
        return ValidationResult("PARTITION_AB_SYMMETRY", UNKNOWN,
                                "slot_a/slot_b roles not both present", "MEDIUM", used)
    if a["size"] != b["size"]:
        return ValidationResult("PARTITION_AB_SYMMETRY", FAIL,
                                f"slot_a {a['size']} B != slot_b {b['size']} B", "MEDIUM", used)
    return ValidationResult("PARTITION_AB_SYMMETRY", PASS, "A/B slots are symmetric", "MEDIUM", used)


@rule("RECOVERY_PRESENT", ("ota",), ("partitions",), "HIGH")
def recovery_present(ctx):
    parts = _partitions(ctx)
    used = {"partitions": ctx.get("partitions")}
    if parts is None:
        return ValidationResult("RECOVERY_PRESENT", UNKNOWN, "missing partitions", "HIGH", used)
    if any(p["role"] in ("recovery", "slot_b") for p in parts):
        return ValidationResult("RECOVERY_PRESENT", PASS, "recovery/slot_b partition present", "HIGH", used)
    return ValidationResult("RECOVERY_PRESENT", FAIL, "no recovery or slot_b partition", "HIGH", used)


@rule("LOAD_ADDR_CONFLICT", ("uboot", "linux"),
      ("kernel_load_addr", "dtb_load_addr"), "HIGH")
def load_addr_conflict(ctx):
    (kern, dtb, ddr_base, ddr_bytes), missing = _ints(
        ctx, "kernel_load_addr", "dtb_load_addr", "ddr_base", "ddr_bytes")
    used = {"kernel_load_addr": kern, "dtb_load_addr": dtb,
            "ddr_base": ddr_base, "ddr_bytes": ddr_bytes}
    if missing:
        return ValidationResult("LOAD_ADDR_CONFLICT", UNKNOWN, f"missing {missing}", "HIGH", used)
    ddr_end = ddr_base + ddr_bytes
    for name, a in (("kernel_load_addr", kern), ("dtb_load_addr", dtb)):
        if not (ddr_base <= a < ddr_end):
            return ValidationResult("LOAD_ADDR_CONFLICT", FAIL,
                                    f"{name} 0x{a:08X} outside DDR [0x{ddr_base:08X},0x{ddr_end:08X})",
                                    "HIGH", used)
    init = _region(ctx, "ddr_init_region")
    if init is not None:
        ib, isz = init
        for name, a in (("kernel_load_addr", kern), ("dtb_load_addr", dtb)):
            if ib <= a < ib + isz:
                return ValidationResult("LOAD_ADDR_CONFLICT", FAIL,
                                        f"{name} 0x{a:08X} inside DDR init region", "HIGH", used)
    if kern == dtb:
        return ValidationResult("LOAD_ADDR_CONFLICT", FAIL,
                                "kernel and DTB load addresses identical", "HIGH", used)
    return ValidationResult("LOAD_ADDR_CONFLICT", PASS,
                            "kernel/DTB load addresses within DDR and non-conflicting", "HIGH", used)


@rule("DDR_TIMING_VERIFIED", ("uboot",), ("ddr_timing_verified",), "HIGH")
def ddr_timing_verified(ctx):
    used = {"ddr_timing_verified": ctx.get("ddr_timing_verified"),
            "ddr_timing_present": ctx.get("ddr_timing_present")}
    if "ddr_timing_verified" not in ctx:
        return ValidationResult("DDR_TIMING_VERIFIED", UNKNOWN, "no DDR timing data", "HIGH", used)
    if as_int(ctx.get("ddr_timing_verified")):
        return ValidationResult("DDR_TIMING_VERIFIED", PASS,
                                "DDR timing human-verified from datasheet", "HIGH", used)
    if as_int(ctx.get("ddr_timing_present")):
        return ValidationResult("DDR_TIMING_VERIFIED", FAIL,
                                "DDR timing present but not human-verified", "HIGH", used)
    return ValidationResult("DDR_TIMING_VERIFIED", UNKNOWN,
                            "DDR timing not verified from datasheet", "HIGH", used)


@rule("BOOT_FLOW_CONSISTENCY", ("uboot", "linux"),
      ("bootloader_load_addr", "kernel_load_addr", "init_addr"), "HIGH")
def boot_flow_consistency(ctx):
    (bl, kern, init), missing = _ints(
        ctx, "bootloader_load_addr", "kernel_load_addr", "init_addr")
    used = {"bootloader_load_addr": bl, "kernel_load_addr": kern, "init_addr": init}
    if missing:
        return ValidationResult("BOOT_FLOW_CONSISTENCY", UNKNOWN, f"missing {missing}", "HIGH", used)
    addrs = [("bootloader", bl), ("kernel", kern), ("init", init)]
    for i in range(len(addrs)):
        for j in range(i + 1, len(addrs)):
            if addrs[i][1] == addrs[j][1]:
                return ValidationResult("BOOT_FLOW_CONSISTENCY", FAIL,
                                        f"{addrs[i][0]} and {addrs[j][0]} share an address",
                                        "HIGH", used)
    return ValidationResult("BOOT_FLOW_CONSISTENCY", PASS,
                            "bootloader/kernel/init addresses are distinct", "HIGH", used)


@rule("CONSOLE_UART_DEFINED", ("uboot", "linux", "bare_metal_app"), ("console_uart",), "MEDIUM")
def console_uart_defined(ctx):
    cu = ctx.get("console_uart")
    used = {"console_uart": cu, "stdout_path": ctx.get("stdout_path")}
    if not cu:
        return ValidationResult("CONSOLE_UART_DEFINED", UNKNOWN, "console_uart not set", "MEDIUM", used)
    if ctx.get("_goal") == "linux" and not ctx.get("stdout_path"):
        return ValidationResult("CONSOLE_UART_DEFINED", UNKNOWN,
                                "stdout_path required for Linux console", "MEDIUM", used)
    return ValidationResult("CONSOLE_UART_DEFINED", PASS, f"console = {cu}", "MEDIUM", used)


def _arch_family(arch: str | None) -> str | None:
    if not arch:
        return None
    return arch.lower().split("-")[0]


@rule("TOOLCHAIN_ARCH_MATCH", None, ("toolchain_arch",), "HIGH")
def toolchain_arch_match(ctx):
    ta, soc = ctx.get("toolchain_arch"), ctx.get("soc.arch")
    used = {"toolchain_arch": ta, "soc.arch": soc}
    if not ta:
        return ValidationResult("TOOLCHAIN_ARCH_MATCH", UNKNOWN, "toolchain_arch not set", "HIGH", used)
    fa, fs = _arch_family(ta), _arch_family(soc)
    if fs is None:
        return ValidationResult("TOOLCHAIN_ARCH_MATCH", UNKNOWN, "soc.arch unknown", "HIGH", used)
    if fa == fs:
        return ValidationResult("TOOLCHAIN_ARCH_MATCH", PASS,
                                f"toolchain {fa} matches SoC {fs}", "HIGH", used)
    return ValidationResult("TOOLCHAIN_ARCH_MATCH", FAIL,
                            f"toolchain {fa} != SoC {fs}", "HIGH", used)


@rule("SDK_HOST_OS_MATCH", None, ("host_os",), "MEDIUM")
def sdk_host_os_match(ctx):
    host, req = ctx.get("host_os"), ctx.get("sdk_required_host_os")
    used = {"host_os": host, "sdk_required_host_os": req}
    if not host:
        return ValidationResult("SDK_HOST_OS_MATCH", UNKNOWN, "host_os not set", "MEDIUM", used)
    if not req:
        return ValidationResult("SDK_HOST_OS_MATCH", PASS, "no SDK host-OS requirement", "MEDIUM", used)
    if req.lower() in host.lower() or host.lower() in req.lower():
        return ValidationResult("SDK_HOST_OS_MATCH", PASS, f"host {host} satisfies {req}", "MEDIUM", used)
    return ValidationResult("SDK_HOST_OS_MATCH", FAIL, f"host {host} != required {req}", "MEDIUM", used)


@rule("DRIVER_COMPATIBLE_STRING", ("driver",), ("dtb_compatible",), "MEDIUM")
def driver_compatible_string(ctx):
    cs = ctx.get("dtb_compatible")
    used = {"dtb_compatible": cs}
    if not cs:
        return ValidationResult("DRIVER_COMPATIBLE_STRING", UNKNOWN,
                                "dtb_compatible not set", "MEDIUM", used)
    return ValidationResult("DRIVER_COMPATIBLE_STRING", PASS, f"compatible = {cs}", "MEDIUM", used)


@rule("REGISTER_MAP_PRESENT", ("driver",), ("register_facts",), "HIGH")
def register_map_present(ctx):
    regs = ctx.get("register_facts")
    used = {"register_facts": regs}
    if not isinstance(regs, list) or not regs:
        return ValidationResult("REGISTER_MAP_PRESENT", UNKNOWN, "no register facts", "HIGH", used)
    if any(as_int(r.get("verified")) for r in regs):
        return ValidationResult("REGISTER_MAP_PRESENT", PASS, "human-verified register map present", "HIGH", used)
    return ValidationResult("REGISTER_MAP_PRESENT", FAIL,
                            "register facts present but none human-verified", "HIGH", used)


@rule("PINMUX_CONFLICT", None, ("pin_assignments",), "HIGH")
def pinmux_conflict(ctx):
    """Detect a physical pin claimed by more than one signal/function (spec §2.3).

    ``pin_assignments``: list of ``{pin, signal}`` (optional ``function``). A pin mapped to
    two distinct signals is a mux conflict.
    """
    pa = ctx.get("pin_assignments")
    used = {"pin_assignments": pa}
    if not isinstance(pa, list) or not pa:
        return ValidationResult("PINMUX_CONFLICT", UNKNOWN, "no pin assignments provided", "HIGH", used)
    pin_to_signals: dict[Any, set] = {}
    for a in pa:
        pin, sig = a.get("pin"), a.get("signal")
        if pin is None or sig is None:
            return ValidationResult("PINMUX_CONFLICT", UNKNOWN,
                                    "assignment missing pin/signal", "HIGH", used)
        pin_to_signals.setdefault(pin, set()).add(sig)
    conflicts = {p: sigs for p, sigs in pin_to_signals.items() if len(sigs) > 1}
    if conflicts:
        pin = sorted(conflicts, key=str)[0]
        return ValidationResult("PINMUX_CONFLICT", FAIL,
                                f"pin {pin} assigned to multiple signals: {sorted(conflicts[pin])}",
                                "HIGH", used)
    return ValidationResult("PINMUX_CONFLICT", PASS,
                            f"{len(pin_to_signals)} pins assigned, no mux conflicts", "HIGH", used)


@rule("POWER_SEQUENCE", None, ("power_rails",), "HIGH")
def power_sequence(ctx):
    """Validate power-rail power-on ordering against declared dependencies (spec §2.3).

    ``power_rails``: list of ``{name, order, depends_on?}`` where ``order`` is the power-on
    index and ``depends_on`` names rail(s) that must be stable first. Each dependency must
    have a strictly smaller order; order indices must be distinct (no ambiguity).
    """
    rails = ctx.get("power_rails")
    used = {"power_rails": rails}
    if not isinstance(rails, list) or not rails:
        return ValidationResult("POWER_SEQUENCE", UNKNOWN, "no power rails provided", "HIGH", used)
    order_by_name: dict[str, int] = {}
    for r in rails:
        name, order = r.get("name"), as_int(r.get("order"))
        if name is None or order is None:
            return ValidationResult("POWER_SEQUENCE", UNKNOWN,
                                    "rail missing name/order", "HIGH", used)
        order_by_name[name] = order
    if len(set(order_by_name.values())) != len(order_by_name):
        return ValidationResult("POWER_SEQUENCE", FAIL,
                                "duplicate power-on order indices (ambiguous sequencing)",
                                "HIGH", used)
    for r in rails:
        dep = r.get("depends_on")
        deps = [dep] if isinstance(dep, str) else (dep or [])
        for d in deps:
            if d not in order_by_name:
                return ValidationResult("POWER_SEQUENCE", FAIL,
                                        f"rail {r['name']} depends on unknown rail {d}", "HIGH", used)
            if order_by_name[d] >= order_by_name[r["name"]]:
                return ValidationResult("POWER_SEQUENCE", FAIL,
                                        f"rail {r['name']} powers up before/with its dependency {d}",
                                        "HIGH", used)
    return ValidationResult("POWER_SEQUENCE", PASS,
                            f"{len(rails)} rails sequenced consistently", "HIGH", used)


# Per-rule teach (mentor layer): what the field is, units, where to find it, the consequence.
# Attached to any non-PASS result so a beginner is never left with a bare key name.
RULE_TEACH: dict[str, str] = {
    "FLASH_CAPACITY":
        "Checks the firmware fits in flash. Needs estimated_image_size (your compiled .bin "
        "size in bytes) and the board's flash size (datasheet memory map, e.g. STM32F411RE = "
        "524288). Without it, image-fit checks and export cannot run.",
    "RAM_BUDGET":
        "Checks stack + heap + statics fit in RAM. Needs stack_size/heap_size/static_size "
        "(bytes) and the board's RAM size (datasheet, e.g. STM32F411RE = 131072). Without it, "
        "a RAM overflow won't be caught until the board crashes.",
    "VECTOR_TABLE_PLACEMENT":
        "The interrupt vector table must sit at the start of flash and be aligned. Set "
        "vector_table_addr — for a simple app this is the flash base (e.g. 0x08000000 on STM32). "
        "Wrong placement means the MCU faults on the first interrupt.",
    "BOOTLOADER_APP_NO_OVERLAP":
        "Bootloader and application flash regions must not overlap. Provide bl_region and "
        "app_region as {base,size}. Overlap corrupts one image when you flash the other.",
    "PARTITION_LAYOUT_FITS":
        "All partitions must fit in storage. Provide partitions [{name,role,base,size}] and "
        "primary_storage_bytes. If they exceed storage, the layout won't flash.",
    "PARTITION_NO_OVERLAP":
        "Partitions must not overlap. Provide partitions [{name,role,base,size}]; overlapping "
        "ranges corrupt adjacent images.",
    "PARTITION_AB_SYMMETRY":
        "A/B OTA slots must be the same size so either can hold a full image. Give slot_a and "
        "slot_b equal sizes.",
    "RECOVERY_PRESENT":
        "Fail-safe OTA needs a recovery (or slot_b) partition to fall back to. Without one, a "
        "bad update bricks the device.",
    "LOAD_ADDR_CONFLICT":
        "Kernel/DTB load addresses must be inside DDR and clear of the DDR-init region. Needs "
        "kernel_load_addr, dtb_load_addr, ddr_base, ddr_bytes (datasheet). A conflict overwrites "
        "early-boot data.",
    "DDR_TIMING_VERIFIED":
        "DDR timing must be confirmed from the datasheet before first boot. Set "
        "ddr_timing_verified=1 once you've checked CL/tRCD/tRP in the TRM. Unverified DDR is "
        "unstable and corrupts memory intermittently.",
    "BOOT_FLOW_CONSISTENCY":
        "bootloader -> kernel -> init load addresses must form a clean, non-overlapping chain. "
        "Provide bootloader_load_addr, kernel_load_addr, init_addr.",
    "CONSOLE_UART_DEFINED":
        "The debug console UART carries printf/boot output. Set console_uart (e.g. USART2; for "
        "Linux also stdout_path). Without it you have no log to debug a failed boot.",
    "TOOLCHAIN_ARCH_MATCH":
        "Your compiler's target must match the board's CPU. Run `eaedk toolchain detect`, or set "
        "toolchain_arch. A mismatch produces firmware the chip cannot run.",
    "SDK_HOST_OS_MATCH":
        "Some SDKs require a specific host OS. Set host_os if your SDK is OS-specific (e.g. "
        "vendor tools that are Windows-only).",
    "PINMUX_CONFLICT":
        "Two signals must not be wired to the same physical pin. Provide pin_assignments "
        "[{pin,signal}]; a double-claimed pin means one peripheral silently won't work.",
    "POWER_SEQUENCE":
        "Power rails must come up in the required order. Provide power_rails "
        "[{name,order,depends_on}]. A bad sequence can damage the silicon or hang boot.",
    "DRIVER_COMPATIBLE_STRING":
        "A Linux driver binds via its DTB compatible string. Set dtb_compatible (e.g. "
        "\"wiznet,w5500\") matching the driver's of_match_table.",
    "REGISTER_MAP_PRESENT":
        "A driver needs the peripheral's register map, human-verified from the datasheet. Add "
        "register facts (ingest the datasheet, then confirm) before writing register code.",
}


# --- runner ----------------------------------------------------------------

def run_validations(ctx: dict[str, Any], goal_type: str,
                    only: list[str] | None = None) -> list[ValidationResult]:
    """Run every rule applicable to ``goal_type`` (or only the named rules)."""
    provided: set[str] = ctx.get("_provided", set())
    results: list[ValidationResult] = []
    for key, r in RULES.items():
        if only is not None and key not in only:
            continue
        if r.goals is not None and goal_type not in r.goals:
            continue
        res = r.func(ctx)
        res.engaged = (not r.user_inputs) or any(k in provided for k in r.user_inputs)
        if res.status != PASS and not res.teach:        # attach the mentor teach string
            res.teach = RULE_TEACH.get(res.check, "")
        results.append(res)
    return results


def feasibility(results: list[ValidationResult]) -> str:
    # Only gating results affect feasibility. Existing rules default gating=True, so behavior
    # is unchanged; non-HIGH toolchain checks set gating=False to surface without blocking.
    gating = [r for r in results if r.gating]
    if any(r.status == FAIL for r in gating):
        return "not_feasible"
    if any(r.status == UNKNOWN and r.engaged for r in gating):
        return "blocked"
    return "feasible"
