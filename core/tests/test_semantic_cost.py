"""Golden evals for v3.0 P2 — semantic intent translation (cost table + validate + chat)."""
from eaedk.store.db import connect
from eaedk.store.migrate import migrate
from eaedk.seed import seed_all
from eaedk import repo
from eaedk import semantic_cost as sc
from eaedk.mentor_llm import mentor_chat


def _seeded(tmp_path):
    conn = connect(str(tmp_path / "t.db"))
    migrate(conn)
    seed_all(conn, force=True)
    return conn


# --- 2A: the cost table is seeded, ranged, and flagged unverified -------------------------

def test_2a_cost_table_seeded_unverified(tmp_path):
    conn = _seeded(tmp_path)
    rows = {r["term"]: r for r in repo.all_semantic_costs(conn)}
    for term in ("grpc", "tls_mbedtls", "mqtt_paho", "freertos", "tflite_micro", "lwip", "fatfs"):
        assert term in rows, term
        r = rows[term]
        assert r["flash_min_bytes"] <= r["flash_max_bytes"]
        assert r["ram_min_bytes"] <= r["ram_max_bytes"]
        assert r["verified_by_human"] == 0               # honest: unverified until a human confirms
        assert r["source"]                               # every entry carries a citation


# --- 2C: validate --intent golden ---------------------------------------------------------

def test_2c_grpc_tls_on_blue_pill_fails_with_math(tmp_path):
    conn = _seeded(tmp_path)
    known, unknown = sc.classify_terms("grpc tls")
    res = sc.assess(conn, "STM32F103-BluePill", known, unknown)
    # GOLDEN: summed flash ~500-800KB > 64KB, summed RAM ~70-130KB > 20KB -> FAIL
    assert res["verdict"] == "FAIL"
    assert res["flash_min"] == 409600 + 102400           # 500KB
    assert res["flash_max"] == 614400 + 204800           # 800KB
    assert res["ram_min"] == 51200 + 20480               # 70KB
    assert res["board_flash"] == 65536 and res["board_ram"] == 20480
    out = sc.render(res)
    assert "Verdict: FAIL" in out
    assert "500KB" in out and "64KB" in out              # the math is shown, not just the verdict
    assert "exceeds" in out


def test_2c_freertos_fatfs_fits_blue_pill(tmp_path):
    conn = _seeded(tmp_path)
    known, unknown = sc.classify_terms("freertos fatfs")
    res = sc.assess(conn, "STM32F103-BluePill", known, unknown)
    # FreeRTOS (5-15K) + FatFs (8-15K) max = 30K flash < 64K, RAM max 16K < 20K -> PASS
    assert res["verdict"] == "PASS"


def test_2c_unknown_term_is_flagged_not_silent(tmp_path):
    conn = _seeded(tmp_path)
    known, unknown = sc.classify_terms("coap grpc")
    res = sc.assess(conn, "STM32F103-BluePill", known, unknown)
    assert "coap" in res["unknown_terms"]
    assert "no cost data for: coap" in sc.render(res).lower()


# --- 2B: chat injects the cost data --------------------------------------------------------

def test_2b_chat_injects_cost_data_and_verdict(tmp_path):
    conn = _seeded(tmp_path)
    out = mentor_chat(conn, "STM32F103-BluePill",
                      [{"role": "user", "content": "I want to add gRPC and TLS to this"}],
                      use_llm=False)
    assert "Based on known cost data:" in out
    assert "64KB flash" in out
    assert "will NOT fit" in out                          # the feasibility conclusion, grounded


def test_2b_chat_unknown_term_asks_for_numbers(tmp_path):
    conn = _seeded(tmp_path)
    out = mentor_chat(conn, "STM32F103-BluePill",
                      [{"role": "user", "content": "can I run CoAP on here?"}], use_llm=False)
    assert "don't have cost data for coap" in out.lower()
    assert "estimated_image_size" in out


def test_2b_no_note_when_no_intent_mentioned(tmp_path):
    conn = _seeded(tmp_path)
    out = mentor_chat(conn, "STM32F103-BluePill",
                      [{"role": "user", "content": "how do I blink an LED?"}], use_llm=False)
    assert "Based on known cost data" not in out          # no false trigger on ordinary questions


# --- v3.1 Gap 3: ML/AI intent is costed (TFLite baseline) ---------------------------------

def test_gap3_ai_aliases_and_phrases_map_to_tflite():
    for q in ("uses AI to recognize hand gestures", "I want a neural network", "tflite inference",
              "machine learning on this", "edge ai gesture recognition", "object detection"):
        assert "tflite_micro" in sc.parse_intent(q), q
    assert sc.parse_intent("just blink an LED") == []     # no false positive


def test_gap3_gesture_ai_is_infeasible_on_blue_pill(tmp_path):
    conn = _seeded(tmp_path)
    terms = sc.parse_intent("a robot that uses AI to recognize hand gestures")
    res = sc.assess(conn, "STM32F103-BluePill", terms)
    assert "tflite_micro" in [r["term"] for r in res["terms"]]
    assert res["verdict"] == "FAIL"                       # 200-300KB flash vs 64KB
    assert res["flash_min"] == 204800                     # TFLite-Micro min


