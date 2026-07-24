"""Read the authoritative skills-lock.json record for a synced upstream skill.

The lock file is the single source of truth for upstream version, commit,
content hash, approved read-only capabilities and required environment. Runtime
metadata and the reviewed manifest/prompt are derived from this record so that
lock, manifest and runtime never disagree.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def _workspace_path(workspace_path: str | Path | None = None) -> Path:
    return Path(workspace_path or os.getenv("COZE_WORKSPACE_PATH") or Path(__file__).resolve().parents[3])


def lock_path(workspace_path: str | Path | None = None) -> Path:
    return _workspace_path(workspace_path) / "skills-lock.json"


def load_skill_lock_record(workspace_path: str | Path | None, lock_key: str) -> dict[str, Any]:
    """Return the lock record for *lock_key*, raising if it is absent."""
    path = lock_path(workspace_path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise KeyError(f"lock_unreadable:{lock_key}") from exc
    record = (payload.get("skills") or {}).get(lock_key) or {}
    if not record:
        raise KeyError(f"lock_missing_skill:{lock_key}")
    return dict(record)


def hifleet_lock_record(workspace_path: str | Path | None = None) -> dict[str, Any]:
    return load_skill_lock_record(workspace_path, "hifleet-skills")
