"""DebugEvent V1 streaming for the dialogue test workbench.

Emits a normalized V1 SSE stream (``event: debug``) for both profiles, gated by a
server-side internal debug token so external callers cannot enable it. The normal
customer-facing stream is untouched.

- customer_support: LangGraph ``updates`` + a real tool-call callback handler.
- customer_ceshi: the synchronous Responses tool loop runs in one node; we emit
  real tool.started/completed from the node's actual ``observations`` and a single
  answer.completed (step stream, no fake token deltas).
"""
from __future__ import annotations

import asyncio
import os
from typing import Any, AsyncGenerator

from debug_events import (
    DebugEmitter,
    EventPhase,
    EventRuntime,
    SupportCursor,
    ToolCallCallbackHandler,
    adapt_customer_support_update,
    encode_sse,
    finalize_support_run,
)

INTERNAL_DEBUG_HEADER = "x-internal-debug-trace"


def debug_token() -> str:
    return os.getenv("INTERNAL_DEBUG_TRACE_TOKEN", "").strip()


def is_debug_request(headers: dict[str, Any]) -> bool:
    """True only when the internal debug header carries the server-side token."""
    token = debug_token()
    if not token:
        return False
    return str(headers.get(INTERNAL_DEBUG_HEADER) or headers.get(INTERNAL_DEBUG_HEADER.title()) or "") == token


def _runtime_for(mode: str) -> EventRuntime:
    if mode in {"responses", "multimodal_responses"}:
        return EventRuntime.RESPONSES
    if mode in {"chat_function_calling", "chat"}:
        return EventRuntime.CHAT_FUNCTION_CALLING
    return EventRuntime.LANGGRAPH


async def stream_customer_support_v1(
    graph: Any,
    payload: dict[str, Any],
    run_config: dict[str, Any],
    ctx: Any,
    run_id: str,
    *,
    session_id: str = "",
    agent_profile: str = "customer_support",
    model: str | None = None,
) -> AsyncGenerator[bytes, None]:
    emitter = DebugEmitter(
        run_id,
        session_id=session_id or None,
        agent_profile=agent_profile,
        endpoint="/stream_run",
        runtime=EventRuntime.LANGGRAPH,
        model=model,
    )
    cursor = SupportCursor()
    handler = ToolCallCallbackHandler(emitter)
    rc = dict(run_config or {})
    rc["callbacks"] = [handler]
    yield encode_sse(emitter.run_started(summary="customer_support 运行开始"))
    try:
        async for chunk in graph.astream(payload, config=rc, context=ctx, stream_mode=["updates"]):
            if not (isinstance(chunk, tuple) and len(chunk) == 2):
                continue
            mode, data = chunk
            if mode != "updates" or not isinstance(data, dict):
                continue
            for node_name, state in data.items():
                if not isinstance(state, dict):
                    continue
                for ev in adapt_customer_support_update(emitter, node_name, state, cursor):
                    yield encode_sse(ev)
        for ev in finalize_support_run(emitter, cursor, status="completed"):
            yield encode_sse(ev)
    except asyncio.CancelledError:
        for ev in finalize_support_run(emitter, cursor, status="cancelled"):
            yield encode_sse(ev)
        raise
    except Exception as exc:  # noqa: BLE001
        for ev in finalize_support_run(emitter, cursor, status="failed", error=str(exc)):
            yield encode_sse(ev)


