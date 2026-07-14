"""Structured understanding helpers for customer_support."""
from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field

from agents.customer_support_scenarios import (
    DestinationEtaScenario,
    classify_destination_eta_scenario,
)
from agents.ship_update_extractor import extract_ship_update_parameters_with_agent


SHIP_UPDATE_OPERATION_TYPES = {"position_update", "static_update", "mixed_update", "ambiguous_update"}
NON_WRITE_OPERATION_TYPES = {"frontend_capability_question", "data_delay_troubleshooting", "ship_query", "none"}
VALID_OPERATION_TYPES = SHIP_UPDATE_OPERATION_TYPES | NON_WRITE_OPERATION_TYPES | {"unknown"}
VALID_PENDING_ACTIONS = {"resume", "hold", "cancel", "pause", "none"}


class CustomerUnderstanding(BaseModel):
    intent: str | None = None
    task_type: str | None = None
    user_goal: str | None = None
    target_object: str = ""
    question_type: str = ""

    frontend_capability_question: bool = False
    backend_action_request: bool = False
    ship_data_issue: bool = False
    ship_write_request: bool = False
    knowledge_question: bool = False

    operation_type: str = "none"
    ship_update_candidate: bool = False
    pending_action: str = "none"
    non_write_reason: str = "none"
    ship_identity: dict[str, Any] = Field(default_factory=dict)
    ship_update_fields: dict[str, Any] = Field(default_factory=dict)
    ship_update_confidence: str = "low"

    entities: dict[str, Any] = Field(default_factory=dict)
    required_fields: list[str] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)

    risk_level: str = "low"
    evidence_required: bool = False
    action_allowed: bool = False

    scenario: str | None = None
    multimodal_scenario: str | None = None
    business_scenario: str | None = None
    needs_visual_grounding: bool = False
    needs_product_knowledge: bool = False
    needs_public_evidence: bool = False
    needs_ship_data: bool = False
    needs_backend_diagnosis: bool = False
    is_write_request: bool = False
    ship_identities: list[dict[str, Any]] = Field(default_factory=list)
    required_claims: list[str] = Field(default_factory=list)
    missing_slot: dict[str, str] = Field(default_factory=dict)
    confidence: str = "low"
    reason_summary: str = ""
    notes: str | None = None


MULTIMODAL_SCENARIOS = {
    "chart_symbol_explanation",
    "platform_ui_explanation",
    "platform_metric_definition",
    "platform_troubleshooting",
    "ship_tracking_incident",
    "ship_query_from_media",
    "ship_update_from_media",
    "file_or_document_task",
    "audio_request",
    "video_request",
    "general_multimodal_question",
    "ambiguous_multimodal",
}


def _is_file_document_task_request(text: str) -> bool:
    """Return whether the customer asks to process an attached document.

    An upload-error screenshot alone remains a troubleshooting request. This
    guard only promotes a mixed attachment turn when the customer explicitly
    asks to inspect, analyze, or transform the actual file.
    """
    value = str(text or "").lower()
    task_markers = ("分析", "读取", "处理", "统计", "汇总", "提取", "整理", "计算", "生成报告", "生成报表")
    file_markers = ("文件", "表格", "数据", "excel", "csv", "pdf", "xlsx", "xls", "文档")
    return any(marker in value for marker in task_markers) and any(marker in value for marker in file_markers)


