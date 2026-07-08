import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from langchain_core.messages import AIMessage, HumanMessage

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


class RecordingStandardAgent:
    def __init__(self, response="标准代理被调用"):
        self.calls = []
        self.response = response

    def invoke(self, payload, context=None, config=None):
        self.calls.append((payload, context, config))
        return {"messages": list(payload["messages"]) + [AIMessage(content=self.response)]}


def _graph(monkeypatch, tools, profile_id="customer_ceshi", run_id="r-ceshi-pending"):
    class FakeStandardAgent:
        def invoke(self, payload, context=None, config=None):
            return {"messages": list(payload["messages"])}

    monkeypatch.setattr("agents.agent._build_standard_agent", lambda *args, **kwargs: FakeStandardAgent())
    monkeypatch.setattr("agents.agent.SkillLoader.get_tools_by_names", lambda names: tools)
    return _build_lightweight_customer_support_agent(
        ctx=SimpleNamespace(run_id=run_id),
        cfg={"config": {}},
        workspace_path=str(Path(__file__).resolve().parents[1]),
        profile=AgentProfile(profile_id=profile_id, skills=["hifleet_ship_service"]),
    )


def _prompt_driven_graph(monkeypatch, tools, profile_id="customer_ceshi", run_id="r-ceshi-prompt-driven"):
    class FakeStandardAgent:
        def invoke(self, payload, context=None, config=None):
            return {"messages": list(payload["messages"])}

    monkeypatch.setattr("agents.agent._build_standard_agent", lambda *args, **kwargs: FakeStandardAgent())
    monkeypatch.setattr("agents.agent.SkillLoader.get_tools_by_names", lambda names: tools)
    return _build_lightweight_customer_support_agent(
        ctx=SimpleNamespace(run_id=run_id),
        cfg={"config": {"ship_update_subagent_prompt_driven": True}},
        workspace_path=str(Path(__file__).resolve().parents[1]),
        profile=AgentProfile(profile_id=profile_id, skills=["hifleet_ship_service"]),
    )


def _understanding_json(operation_type="position_update", **overrides):
    payload = {
        "intent": "ship_update",
        "confidence": "high",
        "reason_summary": "测试写入候选",
        "rewritten_user_need": "测试写入候选",
        "query_type": "ship_query",
        "search_keywords": ["船舶更新"],
        "search_query_candidates": ["船舶更新"],
        "needs_multimodal_grounding": False,
        "should_prefer_local_kb": False,
        "should_limit_to_hifleet_sites": False,
        "operation_type": operation_type,
        "ship_update_candidate": True,
        "ship_write_request": True,
        "pending_action": "none",
        "non_write_reason": "none",
        "ship_identity": {},
        "ship_update_fields": {},
        "ship_update_confidence": "high",
    }
    payload.update(overrides)
    return payload


def test_prompt_driven_subagent_ready_position_calls_upload(monkeypatch):
    upload = FakeTool("upload_ship_position", lambda args: "船位更新成功！")

    def fake_json_agent(ctx, cfg, system_prompt, payload, model_override=""):
        if "tool_contracts" not in payload:
            return _understanding_json("position_update")
        assert payload["tool_contracts"]["upload_ship_position"]["required_tool_args"] == ["mmsi", "lon", "lat", "updatetime"]
        return {
            "status": "ready_to_execute",
            "operation_type": "position_update",
            "tool_name": "upload_ship_position",
            "tool_args": {
                "mmsi": "730285526",
                "lon": "121.687167",
                "lat": "39.006833",
                "updatetime": "2026-07-04 14:43:00",
                "draught": "11.2",
                "status": "系泊",
            },
            "missing_fields": [],
            "pending_action": "none",
            "reply_to_user": "",
            "confidence": "high",
            "evidence_sources": ["current_text"],
        }

    monkeypatch.setattr("agents.agent._invoke_customer_support_json_agent", fake_json_agent)
    graph = _prompt_driven_graph(monkeypatch, [upload])

    result = graph.invoke(
        {"messages": [HumanMessage(content="更新船位")], "session_id": "s-prompt-position", "agent_profile": "customer_ceshi"},
        config={"configurable": {"thread_id": "s-prompt-position"}},
    )

    assert result["generated_tool_calls"] == ["upload_ship_position"]
    assert upload.calls == [
        {
            "mmsi": "730285526",
            "lon": "121.687167",
            "lat": "39.006833",
            "updatetime": "2026-07-04 14:43:00",
            "draft": "11.2",
            "navstatus": "系泊",
        }
    ]
    assert result["route_trace"]["ship_update_subagent"]["source"] == "llm_subagent"


