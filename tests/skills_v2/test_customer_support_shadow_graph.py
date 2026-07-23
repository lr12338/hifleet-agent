from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from langchain_core.messages import AIMessage, HumanMessage

from agents.agent import _build_lightweight_customer_support_agent
from agents.profiles import AgentProfile


ROOT = Path(__file__).resolve().parents[2]


def test_customer_support_shadow_keeps_legacy_reply_and_records_dry_run(monkeypatch) -> None:
    class FakeStandardAgent:
        def invoke(self, _payload, context=None):
            return {
                "messages": [
                    AIMessage(tool_calls=[{"name": "upload_ship_position", "args": {}, "id": "call-1"}], content=""),
                    AIMessage(content="本次船舶信息更新尚未执行成功。"),
                ]
            }

    monkeypatch.setenv("CUSTOMER_SUPPORT_SKILLS_SHADOW", "true")
    monkeypatch.setattr("agents.agent._build_standard_agent", lambda *args, **kwargs: FakeStandardAgent())
    monkeypatch.setattr("agents.agent._load_all_tools", lambda _profile: [])
    monkeypatch.setattr(
        "agents.agent._run_lightweight_customer_understanding",
        lambda **_kwargs: {"intent": "knowledge", "evidence_required": False, "search_query_candidates": []},
    )
    graph = _build_lightweight_customer_support_agent(
        ctx=SimpleNamespace(headers={}, run_id="shadow-run"),
        cfg={},
        workspace_path=str(ROOT),
        profile=AgentProfile(profile_id="customer_support", skills=[]),
    )

    result = graph.invoke(
        {"messages": [HumanMessage(content="请更新船位")], "session_id": "shadow-session", "agent_profile": "customer_support"},
        config={"configurable": {"thread_id": "shadow-session"}},
    )

    assert result["messages"][-1].content == "本次船舶信息更新尚未执行成功。"
    shadow = result["route_trace"]["skills_v2_shadow"]
    assert shadow["status"] == "completed_contract_shadow"
    assert shadow["dry_run"] is True
    assert shadow["executed_tools"] == []
