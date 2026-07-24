import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import debug_events as de
from debug_events import DebugEvent, DebugEventType, EventRuntime, EventPhase


def _emitter(**kw):
    return de.DebugEmitter("run_test", agent_profile=kw.get("profile", "customer_ceshi"), runtime=kw.get("runtime", EventRuntime.RESPONSES), **{k: v for k, v in kw.items() if k not in ("profile", "runtime")})


def test_debug_event_schema_version_and_required_fields():
    e = _emitter()
    ev = e.run_started()
    assert ev.schema_version == "debug-event.v1"
    data = ev.to_sse_data()
    assert data["schema_version"] == "debug-event.v1"
    assert data["run_id"] == "run_test"
    assert data["type"] == "run.started"
    assert "seq" in data and "event_id" in data and "timestamp" in data


def test_seq_strictly_monotonic_within_run():
    e = _emitter()
    seqs = [e.answer_delta(str(i)).seq for i in range(5)]
    assert seqs == [0, 1, 2, 3, 4]
    agg = de.DebugAggregator()
    # rebuild events via emitter into aggregator
    e2 = _emitter()
    for i in range(5):
        agg.feed(e2.answer_delta("x"))
    agg.finalize()
    assert agg.summary()["seq_violations"] == []


def test_run_id_association_and_mismatch_detected():
    e = de.DebugEmitter("run_a")
    agg = de.DebugAggregator()
    agg.feed(e.run_started())
    other = DebugEvent(event_id="evt_x", seq=99, run_id="run_b", type=DebugEventType.ANSWER_DELTA)
    agg.feed(other)
    agg.finalize()
    assert agg.summary()["run_id_mismatches"] == ["run_b"]


def test_chat_adapter_emits_answer_delta_and_tool_started():
    e = _emitter(profile="customer_support", runtime=EventRuntime.CHAT)
    idx_map = {}
    evs = de.adapt_chat_chunk(e, {"choices": [{"delta": {"content": "Hello ", "tool_calls": [{"index": 0, "id": "call_1", "function": {"name": "local_kb_search", "arguments": "{\"q\":"}}]}}]}, tool_call_index_to_id=idx_map)
    types = [ev.type for ev in evs]
    assert "answer.delta" in types
    assert "tool.started" in types
    assert idx_map[0] == "call_1"


def test_chat_adapter_provider_reasoning_only_when_present():
    e = _emitter(runtime=EventRuntime.CHAT)
    evs = de.adapt_chat_chunk(e, {"choices": [{"delta": {"reasoning_content": "thinking..."}}]})
    assert evs and evs[0].type == "reasoning.summary" and evs[0].source == "provider"
    # No reasoning field -> no reasoning event
    evs2 = de.adapt_chat_chunk(e, {"choices": [{"delta": {"content": "x"}}]})
    assert all(ev.type != "reasoning.summary" for ev in evs2)


def test_responses_adapter_native_events_mapped():
    e = _emitter()
    events = [
        {"type": "response.created"},
        {"type": "response.output_text.delta", "delta": "Hi"},
        {"type": "response.output_item.added", "item": {"type": "function_call", "call_id": "c1", "name": "web_search"}},
        {"type": "response.function_call_arguments.delta", "item_id": "c1", "delta": "{\"q\""},
        {"type": "response.output_item.done", "item": {"type": "function_call", "call_id": "c1", "name": "web_search", "arguments": "{}"}},
        {"type": "response.completed", "response": {"output_text": "Hi there"}},
    ]
    all_events = []
    for ev in events:
        all_events += de.adapt_responses_event(e, ev)
    types = [ev.type for ev in all_events]
    assert "run.started" in types
    assert "answer.delta" in types
    assert "tool.started" in types
    assert "tool.arguments.delta" in types
    assert "tool.completed" in types
    assert "answer.completed" in types
    assert "run.completed" in types
    # answer.completed carries full text
    completed = [ev for ev in all_events if ev.type == "answer.completed"][0]
    assert completed.data["answer"] == "Hi there"


def test_responses_adapter_unknown_event_downgrades_to_raw():
    e = _emitter()
    evs = de.adapt_responses_event(e, {"type": "response.some_future_event", "foo": "bar"})
    assert len(evs) == 1
    assert evs[0].type == "raw_provider_event"
    assert evs[0].visibility == "internal_debug"
    # Must not be interpreted as an answer
    assert evs[0].type != "answer.delta"


def test_responses_step_mode_emits_real_tool_pair_and_single_answer():
    e = _emitter()
    evs = [e.run_started()]
    evs.append(de.responses_step_tool_started(e, name="local_kb_search", call_id="c1", arguments={"query": "航线"}))
    evs.append(de.responses_step_tool_finished(e, name="local_kb_search", call_id="c1", result={"hits": 2}))
    evs += de.responses_step_answer(e, full_text="最终回答", observations=[{"evidence_id": "e-1"}])
    evs.append(e.run_completed())
    agg = de.DebugAggregator()
    for ev in evs:
        agg.feed(ev)
    agg.finalize()
    s = agg.summary()
    assert s["tool_started"] == 1 and s["tool_completed"] == 1
    assert s["unpaired_tool_starts"] == [] and s["unpaired_tool_completes"] == []
    assert agg.answer_text == ""  # step mode: no answer.delta, only answer.completed
    assert s["is_complete"]


