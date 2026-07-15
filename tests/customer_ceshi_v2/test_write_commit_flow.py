from types import SimpleNamespace

from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import MemorySaver

from agents.customer_ceshi_v2.actions import ShipUpdateGate
from agents.customer_ceshi_v2.builder import build_customer_ceshi_v2_agent
from agents.customer_ceshi_v2.contracts import Observation, WriteProposal


def test_explicit_confirmation_token_commits_only_through_gate(monkeypatch):
    import agents.customer_ceshi_v2.builder as builder

    monkeypatch.setattr(builder, "get_memory_saver", lambda: MemorySaver())
    gate = ShipUpdateGate(enabled=True, secret="test", executor=lambda proposal: Observation(status="success", capability="commit_ship_update", facts=["Write service confirmed success."]))
    prepared = gate.prepare(WriteProposal(operation="ship_static_info", fields={"mmsi": "123456789"}), user_id="u", session_id="s", profile_id="customer_ceshi")
    token = prepared.data["confirmation_token"]
    graph = build_customer_ceshi_v2_agent(SimpleNamespace(customer_ceshi_v2_write_gate=gate), {"config": {"customer_ceshi_v2_ship_write_enabled": True}}, ".", SimpleNamespace(profile_id="customer_ceshi", skills=[]))

    result = graph.invoke({"messages": [HumanMessage(content=f"确认 {token}")], "session_id": "s", "user_id": "u"}, {"configurable": {"thread_id": "write"}})

    assert result["generated_tool_calls"] == ["commit_ship_update"]
    assert result["generated_answer"] == "Write service confirmed success."
