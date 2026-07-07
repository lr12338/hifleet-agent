import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from langchain_core.messages import AIMessage, HumanMessage

from agents.agent import _build_lightweight_customer_support_agent
from agents.customer_support_router import classify_message, execute_update_chain, extract_entities, make_trace
from agents.profiles import AgentProfile


class FakeTool:
    def __init__(self, name, handler):
        self.name = name
        self.handler = handler
        self.calls = []

    def invoke(self, args):
        self.calls.append(args)
        return self.handler(args)


def test_static_update_failure_does_not_claim_success():
    static_update = FakeTool("update_ship_static_info", lambda args: "error: timeout")
    text = "更新目的港，mmsi：730285526，SINGAPORE / 2026-07-08 03:00:00"
    entities = extract_entities(text)
    trace = make_trace(classify_message(text, entities), entities)

    output = execute_update_chain(text, entities, {"update_ship_static_info": static_update}, trace)

    assert "静态信息更新成功" not in output
    assert "暂未成功提交" in output
    assert trace.check_result["write_result"] is False
    assert trace.check_result["allowed_success_claim"] is False


def test_destination_eta_frontend_question_does_not_call_write_tools(monkeypatch):
    upload = FakeTool("upload_ship_position", lambda args: "不应调用")
    static_update = FakeTool("update_ship_static_info", lambda args: "不应调用")

    class FakeStandardAgent:
        def invoke(self, payload, context=None, config=None):
            return {
                "messages": list(payload["messages"])
                + [
                    AIMessage(
                        content=(
                            "目前没有查到普通用户可在前台自助编辑目的港/ETA 的明确入口。"
                            "如需协助处理，请提供 MMSI、正确目的港和 ETA。"
                        )
                    )
                ]
            }

    monkeypatch.setattr("agents.agent._build_standard_agent", lambda *args, **kwargs: FakeStandardAgent())
    monkeypatch.setattr("agents.agent.SkillLoader.get_tools_by_names", lambda names: [upload, static_update])
    graph = _build_lightweight_customer_support_agent(
        ctx=SimpleNamespace(run_id="r-front-eta"),
        cfg={"config": {}},
        workspace_path=str(Path(__file__).resolve().parents[1]),
        profile=AgentProfile(profile_id="customer_ceshi", skills=["hifleet_ship_service"]),
    )

    result = graph.invoke(
        {
            "messages": [HumanMessage(content="怎么在 HiFleet 平台手动更新船舶目的港和 ETA？")],
            "session_id": "s-front-eta",
            "agent_profile": "customer_ceshi",
        },
        config={"configurable": {"thread_id": "s-front-eta"}},
    )

    answer = result["messages"][-1].content
    assert upload.calls == []
    assert static_update.calls == []
    assert "编辑按钮" not in answer
    assert "立即生效" not in answer
    assert "自动解析" not in answer
    assert "MMSI" in answer
    assert result["route_trace"]["ship_update_gate"]["should_run_harness"] is False
    assert result["route_trace"]["check_result"].get("scenario_guard") != "frontend_capability_question"


