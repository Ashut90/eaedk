"""Mentor LLM layer (Part 3) — the model explains and guides; it never asserts a hardware fact.

Reuses the offline Gateway + the post-filter. Off by default: without `--llm`, both commands
return a useful deterministic answer (the curated learning path / concept anchor). With `--llm`,
the model elaborates and the post-filter strips any uncited hardware value.
"""
from __future__ import annotations

import sqlite3

from . import repo, mentor
from .llm.gateway import Gateway
from .llm.postfilter import build_board_allowlist, filter_text

# Role A — System Architect. Reason about goal/scope/trade-off before recommending anything.
_ASK_SYSTEM = (
    "You are EAEDK's mentor acting as a battle-tested principal firmware engineer. The user is "
    "asking what to build or which design to choose. Do NOT recommend immediately. First reason: "
    "what are they trying to LEARN or ship, what does THIS board's capability map make possible, "
    "and what is the trade-off (e.g. HAL ships fast but hides the register boundaries; bare-metal "
    "is slower but shows how the bus actually moves data). Open with a consequence or a question, "
    "never a definition. Then point at a first step WITH a reason. Reason ONLY from the CONTEXT; "
    "NEVER state a hardware fact (address, register, clock, memory size) not in the CONTEXT — for a "
    "specific value, name the datasheet section to verify instead. End with ONE question that makes "
    "the user decide what matters most: speed of shipping, or depth of understanding.")

# Role A — explain by starting from the hardware consequence, not a textbook definition.
_EXPLAIN_SYSTEM = (
    "You are EAEDK's mentor. Explain the concept by starting from the hardware CONSEQUENCE — what "
    "the chip does wrong without it — in at most three plain sentences for a beginner, using the "
    "board's architecture as context, then say what to check next. Never open with a definition. "
    "NEVER invent an address, register, clock, or timing value; if a specific value matters, name "
    "the datasheet section to confirm it instead of stating it.")


def _ctx(conn: sqlite3.Connection, board_name: str) -> tuple[dict, dict, list]:
    board, soc = repo.load_board(conn, board_name)
    caps = mentor.capability_map(conn, board_name)
    path = mentor.learning_path_for(conn, {c["capability"] for c in caps})
    return soc, caps, path


def _llm_or_note(use_llm: bool, gw: Gateway) -> str | None:
    if not use_llm:
        return "\n[mentor] add --llm for a conversational answer (offline model)."
    if not gw.available():
        return (f"\n[mentor] LLM unavailable (model '{gw.model}' not pulled); showing the "
                "deterministic answer above.")
    return None


def mentor_ask(conn: sqlite3.Connection, board_name: str, question: str,
               use_llm: bool = False, gateway: Gateway | None = None) -> str:
    soc, caps, path = _ctx(conn, board_name)
    first = path[0] if path else None
    # Deterministic backbone — always shown.
    head = [f"Mentor — {board_name} ({soc['arch']})"]
    if first:
        head.append(f"Start with: {first['title']}.  Why: {first['why']}")
    body = "\n".join(head)

    gw = gateway or Gateway()
    note = _llm_or_note(use_llm, gw)
    if note is not None:
        return body + note

    cap_lines = "\n".join(f"- {c['summary'] or c['capability']}" for c in caps)
    path_lines = "\n".join(f"{s['step']}. {s['title']} — {s['why']}" for s in path)
    prompt = (f"CONTEXT\nBoard: {board_name} ({soc['arch']})\nCapabilities:\n{cap_lines}\n"
              f"Learning path:\n{path_lines}\n\nQUESTION: {question}\n\nAnswer:")
    raw = gw.provider.generate(_ASK_SYSTEM, prompt)
    filtered, removed = filter_text(raw, build_board_allowlist(conn, board_name))
    return f"{body}\n\n{filtered}\n[mentor] {removed} uncited hardware claim(s) removed."


