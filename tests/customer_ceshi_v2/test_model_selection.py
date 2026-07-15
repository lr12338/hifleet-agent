from types import SimpleNamespace

from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import MemorySaver

from agents.customer_ceshi_v2.builder import build_customer_ceshi_v2_agent
from agents.customer_ceshi_v2.contracts import AgentDecision


class FinishTextClient:
    def __init__(self):
        self.calls = 0

    def decide(self, **kwargs):
        self.calls += 1
        return AgentDecision(action="finish", answer_draft="I need no external evidence for this acknowledgement.")


class FailingPerceptionClient:
    def inspect(self, assets, goal):
        raise AssertionError("pure text must not invoke multimodal perception")


class EmptyRegistry:
    def descriptors(self):
        return []


def test_pure_text_does_not_invoke_multimodal_client(monkeypatch):
    import agents.customer_ceshi_v2.builder as builder

    monkeypatch.setattr(builder, "get_memory_saver", lambda: MemorySaver())
    text = FinishTextClient()
    context = SimpleNamespace(customer_ceshi_v2_text_client=text, customer_ceshi_v2_perception_client=FailingPerceptionClient(), customer_ceshi_v2_tool_registry=EmptyRegistry())
    graph = build_customer_ceshi_v2_agent(context, {"config": {}}, ".", SimpleNamespace(profile_id="customer_ceshi"))

    result = graph.invoke({"messages": [HumanMessage(content="hello")], "session_id": "v2-text"}, {"configurable": {"thread_id": "customer_ceshi_v2:v2-text"}})

    assert text.calls == 1
    assert result["generated_answer"].startswith("I need")
