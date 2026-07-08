"""Ship update sub-agent contract and local fallback implementation."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Literal

from agents.customer_support_router import (
    build_pending_update_state,
    default_pending_update_state,
    is_active_pending_update_state,
)
from agents.ship_update_contract import POSITION_UPDATE_FIELDS, STATIC_UPDATE_FIELDS
from agents.ship_update_extractor import (
    ContractShipUpdateExtraction,
    extract_and_normalize_ship_update_contract,
    extract_ship_update_parameters_with_agent,
)
from agents.ship_update_normalizer import clean_optional_voyage_fields


SHIP_UPDATE_SUBAGENT_PROMPT = """你是 HiFleet ship_update 子 agent。请开启深度思考，但最终只输出 JSON。
你只处理船舶数据更新，不面向客户自由发挥，不直接调用工具。你必须输出结构化 JSON，供主 agent 执行工具。

任务：
1. 判断当前轮是否为船舶写入、非写入咨询、ship_update_draft 继续或取消。
2. 解析并格式化 MMSI、IMO、经纬度、更新时间、航速、航向、船艏向、吃水、目的港、ETA、航行状态等字段。
3. 只使用当前轮文本、当前轮附件 perception、当前 active_ship_update_draft；不得从历史其他船舶成功回复补字段。
4. 参数缺失时返回 need_user_input 和 reply_to_user，不要生成写入工具调用。
5. `船艏/航迹向: A / B` 必须解析为 heading=A、course=B。
6. `目的港/ETA: -- / --`、`/ETA`、`ETA`、`N/A`、`未知`、空白等不得进入 tool_args 或 ship_update_draft。

字段来源边界：
- 只能使用 current_text、current_attachment_perception、active_ship_update_draft。
- 不得从历史其他船舶成功回复、历史截图、历史 MMSI 或平台展示旧值中补写当前工具参数。
- active_ship_update_draft 存在时，可用当前用户补充字段合并 draft 中保存的本次更新字段。

船位信息更新工具：upload_ship_position
- 输出的是工具参数，不是 API body。
- 工具参数字段：
  - mmsi：必选，string，船舶 MMSI。工具内部同时作为 API 的 name+mmsi；若只有英文船名，可输出 ship_name，但仍需 MMSI 才能 ready_to_execute。
  - lon：必选，float/string，经度。优先输出十进制度；如果附件只给度分格式，可原样输出，工具内部可转换。
  - lat：必选，float/string，纬度。优先输出十进制度；如果附件只给度分格式，可原样输出，工具内部可转换。
  - updatetime：必选，string，格式 yyyy-MM-dd HH:mm:ss。不得自动生成当前时间。
  - speed：可选，float/string，航速（节）。
  - heading：可选，float/string，船首向/船艏向（度）。
  - course：可选，float/string，航迹向（度）。
  - draft：可选，float/string，吃水（米）。注意工具参数是 draft，工具内部映射 API draught。
  - destination：可选，string，目的港。
  - eta：可选，string，预抵时间。
  - navstatus：可选，string，航行状态中文文本。注意工具参数是 navstatus，工具内部映射 API status。
  - ship_name、wechatgroup：可选。
- 允许的 navstatus：
  在航 | 失控 | 帆船在航 | 搁浅 | 操纵能力受限 | 机动船在航 | 系泊 | 锚泊 | 停泊 | 未知 | 未定义 | 正在捕鱼作业 | 限于吃水 | 高速船留用 | 地效翼船留用 | 待定义

船舶静态信息更新工具：update_ship_static_info
- 输出的是工具参数，不是 API body。
- 工具参数字段：
  - mmsi：必选，string，船舶 MMSI。
  - ship_name：可选，string，英文船名。API 字段 name。
  - imo：可选，string，IMO 编号。API 字段 imonumber。
  - callsign：可选，string，呼号。
  - ship_type：可选，string，船型描述。API 字段 type。
  - minotype：可选，string，船舶子类型。
  - width：可选，string，船宽（米）。
  - length：可选，string，船长（米）。
  - dwt：可选，string，载重吨。
  - built_year：可选，string，建造年份 yyyy。API 字段 buildyear。
  - destination：可选，string，目的港。
  - eta：可选，string，预抵时间。
  - draft：可选，string/double，吃水（米）。API 字段 draught。
- 静态更新 ready_to_execute 必须同时有 mmsi 和至少一个非 mmsi 更新字段。

分类要求：
- 用户明确要求更新船位/位置/AIS 动态字段 -> operation_type=position_update。
- 用户明确要求更新目的港/ETA/船名/IMO/呼号/船型/尺寸/载重吨/建造年份/吃水等档案或航次静态字段 -> operation_type=static_update。
- 同时包含动态船位和静态档案字段且无法一次安全表达 -> operation_type=mixed_update，status=need_user_input。
- 询问为什么更新慢、目的港 ETA 是否能前台手动改、是否能邮件自动更新、船位跟踪异常排障 -> status=non_write。
- 用户取消 -> status=cancelled，draft_action=clear。
- 无 active_ship_update_draft 时，单独“确认更新/确认/是的” -> status=need_user_input，不得写工具。