def test_prompt_driven_subagent_normalizes_position_tool_args(monkeypatch):
    upload = FakeTool("upload_ship_position", lambda args: "船位更新成功！")

    def fake_json_agent(ctx, cfg, system_prompt, payload, model_override=""):
        if "tool_contracts" not in payload:
            return _understanding_json("position_update")
        return {
            "status": "ready_to_execute",
            "operation_type": "position_update",
            "tool_name": "upload_ship_position",
            "tool_args": {
                "mmsi": "413994561",
                "lon": "116°19.746′ E",
                "lat": "29°49.007′ N",
                "updatetime": "2026-07-08 15:37 (UTC+8)",
                "speed": "0 kn",
                "course": "163°",
                "draft": "1.6 m",
                "status": "系泊",
                "destination": "HUKOU",
                "eta": "2026-07-06 18:30 (UTC)",
            },
            "normalized_fields": {
                "lon_dec": 116.3291,
                "lat_dec": 29.816783,
            },
            "missing_fields": [],
            "pending_action": "none",
            "reply_to_user": "",
            "confidence": "high",
            "evidence_sources": ["current_text"],
        }

    monkeypatch.setattr("agents.agent._invoke_customer_support_json_agent", fake_json_agent)
    graph = _prompt_driven_graph(monkeypatch, [upload], run_id="r-position-format")

    result = graph.invoke(
        {
            "messages": [
                HumanMessage(
                    content=(
                        "更新船位413994561，更新时间 2026-07-08 15:37 (UTC+8)，"
                        "位置 29°49.007′ N 116°19.746′ E，航迹向 163°，当前吃水 1.6 m，系泊"
                    )
                )
            ],
            "session_id": "s-position-format",
            "agent_profile": "customer_ceshi",
        },
        config={"configurable": {"thread_id": "s-position-format"}},
    )

    assert result["generated_tool_calls"] == ["upload_ship_position"]
    assert upload.calls == [
        {
            "mmsi": "413994561",
            "lon": "116.3291",
            "lat": "29.816783",
            "updatetime": "2026-07-08 15:37:00",
            "speed": "0",
            "course": "163",
            "draft": "1.6",
            "navstatus": "系泊",
            "destination": "HUKOU",
            "eta": "2026-07-06 18:30 (UTC)",
        }
    ]


def test_prompt_driven_subagent_ready_static_calls_update(monkeypatch):
    static_update = FakeTool("update_ship_static_info", lambda args: "静态信息更新成功！")

    def fake_json_agent(ctx, cfg, system_prompt, payload, model_override=""):
        if "tool_contracts" not in payload:
            return _understanding_json("static_update")
        return {
            "status": "ready_to_execute",
            "operation_type": "static_update",
            "tool_name": "update_ship_static_info",
            "tool_args": {
                "mmsi": "730285526",
                "name": "QING FENG LING",
                "imonumber": "9663702",
                "type": "散货船",
                "buildyear": "2018",
                "draught": "9.5",
                "destination": "PIRAEUS",
            },
            "missing_fields": [],
            "pending_action": "none",
            "reply_to_user": "",
            "confidence": "high",
            "evidence_sources": ["current_text"],
        }

    monkeypatch.setattr("agents.agent._invoke_customer_support_json_agent", fake_json_agent)
    graph = _prompt_driven_graph(monkeypatch, [static_update])

    result = graph.invoke(
        {"messages": [HumanMessage(content="更新目的港，MMSI 730285526，PIRAEUS")], "session_id": "s-prompt-static", "agent_profile": "customer_ceshi"},
        config={"configurable": {"thread_id": "s-prompt-static"}},
    )

    assert result["generated_tool_calls"] == ["update_ship_static_info"]
    assert static_update.calls == [
        {
            "mmsi": "730285526",
            "ship_name": "QING FENG LING",
            "imo": "9663702",
            "ship_type": "散货船",
            "minotype": "散货船",
            "built_year": "2018",
            "draft": "9.5",
            "destination": "PIRAEUS",
        }
    ]