# Role A (System Architect) + Role D (Reverse Mentor). Teach the engineer to THINK, not to copy code.
_CHAT_SYSTEM = (
    "You are EAEDK's mentor. Your job is to teach a person to THINK like a firmware engineer — not "
    "to hand out code or definitions. Reason ONLY from the CONTEXT (board, architecture, the "
    "peripherals this board actually has, flash/RAM, project, learning step, and whether the user "
    "is on the Wokwi simulator or real hardware).\n"
    "Rules for every reply:\n"
    "- Open with a CONSEQUENCE or a QUESTION, never a definition.\n"
    "- Tie the answer to THIS board's real capabilities; if a peripheral is not in the list, say so "
    "rather than assume it. Never give a generic answer when the board is known.\n"
    "- When the user names a project type (robotics, sensor, motor, audio, IoT), reason about WHICH "
    "of this board's specific peripherals that project needs and why — e.g. motor control = a "
    "timer's PWM through an H-bridge, not generic GPIO — never list all peripherals generically.\n"
    "- For any specific register, alternate-function (AF) number, address, clock, or timing: do NOT "
    "state the value — you cannot verify it. Name the concern and the datasheet table to check, then "
    "ask which value the user actually used.\n"
    "- Explain WHY a choice and what breaks if they pick the alternative; never 'it depends' without "
    "saying what it depends on.\n"
    "- Before any code, make the user answer four questions (ASK, do not answer them): what is the "
    "goal, why are they doing it, how (which of this board's peripherals), when is it done (which "
    "validations PASS).\n"
    "- If the user is on Wokwi, downgrade physical-only concerns (boot pins, factory bootloader ROM) "
    "to 'later, on real hardware' — never block learning over something the simulator does not model.\n"
    "- End with exactly ONE 'Try this:' tied to this board and project, then ONE follow-up question.\n"
    "- No filler ('great question', 'certainly', 'of course'). NEVER assert a hardware fact "
    "(address, register, clock, memory size) not in the CONTEXT.")


def _detect_concept(conn: sqlite3.Connection, text: str):
    """Return the concept row whose name appears in the user's text, else None. Punctuation is
    normalised to spaces so 'what is a hardfault?' still matches 'hardfault'."""
    import re as _re
    low = " " + _re.sub(r"[^a-z0-9]+", " ", (text or "").lower()) + " "
    for row in repo.list_concepts(conn):
        name = _re.sub(r"[^a-z0-9]+", " ", row["name"].lower()).strip()
        if f" {name} " in low or f" {name}s " in low:
            return row
    return None


def _board_try_this(family: str | None) -> str:
    """A concrete, board-family-appropriate experiment for the user's first project. No numbers
    that the post-filter would strip — kept conceptual so it always survives."""
    if family == "stm32":
        return ("In your blink project, find the line that enables your LED pin's GPIO clock "
                "(the RCC line) and comment it out. Build it, run it in Wokwi, and watch the LED "
                "do nothing — that's exactly why enabling the clock first matters.")
    if family == "avr":
        return ("In your blink project, set F_CPU to the wrong value and watch your delay timing "
                "break — it shows how the clock setting drives everything.")
    if family == "esp32":
        return ("In your blink project, try a Wi-Fi call before nvs_flash_init() and watch it "
                "fail — it shows why init order matters on the ESP32.")
    if family == "rp2040":
        return ("In your blink project, look at how the second-stage bootloader (boot2) is placed "
                "before your code — remove it in your head and you'll see why nothing would run.")
    return ("In your blink project, change the order of your init calls and see what breaks — "
            "order is everything in bare-metal firmware.")


# Project-type reasoning (v2.4.1): when the user names a domain, reason about WHICH of THIS board's
# specific peripherals that project needs and why — never a generic capability list.
_PROJECT_DOMAINS = {
    "robotics": ("robot", "robotics", "motor", "servo", "h-bridge", "h bridge", "bldc", "stepper",
                 "wheel", "drone", "actuator", "encoder"),
    "sensor":   ("sensor", "imu", "accelerometer", "gyro", "temperature", "humidity", "proximity",
                 "distance", "lidar"),
    "audio":    ("audio", "sound", "speaker", "microphone", "music", "i2s"),
    "iot":      ("iot", "wifi", "wi-fi", "bluetooth", " ble ", "mqtt", "internet of"),
}


def _detect_domain(text: str) -> str | None:
    low = " " + (text or "").lower() + " "
    for domain, kws in _PROJECT_DOMAINS.items():
        if any(k in low for k in kws):
            return domain
    return None


