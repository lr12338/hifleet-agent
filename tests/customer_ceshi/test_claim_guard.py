from agents.customer_ceshi_responses.claim_guard import guard_claims, limit_reply


def test_unrelated_tool_success_cannot_prove_product_permission():
    answer, blocked = guard_claims("HiFleet支持在前台编辑目的港。", [{"status": "success", "capability": "get_ship_position", "facts": ["MMSI 730285526 船位已更新"]}])
    assert blocked
    assert "缺少可直接核验" in answer


def test_direct_evidence_keeps_high_risk_claim():
    answer, blocked = guard_claims("该功能支持前台编辑。", [{"status": "success", "facts": ["官方帮助：该功能支持前台编辑。"]}])
    assert blocked == []
    assert answer == "该功能支持前台编辑。"


def test_reply_limit_keeps_complete_sentences_when_possible():
    answer = "第一句" + "甲" * 100 + "。第二句" + "乙" * 100 + "。"
    limited = limit_reply(answer, max_chinese_chars=180)
    assert limited.endswith("。")
    assert "第二句" not in limited
    assert sum("\u4e00" <= char <= "\u9fff" for char in limited) <= 180
