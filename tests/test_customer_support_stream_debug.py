import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agents.customer_support_stream_debug import (
    DebugRuntimeCursor,
    build_customer_support_debug_events_from_update,
)


def test_no_filename_special_casing_for_01_query():
    """附件名 01_query.png 不得被推断为'安全水域浮标'。"""
    cursor = DebugRuntimeCursor()
    update = {
        "delegate": {
            "route": "knowledge",
            "task_type": "platform_knowledge",
            "attachments": [{"type": "image", "filename": "01_query.png", "url": "https://b.oss.com/k?Signature=s"}],
            "route_trace": {"tool_call_sequence": ["smart_search"]},
        }
    }
    events = build_customer_support_debug_events_from_update(update, cursor)
    text = " ".join(str(item.get("text", "")) for item in events)
    assert "安全水域浮标" not in text
    assert "锚地" not in text
    # No fabricated tool_request / planned tool_response
    assert not any(item["type"] == "tool_request" for item in events)
    assert not any(item["type"] == "tool_response" for item in events)
    # thinking events must be marked runtime_summary, not provider reasoning
    thinking = [item for item in events if item["type"] == "thinking"]
    assert thinking and all(item.get("source") == "runtime_summary" for item in thinking)


def test_no_pre_fabricated_tool_calls_or_planned_results():
    """运行路径不得在工具未真实执行时生成 tool_request/planned tool_response。"""
    cursor = DebugRuntimeCursor()
    update = {"delegate": {"route": "knowledge", "task_type": "platform_knowledge"}}
    events = build_customer_support_debug_events_from_update(update, cursor)
    assert not any(item["type"] == "tool_request" for item in events)
    assert not any(item["type"] == "tool_response" for item in events)


def test_runtime_summary_is_factual_not_stepwise_masquerade():
    cursor = DebugRuntimeCursor()
    update = {"delegate": {"route": "knowledge", "task_type": "platform_knowledge"}}
    events = build_customer_support_debug_events_from_update(update, cursor)
    thinking = [item for item in events if item["type"] == "thinking"][0]
    assert thinking["source"] == "runtime_summary"
    assert "运行时摘要" in thinking["text"]
    # Must not present fabricated step-by-step model reasoning
    assert "1. 前置安全" not in thinking["text"]


def test_runtime_update_debug_events_follow_real_delegate_state():
    cursor = DebugRuntimeCursor()
    update = {
        "delegate": {
            "route": "knowledge",
            "task_type": "platform_knowledge",
            "attachments": [{"type": "image"}],
            "route_trace": {
                "tool_call_sequence": ["smart_search", "get_ship_position"],
            },
        }
    }
    events = build_customer_support_debug_events_from_update(update, cursor)
    text = " ".join(str(item.get("text", "")) for item in events)

    assert any(item["type"] == "message_start" for item in events)
    # No fake tool_response/completed; recorded tools surfaced as runtime_summary fact only
    assert not any(item["type"] == "tool_response" for item in events)
    assert any(item.get("source") == "runtime_summary" for item in events)


def test_runtime_update_debug_events_follow_post_guard_state():
    cursor = DebugRuntimeCursor(started=True)
    update = {
        "check": {
            "check_result": {
                "has_answer": True,
                "links_ok": False,
                "post_guard_applied": True,
            }
        }
    }
    events = build_customer_support_debug_events_from_update(update, cursor)
    text = " ".join(str(item.get("text", "")) for item in events)

    assert any(item["type"] == "thinking" for item in events)
    assert "后置内容质检" in text
    assert "安全兜底" in text
    assert all(item.get("source") == "runtime_summary" for item in events if item["type"] == "thinking")


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


def test_no_sensitive_attachment_url_in_events():
    cursor = DebugRuntimeCursor()
    update = {
        "delegate": {
            "route": "knowledge",
            "attachments": [{"type": "image", "url": "https://b.oss.com/k?Signature=SECRET&Expires=1"}],
        }
    }
    events = build_customer_support_debug_events_from_update(update, cursor)
    blob = repr(events)
    assert "Signature=SECRET" not in blob
