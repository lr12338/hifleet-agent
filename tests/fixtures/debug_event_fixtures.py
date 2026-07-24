"""Raw provider event fixtures for adapter and SSE parser tests.

These are anonymized, shape-accurate samples (no real keys/tokens) used to prove
the Chat and Responses adapters and the frontend SSE parser without hitting a
live provider.
"""
from __future__ import annotations

# --- Chat Completions streaming chunks (customer_support path) ---
CHAT_CHUNKS_TEXT_ONLY = [
    {"choices": [{"delta": {"content": "HiFleet "}, "index": 0}]},
    {"choices": [{"delta": {"content": "绿点表示船位正常。"}, "index": 0}]},
    {"choices": [{"delta": {}, "index": 0, "finish_reason": "stop"}]},
]

CHAT_CHUNKS_WITH_TOOL_CALL = [
    {"choices": [{"delta": {"role": "assistant"}, "index": 0}]},
    {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "call_abc", "function": {"name": "local_kb_search", "arguments": "{\"query\":"}}]}, "index": 0}]},
    {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": " \"航线\"}"}}]}, "index": 0}]},
    {"choices": [{"delta": {}, "index": 0, "finish_reason": "tool_calls"}]},
]

CHAT_CHUNKS_WITH_REASONING = [
    {"choices": [{"delta": {"reasoning_content": "先检索本地知识库。"}, "index": 0}]},
    {"choices": [{"delta": {"content": "回答。"}, "index": 0}]},
]

# --- Responses API streaming events (native, when provider supports it) ---
RESPONSES_NATIVE_STREAM = [
    {"type": "response.created", "response": {"id": "resp_1"}},
    {"type": "response.in_progress"},
    {"type": "response.reasoning_summary_text.delta", "delta": "需要检索。"},
    {"type": "response.output_item.added", "item": {"type": "function_call", "call_id": "call_1", "name": "web_search"}},
    {"type": "response.function_call_arguments.delta", "item_id": "call_1", "delta": "{\"q\":"},
    {"type": "response.function_call_arguments.delta", "item_id": "call_1", "delta": " \"船位\"}"},
    {"type": "response.output_item.done", "item": {"type": "function_call", "call_id": "call_1", "name": "web_search", "arguments": "{\"q\":\"船位\"}"}},
    {"type": "response.output_text.delta", "delta": "船位"},
    {"type": "response.output_text.delta", "delta": "正常。"},
    {"type": "response.completed", "response": {"id": "resp_1", "output_text": "船位正常。"}},
]

RESPONSES_FAILED = [
    {"type": "response.created"},
    {"type": "response.failed", "error": {"message": "rate limited"}},
]

RESPONSES_UNKNOWN_FUTURE = [
    {"type": "response.output_audio.delta", "delta": "binary"},
]

# --- Synchronous customer_ceshi step-mode observations (current reality) ---
RESPONSES_STEP_OBSERVATIONS = [
    {"tool_name": "local_kb_search", "call_id": "call_step_1", "arguments": {"query": "航线"}, "result": {"hits": 2}, "status": "completed"},
    {"tool_name": "inspect_media", "call_id": "call_step_2", "arguments": {"asset_id": "a1"}, "result": {"error": "timeout"}, "status": "failed"},
]

# --- Raw SSE byte frames for frontend parser tests (mix of delimiters) ---
def sse_frame(event: str, data: str, *, crlf: bool = False) -> bytes:
    nl = "\r\n" if crlf else "\n"
    return f"event: {event}{nl}data: {data}{nl}{nl}".encode("utf-8")


SSE_STREAM_LF = b"".join(
    [
        b"event: debug\ndata: " + b'{"type":"run.started","run_id":"r"}' + b"\n\n",
        b"event: debug\ndata: " + b'{"type":"answer.delta","data":{"delta":"Hi"}}' + b"\n\n",
        b"event: debug\ndata: " + b'{"type":"run.completed"}' + b"\n\n",
    ]
)

SSE_STREAM_CRLF = b"".join(
    [
        b"event: debug\r\ndata: " + b'{"type":"run.started","run_id":"r"}' + b"\r\n\r\n",
        b"event: debug\r\ndata: " + b'{"type":"answer.delta","data":{"delta":"Hi"}}' + b"\r\n\r\n",
        b"event: debug\r\ndata: " + b'{"type":"run.completed"}' + b"\r\n\r\n",
    ]
)

# Multiline data + comment heartbeat + id lines
SSE_STREAM_MULTILINE = (
    b": heartbeat comment\n"
    b"id: 1\n"
    b"event: debug\n"
    b'data: {"type":"run.started",\n'
    b'data: "run_id":"r"}\n\n'
    b"id: 2\n"
    b"event: debug\n"
    b'data: {"type":"answer.delta","data":{"delta":"x"}}\n\n'
)

# UTF-8 chinese split across two chunks (first chunk cuts a multibyte char)
_UTF8 = "数据".encode("utf-8")  # 6 bytes
SSE_UTF8_SPLIT = [
    b'event: debug\ndata: {"type":"answer.delta","data":{"delta":"' + _UTF8[:3],
    _UTF8[3:] + b'"}}\n\n',
]
