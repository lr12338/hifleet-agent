from __future__ import annotations

import json
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from skills_v2.adapters.customer_ceshi import build_customer_ceshi_bundle
from skills_v2.adapters.customer_support_shadow import build_customer_support_shadow_bundle, compare_legacy_trace_with_v2
from skills_v2.core.manifest_loader import load_manifest
from skills_v2.core.policy import customer_support_shadow_enabled, resolve_skill_runtime
from skills_v2.skills.ship_info_update.validators import validate_position_update, validate_static_update


ROOT = Path(__file__).resolve().parents[2]


def test_v2_defaults_keep_customer_support_on_legacy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CUSTOMER_SUPPORT_SKILLS_MODE", raising=False)
    monkeypatch.delenv("CUSTOMER_CESHI_SKILLS_MODE", raising=False)
    assert resolve_skill_runtime("customer_support", ROOT) == "legacy"
    assert resolve_skill_runtime("customer_ceshi", ROOT) == "v2"


def test_environment_override_allows_configuration_only_rollback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CUSTOMER_CESHI_SKILLS_MODE", "legacy")
    assert resolve_skill_runtime("customer_ceshi", ROOT) == "legacy"


def test_customer_support_shadow_is_opt_in_and_never_replays_writes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CUSTOMER_SUPPORT_SKILLS_SHADOW", raising=False)
    assert customer_support_shadow_enabled(ROOT) is False
    monkeypatch.setenv("CUSTOMER_SUPPORT_SKILLS_SHADOW", "true")
    assert customer_support_shadow_enabled(ROOT) is True
    comparison = compare_legacy_trace_with_v2(
        route_trace={"task_type": "ship_update", "tool_call_sequence": ["upload_ship_position"], "evidence_items": [{"source": "legacy"}]},
        final_answer="更新成功",
        workspace_path=ROOT,
    )
    assert comparison["executed_tools"] == []
    assert comparison["dry_run"] is True
    assert comparison["write_state"] == "dry_run_required"
    assert comparison["prompt_loaded_chars"] > 0
    assert "upload_ship_position" in comparison["tool_selection"]["legacy_not_in_v2"]


def test_customer_support_shadow_injects_v2_prompt_into_no_tool_model() -> None:
    class FakeShadowModel:
        def __init__(self) -> None:
            self.messages = []

        def invoke(self, messages):
            self.messages = list(messages)
            return AIMessage(
                content=json.dumps(
                    {
                        "scenario": "ship_update",
                        "recommended_tools": ["prepare_ship_update", "upload_ship_position"],
                        "parameter_summary": {"mmsi": "123456789"},
                        "evidence_requirements": ["explicit confirmation"],
                        "high_risk_claims": ["write success requires a real response"],
                        "proposed_reply": "我会先生成草稿，等待确认。",
                        "confidence": "medium",
                    },
                    ensure_ascii=False,
                )
            )

    shadow_model = FakeShadowModel()
    comparison = compare_legacy_trace_with_v2(
        route_trace={"task_type": "ship_update", "tool_call_sequence": ["upload_ship_position"]},
        final_answer="本次更新尚未确认成功。",
        workspace_path=ROOT,
        shadow_model=shadow_model,
        user_text="请更新船位",
    )

    assert comparison["status"] == "completed_prompt_shadow"
    assert comparison["executed_tools"] == []
    assert isinstance(shadow_model.messages[0], SystemMessage)
    assert "HiFleet 数据 V2" in shadow_model.messages[0].content
    assert isinstance(shadow_model.messages[1], HumanMessage)
    inference = comparison["shadow_inference"]
    assert inference["prompt_injected"] is True
    assert inference["recommended_tools"] == ["prepare_ship_update"]
    assert inference["unapproved_recommended_tools"] == ["upload_ship_position"]
    assert inference["proposed_reply_has_success_claim"] is False


