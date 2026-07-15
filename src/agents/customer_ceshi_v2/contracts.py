from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class MediaAsset(BaseModel):
    asset_id: str
    kind: Literal["image", "audio", "video", "file"]
    url: str = ""
    filename: str = ""
    sha256: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class InspectMediaRequest(BaseModel):
    asset_id: str
    objective: str
    questions: list[str] = Field(default_factory=list)
    mode: Literal["broad_scan", "ocr", "entity_extract", "field_extract", "visual_detail", "timeline", "transcription", "targeted_verify"] = "broad_scan"
    expected_fields: list[str] = Field(default_factory=list)
    region: dict[str, Any] | None = None
    time_range: dict[str, Any] | None = None


class PerceivedField(BaseModel):
    name: str
    value: Any = None
    raw_text: str = ""
    status: Literal["observed", "inferred", "uncertain", "placeholder", "conflict"] = "uncertain"
    confidence: Literal["high", "medium", "low"] = "low"
    source_ref: str = ""
    asset_id: str
    region: dict[str, Any] | None = None
    time_range: dict[str, Any] | None = None


class PerceptionPacket(BaseModel):
    asset_id: str
    media_type: str
    model: str
    schema_version: str = "1.0"
    requested_objective: str
    requested_questions: list[str] = Field(default_factory=list)
    transcript: list[dict[str, Any]] = Field(default_factory=list)
    ocr_blocks: list[dict[str, Any]] = Field(default_factory=list)
    visual_objects: list[dict[str, Any]] = Field(default_factory=list)
    entities: list[dict[str, Any]] = Field(default_factory=list)
    fields: list[PerceivedField] = Field(default_factory=list)
    events: list[dict[str, Any]] = Field(default_factory=list)
    factual_summary: str = ""
    visual_features: list[str] = Field(default_factory=list)
    suspected_symbol: str = ""
    unresolved_questions: list[str] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    overall_confidence: Literal["high", "medium", "low"] = "low"
    evidence_refs: list[dict[str, Any]] = Field(default_factory=list)


class Claim(BaseModel):
    id: str
    text: str
    importance: Literal["required", "supporting"] = "supporting"
    status: Literal["unverified", "supported", "unsupported", "conflict"] = "unverified"


class WriteProposal(BaseModel):
    operation: Literal["ship_position", "ship_static_info"]
    fields: dict[str, Any] = Field(default_factory=dict)
    field_sources: dict[str, str] = Field(default_factory=dict)


class ToolCall(BaseModel):
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class AgentDecision(BaseModel):
    action: Literal["call_tools", "ask_user", "propose_write", "finish"]
    tool_calls: list[ToolCall] = Field(default_factory=list)
    asset_ids: list[str] = Field(default_factory=list)
    perception_goal: str = ""
    media_requests: list[InspectMediaRequest] = Field(default_factory=list)
    question: str = ""
    answer_draft: str = ""
    claims: list[str] = Field(default_factory=list)
    write_proposal: WriteProposal | None = None


class Observation(BaseModel):
    status: Literal["success", "partial", "not_found", "invalid_input", "forbidden", "temporary_error", "timeout", "upstream_error"]
    capability: str
    facts: list[str] = Field(default_factory=list)
    data: dict[str, Any] = Field(default_factory=dict)
    sources: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    retry_allowed: bool = False
    suggested_fix: str = ""


class EvidenceReview(BaseModel):
    ready: bool = False
    supported_claims: list[str] = Field(default_factory=list)
    unsupported_claims: list[str] = Field(default_factory=list)
    missing_required_facts: list[str] = Field(default_factory=list)
    recommended_action: Literal["search_more", "inspect_media_again", "remove_claim", "qualify_claim", "ask_user", "finish"] = "finish"
    repaired_answer: str = ""
