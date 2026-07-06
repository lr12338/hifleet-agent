"""High-risk customer-support scenario classifiers."""
from __future__ import annotations

from enum import Enum
import re


class DestinationEtaScenario(str, Enum):
    AIS_DELAY_EXPLANATION = "ais_delay_explanation"
    FRONTEND_CAPABILITY_QUESTION = "frontend_capability_question"
    BACKEND_UPDATE_REQUEST = "backend_update_request"
    EMAIL_UPDATE_QUESTION = "email_update_question"
    UNKNOWN = "unknown"


DESTINATION_ETA_MARKERS = ("目的港", "eta", "ETA", "预抵", "预计到达", "destination", "dest")


def mentions_destination_eta(text: str) -> bool:
    value = str(text or "")
    return any(marker in value for marker in DESTINATION_ETA_MARKERS)


def classify_destination_eta_scenario(text: str) -> DestinationEtaScenario:
    value = str(text or "")
    lowered = value.lower()
    if not mentions_destination_eta(value) and "reports@hifleet.com" not in lowered:
        return DestinationEtaScenario.UNKNOWN

    if "reports@hifleet.com" in lowered or any(marker in value for marker in ("邮件", "发邮件", "邮箱")):
        return DestinationEtaScenario.EMAIL_UPDATE_QUESTION

    if any(marker in value for marker in ("为什么", "没更新", "不更新", "一直显示", "显示旧", "没变", "不同步", "滞后", "延迟")):
        return DestinationEtaScenario.AIS_DELAY_EXPLANATION

    frontend_markers = (
        "怎么在平台",
        "如何在平台",
        "平台手动",
        "自己改",
        "自行",
        "网页端",
        "前台",
        "入口",
        "按钮",
        "手动上传",
        "手动更新",
        "操作流程",
        "怎么操作",
    )
    if any(marker in value for marker in frontend_markers):
        return DestinationEtaScenario.FRONTEND_CAPABILITY_QUESTION

    backend_action = (
        "帮我",
        "帮忙",
        "客服",
        "后台",
        "改成",
        "修改为",
        "更新为",
        "目的港改",
        "eta改",
        "ETA改",
    )
    has_identifier = bool(re.search(r"\b\d{9}\b|\b\d{7}\b|mmsi|imo|MMSI|IMO", value))
    if any(marker in value for marker in backend_action) or (has_identifier and any(marker in value for marker in ("更新", "修改", "改成"))):
        return DestinationEtaScenario.BACKEND_UPDATE_REQUEST

    return DestinationEtaScenario.UNKNOWN


def destination_eta_safe_response(scenario: DestinationEtaScenario) -> str:
    if scenario == DestinationEtaScenario.EMAIL_UPDATE_QUESTION:
        return (
            "目前不应将 reports@hifleet.com 描述为“文本邮件自动更新目的港/ETA”的入口。"
            "该邮箱用途需要以官方文档为准；如无明确证据，不建议通过发送文本邮件更新目的港或 ETA。"
            "若需要处理目的港/ETA，请提供船舶 MMSI 和最新信息，由客服协助核实。"
        )
    if scenario == DestinationEtaScenario.AIS_DELAY_EXPLANATION:
        return (
            "目的港和 ETA 属于 AIS 静态信息，更新频率通常低于动态船位，平台展示可能存在滞后。"
            "如果船上已经修改但 HiFleet 仍显示旧值，请提供船舶 MMSI、当前显示值和最新目的港/ETA，"
            "我们可以协助核实数据来源并交由客服或工作人员进一步处理。"
        )
    return (
        "目前我没有查到 HiFleet 前台向普通用户开放“自助编辑船舶目的港/ETA”的明确入口。"
        "目的港和 ETA 属于 AIS 静态信息，更新频率通常低于动态船位，平台展示可能存在滞后。"
        "若需要协助核实或强制更新，请提供船舶 MMSI、最新目的港和 ETA，我们可以交由客服或工作人员进一步处理。"
    )

