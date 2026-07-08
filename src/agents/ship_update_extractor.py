"""Structured extraction for ship update requests.

The module is intentionally deterministic by default so tests and local runs do
not depend on an external model. A future LLM extractor can populate the same
schema before deterministic normalization.
"""
from __future__ import annotations

from typing import Any, Literal
import re

from pydantic import BaseModel, Field

from agents.ship_update_contract import NAV_STATUS_ALIASES, NAV_STATUS_VALUES, POSITION_UPDATE_CONTRACT, STATIC_UPDATE_CONTRACT
from agents.ship_update_normalizer import NormalizedShipUpdate, clean_optional_voyage_fields, normalize_ship_update_fields


class ContractShipUpdateExtraction(BaseModel):
    operation_type: Literal[
        "none",
        "ship_query",
        "position_update",
        "static_update",
        "mixed_update",
        "ambiguous_update",
        "frontend_capability_question",
        "data_delay_troubleshooting",
        "unknown",
    ] = "unknown"
    fields: dict[str, Any] = Field(default_factory=dict)
    ship_identity: dict[str, Any] = Field(default_factory=dict)
    position_update_fields: dict[str, Any] = Field(default_factory=dict)
    static_update_fields: dict[str, Any] = Field(default_factory=dict)
    raw_mentions: dict[str, Any] = Field(default_factory=dict)
    confidence: dict[str, float] = Field(default_factory=dict)
    ambiguities: list[str] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)
    invalid_fields: list[str] = Field(default_factory=list)
    conflict_fields: list[str] = Field(default_factory=list)
    unsupported_fields: list[str] = Field(default_factory=list)
    action_recommendation: str = "none"
    next_question: str | None = None
    action_allowed: bool = False
    source: str = "fallback_contract_parser"


class ShipPositionUpdateExtraction(BaseModel):
    operation: str | None = None
    mmsi: str | None = None
    imo: str | None = None
    ship_name: str | None = None
    raw_updatetime: str | None = None
    normalized_updatetime: str | None = None
    updatetime_valid: bool = False
    updatetime_error: str | None = None
    updatetime_suggestion: str | None = None
    longitude_raw: str | None = None
    latitude_raw: str | None = None
    longitude_decimal: float | None = None
    latitude_decimal: float | None = None
    longitude_valid: bool = False
    latitude_valid: bool = False
    position_error: str | None = None
    speed: float | None = None
    heading: float | None = None
    course: float | None = None
    draft: float | None = None
    nav_status: str | None = None
    destination: str | None = None
    eta: str | None = None
    field_confidence: dict[str, float] = Field(default_factory=dict)
    missing_required_fields: list[str] = Field(default_factory=list)
    suspicious_fields: list[str] = Field(default_factory=list)
    can_write: bool = False
    need_user_confirmation: bool = False
    user_confirmation_message: str | None = None
    raw_fields: dict[str, str] = Field(default_factory=dict)
    operation_type: str | None = None
    fields: dict[str, Any] = Field(default_factory=dict)
    ship_identity: dict[str, Any] = Field(default_factory=dict)
    position_update_fields: dict[str, Any] = Field(default_factory=dict)
    static_update_fields: dict[str, Any] = Field(default_factory=dict)
    raw_mentions: dict[str, Any] = Field(default_factory=dict)
    ambiguities: list[str] = Field(default_factory=list)
    invalid_fields: list[str] = Field(default_factory=list)
    conflict_fields: list[str] = Field(default_factory=list)
    unsupported_fields: list[str] = Field(default_factory=list)
    action_recommendation: str | None = None
    next_question: str | None = None
    source: str = "fallback_contract_parser"
    notes: str | None = None


COORD_VALUE = r"(?:(?:[NSEWnsew东南西北](?![A-Za-z])\s*)?\d{1,3}(?:\.\d+)?(?:(?:\s*[°度-]\s*|\s+)\d{1,2}(?:\.\d+)?\s*(?:[′'分])?)?(?:\s*[NSEWnsew东南西北](?![A-Za-z]))?)"
TIME_VALUE = r"(?:2?20\d{2}[-/年]\d{1,2}[-/月]\d{1,2}(?:日)?(?:[ T]\d{1,2}(?:(?::|：)?\d{2})(?:(?::|：)\d{1,2})?)?)"


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip(" ，,。；;")


def _last_match(pattern: str, text: str, flags: int = re.IGNORECASE) -> str:
    matches = list(re.finditer(pattern, text, flags=flags))
    if not matches:
        return ""
    return _clean(matches[-1].group(1))


