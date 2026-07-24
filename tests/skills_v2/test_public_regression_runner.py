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


def test_semantic_pass_requires_real_image_and_satisfied_assertions() -> None:
    case = _case(
        allowed_tools=["inspect_media", "local_kb_search"],
        attachment="img.png",
        required_observations=["图例"],
        required_uncertainty=["不能确定"],
        forbidden_certainty=["安全水域浮标"],
    )
    result = {
        "status": "success",
        "generated_answer": "仅凭截图不能确定符号含义，建议核对图例。",
        "metrics": {"tool_names": ["inspect_media", "local_kb_search"]},
    }
    with_media = _evaluate(case, result, has_real_media=True)
    assert with_media["status"] == "semantic_passed"
    # Same answer but no real image travelled through /run -> not a semantic pass.
    no_media = _evaluate(case, result, has_real_media=False)
    assert no_media["status"] == "real_http_passed"


def test_http_success_and_inspect_media_alone_is_not_semantic_pass() -> None:
    case = _case(allowed_tools=["inspect_media"], attachment="img.png")  # no structured semantic assertions
    result = {
        "status": "success",
        "generated_answer": "已查看图片。",
        "metrics": {"tool_names": ["inspect_media"]},
    }
    evaluated = _evaluate(case, result, has_real_media=True)
    assert evaluated["status"] == "real_http_passed"
    assert evaluated["semantic_assertions_present"] is False


def test_real_image_with_failed_semantic_assertion_is_failed() -> None:
    case = _case(
        allowed_tools=["inspect_media"],
        attachment="img.png",
        required_observations=["图例"],
        forbidden_certainty=["安全水域浮标"],
    )
    result = {
        "status": "success",
        "generated_answer": "这是安全水域浮标。",
        "metrics": {"tool_names": ["inspect_media"]},
    }
    evaluated = _evaluate(case, result, has_real_media=True)
    assert evaluated["status"] == "failed"
    assert evaluated["forbidden_certainty_found"] == ["安全水域浮标"]


def test_pre_run_status_distinguishes_fixture_states() -> None:
    from scripts.run_shared_skills_v2_regression import _pre_run_status

    valid_no_url = _case(attachment="img.png", fixture_quality="valid")
    assert _pre_run_status(valid_no_url, "") == ("fixture_prepared", "attachment_url_not_supplied")
    invalid = _case(attachment="img.png", fixture_quality="invalid")
    assert _pre_run_status(invalid, "https://x") == ("invalid_fixture", "fixture_quality_invalid")
    absent = _case(attachment=None, fixture_quality="absent")
    assert _pre_run_status(absent, "") == ("", "")