def _domain_reasoning(domain: str, caps: set, family: str | None, board_name: str) -> str:
    """Curated, family-aware reasoning: which of THIS board's specific peripherals the project needs
    and why. Deterministic guidance (same class as the existing think-before-code hints), gated on the
    board's capabilities and chip family — so it is specific, not a generic peripheral dump."""
    has = caps.__contains__
    if domain == "robotics":
        L = [f"Robotics on {board_name} is move + sense + decide, and each maps to a SPECIFIC "
             "peripheral, not a generic GPIO pin:"]
        if has("timer"):
            if family == "stm32":
                L.append("- Move: motor speed is a PWM signal from a TIMER. On STM32 the advanced-"
                         "control timer (TIM1) has COMPLEMENTARY outputs with dead-time — exactly what "
                         "drives both sides of an H-BRIDGE (a DC/BLDC motor). A basic timer "
                         "(TIM9/10/11) cannot. Confirm which timer is the advanced one on your part in "
                         "the datasheet.")
            else:
                L.append("- Move: motor speed is a PWM signal from a TIMER, sent through a motor "
                         "driver / H-bridge — never straight from a GPIO pin.")
            L.append("- Position: a TIMER in encoder mode counts wheel-encoder pulses for odometry.")
        else:
            L.append("- Move: motors need PWM, but no timer capability is recorded for this board — "
                     "check the datasheet for a timer with PWM output.")
        buses = [b.upper() for b in ("i2c", "spi") if has(b)]
        if buses:
            L.append(f"- Sense: an IMU (accel+gyro) over {'/'.join(buses)} gives orientation — your "
                     "balance and heading.")
        if has("uart"):
            L.append("- Talk: UART to a host (Raspberry Pi, GPS, or a Bluetooth module).")
        L.append("Safety: a motor MUST go through a driver/H-bridge on its own supply — back-EMF and "
                 "current will destroy a pin driven directly.")
        return "\n".join(L)
    if domain == "sensor":
        L = [f"A sensor project on {board_name} needs a sensor BUS, not generic GPIO:"]
        if has("i2c"):
            L.append("- I2C: many sensors on two wires (IMU, temp/humidity, ToF) — start here.")
        if has("spi"):
            L.append("- SPI: faster, more wires — for high-rate sensors (some IMUs, external ADCs).")
        L.append("- A TIMER sets a fixed sample rate so readings are evenly spaced." if has("timer")
                 else "- Sample at a fixed rate (a timer interrupt) so readings are evenly spaced.")
        if not (has("i2c") or has("spi")):
            L.append("- No I2C/SPI is recorded for this board — check the datasheet before assuming a bus.")
        return "\n".join(L)
    if domain == "audio":
        L = [f"Audio on {board_name} is about streaming samples without the CPU stalling:",
             "- Output: I2S to a DAC/codec if the board has it, or PWM as a crude fallback.",
             ("- A TIMER sets the sample rate; DMA moves samples so the CPU isn't blocked."
              if has("timer") else "- You need a steady sample clock (timer) and ideally DMA."),
             "- Check the datasheet for I2S/DAC — not every board has true audio output."]
        return "\n".join(L)
    if domain == "iot":
        L = [f"An IoT project on {board_name} starts with one question: does it have connectivity?"]
        conn_caps = [c for c in ("wifi", "ethernet", "bluetooth", "ble") if has(c)]
        if conn_caps:
            L.append(f"- This board has {', '.join(conn_caps)} — that's your link.")
        else:
            L.append("- No Wi-Fi/Ethernet/BLE is recorded for this board, so you'd add a module over "
                     "UART or SPI (e.g. an ESP-AT module or a network controller). Confirm in the datasheet.")
        if has("uart"):
            L.append("- UART connects that module (and your debug console).")
        return "\n".join(L)
    return ""


# --- Role detection (v2.5.0): decide the behavioural mode in Python, before the model runs -------

# Simulation-specific triggers: only THESE (with the Wokwi flag set) make the mentor the
# Reverse Mentor. A Wokwi user's design or debug question still gets Architect / Peer (v2.5.1).
_SIM_TRIGGERS = ("wokwi", "simulator", "virtual", "blocking", "boot pin", "boot0",
                 "can't export", "cannot export", "won't export")


def detect_mentor_role(user_message: str, page_context: dict) -> str:
    """Pick the mentor's behavioural mode deterministically (docs/25, reordered in v2.5.1):
    SPONSOR -> PEER_MENTOR -> SYSTEM_ARCHITECT -> REVERSE_MENTOR(sim trigger + flag) -> ARCHITECT.
    The Wokwi flag informs the 'Try this' (point at the simulator) but no longer overrides the
    reasoning role for ordinary questions."""
    msg = (user_message or "").lower()

    # Role C — deterministic only, never needs the model.
    if (page_context.get("page_type") or "") in ("validate", "export"):
        return "SPONSOR"

    # Role B — the user shared code or is debugging.
    code = page_context.get("current_code") or ""
    peer_triggers = ("my code", "compiles but", "doesn't work", "nothing happens", "not working",
                     "wrong output", "review this", "check this", "what's wrong")
    if len(code) > 50 or any(t in msg for t in peer_triggers):
        return "PEER_MENTOR"

    # Role A — architectural / design / feasibility question.
    arch_triggers = ("should i use", "which board", "hal or", "bare metal", "where do i start",
                     "how do i design", "architecture", "which peripheral", "why would i",
                     "can i do", "is it possible", "what board", "robotics", "motor", "sensor",
                     "iot", "wifi", "bootloader", "fail-safe", "rtos", "driver")
    if any(t in msg for t in arch_triggers):
        return "SYSTEM_ARCHITECT"

    # Role D — only for a simulation-specific question on the Wokwi path.
    if page_context.get("wokwi_flag") and any(t in msg for t in _SIM_TRIGGERS):
        return "REVERSE_MENTOR"

    return "SYSTEM_ARCHITECT"


