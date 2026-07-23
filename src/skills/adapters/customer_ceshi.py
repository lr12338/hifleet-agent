"""Responses API adapter for Shared Skills V2 without changing its agent loop."""
from __future__ import annotations

from pathlib import Path

from skills import SkillLoader
from skills.core.contracts import SkillRuntimeBundle
from skills.core.policy import resolve_skill_runtime
from skills.core.registry import SharedSkillRegistry


V2_SKILL_IDS = ("knowledge_retrieval", "hifleet_data", "ship_info_update")


def _foundation_descriptors(registry: SharedSkillRegistry):
    """Search and verification are base tools, not business Skills."""
    tools = {tool.name: tool for tool in SkillLoader.get_tools_by_names(["web_search", "verify_public_page"])}
    definitions = (
        ("web_search", "Search public web sources once for candidate evidence.", "low"),
        ("verify_public_page", "Verify one URL that was supplied by the user or returned by web_search.", "medium"),
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
        source_versions={
            skill_id: {
                "skill_version": manifest.skill_version,
                "upstream_commit": manifest.upstream_commit,
                "upstream_repository": manifest.upstream_repository,
            }
            for skill_id, manifest in manifests.items()
        },
    )
