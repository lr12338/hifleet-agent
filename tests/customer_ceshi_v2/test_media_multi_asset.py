from types import SimpleNamespace

from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import MemorySaver

from agents.customer_ceshi_v2.builder import build_customer_ceshi_v2_agent
from agents.customer_ceshi_v2.contracts import AgentDecision, Observation
from agents.customer_ceshi_v2.tools import ToolDescriptor


class MediaThenFinishClient:
    def __init__(self):
        self.calls = 0

    def decide(self, **kwargs):
        self.calls += 1
        if self.calls == 1:
            return AgentDecision(action="call_tools", media_requests=[{"asset_id": "asset-0-0", "objective": "Read the first screenshot", "mode": "ocr"}, {"asset_id": "asset-1-1", "objective": "Read the second screenshot", "mode": "field_extract"}])
        return AgentDecision(action="finish", answer_draft="Both screenshots were inspected.", claims=[])


class RecordingPerceptionService:
    def __init__(self):
        self.requests = []

    def inspect(self, assets, requests):
        self.requests = requests
        return [Observation(status="success", capability="inspect_media", facts=[f"Observed {request.asset_id}"]) for request in requests], 0


class EmptyRegistry:
    def descriptors(self):
        return [ToolDescriptor(name="local_kb_search", capability="knowledge")]


def test_model_drives_multi_asset_media_inspection(monkeypatch):
    import agents.customer_ceshi_v2.builder as builder

    monkeypatch.setattr(builder, "get_memory_saver", lambda: MemorySaver())
    text = MediaThenFinishClient()
    perception = RecordingPerceptionService()
    context = SimpleNamespace(customer_ceshi_v2_text_client=text, customer_ceshi_v2_perception_service=perception, customer_ceshi_v2_tool_registry=EmptyRegistry())
    graph = build_customer_ceshi_v2_agent(context, {"config": {}}, ".", SimpleNamespace(profile_id="customer_ceshi"))
    content = [{"type": "image_url", "image_url": {"url": "https://example.test/one.png"}}, {"type": "image_url", "image_url": {"url": "https://example.test/two.png"}}]

    result = graph.invoke({"messages": [HumanMessage(content=content)], "session_id": "media"}, {"configurable": {"thread_id": "customer_ceshi_v2:media"}})

    assert [request.asset_id for request in perception.requests] == ["asset-0-0", "asset-1-1"]
    assert text.calls == 2
    assert result["metrics"]["media_calls"] == 2
