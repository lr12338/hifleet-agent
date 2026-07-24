"""Read the authoritative V2 upstream lock for a synced skill.

The V2 lock lives at ``src/skills_v2/upstream/hifleet_skills/lock.json`` and is
intentionally separate from the legacy repo-root ``skills-lock.json`` so the two
skill systems never share a single source of truth. Runtime metadata and the
reviewed manifest/prompt are derived from this record so lock, manifest and
runtime never disagree.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def _workspace_path(workspace_path: str | Path | None = None) -> Path:
    return Path(workspace_path or os.getenv("COZE_WORKSPACE_PATH") or Path(__file__).resolve().parents[3])


def v2_lock_dir(workspace_path: str | Path | None = None) -> Path:
    return _workspace_path(workspace_path) / "src" / "skills_v2" / "upstream" / "hifleet_skills"


def lock_path(workspace_path: str | Path | None = None) -> Path:
    return v2_lock_dir(workspace_path) / "lock.json"


def load_skill_lock_record(workspace_path: str | Path | None, lock_key: str) -> dict[str, Any]:
    """Return the lock record for *lock_key*, raising if it is absent.

    Only the ``hifleet-skills`` key is supported because it is the single upstream
    skill synced into V2. The V2 lock file holds that record directly.
    """
    if lock_key != "hifleet-skills":
        raise KeyError(f"lock_unknown_key:{lock_key}")
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