def classify_multimodal_scenario(
    text: str,
    perception: dict[str, Any] | None = None,
    *,
    has_media: bool = False,
    has_file_attachment: bool = False,
) -> str:
    """Classify the customer's goal, not merely the attachment's visible content."""
    if not has_media:
        return ""
    value = str(text or "")
    lower = value.lower()
    perception = dict(perception or {})
    attachment_type = str(perception.get("attachment_type") or "").lower()
    visible = " ".join(
        str(perception.get(key) or "")
        for key in ("recognized_text", "visible_text", "summary", "visible_features", "suspected_issue")
    ).lower()
    has_ship_identity = bool(
        any(
            str(entity.get(key) or "").strip()
            for entity in list(perception.get("ship_entities") or [])
            if isinstance(entity, dict)
            for key in ("name", "mmsi", "imo")
        )
    )
    if attachment_type == "file" or (has_file_attachment and _is_file_document_task_request(value)):
        return "file_or_document_task"
    if attachment_type == "audio":
        return "audio_request"
    if attachment_type == "video":
        return "video_request"
    if any(marker in lower + visible for marker in ("报错", "加载失败", "打不开", "无反应", "上传失败", "数据不显示", "error", "failed", "failure")):
        return "platform_troubleshooting"
    if any(marker in lower for marker in ("更新", "上传", "修改", "补录", "改为")):
        return "ship_update_from_media" if any(marker in lower + visible for marker in ("船位", "mmsi", "imo", "目的港", "eta", "船型")) else "general_multimodal_question"
    if any(marker in lower for marker in ("后台看看", "没有船位", "船位不更新", "ais正常", "ais 正常", "跟踪异常")):
        return "ship_tracking_incident"
    if any(marker in lower for marker in ("平均航速", "总里程", "在线时间", "统计口径", "是否包含", "怎么算")):
        return "platform_metric_definition"
    if any(marker in lower for marker in ("查询船位", "查船", "查一下", "轨迹", "档案", "psc", "现在在哪里", "当前位置")) and (has_ship_identity or any(marker in visible for marker in ("mmsi", "imo", "船名"))):
        return "ship_query_from_media"
    if any(marker in lower for marker in ("海图", "符号", "图标", "图中", "标志", "紫色", "小圈圈")):
        return "chart_symbol_explanation"
    if any(marker in lower for marker in ("按钮", "字段", "状态", "这个页面", "这个数值", "是什么意思", "怎么用")) and any(marker in visible for marker in ("按钮", "字段", "页面", "hifleet")):
        return "platform_ui_explanation"
    if not value.strip() and str(perception.get("confidence") or "low").lower() == "low":
        return "ambiguous_multimodal"
    return "general_multimodal_question"


def classify_multimodal_business_scenario(text: str, perception: dict[str, Any] | None = None, *, has_media: bool = False) -> str:
    """Classify the actual support task contained in an audio/video envelope.

    ``audio_request`` and ``video_request`` remain the observable media
    scenarios. When a transcript or key-frame summary makes the customer's
    business task clear, this helper supplies the deterministic inner route.
    """
    perception = dict(perception or {})
    envelope_type = str(perception.get("attachment_type") or "").lower()
    if envelope_type not in {"audio", "video"}:
        return ""
    media_text = " ".join(
        str(perception.get(key) or "")
        for key in ("audio_transcript", "video_summary", "recognized_text", "visible_text", "summary", "suspected_issue")
    ).strip()
    if not media_text:
        return ""
    derived_perception = {**perception, "attachment_type": "image"}
    result = classify_multimodal_scenario(f"{text}\n{media_text}".strip(), derived_perception, has_media=True)
    return "" if result in {"general_multimodal_question", "ambiguous_multimodal"} else result


def _audio_user_instruction(text: str, perception: dict[str, Any] | None) -> str:
    """Treat a speech transcript as customer text, never a video summary.

    An audio transcript represents the customer's own request and can therefore
    satisfy the explicit-command part of a write request. A video/key-frame
    summary only describes observed content and must not authorize a write.
    """
    value = str(text or "").strip()
    perception = dict(perception or {})
    if str(perception.get("attachment_type") or "").lower() != "audio":
        return value
    transcript = str(perception.get("audio_transcript") or perception.get("recognized_text") or "").strip()
    return "\n".join(part for part in (value, transcript) if part)


