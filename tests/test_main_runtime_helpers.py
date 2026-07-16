import threading
import time

import main


def test_compact_run_response_excludes_internal_state():
    response = main._compact_run_response(
        {
            "status": "degraded",
            "run_id": "run-1",
            "generated_answer": "请稍后重试",
            "working_memory": {"private": "value"},
            "messages": [{"content": "private"}],
            "metrics": {"elapsed_ms": 12},
            "dependency_error": {"code": "model_timeout", "retryable": True},
            "route_trace": {"sources": ["source-1"], "turn_diagnostics": {"inherited_entity": True, "current_media_count": 1, "degrade_stage": "media"}},
        },
        session_id="session-1",
        user_id="user-1",
        source_channel="wechat_cs",
        agent_profile="customer_ceshi",
    )

    assert response["answer"] == "请稍后重试"
    assert response["error"]["code"] == "model_timeout"
    assert response["context"] == {"inherited_entity": True, "current_media_count": 1, "degrade_stage": "media"}
    assert "working_memory" not in response
    assert "messages" not in response


def test_customer_support_run_result_only_returns_current_turn_messages():
    result = main._sanitize_customer_support_run_result(
        {
            "generated_answer": "客服电话是 400-963-6899。",
            "messages": [
                {"type": "system", "content": "请用中文回复。"},
                {"type": "human", "content": "旧问题：查船位"},
                {"type": "ai", "content": "旧船位结果"},
                {"type": "human", "content": "你们联系电话是"},
                {"type": "ai", "content": "错误旧答复"},
            ],
            "working_messages": [{"type": "human", "content": "旧问题：查船位"}],
            "pending_update_state": {"status": "executed_success"},
            "ship_update_draft": {"mmsi": "123456789"},
            "route_trace": {
                "route": "knowledge",
                "task_type": "platform_knowledge",
                "tool_call_sequence": ["local_kb_search", "web_search"],
                "evidence_items": [{"url": "https://www.hifleet.com/helpcenter/?i18n=zh", "snippet": "内部摘要"}],
                "final_response": {"reference_urls": ["https://www.hifleet.com/helpcenter/?i18n=zh"]},
                "reasoning_trace": {"private": "must not return"},
            },
        },
        "customer_support",
    )

    assert result["messages"] == [
        {"type": "human", "content": "你们联系电话是"},
        {"type": "ai", "content": "客服电话是 400-963-6899。"},
    ]
    assert "working_messages" not in result
    assert "pending_update_state" not in result
    assert "ship_update_draft" not in result
    assert result["route_trace"] == {
        "route": "knowledge",
        "task_type": "platform_knowledge",
        "current_turn": {
            "tool_call_count": 2,
            "evidence_count": 1,
            "reference_urls": ["https://www.hifleet.com/helpcenter/?i18n=zh"],
        },
    }


def test_request_llm_route_accepts_nested_external_route(monkeypatch):
    monkeypatch.setattr(main, "load_llm_config", lambda: {"config": {"text_model": "default-text"}})
    monkeypatch.setattr(
        main,
        "resolve_model_selection",
        lambda cfg, **kwargs: {
            "model": kwargs["requested_model"],
            "thinking_type": kwargs["requested_thinking"],
            "reasoning_effort": kwargs["requested_reasoning_effort"],
        },
    )

    route = main._resolve_request_llm_route(
        {
            "messages": [{"role": "user", "content": "你好"}],
            "llm_route": {"model": "external-model", "thinking_type": "disabled", "reasoning_effort": "low"},
        }
    )

    assert route == {"model": "external-model", "thinking_type": "disabled", "reasoning_effort": "low"}


def test_cozeloop_flush_is_scheduled_without_blocking(monkeypatch):
    completed = threading.Event()

    def slow_flush():
        time.sleep(0.05)
        completed.set()

    monkeypatch.setattr(main.cozeloop, "flush", slow_flush)
    monkeypatch.setattr(main, "_COZELOOP_FLUSH_IN_FLIGHT", False)
    monkeypatch.setattr(main, "_COZELOOP_NEXT_FLUSH_AT", 0.0)

    started = time.perf_counter()
    main.schedule_cozeloop_flush()
    elapsed = time.perf_counter() - started

    assert elapsed < 0.03
    assert completed.wait(timeout=1)
