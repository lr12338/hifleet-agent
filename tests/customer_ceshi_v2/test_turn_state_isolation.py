from types import SimpleNamespace

from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import MemorySaver

from agents.customer_ceshi_v2.builder import CHECKPOINT_NAMESPACE, _local_media_fallback_answer, _media_failure_answer, build_customer_ceshi_v2_agent
from agents.customer_ceshi_v2.contracts import AgentDecision, Observation
from agents.customer_ceshi_v2.tools import ToolDescriptor


class FollowupTextClient:
    def __init__(self):
        self.calls = []

    def decide(self, **kwargs):
        self.calls.append(kwargs)
        goal = kwargs["task_goal"]
        if goal == "查询船舶当前船位，MMSI 308068077" and kwargs["step_count"] == 0:
            return AgentDecision(action="call_tools", tool_calls=[{"name": "get_ship_position", "arguments": {"mmsi": "308068077"}}])
        if goal == "查询船舶当前船位，MMSI 308068077":
            return AgentDecision(action="finish", answer_draft="已查询当前船位。")
        if goal == "最近停靠了哪些港口" and kwargs["step_count"] == 0:
            assert any("mmsi=308068077" in fact for item in kwargs["observations"] for fact in item.get("facts", []))
            return AgentDecision(action="call_tools", tool_calls=[{"name": "get_ship_call_ports", "arguments": {"mmsi": "308068077"}}])
        return AgentDecision(action="finish", answer_draft="最近停靠港口：A港、B港。")


class FollowupRegistry:
    def descriptors(self):
        return [
            ToolDescriptor(name="get_ship_position", capability="ship"),
            ToolDescriptor(name="get_ship_call_ports", capability="ship"),
        ]

    def invoke(self, call):
        if call.name == "get_ship_position":
            return Observation(status="success", capability=call.name, facts=["MMSI 308068077 current position"], data={"mmsi": "308068077"})
        return Observation(status="success", capability=call.name, facts=["Recent ports: A港, B港"], data={"mmsi": "308068077"})


def _build_graph(monkeypatch, text, registry, **extras):
    import agents.customer_ceshi_v2.builder as builder

    monkeypatch.setattr(builder, "get_memory_saver", lambda: MemorySaver())
    context = SimpleNamespace(customer_ceshi_v2_text_client=text, customer_ceshi_v2_tool_registry=registry, **extras)
    return build_customer_ceshi_v2_agent(context, {"config": {"customer_ceshi_v2_max_steps": 4}}, ".", SimpleNamespace(profile_id="customer_ceshi"))


def test_new_turn_resets_stale_execution_state_but_keeps_confirmed_ship(monkeypatch):
    text = FollowupTextClient()
    graph = _build_graph(monkeypatch, text, FollowupRegistry())
    config = {"configurable": {"thread_id": "same-session"}}

    first = graph.invoke(
        {
            "messages": [HumanMessage(content="查询船舶当前船位，MMSI 308068077")],
            "session_id": "same-session",
        },
        config,
    )
    second = graph.invoke(
        {
            "messages": [HumanMessage(content="最近停靠了哪些港口")],
            "session_id": "same-session",
            "step_count": 6,
            "candidate_answer": "旧船位答案",
            "dependency_error": {"code": "model_invalid_response", "retryable": True},
            "started_at_ms": 1,
        },
        config,
    )

    assert first["generated_answer"] == "已查询当前船位。"
    assert second["generated_answer"] == "最近停靠港口：A港、B港。"
    assert second["degrade_reason"] == ""
    assert second["dependency_error"] == {}
    assert second["generated_tool_calls"] == ["get_ship_call_ports"]
    assert second["turn_diagnostics"]["turn_initialized"] is True
    assert second["turn_diagnostics"]["inherited_entity"] is True
    assert [call["step_count"] for call in text.calls] == [0, 1, 0, 1]
    assert CHECKPOINT_NAMESPACE == "customer_ceshi_v3"


class FinishThenMediaClient:
    def __init__(self):
        self.calls = 0

    def decide(self, **kwargs):
        self.calls += 1
        if kwargs["step_count"] == 0:
            return AgentDecision(action="finish", answer_draft="uninspected answer")
        return AgentDecision(action="finish", answer_draft="识别完成。")


class RecordingPerception:
    def __init__(self):
        self.assets = []

    def inspect(self, assets, requests):
        self.assets.append([asset.url for asset in assets])
        observations = [
            Observation(
                status="success",
                capability="inspect_media",
                facts=["image inspected"],
                data={"perception_packet": {"asset_id": request.asset_id, "factual_summary": "image inspected"}},
            )
            for request in requests
        ]
        return observations, 0


class EmptyRegistry:
    def descriptors(self):
        return []


class VirtualMediaToolClient:
    def __init__(self):
        self.calls = 0

    def decide(self, **kwargs):
        self.calls += 1
        if kwargs["step_count"] == 0:
            return AgentDecision(action="call_tools", tool_calls=[{"name": "inspect_media", "arguments": {}}])
        return AgentDecision(action="finish", answer_draft="图片识别完成。")