async def stream_customer_ceshi_v1(
    graph: Any,
    payload: dict[str, Any],
    run_config: dict[str, Any],
    ctx: Any,
    run_id: str,
    *,
    session_id: str = "",
    agent_profile: str = "customer_ceshi",
    model: str | None = None,
) -> AsyncGenerator[bytes, None]:
    emitter = DebugEmitter(
        run_id,
        session_id=session_id or None,
        agent_profile=agent_profile,
        endpoint="/stream_run",
        runtime=EventRuntime.RESPONSES,
        model=model,
    )
    yield encode_sse(emitter.run_started(summary="customer_ceshi 运行开始"))
    yield encode_sse(emitter.phase_started(EventPhase.UNDERSTANDING, summary="步骤流：Provider 未提供 Token 增量"))
    try:
        terminal_emitted = False
        async for chunk in graph.astream(payload, config=run_config, context=ctx, stream_mode=["updates"]):
            if not (isinstance(chunk, tuple) and len(chunk) == 2):
                continue
            mode, data = chunk
            if mode != "updates" or not isinstance(data, dict):
                continue
            for _node_name, state in data.items():
                if not isinstance(state, dict):
                    continue
                requested = str(state.get("requested_runtime_mode") or state.get("runtime_mode") or "")
                effective = str(state.get("effective_runtime") or state.get("runtime_mode") or "")
                runtime = _runtime_for(effective or requested)
                fallback_reason = None
                if requested and effective and requested != effective:
                    fallback_reason = f"{requested} 不可用，回退到 {effective}"
                    yield encode_sse(
                        emitter.route_selected(
                            route="fallback" if fallback_reason else (effective or "responses"),
                            summary=fallback_reason or f"运行时：{effective}",
                            data={"requested_runtime": requested, "effective_runtime": effective, "fallback_reason": fallback_reason},
                        )
                    )
                else:
                    yield encode_sse(emitter.route_selected(route=effective or "responses", summary=f"运行时：{effective or 'responses'}"))
                # Real tool events from actual observations (post-loop, but real).
                observations = state.get("observations") or []
                if isinstance(observations, list):
                    for obs in observations:
                        if not isinstance(obs, dict):
                            continue
                        name = str(obs.get("tool_name") or obs.get("capability") or "unknown")
                        call_id = str(obs.get("evidence_id") or f"call_{obs.get('tool_name')}_{len(observations)}")
                        status = str(obs.get("status") or "completed")
                        try:
                            yield encode_sse(emitter.tool_started(name=name, call_id=call_id, arguments_summary={}))
                        except ValueError:
                            pass
                        if status == "failed":
                            yield encode_sse(emitter.tool_failed(call_id=call_id, error=str(obs.get("error") or "tool failed")))
                        else:
                            yield encode_sse(emitter.tool_completed(call_id=call_id, result_summary={"tool_name": name, "result": obs.get("result"), "status": status}, status=status))
                answer = state.get("generated_answer")
                if isinstance(answer, str) and answer:
                    yield encode_sse(emitter.answer_started())
                    yield encode_sse(emitter.answer_completed(full_text=answer))
                status = str(state.get("status") or "success")
                metrics = state.get("metrics") if isinstance(state.get("metrics"), dict) else {}
                if status in {"degraded", "failed"}:
                    yield encode_sse(emitter.run_failed(error=state.get("fallback_reason") or "degraded"))
                else:
                    yield encode_sse(emitter.run_completed(summary="customer_ceshi 运行完成", data={"provider_metrics": metrics}))
                terminal_emitted = True
        if not terminal_emitted:
            yield encode_sse(emitter.run_failed(error="未收到运行结果"))
    except asyncio.CancelledError:
        if not emitter.is_terminal:
            yield encode_sse(emitter.run_cancelled())
        raise
    except Exception as exc:  # noqa: BLE001
        if not emitter.is_terminal:
            yield encode_sse(emitter.run_failed(error=str(exc)))


async def stream_debug_v1(
    profile: str,
    graph: Any,
    payload: dict[str, Any],
    run_config: dict[str, Any],
    ctx: Any,
    run_id: str,
    *,
    session_id: str = "",
    model: str | None = None,
) -> AsyncGenerator[bytes, None]:
    if profile == "customer_support":
        async for frame in stream_customer_support_v1(graph, payload, run_config, ctx, run_id, session_id=session_id, agent_profile=profile, model=model):
            yield frame
    else:
        async for frame in stream_customer_ceshi_v1(graph, payload, run_config, ctx, run_id, session_id=session_id, agent_profile=profile, model=model):
            yield frame


__all__ = ["INTERNAL_DEBUG_HEADER", "debug_token", "is_debug_request", "stream_debug_v1", "stream_customer_support_v1", "stream_customer_ceshi_v1"]