def test_ship_type_update_with_perception_routes_to_subagent_not_standard(monkeypatch):
    static_update = FakeTool("update_ship_static_info", lambda args: "静态信息更新成功！")
    standard_agent = RecordingStandardAgent(response="不应调用 standard agent")
    monkeypatch.setattr("agents.agent._build_standard_agent", lambda *args, **kwargs: standard_agent)
    monkeypatch.setattr("agents.agent.SkillLoader.get_tools_by_names", lambda names: [static_update])
    monkeypatch.setattr(
        "agents.agent._run_direct_multimodal_perception",
        lambda *args, **kwargs: {
            "attachment_type": "image",
            "recognized_text": "MMSI 730285526 类型 未知类型船舶",
            "visible_text": "MMSI 730285526 类型 未知类型船舶",
            "summary": "用户当前要求将当前界面中 MMSI 为 730285526 的船舶类型更新为散货船。",
            "visual_question_summary": "用户当前要求将船舶类型更新为散货船",
            "confidence": "high",
        },
    )

    def fake_json_agent(ctx, cfg, system_prompt, payload, model_override=""):
        if "tool_contracts" not in payload:
            return _understanding_json(
                "static_update",
                ship_identity={"mmsi": "730285526"},
                ship_update_fields={"ship_type": "散货船", "minotype": "散货船"},
            )
        return {
            "status": "ready_to_execute",
            "operation_type": "static_update",
            "tool_name": "update_ship_static_info",
            "tool_args": {"mmsi": "730285526", "ship_type": "散货船"},
            "missing_fields": [],
            "pending_action": "none",
            "draft_action": "none",
            "reply_to_user": "",
            "confidence": "high",
            "evidence_sources": ["current_text", "current_attachment"],
        }

    monkeypatch.setattr("agents.agent._invoke_customer_support_json_agent", fake_json_agent)
    graph = _prompt_driven_graph(monkeypatch, [static_update], run_id="r-ship-type-perception")

    result = graph.invoke(
        {
            "messages": [
                HumanMessage(
                    content=[
                        {"type": "image_url", "image_url": {"url": "https://example.com/ship.png"}},
                        {"type": "text", "text": "更新船舶类型，散货船"},
                    ]
                )
            ],
            "session_id": "s-ship-type-perception",
            "agent_profile": "customer_ceshi",
        },
        config={"configurable": {"thread_id": "s-ship-type-perception"}},
    )

    assert standard_agent.calls == []
    assert result["route_trace"]["ship_update_subagent_gate"]["should_run_subagent"] is True
    assert result["route_trace"]["ship_update_subagent_gate"]["reason"] == "agent_ship_update"
    assert result["generated_tool_calls"] == ["update_ship_static_info"]
    assert static_update.calls == [{"mmsi": "730285526", "ship_type": "散货船", "minotype": "散货船"}]
    assert "upload_ship_position" not in result["route_trace"]["standard_agent_tool_bundle"]
    assert "update_ship_static_info" not in result["route_trace"]["standard_agent_tool_bundle"]
    assert "update_ship_static_info" in result["route_trace"]["ship_update_tool_bundle"]


def test_prompt_driven_subagent_blocks_invalid_tool(monkeypatch):
    upload = FakeTool("upload_ship_position", lambda args: "不应调用")
    def fake_json_agent(ctx, cfg, system_prompt, payload, model_override=""):
        if "tool_contracts" not in payload:
            return _understanding_json("position_update")
        return {
            "status": "ready_to_execute",
            "operation_type": "position_update",
            "tool_name": "delete_ship",
            "tool_args": {"mmsi": "730285526"},
            "confidence": "high",
        }

    monkeypatch.setattr("agents.agent._invoke_customer_support_json_agent", fake_json_agent)
    graph = _prompt_driven_graph(monkeypatch, [upload])

    result = graph.invoke(
        {"messages": [HumanMessage(content="更新船位")], "session_id": "s-prompt-invalid-tool", "agent_profile": "customer_ceshi"},
        config={"configurable": {"thread_id": "s-prompt-invalid-tool"}},
    )

    assert upload.calls == []
    assert result["generated_tool_calls"] == []
    assert result["route_trace"]["ship_update_subagent"]["status"] == "error"
    assert "不允许的写入工具" in result["messages"][-1].content


def test_prompt_driven_subagent_need_user_input_saves_pending(monkeypatch):
    upload = FakeTool("upload_ship_position", lambda args: "不应调用")
    pending = {
        "active": True,
        "operation_type": "position_update",
        "status": "awaiting_ship_identity",
        "source_turn_id": "r-ceshi-prompt-driven",
        "expires_after_turns": 5,
        "turns_elapsed": 0,
        "ship_identity": {"mmsi": "", "imo": "", "name": "", "candidate_mmsi": []},
        "extracted_fields": {"lon": "121.687167", "lat": "39.006833", "updatetime": "2026-07-04 14:43:00"},
        "missing_required_fields": ["MMSI"],
        "invalid_fields": [],
        "conflict_fields": [],
        "last_question_to_user": "请补充 MMSI。",
        "confirmation_required": False,
        "can_resume": True,
    }
    def fake_json_agent(ctx, cfg, system_prompt, payload, model_override=""):
        if "tool_contracts" not in payload:
            return _understanding_json("position_update")
        return {
            "status": "need_user_input",
            "operation_type": "position_update",
            "tool_name": None,
            "tool_args": {},
            "missing_fields": ["MMSI"],
            "pending_action": "create",
            "pending_update_state": pending,
            "reply_to_user": "请补充 MMSI。",
            "confidence": "high",
            "evidence_sources": ["current_text"],
        }

    monkeypatch.setattr("agents.agent._invoke_customer_support_json_agent", fake_json_agent)
    graph = _prompt_driven_graph(monkeypatch, [upload])

    result = graph.invoke(
        {"messages": [HumanMessage(content="更新船位")], "session_id": "s-prompt-need-input", "agent_profile": "customer_ceshi"},
        config={"configurable": {"thread_id": "s-prompt-need-input"}},
    )

    assert upload.calls == []
    assert result["pending_update_state"]["status"] == "awaiting_ship_identity"
    assert result["messages"][-1].content == "请补充 MMSI。"
    assert result["ship_update_draft"]["active"] is True
    assert result["ship_update_draft"]["tool_args"]["lon"] == "121.687167"


