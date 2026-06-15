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
