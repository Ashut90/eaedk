"""The central Navigator — classifies a confused user's message into a confusion TYPE and routes to
the right kind of guidance. These test the general routing mechanism, not the example phrasings.
"""
from eaedk.store.db import connect
from eaedk.store.migrate import migrate
from eaedk.seed import seed_all
from eaedk import navigator as nav
from eaedk.mentor_llm import decide_purpose, mentor_chat

BOARD = "STM32F103-BluePill"


def _seeded(tmp_path):
    conn = connect(str(tmp_path / "t.db"))
    migrate(conn)
    seed_all(conn, force=True)
    return conn


def _route(conn, text, msgs=None):
    msgs = msgs or [{"role": "user", "content": text}]
    purpose = decide_purpose(conn, BOARD, text, {}, msgs)
    return nav.classify(purpose, msgs)


# --- The five+1 modes are distinguishable (acceptance 2) ----------------------------------------

def test_broken_system_routes_to_proof_path(tmp_path):
    conn = _seeded(tmp_path)
    assert _route(conn, "my UART is not working").mode == nav.PROOF_PATH


def test_learning_direction_routes_to_learning_map(tmp_path):
    conn = _seeded(tmp_path)
    assert _route(conn, "kernel programming").mode == nav.LEARNING_MAP
    assert _route(conn, "how do I get into driver development?").learning_map.name == "embedded_linux"
    assert _route(conn, "Yocto").learning_map.name == "build_systems"


def test_engineering_decision_routes_to_decision_map(tmp_path):
    conn = _seeded(tmp_path)
    assert _route(conn, "RTOS or Linux?").mode == nav.DECISION_MAP
    assert _route(conn, "should I use HAL or bare metal?").mode == nav.DECISION_MAP
    assert _route(conn, "which MCU should I use?").mode == nav.DECISION_MAP


def test_vague_routes_to_clarify(tmp_path):
    conn = _seeded(tmp_path)
    assert _route(conn, "asdkfj qwer help").mode == nav.CLARIFY


def test_out_of_scope_routes_to_decline(tmp_path):
    conn = _seeded(tmp_path)
    assert _route(conn, "How can I use Nvidia Jetson?").mode == nav.DECLINE


def test_career_routes_to_learning_map_foundation(tmp_path):
    conn = _seeded(tmp_path)
    r = _route(conn, "I want to become a firmware engineer but don't know where to start")
    assert r.mode == nav.LEARNING_MAP and r.learning_map is None     # foundation/career route


def test_grounded_concept_routes_to_teach(tmp_path):
    conn = _seeded(tmp_path)
    assert _route(conn, "what is SPI?").mode == nav.TEACH


# --- Proof path wins over everything (conversation-aware) ----------------------------------------

def test_proof_path_takes_precedence_mid_conversation(tmp_path):
    conn = _seeded(tmp_path)
    msgs = [{"role": "user", "content": "my UART is not working"},
            {"role": "assistant", "content": "..."},
            {"role": "user", "content": "TX is silent"}]
    assert nav.classify(decide_purpose(conn, BOARD, "TX is silent", {}, msgs), msgs).mode == nav.PROOF_PATH


# --- Generality: adding a map is a registry entry, matching is data-driven -----------------------

def test_learning_maps_are_a_registry():
    assert set(nav.LEARNING_MAPS) >= {"embedded_linux", "build_systems"}
    # match is AND-of-ORs over the map's own groups — no per-area code
    assert nav.match_learning("device tree probe failure").name == "embedded_linux"
    assert nav.match_learning("how do I use SPI?") is None           # not a learning-direction area


# --- Live: learning map guides DIRECTION, not a board/blink dump (acceptance 4, 8) ---------------

def test_live_kernel_routes_to_learning_map_not_generic(tmp_path):
    conn = _seeded(tmp_path)
    out = mentor_chat(conn, BOARD, [{"role": "user", "content": "kernel programming"}], use_llm=False)
    assert "learning-direction question" in out          # framed as direction
    assert "route that works, in order" in out           # gives a route
    assert "First step to prove you're moving" in out     # one first step
    assert "Blink an LED" not in out                      # NOT the generic board default
    assert "STM32F103-BluePill" not in out               # board-less → no selected-board leak
    # sorts ambiguity — mentions non-Linux kernel meanings
    assert "RTOS kernel internals" in out                 # broader sorting, not Linux-only
    assert "MCU peripheral drivers" in out               # covers bare-metal/HAL driver context
    assert "toy OS" in out or "teaching kernel" in out   # covers OS/kernel learning