def build_customer_understanding(
    text: str,
    *,
    entities: dict[str, Any] | None = None,
    has_media: bool = False,
    has_file_attachment: bool = False,
    perception: dict[str, Any] | None = None,
    pending_update_state: dict[str, Any] | None = None,
) -> CustomerUnderstanding:
    value = str(text or "")
    semantic_value = _audio_user_instruction(value, perception)
    pending = dict(pending_update_state or {})
    active_pending = bool(pending.get("active") and pending.get("can_resume") and pending.get("status") not in {"executed_success", "cancelled", "expired"})
    contract = extract_ship_update_parameters_with_agent(semantic_value, perception)
    operation_type = _normalize_operation_type(contract.operation_type)
    if not active_pending and _is_confirmation_like(semantic_value):
        operation_type = "none"
    if _is_non_write_ship_update_capability_question(semantic_value):
        operation_type = "frontend_capability_question"
    elif _is_ship_query_request(semantic_value):
        operation_type = "ship_query"
    scenario = classify_destination_eta_scenario(semantic_value)
    frontend_question = operation_type == "frontend_capability_question" or scenario == DestinationEtaScenario.FRONTEND_CAPABILITY_QUESTION
    backend_request = scenario == DestinationEtaScenario.BACKEND_UPDATE_REQUEST or operation_type in SHIP_UPDATE_OPERATION_TYPES
    email_question = scenario == DestinationEtaScenario.EMAIL_UPDATE_QUESTION
    ship_data_issue = operation_type == "data_delay_troubleshooting" or scenario == DestinationEtaScenario.AIS_DELAY_EXPLANATION
    non_write_reason = "frontend_capability_question" if frontend_question or email_question else "data_delay_troubleshooting" if ship_data_issue else "none"
    ship_write = operation_type in SHIP_UPDATE_OPERATION_TYPES and non_write_reason == "none" and _is_explicit_ship_write_request(semantic_value, operation_type, bool(perception))
    knowledge_question = not ship_write or frontend_question or email_question or ship_data_issue
    required_fields: list[str] = []
    if ship_write:
        required_fields = ["mmsi", "longitude", "latitude", "updatetime"]
        if operation_type == "static_update":
            required_fields = ["mmsi", "destination_or_eta"]

    risk_level = "high" if email_question or frontend_question else "medium" if ship_write or backend_request or ship_data_issue else "low"
    evidence_required = frontend_question or email_question or any(
        marker in semantic_value for marker in ("支持", "入口", "按钮", "立即生效", "自动解析", "网页端", "前台")
    )
    task_type = "platform_capability" if frontend_question or email_question else "ship_update" if ship_write else "ship_data_issue" if ship_data_issue else "knowledge"
    intent = "ship_update" if ship_write else "knowledge"
    notes = ""
    if has_media:
        notes = "当前请求包含多模态内容，理解层记录任务语义和候选字段，船舶更新由 ship_update 子 agent 生成工具计划。"

    action_allowed = ship_write and not frontend_question and not email_question and not ship_data_issue
    multimodal_scenario = classify_multimodal_scenario(
        semantic_value,
        perception,
        has_media=has_media,
        has_file_attachment=has_file_attachment,
    )
    business_scenario = classify_multimodal_business_scenario(semantic_value, perception, has_media=has_media)
    effective_scenario = business_scenario or multimodal_scenario
    if effective_scenario == "ship_tracking_incident":
        task_type, intent, ship_data_issue, knowledge_question = "ship_tracking_incident", "troubleshooting", True, True
        non_write_reason, action_allowed, ship_write, operation_type = "data_delay_troubleshooting", False, False, "data_delay_troubleshooting"
    elif effective_scenario == "platform_troubleshooting":
        task_type, intent, knowledge_question, operation_type = "platform_troubleshooting", "troubleshooting", True, "none"
    elif effective_scenario in {
        "chart_symbol_explanation",
        "platform_ui_explanation",
        "platform_metric_definition",
        "ship_query_from_media",
        "file_or_document_task",
        "audio_request",
        "video_request",
        "general_multimodal_question",
        "ambiguous_multimodal",
    }:
        # A generic update extractor may see labels in a screenshot. Those labels
        # must not remain as an update-operation candidate when the user's goal
        # is demonstrably a non-write multimodal scenario.
        operation_type, ship_write, action_allowed = "none", False, False
        backend_request = False
    required_claims = []
    if effective_scenario in {"platform_ui_explanation", "platform_troubleshooting"}:
        required_claims.append("hifleet_product_evidence")
    if effective_scenario == "platform_metric_definition":
        required_claims.extend(
            [
                "metric_denominator_time",
                "metric_stationary_or_anchored_time",
                "metric_port_low_speed_time",
                "metric_vs_voyage_average_definition",
            ]
        )
    if effective_scenario == "chart_symbol_explanation":
        required_claims.append("visual_and_chart_evidence")
    if effective_scenario == "ship_tracking_incident":
        required_claims.extend(["ship_identity", "last_position_evidence", "incident_packet"])
    ship_identities = [dict(item) for item in list((perception or {}).get("ship_entities") or []) if isinstance(item, dict)]
    target_object = ""
    if ship_identities:
        target_object = str(ship_identities[0].get("name") or ship_identities[0].get("mmsi") or "")
    elif effective_scenario == "platform_metric_definition":
        target_object = "平均航速"
    elif effective_scenario == "chart_symbol_explanation":
        target_object = "截图中的海图/图层符号"
    question_type = {
        "chart_symbol_explanation": "symbol_meaning",
        "platform_ui_explanation": "ui_explanation",
        "platform_metric_definition": "metric_definition",
        "platform_troubleshooting": "troubleshooting",
        "ship_tracking_incident": "tracking_incident",
        "ship_query_from_media": "ship_query",
        "ship_update_from_media": "ship_update",
    }.get(effective_scenario, "multimodal_question")
    confidence = str((perception or {}).get("confidence") or "low").lower()
    reason_summary = f"用户文字决定场景为 {effective_scenario or multimodal_scenario or 'general_multimodal_question'}；附件仅提供对象和可见证据。"
    pending_action = _infer_pending_action(
        semantic_value,
        pending=pending,
        operation_type=operation_type,
        non_write_reason=non_write_reason,
    )
    ship_identity = dict(contract.ship_identity or {})
    for key in ("mmsi", "imo", "ship_name"):
        if contract.fields.get(key) and not ship_identity.get(key):
            ship_identity[key] = contract.fields[key]
    ship_update_confidence = "high" if ship_write and contract.fields else "medium" if ship_write or operation_type in (NON_WRITE_OPERATION_TYPES - {"none"}) else "low"
    return CustomerUnderstanding(
        intent=intent,
        task_type=task_type,
        user_goal=semantic_value.strip(),
        frontend_capability_question=frontend_question,
        backend_action_request=backend_request,
        ship_data_issue=ship_data_issue,
        ship_write_request=ship_write,
        knowledge_question=knowledge_question,
        operation_type=operation_type if operation_type != "unknown" else "none",
        ship_update_candidate=ship_write,
        pending_action=pending_action,
        non_write_reason=non_write_reason,
        ship_identity=ship_identity,
        ship_update_fields=dict(contract.fields or {}),
        ship_update_confidence=ship_update_confidence,
        entities=dict(entities or {}),
        required_fields=required_fields,
        missing_fields=list(contract.missing_fields or []),
        risk_level=risk_level,
        evidence_required=evidence_required,
        action_allowed=action_allowed,
        scenario=effective_scenario or multimodal_scenario or (scenario.value if scenario != DestinationEtaScenario.UNKNOWN else None),
        multimodal_scenario=multimodal_scenario or None,
        business_scenario=business_scenario or None,
        target_object=target_object,
        question_type=question_type,
        needs_visual_grounding=bool(has_media),
        needs_product_knowledge=effective_scenario in {"platform_ui_explanation", "platform_metric_definition", "platform_troubleshooting"},
        needs_public_evidence=bool(evidence_required or effective_scenario in {"chart_symbol_explanation", "platform_metric_definition"}),
        needs_ship_data=effective_scenario in {"ship_tracking_incident", "ship_query_from_media"},
        needs_backend_diagnosis=effective_scenario == "ship_tracking_incident",
        is_write_request=bool(ship_write),
        ship_identities=ship_identities,
        required_claims=required_claims,
        confidence=confidence if confidence in {"high", "medium", "low"} else "low",
        reason_summary=reason_summary,
        notes=notes,
    )


