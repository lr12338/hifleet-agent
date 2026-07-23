"""Typed, adapter-neutral contracts for Shared Skills V2."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


VALID_RISK_LEVELS = {"low", "medium", "high", "critical"}


@dataclass(frozen=True)
class ToolDescriptor:
    """The single business contract that both agent adapters consume."""

    name: str
    skill_id: str
    description: str
    input_schema: Mapping[str, Any]
    read_only: bool = True
    risk_level: str = "low"
    timeout_seconds: int = 20
    requires_confirmation: bool = False
    upstream_commit: str = ""
    skill_version: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("ToolDescriptor.name is required")
        if not self.skill_id:
            raise ValueError("ToolDescriptor.skill_id is required")
        if self.risk_level not in VALID_RISK_LEVELS:
            raise ValueError(f"Invalid risk level: {self.risk_level}")
        if not isinstance(self.input_schema, Mapping) or self.input_schema.get("type") != "object":
            raise ValueError(f"ToolDescriptor {self.name} must use an object input schema")
        if self.timeout_seconds <= 0:
            raise ValueError("ToolDescriptor.timeout_seconds must be positive")

    def chat_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": dict(self.input_schema),
            },
        }

    def responses_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "name": self.name,
            "description": self.description,
            "parameters": dict(self.input_schema),
        }


@dataclass(frozen=True)
class SkillManifest:
    schema_version: int
    skill_id: str
    skill_version: str
    prompt_file: str
    capabilities: tuple[Mapping[str, Any], ...]
    upstream_commit: str = ""
    upstream_repository: str = ""


@dataclass(frozen=True)
class SkillRuntimeBundle:
    """Profile-specific V2 material passed into an existing agent runtime."""

    profile_id: str
    mode: str
    tools: tuple[Any, ...]
    descriptors: tuple[ToolDescriptor, ...]
    prompt: str
    source_versions: Mapping[str, Mapping[str, str]]