def test_live_driver_development_sorts_ambiguity(tmp_path):
    conn = _seeded(tmp_path)
    out = mentor_chat(conn, BOARD, [{"role": "user", "content": "driver development"}], use_llm=False)
    assert "learning-direction question" in out
    assert "MCU peripheral drivers" in out               # specifically covers MCU context
    assert "Linux kernel DRIVER" in out or "Linux kernel/driver" in out
    assert "STM32F103-BluePill" not in out               # board-less → no selected-board leak


def test_yocto_rp2040_uses_user_mentioned_chip_over_selected_board(tmp_path):
    conn = _seeded(tmp_path)
    out = mentor_chat(conn, BOARD, [{"role": "user", "content":
        "how can I start in yocto if I have RP2040 board? Can you suggest me some best project?"}],
        use_llm=False)

    assert "You selected STM32F103-BluePill, but your question says RP2040" in out
    assert "RP2040 is microcontroller-class" in out
    assert "not a Linux-capable Yocto target" in out
    assert "Yocto is for Linux images/rootfs on Linux-capable boards" in out
    assert "Pico SDK GPIO/UART project" in out
    assert "USB CDC/HID project" in out
    assert "FreeRTOS task project" in out
    assert "bootloader / firmware update project" in out
    assert "start with QEMU or a Linux-capable SBC" in out
    assert "HELP — Choose RP2040 firmware path or Yocto/Linux-capable board path" in out
    assert "which board are you targeting" not in out.lower()
    assert "STM32F103-BluePill is a microcontroller" not in out


def test_custom_board_rp2040_kernel_yocto_gets_suitability_split(tmp_path):
    conn = _seeded(tmp_path)
    out = mentor_chat(conn, BOARD, [{"role": "user", "content":
        "I have a custom board with RP2040 chip and I want to develop kernel using Yocto. "
        "Is this the right path?"}], use_llm=False)

    assert "RP2040 is microcontroller-class" in out
    assert "not a Linux-capable Yocto target" in out
    assert "Pico SDK GPIO/UART project" in out
    assert "start with QEMU or a Linux-capable SBC" in out
    assert "COMPARE — RP2040 firmware work is MCU firmware" in out
    assert "do you want RP2040 firmware projects, Yocto learning" in out
    assert "which board are you targeting" not in out.lower()
    assert "STM32F103-BluePill is a microcontroller" not in out


def test_broad_yocto_without_explicit_board_does_not_leak_selected_board(tmp_path):
    """Product rule (updated): a board-LESS broad Yocto question must NOT anchor to the selected board.
    It states the Linux-capable requirement generically and asks the route fork."""
    conn = _seeded(tmp_path)
    out = mentor_chat(conn, BOARD, [{"role": "user", "content": "Yocto"}], use_llm=False)

    assert "STM32F103-BluePill" not in out                          # no selected-board leak
    assert "Linux-capable target" in out and "QEMU" in out          # generic Linux requirement
    assert "Are you targeting app/socket networking" in out         # the route fork, not a board ask
    assert "You selected STM32F103-BluePill, but your question says" not in out


def test_explicit_linux_capable_target_takes_precedence_generally(tmp_path):
    conn = _seeded(tmp_path)
    out = mentor_chat(conn, BOARD, [{"role": "user", "content":
        "I want to learn Yocto on BeagleBone for a custom Linux image."}], use_llm=False)

    assert "You selected STM32F103-BluePill, but your question says BeagleBone" in out
    assert "BeagleBone is Linux-capable" in out
    assert "STM32F103-BluePill is a microcontroller" not in out
    assert "not a Linux-capable Yocto target" not in out
    assert "do you want Yocto learning, a BeagleBone BSP/kernel path" in out


def test_live_uart_still_proof_path(tmp_path):
    conn = _seeded(tmp_path)
    out = mentor_chat(conn, BOARD, [{"role": "user", "content": "my UART is not working"}], use_llm=False)
    assert "UART bring-up problem" in out and "First proof step" in out


def test_live_decision_still_reaches_reasoning_framework(tmp_path):
    conn = _seeded(tmp_path)
    out = mentor_chat(conn, BOARD, [{"role": "user", "content": "should I use HAL or bare metal?"}],
                      use_llm=False)
    assert "trade-off" in out.lower()


# --- Board/chip context precedence is GENERAL (not an RP2040 patch) ------------------------------

