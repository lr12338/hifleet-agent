"""Normalize V2 tool metadata without making semantic-answer decisions."""
from __future__ import annotations

import json
from typing import Any

from .contracts import ToolDescriptor


def normalize_tool_result(raw: Any, descriptor: ToolDescriptor) -> dict[str, Any]:
    if isinstance(raw, str):
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = {"text": raw}
    elif isinstance(raw, dict):
        payload = dict(raw)
    else:
        payload = {"value": str(raw)}
    payload.setdefault("skill_id", descriptor.skill_id)
    payload.setdefault("skill_version", descriptor.skill_version)
    payload.setdefault("upstream_commit", descriptor.upstream_commit)
    payload.setdefault("capability", descriptor.name)
    payload.setdefault("warnings", [])
    return payload
