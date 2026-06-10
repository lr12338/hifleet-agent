from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class ApiCallCreate(BaseModel):
    run_id: str
    session_id: str | None = None
    user_id: str | None = None
    source_channel: str | None = None
    route: str
    intent_hint: str | None = None
    request_json: dict[str, Any] | None = None
    response_json: dict[str, Any] | None = None
    http_status_code: int | None = None
    status: str
    latency_ms: int = 0


class ToolInvocationCreate(BaseModel):
    run_id: str
    tool_name: str
    tool_args: dict[str, Any] | None = None
    tool_result: dict[str, Any] | None = None
    status: str
    code: str | None = None
    message: str | None = None
    retriable: bool = False
    latency_ms: int = 0
    source: str | None = None
    layer_trace: dict[str, Any] | None = None


class AgentErrorCreate(BaseModel):
    run_id: str
    route: str | None = None
    error_code: str
    error_message: str | None = None
    stack_trace: str | None = None
    error_category: str | None = None
    node_name: str | None = None


class LogListFilters(BaseModel):
    start_time: datetime | None = None
    end_time: datetime | None = None
    session_id: str | None = None
    user_id: str | None = None
    source_channel: str | None = None
    route: str | None = None
    status: str | None = None
    keyword: str | None = None
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=200)
