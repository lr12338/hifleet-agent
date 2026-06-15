import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agents.customer_support_stream_debug import build_customer_support_debug_events


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
