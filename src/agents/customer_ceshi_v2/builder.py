from __future__ import annotations

import os
import re
import time
from urllib.parse import urlparse
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from storage.memory.memory_saver import get_memory_saver

from .actions import ShipUpdateGate
from .contracts import AgentDecision, InspectMediaRequest, MediaAsset, ToolCall
from .evidence import EvidenceReviewer
from .models import ModelGatewayError, MultimodalPerceptionClient, TextReasoningClient
from .perception import PerceptionService
from .state import CustomerCeshiV2State
from .tracing import safe_trace
from .tools import CapabilityRegistry


MAX_STEPS_DEFAULT = 6
CHECKPOINT_NAMESPACE = "customer_ceshi_v3"


class _NamespacedGraph:
    """Keep experimental checkpoints separate even when callers reuse a session id."""

    def __init__(self, graph: Any):
        self._graph = graph

    def _config(self, config: dict[str, Any] | None) -> dict[str, Any]:
        scoped = dict(config or {})
        configurable = dict(scoped.get("configurable") or {})
        thread_id = str(configurable.get("thread_id", "default"))
        configurable["thread_id"] = thread_id if thread_id.startswith(f"{CHECKPOINT_NAMESPACE}:") else f"{CHECKPOINT_NAMESPACE}:{thread_id}"
        configurable["checkpoint_ns"] = CHECKPOINT_NAMESPACE
        scoped["configurable"] = configurable
        return scoped

    def invoke(self, input: Any, config: dict[str, Any] | None = None, **kwargs: Any):
        try:
            return self._graph.invoke(input, self._config(config), **kwargs)
        except Exception:
            answer = "实验客服链遇到暂时性错误，未切换到生产客服链；请稍后重试或补充必要信息。"
            return {"phase": "done", "status": "degraded", "generated_answer": answer, "messages": [AIMessage(content=answer)], "route_trace": {"agent": "customer_ceshi_v2", "degrade_reason": "unhandled_v2_error"}}

    def stream(self, input: Any, config: dict[str, Any] | None = None, **kwargs: Any):
        return self._graph.stream(input, self._config(config), **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._graph, name)


def _latest_human_message(messages: list[Any]) -> Any | None:
    for message in reversed(messages or []):
        if isinstance(message, HumanMessage) or getattr(message, "type", "") == "human":
            return message
    return None


def _latest_text(messages: list[Any]) -> str:
    message = _latest_human_message(messages)
    if message is not None:
        content = getattr(message, "content", "")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "\n".join(str(part.get("text", "")) for part in content if isinstance(part, dict) and part.get("type") == "text")
    return ""


def _extract_media(message: Any | None) -> list[dict[str, Any]]:
    assets: list[dict[str, Any]] = []
    content = getattr(message, "content", "") if message is not None else ""
    if not isinstance(content, list):
        return assets
    for index, part in enumerate(content):
        if not isinstance(part, dict):
            continue
        part_type = str(part.get("type", "")).lower()
        kind = {"image_url": "image", "input_audio": "audio", "video_url": "video", "file_url": "file"}.get(part_type)
        if not kind:
            continue
        details = part.get(part_type, {}) or {}
        url = str(details.get("url", "")) if isinstance(details, dict) else ""
        assets.append(MediaAsset(asset_id=f"asset-{index}-{index}", kind=kind, url=url).model_dump())
    return assets


def _references_previous_media(text: str) -> bool:
    normalized = re.sub(r"\s+", "", text or "")
    return any(token in normalized for token in ("上一条媒体", "上一张", "刚才图片", "这张图", "这个标识", "这是什么", "这是什么标识"))


def _current_media_requires_inspection(text: str, assets: list[dict[str, Any]], observations: list[dict[str, Any]]) -> bool:
    if not assets:
        return False
    # A current image is normally the subject of the turn even when the user
    # asks with wording such as "图上紫色的波浪线" instead of "这是什么".
    if not _references_previous_media(text) and (not text.strip() or not any(item.get("kind") == "image" for item in assets)):
        return False
    asset_ids = {str(item.get("asset_id", "")) for item in assets}
    return not any(
        item.get("status") == "success"
        and item.get("capability") == "inspect_media"
        and str((item.get("data") or {}).get("perception_packet", {}).get("asset_id", "")) in asset_ids
        for item in observations
    )


