"""V2 profile policy and configuration-only emergency rollback controls."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


DEFAULT_SKILL_RUNTIME = {"customer_support": "legacy", "customer_ceshi": "v2"}
EXTERNAL_V2_SKILLS = frozenset({"knowledge_retrieval", "hifleet_data", "ship_info_update"})
DENIED_EXTERNAL_TOOLS = frozenset({"knowledge_admin", "upload_ship_position", "update_ship_static_info"})


def _workspace_path(workspace_path: str | Path | None = None) -> Path:
    return Path(workspace_path or os.getenv("COZE_WORKSPACE_PATH") or Path(__file__).resolve().parents[3])


def load_skill_runtime_config(workspace_path: str | Path | None = None) -> dict[str, Any]:
    path = _workspace_path(workspace_path) / "config" / "agent_profiles.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        payload = {}
    return dict(payload.get("skill_runtime") or {})


def resolve_skill_runtime(profile_id: str, workspace_path: str | Path | None = None) -> str:
    profile = (profile_id or "").strip()
    default = DEFAULT_SKILL_RUNTIME.get(profile, "legacy")
    configured = str((load_skill_runtime_config(workspace_path).get(profile) or {}).get("mode") or default).strip().lower()
    override_name = f"{profile.upper()}_SKILLS_MODE"
    mode = os.getenv(override_name, configured).strip().lower()
    return mode if mode in {"legacy", "v2", "shadow"} else default


def profile_allows_tool(skill_id: str, tool_name: str) -> bool:
    return skill_id in EXTERNAL_V2_SKILLS and tool_name not in DENIED_EXTERNAL_TOOLS