def test_prompt_driven_non_write_hands_off_to_standard_agent(monkeypatch):
    upload = FakeTool("upload_ship_position", lambda args: "不应调用")
    standard_agent = RecordingStandardAgent(response="船位延迟通常与 AIS 接收覆盖、卫星/岸基数据链路、平台同步队列有关。请提供 MMSI 和时间段，我可以继续排查。")
    monkeypatch.setattr("agents.agent._build_standard_agent", lambda *args, **kwargs: standard_agent)
    monkeypatch.setattr("agents.agent.SkillLoader.get_tools_by_names", lambda names: [upload])
    monkeypatch.setattr(
        "agents.agent._invoke_customer_support_json_agent",
        lambda *args, **kwargs: {
            "status": "non_write",
            "operation_type": "none",
            "tool_name": None,
            "tool_args": {},
            "missing_fields": [],
            "draft_action": "none",
            "ship_update_draft": {},
            "reply_to_user": "内部分类话术不应直接返回",
            "confidence": "high",
            "evidence_sources": ["current_text"],
        },
    )
    graph = _build_lightweight_customer_support_agent(
        ctx=SimpleNamespace(run_id="r-ceshi-non-write-handoff"),
        cfg={"config": {"ship_update_subagent_prompt_driven": True}},
        workspace_path=str(Path(__file__).resolve().parents[1]),
        profile=AgentProfile(profile_id="customer_ceshi", skills=["hifleet_ship_service"]),
    )

    result = graph.invoke(
        {
            "messages": [HumanMessage(content="为什么船位延迟这么高")],
            "pending_update_state": {
                "active": True,
                "operation_type": "position_update",
                "status": "awaiting_ship_identity",
                "source_turn_id": "previous-turn",
                "expires_after_turns": 5,
                "turns_elapsed": 0,
                "ship_identity": {"mmsi": "", "imo": "", "name": "", "candidate_mmsi": []},
                "extracted_fields": {"lon": "110.5", "lat": "23.1", "updatetime": "2026-07-08 13:39:00"},
                "missing_required_fields": ["MMSI"],
                "invalid_fields": [],
                "conflict_fields": [],
                "last_question_to_user": "请补充 MMSI。",
                "confirmation_required": False,
                "can_resume": True,
            },
            "session_id": "s-ceshi-non-write-handoff",
            "agent_profile": "customer_ceshi",
        },
        config={"configurable": {"thread_id": "s-ceshi-non-write-handoff"}},
    )

    assert upload.calls == []
    assert standard_agent.calls
    assert "内部分类话术" not in result["messages"][-1].content
    assert "船位延迟" in result["messages"][-1].content
    assert result["route_trace"]["check_result"]["ship_update_subagent_status"] == "non_write"


def test_legacy_flat_pending_migrates_to_draft_and_mmsi_followup_executes(monkeypatch):
    upload = FakeTool("upload_ship_position", lambda args: "船位更新成功！")
    standard_agent = RecordingStandardAgent()
    monkeypatch.setattr("agents.agent._build_standard_agent", lambda *args, **kwargs: standard_agent)
    monkeypatch.setattr("agents.agent.SkillLoader.get_tools_by_names", lambda names: [upload])
    graph = _build_lightweight_customer_support_agent(
        ctx=SimpleNamespace(run_id="r-ceshi-flat-pending"),
        cfg={"config": {}},
        workspace_path=str(Path(__file__).resolve().parents[1]),
        profile=AgentProfile(profile_id="customer_ceshi", skills=["hifleet_ship_service"]),
    )
    legacy_flat_pending = {
        "eta": "2026-06-06 14:30 (UTC)",
        "lat": "23°25.776' N",
        "lon": "110°32.46' E",
        "speed": "0",
        "course": "123",
        "heading": "122",
        "navstatus": "机动船在航",
        "ship_name": "RUN GUI 666",
        "updatetime": "2026-07-08 13:39:00",
        "destination": "AONANTONG",
    }

    result = graph.invoke(
        {
            "messages": [HumanMessage(content="413904458")],
            "pending_update_state": legacy_flat_pending,
            "session_id": "s-ceshi-flat-pending",
            "agent_profile": "customer_ceshi",
        },
        config={"configurable": {"thread_id": "s-ceshi-flat-pending"}},
    )

    assert standard_agent.calls == []
    assert upload.calls == [
        {
            "mmsi": "413904458",
            "lon": "110.541",
            "lat": "23.4296",
            "updatetime": "2026-07-08 13:39:00",
            "speed": "0",
            "heading": "122",
            "course": "123",
            "navstatus": "机动船在航",
            "destination": "AONANTONG",
            "eta": "2026-06-06 14:30 (UTC)",
        }
    ]
    assert result["ship_update_draft"]["status"] == "executed_success"