def _current_media_failure(assets: list[dict[str, Any]], observations: list[dict[str, Any]]) -> dict[str, Any] | None:
    asset_ids = {str(item.get("asset_id", "")) for item in assets}
    for observation in reversed(observations):
        if observation.get("capability") != "inspect_media" or observation.get("status") == "success":
            continue
        observed_asset_id = str((observation.get("data") or {}).get("asset_id", ""))
        if observed_asset_id in asset_ids or (not observed_asset_id and len(asset_ids) == 1):
            return observation
    return None


def _local_media_fallback_answer(observations: list[dict[str, Any]]) -> str:
    """Keep deterministic image hints explicitly provisional instead of letting a text model overstate them."""
    for observation in reversed(observations):
        packet = dict((observation.get("data") or {}).get("perception_packet") or {})
        if packet.get("model") != "local_visual_fallback":
            continue
        symbol = str(packet.get("suspected_symbol") or "")
        features = "、".join(str(item) for item in packet.get("visual_features", []) if item)
        if "安全水域浮标" in symbol:
            return f"初步判断：这是**安全水域浮标（Safe Water Mark）**。识别依据是图片中可见{features or '红色圆形标记和中心黑色圆点'}。该判断来自当前图片的可见特征；如需用于航行决策，请以正式海图图例和船载 ECDIS 显示为准。"
        if "区域" in symbol or "边界" in symbol:
            return f"初步判断：图中的紫色线条更像**海图区域或航行管制边界**，并非灰色等深线。当前可见特征为{features or '紫色线条在海图上延伸'}；仅凭截图无法确认具体图层，请在 HiFleet【显示】-【区域】中逐项开关相关图层复核。"
        if "预警" in symbol or "避碰" in symbol:
            return f"初步判断：这些小圈圈更像**船舶预警或避碰关注范围**。当前可见特征为{features or '多个深色圆圈围绕船舶和近岸水域'}；请点击任一圆圈查看属性，并以该属性和平台图例为准。"
        return f"初步判断：{symbol or '该海图标识'}。识别依据：{features or packet.get('factual_summary', '')}。建议结合平台图例进一步确认。"
    return ""


def _media_failure_answer(error_code: str, task_goal: str) -> str:
    setting_question = any(token in (task_goal or "") for token in ("如何设定", "如何设置", "怎么设置", "怎样设置", "如何设"))
    if setting_question and error_code in {"media_no_visual_evidence", "model_timeout", "model_unavailable", "model_busy", "model_auth_failed"}:
        return "抱歉，当前未能识别截图中的具体设置项。请重新上传包含完整设置面板和文字的清晰截图，或直接发送设置项名称；我会按该功能给您说明具体设定步骤。"
    if error_code == "media_no_visual_evidence":
        return "抱歉，我已尝试识别当前图片及其细节，但未提取到足以确认该标识的有效信息。请重新上传更清晰的标记区域截图，或补充图例、位置和颜色特征，我可以继续帮您判断。"
    if error_code in {"media_download_failed", "media_download_timeout"}:
        return "抱歉，当前图片读取失败，请稍后重试或重新上传图片。"
    if error_code == "model_timeout":
        return "抱歉，图片识别超时，暂时无法看清您要咨询的设置项。请重新上传包含完整设置面板和文字的清晰截图，或直接发送设置项名称，我可以继续说明操作步骤。"
    return "抱歉，当前图片识别服务暂时不可用，请稍后重试或重新上传图片。"


def _extract_confirmed_ship_context(observations: list[dict[str, Any]], previous: dict[str, Any]) -> dict[str, Any]:
    context = {key: value for key, value in dict(previous or {}).items() if key in {"mmsi", "imo", "ship_name", "last_media_asset"} and value}
    for observation in observations:
        if observation.get("status") not in {"success", "partial"}:
            continue
        data = dict(observation.get("data") or {})
        arguments = dict(data.get("arguments") or {})
        for key in ("mmsi", "imo", "ship_name"):
            value = arguments.get(key) or data.get(key)
            if value not in (None, ""):
                context[key] = str(value)
    return context


