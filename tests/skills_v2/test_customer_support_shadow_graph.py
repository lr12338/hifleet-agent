from __future__ import annotations

import json
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

    class FakeShadowModel:
        def __init__(self) -> None:
            self.messages = []

        def invoke(self, messages):
            self.messages = list(messages)
            return AIMessage(
                content=json.dumps(
                    {
                        "scenario": "ship_update",
                        "recommended_tools": ["prepare_ship_update"],
                        "parameter_summary": {},
                        "evidence_requirements": ["confirmation"],
                        "high_risk_claims": [],
                        "proposed_reply": "请确认草稿。",
                        "confidence": "medium",
                    },
                    ensure_ascii=False,
                )
            )

    monkeypatch.setenv("CUSTOMER_SUPPORT_SKILLS_SHADOW", "true")
    monkeypatch.setattr("agents.agent._build_standard_agent", lambda *args, **kwargs: FakeStandardAgent())
    monkeypatch.setattr("agents.agent._load_all_tools", lambda _profile: [])
    monkeypatch.setattr(
        "agents.agent._run_lightweight_customer_understanding",
        lambda **_kwargs: {"intent": "knowledge", "evidence_required": False, "search_query_candidates": []},
    )
    shadow_model = FakeShadowModel()
    graph = _build_lightweight_customer_support_agent(
        ctx=SimpleNamespace(headers={}, run_id="shadow-run", customer_support_v2_shadow_model=shadow_model),
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
    assert shadow["status"] == "completed_prompt_shadow"
    assert shadow["dry_run"] is True
    assert shadow["executed_tools"] == []
    assert shadow["shadow_inference"]["prompt_injected"] is True
    assert "HiFleet Data V2" in shadow_model.messages[0].content
