from __future__ import annotations

from typing import Annotated, Any, Literal, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages


class CustomerCeshiV2State(TypedDict, total=False):
    messages: Annotated[list[AnyMessage], add_messages]
    session_id: str
    user_id: str
    source_channel: str
    agent_profile: str
    status: Literal["success", "degraded", "error"]
    phase: Literal["ingest", "orchestrate", "execute", "review", "finalize", "done"]
    step_count: int
    started_at_ms: int
    task_goal: str
    working_memory: dict[str, Any]
    media_assets: list[dict[str, Any]]
    observations: list[dict[str, Any]]
    claims: list[str]
    candidate_answer: str
    decision: dict[str, Any]
    evidence_review: dict[str, Any]
    generated_answer: str
    generated_tool_calls: list[str]
    tool_fingerprints: dict[str, int]
    media_call_count: int
    tool_call_count: int
    metrics: dict[str, Any]
    route_trace: dict[str, Any]
    degrade_reason: str
    dependency_error: dict[str, Any]
    confirmed_context: dict[str, Any]
    turn_diagnostics: dict[str, Any]
