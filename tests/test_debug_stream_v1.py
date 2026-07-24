import asyncio
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import debug_events as de
from agents.debug_stream_v1 import is_debug_request, stream_customer_ceshi_v1, stream_customer_support_v1


def _parse_frames(blob: bytes) -> list[de.DebugEvent]:
    events: list[de.DebugEvent] = []
    for block in blob.decode("utf-8").split("\n\n"):
        data_line = None
        for line in block.split("\n"):
            if line.startswith("data:"):
                data_line = line[len("data:"):].strip()
        if data_line:
            payload = json.loads(data_line)
            events.append(de.DebugEvent(**payload))
    return events


class FakeGraph:
    def __init__(self, updates: list[tuple[str, dict[str, Any]]]):
        self._updates = updates

    async def astream(self, payload, config=None, context=None, stream_mode=None, **kwargs):
        for upd in self._updates:
            yield upd


def _run(coro):
    return asyncio.run(coro)


async def _collect(gen):
    out = []
    async for frame in gen:
        out.append(frame)
    return b"".join(out)


def test_customer_support_v1_stream_emits_real_answer_and_no_fake_tools():
    graph = FakeGraph(
        [
            ("updates", {"delegate": {"route": "knowledge", "task_type": "platform_knowledge", "route_trace": {"tool_call_sequence": ["smart_search"]}}}),
            ("updates", {"finalize": {"messages": [{"role": "assistant", "content": "绿点表示船位正常。"}]}}),
        ]
    )
    blob = _run(_collect(stream_customer_support_v1(graph, {"messages": []}, {}, None, "run_cs", session_id="s1", agent_profile="customer_support", model="m")))
    events = _parse_frames(blob)
    types = [e.type for e in events]
    assert types[0] == "run.started"
    assert "route.selected" in types
    assert "reasoning.summary" in types
    assert "answer.completed" in types
    assert types[-1] == "run.completed"
    # No filename special-casing; no fabricated tool events from node state
    text = " ".join(str(e.summary or "") for e in events)
    assert "安全水域浮标" not in text
    # reasoning summaries are runtime_summary
    assert all(e.source == "runtime_summary" for e in events if e.type == "reasoning.summary")
    agg = de.DebugAggregator()
    for e in events:
        agg.feed(e)
    agg.finalize()
    assert agg.summary()["valid"], agg.summary()


def test_customer_ceshi_v1_step_stream_emits_real_tool_pair_and_single_answer():
    graph = FakeGraph(
        [
            (
                "updates",
                {
                    "customer_ceshi_responses": {
                        "runtime_mode": "responses",
                        "requested_runtime_mode": "responses",
                        "effective_runtime": "responses",
                        "status": "success",
                        "generated_answer": "船位正常。",
                        "observations": [
                            {"evidence_id": "e-1", "tool_name": "local_kb_search", "result": {"hits": 2}, "status": "completed"},
                            {"evidence_id": "e-2", "tool_name": "inspect_media", "result": {"error": "timeout"}, "status": "failed"},
                        ],
                        "metrics": {"model_calls": 2, "tool_calls": 2},
                    }
                },
            )
        ]
    )
    blob = _run(_collect(stream_customer_ceshi_v1(graph, {"messages": []}, {}, None, "run_ceshi", session_id="s2", agent_profile="customer_ceshi", model="m")))
    events = _parse_frames(blob)
    types = [e.type for e in events]
    assert types[0] == "run.started"
    assert "phase.started" in types  # marked as step stream
    # two real tool observations -> 2 started, 1 completed + 1 failed, properly paired
    assert types.count("tool.started") == 2
    assert types.count("tool.completed") == 1
    assert types.count("tool.failed") == 1
    assert "answer.completed" in types
    # no fake token deltas
    assert "answer.delta" not in types
    assert types[-1] == "run.completed"
    agg = de.DebugAggregator()
    for e in events:
        agg.feed(e)
    agg.finalize()
    assert agg.summary()["valid"], agg.summary()
    # answer text recorded
    completed = [e for e in events if e.type == "answer.completed"][0]
    assert completed.data["answer"] == "船位正常。"


def test_customer_ceshi_v1_marks_fallback_honestly():
    graph = FakeGraph(
        [
            (
                "updates",
                {
                    "customer_ceshi_responses": {
                        "runtime_mode": "chat_function_calling",
                        "requested_runtime_mode": "responses",
                        "effective_runtime": "chat_function_calling",
                        "status": "success",
                        "generated_answer": "fallback answer",
                        "observations": [],
                        "metrics": {},
                    }
                },
            )
        ]
    )
    blob = _run(_collect(stream_customer_ceshi_v1(graph, {"messages": []}, {}, None, "run_fb", agent_profile="customer_ceshi", model="m")))
    events = _parse_frames(blob)
    route = [e for e in events if e.type == "route.selected"][0]
    assert route.data["requested_runtime"] == "responses"
    assert route.data["effective_runtime"] == "chat_function_calling"
    assert route.data["fallback_reason"]


def test_is_debug_request_requires_token_and_match(monkeypatch):
    monkeypatch.delenv("INTERNAL_DEBUG_TRACE_TOKEN", raising=False)
    assert is_debug_request({"x-internal-debug-trace": "anything"}) is False
    monkeypatch.setenv("INTERNAL_DEBUG_TRACE_TOKEN", "secret")
    assert is_debug_request({"x-internal-debug-trace": "secret"}) is True
    assert is_debug_request({"x-internal-debug-trace": "wrong"}) is False
    assert is_debug_request({}) is False
