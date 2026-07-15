from types import SimpleNamespace

from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import MemorySaver

from agents.customer_ceshi_v2.builder import build_customer_ceshi_v2_agent
from agents.customer_ceshi_v2.contracts import AgentDecision, Observation
from agents.customer_ceshi_v2.tools import ToolDescriptor


class FakeTextClient:
    def __init__(self):
        self.calls = 0

    def decide(self, **kwargs):
        self.calls += 1
        if self.calls == 1:
            return AgentDecision(action="call_tools", tool_calls=[{"name": "local_kb_search", "arguments": {"query": "ETA"}}])
        return AgentDecision(action="finish", answer_draft="ETA is visible in the returned source.", claims=["ETA"])


class FakeRegistry:
    def descriptors(self):
        return [ToolDescriptor(name="local_kb_search", capability="knowledge")]

    def invoke(self, call):
        return Observation(status="success", capability=call.name, facts=["ETA is visible in the returned source."], sources=["https://example.test/source"])


def test_orchestrator_continues_after_observation(monkeypatch):
    import agents.customer_ceshi_v2.builder as builder

    monkeypatch.setattr(builder, "get_memory_saver", lambda: MemorySaver())
    text = FakeTextClient()
    context = SimpleNamespace(customer_ceshi_v2_text_client=text, customer_ceshi_v2_tool_registry=FakeRegistry())
    graph = build_customer_ceshi_v2_agent(context, {"config": {"customer_ceshi_v2_max_steps": 4}}, ".", SimpleNamespace(profile_id="customer_ceshi"))

    result = graph.invoke({"messages": [HumanMessage(content="What is the ETA?")], "session_id": "v2-loop"}, {"configurable": {"thread_id": "customer_ceshi_v2:v2-loop"}})

    assert text.calls == 2
    assert result["generated_tool_calls"] == ["local_kb_search"]
    assert result["evidence_review"]["ready"] is True
    assert "ETA" in result["generated_answer"]