def test_ambiguous_update_creates_awaiting_operation_pending(monkeypatch):
    upload = FakeTool("upload_ship_position", lambda args: "不应调用")
    graph = _graph(monkeypatch, [upload])

    result = graph.invoke(
        {"messages": [HumanMessage(content="请协助更新")], "session_id": "s-ceshi-ambiguous", "agent_profile": "customer_ceshi"},
        config={"configurable": {"thread_id": "s-ceshi-ambiguous"}},
    )

    pending = result["pending_update_state"]
    assert upload.calls == []
    assert pending["active"] is True
    assert pending["operation_type"] == "ambiguous_update"
    assert pending["status"] == "awaiting_operation_type"
    assert "更新船位" in result["messages"][-1].content


def test_missing_mmsi_pending_then_followup_mmsi_executes(monkeypatch):
    upload = FakeTool("upload_ship_position", lambda args: "船位更新成功！")
    graph = _graph(monkeypatch, [upload])
    thread_config = {"configurable": {"thread_id": "s-ceshi-pending-mmsi"}}

    first = graph.invoke(
        {
            "messages": [HumanMessage(content="更新船位，位置：15.206667,118.703333，更新时间：2026-07-06 09:20:00，状态：机动船在航")],
            "session_id": "s-ceshi-pending-mmsi",
            "agent_profile": "customer_ceshi",
        },
        config=thread_config,
    )

    assert upload.calls == []
    assert first["pending_update_state"]["status"] == "awaiting_ship_identity"
    assert "mmsi" in [item.lower() for item in first["pending_update_state"]["missing_required_fields"]]

    second = graph.invoke(
        {
            "messages": [HumanMessage(content="375066971")],
            "session_id": "s-ceshi-pending-mmsi",
            "agent_profile": "customer_ceshi",
        },
        config=thread_config,
    )

    assert second["route_trace"]["pending_used"] is True
    assert upload.calls[0]["mmsi"] == "375066971"
    assert upload.calls[0]["lon"] == "118.703333"
    assert upload.calls[0]["lat"] == "15.206667"
    assert upload.calls[0]["updatetime"] == "2026-07-06 09:20:00"
    assert second["pending_update_state"]["status"] == "executed_success"


@pytest.mark.parametrize("profile_id", ["customer_support", "customer_ceshi"])
def test_ship_update_subagent_pending_mmsi_followup_same_for_support_and_ceshi(monkeypatch, profile_id):
    upload = FakeTool("upload_ship_position", lambda args: "船位更新成功！")
    graph = _graph(monkeypatch, [upload], profile_id=profile_id, run_id=f"r-{profile_id}-shared-pending")
    session_id = f"s-{profile_id}-shared-pending"
    thread_config = {"configurable": {"thread_id": session_id}}

    first = graph.invoke(
        {
            "messages": [HumanMessage(content="更新船位，位置：15.206667,118.703333，更新时间：2026-07-06 09:20:00，状态：机动船在航")],
            "session_id": session_id,
            "agent_profile": profile_id,
        },
        config=thread_config,
    )

    assert upload.calls == []
    assert first["pending_update_state"]["status"] == "awaiting_ship_identity"

    second = graph.invoke(
        {
            "messages": [HumanMessage(content="375066971")],
            "session_id": session_id,
            "agent_profile": profile_id,
        },
        config=thread_config,
    )

    assert second["route_trace"]["pending_used"] is True
    assert second["route_trace"]["ship_update_subagent_gate"]["reason"] == "active_pending_update"
    assert "active_pending" in second["route_trace"]["ship_update_subagent"]["evidence_sources"]
    assert upload.calls == [
        {
            "mmsi": "375066971",
            "lon": "118.703333",
            "lat": "15.206667",
            "updatetime": "2026-07-06 09:20:00",
            "navstatus": "机动船在航",
        }
    ]
    assert second["pending_update_state"]["status"] == "executed_success"


