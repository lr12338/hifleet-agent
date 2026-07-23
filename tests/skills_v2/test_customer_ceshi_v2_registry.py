from __future__ import annotations

from dataclasses import dataclass
from time import monotonic

from agents.customer_ceshi_responses.builder import NativeToolRuntime
from agents.customer_ceshi_v2.contracts import ToolCall
from agents.customer_ceshi_v2.tools import CapabilityRegistry
from skills.core.contracts import ToolDescriptor


@dataclass
class _Tool:
    name: str
    response: dict

    def invoke(self, arguments):
        return self.response


def _descriptor(name: str) -> ToolDescriptor:
    return ToolDescriptor(name=name, skill_id="foundation", description=name, input_schema={"type": "object", "properties": {}}, skill_version="2")


def test_verify_public_page_requires_a_web_search_url() -> None:
    registry = CapabilityRegistry(
        tools=[
            _Tool("web_search", {"status": "success", "urls": ["https://example.com/page"]}),
            _Tool("verify_public_page", {"status": "success", "text": "verified"}),
        ],
        shared_descriptors=[_descriptor("web_search"), _descriptor("verify_public_page")],
        enforce_known_public_urls=True,
    )
    blocked = registry.invoke(ToolCall(name="verify_public_page", arguments={"url": "https://example.com/page"}))
    assert blocked.status == "forbidden"
    registry.invoke(ToolCall(name="web_search", arguments={"query": "example"}))
    allowed = registry.invoke(ToolCall(name="verify_public_page", arguments={"url": "https://example.com/page"}))
    assert allowed.status == "success"
    assert allowed.data["skill_version"] == "2"


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
