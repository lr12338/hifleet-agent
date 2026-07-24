"""Responses API adapter for Shared Skills V2 without changing its agent loop.

This is the V2-native counterpart of the legacy ``skills.adapters.customer_ceshi``.
It assembles the ``customer_ceshi`` runtime bundle purely from V2 skills and never
imports the legacy ``skills.*`` tree.
"""
from __future__ import annotations

from pathlib import Path

from skills_v2.core.descriptors import SkillRuntimeBundle, ToolDescriptor
from skills_v2.core.lock_store import hifleet_lock_record
from skills_v2.core.policy import resolve_skill_runtime
from skills_v2.core.registry import SharedSkillRegistry


V2_SKILL_IDS = ("knowledge_retrieval", "web_search", "hifleet_data", "ship_info_update")


def _source_versions(manifests, workspace_path: str | Path | None) -> dict[str, dict[str, str]]:
    """Build runtime source versions. hifleet_data is anchored to the V2 lock record."""
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
    tools = registry.tools_for(descriptors)
    manifests = registry._manifests
    return SkillRuntimeBundle(
        profile_id="customer_ceshi",
        mode=resolve_skill_runtime("customer_ceshi", workspace_path),
        tools=tools,
        descriptors=descriptors,
        prompt=registry.prompt_for(V2_SKILL_IDS),
        source_versions=_source_versions(manifests, workspace_path),
    )
