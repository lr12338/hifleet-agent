"""LangChain/LangGraph-facing V2 adapter for shadow-only customer_support use."""
from __future__ import annotations

from pathlib import Path
from time import perf_counter
from typing import Any

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


def compare_legacy_trace_with_v2(
    *,
    route_trace: dict[str, Any],
    final_answer: str,
    workspace_path: str | Path | None = None,
) -> dict[str, Any]:
    """Build a non-executing V2 shadow record for a legacy customer_support turn.

    This deliberately does not call a model or any V2 tool. It verifies the
    legacy trace against the shared V2 contract, preventing duplicate reads and
    making a write turn explicit as dry-run-only shadow data.
    """
    started = perf_counter()
    bundle = build_customer_support_shadow_bundle(workspace_path)
    legacy_tools = [str(name) for name in list(route_trace.get("tool_call_sequence") or []) if str(name)]
    v2_tools = {descriptor.name for descriptor in bundle.descriptors}
    write_tools = {"upload_ship_position", "update_ship_static_info"}
    legacy_writes = [name for name in legacy_tools if name in write_tools]
    evidence = list(route_trace.get("evidence_items") or [])
    return {
        "status": "completed_contract_shadow",
        "runtime_mode": "shadow",
        "executed_tools": [],
        "dry_run": True,
        "scenario": str(route_trace.get("task_type") or route_trace.get("route") or ""),
        "tool_selection": {
            "legacy": legacy_tools,
            "v2_allowed": sorted(v2_tools),
            "legacy_not_in_v2": sorted(set(legacy_tools) - v2_tools),
        },
        "parameters": "not_replayed: prevents duplicate side effects",
        "evidence": {"legacy_count": len(evidence), "v2_replayed": False},
        "high_risk_claims": {"legacy_answer_has_success_claim": "更新成功" in (final_answer or "")},
        "final_reply": {"legacy_length": len(final_answer or ""), "v2_generated": False},
        "latency_ms": int((perf_counter() - started) * 1000),
        "tool_count": 0,
        "write_state": "dry_run_required" if legacy_writes else "no_shadow_write",
        "legacy_write_tools": legacy_writes,
        "source_versions": dict(bundle.source_versions),
        "prompt_loaded_chars": len(bundle.prompt),
    }