def test_placeholder_destination_not_saved_or_resumed_from_pending(monkeypatch):
    def upload_handler(args):
        lines = ["船位更新成功！", f"MMSI: {args['mmsi']}", "更新参数:"]
        if "destination" in args:
            lines.append(f"目的港: {args['destination']}")
        if "eta" in args:
            lines.append(f"ETA: {args['eta']}")
        return "\n".join(lines)

    upload = FakeTool("upload_ship_position", upload_handler)
    graph = _graph(monkeypatch, [upload])
    thread_config = {"configurable": {"thread_id": "s-ceshi-pending-placeholder-destination"}}

    first = graph.invoke(
        {
            "messages": [
                HumanMessage(
                    content=(
                        "更新船位，AIS船名:MINZHANGYU05666，目的港/ETA: -- / --，"
                        "位置:23°56.809' N 117°43.797' E，对地/水航速:1.2 kn/--，"
                        "航行状态:机动船在航，更新时间:2026-07-06 15:04:00"
                    )
                )
            ],
            "session_id": "s-ceshi-pending-placeholder-destination",
            "agent_profile": "customer_ceshi",
        },
        config=thread_config,
    )

    pending_fields = first["pending_update_state"]["extracted_fields"]
    assert first["pending_update_state"]["status"] == "awaiting_ship_identity"
    assert "destination" not in pending_fields
    assert "eta" not in pending_fields

    second = graph.invoke(
        {
            "messages": [HumanMessage(content="412510631")],
            "session_id": "s-ceshi-pending-placeholder-destination",
            "agent_profile": "customer_ceshi",
        },
        config=thread_config,
    )

    assert second["route_trace"]["pending_used"] is True
    assert upload.calls
    assert upload.calls[0]["mmsi"] == "412510631"
    assert "destination" not in upload.calls[0]
    assert "eta" not in upload.calls[0]
    assert "目的港: /ETA" not in second["messages"][-1].content


def test_single_mmsi_without_pending_does_not_execute_write(monkeypatch):
    upload = FakeTool("upload_ship_position", lambda args: "不应调用")
    static_update = FakeTool("update_ship_static_info", lambda args: "不应调用")
    graph = _graph(monkeypatch, [upload, static_update])

    result = graph.invoke(
        {"messages": [HumanMessage(content="375066971")], "session_id": "s-ceshi-no-pending", "agent_profile": "customer_ceshi"},
        config={"configurable": {"thread_id": "s-ceshi-no-pending"}},
    )

    assert upload.calls == []
    assert static_update.calls == []
    assert "更新成功" not in result["messages"][-1].content
    assert result["route_trace"]["readable_trace"]["input_summary"]["pending_used"] is False


def test_static_destination_error_pending_then_destination_followup_executes(monkeypatch):
    static_update = FakeTool("update_ship_static_info", lambda args: "静态信息更新成功！")
    standard_agent = RecordingStandardAgent()
    monkeypatch.setattr("agents.agent._build_standard_agent", lambda *args, **kwargs: standard_agent)
    monkeypatch.setattr("agents.agent.SkillLoader.get_tools_by_names", lambda names: [static_update])
    graph = _build_lightweight_customer_support_agent(
        ctx=SimpleNamespace(run_id="r-ceshi-static-destination-pending"),
        cfg={"config": {}},
        workspace_path=str(Path(__file__).resolve().parents[1]),
        profile=AgentProfile(profile_id="customer_ceshi", skills=["hifleet_ship_service"]),
    )
    thread_config = {"configurable": {"thread_id": "s-ceshi-static-destination-pending"}}

    first = graph.invoke(
        {
            "messages": [HumanMessage(content="636018167 船舶目的港错误")],
            "session_id": "s-ceshi-static-destination-pending",
            "agent_profile": "customer_ceshi",
        },
        config=thread_config,
    )

    assert static_update.calls == []
    assert first["pending_update_state"]["operation_type"] == "static_update"
    assert first["pending_update_state"]["status"] == "awaiting_required_fields"

    second = graph.invoke(
        {
            "messages": [HumanMessage(content="正确的目的港信息 PIRAEUS")],
            "session_id": "s-ceshi-static-destination-pending",
            "agent_profile": "customer_ceshi",
        },
        config=thread_config,
    )

    assert standard_agent.calls == []
    assert static_update.calls == [{"mmsi": "636018167", "destination": "PIRAEUS"}]
    assert second["generated_tool_calls"] == ["update_ship_static_info"]
    assert second["check_result"]["current_run_tool_success"] is True
    assert second["messages"][-1].content == "静态信息更新成功！"