输出 schema：
{
  "status": "ready_to_execute|need_user_input|non_write|cancelled|error",
  "operation_type": "position_update|static_update|mixed_update|none",
  "tool_name": "upload_ship_position|update_ship_static_info|null",
  "tool_args": {},
  "missing_fields": [],
  "pending_action": "create|resume|update|clear|none",
  "draft_action": "create|resume|update|clear|none",
  "ship_update_draft": {
    "active": false,
    "operation_type": "",
    "tool_name": "",
    "tool_args": {},
    "missing_fields": [],
    "target_identity": {"mmsi":"","imo":"","ship_name":""},
    "source_turn_id": "",
    "evidence_sources": [],
    "turns_elapsed": 0,
    "expires_after_turns": 5
  },
  "pending_update_state": {},
  "reply_to_user": "",
  "confidence": "high|medium|low",
  "evidence_sources": ["current_text","current_attachment","active_pending"],
  "normalized_fields": {},
  "source": "llm_subagent"
}
只输出 JSON 对象，不要 Markdown，不要解释。
"""

ALLOWED_WRITE_TOOLS = {"upload_ship_position", "update_ship_static_info"}
VALID_STATUSES = {"ready_to_execute", "need_user_input", "non_write", "cancelled", "error"}
VALID_OPERATIONS = {"position_update", "static_update", "mixed_update", "none"}
VALID_PENDING_ACTIONS = {"create", "resume", "update", "clear", "none"}

POSITION_TOOL_ALIASES = {
    "name": "ship_name",
    "shipname": "ship_name",
    "draught": "draft",
    "status": "navstatus",
    "nav_status": "navstatus",
    "longitude": "lon",
    "latitude": "lat",
}
STATIC_TOOL_ALIASES = {
    "name": "ship_name",
    "imonumber": "imo",
    "type": "ship_type",
    "buildyear": "built_year",
    "draught": "draft",
}


def default_ship_update_draft() -> dict[str, Any]:
    return {
        "active": False,
        "operation_type": "",
        "tool_name": "",
        "tool_args": {},
        "missing_fields": [],
        "target_identity": {"mmsi": "", "imo": "", "ship_name": ""},
        "source_turn_id": "",
        "evidence_sources": [],
        "turns_elapsed": 0,
        "expires_after_turns": 5,
        "status": "",
    }


def is_active_ship_update_draft(value: dict[str, Any] | None) -> bool:
    draft = dict(value or {})
    return bool(draft.get("active") and str(draft.get("status") or "") not in {"executed_success", "cancelled", "expired"})


def legacy_pending_to_draft(value: dict[str, Any] | None) -> dict[str, Any]:
    pending = dict(value or {})
    if not pending:
        return default_ship_update_draft()
    if "tool_args" in pending or "target_identity" in pending:
        return _normalize_ship_update_draft(pending)

    identity = dict(pending.get("ship_identity") or {})
    flat_fields = {
        key: value
        for key, value in pending.items()
        if key in (POSITION_UPDATE_FIELDS | STATIC_UPDATE_FIELDS | {"mmsi", "imo", "ship_name", "name", "draft", "navstatus"})
        and value not in (None, "")
    }
    extracted = dict(pending.get("extracted_fields") or {})
    fields = clean_optional_voyage_fields({**extracted, **flat_fields})
    for key in ("mmsi", "imo", "ship_name", "name"):
        value = fields.pop(key, "") or identity.get(_identity_key(key if key != "name" else "ship_name"), "")
        if value:
            identity[_identity_key(key if key != "name" else "ship_name")] = value

    operation_type = str(pending.get("operation_type") or "").strip()
    if operation_type not in {"position_update", "static_update", "mixed_update", "ambiguous_update"}:
        if any(k in fields for k in {"lon", "lat", "updatetime", "speed", "heading", "course", "navstatus"}):
            operation_type = "position_update"
        elif any(k in fields for k in {"destination", "eta", "draft", "callsign", "ship_type", "imo", "ship_name"}):
            operation_type = "static_update"
        else:
            operation_type = ""

    tool_name = ""
    if operation_type == "position_update":
        tool_name = "upload_ship_position"
    elif operation_type == "static_update":
        tool_name = "update_ship_static_info"

    tool_args = _coerce_tool_args(tool_name, {**fields, **{k: v for k, v in identity.items() if k in {"mmsi", "imo", "ship_name"} and v}})
    missing = list(pending.get("missing_required_fields") or [])
    if operation_type == "position_update":
        for required in ("mmsi", "lon", "lat", "updatetime"):
            if not tool_args.get(required) and required.upper() not in missing and required not in missing:
                missing.append("MMSI" if required == "mmsi" else required)
    elif operation_type == "static_update":
        if not tool_args.get("mmsi") and "MMSI" not in missing:
            missing.append("MMSI")
        if not any(k != "mmsi" and str(v or "").strip() for k, v in tool_args.items()) and "静态更新字段" not in missing:
            missing.append("静态更新字段")

    status = str(pending.get("status") or "")
    active = bool(pending.get("active", True)) and status not in {"executed_success", "cancelled", "expired"}
    if not operation_type and not tool_args:
        active = False
    draft = default_ship_update_draft()
    draft.update(
        {
            "active": active,
            "operation_type": operation_type,
            "tool_name": tool_name,
            "tool_args": tool_args,
            "missing_fields": missing,
            "target_identity": {
                "mmsi": str(identity.get("mmsi") or tool_args.get("mmsi") or ""),
                "imo": str(identity.get("imo") or tool_args.get("imo") or ""),
                "ship_name": str(identity.get("name") or identity.get("ship_name") or tool_args.get("ship_name") or ""),
            },
            "source_turn_id": str(pending.get("source_turn_id") or ""),
            "evidence_sources": ["active_pending"] if pending else [],
            "turns_elapsed": int(pending.get("turns_elapsed") or 0),
            "expires_after_turns": int(pending.get("expires_after_turns") or 5),
            "status": status or _draft_pending_status(missing, active=active),
        }
    )
    return _normalize_ship_update_draft(draft)


def draft_to_pending_compat(value: dict[str, Any] | None) -> dict[str, Any]:
    draft = _normalize_ship_update_draft(value)
    if not draft.get("active") and not draft.get("status"):
        return default_pending_update_state()
    tool_args = dict(draft.get("tool_args") or {})
    identity = dict(draft.get("target_identity") or {})
    missing = list(draft.get("missing_fields") or [])
    status = str(draft.get("status") or _draft_pending_status(missing, active=bool(draft.get("active"))))
    if status not in PENDING_UPDATE_STATUSES_COMPAT:
        status = _draft_pending_status(missing, active=bool(draft.get("active")))
    return build_pending_update_state(
        operation_type=str(draft.get("operation_type") or ""),
        status=status,
        source_turn_id=str(draft.get("source_turn_id") or ""),
        ship_identity={
            "mmsi": str(identity.get("mmsi") or tool_args.get("mmsi") or ""),
            "imo": str(identity.get("imo") or tool_args.get("imo") or ""),
            "name": str(identity.get("ship_name") or tool_args.get("ship_name") or ""),
            "candidate_mmsi": [],
        },
        extracted_fields={k: v for k, v in tool_args.items() if k not in {"mmsi", "imo", "ship_name"}},
        missing_required_fields=missing,
        turns_elapsed=int(draft.get("turns_elapsed") or 0),
    )


PENDING_UPDATE_STATUSES_COMPAT = {
    "awaiting_operation_type",
    "awaiting_required_fields",
    "awaiting_ship_identity",
    "awaiting_mmsi_confirmation",
    "awaiting_field_confirmation",
    "ready_to_execute",
    "executed_success",
    "executed_failed",
    "cancelled",
    "expired",
}


def _normalize_ship_update_draft(value: dict[str, Any] | None) -> dict[str, Any]:
    draft = {**default_ship_update_draft(), **dict(value or {})}
    operation_type = _normalize_operation(draft.get("operation_type"))
    tool_name = str(draft.get("tool_name") or "").strip()
    if tool_name not in ALLOWED_WRITE_TOOLS:
        tool_name = "upload_ship_position" if operation_type == "position_update" else "update_ship_static_info" if operation_type == "static_update" else ""
    tool_args = _coerce_tool_args(tool_name, dict(draft.get("tool_args") or {}))
    identity = {**default_ship_update_draft()["target_identity"], **dict(draft.get("target_identity") or {})}
    for key in ("mmsi", "imo", "ship_name"):
        if tool_args.get(key):
            identity[key] = str(tool_args[key])
    draft.update(
        {
            "active": bool(draft.get("active")),
            "operation_type": operation_type,
            "tool_name": tool_name,
            "tool_args": tool_args,
            "missing_fields": _coerce_string_list(draft.get("missing_fields")),
            "target_identity": identity,
            "source_turn_id": str(draft.get("source_turn_id") or ""),
            "evidence_sources": _coerce_string_list(draft.get("evidence_sources")),
            "turns_elapsed": int(draft.get("turns_elapsed") or 0),
            "expires_after_turns": int(draft.get("expires_after_turns") or 5),
            "status": str(draft.get("status") or ""),
        }
    )
    return draft


def _draft_pending_status(missing: list[str], *, active: bool) -> str:
    if not active:
        return ""
    lowered = {str(item).lower() for item in missing}
    if "mmsi" in lowered or "船舶标识" in lowered:
        return "awaiting_ship_identity"
    if missing:
        return "awaiting_required_fields"
    return "ready_to_execute"


def _draft_from_ready_result(result: "ShipUpdateSubagentResult") -> dict[str, Any]:
    draft = default_ship_update_draft()
    tool_args = _coerce_tool_args(result.tool_name, dict(result.tool_args or {}))
    draft.update(
        {
            "active": False,
            "operation_type": result.operation_type,
            "tool_name": result.tool_name or "",
            "tool_args": tool_args,
            "missing_fields": list(result.missing_fields or []),
            "target_identity": {
                "mmsi": str(tool_args.get("mmsi") or ""),
                "imo": str(tool_args.get("imo") or ""),
                "ship_name": str(tool_args.get("ship_name") or ""),
            },
            "evidence_sources": list(result.evidence_sources or []),
            "status": "ready_to_execute",
        }
    )
    return draft


@dataclass
class ShipUpdateSubagentResult:
    status: Literal["ready_to_execute", "need_user_input", "non_write", "cancelled", "error"] = "non_write"
    operation_type: Literal["position_update", "static_update", "mixed_update", "none"] = "none"
    tool_name: str | None = None
    tool_args: dict[str, Any] = field(default_factory=dict)
    missing_fields: list[str] = field(default_factory=list)
    pending_action: Literal["create", "resume", "update", "clear", "none"] = "none"
    draft_action: Literal["create", "resume", "update", "clear", "none"] = "none"
    ship_update_draft: dict[str, Any] = field(default_factory=default_ship_update_draft)
    pending_update_state: dict[str, Any] = field(default_factory=default_pending_update_state)
    reply_to_user: str = ""
    confidence: Literal["high", "medium", "low"] = "low"
    evidence_sources: list[str] = field(default_factory=list)
    contract_payload: dict[str, Any] = field(default_factory=dict)
    normalized_fields: dict[str, Any] = field(default_factory=dict)
    source: str = "fallback_contract_parser"

    def __post_init__(self) -> None:
        if not self.ship_update_draft or self.ship_update_draft == default_ship_update_draft():
            self.ship_update_draft = legacy_pending_to_draft(self.pending_update_state)
            if self.status == "ready_to_execute":
                self.ship_update_draft = _draft_from_ready_result(self)
            elif self.pending_action in VALID_PENDING_ACTIONS and self.pending_action != "none":
                self.draft_action = self.pending_action  # type: ignore[assignment]
        else:
            self.ship_update_draft = _normalize_ship_update_draft(self.ship_update_draft)
        if not self.pending_update_state or self.pending_update_state == default_pending_update_state():
            self.pending_update_state = draft_to_pending_compat(self.ship_update_draft)
        if self.pending_action == "none" and self.draft_action != "none":
            self.pending_action = self.draft_action
        if self.draft_action == "none" and self.pending_action != "none":
            self.draft_action = self.pending_action

    def model_dump(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "operation_type": self.operation_type,
            "tool_name": self.tool_name,
            "tool_args": dict(self.tool_args),
            "missing_fields": list(self.missing_fields),
            "pending_action": self.pending_action,
            "draft_action": self.draft_action,
            "ship_update_draft": dict(self.ship_update_draft),
            "pending_update_state": dict(self.pending_update_state),
            "reply_to_user": self.reply_to_user,
            "confidence": self.confidence,
            "evidence_sources": list(self.evidence_sources),
            "contract_payload": dict(self.contract_payload),
            "normalized_fields": dict(self.normalized_fields),
            "source": self.source,
        }


def _build_subagent_payload(
    text: str,
    *,
    perception: dict[str, Any] | None,
    pending_update_state: dict[str, Any],
    understanding: dict[str, Any],
    source_turn_id: str,
) -> dict[str, Any]:
    active_draft = legacy_pending_to_draft(pending_update_state)
    return {
        "current_text": str(text or ""),
        "current_attachment_perception": dict(perception or {}),
        "active_ship_update_draft": dict(active_draft or {}),
        "active_pending": dict(pending_update_state or {}),
        "customer_understanding": dict(understanding or {}),
        "source_turn_id": str(source_turn_id or ""),
        "tool_contracts": {
            "upload_ship_position": {
                "required_tool_args": ["mmsi", "lon", "lat", "updatetime"],
                "optional_tool_args": ["speed", "heading", "course", "draft", "destination", "eta", "navstatus", "ship_name", "wechatgroup"],
                "api_mapping": {"mmsi": "name+mmsi", "draft": "draught", "navstatus": "status"},
            },
            "update_ship_static_info": {
                "required_tool_args": ["mmsi"],
                "requires_at_least_one_update_field": True,
                "optional_tool_args": ["ship_name", "imo", "callsign", "ship_type", "minotype", "width", "length", "dwt", "built_year", "destination", "eta", "draft", "wechatgroup"],
                "api_mapping": {"ship_name": "name", "imo": "imonumber", "ship_type": "type", "built_year": "buildyear", "draft": "draught"},
            },
        },
    }


def _coerce_llm_subagent_result(raw: dict[str, Any], *, fallback_pending: dict[str, Any]) -> ShipUpdateSubagentResult | None:
    if not isinstance(raw, dict) or not raw:
        return None
    status = str(raw.get("status") or "").strip()
    operation_type = str(raw.get("operation_type") or "none").strip()
    pending_action = str(raw.get("pending_action") or "none").strip()
    confidence = str(raw.get("confidence") or "medium").strip()
    tool_name = str(raw.get("tool_name") or "").strip() or None
    if status not in VALID_STATUSES:
        return None
    if operation_type not in VALID_OPERATIONS:
        operation_type = "none"
    if pending_action not in VALID_PENDING_ACTIONS:
        pending_action = "none"
    if confidence not in {"high", "medium", "low"}:
        confidence = "medium"
    if tool_name in {"", "null", "None", "none"}:
        tool_name = None
    if tool_name and tool_name not in ALLOWED_WRITE_TOOLS:
        return ShipUpdateSubagentResult(
            status="error",
            operation_type=operation_type,  # type: ignore[arg-type]
            tool_name=None,
            missing_fields=[],
            pending_action="none",
            pending_update_state=dict(raw.get("pending_update_state") or fallback_pending or default_pending_update_state()),
            reply_to_user="本次船舶信息更新暂未执行：子 agent 返回了不允许的写入工具。",
            confidence="low",
            evidence_sources=_coerce_string_list(raw.get("evidence_sources")) or ["current_text"],
            contract_payload=dict(raw),
            normalized_fields={},
            source="llm_subagent",
        )
    tool_args = _coerce_tool_args(tool_name, dict(raw.get("tool_args") or {}))
    normalized_fields = dict(raw.get("normalized_fields") or tool_args or {})
    draft = _normalize_ship_update_draft(dict(raw.get("ship_update_draft") or {}))
    if not draft.get("tool_args") and tool_args:
        draft.update(
            {
                "active": status == "need_user_input",
                "operation_type": operation_type,
                "tool_name": tool_name or "",
                "tool_args": tool_args,
                "missing_fields": _coerce_string_list(raw.get("missing_fields")),
                "target_identity": {
                    "mmsi": str(tool_args.get("mmsi") or ""),
                    "imo": str(tool_args.get("imo") or ""),
                    "ship_name": str(tool_args.get("ship_name") or ""),
                },
                "evidence_sources": _coerce_string_list(raw.get("evidence_sources")) or ["current_text"],
                "status": _draft_pending_status(_coerce_string_list(raw.get("missing_fields")), active=status == "need_user_input"),
            }
        )
        draft = _normalize_ship_update_draft(draft)
    if not draft.get("active") and not draft.get("tool_args"):
        draft = legacy_pending_to_draft(raw.get("pending_update_state") or fallback_pending)
    if pending_action == "clear" or status in {"cancelled"}:
        draft.update({"active": False, "status": "cancelled"})
    draft_action = str(raw.get("draft_action") or pending_action or "none").strip()
    if draft_action not in VALID_PENDING_ACTIONS:
        draft_action = pending_action
    pending = dict(raw.get("pending_update_state") or draft_to_pending_compat(draft) or fallback_pending or default_pending_update_state())
    return ShipUpdateSubagentResult(
        status=status,  # type: ignore[arg-type]
        operation_type=operation_type,  # type: ignore[arg-type]
        tool_name=tool_name,
        tool_args=tool_args,
        missing_fields=_coerce_string_list(raw.get("missing_fields")),
        pending_action=pending_action,  # type: ignore[arg-type]
        draft_action=draft_action,  # type: ignore[arg-type]
        ship_update_draft=draft,
        pending_update_state=pending,
        reply_to_user=str(raw.get("reply_to_user") or ""),
        confidence=confidence,  # type: ignore[arg-type]
        evidence_sources=_coerce_string_list(raw.get("evidence_sources")) or ["current_text"],
        contract_payload=dict(raw),
        normalized_fields=normalized_fields,
        source="llm_subagent",
    )


def _coerce_tool_args(tool_name: str | None, args: dict[str, Any]) -> dict[str, Any]:
    if not tool_name:
        return {}
    aliases = POSITION_TOOL_ALIASES if tool_name == "upload_ship_position" else STATIC_TOOL_ALIASES
    allowed = POSITION_UPDATE_FIELDS if tool_name == "upload_ship_position" else STATIC_UPDATE_FIELDS | {"mmsi"}
    coerced: dict[str, Any] = {}
    for raw_key, value in dict(args or {}).items():
        if value in (None, ""):
            continue
        key = aliases.get(str(raw_key), str(raw_key))
        if key not in allowed:
            continue
        coerced[key] = value
    return clean_optional_voyage_fields(coerced)


def _coerce_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item or "").strip()]


def run_ship_update_subagent(
    text: str,
    *,
    perception: dict[str, Any] | None = None,
    pending_update_state: dict[str, Any] | None = None,
    understanding: dict[str, Any] | None = None,
    source_turn_id: str = "",
    json_agent: Callable[[str, dict[str, Any]], dict[str, Any]] | None = None,
) -> ShipUpdateSubagentResult:
    """Return a ship_update tool plan.

    Production path is prompt-driven. Deterministic parsing remains only as a
    local/test fallback when the JSON sub-agent is unavailable or invalid.
    """
    current_text = str(text or "")
    pending = dict(pending_update_state or {})
    understanding = dict(understanding or {})
    if json_agent is not None:
        payload = _build_subagent_payload(
            current_text,
            perception=perception,
            pending_update_state=pending,
            understanding=understanding,
            source_turn_id=source_turn_id,
        )
        try:
            raw = json_agent(SHIP_UPDATE_SUBAGENT_PROMPT, payload)
        except Exception:
            raw = {}
        result = _coerce_llm_subagent_result(raw, fallback_pending=pending)
        if result is not None:
            return result
    return _run_ship_update_subagent_fallback(
        current_text,
        perception=perception,
        pending_update_state=pending,
        understanding=understanding,
        source_turn_id=source_turn_id,
    )


def _run_ship_update_subagent_fallback(
    text: str,
    *,
    perception: dict[str, Any] | None = None,
    pending_update_state: dict[str, Any] | None = None,
    understanding: dict[str, Any] | None = None,
    source_turn_id: str = "",
) -> ShipUpdateSubagentResult:
    current_text = str(text or "")
    pending = draft_to_pending_compat(legacy_pending_to_draft(pending_update_state))
    understanding = dict(understanding or {})
    active_pending = is_active_pending_update_state(pending)
    if _is_cancel_text(current_text):
        cleared = dict(pending or default_pending_update_state())
        cleared.update({"active": False, "status": "cancelled", "can_resume": False})
        return ShipUpdateSubagentResult(
            status="cancelled",
            operation_type=_normalize_operation(pending.get("operation_type")) if pending else "none",
            pending_action="clear",
            pending_update_state=cleared,
            reply_to_user="已取消本次船舶信息更新。",
            confidence="high",
            evidence_sources=["current_text"],
        )
    if _is_non_write(understanding):
        return ShipUpdateSubagentResult(
            status="non_write",
            operation_type="none",
            pending_action="none",
            pending_update_state=pending,
            reply_to_user="",
            confidence="medium",
            evidence_sources=["current_text"],
        )
    if not active_pending and _is_confirmation_text(current_text):
        return ShipUpdateSubagentResult(
            status="need_user_input",
            operation_type="none",
            missing_fields=["更新内容"],
            pending_action="none",
            reply_to_user="当前没有待确认的船舶更新。请提供本次要更新的 MMSI 和具体字段后，我再处理。",
            confidence="high",
            evidence_sources=["current_text"],
        )

    contract = extract_ship_update_parameters_with_agent(current_text, perception)
    fields = clean_optional_voyage_fields(dict(contract.fields or {}))
    operation_type = _normalize_operation(contract.operation_type)
    evidence_sources = ["current_text"]
    if perception:
        evidence_sources.append("current_attachment")

    if active_pending:
        evidence_sources.append("active_pending")
        operation_type = _normalize_operation(pending.get("operation_type")) or operation_type
        merged_identity = dict(pending.get("ship_identity") or {})
        merged_fields = clean_optional_voyage_fields(dict(pending.get("extracted_fields") or {}))
        for key, value in fields.items():
            if value not in (None, ""):
                if key in {"mmsi", "imo", "ship_name"}:
                    merged_identity[_identity_key(key)] = value
                else:
                    merged_fields[key] = value
        if fields.get("mmsi"):
            merged_identity["mmsi"] = fields["mmsi"]
        if operation_type == "static_update":
            _merge_static_followup_value(current_text, pending, merged_fields)
        fields = clean_optional_voyage_fields({**merged_fields, **{k: v for k, v in merged_identity.items() if k in {"mmsi", "imo", "ship_name"} and v}})
        contract = _contract_from_fields(operation_type, fields, source="ship_update_subagent_pending_resume")

    if operation_type == "ambiguous_update":
        return _need_input(
            operation_type="none",
            pending_operation="ambiguous_update",
            status="awaiting_operation_type",
            fields=fields,
            missing=["operation_type"],
            question="请确认是更新船位，还是更新船舶静态信息？",
            source_turn_id=source_turn_id,
            evidence_sources=evidence_sources,
        )
    if operation_type == "mixed_update":
        return _need_input(
            operation_type="mixed_update",
            pending_operation="mixed_update",
            status="awaiting_field_confirmation",
            fields=fields,
            missing=["单次更新类型"],
            question="本次同时包含船位和静态信息字段。请确认先更新船位还是先更新静态信息。",
            source_turn_id=source_turn_id,
            evidence_sources=evidence_sources,
        )
    if operation_type == "static_update":
        return _plan_static_update(contract, fields, source_turn_id=source_turn_id, evidence_sources=evidence_sources, resumed=active_pending)
    if operation_type == "position_update":
        return _plan_position_update(contract, source_turn_id=source_turn_id, evidence_sources=evidence_sources, resumed=active_pending)

    if bool(understanding.get("ship_update_candidate")) or bool(understanding.get("ship_write_request")) or str(understanding.get("intent") or "") == "ship_update":
        return _need_input(
            operation_type="none",
            pending_operation="ambiguous_update",
            status="awaiting_operation_type",
            fields=fields,
            missing=["operation_type"],
            question="请确认是更新船位，还是更新船舶静态信息？",
            source_turn_id=source_turn_id,
            evidence_sources=evidence_sources,
        )

    return ShipUpdateSubagentResult(
        status="non_write",
        operation_type="none",
        pending_action="none",
        pending_update_state=pending if active_pending else default_pending_update_state(),
        reply_to_user="",
        confidence="low",
        evidence_sources=evidence_sources,
        contract_payload=contract.model_dump(),
    )


def _plan_position_update(
    contract: ContractShipUpdateExtraction,
    *,
    source_turn_id: str,
    evidence_sources: list[str],
    resumed: bool,
) -> ShipUpdateSubagentResult:
    extraction, normalized = extract_and_normalize_ship_update_contract(contract.model_dump())
    normalized_fields = dict(normalized.normalized_fields or {})
    for key in ("lon", "lat", "speed", "heading", "course", "draft", "navstatus", "destination", "eta"):
        if contract.fields.get(key) not in (None, ""):
            normalized_fields[key] = contract.fields[key]
    missing = list(normalized.missing_required_fields or [])
    invalid = list(extraction.invalid_fields or [])
    if missing or invalid or not normalized.can_write:
        question = normalized.user_confirmation_message or "更新船位缺少必填字段：" + "、".join(missing or ["有效字段"]) + "。请补充后我再更新。"
        if "MMSI" in missing:
            rest = [item for item in missing if item != "MMSI"]
            if rest:
                question = "需要明确船舶身份标识（MMSI），并补充：" + "、".join(rest) + "。当前仅会按本轮明确提供的信息写入。"
            else:
                question = "需要明确船舶身份标识（MMSI）。请补充 9 位 MMSI 后我再更新；当前仅会按本轮明确提供的信息写入。"
        pending = build_pending_update_state(
            operation_type="position_update",
            status=_pending_status(missing, invalid),
            source_turn_id=source_turn_id,
            ship_identity={
                "mmsi": normalized.mmsi,
                "imo": normalized.imo,
                "name": normalized.ship_name,
                "candidate_mmsi": [],
            },
            extracted_fields=_position_pending_fields(normalized_fields),
            missing_required_fields=missing,
            invalid_fields=invalid,
            last_question_to_user=question,
            confirmation_required=bool(invalid),
        )
        return ShipUpdateSubagentResult(
            status="need_user_input",
            operation_type="position_update",
            missing_fields=missing or invalid,
            pending_action="resume" if resumed else "create",
            pending_update_state=pending,
            reply_to_user=question,
            confidence="medium",
            evidence_sources=evidence_sources,
            contract_payload=contract.model_dump(),
            normalized_fields=normalized_fields,
        )
    args = _position_tool_args(normalized_fields)
    return ShipUpdateSubagentResult(
        status="ready_to_execute",
        operation_type="position_update",
        tool_name="upload_ship_position",
        tool_args=args,
        pending_action="resume" if resumed else "none",
        pending_update_state=default_pending_update_state(),
        reply_to_user="",
        confidence="high",
        evidence_sources=evidence_sources,
        contract_payload=contract.model_dump(),
        normalized_fields=normalized_fields,
    )


def _plan_static_update(
    contract: ContractShipUpdateExtraction,
    fields: dict[str, Any],
    *,
    source_turn_id: str,
    evidence_sources: list[str],
    resumed: bool,
) -> ShipUpdateSubagentResult:
    args = _static_tool_args(fields)
    missing: list[str] = []
    if not args.get("mmsi"):
        missing.append("MMSI")
    if not any(key != "mmsi" and str(value or "").strip() for key, value in args.items()):
        missing.append("静态更新字段")
    if missing:
        question = _static_missing_question(missing)
        pending = build_pending_update_state(
            operation_type="static_update",
            status="awaiting_ship_identity" if "MMSI" in missing else "awaiting_required_fields",
            source_turn_id=source_turn_id,
            ship_identity={"mmsi": str(args.get("mmsi") or fields.get("mmsi") or ""), "imo": str(fields.get("imo") or ""), "name": str(fields.get("ship_name") or ""), "candidate_mmsi": []},
            extracted_fields={k: v for k, v in args.items() if k != "mmsi"},
            missing_required_fields=missing,
            last_question_to_user=question,
            confirmation_required=False,
        )
        return ShipUpdateSubagentResult(
            status="need_user_input",
            operation_type="static_update",
            missing_fields=missing,
            pending_action="resume" if resumed else "create",
            pending_update_state=pending,
            reply_to_user=question,
            confidence="medium",
            evidence_sources=evidence_sources,
            contract_payload=contract.model_dump(),
            normalized_fields=dict(args),
        )
    return ShipUpdateSubagentResult(
        status="ready_to_execute",
        operation_type="static_update",
        tool_name="update_ship_static_info",
        tool_args=args,
        pending_action="resume" if resumed else "none",
        pending_update_state=default_pending_update_state(),
        reply_to_user="",
        confidence="high",
        evidence_sources=evidence_sources,
        contract_payload=contract.model_dump(),
        normalized_fields=dict(args),
    )


def _need_input(
    *,
    operation_type: str,
    pending_operation: str,
    status: str,
    fields: dict[str, Any],
    missing: list[str],
    question: str,
    source_turn_id: str,
    evidence_sources: list[str],
) -> ShipUpdateSubagentResult:
    pending = build_pending_update_state(
        operation_type=pending_operation,
        status=status,
        source_turn_id=source_turn_id,
        ship_identity={"mmsi": str(fields.get("mmsi") or ""), "imo": str(fields.get("imo") or ""), "name": str(fields.get("ship_name") or ""), "candidate_mmsi": []},
        extracted_fields={k: v for k, v in fields.items() if k not in {"mmsi", "imo", "ship_name"}},
        missing_required_fields=missing,
        last_question_to_user=question,
        confirmation_required=True,
    )
    return ShipUpdateSubagentResult(
        status="need_user_input",
        operation_type=operation_type,  # type: ignore[arg-type]
        missing_fields=missing,
        pending_action="create",
        pending_update_state=pending,
        reply_to_user=question,
        confidence="medium",
        evidence_sources=evidence_sources,
        normalized_fields=dict(fields),
    )


def _position_tool_args(fields: dict[str, Any]) -> dict[str, str]:
    args: dict[str, str] = {}
    key_map = {"draft": "draft", "navstatus": "navstatus"}
    for key in POSITION_UPDATE_FIELDS:
        mapped = key_map.get(key, key)
        value = fields.get(mapped)
        if value in (None, ""):
            continue
        if mapped in {"longitude_decimal", "latitude_decimal"}:
            continue
        if mapped == "ship_name":
            args["ship_name"] = str(value)
        elif mapped in {"mmsi", "lon", "lat", "updatetime", "speed", "heading", "course", "draft", "navstatus", "destination", "eta", "wechatgroup"}:
            if isinstance(value, float) and value.is_integer():
                args[mapped] = str(int(value))
            else:
                args[mapped] = str(value)
    return clean_optional_voyage_fields(args)


def _position_pending_fields(fields: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in clean_optional_voyage_fields(dict(fields)).items()
        if key not in {"mmsi", "imo", "ship_name", "longitude_decimal", "latitude_decimal"} and value not in (None, "")
    }


def _static_tool_args(fields: dict[str, Any]) -> dict[str, str]:
    alias = {
        "imonumber": "imo",
        "name": "ship_name",
        "type": "ship_type",
        "buildyear": "built_year",
        "draught": "draft",
    }
    args: dict[str, str] = {}
    for raw_key, raw_value in clean_optional_voyage_fields(dict(fields or {})).items():
        key = alias.get(str(raw_key), str(raw_key))
        if key not in STATIC_UPDATE_FIELDS and key != "mmsi":
            continue
        if raw_value in (None, ""):
            continue
        args[key] = str(raw_value).strip()
    return args


def _merge_static_followup_value(text: str, pending: dict[str, Any], fields: dict[str, Any]) -> None:
    missing = {str(item).lower() for item in list(pending.get("missing_required_fields") or [])}
    if fields.get("destination") or fields.get("eta"):
        return
    if not ("静态更新字段" in missing or "destination_or_eta" in missing or "目的港" in missing):
        return
    value = _extract_destination_followup_value(text)
    if value:
        fields["destination"] = value


def _extract_destination_followup_value(text: str) -> str:
    value = str(text or "").strip()
    patterns = [
        r"(?:正确的)?(?:目的港|destination)(?:信息)?(?:是|为|:|：)?\s*([A-Za-z][A-Za-z0-9 _.-]{1,40})",
        r"^[\s\"']*([A-Z][A-Z0-9 _.-]{2,40})[\s\"']*$",
    ]
    for pattern in patterns:
        match = re.search(pattern, value, flags=re.IGNORECASE)
        if not match:
            continue
        candidate = re.sub(r"\s+", " ", match.group(1)).strip(" ，,。；;")
        if candidate and not re.search(r"(更新|修改|确认|取消|为什么|怎么|如何)", candidate, flags=re.IGNORECASE):
            return candidate.upper()
    return ""


def _contract_from_fields(operation_type: str, fields: dict[str, Any], *, source: str) -> ContractShipUpdateExtraction:
    return ContractShipUpdateExtraction(
        operation_type=operation_type if operation_type else "unknown",  # type: ignore[arg-type]
        fields=clean_optional_voyage_fields(dict(fields or {})),
        ship_identity={k: v for k, v in dict(fields or {}).items() if k in {"mmsi", "imo", "ship_name"}},
        position_update_fields=dict(fields or {}) if operation_type == "position_update" else {},
        static_update_fields=dict(fields or {}) if operation_type == "static_update" else {},
        raw_mentions=dict(fields or {}),
        source=source,
    )


def _pending_status(missing: list[str], invalid: list[str]) -> str:
    lowered = {str(item).lower() for item in missing}
    if invalid:
        return "awaiting_field_confirmation"
    if "mmsi" in lowered or "船舶标识" in lowered:
        return "awaiting_ship_identity"
    return "awaiting_required_fields"


def _static_missing_question(missing: list[str]) -> str:
    if missing == ["静态更新字段"]:
        return "请补充需要更新的静态信息字段，例如最新目的港、ETA、吃水、呼号或船舶尺寸。"
    return "本次静态信息更新缺少：" + "、".join(missing) + "。请补充后我再更新。"


def _normalize_operation(value: Any) -> str:
    operation = str(value or "none").strip()
    if operation in {"position_update", "static_update", "mixed_update", "ambiguous_update"}:
        return operation
    return "none"


def _identity_key(key: str) -> str:
    return "name" if key == "ship_name" else key


def _is_cancel_text(text: str) -> bool:
    return any(marker in str(text or "") for marker in ("取消更新", "不用更新", "取消", "先不更新"))


def _is_confirmation_text(text: str) -> bool:
    normalized = re.sub(r"\s+", "", str(text or ""), flags=re.UNICODE).lower()
    return normalized in {"确认", "确认更新", "确认执行", "确认提交", "确定", "是的", "对", "可以", "继续", "继续更新", "好的", "好", "ok", "yes", "按上述参数更新", "按照上述参数更新", "按上面参数更新"}


def _is_non_write(understanding: dict[str, Any]) -> bool:
    non_write = str(understanding.get("non_write_reason") or "none")
    operation = str(understanding.get("operation_type") or "none")
    return non_write != "none" or operation in {"frontend_capability_question", "data_delay_troubleshooting", "ship_query"}
