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
    notes: str | None = None


def build_customer_understanding(
    text: str,
    *,
    entities: dict[str, Any] | None = None,
    has_media: bool = False,
    perception: dict[str, Any] | None = None,
    pending_update_state: dict[str, Any] | None = None,
) -> CustomerUnderstanding:
    value = str(text or "")
    pending = dict(pending_update_state or {})
    active_pending = bool(pending.get("active") and pending.get("can_resume") and pending.get("status") not in {"executed_success", "cancelled", "expired"})
    contract = extract_ship_update_parameters_with_agent(value, perception)
    operation_type = _normalize_operation_type(contract.operation_type)
    if not active_pending and _is_confirmation_like(value):
        operation_type = "none"
    if _is_non_write_ship_update_capability_question(value):
        operation_type = "frontend_capability_question"
    elif _is_ship_query_request(value):
        operation_type = "ship_query"
    scenario = classify_destination_eta_scenario(value)
    frontend_question = operation_type == "frontend_capability_question" or scenario == DestinationEtaScenario.FRONTEND_CAPABILITY_QUESTION
    backend_request = scenario == DestinationEtaScenario.BACKEND_UPDATE_REQUEST or operation_type in SHIP_UPDATE_OPERATION_TYPES
    email_question = scenario == DestinationEtaScenario.EMAIL_UPDATE_QUESTION
    ship_data_issue = operation_type == "data_delay_troubleshooting" or scenario == DestinationEtaScenario.AIS_DELAY_EXPLANATION
    non_write_reason = "frontend_capability_question" if frontend_question or email_question else "data_delay_troubleshooting" if ship_data_issue else "none"
    ship_write = operation_type in SHIP_UPDATE_OPERATION_TYPES and non_write_reason == "none" and _is_explicit_ship_write_request(value, operation_type, bool(perception))
    knowledge_question = not ship_write or frontend_question or email_question or ship_data_issue
    required_fields: list[str] = []
    if ship_write:
        required_fields = ["mmsi", "longitude", "latitude", "updatetime"]
        if operation_type == "static_update":
            required_fields = ["mmsi", "destination_or_eta"]

    risk_level = "high" if email_question or frontend_question else "medium" if ship_write or backend_request or ship_data_issue else "low"
    evidence_required = frontend_question or email_question or any(
        marker in value for marker in ("支持", "入口", "按钮", "立即生效", "自动解析", "网页端", "前台")
    )
    task_type = "platform_capability" if frontend_question or email_question else "ship_update" if ship_write else "ship_data_issue" if ship_data_issue else "knowledge"
    intent = "ship_update" if ship_write else "knowledge"
    notes = ""
    if has_media:
        notes = "当前请求包含多模态内容，理解层记录任务语义和候选字段，船舶更新由 ship_update 子 agent 生成工具计划。"

    action_allowed = ship_write and not frontend_question and not email_question and not ship_data_issue
    pending_action = _infer_pending_action(
        value,
        pending=pending,
        operation_type=operation_type,
        non_write_reason=non_write_reason,
    )
    ship_identity = dict(contract.ship_identity or {})
    for key in ("mmsi", "imo", "ship_name"):
        if contract.fields.get(key) and not ship_identity.get(key):
            ship_identity[key] = contract.fields[key]
    ship_update_confidence = "high" if ship_write and contract.fields else "medium" if ship_write or operation_type in NON_WRITE_OPERATION_TYPES else "low"
    return CustomerUnderstanding(
        intent=intent,
        task_type=task_type,
        user_goal=value.strip(),
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
        scenario=scenario.value if scenario != DestinationEtaScenario.UNKNOWN else None,
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
