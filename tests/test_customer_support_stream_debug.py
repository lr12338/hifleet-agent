import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agents.customer_support_stream_debug import (
    DebugRuntimeCursor,
    build_customer_support_debug_events,
    build_customer_support_debug_events_from_update,
)


def _events_for(text, attachment=None):
    content = []
    if attachment:
        content.append({"type": "image_url", "image_url": {"url": attachment}})
    content.append({"type": "text", "text": text})
    return build_customer_support_debug_events({"messages": [{"role": "user", "content": content}]})


def test_reference_01_stream_debug_explains_chart_symbol_search():
    events = _events_for("这个在全球海图里是什么意思", "/home/ecs-user/coze_ai/docs/参考链路/01_query.png")
    text = "\n".join(str(item.get("text", "")) for item in events)

    assert any(item["type"] == "thinking" for item in events)
    assert any(item["type"] == "tool_request" for item in events)
    assert "安全水域浮标" in text
    assert "HiFleet 全球海图 海图符号" in text
    assert "审查与确定" in text


def test_reference_02_stream_debug_has_upload_route_searches():
    events = _events_for("hifleet平台上传不了航线怎么办")
    text = "\n".join(str(item.get("text", "")) for item in events)
    queries = [item for item in events if item["type"] == "tool_request"]

    assert len(queries) >= 3
    assert "上传航线" in text
    assert "文件格式" in text
    assert "浏览器" in text


def test_reference_03_stream_debug_explains_anchor_area_circles():
    events = _events_for("图中的小圈圈是什么意思？", "/home/ecs-user/coze_ai/docs/参考链路/03_query.png")
    text = "\n".join(str(item.get("text", "")) for item in events)

    assert "锚地" in text
    assert "多模态感知" in text
    assert any(item["type"] == "tool_response" for item in events)


def test_reference_04_stream_debug_is_safe_methodology():
    events = _events_for("基于上述对输入的思考与回复，总结是如何思索和检索资源并审查确定的，详细介绍逻辑")
    text = "\n".join(str(item.get("text", "")) for item in events)

    assert "识别用户意图" in text
    assert "本地知识库" in text
    assert "不展示内部工具名" in text
    assert "api_key" not in text.lower()
    assert "system prompt" not in text.lower()


def test_runtime_update_debug_events_follow_real_plan_state():
    cursor = DebugRuntimeCursor()
    update = {
        "plan": {
            "route": "knowledge",
            "task_type": "platform_knowledge",
            "intent_agent_result": {"intent": "knowledge", "why": "用户在询问平台功能定义"},
            "reasoning_public_trace": [
                {"phase": "understand", "text": "已识别当前问题类型：definition。"},
                {"phase": "search_plan", "text": "已规划 2 条检索方向，优先本地知识库和 HiFleet 官方资料。"},
            ],
            "search_plan": [
                {"query": "HiFleet 绿点是什么意思", "source_priority": ["local_kb", "official_site"]},
                {"query": "HiFleet 绿点 船舶颜色", "source_priority": ["official_site"]},
            ],
        }
    }

    events = build_customer_support_debug_events_from_update(update, cursor)
    text = "\n".join(str(item.get("text", "")) for item in events)

    assert any(item["type"] == "message_start" for item in events)
    assert any(item["type"] == "tool_request" for item in events)
    assert "意图识别" in text
    assert "已规划 2 条检索方向" in text


def test_runtime_update_debug_events_emit_final_answer():
    cursor = DebugRuntimeCursor(started=True)
    update = {
        "finalize": {
            "messages": [{"role": "assistant", "content": "HiFleet 绿点一般表示船位状态正常。"}]
        }
    }

    events = build_customer_support_debug_events_from_update(update, cursor)

    assert events[0]["type"] == "answer"
    assert "绿点" in events[0]["text"]
    assert events[-1]["type"] == "message_end"
