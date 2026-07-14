import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agents.multimodal_contracts import evidence_coverage, normalize_evidence_items
from agents.customer_support_understanding import build_customer_understanding


def test_fixture_manifest_covers_all_required_scenarios():
    payload = json.loads((ROOT / "tests/fixtures/multimodal_customer_support/cases.json").read_text(encoding="utf-8"))
    cases = {item["case_id"]: item for item in payload["cases"]}

    assert set(cases) == {f"M{index:02d}" for index in range(1, 16)}
    assert cases["M04"]["expected_scenario"] == "platform_metric_definition"
    assert cases["M05"]["expected_scenario"] == "ship_tracking_incident"
    assert cases["M05"]["forbidden_tools"] == ["upload_ship_position", "update_ship_static_info"]
    assert all(item.get("status") == "missing_fixture" for key, item in cases.items() if key >= "M06")


def test_missing_fixture_synthetic_contracts_cover_business_scenarios_without_claiming_live_execution():
    payload = json.loads((ROOT / "tests/fixtures/multimodal_customer_support/cases.json").read_text(encoding="utf-8"))
    cases = {item["case_id"]: item for item in payload["cases"]}

    for case_id in (f"M{index:02d}" for index in range(6, 16)):
        case = cases[case_id]
        contract = case["synthetic_contract"]
        result = build_customer_understanding(
            contract["user_text"],
            has_media=True,
            has_file_attachment=bool(contract.get("has_file_attachment")),
            perception=contract["perception"],
        )

        assert case["status"] == "missing_fixture"
        assert result.multimodal_scenario == case["expected_scenario"]
        if "expected_business_scenario" in case:
            assert result.business_scenario == case["expected_business_scenario"]
        else:
            assert result.business_scenario is None

    assert build_customer_understanding(
        cases["M09"]["synthetic_contract"]["user_text"],
        has_media=True,
        perception=cases["M09"]["synthetic_contract"]["perception"],
    ).is_write_request is True
    assert build_customer_understanding(
        cases["M12"]["synthetic_contract"]["user_text"],
        has_media=True,
        perception=cases["M12"]["synthetic_contract"]["perception"],
    ).ship_identities == [{"name": "NEW VESSEL", "mmsi": "987654321"}]
    audio_write_contract = cases["M13"]["write_safety_contract"]
    audio_write = build_customer_understanding(
        audio_write_contract["user_text"],
        has_media=True,
        perception=audio_write_contract["perception"],
    )
    assert audio_write.business_scenario == audio_write_contract["expected_business_scenario"]
    assert audio_write.is_write_request is audio_write_contract["expected_write_request"]


def test_evidence_contract_retains_claim_boundaries():
    items = normalize_evidence_items(
        [
            {"source_type": "visual", "snippet": "截图显示 9.73 kn", "claim": "页面显示值"},
            {"source_type": "user_reported", "snippet": "AIS 正常", "claim": "船端 AIS 状态"},
            {"source_type": "ship_tool", "snippet": "last report", "claim": "last_position_evidence"},
            {"source_type": "official_site", "snippet": "搜索摘要", "claim": "product_definition"},
        ]
    )
    coverage = evidence_coverage(items, ["last_position_evidence", "product_definition"])

    assert items[0]["verified"] is False
    assert items[1]["verified"] is False
    assert items[2]["verified"] is True
    assert items[3]["verified"] is False
    assert coverage["covered_claims"] == ["last_position_evidence"]
    assert coverage["missing_claims"] == ["product_definition"]


def test_evidence_coverage_accepts_verified_support_ids_as_claim_coverage():
    coverage = evidence_coverage(
        [
            {
                "source_type": "ship_tool",
                "snippet": "last report",
                "claim": "最近船位",
                "supports": ["ship_identity", "last_position_evidence", "incident_packet"],
            }
        ],
        ["ship_identity", "last_position_evidence", "incident_packet"],
    )

    assert coverage["covered_claims"] == ["ship_identity", "last_position_evidence", "incident_packet"]
    assert coverage["missing_claims"] == []


def test_evaluator_contract_mode_is_truthful_and_masks_attachment_data(tmp_path):
    output_json = tmp_path / "result.json"
    output_md = tmp_path / "result.md"
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/eval_multimodal_customer_support.py",
            "--case",
            "M04",
            "--output-json",
            str(output_json),
            "--output-md",
            str(output_md),
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    assert "case_count" in completed.stdout
    case = json.loads(output_json.read_text(encoding="utf-8"))["cases"][0]
    assert case["execution_mode"] == "contract_only"
    assert case["failure_code"] == "not_executed"
    assert case["scenario"] == "platform_metric_definition"
    assert case["understanding_result"]["operation_type"] == "none"
    assert "data:" not in output_json.read_text(encoding="utf-8")


def test_evaluator_run_api_requires_environment_configuration_without_embedded_endpoint(tmp_path):
    output_json = tmp_path / "result.json"
    completed = subprocess.run(
        [sys.executable, "scripts/eval_multimodal_customer_support.py", "--case", "M01", "--run-api", "--output-json", str(output_json), "--output-md", str(tmp_path / "result.md")],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        env={key: value for key, value in __import__("os").environ.items() if key not in {"MULTIMODAL_EVAL_API_URL", "MULTIMODAL_EVAL_API_TOKEN"}},
    )

    assert "case_count" in completed.stdout
    case = json.loads(output_json.read_text(encoding="utf-8"))["cases"][0]
    assert case["execution_mode"] == "run_api"
    assert case["failure_code"] == "api_not_configured"


def test_direct_graph_mode_forces_memory_checkpointer(monkeypatch):
    import scripts.eval_multimodal_customer_support as evaluator

    monkeypatch.setenv("COZE_CHECKPOINTER_MODE", "postgres")
    result = evaluator._run_direct_graph({"case_id": "M06", "failure_code": "missing_fixture"})

    assert result["failure_code"] == "missing_fixture"
