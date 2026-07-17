from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


ANALYZER_PATH = Path(__file__).resolve().parents[1] / "scripts" / "analyze_customer_dialogs.py"
SPEC = importlib.util.spec_from_file_location("customer_support_dialog_analyzer", ANALYZER_PATH)
assert SPEC and SPEC.loader
analyzer = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = analyzer
SPEC.loader.exec_module(analyzer)

DialogCase = analyzer.DialogCase
baseline_regression_fixtures = analyzer.baseline_regression_fixtures
build_regression_fixtures = analyzer.build_regression_fixtures
mask_text = analyzer.mask_text
select_key_cases = analyzer.select_key_cases
write_scenario_report = analyzer.write_scenario_report


def make_case(
    case_id: str,
    *,
    scenario: str = "船位更新",
    user_input: str = "更新 MMSI 414718000 船位",
    risk_level: str = "P2",
    status: str = "success",
    quality_category: str = "基本正确但表达可优化",
    tools: list[dict] | None = None,
) -> DialogCase:
    return DialogCase(
        case_id=case_id,
        time_bj="2026-07-17 10:00:00",
        channel="wechat_kf",
        session_id_hash="session_test",
        user_id_hash="user_test",
        run_id_hash="run_test",
        route="ship_update",
        status=status,
        latency_ms=120,
        model="test-model",
        user_input=user_input,
        agent_reply="请补充必要字段。",
        business_category="船位更新/待补充或待确认",
        scenario=scenario,
        quality_category=quality_category,
        risk_level=risk_level,
        tools=tools or [],
        errors=[],
        route_trace_summary="route=ship_update",
        issue_summary="",
        expected_reply_points=["缺字段时只追问关键字段"],
    )


def test_mask_text_removes_common_customer_identifiers_and_tokens() -> None:
    text = "请联系 13812345678，邮箱 demo@example.com，token=abcdefghijk"

    masked = mask_text(text)

    assert "138****5678" in masked
    assert "de***@example.com" in masked
    assert "token=***" in masked


def test_select_key_cases_prefers_high_risk_and_collapses_duplicates() -> None:
    duplicate_low_risk = make_case("CS-001", risk_level="P2")
    duplicate_high_risk = make_case(
        "CS-002",
        risk_level="P0",
        status="error",
        quality_category="字段抽取错误",
        tools=[{"name": "upload_ship_position", "success": False, "status": "error"}],
    )
    different_case = make_case(
        "CS-003",
        user_input="更新 MMSI 414718000 船位，经度 121.1 纬度 39.2",
        risk_level="P1",
    )

    selected = select_key_cases([duplicate_low_risk, duplicate_high_risk, different_case])

    assert [case.case_id for case in selected] == ["CS-002", "CS-003"]


def test_generated_fixtures_keep_baseline_guards_and_observed_case() -> None:
    observed_case = make_case("CS-100", scenario="船舶静态信息", user_input="查询 MMSI 414718000 船位")

    fixtures = build_regression_fixtures([observed_case])
    fixture_ids = {fixture["id"] for fixture in fixtures}

    assert "ship_update_degree_minute_coordinates" in fixture_ids
    assert "ship_update_missing_fields_guard" in fixture_ids
    assert "ship_update_tool_failure_never_success" in fixture_ids
    assert "static_info_unverified_effective_time" in fixture_ids
    assert "observed_cs-100" in fixture_ids
    assert all("query" in fixture and "scenario" in fixture for fixture in fixtures)
    observed_fixture = next(fixture for fixture in fixtures if fixture["id"] == "observed_cs-100")
    assert "立即生效" in observed_fixture["forbidden_substrings"]


def test_scenario_report_uses_fixed_developer_structure(tmp_path) -> None:
    case = make_case("CS-200", risk_level="P1", quality_category="字段抽取错误")
    fixtures = baseline_regression_fixtures()
    report_path = tmp_path / "customer_support_dialog_case_report.md"

    from datetime import datetime, timezone

    write_scenario_report(report_path, [case], [case], fixtures, datetime(2026, 7, 10, tzinfo=timezone.utc), None)

    report = report_path.read_text(encoding="utf-8")
    assert "## 1. 场景地图" in report
    assert "## 2. 关键案例" in report
    assert "## 3. 优化建议" in report
    assert "## 4. 测试断言" in report
    assert "CS-200" in report
