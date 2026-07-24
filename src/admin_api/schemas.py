from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class LogListQuery(BaseModel):
    start_time: datetime | None = None
    end_time: datetime | None = None
    session_id: str | None = None
    user_id: str | None = None
    source_channel: str | None = None
    agent_profile: str | None = None
    route: str | None = None
    status: str | None = None
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=200)
    keyword: str | None = None


class AdminTestRunRequest(BaseModel):
    endpoint: Literal['/run', '/stream_run'] = '/run'
    payload: dict[str, Any]
    run_id: str | None = None
    target_agent_url: str | None = None
    timeout_s: int = Field(default=180, ge=1, le=900)
    stream: bool = False


class ArkAttachment(BaseModel):
    type: Literal['image', 'audio', 'video']
    url: str
    name: str | None = None
    mime_type: str | None = None


class ArkChatRequest(BaseModel):
    model: str | None = None
    thinking: Literal['enabled', 'disabled', 'auto'] | None = None
    reasoning_effort: Literal['minimal', 'low', 'medium', 'high'] | None = None
    text: str | None = None
    attachments: list[ArkAttachment] = Field(default_factory=list)
    session_id: str | None = None
    user_id: str | None = None
    source_channel: str | None = None
    stream: bool = True


class LLMConfigRequest(BaseModel):
    text_model: str = Field(min_length=1)
    multimodal_model: str = Field(min_length=1)
    thinking_type: Literal['enabled', 'disabled'] = 'enabled'
    reasoning_effort: Literal['minimal', 'low', 'medium', 'high'] = 'medium'
    text_thinking_type: Literal['enabled', 'disabled'] | None = None
    multimodal_thinking_type: Literal['enabled', 'disabled'] | None = None
    customer_support_json_thinking_type: Literal['enabled', 'disabled'] | None = None
    text_model_base_url_env: str | None = None
    multimodal_model_base_url_env: str | None = None
    json_model_base_url_env: str | None = None


class ChatDebugSessionSaveRequest(BaseModel):
    session_key: str
    title: str
    status: Literal['running', 'ended']
    meta_session_id: str
    user_id: str
    source_channel: str
    model: str
    payload: dict[str, Any]
    agent_profile: Literal['customer_support', 'customer_ceshi'] | None = None
    endpoint: Literal['/run', '/stream_run'] | None = None
    response_mode: Literal['compact', 'full'] | None = None


class ChatDebugSessionListQuery(BaseModel):
    limit: int = Field(default=20, ge=1, le=100)


class SessionListQuery(BaseModel):
    start_time: datetime | None = None
    end_time: datetime | None = None
    user_id: str | None = None
    source_channel: str | None = None
    agent_profile: str | None = None
    status: str | None = None
    keyword: str | None = None
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)


class DashboardSummaryQuery(BaseModel):
    start_time: datetime | None = None
    end_time: datetime | None = None