def test_gap3_chat_now_catches_ai_intent(tmp_path):
    """The Prompt-1 miss: 'AI to recognize hand gestures' on a Blue Pill must now be flagged."""
    conn = _seeded(tmp_path)
    out = mentor_chat(conn, "STM32F103-BluePill",
                      [{"role": "user", "content": "I want a robot that uses AI to recognize hand "
                        "gestures. Can I do that on my Blue Pill?"}], use_llm=False)
    assert "Based on known cost data" in out
    assert "will NOT fit" in out


# --- v3.1 Gap 4: peripheral prerequisites (a network stack needs a NIC) --------------------

def test_gap4_mqtt_fails_on_board_without_nic(tmp_path):
    conn = _seeded(tmp_path)
    res = sc.assess(conn, "STM32F103-BluePill", ["mqtt_paho"])   # fits memory, but no wifi/ethernet
    assert res["verdict"] == "FAIL"
    assert res["peripheral_failures"] == [{"term": "mqtt_paho", "requires": ["wifi", "ethernet"]}]
    assert any("does not have" in r for r in res["reasons"])


def test_gap4_mqtt_passes_on_wifi_board(tmp_path):
    conn = _seeded(tmp_path)
    assert sc.assess(conn, "ESP32-DevKitC", ["mqtt_paho"])["verdict"] == "PASS"        # wifi
    assert sc.assess(conn, "WIZnet-W5500-EVB-Pico", ["mqtt_paho"])["peripheral_failures"] == []  # ethernet


def test_gap4_peripheral_fail_is_independent_of_memory(tmp_path):
    """gRPC on the Blue Pill fails BOTH on memory and on the missing NIC — the peripheral check is
    not masked by the memory check."""
    conn = _seeded(tmp_path)
    res = sc.assess(conn, "STM32F103-BluePill", ["grpc"])
    assert res["verdict"] == "FAIL"
    assert res["peripheral_failures"] and res["peripheral_failures"][0]["term"] == "grpc"


def test_gap4_no_prerequisite_term_unaffected(tmp_path):
    conn = _seeded(tmp_path)
    res = sc.assess(conn, "STM32F103-BluePill", ["freertos"])   # no peripheral requirement
    assert res["verdict"] == "PASS" and res["peripheral_failures"] == []


def test_gap4_unified_validate_surfaces_peripheral_fail(tmp_path):
    """End to end (Gap 4 + Gap 5): a project intending MQTT on a NIC-less board is not feasible."""
    from eaedk.orchestrator.orchestrator import assess_project
    conn = _seeded(tmp_path)
    repo.create_project(conn, "node", "bare_metal_app", "STM32F103-BluePill")
    p = repo.get_project(conn, "node")
    resp = assess_project(conn, p, intent="mqtt")
    assert resp.feasibility == "not_feasible"
    assert resp.intent["peripheral_failures"]
    assert "wifi or ethernet" in " ".join(resp.intent["reasons"])


# --- Grounded board recommendation — "which board suits best for X" --------------------------

def test_recommend_boards_buckets_by_real_geometry(tmp_path):
    """recommend_boards ranks EVERY seeded board by fit, deterministically from verified geometry —
    a tiny MCU is too small for tflite, a large one fits. No selected board, no LLM."""
    conn = _seeded(tmp_path)
    rec = sc.recommend_boards(conn, ["tflite_micro"])
    fits = {e["board"] for e in rec["fits"]}
    too_small = {e["board"] for e in rec["no"]}
    assert "STM32F103-BluePill" in too_small           # 64KB flash / 20KB RAM cannot hold it
    assert any("ESP32" in b or "STM32H7" in b for b in fits)   # a large board fits
    assert "STM32F103-BluePill" not in fits


def test_board_selection_with_intent_recommends_not_overrides(tmp_path):
    """A board-selection question with an AI intent must recommend across the fleet — never the
    selected-board cost override, never an LLM-invented board (the hallucination this replaces)."""
    conn = _seeded(tmp_path)
    out = mentor_chat(conn, "STM32F103-BluePill",
                      [{"role": "user", "content": "which board suits best for an AI application?"}],
                      use_llm=False)
    assert "Arbiter override" not in out               # not the selected-board FAIL override
    assert "verified flash/RAM" in out                 # the grounded fleet recommendation
    assert ("ESP32" in out or "STM32H743" in out)      # real seeded boards, not hallucinated


def test_board_selection_without_intent_gives_framework(tmp_path):
    """No intent named → the board-selection reasoning framework, not a recommendation."""
    conn = _seeded(tmp_path)
    out = mentor_chat(conn, "STM32F103-BluePill",
                      [{"role": "user", "content": "which board should I pick?"}], use_llm=False)
    assert "trade-off" in out.lower() and "verified flash/RAM" not in out
