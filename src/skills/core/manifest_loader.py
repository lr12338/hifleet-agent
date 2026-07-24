"""Load and validate local, machine-readable Shared Skills manifests."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .contracts import SkillManifest
from .errors import ManifestValidationError


def load_manifest(path: str | Path) -> SkillManifest:
    manifest_path = Path(path)
    try:
        payload = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise ManifestValidationError(f"Unable to load manifest {manifest_path.name}") from exc
    if not isinstance(payload, dict):
        raise ManifestValidationError("Manifest root must be a mapping")
    capabilities = payload.get("capabilities") or []
    if not isinstance(capabilities, list) or not capabilities:
        raise ManifestValidationError("Manifest must declare at least one capability")
    names: set[str] = set()
    for capability in capabilities:
        if not isinstance(capability, dict):
            raise ManifestValidationError("Manifest capability must be a mapping")
        name = str(capability.get("tool_name") or capability.get("id") or "").strip()
        if not name or name in names:
            raise ManifestValidationError("Manifest has a missing or duplicate tool name")
        names.add(name)
        if capability.get("read_only") is False and not capability.get("requires_confirmation"):
            raise ManifestValidationError(f"Writable capability {name} requires confirmation")
    try:
        return SkillManifest(
            schema_version=int(payload.get("schema_version", 0)),
            skill_id=str(payload["skill_id"]),
            skill_version=str(payload["skill_version"]),
            prompt_file=str(payload.get("prompt_file", "SKILL.md")),
            capabilities=tuple(capabilities),
            upstream_commit=str(payload.get("upstream_commit", "")),
            upstream_repository=str(payload.get("upstream_repository", "")),
            upstream_lock_key=str(payload.get("upstream_lock_key", "")),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ManifestValidationError("Manifest is missing required metadata") from exc
