"""Structured understanding helpers for customer_support."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from agents.customer_support_scenarios import (
    DestinationEtaScenario,
    classify_destination_eta_scenario,
    mentions_destination_eta,
)


class CustomerUnderstanding(BaseModel):
    intent: str | None = None
    task_type: str | None = None
    user_goal: str | None = None

    frontend_capability_question: bool = False
    backend_action_request: bool = False
    ship_data_issue: bool = False
    ship_write_request: bool = False
    knowledge_question: bool = False

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
) -> CustomerUnderstanding:
    value = str(text or "")
    scenario = classify_destination_eta_scenario(value)
    lower = value.lower()
    is_destination_eta = scenario != DestinationEtaScenario.UNKNOWN or mentions_destination_eta(value)
    frontend_question = scenario == DestinationEtaScenario.FRONTEND_CAPABILITY_QUESTION
    backend_request = scenario == DestinationEtaScenario.BACKEND_UPDATE_REQUEST
    email_question = scenario == DestinationEtaScenario.EMAIL_UPDATE_QUESTION
    ship_data_issue = scenario == DestinationEtaScenario.AIS_DELAY_EXPLANATION
    ship_write = backend_request or (
        any(marker in value for marker in ("更新船位", "上传船位", "修改船位", "补录船位"))
        and not any(marker in value for marker in ("为什么", "怎么", "如何", "更新慢", "不更新", "不显示"))
    )
    knowledge_question = not ship_write or frontend_question or email_question or ship_data_issue
    required_fields: list[str] = []
    if ship_write:
        required_fields = ["mmsi", "longitude", "latitude", "updatetime"]
        if is_destination_eta:
            required_fields = ["mmsi", "destination_or_eta"]

    risk_level = "high" if is_destination_eta or email_question or frontend_question else "medium" if ship_write else "low"
    evidence_required = frontend_question or email_question or any(
        marker in value for marker in ("支持", "入口", "按钮", "立即生效", "自动解析", "网页端", "前台")
    )
    task_type = "platform_capability" if frontend_question or email_question else "ship_update" if ship_write else "ship_data_issue" if ship_data_issue else "knowledge"
    intent = "ship_update" if ship_write else "knowledge"
    notes = ""
    if has_media:
        notes = "当前请求包含多模态内容，理解层仅记录任务语义，字段抽取由写入预检处理。"
    if is_destination_eta:
        notes = (notes + " " if notes else "") + "目的港/ETA 属高风险边界场景，需要区分前台功能咨询与后台代操作。"

    action_allowed = ship_write and not frontend_question and not email_question and not ship_data_issue
    return CustomerUnderstanding(
        intent=intent,
        task_type=task_type,
        user_goal=value.strip(),
        frontend_capability_question=frontend_question,
        backend_action_request=backend_request,
        ship_data_issue=ship_data_issue,
        ship_write_request=ship_write,
        knowledge_question=knowledge_question,
        entities=dict(entities or {}),
        required_fields=required_fields,
        missing_fields=[],
        risk_level=risk_level,
        evidence_required=evidence_required,
        action_allowed=action_allowed,
        scenario=scenario.value if scenario != DestinationEtaScenario.UNKNOWN else None,
        notes=notes,
    )

