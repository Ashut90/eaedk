"""Embedded bug-pattern library — pure regex, no LLM, no I/O.

Each BugPattern has a ``key``, a ``severity`` (HIGH / MEDIUM / LOW), a human
``description``, and a ``fix`` hint.  ``match_line`` runs all patterns against
a single line of C/C++ source and returns the keys of every pattern that fires.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class BugPattern:
    key: str
    severity: str    # HIGH / MEDIUM / LOW
    description: str
    fix: str
    category: str = "code"       # "code" = crash/overflow risk  |  "behavioral" = silent wrong behavior


# ── compiled regexes ──────────────────────────────────────────────────────────

_HAL_DELAY_RE = re.compile(
    r'\bHAL_Delay\s*\(|\bosDelay\s*\(|\bvTaskDelay\s*\(|\bvTaskDelayUntil\s*\(')

_BLOCKING_IO_RE = re.compile(
    r'\b(printf|fprintf|puts|fputs|scanf|fgets|HAL_UART_Transmit)\s*\(')

_SPRINTF_RE = re.compile(r'(?<!\w)sprintf\s*\(')      # not snprintf / asprintf
_GETS_RE    = re.compile(r'(?<!\w)gets\s*\(')

# Standalone HAL peripheral call — starts the line (after whitespace) with HAL_<UPPER>...
# Excludes HAL_Delay / GPIO / NVIC / Tick / __HAL_ / Msp hooks (those return void).
_HAL_PERIPH_RE = re.compile(
    r'^\s*HAL_(?!'
    r'Delay\b|GPIO_Write|GPIO_Toggle|GPIO_Read|NVIC_|GetTick|IncTick|'
    r'MspInit|MspDeInit|PWREx_|__HAL_|RCC_OscConfig|RCC_ClockConfig|'
    r'StatusTypeDef|TIM_Base_Init|TIM_OC_Init|TIM_IC_Init'
    r')[A-Z][A-Za-z0-9_]+\s*\('
)
_HAL_CHECKED_RE = re.compile(r'\bif\s*\(|\bwhile\s*\(|\breturn\b|=\s*HAL_|\w\s*=\s*HAL_')

_MALLOC_RE  = re.compile(r'\b(malloc|calloc|realloc|pvPortMalloc)\s*\(')
_FREE_RE    = re.compile(r'\b(free|vPortFree)\s*\(')

_LARGE_ARRAY_RE = re.compile(
    r'\b(?:uint8_t|int8_t|char|uint16_t|int16_t|uint32_t|int32_t|int)\s+'
    r'\w+\s*\[\s*(\d+)\s*\]')

_TYPE_KEYWORD_RE = re.compile(
    r'^\s*(?:uint\d+_t|int\d+_t|char|bool|_Bool|int|long|short|unsigned|'
    r'volatile|static|register|const|float|double|struct|enum|union)\b')

# Assignment to a bare name (not a comparison, not a type declaration)
_BARE_ASSIGN_RE = re.compile(r'^\s*[a-zA-Z_]\w*\s*[+\-|&^]?=(?!=)')

_BUSY_WAIT_RE = re.compile(
    r'\bwhile\s*\(\s*!\s*\w+|\bwhile\s*\(\s*\w+\s*==\s*0\s*\)'
    r'|\bwhile\s*\(\s*0\s*==\s*\w+')

_MAGIC_ADDR_RE = re.compile(
    r'\*\s*\(\s*(?:volatile\s+)?\w[\w\s*]+\s*\*\s*\)\s*0x[0-9A-Fa-f]{4,}')

_STRNCPY_RE = re.compile(r'\bstrncpy\s*\(')

# ── behavioral / silent-bug regexes ───────────────────────────────────────────

# float f = intA / intB  → integer division, always truncates, looks right but wrong
# \w+ covers both identifiers and integer literals (100, 1000 etc)
# Excludes float literals (3.14f) because \w doesn't match '.'
_FLOAT_INT_DIV_RE = re.compile(
    r'^\s*(?:float|double)\s+\w+\s*=\s*\w+\s*/\s*\w+\s*;')

# uint8_t / uint16_t x = expr * expr  → silent overflow to 0
_NARROW_PRODUCT_RE = re.compile(
    r'^\s*(?:uint8_t|int8_t|uint16_t|int16_t)\s+\w+\s*=\s*\w+\s*\*\s*\w+')

# float/double compared with ==  →  equality rarely true due to rounding
_FLOAT_EQ_RE = re.compile(
    r'\b(?:float|double)\b[^=\n]*==(?!=)|==(?!=)[^=\n]*\b(?:float|double)\b')
# simpler: detect == or != where one side is a floating-point literal
_FLOAT_LITERAL_EQ_RE = re.compile(
    r'(?:==|!=)\s*\d+\.\d+|\d+\.\d+\s*(?:==|!=)')

# HAL_IWDG_Refresh / HAL_WWDG_Refresh inside an if() — may not always execute
_WDT_REFRESH_RE   = re.compile(r'\bHAL_(?:IWDG|WWDG)_Refresh\s*\(')
_WDT_IN_IF_RE     = re.compile(r'\bif\s*\(')   # combined with _WDT_REFRESH_RE in context

# GPIO open-drain output — if no pull-up the line stays low (device never responds)
_OPEN_DRAIN_RE    = re.compile(r'\bGPIO_MODE_OUTPUT_OD\b')

# Delay missing after GPIO reset of a peripheral  →  device startup time not respected
# Heuristic: GPIO_PIN_RESET on a non-LED pin immediately followed by a HAL comm call
_GPIO_RESET_RE    = re.compile(r'HAL_GPIO_WritePin\s*\([^)]*GPIO_PIN_RESET')
_HAL_COMM_RE      = re.compile(
    r'\bHAL_(?:SPI|I2C|UART|USART|CAN|USB|QSPI)_\w+\s*\(')

# File-level patterns (used by engine, not match_line)
ADC_START_RE      = re.compile(r'\bHAL_ADC_Start(?:_DMA|_IT)?\s*\(')
ADC_CALIB_RE      = re.compile(r'\bHAL_ADCEx_Calibration_Start\s*\(')
GPIO_INPUT_RE     = re.compile(r'\bGPIO_MODE_INPUT\b')
GPIO_NOPULL_RE    = re.compile(r'\bGPIO_NOPULL\b')
GPIO_PULLUP_RE    = re.compile(r'\bGPIO_PULLUP\b')
DMA_INIT_RE       = re.compile(r'\bHAL_DMA_Init\s*\(')
RCC_DMA_RE        = re.compile(r'__HAL_RCC_DMA\d_CLK_ENABLE\s*\(')


# ── pattern catalogue ─────────────────────────────────────────────────────────

PATTERNS: list[BugPattern] = [
    BugPattern(
        key="HAL_DELAY_IN_ISR",
        severity="HIGH",
        description="HAL_Delay / osDelay inside an interrupt handler — stalls the CPU and blocks all lower-priority IRQs",
        fix="Remove delays from ISRs. Set a flag and handle timing in the main loop, or use a hardware timer.",
    ),
    BugPattern(
        key="BLOCKING_IO_IN_ISR",
        severity="HIGH",
        description="Blocking I/O (printf / HAL_UART_Transmit) inside interrupt handler — stalls the IRQ and corrupts the output stream",
        fix="Buffer the data in a circular buffer and flush it from a non-ISR context (main loop or DMA TC callback).",
    ),
    BugPattern(
        key="SPRINTF_OVERFLOW",
        severity="HIGH",
        description="sprintf() has no length limit — adjacent memory overwritten when output exceeds buffer size",
        fix="Replace with snprintf(buf, sizeof(buf), fmt, ...) to enforce the buffer boundary.",
    ),
    BugPattern(
        key="GETS_UNSAFE",
        severity="HIGH",
        description="gets() is removed from C11 — always a buffer overflow because it has no length argument",
        fix="Use fgets(buf, sizeof(buf), stdin) instead.",
    ),
    BugPattern(
        key="HAL_RETURN_UNCHECKED",
        severity="MEDIUM",
        description="HAL peripheral function return value discarded — peripheral errors silently ignored",
        fix="Check the return: if (HAL_xxx(...) != HAL_OK) { Error_Handler(); }",
    ),
    BugPattern(
        key="MALLOC_HEAP",
        severity="MEDIUM",
        description="malloc/free in bare-metal firmware — heap fragmentation causes non-deterministic failures over time",
        fix="Use static allocation, stack buffers, or a fixed-size memory pool. Reserve heap for RTOS only if used.",
    ),
    BugPattern(
        key="LARGE_STACK_ARRAY",
        severity="MEDIUM",
        description="Large array allocated on the stack — risks stack overflow, especially in ISRs or small RTOS tasks",
        fix="Declare the buffer as static or global so it lives in BSS/data segment rather than on the stack.",
    ),
    BugPattern(
        key="ISR_FLAG_NO_VOLATILE",
        severity="MEDIUM",
        description="Variable assigned inside ISR — without volatile the compiler may cache it in a register and the main loop never sees the update",
        fix="Declare the shared variable as: volatile uint8_t flagReady;  (or use an atomic type if available).",
    ),
    BugPattern(
        key="BUSY_WAIT_NO_TIMEOUT",
        severity="MEDIUM",
        description="Busy-wait polls a flag with no timeout — hangs forever if the peripheral never responds",
        fix="Add a deadline: uint32_t t = HAL_GetTick(); while (!flag && (HAL_GetTick()-t) < TIMEOUT_MS);",
    ),
    BugPattern(
        key="MAGIC_REGISTER_ADDR",
        severity="LOW",
        description="Raw peripheral register address hardcoded — brittle across chip variants and hard to maintain",
        fix="Use peripheral definitions from HAL/CMSIS headers (e.g. GPIOA->ODR) instead of raw addresses.",
    ),
    BugPattern(
        key="STRNCPY_TRUNCATION",
        severity="LOW",
        description="strncpy() does not null-terminate when source is longer than n — the next strlen/strcpy may run off the buffer",
        fix="After strncpy: buf[sizeof(buf)-1] = '\\0';  Or use strlcpy() if the platform provides it.",
    ),

    # ── behavioral / silent-bug patterns ─────────────────────────────────────
    # These produce NO error and NO crash — the device just behaves incorrectly.
    # Each pattern encodes experience: the trap, what silently goes wrong, and how to verify.

    BugPattern(
        key="ADC_MISSING_CALIBRATION",
        severity="HIGH",
        category="behavioral",
        description="ADC started without HAL_ADCEx_Calibration_Start — readings are systematically off by 2–3%, no error is raised",
        fix="Call HAL_ADCEx_Calibration_Start(&hadc, ADC_CALIB_OFFSET, ADC_SINGLE_ENDED) after HAL_ADC_Init and before HAL_ADC_Start.",
    ),
    BugPattern(
        key="GPIO_FLOATING_INPUT",
        severity="HIGH",
        category="behavioral",
        description="GPIO input with no pull-up or pull-down — pin floats, reads random 0/1, logic 'works' on the bench then fails in the field",
        fix="Set GPIO_InitStruct.Pull = GPIO_PULLUP or GPIO_PULLDOWN to match the circuit, or add a physical resistor.",
    ),
    BugPattern(
        key="OPEN_DRAIN_NO_PULLUP",
        severity="HIGH",
        category="behavioral",
        description="GPIO configured as open-drain output but no pull-up — the line stays low, device never responds, no error from HAL",
        fix="Add GPIO_InitStruct.Pull = GPIO_PULLUP, or fit a physical pull-up resistor (typically 4.7kΩ) on the bus.",
    ),
    BugPattern(
        key="FLOAT_INT_DIVISION",
        severity="MEDIUM",
        category="behavioral",
        description="Float assigned from integer division — C truncates before the assignment, result is always a whole number (e.g. 3/2 = 1.0, not 1.5)",
        fix="Cast one operand: float ratio = (float)numerator / denominator;",
    ),
    BugPattern(
        key="NARROW_OVERFLOW_PRODUCT",
        severity="MEDIUM",
        category="behavioral",
        description="Multiplication result stored in a narrower type — silently wraps to 0 or a wrong value (e.g. uint8_t x = 200 * 2 → 144)",
        fix="Compute in a wider type first: uint32_t tmp = (uint32_t)a * b; then range-check before narrowing.",
    ),
    BugPattern(
        key="FLOAT_EQUALITY_CHECK",
        severity="MEDIUM",
        category="behavioral",
        description="Floating-point value compared with == or != — rounding makes exact equality rarely true; condition silently never fires",
        fix="Compare with a tolerance: fabsf(a - b) < 1e-6f  instead of  a == b.",
    ),
    BugPattern(
        key="WDT_INSIDE_CONDITION",
        severity="MEDIUM",
        category="behavioral",
        description="Watchdog refresh inside an if() — if that branch is skipped the WDT is not refreshed and the device resets hours later in the field",
        fix="Move HAL_IWDG_Refresh to the top of the main loop, unconditionally, before any conditional logic.",
    ),
    BugPattern(
        key="DMA_CLOCK_NOT_ENABLED",
        severity="MEDIUM",
        category="behavioral",
        description="DMA peripheral initialised but its RCC clock may not be enabled — transfers silently fail or corrupt memory, no HAL error",
        fix="Add __HAL_RCC_DMA1_CLK_ENABLE() (or DMA2) before HAL_DMA_Init in your MspInit function.",
    ),
    BugPattern(
        key="RESET_WITHOUT_DELAY",
        severity="LOW",
        category="behavioral",
        description="GPIO reset to a peripheral followed immediately by communication — device has not completed its startup sequence, first byte lost",
        fix="Add HAL_Delay(device_startup_ms) after asserting reset before the first HAL_SPI/I2C call. Check the datasheet for the power-on reset time.",
    ),
]

PATTERN_MAP: dict[str, BugPattern] = {p.key: p for p in PATTERNS}


# ── line-level matcher ────────────────────────────────────────────────────────

def match_line(line: str, in_isr: bool, brace_depth: int) -> list[str]:
    """Return keys of every BugPattern that fires on this source line.

    Parameters mirror the scanner's running state; this function is pure —
    it has no side effects and does not access the filesystem.
    """
    s = line.strip()

    # Skip comment lines and preprocessor directives — too many false positives
    if s.startswith(("//", "/*", "*", "#")):
        return []

    hits: list[str] = []

    if in_isr:
        if _HAL_DELAY_RE.search(line):
            hits.append("HAL_DELAY_IN_ISR")
        if _BLOCKING_IO_RE.search(line):
            hits.append("BLOCKING_IO_IN_ISR")

    if _SPRINTF_RE.search(line):
        hits.append("SPRINTF_OVERFLOW")
    if _GETS_RE.search(line):
        hits.append("GETS_UNSAFE")

    if (_HAL_PERIPH_RE.match(line)
            and not _HAL_CHECKED_RE.search(line)):
        hits.append("HAL_RETURN_UNCHECKED")

    if _MALLOC_RE.search(line) or _FREE_RE.search(line):
        hits.append("MALLOC_HEAP")

    # Large array only meaningful on the stack (inside a function)
    if brace_depth > 0:
        m = _LARGE_ARRAY_RE.search(line)
        if m:
            try:
                if int(m.group(1)) > 512:
                    hits.append("LARGE_STACK_ARRAY")
            except ValueError:
                pass

    if (in_isr
            and _BARE_ASSIGN_RE.match(s)
            and not _TYPE_KEYWORD_RE.match(s)
            and "HAL_" not in line
            and "__" not in line):
        hits.append("ISR_FLAG_NO_VOLATILE")

    if _BUSY_WAIT_RE.search(line):
        hits.append("BUSY_WAIT_NO_TIMEOUT")
    if _MAGIC_ADDR_RE.search(line):
        hits.append("MAGIC_REGISTER_ADDR")
    if _STRNCPY_RE.search(line):
        hits.append("STRNCPY_TRUNCATION")

    # ── behavioral / silent-bug line checks ───────────────────────────────────
    if _FLOAT_INT_DIV_RE.match(line):
        hits.append("FLOAT_INT_DIVISION")
    if _NARROW_PRODUCT_RE.match(line):
        hits.append("NARROW_OVERFLOW_PRODUCT")
    if _FLOAT_LITERAL_EQ_RE.search(line):
        hits.append("FLOAT_EQUALITY_CHECK")
    if _OPEN_DRAIN_RE.search(line):
        hits.append("OPEN_DRAIN_NO_PULLUP")
    if _WDT_REFRESH_RE.search(line) and _WDT_IN_IF_RE.search(line):
        hits.append("WDT_INSIDE_CONDITION")

    return hits
