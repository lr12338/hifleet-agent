"""Agent profile resolution and prompt/tool policy configuration."""
from __future__ import annotations

import json
import os
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List


PROFILE_HEADER = "x-agent-profile"
DEFAULT_PROFILE_ID = "customer_support"
_CURRENT_PROFILE_ID: ContextVar[str] = ContextVar("hifleet_agent_profile", default="")


def set_current_agent_profile(profile_id: str) -> None:
    config = load_profiles_config()
    profiles = config.get("profiles", {}) or {}
    default_profile = config.get("default_profile") or DEFAULT_PROFILE_ID
    _CURRENT_PROFILE_ID.set(_canonicalize_profile_id(profile_id, profiles, default_profile))


def get_current_agent_profile_id() -> str:
    return _CURRENT_PROFILE_ID.get().strip()


@dataclass(frozen=True)
class AgentProfile:
    profile_id: str
    description: str = ""
    aliases: List[str] = field(default_factory=list)
    source_channels: List[str] = field(default_factory=list)
    skills: List[str] = field(default_factory=list)
    disabled_tools: List[str] = field(default_factory=list)
    prompt_file: str = ""
    max_iterations: int = 6
    requires_auth: bool = False
    sandbox_enabled: bool = False
    tool_policy: Dict[str, Any] = field(default_factory=dict)


def _workspace_path() -> Path:
    return Path(os.getenv("COZE_WORKSPACE_PATH", Path(__file__).resolve().parents[2])).resolve()


def _profiles_config_path() -> Path:
    return _workspace_path() / "config" / "agent_profiles.json"


def load_profiles_config() -> Dict[str, Any]:
    path = _profiles_config_path()
    if not path.exists():
        return {"default_profile": DEFAULT_PROFILE_ID, "profiles": {}}
    return json.loads(path.read_text(encoding="utf-8"))


def _canonicalize_profile_id(
    profile_id: str,
    profiles: Dict[str, Any] | None = None,
    default_profile: str = DEFAULT_PROFILE_ID,
) -> str:
    normalized = (profile_id or "").strip()
    if not normalized:
        return default_profile

    profile_map = profiles or (load_profiles_config().get("profiles", {}) or {})
    if normalized in profile_map:
        return normalized

    for canonical_id, data in profile_map.items():
        aliases = data.get("aliases", []) if isinstance(data, dict) else []
        if normalized in {str(alias).strip() for alias in aliases if str(alias).strip()}:
            return canonical_id

    return default_profile


def get_profile(profile_id: str = "") -> AgentProfile:
    config = load_profiles_config()
    profiles = config.get("profiles", {}) or {}
    default_profile = config.get("default_profile") or DEFAULT_PROFILE_ID
    selected = _canonicalize_profile_id(profile_id, profiles, default_profile)
    data = profiles.get(selected, {})
    return AgentProfile(
        profile_id=selected,
        description=str(data.get("description", "")),
        aliases=list(data.get("aliases", []) or []),
        source_channels=list(data.get("source_channels", []) or []),
        skills=list(data.get("skills", []) or []),
        disabled_tools=list(data.get("disabled_tools", []) or []),
        prompt_file=str(data.get("prompt_file", "")),
        max_iterations=int(data.get("max_iterations", 6) or 6),
        requires_auth=bool(data.get("requires_auth", False)),
        sandbox_enabled=bool(data.get("sandbox_enabled", False)),
        tool_policy=dict(data.get("tool_policy", {}) or {}),
    )


def resolve_profile_id(
    *,
    source_channel: str = "",
    requested_profile: str = "",
    headers: Dict[str, Any] | None = None,
) -> str:
    config = load_profiles_config()
    profiles = config.get("profiles", {}) or {}
    default_profile = config.get("default_profile") or DEFAULT_PROFILE_ID

    candidates: List[str] = []
    if requested_profile:
        candidates.append(requested_profile)
    if headers:
        header_profile = headers.get(PROFILE_HEADER) or headers.get(PROFILE_HEADER.title())
        if header_profile:
            candidates.append(str(header_profile))

    for candidate in candidates:
        normalized = _canonicalize_profile_id(candidate, profiles, default_profile)
        if normalized in profiles:
            return normalized

    return default_profile


def read_profile_prompt(profile: AgentProfile) -> str:
    if not profile.prompt_file:
        return ""
    path = (_workspace_path() / profile.prompt_file).resolve()
    workspace = _workspace_path()
    if not str(path).startswith(str(workspace)) or not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def profile_skills(profile: AgentProfile) -> Iterable[str]:
    return profile.skills or []