# --- Domain-aware "Try this" (chosen in Python, not left to the model) ---------------------------

DOMAIN_TRY_THIS = {
    "robotics":   "Generate one PWM signal on {timer} and sweep the duty cycle 0-100% — that is motor "
                  "speed control. Watch it in Wokwi before touching a real motor.",
    "motor":      "Sweep a servo 0-180 degrees at 50Hz PWM — it proves your timer period and "
                  "duty-cycle math are correct.",
    "sensor":     "Write a blocking I2C read of one register and print the raw value over UART — "
                  "before interpreting it, prove the bus is talking.",
    "audio":      "Generate a square wave at 440Hz on a timer output — that is concert A. If you hear "
                  "it, your timer and GPIO are working.",
    "iot":        "Connect to Wi-Fi and print your IP over UART — before sending data, prove the stack "
                  "initialises cleanly.",
    "bootloader": "Write 4 bytes to flash, read them back, and compare — if they match, your flash "
                  "driver works.",
    "driver":     "Write to one register, read it back, and verify the value — before any protocol, "
                  "prove register access works.",
    "default":    "Enable the peripheral clock, configure one pin, and toggle it in a loop — before "
                  "any protocol, prove the pin moves.",
}

_TRY_THIS_KEYWORDS = (
    ("bootloader", ("bootloader", "fail-safe", "failsafe", "ota", "rollback")),
    ("driver",     ("driver", "device tree", "of_match", "register map")),
    ("motor",      ("servo",)),
    ("robotics",   ("robot", "robotics", "motor", "wheel", "drone", "bldc", "stepper", "actuator")),
    ("sensor",     ("sensor", "imu", "accelerometer", "gyro", "temperature", "humidity", "distance",
                    "lidar", "proximity")),
    ("audio",      ("audio", "sound", "speaker", "microphone", "music", "i2s")),
    ("iot",        ("iot", "wifi", "wi-fi", "bluetooth", "ble", "mqtt", "internet of")),
)


def _select_try_this(text: str, family: str | None) -> str:
    """The first experiment to run, matched to the project type the user named. Family-gated: the
    robotics/motor experiment names TIM1 only on STM32, a generic timer output elsewhere. When no
    project type is named, fall back to the board family's own experiment (F_CPU on AVR, RCC on
    STM32, …) rather than a generic default."""
    low = " " + (text or "").lower() + " "
    key = next((k for k, kws in _TRY_THIS_KEYWORDS if any(w in low for w in kws)), None)
    if key is None:
        return _board_try_this(family)
    t = DOMAIN_TRY_THIS[key]
    if "{timer}" in t:
        t = t.replace("{timer}", "TIM1" if family == "stm32" else "a timer output")
    return t


# --- One self-contained few-shot prompt per role (the model never decides which role to play) ----

_ARCH_GUARD = ("\nNote: the register names and AF numbers in the examples are STM32 teaching values. "
               "Reason for the board above; never assert a register, clock, or timing value you were "
               "not given — name the datasheet table to confirm it instead.")


def _role_ctx(board_name, soc, board, caps_set, project, step, current_code,
              family=None) -> dict:
    flash, ram = board.get("flash_bytes"), board.get("ram_bytes")
    return {"board_name": board_name, "arch": soc["arch"], "family": family,
            "peripherals": ", ".join(sorted(caps_set)) or "(none recorded)",
            "flash": f"{flash // 1024}KB" if isinstance(flash, int) else "unknown",
            "ram": f"{ram // 1024}KB" if isinstance(ram, int) else "unknown",
            "project": project or "(none)", "step": step or "(not started)",
            "current_code": (current_code or "(none provided)")[:1500]}