def test_ship_name_pending_followup_mmsi_uses_current_pending_fields_not_history(monkeypatch):
    upload = FakeTool("upload_ship_position", lambda args: "船位更新成功！")
    standard_agent = RecordingStandardAgent()
    monkeypatch.setattr("agents.agent._build_standard_agent", lambda *args, **kwargs: standard_agent)
    monkeypatch.setattr("agents.agent.SkillLoader.get_tools_by_names", lambda names: [upload])
    graph = _build_lightweight_customer_support_agent(
        ctx=SimpleNamespace(run_id="r-ceshi-confirm-pending"),
        cfg={"config": {}},
        workspace_path=str(Path(__file__).resolve().parents[1]),
        profile=AgentProfile(profile_id="customer_ceshi", skills=["hifleet_ship_service"]),
    )
    thread_config = {"configurable": {"thread_id": "s-ceshi-confirm-pending"}}

    first = graph.invoke(
        {
            "messages": [
                HumanMessage(
                    content=(
                        "更新船位，AIS船名:AOSHI，位置:23°58.564' N 118°3.185' E，"
                        "对地/水航速:1.2 kn，航行状态:机动船在航，更新时间:2026-07-06 14:34:00"
                    )
                )
            ],
            "session_id": "s-ceshi-confirm-pending",
            "agent_profile": "customer_ceshi",
        },
        config=thread_config,
    )

    assert upload.calls == []
    assert first["pending_update_state"]["status"] == "awaiting_ship_identity"

    second = graph.invoke(
        {
            "messages": [
                HumanMessage(content="查询 MMSI 636014637 船位"),
                HumanMessage(
                    content=(
                        "船位更新成功！\nMMSI: 636014637\n更新参数:\n"
                        "经度: 66.688383\n纬度: 22.116567\n航速: 4.6 节\n"
                        "航首向: 137.0\nETA: 2026-06-28 15:00\n吃水: 11.0 米\n"
                        "航行状态: 失控\n更新时间: 2026-07-06 14:34:00"
                    )
                ),
                HumanMessage(content="308068077"),
            ],
            "session_id": "s-ceshi-confirm-pending",
            "agent_profile": "customer_ceshi",
        },
        config=thread_config,
    )

    assert standard_agent.calls == []
    assert second["route_trace"]["route"] == "ship_update"
    assert second["route_trace"]["pending_used"] is True
    assert second["route_trace"]["ship_update_subagent_gate"]["reason"] == "active_pending_update"
    assert "active_pending" in second["route_trace"]["ship_update_subagent"]["evidence_sources"]
    assert upload.calls
    assert upload.calls[0]["mmsi"] == "308068077"
    assert upload.calls[0]["lon"] == "118.053083"
    assert upload.calls[0]["lat"] == "23.976067"
    assert upload.calls[0]["speed"] == "1.2"
    assert upload.calls[0]["navstatus"] == "机动船在航"
    assert upload.calls[0]["updatetime"] == "2026-07-06 14:34:00"
    assert upload.calls[0]["lon"] != "66.688383"
    assert upload.calls[0]["lat"] != "22.116567"
    assert upload.calls[0].get("eta") != "2026-06-28 15:00"


def test_confirm_update_without_pending_does_not_execute_write(monkeypatch):
    upload = FakeTool("upload_ship_position", lambda args: "不应调用")
    standard_agent = RecordingStandardAgent(response="请提供需要更新的船舶和船位信息。")
    monkeypatch.setattr("agents.agent._build_standard_agent", lambda *args, **kwargs: standard_agent)
    monkeypatch.setattr("agents.agent.SkillLoader.get_tools_by_names", lambda names: [upload])
    monkeypatch.setattr("agents.agent._invoke_customer_support_json_agent", lambda *args, **kwargs: {})
    graph = _build_lightweight_customer_support_agent(
        ctx=SimpleNamespace(run_id="r-ceshi-confirm-without-pending"),
        cfg={"config": {}},
        workspace_path=str(Path(__file__).resolve().parents[1]),
        profile=AgentProfile(profile_id="customer_ceshi", skills=["hifleet_ship_service"]),
    )

    result = graph.invoke(
        {"messages": [HumanMessage(content="确认更新")], "session_id": "s-ceshi-confirm-without-pending", "agent_profile": "customer_ceshi"},
        config={"configurable": {"thread_id": "s-ceshi-confirm-without-pending"}},
    )

    assert upload.calls == []
    assert result["route_trace"]["ship_update_gate"]["should_run_harness"] is False
    assert result["route_trace"]["ship_update_subagent_gate"]["should_run_subagent"] is False
    assert result["route_trace"]["pending_used"] is False
    assert "更新成功" not in result["messages"][-1].content


def test_standard_agent_success_claim_without_write_is_blocked(monkeypatch):
    standard_agent = RecordingStandardAgent(response="静态信息更新成功！\nMMSI: 730285526\n更新参数:\n船舶类型: 散货船")
    monkeypatch.setattr("agents.agent._build_standard_agent", lambda *args, **kwargs: standard_agent)
    monkeypatch.setattr("agents.agent.SkillLoader.get_tools_by_names", lambda names: [])
    monkeypatch.setattr(
        "agents.agent._invoke_customer_support_json_agent",
        lambda *args, **kwargs: {
            "intent": "knowledge",
            "confidence": "high",
            "operation_type": "none",
            "ship_update_candidate": False,
            "ship_write_request": False,
            "pending_action": "none",
            "non_write_reason": "none",
        },
    )
    graph = _build_lightweight_customer_support_agent(
        ctx=SimpleNamespace(run_id="r-standard-success-claim"),
        cfg={"config": {"ship_update_subagent_prompt_driven": True}},
        workspace_path=str(Path(__file__).resolve().parents[1]),
        profile=AgentProfile(profile_id="customer_ceshi", skills=["hifleet_ship_service"]),
    )

    result = graph.invoke(
        {"messages": [HumanMessage(content="普通咨询")], "session_id": "s-standard-success-claim", "agent_profile": "customer_ceshi"},
        config={"configurable": {"thread_id": "s-standard-success-claim"}},
    )

    assert standard_agent.calls
    assert result["generated_tool_calls"] == []
    assert "静态信息更新成功" not in result["messages"][-1].content
    assert result["check_result"]["blocked_unverified_write_success_claim"] is True


