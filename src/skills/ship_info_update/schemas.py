"""Single source of transaction tool schemas for both adapter protocols."""
from __future__ import annotations

from skills.core.contracts import ToolDescriptor
from skills.core.manifest_loader import load_manifest


def transaction_descriptors() -> tuple[ToolDescriptor, ...]:
    manifest = load_manifest(__file__.replace("schemas.py", "manifest.yaml"))
    return tuple(
        ToolDescriptor(
            name=str(item["id"]),
            skill_id=manifest.skill_id,
            description=str(item["description"]),
            input_schema=dict(item["input_schema"]),
            read_only=False,
            risk_level=str(item["risk_level"]),
            timeout_seconds=int(item["timeout_seconds"]),
            requires_confirmation=True,
            skill_version=manifest.skill_version,
        )
        for item in manifest.capabilities
    )
