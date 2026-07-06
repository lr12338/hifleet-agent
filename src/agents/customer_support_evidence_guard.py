"""Final answer guard for unsupported high-risk platform capability claims."""
from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any

from agents.customer_support_scenarios import DestinationEtaScenario, destination_eta_safe_response


@dataclass
class EvidenceGuardResult:
    text: str
    triggered: bool = False
    blocked_claims: list[str] = field(default_factory=list)
    fallback_reason: str | None = None


HIGH_RISK_CLAIM_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("网页端可编辑", re.compile(r"(网页端|平台|前台|详情页).{0,18}(编辑|修改|更新).{0,18}(目的港|ETA|eta|静态信息)", re.I)),
    ("详情页可点击编辑", re.compile(r"详情页.{0,12}(点击|选择).{0,12}(编辑|修改)", re.I)),
    ("当前页面旁边有编辑按钮", re.compile(r"(旁边|页面|右侧).{0,12}编辑按钮", re.I)),
    ("提交后立即生效", re.compile(r"(提交|保存).{0,8}(立即|马上|实时).{0,8}生效", re.I)),
    ("发送邮件即可自动解析", re.compile(r"(邮件|发邮件|reports@hifleet\.com).{0,24}(自动解析|自动更新|即可更新|可以更新)", re.I)),
    ("reports@hifleet.com 可更新目的港", re.compile(r"reports@hifleet\.com.{0,30}(目的港|destination)", re.I)),
    ("reports@hifleet.com 可更新 ETA", re.compile(r"reports@hifleet\.com.{0,30}(ETA|eta|预抵)", re.I)),
    ("用户可自行修改目的港", re.compile(r"(用户|普通用户|您|自己|自行).{0,18}(修改|更新|编辑).{0,18}目的港", re.I)),
    ("用户可自行修改 ETA", re.compile(r"(用户|普通用户|您|自己|自行).{0,18}(修改|更新|编辑).{0,18}(ETA|eta|预抵)", re.I)),
    ("平台支持普通用户自助修改船舶静态信息", re.compile(r"(普通用户|用户|您).{0,18}(自助|自行).{0,18}(修改|更新|编辑).{0,18}静态信息", re.I)),
]


def _has_verified_evidence(route_trace: dict[str, Any]) -> bool:
    if bool(route_trace.get("verified_evidence")):
        return True
    evidence_guard = route_trace.get("evidence_guard")
    if isinstance(evidence_guard, dict) and evidence_guard.get("verified_evidence"):
        return True
    supported = route_trace.get("supported_claims")
    if supported:
        return True
    reasoning = route_trace.get("reasoning_trace")
    if isinstance(reasoning, dict):
        if reasoning.get("verified_evidence") or reasoning.get("supported_claims"):
            return True
    return False


def apply_high_risk_evidence_guard(
    text: str,
    *,
    route_trace: dict[str, Any] | None = None,
    scenario: str | None = None,
) -> EvidenceGuardResult:
    value = str(text or "")
    trace = dict(route_trace or {})
    blocked = [label for label, pattern in HIGH_RISK_CLAIM_PATTERNS if pattern.search(value)]
    if not blocked:
        return EvidenceGuardResult(text=value)
    if _has_verified_evidence(trace):
        return EvidenceGuardResult(text=value)

    resolved_scenario = DestinationEtaScenario.UNKNOWN
    try:
        if scenario:
            resolved_scenario = DestinationEtaScenario(scenario)
    except ValueError:
        resolved_scenario = DestinationEtaScenario.UNKNOWN
    if any("reports@hifleet.com" in claim or "邮件" in claim for claim in blocked):
        resolved_scenario = DestinationEtaScenario.EMAIL_UPDATE_QUESTION
    fallback = destination_eta_safe_response(resolved_scenario)
    return EvidenceGuardResult(
        text=fallback,
        triggered=True,
        blocked_claims=blocked,
        fallback_reason="unsupported_high_risk_platform_claim",
    )

