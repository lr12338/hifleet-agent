"""Tool parameter contracts for customer-support ship updates."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ShipUpdateParameterContract:
    operation_type: str
    tool_name: str
    required_fields: tuple[str, ...]
    optional_fields: tuple[str, ...]

    @property
    def supported_fields(self) -> tuple[str, ...]:
        return self.required_fields + self.optional_fields


POSITION_UPDATE_CONTRACT = ShipUpdateParameterContract(
    operation_type="position_update",
    tool_name="upload_ship_position",
    required_fields=("mmsi", "lon", "lat", "updatetime"),
    optional_fields=(
        "speed",
        "heading",
        "course",
        "draft",
        "navstatus",
        "destination",
        "eta",
        "ship_name",
        "wechatgroup",
    ),
)

STATIC_UPDATE_CONTRACT = ShipUpdateParameterContract(
    operation_type="static_update",
    tool_name="update_ship_static_info",
    required_fields=("mmsi",),
    optional_fields=(
        "ship_name",
        "imo",
        "ship_type",
        "minotype",
        "length",
        "width",
        "dwt",
        "flag",
        "callsign",
        "built_year",
        "destination",
        "eta",
        "draft",
        "wechatgroup",
    ),
)

POSITION_UPDATE_FIELDS = set(POSITION_UPDATE_CONTRACT.supported_fields)
STATIC_UPDATE_FIELDS = set(STATIC_UPDATE_CONTRACT.supported_fields)
SHIP_UPDATE_IDENTIFIER_FIELDS = {"mmsi", "imo", "ship_name"}

NAV_STATUS_VALUES = (
    "机动船在航",
    "操纵能力受限",
    "正在捕鱼作业",
    "帆船在航",
    "限于吃水",
    "锚泊",
    "系泊",
    "停泊",
    "搁浅",
    "失控",
    "在航",
    "未知",
    "未定义",
    "待定义",
)

NAV_STATUS_ALIASES = {
    "航行中": "在航",
    "航行": "在航",
    "抛锚": "锚泊",
    "锚": "锚泊",
    "未在指挥": "失控",
    "受限操纵": "操纵能力受限",
    "操纵受限": "操纵能力受限",
    "操作受限": "操纵能力受限",
    "吃水受限": "限于吃水",
    "吃水受限制": "限于吃水",
    "靠泊": "系泊",
    "从事捕鱼": "正在捕鱼作业",
    "渔船作业": "正在捕鱼作业",
    "捕鱼": "正在捕鱼作业",
    "从事航行": "在航",
}


def normalize_nav_status(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if raw in NAV_STATUS_VALUES:
        return raw
    return NAV_STATUS_ALIASES.get(raw, raw)


def operation_contract(operation_type: str) -> ShipUpdateParameterContract | None:
    if operation_type == POSITION_UPDATE_CONTRACT.operation_type:
        return POSITION_UPDATE_CONTRACT
    if operation_type == STATIC_UPDATE_CONTRACT.operation_type:
        return STATIC_UPDATE_CONTRACT
    return None