_ARCHITECT_TEMPLATE = """You are a battle-tested principal firmware engineer. Your job is to force the
student to think before they touch code. You never answer a design question without first establishing
what the student is trying to learn and what trade-offs matter to them.

Board: {board_name} | Arch: {arch}
Peripherals: {peripherals}
Flash: {flash} | RAM: {ram}
Project: {project} | Stage: {step}

STUDY THESE EXAMPLES:

EXAMPLE 1 — "can I do robotics with this board?"
WRONG: "Yes, the board has GPIO, I2C, SPI, timers and UART, making it suitable for many applications.
Try blinking an LED first."
RIGHT: "Yes — and let's think about what robotics actually needs first. It needs motor control,
sensing, and timing. Motor control: an advanced-control timer (TIM1 on STM32) has complementary
outputs, which drive both sides of an H-bridge without external logic inversion; a basic timer like
TIM9 cannot. Never connect a motor straight to a GPIO pin — back-EMF destroys it. Sensing: I2C and SPI
cover almost every robot sensor. Before I suggest what to build first: what kind of robot — wheels and
motors, a robotic arm, or something else? That changes which peripheral you learn first and why."

EXAMPLE 2 — "should I use HAL or bare metal?"
WRONG: "Both have advantages. HAL is easier; bare metal gives more control."
RIGHT: "What are you trying to learn here? HAL hides the register boundaries — fine for shipping under
deadline, but it leaves you blind to how the bus moves data: you call HAL_UART_Transmit() and it
works, but you don't know why, which is a problem when it stops. Bare metal maps USART1 to its
registers — slower to write, but you understand every bit. Since you said you want to learn firmware,
not just ship: bare metal first. What matters more right now — getting something working fast, or
understanding why it works?"

EXAMPLE 3 — "which board for a WiFi temperature sensor?"
WRONG: "The ESP32 is a popular choice with good community support."
RIGHT: "Three needs: a sensor interface (I2C or SPI), WiFi, and enough processing that the WiFi stack
doesn't starve your reads. That narrows it to boards with integrated WiFi. The ESP32 wins on cost,
dual-core, integrated WiFi/BLE, I2C and SPI. The constraint tutorials skip: its WiFi stack runs on one
core, so blocking sensor code on that core trips the watchdog — pin the sensor task to the other core
or use non-blocking reads. One question first: does this run on battery? That changes the architecture."

Now respond to the user in exactly this style. Reason from the board's specific peripherals above.
Never list all peripherals generically. Never suggest blinking an LED when the user asked about
something else. End with exactly one specific question.""" + _ARCH_GUARD


_PEER_HEAD = """You are a colleague sitting next to the engineer who fell into the same trap
yesterday. You look at their code and find the ONE hardware consequence they have not considered. You
never comment on style or unused variables.

Board: {board_name} | Arch: {arch}
Peripherals: {peripherals}
Flash: {flash} | RAM: {ram}
Current code:
{current_code}

STUDY THESE EXAMPLES (they are for THIS board's chip family — reason from them):
"""

_PEER_TAIL = """
Now review the user's code or question in exactly this style. Find the one hardware consequence they
have not considered. Never comment on style. End with one specific question about their code.""" \
    + _ARCH_GUARD