def _normalize_operation_type(value: Any) -> str:
    operation_type = str(value or "none").strip().lower()
    if operation_type == "unknown":
        return "none"
    return operation_type if operation_type in VALID_OPERATION_TYPES else "none"


def _is_explicit_ship_write_request(text: str, operation_type: str, has_perception: bool) -> bool:
    value = str(text or "")
    lowered = value.lower()
    if operation_type not in SHIP_UPDATE_OPERATION_TYPES:
        return False
    if _is_non_write_ship_update_capability_question(value) or _is_ship_query_request(value):
        return False
    write_markers = ("更新", "上传", "修改", "补录", "改为", "update")
    if any(marker in lowered for marker in write_markers):
        return True
    if operation_type == "static_update" and any(marker in value for marker in ("错误", "有误", "不对", "错了")):
        return True
    return has_perception and operation_type in {"position_update", "static_update"} and any(marker in value for marker in ("船位", "目的港", "ETA", "eta", "静态"))


def _is_non_write_ship_update_capability_question(text: str) -> bool:
    value = str(text or "")
    if not any(marker in value for marker in ("吗", "怎么", "如何", "怎样", "能不能", "是否", "可以", "支持", "入口", "按钮", "邮件", "邮箱", "平台", "前台")):
        return False
    return any(marker in value for marker in ("更新船舶数据", "更新船位", "更新目的港", "更新ETA", "更新 eta", "修改目的港", "编辑目的港", "船舶数据"))