def test_stm32_yocto_no_pico_sdk_and_separate_yocto_route(tmp_path):
    conn = _seeded(tmp_path)
    out = mentor_chat(conn, BOARD, [{"role": "user", "content":
        "I have STM32F103 BluePill and want to learn Yocto. Is this board okay?"}], use_llm=False)
    assert "STM32F103" in out
    assert "microcontroller-class" in out and "not a Linux-capable Yocto target" in out
    assert "Pico SDK" not in out                                  # wrong-SDK bug gone
    assert "STM32Cube/HAL" in out
    assert "start with QEMU or a Linux-capable SBC" in out        # separate Yocto/QEMU route
    assert "your board's reference image" not in out             # no contradictory MCU tail
    assert "Pick your board's reference image" not in out


def test_nrf52840_yocto_does_not_hijack_to_stm32(tmp_path):
    conn = _seeded(tmp_path)
    out = mentor_chat(conn, BOARD, [{"role": "user", "content":
        "I have an nRF52840 board and want to start Yocto/kernel work. Is that correct?"}], use_llm=False)
    assert "nRF52840" in out
    assert "STM32F103-BluePill is a microcontroller" not in out   # the hijack bug, gone
    assert "answering for nRF52840" in out
    assert "microcontroller-class" in out and "not a Linux-capable Yocto target" in out
    assert "nRF Connect SDK" in out and "Zephyr" in out
    assert "start with QEMU or a Linux-capable SBC" in out


def test_esp32_yocto_does_not_hijack_uses_esp_idf(tmp_path):
    conn = _seeded(tmp_path)
    out = mentor_chat(conn, BOARD, [{"role": "user", "content":
        "I have an ESP32-S3 and want to start Yocto/kernel work."}], use_llm=False)
    assert "ESP32" in out
    assert "STM32F103-BluePill is a microcontroller" not in out
    assert "microcontroller-class" in out
    assert "ESP-IDF" in out


def test_raspberry_pi_yocto_is_linux_capable(tmp_path):
    conn = _seeded(tmp_path)
    out = mentor_chat(conn, BOARD, [{"role": "user", "content":
        "I have Raspberry Pi and want to start Yocto kernel development. What project should I do?"}],
        use_llm=False)
    assert "Raspberry Pi" in out
    assert "Linux-capable" in out
    assert "cannot run Linux" not in out
    assert "not a Linux-capable Yocto target" not in out
    assert "STM32F103-BluePill is a microcontroller" not in out


def test_unknown_chip_part_asks_class_instead_of_hijacking(tmp_path):
    conn = _seeded(tmp_path)
    out = mentor_chat(conn, BOARD, [{"role": "user", "content":
        "I have an STM32H755 and want to start Yocto/kernel work."}], use_llm=False)
    assert "STM32H755" in out
    assert "I will not assume the selected board is the target" in out
    assert "STM32F103-BluePill is a microcontroller" not in out


# --- P1: multi-sentence questions must not false-decline on a 2nd-sentence capital ---------------

def test_p1_multi_sentence_does_not_false_decline(tmp_path):
    conn = _seeded(tmp_path)
    for q in ("I want to understand communication protocols deeply. How should I start?",
              "I have some embedded experience but communications feel huge. Where do I even begin?"):
        assert _route(conn, q).mode != nav.DECLINE                    # 'How'/'Where' are not subjects
    assert _route(conn, "How can I use Nvidia Jetson?").mode == nav.DECLINE   # real subject still declines


# --- P3: the general classification mechanism + 3 proof-of-concept classes ----------------------

def test_comms_compares_protocols_grouped_by_use_case(tmp_path):
    conn = _seeded(tmp_path)
    out = mentor_chat(conn, BOARD, [{"role": "user", "content":
        "I am confused between UART, SPI, I2C, CAN, Ethernet, BLE, Wi-Fi, and LoRa. "
        "Which direction should I follow?"}], use_llm=False)
    assert "communication systems direction" in out
    assert "wired local bus" in out and "wireless link" in out and "networking" in out  # grouped
    for p in ("UART", "SPI", "I2C", "CAN", "BLE", "Wi-Fi", "LoRa"):                      # compares 4+
        assert p in out
    assert "Blink an LED" not in out and "STM32F103-BluePill" not in out                # no board leak


def test_sdr_gives_sdr_entry_point_not_blink(tmp_path):
    conn = _seeded(tmp_path)
    out = mentor_chat(conn, BOARD, [{"role": "user", "content":
        "I want to learn SDR or RF communication, but I am mostly an embedded software person. "
        "How do I start without getting lost?"}], use_llm=False)
    assert "RF / SDR" in out and "RTL-SDR" in out and "GNU Radio" in out
    assert "Blink an LED" not in out and "Run an RTOS" not in out and "STM32F103-BluePill" not in out