# One BAD/GOOD pair per chip family — the model sees the family that matches the actual board.
_PEER_EXAMPLES = {
    "stm32": """
EXAMPLE 1 — "my code compiles but nothing happens" (STM32)
WRONG: "There could be several reasons. Check your connections and toolchain."
RIGHT: "Three things cause this on STM32, in order: 1) Clock not enabled — every peripheral is off by
default; configuring UART registers before enabling its APB clock writes to dead hardware, no error,
just silence. Did you enable the USART clock in RCC first? 2) Wrong alternate function — PA9 can be
USART1_TX (AF7) or TIM1_CH2 (AF1); on AF1 the pin is a timer output, not UART. Which AF did you write
to GPIOA->AFR? 3) Wrong baud divisor — if BRR is computed for the wrong clock, the terminal sees
garbage. Which of the three did you check first?"

EXAMPLE 2 — engineer submits UART init code
WRONG: "Looks good. Add error handling and use const where appropriate."
RIGHT: "You enabled the USART clock and set the pin modes — clean. One thing before you flash: which
alternate-function number did you pass to the TX pin? If it's the timer AF instead of the UART AF, the
code compiles, the board is silent, the console is dead. Check the alternate-function table — what AF
value did you write?"
""",
    "avr": """
EXAMPLE 1 — "my code compiles but nothing happens" (AVR / ATmega)
WRONG: "Make sure the peripheral clock is enabled before you use the peripheral."
RIGHT: "On AVR there is no peripheral clock to gate — the #1 cause is F_CPU and the fuse bits. If the
F_CPU you define in code does not match the clock the fuses actually select (the internal 1MHz/8MHz
oscillator vs an external 16MHz crystal), then _delay_ms() runs at the wrong speed AND your UART baud
divisor (UBRR, computed from F_CPU) is wrong — the serial monitor shows garbage or nothing. Does your
F_CPU match the clock the low fuse (CKSEL) selects? And is UBRR computed from that same F_CPU? Which
did you check first — the fuse/clock, or the baud math?"
""",
    "esp32": """
EXAMPLE 1 — "my code compiles but nothing happens" (ESP32)
WRONG: "Check which alternate-function number you assigned to the pin."
RIGHT: "On ESP32 the cause is rarely a pin function — it is usually that you blocked the WiFi core. The
WiFi/BLE stack runs on Core 0; a long blocking loop on that core (a busy-wait, a long delay with no
yield, a blocking read) starves the idle task, the task watchdog fires, and the chip reboots — it
looks like 'nothing happens' but is a reboot loop. Are you blocking on Core 0? Pin the heavy task to
Core 1 with xTaskCreatePinnedToCore, or break it up with vTaskDelay so the watchdog is fed. Second:
did nvs_flash_init() run before any WiFi call — a bad NVS partition reboot-loops too. Which are you
hitting — a blocked core, or NVS?"
""",
    "rp2040": """
EXAMPLE 1 — "my code compiles but nothing happens" (RP2040)
WRONG: "Enable the APB2 clock for the peripheral first."
RIGHT: "RP2040 has no APB2 — that is an STM32 register. Two RP2040-specific traps cause silent failure:
1) Multicore — if you launched code on core1 (multicore_launch_core1) and it touches a resource core0
also uses without a lock or the inter-core FIFO, you get a race that hangs one core with no error.
2) PIO — a PIO state machine needs its clock divider set, its program loaded, AND the state machine
enabled (pio_sm_set_enabled); miss the enable and the PIO sits idle silently. Which are you using —
multicore, or PIO? That tells us where the silence is."
""",
}
_PEER_EXAMPLES["default"] = _PEER_EXAMPLES["stm32"]


_REVERSE_TEMPLATE = """You are a mentor who knows the difference between what matters in a real circuit
and what the simulator handles for you. You never block a beginner from learning because the simulator
skips a physical constraint they don't need yet.

Board: {board_name} | Arch: {arch}
Target: Wokwi simulation (not physical hardware)
Peripherals: {peripherals}

STUDY THESE EXAMPLES:

EXAMPLE 1 — "the validator is blocking my bootloader export" (boot-pin UNKNOWN)
WRONG: "You need to configure your boot pins before export."
RIGHT: "In Wokwi you don't need to worry about boot pins — the simulator handles boot mode
automatically. Boot-pin configuration only matters with a physical programmer on real hardware. For
your simulation, proceed; the physical boot-pin warning is advisory here, not a blocker. When you move
to a real board, that's when you confirm BOOT0 is LOW for normal boot and HIGH to enter the bootloader
for flashing — a real-hardware concern, not Wokwi."

EXAMPLE 2 — "I want to test my power sequencing in Wokwi"
WRONG: "Wokwi supports power sequencing simulation."
RIGHT: "Wokwi doesn't emulate power-rail sequencing — that's physical behaviour the simulator skips.
What it emulates well: GPIO, UART, SPI, I2C, timers, interrupts — enough for most beginner and
intermediate firmware. Power sequencing matters when multiple rails must come up in order to protect
hardware — a real-PCB concern. For now, what are you actually trying to test? If it's code logic, Wokwi
helps; if it's hardware power behaviour, you need a real board."

Now respond knowing the user is on Wokwi. Never block learning with physical-hardware constraints the
simulator doesn't need. Explain what Wokwi does and does not emulate when relevant, and end with one
question.""" + _ARCH_GUARD


def build_architect_prompt(ctx: dict) -> str:
    return _ARCHITECT_TEMPLATE.format(**ctx)


def build_peer_mentor_prompt(ctx: dict) -> str:
    examples = _PEER_EXAMPLES.get(ctx.get("family") or "default", _PEER_EXAMPLES["default"])
    return _PEER_HEAD.format(**ctx) + examples + _PEER_TAIL


