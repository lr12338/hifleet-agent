from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from time import monotonic
from types import SimpleNamespace

from agents.customer_ceshi_responses.builder import build_customer_ceshi_responses_agent
from agents.customer_ceshi_responses.builder import NativeToolRuntime
from agents.customer_ceshi_v2.contracts import ToolCall
from agents.customer_ceshi_v2.tools import CapabilityRegistry
from agents.profiles import get_profile
from skills_v2.core.descriptors import ToolDescriptor


ROOT = Path(__file__).resolve().parents[2]


@dataclass
class _Tool:
    name: str
    response: dict

    def invoke(self, arguments):
        return self.response


def _descriptor(name: str) -> ToolDescriptor:
    return ToolDescriptor(name=name, skill_id="foundation", description=name, input_schema={"type": "object", "properties": {}}, skill_version="2")


def test_v2_denies_public_page_verification_even_after_web_search() -> None:
    registry = CapabilityRegistry(
        tools=[
            _Tool("web_search", {"status": "success", "urls": ["https://example.com/page"]}),
            _Tool("verify_public_page", {"status": "success", "text": "verified"}),
        ],
        shared_descriptors=[_descriptor("web_search"), _descriptor("verify_public_page")],
        enforce_known_public_urls=True,
    )
    registry.invoke(ToolCall(name="web_search", arguments={"query": "example"}))
    blocked = registry.invoke(ToolCall(name="verify_public_page", arguments={"url": "https://example.com/page"}))
    assert blocked.status == "forbidden"
    assert "verify_public_page" not in registry._tools


def test_v2_result_trace_reports_runtime_and_upstream_metadata() -> None:
    runtime = NativeToolRuntime(
        client=object(),
        registry=CapabilityRegistry(tools=[]),
        config={},
        mode="responses",
        skill_runtime_metadata={"mode": "v2", "source_versions": {"hifleet_data": {"upstream_commit": "abc"}}},
    )
    result = runtime._result("ok", [], [], 0, 0, 0, "stop", "not_required", monotonic(), "", "", "")
    assert result["metrics"]["skills_runtime"]["mode"] == "v2"
    assert result["route_trace"]["skills_runtime"]["source_versions"]["hifleet_data"]["upstream_commit"] == "abc"


def test_prepare_ship_update_enforces_shared_invalid_fields_contract() -> None:
    runtime = NativeToolRuntime(client=object(), registry=CapabilityRegistry(tools=[]), config={}, mode="responses")
    result = runtime._draft_operation(
        "prepare_ship_update",
        {
            "operation_type": "position_update",
            "mmsi": "123",
            "longitude": "190",
            "latitude": "91",
            "updatetime": "not-a-time",
        },
        "invalid-fields-session",
    )
    assert result.status == "invalid_input"
    assert result.data["invalid_fields"] == ["mmsi", "lon", "lat", "updatetime"]


def test_v2_load_failure_uses_safe_constrained_registry(monkeypatch) -> None:
    monkeypatch.setattr(
        "agents.customer_ceshi_responses.builder.build_customer_ceshi_bundle",
        lambda _workspace_path: (_ for _ in ()).throw(RuntimeError("broken manifest")),
    )
    runtime = build_customer_ceshi_responses_agent(
        SimpleNamespace(customer_ceshi_responses_client=object()),
        {},
        str(ROOT),
        get_profile("customer_ceshi"),
    )
    text_runtime = runtime.runtime.text_runtime

    assert text_runtime.skill_runtime_metadata == {
        "mode": "safe_constrained",
        "source_versions": {},
        "fallback_reason": "RuntimeError",
    }
    assert {"agent_browser_deep_search", "web_search_agent_browser", "upload_ship_position", "update_ship_static_info", "upsert_local_kb_entry"}.isdisjoint(text_runtime.registry._tools)
    assert text_runtime.registry._enforce_known_public_urls is True


def test_hifleet_source_versions_are_anchored_to_the_lock(monkeypatch) -> None:
    """Runtime source_versions must come from the V2 lock, not a stale manifest."""
    import json

    from skills_v2.adapters.customer_ceshi import build_customer_ceshi_bundle

    bundle = build_customer_ceshi_bundle(str(ROOT))
    record = json.loads((ROOT / "src" / "skills_v2" / "upstream" / "hifleet_skills" / "lock.json").read_text(encoding="utf-8"))["skills"]["hifleet-skills"]
    sv = bundle.source_versions["hifleet_data"]
    assert sv["upstream_commit"] == record["commit"]
    assert sv["skill_version"] == record["version"]
    assert sv["content_hash"] == record["contentHash"]
    # The V2 bundle exposes web_search as its own Skill; no browser verification tool.
    web_search_skill = {item.name for item in bundle.descriptors if item.skill_id == "web_search"}
    assert web_search_skill == {"web_search"}
    assert "verify_public_page" not in {item.name for item in bundle.descriptors}


def test_lock_override_beats_stale_manifest_commit(monkeypatch) -> None:
    """If the manifest commit diverges from the lock, the lock wins at runtime."""
    import json

    from skills_v2.core.registry import SharedSkillRegistry

    registry = SharedSkillRegistry(str(ROOT))
    manifests = registry.load_manifests(("hifleet_data",))
    record = json.loads((ROOT / "src" / "skills_v2" / "upstream" / "hifleet_skills" / "lock.json").read_text(encoding="utf-8"))["skills"]["hifleet-skills"]
    assert manifests["hifleet_data"].upstream_commit == record["commit"]
    assert manifests["hifleet_data"].upstream_lock_key == "hifleet-skills"