def test_customer_support_shadow_model_failure_keeps_contract_only_record() -> None:
    class FailingShadowModel:
        def invoke(self, _messages):
            raise RuntimeError("model unavailable")

    comparison = compare_legacy_trace_with_v2(
        route_trace={},
        final_answer="保守回复",
        workspace_path=ROOT,
        shadow_model=FailingShadowModel(),
        user_text="普通咨询",
    )

    assert comparison["status"] == "completed_contract_shadow"
    assert comparison["shadow_inference"]["status"] == "failed"
    assert comparison["executed_tools"] == []


def test_customer_adapters_share_business_contracts() -> None:
    customer_ceshi = build_customer_ceshi_bundle(ROOT)
    customer_support = build_customer_support_shadow_bundle(ROOT)
    ceshi_contracts = {(item.name, json.dumps(item.input_schema, sort_keys=True), item.risk_level) for item in customer_ceshi.descriptors if item.skill_id != "foundation"}
    support_contracts = {(item.name, json.dumps(item.input_schema, sort_keys=True), item.risk_level) for item in customer_support.descriptors if item.skill_id != "foundation"}
    assert ceshi_contracts == support_contracts


def test_external_v2_never_exposes_low_level_writes_or_knowledge_admin() -> None:
    bundle = build_customer_ceshi_bundle(ROOT)
    names = {item.name for item in bundle.descriptors}
    assert {"upload_ship_position", "update_ship_static_info", "upsert_local_kb_entry"}.isdisjoint(names)
    assert names & {"prepare_ship_update", "commit_ship_update", "cancel_ship_update"}
    assert "web_search" in names
    assert {"verify_public_page", "agent_browser_deep_search", "web_search_agent_browser"}.isdisjoint(names)


def test_manifests_are_machine_readable_and_unique() -> None:
    manifests = [load_manifest(ROOT / "src" / "skills_v2" / "skills" / skill / "manifest.yaml") for skill in ("knowledge_retrieval", "web_search", "hifleet_data", "ship_info_update")]
    names = [str(item.get("tool_name") or item.get("id")) for manifest in manifests for item in manifest.capabilities]
    assert len(names) == len(set(names))


def test_ship_update_validators_reject_invalid_and_conflicting_data() -> None:
    assert validate_position_update({"mmsi": "123", "lon": 190, "lat": 91, "updatetime": "now"}) == ["mmsi", "lon", "lat", "updatetime"]
    invalid = validate_static_update({"mmsi": "123456789", "ship_type": "cargo", "minotype": "tanker"})
    assert invalid == ["ship_type_minotype_conflict"]


def test_knowledge_retrieval_is_an_independent_read_only_skill() -> None:
    manifest = load_manifest(ROOT / "src" / "skills_v2" / "skills" / "knowledge_retrieval" / "manifest.yaml")
    assert manifest.skill_id == "knowledge_retrieval"
    assert manifest.upstream_commit == ""
    names = [str(cap.get("tool_name") or cap.get("id")) for cap in manifest.capabilities]
    assert names == ["local_kb_search"]
    assert all(cap.get("read_only") is True for cap in manifest.capabilities)


def test_ship_info_update_skills_require_confirmation_and_validators() -> None:
    manifest = load_manifest(ROOT / "src" / "skills_v2" / "skills" / "ship_info_update" / "manifest.yaml")
    names = [str(cap.get("id")) for cap in manifest.capabilities]
    assert names == ["prepare_ship_update", "commit_ship_update", "cancel_ship_update"]
    assert all(cap.get("requires_confirmation") is True for cap in manifest.capabilities)
    # Deterministic validators reject both position and static invalid data.
    assert validate_position_update({"mmsi": "123456789", "lon": 121.5, "lat": 31.2, "updatetime": "2026-07-23 10:00:00"}) == []
    assert validate_static_update({"mmsi": "123456789", "ship_type": "cargo", "minotype": "cargo"}) == []
