"""V2 shadow-only adapter for customer_support comparison (never the response path).

The customer_support response chain stays legacy. This module only builds an
opt-in, no-tool V2 shadow record so a legacy turn can be compared against the V2
Skill prompt. It imports only ``skills_v2.*`` and never the legacy tree.
"""
from __future__ import annotations

import json
from pathlib import Path
from time import perf_counter
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from skills_v2.core.descriptors import SkillRuntimeBundle
from skills_v2.core.policy import resolve_skill_runtime
from skills_v2.core.registry import SharedSkillRegistry


V2_SKILL_IDS = ("knowledge_retrieval", "web_search", "hifleet_data", "ship_info_update")


def _shadow_system_prompt(bundle: SkillRuntimeBundle) -> str:
    allowed_tools = ", ".join(descriptor.name for descriptor in bundle.descriptors)
    shadow_instruction = """You are running a non-customer-visible Shared Skills V2 shadow assessment.
Do not call tools, do not request credentials, and do not claim that any write occurred.
Use the V2 Skill instructions above to assess the request independently. Return only JSON with:
scenario (string), recommended_tools (array of allowed tool names), parameter_summary (object),
evidence_requirements (array of strings), high_risk_claims (array of strings),
proposed_reply (string), and confidence (low|medium|high).
If evidence is insufficient, make the proposed_reply conservative or request a key detail.
Allowed tool names: """ + allowed_tools
    return "\n\n".join(part for part in (bundle.prompt, shadow_instruction) if part)


def _shadow_response_text(response: Any) -> str:
    content = getattr(response, "content", response)
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        return "\n".join(str(item.get("text") or item.get("content") or "") for item in content if isinstance(item, dict)).strip()
    return str(content or "").strip()


def _parse_shadow_json(text: str) -> dict[str, Any] | None:
    candidate = (text or "").strip()
    if candidate.startswith("```"):
        candidate = candidate.split("\n", 1)[1] if "\n" in candidate else ""
        if candidate.endswith("```"):
            candidate = candidate[:-3]
    try:
        payload = json.loads(candidate)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _run_prompt_shadow(
    *,
    bundle: SkillRuntimeBundle,
    shadow_model: Any,
    user_text: str,
    route_trace: dict[str, Any],
    legacy_answer: str,
) -> dict[str, Any]:
    allowed_tools = {descriptor.name for descriptor in bundle.descriptors}
    trace_summary = {
        "legacy_scenario": str(route_trace.get("task_type") or route_trace.get("route") or ""),
        "legacy_tools": [str(name) for name in list(route_trace.get("tool_call_sequence") or []) if str(name)],
        "legacy_evidence_count": len(list(route_trace.get("evidence_items") or [])),
        "legacy_answer": str(legacy_answer or "")[:2000],
    }
    response = shadow_model.invoke(
        [
            SystemMessage(content=_shadow_system_prompt(bundle)),
            HumanMessage(
                content=json.dumps(
                    {"user_request": str(user_text or ""), "legacy_trace": trace_summary},
                    ensure_ascii=False,
                )
            ),
        ]
    )
    raw = _shadow_response_text(response)
    parsed = _parse_shadow_json(raw)
    if parsed is None:
        return {
            "status": "invalid_output",
            "prompt_injected": True,
            "model_invoked": True,
            "response_format": "non_json",
            "response_length": len(raw),
        }
    requested_tools = [str(name) for name in list(parsed.get("recommended_tools") or []) if str(name)]
    proposed_reply = str(parsed.get("proposed_reply") or "")
    return {
        "status": "completed",
        "prompt_injected": True,
        "model_invoked": True,
        "response_format": "json",
        "scenario": str(parsed.get("scenario") or ""),
        "recommended_tools": [name for name in requested_tools if name in allowed_tools],
        "unapproved_recommended_tools": sorted(set(requested_tools) - allowed_tools),
        "parameter_summary": dict(parsed.get("parameter_summary") or {}) if isinstance(parsed.get("parameter_summary"), dict) else {},
        "evidence_requirements": [str(item) for item in list(parsed.get("evidence_requirements") or []) if str(item)][:20],
        "high_risk_claims": [str(item) for item in list(parsed.get("high_risk_claims") or []) if str(item)][:20],
        "proposed_reply_length": len(proposed_reply),
        "proposed_reply_has_success_claim": "更新成功" in proposed_reply,
        "confidence": str(parsed.get("confidence") or ""),
    }


def build_customer_support_shadow_bundle(workspace_path: str | Path | None = None) -> SkillRuntimeBundle:
    """Produce the same contracts as customer_ceshi; callers keep legacy user output."""
    registry = SharedSkillRegistry(workspace_path)
    descriptors = registry.descriptors_for(V2_SKILL_IDS)
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
    shadow_model: Any | None = None,
    user_text: str = "",
) -> dict[str, Any]:
    """Build a no-tool V2 shadow record for a legacy customer_support turn.

    Any available shadow model receives the injected V2 Skill prompt but is never
    given tools. The comparison remains contract-only when model setup or output
    is unavailable, preventing duplicate reads and writes.
    """
    started = perf_counter()
    bundle = build_customer_support_shadow_bundle(workspace_path)
    legacy_tools = [str(name) for name in list(route_trace.get("tool_call_sequence") or []) if str(name)]
    v2_tools = {descriptor.name for descriptor in bundle.descriptors}
    write_tools = {"upload_ship_position", "update_ship_static_info"}
    legacy_writes = [name for name in legacy_tools if name in write_tools]
    evidence = list(route_trace.get("evidence_items") or [])
    shadow_inference: dict[str, Any] = {
        "status": "not_available",
        "prompt_injected": False,
        "model_invoked": False,
    }
    if shadow_model is not None:
        try:
            shadow_inference = _run_prompt_shadow(
                bundle=bundle,
                shadow_model=shadow_model,
                user_text=user_text,
                route_trace=route_trace,
                legacy_answer=final_answer,
            )
        except Exception as exc:
            shadow_inference = {
                "status": "failed",
                "prompt_injected": False,
                "model_invoked": True,
                "reason": type(exc).__name__,
            }
    return {
        "status": "completed_prompt_shadow" if shadow_inference.get("status") == "completed" else "completed_contract_shadow",
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
        "shadow_inference": shadow_inference,
    }
