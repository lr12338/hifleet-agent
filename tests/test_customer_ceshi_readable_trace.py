import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from langchain_core.messages import HumanMessage

from agents.agent import _build_lightweight_customer_support_agent
from agents.profiles import AgentProfile


class FakeTool:
    def __init__(self, name, handler):
        self.name = name
        self.handler = handler
        self.calls = []

    def invoke(self, args):
        self.calls.append(args)
        return self.handler(args)


def test_readable_trace_has_required_structure_and_redaction(monkeypatch):
    upload = FakeTool("upload_ship_position", lambda args: "船位更新成功！")

    class FakeStandardAgent:
        def invoke(self, payload, context=None, config=None):
            raise AssertionError("write preflight should not delegate")

    monkeypatch.setattr("agents.agent._build_standard_agent", lambda *args, **kwargs: FakeStandardAgent())
    monkeypatch.setattr("agents.agent.SkillLoader.get_tools_by_names", lambda names: [upload])
    graph = _build_lightweight_customer_support_agent(
        ctx=SimpleNamespace(run_id="r-readable"),
        cfg={"config": {}},
        workspace_path=str(Path(__file__).resolve().parents[1]),
        profile=AgentProfile(profile_id="customer_ceshi", skills=["hifleet_ship_service"]),
    )

    result = graph.invoke(
        {
            "messages": [HumanMessage(content="请更新船位 MMSI 730285526 经度 121.687167 纬度 39.006833 更新时间 2026-07-04 14:43:00 状态 系泊")],
            "session_id": "s-readable",
            "agent_profile": "customer_ceshi",
        },
        config={"configurable": {"thread_id": "s-readable"}},
    )

    readable = result["route_trace"]["readable_trace"]
    required = {
        "input_summary",
        "understanding_summary",
        "extracted_fields",
        "pending_update_summary",
        "decision_summary",
        "write_action_summary",
        "tool_result_summary",
        "evidence_summary",
        "risk_guard_summary",
        "final_response_summary",
        "agent_process_summary",
    }
    assert required.issubset(readable.keys())
    serialized = str(readable).lower()
    assert readable["agent_process_summary"]
    assert "/home/" not in serialized
    assert "token" not in serialized
    assert "api_key" not in serialized
    assert "secret" not in serialized
    assert "password" not in serialized
    assert readable["write_action_summary"]["execution_status"] == "ok"
    assert readable["tool_result_summary"]["write_tool_success"] is True
