"""Responses API adapter for Shared Skills V2 without changing its agent loop."""
from __future__ import annotations

from pathlib import Path

from skills import SkillLoader
from skills.core.contracts import SkillRuntimeBundle
from skills.core.lock_store import hifleet_lock_record
from skills.core.policy import resolve_skill_runtime
from skills.core.registry import SharedSkillRegistry


V2_SKILL_IDS = ("knowledge_retrieval", "hifleet_data", "ship_info_update")


def _foundation_descriptors(registry: SharedSkillRegistry):
    """Search is the only foundation tool. Page verification/browser tools are not exposed."""
    tools = {tool.name: tool for tool in SkillLoader.get_tools_by_names(["web_search"])}
    definitions = (
        ("web_search", "Search public web sources once for candidate evidence.", "low"),
    )
    from skills.core.contracts import ToolDescriptor

    return tuple(
        ToolDescriptor(
            name=name,
            skill_id="foundation",
            description=description,
            input_schema=registry._schema_for(tools[name]),
            read_only=True,
            risk_level=risk_level,
            timeout_seconds=20,
            skill_version="2.0.0",
        )
        for name, description, risk_level in definitions
        if name in tools
    )


def _source_versions(manifests, workspace_path: str | Path | None) -> dict[str, dict[str, str]]:
    """Build runtime source versions. hifleet_data is anchored to the lock record."""
    versions: dict[str, dict[str, str]] = {}
    for skill_id, manifest in manifests.items():
        entry = {
            "skill_version": manifest.skill_version,
            "upstream_commit": manifest.upstream_commit,
            "upstream_repository": manifest.upstream_repository,
        }
        if manifest.upstream_lock_key:
            try:
                record = hifleet_lock_record(workspace_path)
            except KeyError:
                record = {}
            if record.get("contentHash"):
                entry["content_hash"] = str(record["contentHash"])
            if record.get("lastKnownGood"):
                entry["last_known_good"] = str(record["lastKnownGood"])
        versions[skill_id] = entry
    return versions


def build_customer_ceshi_bundle(workspace_path: str | Path | None = None) -> SkillRuntimeBundle:
    registry = SharedSkillRegistry(workspace_path)
    descriptors = registry.descriptors_for(V2_SKILL_IDS)
    foundation = _foundation_descriptors(registry)
    tools = registry.tools_for(descriptors) + tuple(SkillLoader.get_tools_by_names([item.name for item in foundation]))
    manifests = registry._manifests
    return SkillRuntimeBundle(
        profile_id="customer_ceshi",
        mode=resolve_skill_runtime("customer_ceshi", workspace_path),
        tools=tools,
        descriptors=descriptors + foundation,
        prompt=registry.prompt_for(V2_SKILL_IDS),
        source_versions=_source_versions(manifests, workspace_path),
    )