def _extract_position_pair(text: str) -> tuple[str, str]:
    patterns = [
        rf"(?:位置|posn|position)[:：\s]*({COORD_VALUE})\s*[,，/]\s*({COORD_VALUE})",
        rf"(?:位置|posn|position)[:：\s]*({COORD_VALUE})\s+({COORD_VALUE})",
        rf"lat(?:itude)?[:：\s]*({COORD_VALUE}).*?lon(?:gitude|g)?[:：\s]*({COORD_VALUE})",
        rf"纬度[:：\s]*({COORD_VALUE}).*?经度[:：\s]*({COORD_VALUE})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL)
        if not match:
            continue
        first = _clean(match.group(1))
        second = _clean(match.group(2))
        if re.search(r"[NSns南北]", first) or re.search(r"[EWew东西]", second):
            return second, first
        if not re.search(r"[NSEWnsew东西南北]", first + second):
            try:
                first_num = abs(float(first))
                second_num = abs(float(second))
            except ValueError:
                first_num = second_num = 0
            if first_num <= 90 and second_num <= 180:
                return second, first
        return first, second
    return "", ""


def extract_ship_update_fields(text: str, perception: dict[str, Any] | None = None) -> dict[str, str]:
    return extract_ship_update_parameters_with_agent(text, perception).fields


def extract_ship_update_parameters_with_agent(text: str, perception: dict[str, Any] | None = None) -> ContractShipUpdateExtraction:
    """Format ship update parameters according to tool contracts.

    The production hook is intentionally contract-shaped: an LLM schema extractor
    can replace the body while preserving this I/O. The deterministic local
    implementation mirrors the expected agent output for tests and offline runs.
    """
    source_parts = [str(text or "")]
    perception = dict(perception or {})
    for key in ("visible_text", "summary", "visible_features", "recognized_text"):
        if perception.get(key):
            source_parts.append(str(perception[key]))
    source = "\n".join(source_parts)
    fields: dict[str, str] = {}
    raw_mentions: dict[str, str] = {}
    ambiguities: list[str] = []
    fields["mmsi"] = _last_match(r"(?:mmsi|MMSI|船舶识别号)?[:：\s]*\b(\d{9})\b", source)
    fields["imo"] = _last_match(r"(?:imo|IMO)[:：\s]*(\d{7})", source)
    fields["updatetime"] = _last_match(r"(?:更新时间|船位时间|UTC时间|update\s*time|updatetime|时间)[:：\s]*(" + TIME_VALUE + ")", source)
    if fields.get("updatetime"):
        raw_mentions["updatetime"] = fields["updatetime"]
        if re.search(r"\s\d{3,4}$", fields["updatetime"]):
            ambiguities.append("时间字段未使用冒号，按 HHMM 理解。")
    fields["lon"] = _last_match(r"(?:经度|lon|lng|longitude|Long\.)[:：\s]*(" + COORD_VALUE + ")", source)
    fields["lat"] = _last_match(r"(?:纬度|lat|latitude)[:：\s]*(" + COORD_VALUE + ")", source)
    pair_lon, pair_lat = _extract_position_pair(source)
    if pair_lon:
        fields["lon"] = pair_lon
    if pair_lat:
        fields["lat"] = pair_lat
    compact_pair = _extract_directional_coordinate_pair(source)
    if compact_pair[0] or compact_pair[1]:
        fields["lon"], fields["lat"] = compact_pair
    fields["speed"] = _last_match(r"(?:航速|速度|speed|sog|SOG|对地/水航速|对地航速)[:：\s]*(\d+(?:\.\d+)?)", source)
    heading, course = _extract_heading_course(source)
    fields["heading"] = heading or _last_match(r"(?:船首向|船艏向|航首向|heading|hdg|HDG)[:：\s]*(\d+(?:\.\d+)?)", source)
    fields["course"] = course or _last_match(r"(?:航迹向|航向|course|cog|COG)[:：\s]*(\d+(?:\.\d+)?)", source)
    fields["draft"] = _last_match(r"(?:吃水|当前吃水|draft|draught)[:：\s]*(\d+(?:\.\d+)?)", source)
    fields["navstatus"] = _extract_navstatus(source)
    fields["destination"] = _last_match(r"(?:目的港|destination|dest)[:：\s]*([A-Za-z0-9/_-]{2,40})", source)
    fields["eta"] = _last_match(r"(?:ETA|eta|预计到达|预抵时间|预抵)[:：\s]*(" + TIME_VALUE + ")", source)
    dest_eta = re.search(r"(?:目的港/ETA|destination/eta)[:：\s]*([A-Za-z0-9/_-]{2,40})\s*/\s*(" + TIME_VALUE + ")", source, flags=re.IGNORECASE)
    if dest_eta:
        fields["destination"] = _clean(dest_eta.group(1))
        fields["eta"] = _clean(dest_eta.group(2))
    fields = clean_optional_voyage_fields(_stringify_fields({key: value for key, value in fields.items() if value}))
    raw_mentions.update(fields)
    operation_type = _infer_operation_type(source, fields)
    normalized = normalize_ship_update_fields(fields)
    missing_fields = list(normalized.missing_required_fields) if operation_type in {"position_update", "mixed_update"} else []
    invalid_fields = []
    if normalized.position_error:
        invalid_fields.append(normalized.position_error)
    if normalized.updatetime_error and normalized.raw_updatetime:
        invalid_fields.append(normalized.updatetime_error)
    invalid_fields.extend(normalized.suspicious_fields)
    action_allowed = normalized.can_write if operation_type in {"position_update", "mixed_update"} else bool(fields.get("mmsi"))
    return ContractShipUpdateExtraction(
        operation_type=operation_type,
        fields=fields,
        raw_mentions=raw_mentions,
        confidence={key: 0.9 for key in fields},
        ambiguities=ambiguities,
        missing_fields=missing_fields,
        invalid_fields=invalid_fields,
        unsupported_fields=[],
        action_allowed=action_allowed,
        source="fallback_contract_parser",
    )


def _extract_heading_course(text: str) -> tuple[str, str]:
    separate = re.search(
        r"(?:船首向|船艏向|航首向|heading|hdg)[:：\s]*(\d+(?:\.\d+)?)°?.{0,12}?(?:航迹向|航向|course|cog)[:：\s]*(\d+(?:\.\d+)?)",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if separate:
        return _clean(separate.group(1)), _clean(separate.group(2))
    match = re.search(
        r"(?:船艏/航迹向|船首向/航迹向|heading/course|hdg/cog)[:：\s]*(\d+(?:\.\d+)?)°?\s*/\s*(\d+(?:\.\d+)?)",
        text,
        flags=re.IGNORECASE,
    )
    if not match:
        return "", ""
    return _clean(match.group(1)), _clean(match.group(2))


def _extract_directional_coordinate_pair(text: str) -> tuple[str, str]:
    coord = r"\d{1,3}(?:[°度-]\s*|\s+)\d{1,2}(?:\.\d+)?\s*(?:[′'分])?\s*[NSEWnsew东南西北]"
    matches = [_clean(item.group(0)) for item in re.finditer(coord, text or "", flags=re.IGNORECASE)]
    if len(matches) < 2:
        return "", ""
    lon = next((item for item in matches if re.search(r"[EWew东西]", item)), "")
    lat = next((item for item in matches if re.search(r"[NSns南北]", item)), "")
    return lon, lat


def _extract_navstatus(text: str) -> str:
    labeled = _last_match(r"(?:航行状态|状态|navstatus)[:：\s]*([^\s，,。；;]+)", text)
    if labeled:
        return labeled
    lines = [_clean(line) for line in str(text or "").splitlines()]
    for status in NAV_STATUS_VALUES:
        if status in lines:
            return status
    for alias, canonical in NAV_STATUS_ALIASES.items():
        if alias in lines:
            return canonical
    for status in NAV_STATUS_VALUES:
        if re.search(rf"(?<![\u4e00-\u9fff]){re.escape(status)}(?![\u4e00-\u9fff])", text):
            return status
    return ""


def _infer_operation_type(text: str, fields: dict[str, str]) -> str:
    lowered = str(text or "").lower()
    if re.search(r"(为什么|为何|怎么|如何|怎样).{0,30}(船位|ais|目的港|eta|更新|刷新).{0,30}(慢|延迟|不更新|不刷新|旧|没变)", text, flags=re.IGNORECASE) or re.search(
        r"(船位|ais|目的港|eta).{0,18}(慢|延迟|不更新|不刷新|旧|没变|异常|正常但)",
        text,
        flags=re.IGNORECASE,
    ):
        return "data_delay_troubleshooting"
    if re.search(
        r"(怎么|如何|怎样|能不能|是否|可以).{0,30}(平台|前台|按钮|入口|手动|邮件|邮箱|mail).{0,30}(更新|修改|编辑).{0,30}(目的港|eta|船位)?",
        text,
        flags=re.IGNORECASE,
    ):
        return "frontend_capability_question"
    static_markers = ("静态信息", "静态", "档案")
    static_update_markers = (
        r"(?:更新|修改|改|补充)\s*(?:船型|船长|船宽|载重吨|呼号|船旗|建造年份|目的港|ETA|eta|预抵)",
        r"(?:ship_type|length|width|dwt|callsign|flag|built_year)\s*[:：]",
    )
    position_markers = ("船位", "位置", "经度", "纬度", "航速", "航首向", "船首向", "船艏向", "航迹向", "航行状态")
    has_position = any(marker in text for marker in position_markers) or any(field in fields for field in POSITION_UPDATE_CONTRACT.supported_fields if field not in {"mmsi", "ship_name"})
    static_only_fields = {"ship_type", "minotype", "length", "width", "dwt", "flag", "callsign", "built_year"}
    has_static = (
        any(marker in text for marker in static_markers)
        or any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in static_update_markers)
        or any(field in fields for field in static_only_fields)
        or bool(re.search(r"(目的港|ETA|eta|预抵).{0,12}(错误|有误|不对|错了)", text, flags=re.IGNORECASE))
    )
    if has_position and has_static:
        return "mixed_update"
    if has_static and not has_position:
        return "static_update"
    if has_position:
        return "position_update"
    if "update" in lowered or "更新" in text or "修改" in text:
        return "ambiguous_update"
    return "unknown"


def build_ship_update_extraction(raw_fields: dict[str, str], normalized: NormalizedShipUpdate) -> ShipPositionUpdateExtraction:
    contract = ContractShipUpdateExtraction(
        operation_type="position_update" if any(key in raw_fields for key in ("lon", "lat", "updatetime", "heading", "course", "speed", "draft", "navstatus")) else "unknown",
        fields=dict(raw_fields),
        raw_mentions=dict(raw_fields),
        confidence={key: 0.9 for key in raw_fields},
        missing_fields=list(normalized.missing_required_fields),
        invalid_fields=[item for item in [normalized.position_error, normalized.updatetime_error, *normalized.suspicious_fields] if item],
        action_allowed=normalized.can_write,
    )
    return ShipPositionUpdateExtraction(
        operation="upload_ship_position",
        mmsi=normalized.mmsi or None,
        imo=normalized.imo or None,
        ship_name=normalized.ship_name or None,
        raw_updatetime=normalized.raw_updatetime or None,
        normalized_updatetime=normalized.normalized_updatetime or None,
        updatetime_valid=normalized.updatetime_valid,
        updatetime_error=normalized.updatetime_error or None,
        updatetime_suggestion=normalized.updatetime_suggestion or None,
        longitude_raw=normalized.longitude_raw or None,
        latitude_raw=normalized.latitude_raw or None,
        longitude_decimal=normalized.longitude_decimal,
        latitude_decimal=normalized.latitude_decimal,
        longitude_valid=normalized.longitude_valid,
        latitude_valid=normalized.latitude_valid,
        position_error=normalized.position_error or None,
        speed=normalized.speed,
        heading=normalized.heading,
        course=normalized.course,
        draft=normalized.draft,
        nav_status=normalized.nav_status or None,
        destination=normalized.destination or None,
        eta=normalized.eta or None,
        field_confidence={key: 0.9 for key in raw_fields},
        missing_required_fields=list(normalized.missing_required_fields),
        suspicious_fields=list(normalized.suspicious_fields),
        can_write=normalized.can_write,
        need_user_confirmation=normalized.need_user_confirmation,
        user_confirmation_message=normalized.user_confirmation_message or None,
        raw_fields=dict(raw_fields),
        operation_type=contract.operation_type,
        fields=dict(contract.fields),
        ship_identity=dict(contract.ship_identity),
        position_update_fields=dict(contract.position_update_fields),
        static_update_fields=dict(contract.static_update_fields),
        raw_mentions=dict(contract.raw_mentions),
        ambiguities=list(contract.ambiguities),
        invalid_fields=list(contract.invalid_fields),
        conflict_fields=list(contract.conflict_fields),
        unsupported_fields=list(contract.unsupported_fields),
        action_recommendation=contract.action_recommendation,
        next_question=contract.next_question,
        source=contract.source,
        notes=normalized.notes or None,
    )


def extract_and_normalize_ship_update(text: str, perception: dict[str, Any] | None = None) -> tuple[ShipPositionUpdateExtraction, NormalizedShipUpdate]:
    contract = extract_ship_update_parameters_with_agent(text, perception)
    return extract_and_normalize_ship_update_contract(contract.model_dump())


def extract_and_normalize_ship_update_contract(contract_payload: dict[str, Any]) -> tuple[ShipPositionUpdateExtraction, NormalizedShipUpdate]:
    normalized_payload = normalize_contract_payload(contract_payload or {})
    contract = ContractShipUpdateExtraction.model_validate(normalized_payload)
    contract.fields = _stringify_fields(contract.fields)
    contract.raw_mentions = _stringify_fields(contract.raw_mentions)
    normalized = normalize_ship_update_fields(contract.fields)
    extraction = build_ship_update_extraction(contract.fields, normalized)
    extraction.operation_type = contract.operation_type
    extraction.fields = dict(contract.fields)
    extraction.ship_identity = dict(contract.ship_identity)
    extraction.position_update_fields = dict(contract.position_update_fields)
    extraction.static_update_fields = dict(contract.static_update_fields)
    extraction.raw_mentions = dict(contract.raw_mentions)
    extraction.ambiguities = list(contract.ambiguities)
    extraction.invalid_fields = list(contract.invalid_fields)
    extraction.conflict_fields = list(contract.conflict_fields)
    extraction.unsupported_fields = list(contract.unsupported_fields)
    extraction.action_recommendation = contract.action_recommendation
    extraction.next_question = contract.next_question
    extraction.source = contract.source
    return extraction, normalized


def normalize_contract_payload(payload: dict[str, Any]) -> dict[str, Any]:
    data = dict(payload or {})
    fields = dict(data.get("fields") or {})
    ship_identity = dict(data.get("ship_identity") or {})
    position_fields = dict(data.get("position_update_fields") or {})
    static_fields = dict(data.get("static_update_fields") or {})
    operation_type = str(data.get("operation_type") or "unknown")

    alias_map = {
        "name": "ship_name",
        "imonumber": "imo",
        "type": "ship_type",
        "buildyear": "built_year",
        "draught": "draft",
        "status": "navstatus",
    }

    def merge(source: dict[str, Any]) -> None:
        for key, value in source.items():
            mapped = alias_map.get(str(key), str(key))
            if mapped in {"destination", "eta"} and clean_optional_voyage_fields({mapped: value}).get(mapped) is None:
                continue
            if value not in (None, "") and not fields.get(mapped):
                fields[mapped] = value

    merge(ship_identity)
    if operation_type == "static_update":
        merge(static_fields)
    elif operation_type == "position_update":
        merge(position_fields)
    else:
        merge(position_fields)
        merge(static_fields)
    if ship_identity.get("mmsi") and not fields.get("mmsi"):
        fields["mmsi"] = ship_identity["mmsi"]

    missing = list(data.get("missing_fields") or data.get("missing_required_fields") or [])
    fields = clean_optional_voyage_fields(fields)
    position_fields = clean_optional_voyage_fields(position_fields)
    static_fields = clean_optional_voyage_fields(static_fields)
    fields = _sync_static_ship_type_fields(fields)
    static_fields = _sync_static_ship_type_fields(static_fields)
    raw_mentions = clean_optional_voyage_fields(dict(data.get("raw_mentions") or {}))
    data["fields"] = fields
    data["ship_identity"] = ship_identity
    data["position_update_fields"] = position_fields
    data["static_update_fields"] = static_fields
    data["raw_mentions"] = raw_mentions
    data["missing_fields"] = [str(item) for item in missing]
    data["invalid_fields"] = [str(item) for item in list(data.get("invalid_fields") or [])]
    data["conflict_fields"] = [str(item) for item in list(data.get("conflict_fields") or [])]
    data["unsupported_fields"] = [str(item) for item in list(data.get("unsupported_fields") or [])]
    data["action_recommendation"] = str(data.get("action_recommendation") or "none")
    data["source"] = str(data.get("source") or "llm_contract_extractor")
    return data


def _sync_static_ship_type_fields(fields: dict[str, Any]) -> dict[str, Any]:
    result = dict(fields or {})
    ship_type = str(result.get("ship_type") or "").strip()
    minotype = str(result.get("minotype") or "").strip()
    if not (ship_type or minotype):
        return result
    if ship_type and minotype and ship_type != minotype:
        return result
    unified = ship_type or minotype
    result["ship_type"] = unified
    result["minotype"] = unified
    return result


def _stringify_fields(fields: dict[str, Any]) -> dict[str, str]:
    return {str(key): str(value).strip() for key, value in dict(fields or {}).items() if str(value or "").strip()}