def _media_metadata_observations(assets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    observations: list[dict[str, Any]] = []
    for asset in assets:
        if asset.get("kind") != "video":
            continue
        filename = urlparse(str(asset.get("url", ""))).path.rsplit("/", 1)[-1]
        observations.append({"status": "success", "capability": "media_metadata", "facts": [f"Video attachment metadata is available: {filename or asset.get('asset_id')}"], "data": {"asset_id": asset.get("asset_id"), "media_type": "video", "filename": filename}, "warnings": [], "sources": []})
    return observations


def _confirmation_token(text: str) -> str:
    match = re.search(r"(?:确认|confirm)\s*[:：]?\s*([a-f0-9]{64})", text or "", re.I)
    return match.group(1) if match else ""


def _redacted_trace(state: CustomerCeshiV2State, *, reason: str = "") -> dict[str, Any]:
    observations = list(state.get("observations", []))
    media_packets = [item.get("data", {}).get("perception_packet", {}) for item in observations if item.get("capability") == "inspect_media"]
    models = ["deepseek_orchestrator"] + (["doubao_perception"] if media_packets else [])
    diagnostics = dict(state.get("turn_diagnostics", {}))
    trace = {"agent": "customer_ceshi_v2", "checkpoint_namespace": CHECKPOINT_NAMESPACE, "models": models, "model_routes": diagnostics.get("model_routes", {}), "task_goal": state.get("task_goal", ""), "media_asset_count": len(state.get("media_assets", [])), "media_types": [item.get("kind") for item in state.get("media_assets", [])], "media_objectives": [packet.get("requested_objective", "") for packet in media_packets], "media_facts": [packet.get("factual_summary", "") for packet in media_packets], "tool_calls": state.get("generated_tool_calls", []), "sources": [source for item in observations for source in item.get("sources", [])], "claims": state.get("claims", []), "evidence": state.get("evidence_review", {}), "metrics": state.get("metrics", {}), "degrade_reason": reason or state.get("degrade_reason", ""), "turn_diagnostics": diagnostics}
    return safe_trace(trace)


def build_customer_ceshi_v2_agent(ctx: Any, cfg: dict[str, Any], workspace_path: str, profile: Any, intent_hint: str = ""):
    config = dict(cfg.get("config") or cfg)
    enabled = os.getenv("CUSTOMER_CESHI_V2_ENABLED", "true").lower() not in {"0", "false", "off"}
    text_client = getattr(ctx, "customer_ceshi_v2_text_client", None) if ctx is not None else None
    perception_client = getattr(ctx, "customer_ceshi_v2_perception_client", None) if ctx is not None else None
    registry = getattr(ctx, "customer_ceshi_v2_tool_registry", None) if ctx is not None else None
    registry = registry or CapabilityRegistry(skill_names=list(getattr(profile, "skills", []) or []))
    orchestrator = text_client or TextReasoningClient(config, ctx=ctx)
    evidence_reviewer = getattr(ctx, "customer_ceshi_v2_evidence_reviewer", None) if ctx is not None else None
    evidence_reviewer = evidence_reviewer or EvidenceReviewer(orchestrator)
    perception = perception_client or MultimodalPerceptionClient(config, ctx=ctx)
    perception_service = getattr(ctx, "customer_ceshi_v2_perception_service", None) if ctx is not None else None
    perception_service = perception_service or PerceptionService(perception)
    write_gate = getattr(ctx, "customer_ceshi_v2_write_gate", None) if ctx is not None else None
    write_gate = write_gate or ShipUpdateGate(enabled=bool(config.get("customer_ceshi_v2_ship_write_enabled", False)))
    max_steps = int(config.get("customer_ceshi_v2_max_steps", MAX_STEPS_DEFAULT))
    max_media_calls = int(config.get("customer_ceshi_v2_max_media_calls", 4))
    max_tool_calls = int(config.get("customer_ceshi_v2_max_tool_calls", 8))
    max_runtime_ms = int(config.get("customer_ceshi_v2_max_runtime_ms", 90_000))

    def ingest(state: CustomerCeshiV2State):
        latest_message = _latest_human_message(state.get("messages", []))
        text = _latest_text(state.get("messages", []))
        previous_context = dict(state.get("confirmed_context", {}))
        assets = _extract_media(latest_message)
        inherited_entity = {key: previous_context[key] for key in ("mmsi", "imo", "ship_name") if previous_context.get(key)}
        inherited_media = False
        if not assets and _references_previous_media(text) and isinstance(previous_context.get("last_media_asset"), dict):
            assets = [dict(previous_context["last_media_asset"])]
            inherited_media = True
        initial_observations = _media_metadata_observations(assets)
        if inherited_entity:
            entity_text = ", ".join(f"{key}={value}" for key, value in inherited_entity.items())
            initial_observations.append({"status": "success", "capability": "confirmed_context", "facts": [f"Confirmed ship context from a prior successful tool result: {entity_text}"], "data": inherited_entity, "warnings": [], "sources": []})
        base_update = {
            "step_count": 0,
            "started_at_ms": int(time.monotonic() * 1000),
            "task_goal": text,
            "candidate_answer": "",
            "decision": {},
            "evidence_review": {},
            "generated_answer": "",
            "degrade_reason": "",
            "dependency_error": {},
            "claims": [],
            "generated_tool_calls": [],
            "tool_fingerprints": {},
            "media_call_count": 0,
            "tool_call_count": 0,
            "media_assets": assets,
            "observations": initial_observations,
            "metrics": {"model_calls": 0, "media_calls": 0, "tool_calls": 0, "cache_hits": 0, "evidence_reviews": 0},
            "confirmed_context": previous_context,
            "turn_diagnostics": {
                "turn_initialized": True,
                "checkpoint_namespace": CHECKPOINT_NAMESPACE,
                "inherited_entity": bool(inherited_entity),
                "inherited_media": inherited_media,
                "current_media_count": len(assets),
                "degrade_stage": "",
                "model_routes": {
                    "orchestrator": {
                        "role": getattr(orchestrator, "model_role", "json"),
                        "model": getattr(orchestrator, "model", ""),
                        "external_model_ignored": bool(getattr(orchestrator, "model_override_ignored", False)),
                    },
                    "perception": {
                        "role": getattr(perception, "model_role", "multimodal"),
                        "model": getattr(perception, "model", ""),
                        "external_model_ignored": bool(getattr(perception, "model_override_ignored", False)),
                    },
                },
            },
        }
        if not enabled:
            return {**base_update, "phase": "finalize", "degrade_reason": "feature_disabled", "candidate_answer": "实验客服链当前已关闭，未切换到生产客服链。"}
        token = _confirmation_token(text)
        if token:
            result = write_gate.commit(token, user_id=str(state.get("user_id", "")), session_id=str(state.get("session_id", "")), profile_id="customer_ceshi")
            answer = result.facts[0] if result.status == "success" and result.facts else "写入未完成：确认令牌无效、已过期，或写入服务未明确确认成功。"
            return {**base_update, "phase": "finalize", "observations": [result.model_dump()], "generated_tool_calls": ["commit_ship_update"], "candidate_answer": answer, "degrade_reason": "" if result.status == "success" else "write_commit_not_confirmed"}
        return {**base_update, "phase": "orchestrate", "working_memory": {"goal": text, "known_facts": [fact for item in initial_observations for fact in item.get("facts", [])], "claims": [], "hypotheses": [], "open_questions": [], "observations": initial_observations, "next_objective": "Determine the next evidence-gathering action.", "completion_ready": False, "completion_reason": ""}}

    def orchestrate(state: CustomerCeshiV2State):
        elapsed_ms = int(time.monotonic() * 1000) - int(state.get("started_at_ms", int(time.monotonic() * 1000)))
        if int(state.get("step_count", 0)) >= max_steps or elapsed_ms >= max_runtime_ms:
            diagnostics = dict(state.get("turn_diagnostics", {})); diagnostics["degrade_stage"] = "orchestrate_budget"
            return {"phase": "finalize", "degrade_reason": "step_budget_exhausted", "candidate_answer": "为保证可靠性，我暂时无法在允许的步骤内完成核验。", "turn_diagnostics": diagnostics}
        media_failure = _current_media_failure(state.get("media_assets", []), state.get("observations", []))
        if media_failure:
            error_code = str((media_failure.get("warnings") or ["media_perception_failed"])[0])
            diagnostics = dict(state.get("turn_diagnostics", {})); diagnostics["degrade_stage"] = "media_perception"; diagnostics["media_error_code"] = error_code
            media_diagnostics = dict((media_failure.get("data") or {}).get("media_diagnostics") or {})
            if media_diagnostics:
                diagnostics["media_delivery"] = media_diagnostics.get("media_delivery", "")
                diagnostics["media_perception_variant"] = media_diagnostics.get("media_perception_variant", "")
            answer = _media_failure_answer(error_code, state.get("task_goal", ""))
            return {
                "phase": "finalize",
                "degrade_reason": error_code,
                "dependency_error": {"code": error_code, "retryable": bool(media_failure.get("retry_allowed"))},
                "candidate_answer": answer,
                "turn_diagnostics": diagnostics,
            }
        if _current_media_requires_inspection(state.get("task_goal", ""), state.get("media_assets", []), state.get("observations", [])):
            diagnostics = dict(state.get("turn_diagnostics", {})); diagnostics["degrade_stage"] = "media_preflight"
            decision = AgentDecision(
                action="call_tools",
                media_requests=[
                    InspectMediaRequest(
                        asset_id=item["asset_id"],
                        objective="Inspect the current attachment before answering the user's media question.",
                        mode="visual_detail",
                    )
                    for item in state.get("media_assets", [])
                ],
            )
            return {
                "phase": "execute",
                "step_count": int(state.get("step_count", 0)) + 1,
                "decision": decision.model_dump(),
                "turn_diagnostics": diagnostics,
            }
        local_media_answer = _local_media_fallback_answer(state.get("observations", []))
        if local_media_answer:
            diagnostics = dict(state.get("turn_diagnostics", {})); diagnostics["degrade_stage"] = "media_local_fallback"; diagnostics["media_answer_mode"] = "provisional_local_visual"
            return {"phase": "finalize", "candidate_answer": local_media_answer, "turn_diagnostics": diagnostics}
        call_port_result = next(
            (
                item
                for item in reversed(state.get("observations", []))
                if item.get("capability") == "get_ship_call_ports"
                and item.get("status") in {"not_found", "upstream_error", "invalid_input"}
            ),
            None,
        )
        if call_port_result:
            diagnostics = dict(state.get("turn_diagnostics", {})); diagnostics["degrade_stage"] = "get_ship_call_ports"
            status = call_port_result.get("status")
            if status == "not_found":
                answer = "已按当前确认的船舶标识重新查询历史挂靠，但未查到可展示的靠港记录。请确认查询时间范围，或稍后重试。"
                return {"phase": "finalize", "candidate_answer": answer, "turn_diagnostics": diagnostics}
            return {"phase": "finalize", "degrade_reason": "get_ship_call_ports_failed", "dependency_error": {"code": "get_ship_call_ports_failed", "retryable": status == "upstream_error"}, "candidate_answer": "历史挂靠查询服务暂时未返回有效结果，请稍后重试或补充查询时间范围。", "turn_diagnostics": diagnostics}
        virtual_descriptors = [
            {"name": "inspect_media", "capability": "media_perception", "read_only": True, "description": "Inspect one attachment for observed facts."},
            {"name": "inspect_media_region", "capability": "media_perception", "read_only": True, "description": "Recheck an image region for a specific question."},
            {"name": "inspect_video_segment", "capability": "media_perception", "read_only": True, "description": "Recheck a bounded video time range."},
            {"name": "prepare_ship_update", "capability": "ship_write_gate", "read_only": False, "requires_confirmation": True, "description": "Prepare, validate, and summarize a ship update without committing it."},
            {"name": "commit_ship_update", "capability": "ship_write_gate", "read_only": False, "requires_confirmation": True, "description": "Commit only with a valid confirmation token."},
        ]
        try:
            decision = orchestrator.decide(task_goal=state.get("task_goal", ""), observations=state.get("observations", []), assets=state.get("media_assets", []), descriptors=[descriptor.__dict__ for descriptor in registry.descriptors()] + virtual_descriptors, step_count=int(state.get("step_count", 0)))
        except ModelGatewayError as exc:
            diagnostics = dict(state.get("turn_diagnostics", {})); diagnostics["degrade_stage"] = "orchestrator_model"
            metrics = dict(state.get("metrics", {})); metrics["model_calls"] = int(metrics.get("model_calls", 0)) + int(getattr(exc, "model_calls", 0) or getattr(orchestrator, "last_decision_model_calls", 0) or 1); metrics["elapsed_ms"] = elapsed_ms
            return {"phase": "finalize", "degrade_reason": exc.code, "dependency_error": {"code": exc.code, "retryable": exc.retryable}, "candidate_answer": "抱歉，当前智能服务暂时不可用，请稍后重试。", "turn_diagnostics": diagnostics, "metrics": metrics}
        except Exception:
            diagnostics = dict(state.get("turn_diagnostics", {})); diagnostics["degrade_stage"] = "orchestrator"
            metrics = dict(state.get("metrics", {})); metrics["model_calls"] = int(metrics.get("model_calls", 0)) + int(getattr(orchestrator, "last_decision_model_calls", 0) or 1); metrics["elapsed_ms"] = elapsed_ms
            return {"phase": "finalize", "degrade_reason": "orchestrator_error", "dependency_error": {"code": "orchestrator_error", "retryable": True}, "candidate_answer": "抱歉，当前智能服务暂时不可用，请稍后重试。", "turn_diagnostics": diagnostics, "metrics": metrics}
        if decision.action == "finish" and _current_media_requires_inspection(state.get("task_goal", ""), state.get("media_assets", []), state.get("observations", [])):
            decision = AgentDecision(action="call_tools", media_requests=[InspectMediaRequest(asset_id=item["asset_id"], objective="Inspect the current attachment before answering the user's media question.", mode="visual_detail") for item in state.get("media_assets", [])])
        metrics = dict(state.get("metrics", {})); metrics["model_calls"] = int(metrics.get("model_calls", 0)) + int(getattr(orchestrator, "last_decision_model_calls", 0) or 1); metrics["elapsed_ms"] = elapsed_ms
        working_memory = dict(state.get("working_memory", {})); working_memory["claims"] = decision.claims or working_memory.get("claims", []); working_memory["next_objective"] = decision.perception_goal or decision.question or "Execute the selected capability."
        update = {"phase": "execute", "step_count": int(state.get("step_count", 0)) + 1, "decision": decision.model_dump(), "claims": decision.claims or state.get("claims", []), "working_memory": working_memory, "metrics": metrics}
        if decision.action == "finish":
            update.update({"phase": "review", "candidate_answer": decision.answer_draft})
        elif decision.action == "ask_user":
            update.update({"phase": "finalize", "candidate_answer": decision.question or "该操作需要补充信息或明确确认；实验链不会直接执行写入。", "degrade_reason": "write_confirmation_required" if decision.action == "propose_write" else "needs_user_input"})
        elif decision.action == "propose_write":
            proposal = decision.write_proposal
            if proposal is None:
                update.update({"phase": "finalize", "candidate_answer": "请提供要更新的字段和船舶标识；实验链不会直接执行写入。", "degrade_reason": "missing_write_proposal"})
            else:
                observation = write_gate.prepare(proposal, user_id=str(state.get("user_id", "")), session_id=str(state.get("session_id", "")), profile_id="customer_ceshi")
                update.update({"phase": "finalize", "candidate_answer": observation.facts[0] if observation.status == "success" and observation.facts else "该写入请求尚未执行；需要明确确认或补充信息。", "degrade_reason": "write_confirmation_required", "observations": list(state.get("observations", [])) + [observation.model_dump()]})
        return update

    def execute(state: CustomerCeshiV2State):
        decision = AgentDecision.model_validate(state.get("decision", {}))
        observations = list(state.get("observations", []))
        calls = list(state.get("generated_tool_calls", []))
        metrics = dict(state.get("metrics", {}))
        fingerprints = dict(state.get("tool_fingerprints", {}))
        media_calls = int(state.get("media_call_count", 0))
        tool_calls = int(state.get("tool_call_count", 0))
        confirmed_context = dict(state.get("confirmed_context", {}))
        media_tool_names = {"inspect_media", "inspect_media_region", "inspect_video_segment"}
        media_tool_calls = [call for call in decision.tool_calls if call.name in media_tool_names]
        if decision.action == "call_tools" and (decision.media_requests or media_tool_calls):
            requested = list(decision.media_requests)
            for call in media_tool_calls:
                asset_ids = [str(call.arguments.get("asset_id", ""))] if call.arguments.get("asset_id") else [str(item.get("asset_id", "")) for item in state.get("media_assets", [])]
                requested.extend(
                    InspectMediaRequest(
                        asset_id=asset_id,
                        objective=str(call.arguments.get("objective") or decision.perception_goal or "Inspect the attachment for relevant observed facts."),
                        mode="targeted_verify" if call.name == "inspect_media_region" else "visual_detail",
                        region=call.arguments.get("region") if isinstance(call.arguments.get("region"), dict) else None,
                    )
                    for asset_id in asset_ids
                    if asset_id
                )
            if not requested:
                requested = [InspectMediaRequest(asset_id=asset_id, objective=decision.perception_goal or "Inspect the attachment for relevant observed facts.") for asset_id in decision.asset_ids]
            if not requested:
                requested = [InspectMediaRequest(asset_id=item["asset_id"], objective=decision.perception_goal or "Inspect the attachment for relevant observed facts.") for item in state.get("media_assets", [])]
            unique_requested: list[InspectMediaRequest] = []
            seen_media_requests: set[tuple[str, str, str]] = set()
            for request in requested:
                identity = (request.asset_id, request.mode, request.objective)
                if identity not in seen_media_requests:
                    seen_media_requests.add(identity)
                    unique_requested.append(request)
            requested = unique_requested
            allowed = requested[:max(0, max_media_calls - media_calls)]
            if not allowed:
                observations.append({"status": "forbidden", "capability": "inspect_media", "warnings": ["Media-call budget exhausted."]})
            else:
                media_observations, cache_hits = perception_service.inspect([MediaAsset.model_validate(item) for item in state.get("media_assets", [])], allowed)
                for request, observation in zip(allowed, media_observations):
                    dumped = observation.model_dump()
                    dumped["data"] = {**dumped.get("data", {}), "asset_id": request.asset_id}
                    observations.append(dumped)
                successful_asset_ids = {
                    request.asset_id
                    for request, observation in zip(allowed, media_observations)
                    if observation.status == "success"
                }
                current_assets = {str(item.get("asset_id", "")): item for item in state.get("media_assets", [])}
                for asset_id in successful_asset_ids:
                    if asset_id in current_assets:
                        confirmed_context["last_media_asset"] = dict(current_assets[asset_id])
                actual_media_calls = int(getattr(perception_service, "last_call_count", len(allowed)))
                media_calls += actual_media_calls
                metrics["media_calls"] = int(metrics.get("media_calls", 0)) + actual_media_calls
                metrics["cache_hits"] = int(metrics.get("cache_hits", 0)) + cache_hits
        if decision.action == "call_tools":
            actionable_tool_calls = [call for call in decision.tool_calls if call.name not in media_tool_names]
            for call in actionable_tool_calls[:max(0, min(3, max_tool_calls - tool_calls))]:
                fingerprint = f"{call.name}:{sorted(call.arguments.items())}"
                count = int(fingerprints.get(fingerprint, 0))
                if count >= 2:
                    observations.append({"status": "forbidden", "capability": call.name, "warnings": ["Identical call circuit breaker opened."], "data": {"arguments": call.arguments}})
                    continue
                observation = registry.invoke(ToolCall.model_validate(call))
                dumped = observation.model_dump()
                dumped["data"] = {**dumped.get("data", {}), "arguments": call.arguments}
                observations.append(dumped)
                calls.append(call.name)
                fingerprints[fingerprint] = count + 1
                tool_calls += 1
                if observation.status in {"temporary_error", "timeout"} and observation.retry_allowed and count == 0 and tool_calls < max_tool_calls:
                    retry = registry.invoke(ToolCall.model_validate(call))
                    retry_dumped = retry.model_dump()
                    retry_dumped["data"] = {**retry_dumped.get("data", {}), "arguments": call.arguments, "retry": 1}
                    observations.append(retry_dumped)
                    calls.append(call.name)
                    fingerprints[fingerprint] = count + 2
                    tool_calls += 1
            metrics["tool_calls"] = int(metrics.get("tool_calls", 0)) + tool_calls - int(state.get("tool_call_count", 0))
        confirmed_context = _extract_confirmed_ship_context(observations, confirmed_context)
        working_memory = dict(state.get("working_memory", {})); working_memory["observations"] = observations; working_memory["known_facts"] = [fact for item in observations if item.get("status") in {"success", "partial"} for fact in item.get("facts", [])]
        diagnostics = dict(state.get("turn_diagnostics", {})); diagnostics["confirmed_entity_available"] = bool(any(confirmed_context.get(key) for key in ("mmsi", "imo", "ship_name"))); diagnostics["current_media_count"] = len(state.get("media_assets", []))
        media_observations = [item for item in observations if item.get("capability") == "inspect_media"]
        if media_observations:
            latest_media_data = dict(media_observations[-1].get("data") or {})
            latest_media_diagnostics = dict(latest_media_data.get("media_diagnostics") or {})
            diagnostics["media_delivery"] = latest_media_diagnostics.get("media_delivery", "")
            diagnostics["media_perception_passes"] = int(latest_media_data.get("media_perception_passes", 0))
        return {"phase": "orchestrate", "observations": observations, "generated_tool_calls": calls, "tool_fingerprints": fingerprints, "media_call_count": media_calls, "tool_call_count": tool_calls, "working_memory": working_memory, "metrics": metrics, "confirmed_context": confirmed_context, "turn_diagnostics": diagnostics}

    def review(state: CustomerCeshiV2State):
        evidence = evidence_reviewer.review(goal=state.get("task_goal", ""), answer=state.get("candidate_answer", ""), claims=state.get("claims", []), observations=state.get("observations", []))
        metrics = dict(state.get("metrics", {})); metrics["evidence_reviews"] = int(metrics.get("evidence_reviews", 0)) + 1
        if evidence.ready or int(state.get("step_count", 0)) >= max_steps:
            working_memory = dict(state.get("working_memory", {})); working_memory["completion_ready"] = evidence.ready; working_memory["completion_reason"] = "claims reviewed"
            return {"phase": "finalize", "evidence_review": evidence.model_dump(), "candidate_answer": evidence.repaired_answer or state.get("candidate_answer", ""), "working_memory": working_memory, "metrics": metrics}
        return {"phase": "orchestrate", "evidence_review": evidence.model_dump(), "candidate_answer": evidence.repaired_answer or state.get("candidate_answer", ""), "metrics": metrics}

    def finalize(state: CustomerCeshiV2State):
        answer = state.get("candidate_answer") or "我暂时无法提供经证据核验的答复。"
        degraded = bool(state.get("degrade_reason"))
        metrics = dict(state.get("metrics", {}))
        started_at_ms = int(state.get("started_at_ms", 0))
        if started_at_ms:
            metrics["elapsed_ms"] = int(time.monotonic() * 1000) - started_at_ms
        state_with_metrics = dict(state)
        state_with_metrics["metrics"] = metrics
        return {"phase": "done", "status": "degraded" if degraded else "success", "generated_answer": answer, "messages": [AIMessage(content=answer)], "metrics": metrics, "route_trace": _redacted_trace(state_with_metrics)}

    def route_after_ingest(state: CustomerCeshiV2State) -> str:
        return "finalize" if state.get("phase") == "finalize" else "orchestrate"

    def route_after_orchestrate(state: CustomerCeshiV2State) -> str:
        return state.get("phase", "finalize")

    graph = StateGraph(CustomerCeshiV2State)
    graph.add_node("ingest", ingest)
    graph.add_node("orchestrate", orchestrate)
    graph.add_node("execute", execute)
    graph.add_node("review", review)
    graph.add_node("finalize", finalize)
    graph.add_edge(START, "ingest")
    graph.add_conditional_edges("ingest", route_after_ingest, {"orchestrate": "orchestrate", "finalize": "finalize"})
    graph.add_conditional_edges("orchestrate", route_after_orchestrate, {"execute": "execute", "review": "review", "finalize": "finalize"})
    graph.add_edge("execute", "orchestrate")
    graph.add_conditional_edges("review", lambda state: "finalize" if state.get("phase") == "finalize" else "orchestrate", {"orchestrate": "orchestrate", "finalize": "finalize"})
    graph.add_edge("finalize", END)
    try:
        checkpointer = get_memory_saver()
    except Exception:
        checkpointer = MemorySaver()
    return _NamespacedGraph(graph.compile(checkpointer=checkpointer))