def test_overwhelm_structures_the_domain_not_a_board_roadmap(tmp_path):
    conn = _seeded(tmp_path)
    out = mentor_chat(conn, BOARD, [{"role": "user", "content":
        "I have years of embedded experience but communication systems feel huge. "
        "Where do I even begin?"}], use_llm=False)
    assert "layers" in out and ("cut it into" in out or "spine" in out)
    assert "Blink an LED" not in out and "STM32F103-BluePill" not in out


def test_board_less_uncovered_domain_gets_scoped_uncertainty(tmp_path):
    conn = _seeded(tmp_path)
    out = mentor_chat(conn, BOARD, [{"role": "user", "content":
        "I want to work on audio/video casting or streaming in embedded systems. "
        "What path should I follow?"}], use_llm=False)
    assert "I don't have a structured direction map" in out and "audio/video" in out
    assert "STM32F103-BluePill" not in out and "Blink an LED" not in out


def test_extensibility_new_class_routes_via_data_only(tmp_path):
    """Proof the mechanism is general: register a 4th class as pure DATA and it routes with ZERO
    changes to classify, decide_purpose, or any router code."""
    conn = _seeded(tmp_path)
    dummy = nav.LearningMap(
        name="power_sequencing_basics", title="power sequencing basics",
        match_groups=(("power sequencing", "rail sequencing", "power-up order", "power up order"),),
        overview="Rails must come up in a defined order or you damage parts or fail to boot.",
        sub_routes=(nav.SubRoute("rail order", ("order", "sequence", "rail"),
                                 "bring rails up in the datasheet's specified order with the right delays"),),
        first_step="Draw the rail tree and the required power-up order straight from the datasheet.",
        clarifying_question="which rails do you have, and what order does the datasheet require?",
        board_dependent=False)
    nav.LEARNING_MAPS[dummy.name] = dummy                            # <-- the only change: a data entry
    try:
        q = "how do I get power sequencing right on my custom design?"
        assert _route(conn, q).learning_map.name == "power_sequencing_basics"
        out = mentor_chat(conn, BOARD, [{"role": "user", "content": q}], use_llm=False)
        assert "power sequencing basics direction" in out and "rail" in out.lower()
    finally:
        del nav.LEARNING_MAPS[dummy.name]


# --- Board-less Linux/Yocto/networking must not leak the selected board --------------------------

def test_linux_networking_board_less_no_leak_and_route_fork(tmp_path):
    conn = _seeded(tmp_path)
    out = mentor_chat(conn, BOARD, [{"role": "user", "content":
        "Linux networking + embedded comms: Yocto/kernel drivers/socket first?"}], use_llm=False)
    assert "STM32F103-BluePill" not in out                          # no selected-board leak
    assert "app / socket networking" in out                         # separates socket/app...
    assert "kernel driver / BSP / device tree" in out               # ...from kernel/BSP...
    assert "Yocto / image integration" in out                       # ...from Yocto-image
    assert "Are you targeting app/socket networking, kernel driver/BSP work, or Yocto image integration?" in out


def test_start_yocto_board_less_no_invented_board(tmp_path):
    conn = _seeded(tmp_path)
    out = mentor_chat(conn, BOARD, [{"role": "user", "content": "How can I start Yocto?"}], use_llm=False)
    assert "STM32F103-BluePill" not in out
    for chip in ("RP2040", "nRF52840", "ESP32"):                    # must not INVENT a board
        assert chip not in out
    assert "Linux-capable target" in out and "QEMU" in out          # explains Yocto needs Linux target


def test_explicit_stm32_yocto_still_uses_the_named_board(tmp_path):
    conn = _seeded(tmp_path)
    out = mentor_chat(conn, BOARD, [{"role": "user", "content":
        "I have STM32F103 BluePill and want to learn Yocto. Is this board okay?"}], use_llm=False)
    assert "STM32F103" in out                                       # the user named it → used
    assert "microcontroller-class" in out and "not a Linux-capable Yocto target" in out
    assert "STM32Cube/HAL" in out                                   # firmware route
    assert "start with QEMU or a Linux-capable SBC" in out          # separate Yocto route


def test_active_project_board_context_still_allowed(tmp_path):
    """When the selected board is genuinely in scope (active project), the board aside is allowed."""
    from eaedk import repo
    conn = _seeded(tmp_path)
    repo.create_project(conn, "p1", "bare_metal_app", BOARD)
    out = mentor_chat(conn, BOARD, [{"role": "user", "content": "kernel programming"}],
                      use_llm=False, project="p1")
    assert "STM32F103-BluePill is a microcontroller" in out         # board context is in scope here


# --- Board-less career/foundation + bare 'where do I start?' must not leak the selected board -----