def test_bay_of_bengal_tracking_issue_not_misrouted_to_destination_eta_or_write(monkeypatch):
    upload = FakeTool("upload_ship_position", lambda args: "不应调用")
    static_update = FakeTool("update_ship_static_info", lambda args: "不应调用")

    class FakeStandardAgent:
        def invoke(self, payload, context=None, config=None):
            return {
                "messages": list(payload["messages"])
                + [
                    AIMessage(
                        content=(
                            "这类情况更像船位跟踪数据接收或展示延迟问题。建议提供两艘船的 MMSI、"
                            "异常开始时间和截图时段，我们可以从 AIS 接收链路、卫星/岸基覆盖、"
                            "平台数据入库与展示刷新记录逐项排查。"
                        )
                    )
                ]
            }

    monkeypatch.setattr("agents.agent._build_standard_agent", lambda *args, **kwargs: FakeStandardAgent())
    monkeypatch.setattr("agents.agent.SkillLoader.get_tools_by_names", lambda names: [upload, static_update])
    monkeypatch.setattr(
        "agents.agent._run_direct_multimodal_perception",
        lambda **kwargs: {
            "attachment_type": "image",
            "recognized_text": (
                "AIS船名 GOLDEN LILY 更新于 2026-06-27 05:44:25 UTC+8 1天前 "
                "暂未收到更新船位 MMSI 370731000 目的港 INPR ETA 2026-06-29 19:00 "
                "Bay of Bengal 10. 禾盛东方"
            ),
            "visible_text": (
                "AIS船名 GOLDEN LILY 暂未收到更新船位 MMSI 370731000 "
                "目的港 INPR ETA 2026-06-29 19:00 Bay of Bengal"
            ),
            "summary": "孟加拉湾区域两艘船被圈出，其中 GOLDEN LILY 面板提示暂未收到更新船位。",
            "visible_features": "页面内有当前、历史、计划、资料、居中、分享等按钮。",
            "confidence": "high",
            "source": "test",
        },
    )
    graph = _build_lightweight_customer_support_agent(
        ctx=SimpleNamespace(run_id="r-bay-tracking"),
        cfg={"config": {}},
        workspace_path=str(Path(__file__).resolve().parents[1]),
        profile=AgentProfile(profile_id="customer_ceshi", skills=["hifleet_ship_service"]),
    )

    result = graph.invoke(
        {
            "messages": [
                HumanMessage(
                    content=[
                        {"type": "image_url", "image_url": {"url": "https://example.com/bay.jpg"}},
                        {
                            "type": "text",
                            "text": (
                                "请结合用户上一条发送的媒体内容，回答以下补充说明或问题："
                                "我司2艘船舶在BAY OF BENGAL 航行连续1-2天都没有船位跟踪，"
                                "船长讲：船上的AIS 工况也正常。我查周边其他船舶的船位跟踪都是正常的，"
                                "请指导们后台看看什么问题？谢谢！"
                            ),
                        },
                    ]
                )
            ],
            "session_id": "s-bay-tracking",
            "agent_profile": "customer_ceshi",
        },
        config={"configurable": {"thread_id": "s-bay-tracking"}},
    )

    answer = result["messages"][-1].content
    assert upload.calls == []
    assert static_update.calls == []
    assert "自助编辑" not in answer
    assert "目的港/ETA" not in answer
    assert "船位跟踪" in answer
    assert "MMSI" in answer
    assert result["route_trace"]["ship_update_gate"]["should_run_harness"] is False
    assert result["route_trace"]["reasoning_trace"]["ship_tracking_issue"] is True
    assert result["generated_tool_calls"] == []
    assert result["route_trace"]["tool_call_sequence"] == []


def test_text_attachment_mmsi_conflict_blocks_write():
    upload = FakeTool("upload_ship_position", lambda args: "不应调用")
    text = "更新船位 MMSI 730285526 经度 121.687167 纬度 39.006833 更新时间 2026-07-04 14:43:00"
    perception = {"visible_text": "MMSI:375066971 经度 121.687167 纬度 39.006833 更新时间 2026-07-04 14:43:00", "confidence": "high"}
    entities = extract_entities(text)
    trace = make_trace(classify_message(text, entities), entities)

    output = execute_update_chain(text, entities, {"upload_ship_position": upload}, trace, perception=perception)

    assert upload.calls == []
    assert "字段冲突" in output
    assert trace.reasoning_trace["pending_update_state"]["status"] == "awaiting_field_confirmation"
    assert trace.reasoning_trace["pending_update_state"]["conflict_fields"] == ["mmsi"]


def test_draft_update_without_context_asks_which_write_type():
    upload = FakeTool("upload_ship_position", lambda args: "不应调用")
    static_update = FakeTool("update_ship_static_info", lambda args: "不应调用")
    text = "MMSI 730285526 吃水改为 12.6"
    entities = extract_entities(text)
    trace = make_trace(classify_message(text, entities), entities)

    output = execute_update_chain(text, entities, {"upload_ship_position": upload, "update_ship_static_info": static_update}, trace)

    assert upload.calls == []
    assert static_update.calls == []
    assert "当前船位里的吃水" in output
    assert "静态信息里的吃水" in output


def test_mixed_update_does_not_double_write():
    upload = FakeTool("upload_ship_position", lambda args: "不应调用")
    static_update = FakeTool("update_ship_static_info", lambda args: "不应调用")
    text = "请更新船位和目的港 ETA，MMSI 730285526，经度 121.687167 纬度 39.006833 更新时间 2026-07-04 14:43:00，目的港 SINGAPORE ETA 2026-07-08 03:00:00"
    entities = extract_entities(text)
    trace = make_trace(classify_message(text, entities), entities)

    output = execute_update_chain(text, entities, {"upload_ship_position": upload, "update_ship_static_info": static_update}, trace)

    assert upload.calls == []
    assert static_update.calls == []
    assert "先更新哪一项" in output
    assert trace.reasoning_trace["pending_update_state"]["operation_type"] == "mixed_update"
