from __future__ import annotations

import json
from pathlib import Path

import pytest

from skills.adapters.customer_ceshi import build_customer_ceshi_bundle
from skills.adapters.customer_support import build_customer_support_shadow_bundle, compare_legacy_trace_with_v2
from skills.core.manifest_loader import load_manifest
from skills.core.policy import customer_support_shadow_enabled, resolve_skill_runtime
from skills.ship_info_update.validators import validate_position_update, validate_static_update


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
    assert {"web_search", "verify_public_page"}.issubset(names)
    assert "agent_browser_deep_search" not in names


def test_manifests_are_machine_readable_and_unique() -> None:
    manifests = [load_manifest(ROOT / "src" / "skills" / skill / "manifest.yaml") for skill in ("knowledge_retrieval", "hifleet_data", "ship_info_update")]
    names = [str(item.get("tool_name") or item.get("id")) for manifest in manifests for item in manifest.capabilities]
    assert len(names) == len(set(names))


def test_ship_update_validators_reject_invalid_and_conflicting_data() -> None:
    assert validate_position_update({"mmsi": "123", "lon": 190, "lat": 91, "updatetime": "now"}) == ["mmsi", "lon", "lat", "updatetime"]
    invalid = validate_static_update({"mmsi": "123456789", "ship_type": "cargo", "minotype": "tanker"})
    assert invalid == ["ship_type_minotype_conflict"]