def test_boardless_career_roadmap_does_not_leak_selected_board(tmp_path):
    conn = _seeded(tmp_path)
    out = mentor_chat(conn, BOARD, [{"role": "user", "content":
        "I want to become a firmware engineer. Where should I start?"}], use_llm=False)
    assert "STM32F103-BluePill" not in out and "On this board" not in out
    assert "C fundamentals" in out and "GPIO + UART" in out          # board-independent firmware map
    assert "Are you aiming for MCU firmware, embedded Linux, driver/BSP work, or IoT/product firmware?" in out


def test_boardless_where_do_i_start_does_not_leak_selected_board(tmp_path):
    conn = _seeded(tmp_path)
    out = mentor_chat(conn, BOARD, [{"role": "user", "content": "where do I start?"}], use_llm=False)
    assert "STM32F103-BluePill" not in out
    assert "Blink an LED" not in out and "RCC" not in out            # no board-specific advice
    assert ("Are you trying to learn MCU firmware, Linux/Yocto, communication systems, "
            "debugging, or a specific board project?") in out


def test_this_board_where_do_i_start_keeps_selected_board(tmp_path):
    conn = _seeded(tmp_path)
    out = mentor_chat(conn, BOARD, [{"role": "user", "content": "where do I start on this board?"}],
                      use_llm=False)
    assert "STM32F103-BluePill" in out and "Try this:" in out        # board context allowed here


def test_explicit_stm32_where_do_i_start_keeps_named_board(tmp_path):
    conn = _seeded(tmp_path)
    out = mentor_chat(conn, BOARD, [{"role": "user", "content":
        "I have STM32F103 BluePill. Where should I start?"}], use_llm=False)
    assert "STM32F103-BluePill" in out                               # the named board → board-specific


# --- Conversational mentor (docs/34): one path = communication_systems --------------------------

class _TeachGw:
    """Fake gateway that records the prompt and returns a fixed teaching answer."""
    model = "fake"

    def __init__(self, text):
        self.text = text
        self.provider = self
        self.calls = []

    def available(self):
        return True

    def generate(self, system, prompt):
        self.calls.append((system, prompt))
        return self.text


_COMMS_Q = ("I am confused between UART, SPI, I2C, CAN, Ethernet, BLE, Wi-Fi, and LoRa. "
            "Which direction should I follow?")


def test_comms_is_taught_by_llm_with_followups(tmp_path):
    conn = _seeded(tmp_path)
    teach = ("Let's cut through it. For chip-to-chip use a wired bus; for device-to-device go wireless; "
             "to a host use networking. Start by writing down your range, power, and data-rate budget.")
    out = mentor_chat(conn, BOARD, [{"role": "user", "content": _COMMS_Q}],
                      use_llm=True, gateway=_TeachGw(teach))
    assert teach in out                                  # the LLM's own teaching, not a template
    assert "This is a communication systems direction" not in out   # NOT the deterministic render
    assert "Where next? You could ask:" in out and "Compare BLE vs Wi-Fi vs LoRa" in out  # follow-ups


def test_comms_teach_blocks_invented_board_fact_and_falls_back(tmp_path):
    conn = _seeded(tmp_path)
    dirty = "Easy — just put SPI2 on PB13/PB14 and set the clock to 10MHz at 0x40013800."
    out = mentor_chat(conn, BOARD, [{"role": "user", "content": _COMMS_Q}],
                      use_llm=True, gateway=_TeachGw(dirty))
    assert "PB13" not in out and "10MHz" not in out and "0x40013800" not in out   # invented → blocked
    assert "This is a communication systems direction" in out                     # safe deterministic


def test_comms_offline_is_deterministic(tmp_path):
    conn = _seeded(tmp_path)
    out = mentor_chat(conn, BOARD, [{"role": "user", "content": _COMMS_Q}], use_llm=False)
    assert "This is a communication systems direction" in out and "Where next?" not in out


def test_comms_teach_is_multi_turn_history_aware(tmp_path):
    conn = _seeded(tmp_path)
    gw = _TeachGw("Sure — BLE vs LoRa for a battery sensor: LoRa wins on range and power.")
    msgs = [{"role": "user", "content": _COMMS_Q},
            {"role": "assistant", "content": "...the three layers..."},
            {"role": "user", "content": "compare BLE vs LoRa for a battery sensor"}]
    mentor_chat(conn, BOARD, msgs, use_llm=True, gateway=gw)
    _system, prompt = gw.calls[0]
    assert "battery sensor" in prompt and "UART, SPI, I2C" in prompt   # full conversation reached the model
    assert "VERIFIED FACT PACKET" in prompt                            # grounded teach, not freeform
