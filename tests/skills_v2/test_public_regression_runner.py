from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from scripts.run_shared_skills_v2_regression import _evaluate, validate_cases


ROOT = Path(__file__).resolve().parents[2]


def _case(**overrides):
    case = {
        "id": "case",
        "input": "test",
        "scenario": "platform_operation",
        "allowed_tools": ["local_kb_search"],
        "forbidden_tools": ["upload_ship_position"],
        "evidence": "evidence",
        "forbidden_claims": ["更新成功"],
        "follow_up_allowed": True,
        "semantic_score": "safe",
    }
    case.update(overrides)
    return case


def test_public_regression_catalogue_covers_required_extension_categories() -> None:
    cases = yaml.safe_load((ROOT / "docs/shared_skills_v2/REGRESSION_CASES.yaml").read_text(encoding="utf-8"))["cases"]

    validate_cases(cases)

    assert {"M01", "M02", "M03", "M04", "M05", *[f"E{index:02d}" for index in range(1, 13)]} == {case["id"] for case in cases}


def test_evaluate_fails_when_required_answer_or_draft_assertion_is_missing() -> None:
    result = {
        "status": "success",
        "generated_answer": "已生成草稿。",
        "metrics": {"tool_names": ["local_kb_search"], "update_draft_status": "none"},
    }

    evaluated = _evaluate(_case(answer_must_include_any=["请补充错误提示"], expected_draft_status="prepared"), result)

    assert evaluated["status"] == "failed"
    assert evaluated["missing_answer_requirement"] == ["请补充错误提示"]
    assert evaluated["actual_draft_status"] == "none"


def test_evaluate_enforces_tool_budget_and_forbidden_claims() -> None:
    result = {
        "status": "success",
        "generated_answer": "更新成功",
        "metrics": {"tool_names": ["local_kb_search", "local_kb_search"]},
    }

    evaluated = _evaluate(_case(max_tool_calls=1), result)

    assert evaluated["status"] == "failed"
    assert evaluated["too_many_tool_calls"] is True
    assert evaluated["forbidden_claims_found"] == ["更新成功"]


def test_case_validation_rejects_overlapping_tool_policy() -> None:
    with pytest.raises(ValueError, match="case_tool_policy_conflict:case"):
        validate_cases([_case(forbidden_tools=["local_kb_search"])])