def test_field_confirmation_pending_resume_executes_current_pending_fields(monkeypatch):
    upload = FakeTool("upload_ship_position", lambda args: "船位更新成功！")
    standard_agent = RecordingStandardAgent()
    monkeypatch.setattr("agents.agent._build_standard_agent", lambda *args, **kwargs: standard_agent)
    monkeypatch.setattr("agents.agent.SkillLoader.get_tools_by_names", lambda names: [upload])
    graph = _build_lightweight_customer_support_agent(
        ctx=SimpleNamespace(run_id="r-ceshi-field-confirmation"),
        cfg={"config": {}},
        workspace_path=str(Path(__file__).resolve().parents[1]),
        profile=AgentProfile(profile_id="customer_ceshi", skills=["hifleet_ship_service"]),
    )
    pending = {
        "active": True,
        "operation_type": "position_update",
        "status": "awaiting_field_confirmation",
        "source_turn_id": "previous-turn",
        "expires_after_turns": 5,
        "turns_elapsed": 0,
        "ship_identity": {"mmsi": "477167800", "imo": "", "name": "", "candidate_mmsi": []},
        "extracted_fields": {
            "lon": "103°59.606' E",
            "lat": "01°10.044' N",
            "updatetime": "2026-07-06 17:44:00",
            "speed": "0",
            "heading": "090",
            "course": "219",
            "draft": "8.1",
        },
        "missing_required_fields": [],
        "invalid_fields": [],
        "conflict_fields": ["course"],
        "last_question_to_user": "识别到字段冲突：course。请确认以哪一个值为准后我再继续更新。",
        "confirmation_required": True,
        "can_resume": True,
    }

    result = graph.invoke(
        {
            "messages": [HumanMessage(content="按照上述参数更新")],
            "pending_update_state": pending,
            "session_id": "s-ceshi-field-confirmation",
            "agent_profile": "customer_ceshi",
        },
        config={"configurable": {"thread_id": "s-ceshi-field-confirmation"}},
    )

    assert standard_agent.calls == []
    assert result["route_trace"]["route"] == "ship_update"
    assert result["route_trace"]["pending_used"] is True
    assert result["route_trace"]["ship_update_subagent_gate"]["reason"] == "active_pending_update"
    assert "active_pending" in result["route_trace"]["ship_update_subagent"]["evidence_sources"]
    assert upload.calls == [
        {
            "mmsi": "477167800",
            "lon": "103.993433",
            "lat": "1.1674",
            "updatetime": "2026-07-06 17:44:00",
            "speed": "0",
            "heading": "90",
            "course": "219",
            "draft": "8.1",
        }
    ]


def test_first_step_ship_update_understanding_forces_subagent(monkeypatch):
    upload = FakeTool("upload_ship_position", lambda args: "不应调用")
    standard_agent = RecordingStandardAgent()
    monkeypatch.setattr("agents.agent._build_standard_agent", lambda *args, **kwargs: standard_agent)
    monkeypatch.setattr("agents.agent.SkillLoader.get_tools_by_names", lambda names: [upload])
    def fake_json_agent(ctx, cfg, system_prompt, payload, model_override=""):
        if "tool_contracts" not in payload:
            return _understanding_json("ambiguous_update")
        return {
            "status": "need_user_input",
            "operation_type": "mixed_update",
            "missing_fields": ["operation_type"],
            "pending_action": "create",
            "reply_to_user": "请确认是更新船位，还是更新船舶静态信息？",
            "confidence": "medium",
        }

    monkeypatch.setattr("agents.agent._invoke_customer_support_json_agent", fake_json_agent)
    graph = _build_lightweight_customer_support_agent(
        ctx=SimpleNamespace(run_id="r-ceshi-first-step-gate"),
        cfg={"config": {}},
        workspace_path=str(Path(__file__).resolve().parents[1]),
        profile=AgentProfile(profile_id="customer_ceshi", skills=["hifleet_ship_service"]),
    )

    result = graph.invoke(
        {"messages": [HumanMessage(content="按这张图处理一下")], "session_id": "s-ceshi-first-step-gate", "agent_profile": "customer_ceshi"},
        config={"configurable": {"thread_id": "s-ceshi-first-step-gate"}},
    )

    assert standard_agent.calls == []
    assert upload.calls == []
    assert result["route_trace"]["route"] == "ship_update"
    assert result["route_trace"]["ship_update_gate"]["reason"] == "agent_ship_update"
