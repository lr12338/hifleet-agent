"""LangChain/LangGraph-facing V2 adapter for shadow-only customer_support use."""
from __future__ import annotations

from pathlib import Path

from .customer_ceshi import _foundation_descriptors
from skills.core.contracts import SkillRuntimeBundle
from skills.core.policy import resolve_skill_runtime
from skills.core.registry import SharedSkillRegistry


V2_SKILL_IDS = ("knowledge_retrieval", "hifleet_data", "ship_info_update")


def build_customer_support_shadow_bundle(workspace_path: str | Path | None = None) -> SkillRuntimeBundle:
    """Produce the same contracts as customer_ceshi; callers keep legacy user output."""
    registry = SharedSkillRegistry(workspace_path)
    descriptors = registry.descriptors_for(V2_SKILL_IDS) + _foundation_descriptors(registry)
    manifests = registry._manifests
    return SkillRuntimeBundle(
        profile_id="customer_support",
        mode=resolve_skill_runtime("customer_support", workspace_path),
        tools=registry.tools_for(descriptors),
        descriptors=descriptors,
        prompt=registry.prompt_for(V2_SKILL_IDS),
        source_versions={
            skill_id: {"skill_version": manifest.skill_version, "upstream_commit": manifest.upstream_commit}
            for skill_id, manifest in manifests.items()
        },
    )
