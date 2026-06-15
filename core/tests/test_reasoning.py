"""Golden tests for v2.6.0 — the engineering-reasoning framework (docs/27).

The mentor teaches thinking, not answers: a design question renders the framework (what / why /
options / trade-offs / how-to-decide / next), the reasoning is board-agnostic, and the design
backbone leads with the problem, never with implementation.
"""
from eaedk.store.db import connect
from eaedk.store.migrate import migrate
from eaedk.seed import seed_all
from eaedk import reasoning, mentor_llm


def _seeded(tmp_path):
    conn = connect(str(tmp_path / "t.db"))
    migrate(conn)
    seed_all(conn, force=True)
    return conn


def test_detect_topic_maps_engineering_decisions():
    assert reasoning.detect_topic("should I use HAL or bare metal?").key == "hal_vs_baremetal"
    assert reasoning.detect_topic("do I need an RTOS?").key == "rtos_vs_superloop"
    assert reasoning.detect_topic("polling or interrupts?").key == "polling_vs_interrupt_vs_dma"
    assert reasoning.detect_topic("which board for a wifi project?").key == "board_selection"
    assert reasoning.detect_topic("how do I build a fail-safe bootloader?").key == "failsafe_bootloader"
    assert reasoning.detect_topic("what's a good watchdog strategy?").key == "watchdog_strategy"
    assert reasoning.detect_topic("just blink an led") is None        # not an engineering decision


def test_render_contains_every_framework_axis():
    out = reasoning.render(reasoning.TOPICS["hal_vs_baremetal"])
    for axis in ("The problem", "Why it exists", "When it applies", "Your options:",
                 "The trade-off", "How an engineer decides", "Next:"):
        assert axis in out, axis
    assert "Bare-metal" in out and "?" in out          # alternatives + decision questions present


def test_reasoning_is_board_agnostic():
    # a no-enrich topic renders identically on any board — the thinking survives a change of board.
    a = reasoning.render(reasoning.TOPICS["hal_vs_baremetal"], "Nucleo-F411RE", "arm-cortex-m4", "stm32")
    b = reasoning.render(reasoning.TOPICS["hal_vs_baremetal"], "ESP32-DevKitC", "xtensa-lx6", "esp32")
    assert a == b


def test_board_facts_enrich_but_do_not_drive():
    stm = reasoning.render(reasoning.TOPICS["rtos_vs_superloop"], "Nucleo-F411RE", "arm-cortex-m4",
                           "stm32", ram_kb=128)
    uno = reasoning.render(reasoning.TOPICS["rtos_vs_superloop"], "Arduino-Uno", "avr", "avr", ram_kb=2)
    assert "128KB" in stm and "2KB" in uno                          # the board enriches the trade-off
    assert stm.splitlines()[0] == uno.splitlines()[0]              # same problem statement, any board


def test_design_backbone_leads_with_problem_not_implementation(tmp_path):
    conn = _seeded(tmp_path)
    out = mentor_llm.mentor_chat(conn, "STM32F103-BluePill",
                                 [{"role": "user", "content": "should I use HAL or bare metal?"}],
                                 use_llm=False)
    assert out.strip().splitlines()[0].startswith("The problem")   # never opens with code/registers
    assert "Your options:" in out and "trade-off" in out.lower()   # teaches options + trade-offs
    assert "decides" in out.lower()                                # teaches the decision process


# --- v2.6.1: three new topics (clock tree, interrupt vs polling, memory layout) -------------

def test_new_topics_detected():
    assert reasoning.detect_topic("do I need to enable the peripheral clock?").key == "clock_tree"
    assert reasoning.detect_topic("my peripheral does nothing").key == "clock_tree"
    assert reasoning.detect_topic("interrupt vs polling?").key == "interrupt_vs_polling"
    assert reasoning.detect_topic("is this variable shared with an interrupt safe?").key == "interrupt_vs_polling"
    assert reasoning.detect_topic("where do my variables live?").key == "memory_layout"
    assert reasoning.detect_topic("what does a stack overflow look like?").key == "memory_layout"


def test_interrupt_topic_does_not_steal_the_broad_polling_topic():
    # the existing "how do I move data" topic must still win for the general phrasing
    assert reasoning.detect_topic("polling or interrupts?").key == "polling_vs_interrupt_vs_dma"


def test_new_topics_render_all_axes():
    for key in ("clock_tree", "interrupt_vs_polling", "memory_layout"):
        out = reasoning.render(reasoning.TOPICS[key])
        for axis in ("The problem", "Your options:", "The trade-off", "How an engineer decides", "Next:"):
            assert axis in out, (key, axis)


def test_memory_enrichment_scales_with_ram():
    tiny = reasoning.render(reasoning.TOPICS["memory_layout"], "Arduino-Uno", "avr", "avr", ram_kb=2)
    big = reasoning.render(reasoning.TOPICS["memory_layout"], "Nucleo-F411RE", "arm-cortex-m4",
                           "stm32", ram_kb=128)
    assert "stack overflow is your FIRST enemy" in tiny       # 2KB
    assert "stack guard" in big                               # 128KB


def test_clock_enrichment_is_family_aware():
    stm = reasoning.render(reasoning.TOPICS["clock_tree"], "Nucleo-F411RE", "arm-cortex-m4", "stm32")
    avr = reasoning.render(reasoning.TOPICS["clock_tree"], "Arduino-Uno", "avr", "avr")
    assert "RCC" in stm and "PRR" in avr and "RCC" not in avr  # the right chip's clock mechanism


# --- P2: linker-script questions route to a mandatory-not-optional teach path ---------------

def test_p2_linker_script_routing_and_content():
    for q in ("Why does this memory.ld file exist?", "what is a linker script?",
              "what goes in the .bss section?", "explain the flash layout"):
        assert reasoning.detect_topic(q).key == "linker_script", q
    out = reasoning.render(reasoning.TOPICS["linker_script"], "STM32F103-BluePill", "arm-cortex-m3",
                           "stm32", ram_kb=20, flash_kb=64, flash_base=0x08000000, ram_base=0x20000000)
    assert "MANDATORY" in out                                    # not optional
    assert "no operating system" in out.lower() and "loader" in out.lower()   # why: no OS loader
    assert "0x08000000" in out and "0x20000000" in out          # real board ORIGIN values
    assert "IAR" not in out and "Keil" not in out               # no proprietary IDE pushed