def _is_ship_query_request(text: str) -> bool:
    value = str(text or "")
    if any(marker in value for marker in ("更新", "上传", "修改", "补录", "改为", "update")):
        return False
    return any(marker in value for marker in ("查询", "查一下", "船位", "位置", "轨迹", "档案", "MMSI", "mmsi", "IMO", "imo"))


def _is_confirmation_like(text: str) -> bool:
    normalized = re.sub(r"\s+", "", str(text or ""), flags=re.UNICODE).lower()
    if not normalized or any(marker in normalized for marker in ("取消", "不用", "不要", "先不", "别")):
        return False
    if normalized in {"确认", "确认更新", "确认执行", "确认提交", "确定", "是的", "对", "可以", "继续", "继续更新", "好的", "好", "ok", "yes", "按上述参数更新", "按照上述参数更新", "按上面参数更新", "按这个参数更新"}:
        return True
    return bool(re.fullmatch(r"(请)?确认(更新|执行|提交)?(该)?(mmsi)?", normalized, flags=re.IGNORECASE))


def _infer_pending_action(
    text: str,
    *,
    pending: dict[str, Any],
    operation_type: str,
    non_write_reason: str,
) -> str:
    if not pending.get("active") or not pending.get("can_resume") or pending.get("status") in {"executed_success", "cancelled", "expired"}:
        return "none"
    normalized = str(text or "").strip()
    compact = re.sub(r"\s+", "", normalized, flags=re.UNICODE)
    if any(marker in normalized for marker in ("取消更新", "不用更新", "取消", "先不更新")):
        return "cancel"
    if non_write_reason != "none":
        return "pause"
    status = str(pending.get("status") or "")
    if status in {"awaiting_ship_identity", "awaiting_required_fields"} and re.fullmatch(r"\d{9}", compact):
        return "resume"
    if status == "awaiting_mmsi_confirmation" and _is_confirmation_like(normalized):
        return "resume"
    if status == "awaiting_field_confirmation":
        conflict_fields = [str(item or "") for item in list(pending.get("conflict_fields") or [])]
        if _is_confirmation_like(normalized) or any(field and field in normalized for field in conflict_fields):
            return "resume"
    if operation_type in SHIP_UPDATE_OPERATION_TYPES:
        return "resume"
    return "hold"
