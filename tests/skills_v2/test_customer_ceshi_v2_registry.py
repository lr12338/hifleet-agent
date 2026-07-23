from __future__ import annotations

from dataclasses import dataclass

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
