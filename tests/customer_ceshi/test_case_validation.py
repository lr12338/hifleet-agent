import json

from scripts.validate_customer_ceshi_cases import _case


def test_high_risk_legacy_reply_is_manual_review_not_gold():
    case = _case({"case_id": "p0", "business_category": "平台功能问答", "risk_level": "P0", "user_input": "怎么手动上传目的港", "agent_reply": "旧回复不得作为真值"})
    assert case["gold_status"] == "manual_review_required"
    assert case["reply_contract"]["do_not_use_agent_reply_as_gold"] is True
    assert "不得根据内部写工具断言用户前台有编辑入口" not in case["forbidden_claims"]


def test_static_update_adds_frontend_hallucination_ban():
    case = _case({"case_id": "eta", "business_category": "船舶静态信息更新/目的港或ETA", "risk_level": "P2", "user_input": "ETA"})
    assert case["gold_status"] == "validated"
    assert "不得承诺立即生效" in case["forbidden_claims"]


def test_five_digit_year_update_requires_clarification_not_prepare_tool():
    case = _case({"case_id": "bad-date", "business_category": "船位更新", "risk_level": "P1", "user_input": "更新船位，更新时间：22026-07-04 15:36"})
    assert case["expected_tools"] == []
    assert case["ambiguities"] == ["suspicious_five_digit_year_requires_user_confirmation", "current_turn_mmsi_required"]


def test_update_without_mmsi_requires_clarification_not_prepare_tool():
    case = _case({"case_id": "missing-mmsi", "business_category": "船位更新", "risk_level": "P1", "user_input": "请更新船位，经度 120E 纬度 30N"})
    assert case["expected_tools"] == []
    assert case["ambiguities"] == ["current_turn_mmsi_required"]


def test_platform_case_requires_one_evidence_tool_not_a_specific_provider():
    case = _case({"case_id": "platform", "business_category": "平台功能问答", "risk_level": "P1", "user_input": "平台如何使用？"})
    assert case["expected_tools"] == []
    assert "local_kb_search" in case["expected_any_tools"]
