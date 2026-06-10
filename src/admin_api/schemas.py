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
    endpoint: Literal["/run", "/stream_run"] = "/run"
    payload: dict[str, Any]
    target_agent_url: str | None = None
    timeout_s: int = Field(default=180, ge=1, le=900)
    stream: bool = False


class ArkAttachment(BaseModel):
    type: Literal["image", "audio", "video"]
    url: str
    name: str | None = None
    mime_type: str | None = None


class ArkChatRequest(BaseModel):
    model: str
    thinking: Literal["enabled", "disabled", "auto"] = "enabled"
    text: str | None = None
    attachments: list[ArkAttachment] = Field(default_factory=list)
    session_id: str | None = None
    user_id: str | None = None
    source_channel: str | None = None
    stream: bool = True


class ChatDebugSessionSaveRequest(BaseModel):
    session_key: str
    title: str
    status: Literal["running", "ended"]
    meta_session_id: str
    user_id: str
    source_channel: str
    model: str
    payload: dict[str, Any]


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
