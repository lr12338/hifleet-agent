from types import SimpleNamespace

from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import MemorySaver

from agents.customer_ceshi_v2.builder import build_customer_ceshi_v2_agent


class FailingTextClient:
    def decide(self, **kwargs):
        raise RuntimeError("provider unavailable")


class EmptyRegistry:
    def descriptors(self):
        return []


def test_orchestrator_failure_stays_in_v2_and_degrades_safely(monkeypatch):
    import agents.customer_ceshi_v2.builder as builder

    monkeypatch.setattr(builder, "get_memory_saver", lambda: MemorySaver())
    context = SimpleNamespace(customer_ceshi_v2_text_client=FailingTextClient(), customer_ceshi_v2_tool_registry=EmptyRegistry())
    graph = build_customer_ceshi_v2_agent(context, {"config": {}}, ".", SimpleNamespace(profile_id="customer_ceshi"))

    result = graph.invoke({"messages": [HumanMessage(content="help")], "session_id": "error"}, {"configurable": {"thread_id": "customer_ceshi_v2:error"}})

    assert result["route_trace"]["agent"] == "customer_ceshi_v2"
    assert "生产客服链" not in result["generated_answer"] or "未切换到生产客服链" in result["generated_answer"]