def build_reverse_mentor_prompt(ctx: dict) -> str:
    return _REVERSE_TEMPLATE.format(**ctx)


_ROLE_BUILDERS = {"SYSTEM_ARCHITECT": build_architect_prompt,
                  "PEER_MENTOR": build_peer_mentor_prompt,
                  "REVERSE_MENTOR": build_reverse_mentor_prompt}


def _followup_question(caps: list, path: list) -> str:
    if any(c["capability"] == "uart" for c in caps):
        return "Question: do you know which clock your UART runs on?"
    if path:
        return f"Question: are you ready to start with '{path[0]['title']}'?"
    return "Question: what would you like to build first?"


_PROGRESS_Q = ("how am i doing", "what's next", "whats next", "what next", "my progress",
               "where am i", "am i done", "how far")


def _progress_summary(conn, project_name: str | None):
    """The State Engine's next-incomplete item for this project (or None). Deterministic."""
    if not project_name:
        return None
    p = repo.get_project(conn, project_name)
    if p is None:
        return None
    from .engines.state import project_status
    s = project_status(conn, p)
    return s


def mentor_chat(conn: sqlite3.Connection, board_name: str, messages: list[dict],
                use_llm: bool = False, gateway: Gateway | None = None,
                project: str | None = None, has_hardware: bool = False,
                extra_context: str = "", page_type: str = "", current_code: str = "") -> str:
    """A 2-way mentor turn. ``messages`` is the conversation so far ([{role, content}, ...]).
    Always returns an answer + a board-tied 'Try this' + a follow-up question (the contract holds
    even offline). The post-filter runs on every LLM response. ``project`` lets the mentor read the
    State Engine for progress questions; ``has_hardware`` ties the example to Wokwi when False."""
    from .mentor import family_of
    board, soc = repo.load_board(conn, board_name)
    if board is None:
        return ("I can't find that board. Pick one from the Boards list, then ask me again. "
                "Try this: open the Boards tab and click a board to see what it can do. "
                "Question: which board do you have?")
    caps = mentor.capability_map(conn, board_name)
    path = mentor.learning_path_for(conn, {c["capability"] for c in caps})
    fam = family_of(soc["name"])
    last_user = next((m["content"] for m in reversed(messages) if m.get("role") == "user"), "")
    concept = _detect_concept(conn, last_user)
    domain = _detect_domain(last_user)
    cap_set = {c["capability"] for c in caps}
    role = detect_mentor_role(last_user, {"page_type": page_type,
                                          "wokwi_flag": (not has_hardware),
                                          "current_code": current_code})
    try_this = _select_try_this(last_user, fam)        # Fix 4: domain-aware, family-gated
    if not has_hardware and "wokwi" not in try_this.lower():
        try_this += " (run it in the Wokwi simulator — no hardware needed)."
    question = _followup_question(caps, path)
    progress = _progress_summary(conn, project)

    # "How am I doing / what's next" -> answer straight from the State Engine (never the LLM).
    if progress and any(k in last_user.lower() for k in _PROGRESS_Q):
        if progress["next"]:
            head = (f"You're at {progress['complete']}/{progress['total']} "
                    f"({progress['percent']}%). Your next task is '{progress['next']['title']}'. "
                    f"Why it matters: {progress['next']['why_it_matters']}")
        else:
            head = (f"You're at {progress['complete']}/{progress['total']} — every item is "
                    "complete. Nice work.")
        return f"{head}\n\nTry this: {try_this}\n\n{question}"

    # Deterministic backbone — a real answer even with no model, always ending in an action.
    # A named project type reasons about THIS board's specific peripherals (v2.4.1), ahead of the
    # generic "start with blink" default.
    if domain:
        head = _domain_reasoning(domain, cap_set, fam, board_name)
    elif concept is not None:
        head = f"{concept['anchor']}"
    elif path:
        head = (f"Good question. For {board_name}, the place to start is '{path[0]['title']}' — "
                f"{path[0]['why']}")
    else:
        head = f"For {board_name}, tell me what you want to do and I'll point you at the first step."
    backbone = f"{head}\n\nTry this: {try_this}\n\n{question}"

    gw = gateway or Gateway()
    note = _llm_or_note(use_llm, gw)
    if note is not None:
        return backbone                              # offline: the structured deterministic answer

    # Role C — the Validation Engine's verdict is deterministic; the chat points there, never the model.
    if role == "SPONSOR":
        return ("That's the Validation Engine's call, not mine — open Validate or Export to see "
                "exactly what passed, failed, or is unknown, and why. I explain those results; I "
                "never override them.\n\nTry this: run Validate and read the first FAIL or UNKNOWN.\n\n"
                "Question: which check is blocking you?")

    cap_lines = "\n".join(f"- {c['summary'] or c['capability']}" for c in caps)
    have = ", ".join(sorted(c["capability"] for c in caps)) or "(none recorded)"
    path_lines = "\n".join(f"{s['step']}. {s['title']} — {s['why']}" for s in path)
    history = "\n".join(f"{m.get('role','user').upper()}: {m.get('content','')}"
                        for m in messages[-8:])
    hw = ("The user has a PHYSICAL board." if has_hardware
          else "The user is on the Wokwi simulator (NO physical board); tie 'Try this' to Wokwi and "
               "treat physical-only concerns as 'later, on real hardware'.")
    geo = []
    if isinstance(board.get("flash_bytes"), int):
        geo.append(f"flash {board['flash_bytes'] // 1024}KB")
    if isinstance(board.get("ram_bytes"), int):
        geo.append(f"RAM {board['ram_bytes'] // 1024}KB")
    geo_line = ("Geometry: " + ", ".join(geo) + "\n") if geo else ""
    step_line = f"Current learning step: {path[0]['title']}\n" if path else ""
    prog_line = ""
    if progress and progress["next"]:
        prog_line = (f"Project '{project}' progress: {progress['complete']}/{progress['total']} "
                     f"done; next task: {progress['next']['title']}.\n")
    extra_line = (extra_context.strip() + "\n") if extra_context and extra_context.strip() else ""
    dom_block = ""
    if domain:
        dom_block = (f"PROJECT DOMAIN — the user named a '{domain}' project. Reason about WHICH of "
                     "this board's peripherals it needs and why; build on this, stay specific:\n"
                     + _domain_reasoning(domain, cap_set, fam, board_name) + "\n")
    prompt = (f"CONTEXT\nBoard: {board_name} ({soc['arch']})\n{hw}\n"
              f"This board HAS these peripherals: {have}\n{geo_line}{step_line}{prog_line}{dom_block}{extra_line}"
              f"Capabilities (reason from these, not generic):\n{cap_lines}\nLearning path:\n{path_lines}\n"
              + (f"Concept anchor (true; build on this): {concept['anchor']}\n" if concept else "")
              + f"When you suggest an experiment, use this 'Try this': {try_this}\n"
              + f"\nCONVERSATION SO FAR:\n{history}\n\nReply now (answer + Try this + a question):")
    # The model receives a prompt that already IS the detected role (board context before examples).
    step = path[0]["title"] if path else None
    system = _ROLE_BUILDERS.get(role, build_architect_prompt)(
        _role_ctx(board_name, soc, board, cap_set, project, step, current_code, fam))
    raw = gw.provider.generate(system, prompt)
    filtered, removed = filter_text(raw, build_board_allowlist(conn, board_name))
    answer = filtered.strip()
    # Enforce the contract even if the model (or the post-filter) dropped a part.
    if len(answer) < 20:
        answer = backbone
    else:
        if "try this" not in answer.lower():
            answer += f"\n\nTry this: {try_this}"
        if not answer.rstrip().endswith("?") and "question:" not in answer.lower():
            answer += f"\n\n{question}"
    return answer


def mentor_explain(conn: sqlite3.Connection, board_name: str, concept: str,
                   use_llm: bool = False, gateway: Gateway | None = None) -> str:
    soc, _caps, _path = _ctx(conn, board_name)
    anchor = repo.get_concept(conn, concept)
    if anchor is None:
        known = ", ".join(r["name"] for r in repo.list_concepts(conn))
        base = (f"'{concept}' isn't in the concept library yet. Known: {known}.")
    else:
        base = f"{anchor['name']}: {anchor['anchor']}"

    gw = gateway or Gateway()
    note = _llm_or_note(use_llm, gw)
    if note is not None:
        return base + note

    prompt = (f"Concept: {concept}\nBoard architecture: {soc['arch']}\n"
              f"Factual anchor (true; build on this): {anchor['anchor'] if anchor else '(none)'}\n"
              f"Explain in at most two sentences (what it is; what to check next):")
    raw = gw.provider.generate(_EXPLAIN_SYSTEM, prompt)
    filtered, removed = filter_text(raw, build_board_allowlist(conn, board_name))
    return f"{base}\n\n{filtered}\n[mentor] {removed} uncited hardware claim(s) removed."