def test_current_turn_only_uses_latest_media_and_forces_inspection(monkeypatch):
    text = FinishThenMediaClient()
    perception = RecordingPerception()
    graph = _build_graph(monkeypatch, text, EmptyRegistry(), customer_ceshi_v2_perception_service=perception)
    config = {"configurable": {"thread_id": "media-session"}}

    graph.invoke(
        {"messages": [HumanMessage(content=[{"type": "image_url", "image_url": {"url": "https://example.test/old.png"}}, {"type": "text", "text": "这是什么标识"}])], "session_id": "media-session"},
        config,
    )
    result = graph.invoke(
        {"messages": [HumanMessage(content=[{"type": "image_url", "image_url": {"url": "https://example.test/new.png"}}, {"type": "text", "text": "这是什么标识"}])], "session_id": "media-session"},
        config,
    )

    assert perception.assets == [["https://example.test/old.png"], ["https://example.test/new.png"]]
    assert result["metrics"]["media_calls"] == 1
    assert result["generated_answer"] == "识别完成。"
    assert text.calls == 2


class FailingPerception:
    def inspect(self, assets, requests):
        return [Observation(status="temporary_error", capability="inspect_media", warnings=["model_timeout"], retry_allowed=True) for _ in requests], 0


def test_current_media_failure_is_reported_without_orchestrator_fallback(monkeypatch):
    text = FinishThenMediaClient()
    graph = _build_graph(
        monkeypatch,
        text,
        EmptyRegistry(),
        customer_ceshi_v2_perception_service=FailingPerception(),
    )

    result = graph.invoke(
        {
            "messages": [
                HumanMessage(
                    content=[
                        {"type": "image_url", "image_url": {"url": "https://example.test/current.png"}},
                        {"type": "text", "text": "这是什么标识"},
                    ]
                )
            ],
            "session_id": "media-failure",
        },
        {"configurable": {"thread_id": "media-failure"}},
    )

    assert text.calls == 0
    assert result["status"] == "degraded"
    assert result["degrade_reason"] == "model_timeout"
    assert result["dependency_error"] == {"code": "model_timeout", "retryable": True}
    assert result["metrics"]["media_calls"] == 1
    assert result["turn_diagnostics"]["degrade_stage"] == "media_perception"


def test_virtual_inspect_media_call_does_not_reach_capability_registry(monkeypatch):
    text = VirtualMediaToolClient()
    perception = RecordingPerception()
    graph = _build_graph(monkeypatch, text, EmptyRegistry(), customer_ceshi_v2_perception_service=perception)

    result = graph.invoke(
        {
            "messages": [
                HumanMessage(
                    content=[
                        {"type": "image_url", "image_url": {"url": "https://example.test/current.png"}},
                        {"type": "text", "text": "图上紫色的波浪线是指的什么"},
                    ]
                )
            ],
            "session_id": "virtual-media-tool",
        },
        {"configurable": {"thread_id": "virtual-media-tool"}},
    )

    assert result["status"] == "success"
    assert result["generated_answer"] == "图片识别完成。"
    assert result["metrics"]["media_calls"] == 1
    assert result["metrics"]["tool_calls"] == 0


def test_local_visual_fallback_answer_stays_provisional():
    answer = _local_media_fallback_answer(
        [
            {
                "capability": "inspect_media",
                "data": {
                    "perception_packet": {
                        "model": "local_visual_fallback",
                        "suspected_symbol": "安全水域浮标（Safe Water Mark）",
                        "visual_features": ["红色圆形标记", "中心黑色圆点"],
                    }
                },
            }
        ]
    )

    assert "初步判断" in answer
    assert "安全水域浮标" in answer
    assert "正式海图图例" in answer


def test_setting_image_failure_requests_the_specific_missing_detail():
    answer = _media_failure_answer("model_unavailable", "请结合用户上一条发送的媒体内容，这个是如何设定的")

    assert "具体设置项" in answer
    assert "完整设置面板和文字" in answer


def test_previous_media_reference_uses_only_last_successful_asset(monkeypatch):
    text = FinishThenMediaClient()
    perception = RecordingPerception()
    graph = _build_graph(monkeypatch, text, EmptyRegistry(), customer_ceshi_v2_perception_service=perception)
    config = {"configurable": {"thread_id": "previous-media"}}

    graph.invoke(
        {"messages": [HumanMessage(content=[{"type": "image_url", "image_url": {"url": "https://example.test/last.png"}}, {"type": "text", "text": "这是什么标识"}])], "session_id": "previous-media"},
        config,
    )
    result = graph.invoke(
        {"messages": [HumanMessage(content="请结合上一条媒体，这是什么标识")], "session_id": "previous-media"},
        config,
    )

    assert perception.assets == [["https://example.test/last.png"], ["https://example.test/last.png"]]
    assert result["turn_diagnostics"]["inherited_media"] is True
