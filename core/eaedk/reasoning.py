"""Engineering-reasoning library (v2.6.0) — the mentor's framework, not an answer engine.

A board-agnostic, offline, curated set of engineering decisions. Each topic carries the framework
axes (what / why / when / alternatives / trade-offs / how-to-think / next-step) so the mentor can
teach a learner *how to reason*, not hand them an answer. Board facts only ENRICH a trade-off; the
reasoning is valid on STM32 / RP2040 / ESP32 / AVR / Linux alike.

Pure Python data + a renderer, so the framework holds with no model and no database — an air-gapped
mentor still teaches the thinking. ``detect_topic`` maps a question to a topic; ``render`` produces
the framework as readable prose, optionally enriched by the board in view.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable


@dataclass
class Topic:
    key: str
    title: str
    keywords: tuple[str, ...]
    what: str
    why: str
    when: str
    alternatives: list[tuple[str, str]]          # (option, one-line characterisation)
    tradeoffs: str
    how_to_think: list[str]                       # the questions an engineer asks before deciding
    next_step: str
    enrich: Callable[[dict], str | None] | None = None   # board facts sharpen the trade-off


def _ram_enrich(ctx: dict) -> str | None:
    ram = ctx.get("ram_kb")
    if not isinstance(ram, int):
        return None
    return (f"On {ctx.get('board_name', 'this board')} you have {ram}KB of RAM. An RTOS task stack "
            f"is typically 0.5–2KB each, so count your tasks against that budget before you commit — "
            f"{'that is tight; favour a super-loop unless you truly need concurrency.' if ram < 32 else 'you have room, but still add tasks only when timing forces it.'}")


def _flash_enrich(ctx: dict) -> str | None:
    flash = ctx.get("flash_kb")
    if not isinstance(flash, int):
        return None
    return (f"On {ctx.get('board_name', 'this board')} you have {flash}KB of flash. An A/B (dual-slot) "
            f"scheme needs room for two full images, so {'that may be tight — a single recovery image is the realistic choice.' if flash < 256 else 'two slots are feasible; weigh the robustness against the lost space.'}")


def _mem_enrich(ctx: dict) -> str | None:
    ram, b = ctx.get("ram_kb"), ctx.get("board_name", "this board")
    if not isinstance(ram, int):
        return None
    if ram <= 8:
        return (f"On {b} you have only {ram}KB of RAM — a stack overflow is your FIRST enemy. Keep "
                f"locals tiny, avoid recursion and malloc, and put any buffer bigger than a few dozen "
                f"bytes in static memory.")
    return (f"On {b} you have {ram}KB of RAM — comfortable, but still set a stack guard and watch the "
            f"high-water mark, because a stack/heap collision is just as silent here.")


def _clock_enrich(ctx: dict) -> str | None:
    notes = {
        "stm32": "On STM32, enable a peripheral's clock in RCC->APBxENR / AHBxENR (and its GPIO port's "
                 "clock too) BEFORE you configure it.",
        "rp2040": "On RP2040, bring the peripheral out of reset and un-gate its clock in the RESETS / "
                  "CLOCKS block before use.",
        "esp32": "On ESP32 the IDF driver enables the peripheral clock for you; a hand-written register "
                 "driver must enable it in the system clock-control registers first.",
        "avr": "On AVR most peripherals are clocked by default, but the Power Reduction Register (PRR) "
               "can gate them — if a peripheral is dead, check its PRR bit.",
    }
    return notes.get(ctx.get("family"))


TOPICS: dict[str, Topic] = {
    "hal_vs_baremetal": Topic(
        key="hal_vs_baremetal", title="HAL vs bare-metal",
        keywords=("hal or", "hal vs", "bare metal", "bare-metal", "cmsis", "register level",
                  "register-level", "abstraction layer", "use hal", "use the hal"),
        what="HAL (Hardware Abstraction Layer) is vendor C that wraps the chip's registers behind "
             "functions like HAL_UART_Transmit(); bare-metal means you write those registers yourself.",
        why="Registers are unforgiving and chip-specific. HAL exists to ship faster and port across a "
            "vendor's family without re-reading every register; bare-metal exists to keep full control "
            "and a real understanding of what the silicon is doing.",
        when="Reach for HAL under deadline, across a large peripheral set, or when portability within "
             "the vendor's line matters. Reach for bare-metal when you are learning how the hardware "
             "works, when you need the smallest/fastest code, or when HAL's behaviour is fighting you.",
        alternatives=[("Bare-metal / CMSIS-register", "you own every bit — most control and "
                       "understanding, slowest to write"),
                      ("HAL / vendor SDK", "fast to write, portable within the family, hides the "
                       "register boundaries"),
                      ("LL (low-layer) helpers", "a middle ground — thin inline register helpers")],
        tradeoffs="HAL trades understanding and code size for development speed. Bare-metal trades "
                  "development speed for control, size, and debuggability — when it breaks you know "
                  "exactly what each line touched.",
        how_to_think=["Am I optimising to LEARN, or to ship on a deadline?",
                      "How many peripherals must I bring up — a few (bare-metal is fine) or dozens?",
                      "Will this code move to other chips in the family later?",
                      "When it breaks, do I want to debug my own register writes or the vendor's layer?"],
        next_step="Pick one peripheral (a UART) and write its init BOTH ways — bare-metal once, then "
                  "with HAL. You will feel exactly what each hides and exposes."),

    "interrupt_vs_polling": Topic(
        key="interrupt_vs_polling", title="interrupt vs polling — and the hazards",
        keywords=("interrupt vs polling", "polling vs interrupt", "interrupt or poll",
                  "poll or interrupt", "interrupt or polling", "when should i use an interrupt",
                  "when to use an interrupt", "race condition", "volatile", "atomic read",
                  "atomic access", "priority inversion", "nested interrupt", "shared variable",
                  "interrupt-safe", "interrupt safe"),
        what="Polling and interrupts are two ways to NOTICE an event. Polling: the CPU checks in a "
             "loop. Interrupt: the hardware calls your code the instant the event fires, then returns "
             "to what it was doing.",
        why="Polling burns the CPU waiting and can miss fast events between checks; interrupts free the "
            "CPU and react immediately. But that asynchrony is the catch — an interrupt can fire in "
            "the middle of your main code and corrupt state they share.",
        when="Poll when the work is simple, single-task, and timing isn't tight (a button in a slow "
             "loop). Use interrupts for asynchronous events, to let the CPU SLEEP until something "
             "happens (power saving), or for hard real-time response.",
        alternatives=[("Polling", "dead simple, no concurrency bugs; burns CPU and can miss fast events"),
                      ("Interrupt", "instant, frees the CPU, enables sleep; introduces races, "
                       "shared-state and re-entrancy bugs"),
                      ("Interrupt sets a flag, main loop acts", "the common SAFE pattern — the ISR does "
                       "the minimum, the main loop does the work")],
        tradeoffs="Interrupts buy responsiveness and efficiency at the price of concurrency — bugs that "
                  "simply don't exist in polling. A variable shared with an ISR must be 'volatile' or "
                  "the compiler caches a stale copy. A multi-byte read of an ISR-updated value can TEAR "
                  "(half-old, half-new) unless it's atomic. A long ISR can be interrupted again and "
                  "overflow the stack. Polling has none of these — slower, but boring, and boring is "
                  "safe.",
        how_to_think=["Is the event asynchronous, or can I just check for it in my loop?",
                      "Does the CPU need to sleep or do other work while waiting?",
                      "Is any variable shared between the ISR and main code — is it 'volatile' and read "
                      "atomically?",
                      "Am I keeping the ISR short (set a flag, return) rather than doing real work in it?"],
        next_step="Start polled. When you need async response or power saving, move to an interrupt — "
                  "but make the ISR do the MINIMUM (set a volatile flag) and let the main loop act on "
                  "it. That pattern sidesteps most concurrency bugs."),

    "polling_vs_interrupt_vs_dma": Topic(
        key="polling_vs_interrupt_vs_dma", title="polling vs interrupt vs DMA",
        keywords=("polling", "poll or", "interrupt", "interrupts", " isr", "dma", "blocking read",
                  "non-blocking", "how do i read", "how should i read"),
        what="Three ways to move data between the CPU and a peripheral: poll (the CPU waits and "
             "checks), interrupt (the peripheral signals the CPU when ready), or DMA (a dedicated "
             "engine moves the data with no CPU involvement).",
        why="The CPU is scarce. The problem is how to service a peripheral without wasting cycles "
            "waiting — and without missing data while the CPU is busy elsewhere.",
        when="Poll for simple, short, infrequent transfers where blocking is acceptable. Interrupt "
             "when events are sporadic and you must react promptly without busy-waiting. DMA when data "
             "is high-rate or bulk (audio, ADC streams, big SPI/UART) and the CPU must stay free.",
        alternatives=[("Polling", "simplest to write and reason about; wastes CPU and can block or "
                       "miss data"),
                      ("Interrupt", "responsive, frees the main loop; adds concurrency (shared state, "
                       "re-entrancy) you must handle"),
                      ("DMA", "moves bulk data with near-zero CPU; most setup, easiest to get subtly "
                       "wrong (half-transfer, buffer races)")],
        tradeoffs="Each step up the ladder trades simplicity for efficiency. Polling is easy but "
                  "greedy; interrupts free the CPU but introduce concurrency you must reason about; "
                  "DMA is the most efficient and the most to configure correctly.",
        how_to_think=["How fast and how bulky is the data — bytes occasionally, or a continuous stream?",
                      "Can the main loop afford to block while this happens?",
                      "What is the cost of missing a byte?",
                      "Do I have the concurrency discipline that interrupts and DMA demand?"],
        next_step="Implement one input (a sensor read) POLLED first. Then move it to an interrupt and "
                  "watch the main loop free up. Reach for DMA only when the data rate forces it."),

    "rtos_vs_superloop": Topic(
        key="rtos_vs_superloop", title="RTOS vs super-loop",
        keywords=("rtos", "freertos", "zephyr", "scheduler", "superloop", "super-loop", "super loop",
                  "multitask", "do i need an rtos", "need an rtos", "real-time os", " tasks "),
        what="A super-loop is a single while(1) that runs your tasks in sequence. An RTOS is a "
             "scheduler that runs multiple tasks 'concurrently', switching on priority and time.",
        why="An RTOS solves INDEPENDENT timing: when several activities each have their own deadline "
            "and one slow step would otherwise starve the others. A super-loop couples everything to "
            "its slowest task.",
        when="Stay with a super-loop (plus interrupts) until you have multiple independent timing "
             "requirements, a blocking operation that stalls everything, or a team that needs modular "
             "tasks. Add an RTOS when the super-loop's timing coupling becomes the bug.",
        alternatives=[("Super-loop", "trivial, deterministic, tiny; everything is coupled — one slow "
                       "step delays all"),
                      ("Super-loop + interrupts", "handles urgent events without an RTOS; still one "
                       "thread of control"),
                      ("RTOS (FreeRTOS / Zephyr)", "independent prioritised tasks; costs RAM and adds "
                       "scheduling + concurrency complexity")],
        tradeoffs="An RTOS buys independent timing and modularity at the cost of RAM (a stack per "
                  "task), scheduler complexity, and a class of concurrency bugs — priority inversion, "
                  "deadlocks, per-task stack overflow. Most beginner projects do not need it and reach "
                  "for it too early.",
        how_to_think=["Do I actually have two or more activities with INDEPENDENT deadlines?",
                      "Is a blocking call (a network read) stalling time-critical work?",
                      "Do I have the RAM for several task stacks?",
                      "Can I do this as interrupts + a super-loop first, and add an RTOS only when that "
                      "breaks?"],
        next_step="Write the project as a super-loop first. When you start adding flags and timers to "
                  "FAKE concurrency, that is the signal an RTOS would help — not before.",
        enrich=_ram_enrich),

    "failsafe_bootloader": Topic(
        key="failsafe_bootloader", title="fail-safe bootloader / updates",
        keywords=("bootloader", "fail-safe", "failsafe", "fail safe", "ota", "firmware update",
                  "over-the-air", "rollback", "a/b", "dual bank", "dual-bank", "brick"),
        what="A fail-safe bootloader is a small first program that decides what to run and can recover "
             "from a bad update — it verifies the application and falls back to a known-good image "
             "instead of bricking.",
        why="Power can die mid-write, or a broken image can ship. Without a fail-safe path, a "
            "half-written or bad firmware leaves a dead device with no way back.",
        when="Any product that updates firmware in the field needs this. A board you only flash over a "
             "debugger does not.",
        alternatives=[("No bootloader (flash over SWD/JTAG)", "simplest; no field update, a bad flash "
                       "needs a programmer"),
                      ("Single-bank bootloader + app", "enables UART/USB update; a failed update can "
                       "still brick with no fallback"),
                      ("A/B (dual-slot) or a recovery image", "a failed update rolls back to the "
                       "working image; costs flash for two images")],
        tradeoffs="Robustness costs flash and complexity. A/B doubles image storage but makes a failed "
                  "update a non-event; a recovery image is cheaper but the recovery path is more "
                  "manual; no-rollback is simplest and the most dangerous in the field.",
        how_to_think=["How does new firmware reach the device — UART, USB, network?",
                      "What happens if power dies exactly mid-write?",
                      "Do I have flash for two full images (A/B), or just a recovery stub?",
                      "Who verifies the new image is authentic and intact before it runs?"],
        next_step="Before any code, draw the flash map on paper — bootloader, app, and slot B / "
                  "recovery. Then EAEDK validates that the regions don't overlap and a recovery path "
                  "exists (BOOTLOADER_APP_NO_OVERLAP, PARTITION_AB_SYMMETRY, RECOVERY_PRESENT).",
        enrich=_flash_enrich),

    "board_selection": Topic(
        key="board_selection", title="choosing a board",
        keywords=("which board", "what board", "choose a board", "board for", "which mcu",
                  "what chip", "which chip", "pick a board"),
        what="Choosing a board is matching a project's needs — peripherals, processing, connectivity, "
             "power — to a chip, before you fall in love with one.",
        why="Every board is a bundle of trade-offs, and tutorials push the popular one regardless of "
            "your constraints. The wrong board makes the whole project harder.",
        when="At the very start, before any code — and again whenever a constraint (battery, a "
             "peripheral, a deadline) changes.",
        alternatives=[("Connectivity", "integrated WiFi/BLE/Ethernet vs an external module over "
                       "UART/SPI"),
                      ("Processing & RAM", "enough headroom for the heaviest task plus its stack"),
                      ("Power", "battery vs mains changes the whole architecture"),
                      ("Ecosystem", "toolchain, community, and examples you can lean on when stuck")],
        tradeoffs="Integrated connectivity saves wiring but costs power; a bare MCU is cheaper and "
                  "lower-power but needs external radios; a Linux SBC gives you an OS but draws watts "
                  "and boots slowly. There is no 'best board' — only best-for-these-constraints.",
        how_to_think=["What does the project actually NEED — list the peripherals and connectivity.",
                      "Does it run on battery? That alone narrows the field.",
                      "What is the heaviest task's RAM and processing demand?",
                      "What toolchain and community will I lean on when I'm stuck?"],
        next_step="Write down your four constraints (peripherals, processing, power, connectivity) "
                  "BEFORE looking at any board. Then the shortlist picks itself."),

    "watchdog_strategy": Topic(
        key="watchdog_strategy", title="watchdog strategy",
        keywords=("watchdog", "iwdg", "wwdg", "reset on hang", "dead-man", "deadman", "lockup",
                  "hang recovery"),
        what="A watchdog is a hardware timer that resets the chip unless your code 'feeds' it "
             "periodically — a dead-man's switch for firmware.",
        why="Firmware can hang (an infinite loop, a stuck peripheral, corrupted state) with no one to "
            "recover it in the field. The watchdog guarantees a reset instead of a permanent freeze.",
        when="Any unattended device. The subtlety: feed it from a place that proves the system is "
             "actually HEALTHY, not just that the loop is spinning.",
        alternatives=[("No watchdog", "simplest; a hang is permanent until someone power-cycles"),
                      ("Feed in the main loop", "catches a fully-stuck CPU; misses a hung task while "
                       "the loop still runs"),
                      ("Health-gated feed", "feed only when every critical task has reported in — "
                       "catches partial hangs too")],
        tradeoffs="Feeding blindly in the main loop is easy but gives false confidence: if one "
                  "critical task freezes and the loop keeps spinning, the watchdog never fires. "
                  "Health-gating the feed is more work but actually proves the system is alive.",
        how_to_think=["What does 'healthy' mean here — which tasks must be alive?",
                      "Where do I feed the watchdog so a PARTIAL hang still triggers a reset?",
                      "How long can the device be frozen before the reset must happen?",
                      "Is the watchdog on from the first instruction, or is there an unguarded window?"],
        next_step="Decide your timeout and your feed POINT on paper first. A watchdog fed in the wrong "
                  "place is worse than none — it hides the hang."),

    "debugging_method": Topic(
        key="debugging_method", title="the 'nothing happens' method",
        keywords=("nothing happens", "doesn't work", "no output", "how do i debug", "where do i start "
                  "debug", "board is dead", "board seems dead", "how to debug"),
        what="Debugging 'nothing happens' is not guessing — it's bisecting the signal path from the "
             "symptom back to the first thing that could be wrong.",
        why="'Nothing' has many causes, and randomly changing code wastes hours. A method turns a "
            "black box into an ordered set of hypotheses you can rule out one at a time.",
        when="The moment a board is silent or dead after flashing.",
        alternatives=[("Guess and change code", "fast to start, slow to finish; you learn nothing"),
                      ("Bisect the signal path", "an ordered list of causes (clock → pin config → "
                       "peripheral → protocol), ruling each out"),
                      ("Instrument", "toggle a GPIO / blink an LED at each stage to see how far "
                       "execution actually reaches")],
        tradeoffs="Guessing feels productive and rarely is. Bisecting is disciplined and almost always "
                  "faster. Instrumenting costs a few lines but turns invisible failure into a visible "
                  "'it got this far'.",
        how_to_think=["What is the FIRST thing in the chain that must be true — is the clock running?",
                      "How far does execution actually reach (toggle a pin to find out)?",
                      "What is the single most common cause on this chip family — have I ruled it out?",
                      "Am I changing ONE thing at a time, or shotgunning?"],
        next_step="Blink an LED at the very top of main(). If it blinks, your clock and toolchain are "
                  "fine and the bug is downstream. If it doesn't, the problem is clock/boot, not your "
                  "application."),

    "clock_tree": Topic(
        key="clock_tree", title="clock tree and peripheral clocking",
        keywords=("clock tree", "clock domain", "peripheral clock", "clock enable", "enable the clock",
                  "clock gat", "rcc", "clocking", "why is my peripheral", "peripheral does nothing",
                  "peripheral silently", "peripheral is dead"),
        what="A clock domain is the timing signal that drives a peripheral. On most ARM MCUs every "
             "peripheral starts OFF — its clock is gated (disabled) to save power — and does literally "
             "nothing until you enable it.",
        why="Power. A chip with dozens of peripherals would waste energy clocking ones you never use, "
            "so they ship disabled. The catch: you must remember to turn each one on, and forgetting "
            "is SILENT — the register writes land on dead hardware with no error at all.",
        when="Always, before you touch any peripheral's registers — on STM32 and most ARM MCUs.",
        alternatives=[("Configure, then enable the clock (WRONG)", "your register writes hit a dead "
                       "peripheral — no error, no effect"),
                      ("Enable the clock first (RIGHT)", "clock enable → GPIO config → peripheral "
                       "config → enable the peripheral"),
                      ("Let HAL enable it", "vendor init turns the clock on for you — convenient, but "
                       "you won't think to suspect it when it breaks")],
        tradeoffs="Gated clocks save real power but cost you a mandatory step that is invisible when "
                  "forgotten. HAL enabling the clock for you trades that vigilance for not "
                  "understanding the failure when it happens — and a missing clock ALWAYS looks like "
                  "'nothing happens'.",
        how_to_think=["Is this peripheral's clock enabled BEFORE I configure it?",
                      "Which bus is it on, and am I enabling the right clock register?",
                      "Did I enable the GPIO PORT's clock too, not just the peripheral's?",
                      "When nothing happens, is the clock the FIRST thing I check?"],
        next_step="Burn the sequence in: enable the clock → configure the pins → configure the "
                  "peripheral → enable the peripheral. When a peripheral is silent, suspect its clock "
                  "before anything else.",
        enrich=_clock_enrich),

    "memory_layout": Topic(
        key="memory_layout", title="memory layout — stack, heap, static, flash",
        keywords=("memory layout", "stack overflow", "stack and heap", "the heap", "heap memory",
                  "malloc", "where do my variables", "where my variables live", "where variables live",
                  "running out of ram", "out of ram", "ram usage", "global variable",
                  "static variable", "stack pointer", "memory model", ".bss"),
        what="Your program lives in two memories. FLASH holds what doesn't change — your code and "
             "'const' data. RAM holds what does — the stack (locals and call frames, growing down), "
             "the heap (malloc, growing up), and static/global variables (fixed addresses).",
        why="RAM is small and most MCUs have no MMU to catch mistakes. The stack and heap grow toward "
            "each other in the SAME RAM; when they collide you get silent corruption, not a clean "
            "error. You need to know where each variable lives to reason about why a board crashed.",
        when="Whenever you declare a variable, size a buffer, call malloc, or a board crashes "
             "mysteriously under load.",
        alternatives=[("Stack (locals)", "fast, automatic, freed on return; small — a big local buffer "
                       "or deep recursion silently overflows it"),
                      ("Static / global", "fixed address, always present; persists and is SHARED — "
                       "dangerous across interrupts"),
                      ("Heap (malloc/free)", "flexible size at runtime; fragments, can fail mid-run, "
                       "and is discouraged in small embedded")],
        tradeoffs="The stack is fast and automatic but small — a large local array overruns it "
                  "silently. Globals are convenient but shared state is the root of interrupt bugs. "
                  "The heap is flexible but fragments and can fail, which is why much embedded code "
                  "avoids malloc and pre-allocates instead.",
        how_to_think=["Where does this variable LIVE — stack, static, or heap?",
                      "Is this buffer big enough to blow the stack (then make it static)?",
                      "Is any global touched by an interrupt — is that access safe?",
                      "Do I know my stack high-water mark, or am I guessing how much RAM is left?"],
        next_step="Move your big buffers off the stack — anything large goes static or heap. Then read "
                  "the linker map (or the stack high-water mark) to see how much RAM you're actually "
                  "using, before it crashes.",
        enrich=_mem_enrich),
}


def detect_topic(text: str) -> Topic | None:
    """Map a question to a reasoning topic by keyword (None if it isn't a known engineering decision)."""
    low = " " + (text or "").lower() + " "
    for topic in TOPICS.values():
        if any(k in low for k in topic.keywords):
            return topic
    return None


def render(topic: Topic, board_name: str | None = None, arch: str | None = None,
           family: str | None = None, ram_kb: int | None = None, flash_kb: int | None = None) -> str:
    """Render the framework as readable, teaching prose. The core (what / why / options / trade-offs /
    how-to-decide) is board-agnostic; only the optional enrichment line uses the board's facts."""
    L = [f"The problem — {topic.what}",
         f"Why it exists: {topic.why}",
         f"When it applies: {topic.when}",
         "Your options:"]
    L += [f"  • {opt} — {desc}" for opt, desc in topic.alternatives]
    L.append(f"The trade-off: {topic.tradeoffs}")
    L.append("How an engineer decides — ask yourself:")
    L += [f"  ? {q}" for q in topic.how_to_think]
    if topic.enrich is not None:
        enr = topic.enrich({"board_name": board_name, "arch": arch, "family": family,
                            "ram_kb": ram_kb, "flash_kb": flash_kb})
        if enr:
            L.append(enr)
    L.append(f"Next: {topic.next_step}")
    return "\n".join(L)