def test_responses_fallback_marks_effective_runtime_honestly():
    e = _emitter()
    ev = de.responses_step_fallback(e, requested_runtime="responses", effective_runtime="chat_function_calling", fallback_reason="responses_unavailable")
    assert ev.type == "route.selected"
    assert ev.data["effective_runtime"] == "chat_function_calling"
    assert ev.data["requested_runtime"] == "responses"
    assert "fallback_reason" in ev.data


def test_tool_call_pairing_violations_detected():
    e = _emitter()
    agg = de.DebugAggregator()
    agg.feed(e.run_started())
    agg.feed(e.tool_started(name="x", call_id="c1"))  # never completed
    agg.feed(e.tool_completed(call_id="c2", result_summary={}))  # no started
    agg.feed(e.run_completed())
    agg.finalize()
    s = agg.summary()
    assert "c1" in s["unpaired_tool_starts"]
    assert "c2" in s["unpaired_tool_completes"]


def test_event_data_redaction_drops_secrets():
    e = _emitter()
    ev = e.run_started(data={"api_key": "sk-secret", "authorization": "Bearer abc", "text": "ok"})
    assert ev.data["api_key"] == "[redacted]"
    assert ev.data["authorization"] == "[redacted]"
    assert ev.data["text"] == "ok"


def test_attachment_signed_url_redaction():
    url = "https://bucket.oss.aliyuncs.com/admin_uploads/2026/01/01/abc_file.png?Expires=123&Signature=SECRET&x-amz-credential=AKID&file=x"
    sanitized = de.sanitize_url(url)
    assert "Signature=***" in sanitized
    assert "x-amz-credential=***" in sanitized
    assert "SECRET" not in sanitized
    assert "file=x" in sanitized  # non-sensitive param preserved
    assert sanitized.startswith("https://bucket.oss.aliyuncs.com/admin_uploads/")


def test_aggregator_incomplete_stream_detection():
    e = _emitter()
    agg = de.DebugAggregator()
    agg.feed(e.run_started())
    agg.feed(e.answer_delta("partial"))
    agg.finalize()
    s = agg.summary()
    assert s["is_incomplete_stream"] is True
    assert s["is_complete"] is False
    assert s["valid"] is False


def test_terminal_guard_blocks_events_after_run_completed():
    e = _emitter()
    e.run_completed()
    import pytest
    with pytest.raises(RuntimeError):
        e.answer_delta("x")
    # heartbeat still allowed
    e.heartbeat()


def test_langgraph_adapter_no_filename_special_casing_or_fake_tools():
    e = de.DebugEmitter("r", agent_profile="customer_support", runtime=EventRuntime.LANGGRAPH)
    cursor = de.SupportCursor()
    # delegate update with an attachment named 01_query.png — must NOT infer "安全水域浮标"
    update = {
        "delegate": {
            "route": "knowledge",
            "task_type": "platform_knowledge",
            "attachments": [{"type": "image", "filename": "01_query.png", "url": "https://b.oss.com/k?Signature=s"}],
            "route_trace": {"tool_call_sequence": ["smart_search"]},
        }
    }
    evs = []
    for node, state in update.items():
        evs += de.adapt_customer_support_update(e, node, state, cursor)
    types = [ev.type for ev in evs]
    text = " ".join(str(ev.summary or "") for ev in evs)
    assert "安全水域浮标" not in text
    assert "tool.started" not in types  # node updates don't emit fake tool exec
    assert "tool.completed" not in types
    assert "route.selected" in types
    # reasoning summary must be marked runtime_summary, not provider
    rs = [ev for ev in evs if ev.type == "reasoning.summary"]
    assert rs and rs[0].source == "runtime_summary"
    # attachment URL in evidence summary must be redacted if present
    for ev in evs:
        assert "Signature=s" not in str(ev.data)


def test_langgraph_adapter_emits_real_answer_from_finalize():
    e = de.DebugEmitter("r", agent_profile="customer_support", runtime=EventRuntime.LANGGRAPH)
    cursor = de.SupportCursor(started=True)
    evs = de.adapt_customer_support_update(
        e, "finalize", {"messages": [{"role": "assistant", "content": "HiFleet 绿点表示船位正常。"}]}, cursor
    )
    types = [ev.type for ev in evs]
    assert "answer.started" in types and "answer.completed" in types
    assert "绿点" in evs[-1].data["answer"]


def test_duplicate_event_id_detected():
    e = _emitter()
    agg = de.DebugAggregator()
    ev = e.run_started()
    agg.feed(ev)
    agg.feed(ev)  # same object reused -> same event_id
    agg.finalize()
    assert agg.summary()["duplicate_ids"] == [ev.event_id]
