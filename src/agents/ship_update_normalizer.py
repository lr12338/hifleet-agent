"""Deterministic normalization and validation for ship position updates."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime
import re
from typing import Any

from agents.ship_update_contract import normalize_nav_status


@dataclass
class NormalizedShipUpdate:
    mmsi: str = ""
    imo: str = ""
    ship_name: str = ""
    raw_updatetime: str = ""
    normalized_updatetime: str = ""
    updatetime_valid: bool = False
    updatetime_error: str = ""
    updatetime_suggestion: str = ""
    longitude_raw: str = ""
    latitude_raw: str = ""
    longitude_decimal: float | None = None
    latitude_decimal: float | None = None
    longitude_valid: bool = False
    latitude_valid: bool = False
    position_error: str = ""
    speed: float | None = None
    heading: float | None = None
    course: float | None = None
    draft: float | None = None
    nav_status: str = ""
    destination: str = ""
    eta: str = ""
    missing_required_fields: list[str] = field(default_factory=list)
    suspicious_fields: list[str] = field(default_factory=list)
    can_write: bool = False
    need_user_confirmation: bool = False
    user_confirmation_message: str = ""
    raw_fields: dict[str, str] = field(default_factory=dict)
    normalized_fields: dict[str, Any] = field(default_factory=dict)
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def normalize_coord(value: str, *, expected_axis: str) -> tuple[float | None, bool, str]:
    raw = str(value or "").strip()
    if not raw:
        return None, False, "missing"
    compact = (
        raw.replace("′", "'")
        .replace("’", "'")
        .replace("‘", "'")
        .replace("″", '"')
        .replace("“", '"')
        .replace("”", '"')
        .strip()
    )
    compact = re.sub(r"\s+", " ", compact)
    direction_match = re.search(r"([NSEWnsew东西南北])", compact)
    direction = direction_match.group(1).upper() if direction_match else ""
    direction = {"东": "E", "西": "W", "南": "S", "北": "N"}.get(direction, direction)

    numbers = re.findall(r"\d+(?:\.\d+)?", compact)
    if not numbers:
        return None, False, "invalid_number"
    is_dmm = "°" in compact or "度" in compact or len(numbers) >= 2
    if not direction and is_dmm:
        return None, False, "missing_direction"
    if direction and expected_axis == "lon" and direction not in {"E", "W"}:
        return None, False, "wrong_direction"
    if direction and expected_axis == "lat" and direction not in {"N", "S"}:
        return None, False, "wrong_direction"
    if is_dmm:
        degrees = float(numbers[0])
        minutes = float(numbers[1]) if len(numbers) >= 2 else 0.0
        seconds = float(numbers[2]) if len(numbers) >= 3 else 0.0
        decimal = degrees + minutes / 60.0 + seconds / 3600.0
    else:
        decimal = float(numbers[0])
    if raw.strip().startswith("-"):
        decimal = -abs(decimal)
    if direction in {"W", "S"}:
        decimal = -decimal
    if expected_axis == "lon" and not -180 <= decimal <= 180:
        return decimal, False, "out_of_range"
    if expected_axis == "lat" and not -90 <= decimal <= 90:
        return decimal, False, "out_of_range"
    return round(decimal, 6), True, ""


def normalize_time(value: str) -> tuple[str, bool, str, str]:
    raw = str(value or "").strip()
    if not raw:
        return "", False, "missing", ""
    cleaned = re.sub(r"\([^)]*\)", "", raw)
    cleaned = re.sub(r"（[^）]*）", "", cleaned).strip()
    cleaned = cleaned.replace("年", "-").replace("月", "-").replace("日", " ")
    cleaned = cleaned.replace("/", "-").replace("T", " ").replace("：", ":")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()

    time_tail = r"(?:\s+(\d{1,2})(?::?(\d{2})(?::(\d{1,2}))?)?)?"
    suspicious = re.search(r"\b(2?20\d{2})-(\d{1,2})-(\d{1,2})" + time_tail, cleaned)
    if suspicious:
        year = suspicious.group(1)
        if len(year) == 5 and year.startswith("22"):
            hour, minute, second = _normalize_time_parts(suspicious.group(4), suspicious.group(5), suspicious.group(6))
            suggestion = f"20{year[-2:]}-{int(suspicious.group(2)):02d}-{int(suspicious.group(3)):02d} {hour:02d}:{minute:02d}"
            if suspicious.group(6):
                suggestion += f":{second:02d}"
            return "", False, "年份格式疑似有误", suggestion

    match = re.search(r"\b(20\d{2})-(\d{1,2})-(\d{1,2})" + time_tail, cleaned)
    if not match:
        return "", False, "invalid_format", ""
    if not match.group(4):
        return "", False, "缺少具体时分", ""
    if not match.group(5) and not re.fullmatch(r"\d{3,4}", str(match.group(4) or "")):
        return "", False, "缺少具体时分", ""
    hour, minute, second = _normalize_time_parts(match.group(4), match.group(5), match.group(6))
    normalized = f"{int(match.group(1)):04d}-{int(match.group(2)):02d}-{int(match.group(3)):02d} {hour:02d}:{minute:02d}:{second:02d}"
    try:
        datetime.strptime(normalized, "%Y-%m-%d %H:%M:%S")
    except ValueError as exc:
        return "", False, str(exc), ""
    return normalized, True, "", ""


def _normalize_time_parts(hour_or_hhmm: str | None, minute: str | None, second: str | None) -> tuple[int, int, int]:
    first = str(hour_or_hhmm or "0").strip()
    if minute is None and re.fullmatch(r"\d{3,4}", first):
        padded = first.zfill(4)
        return int(padded[:2]), int(padded[2:]), int(second or 0)
    return int(first or 0), int(minute or 0), int(second or 0)


def _to_float(value: str) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(str(value).strip())
    except ValueError:
        return None


def normalize_ship_update_fields(raw_fields: dict[str, str]) -> NormalizedShipUpdate:
    fields = {key: str(value).strip() for key, value in dict(raw_fields or {}).items() if str(value or "").strip()}
    result = NormalizedShipUpdate(raw_fields=fields)
    result.mmsi = fields.get("mmsi", "")
    result.imo = fields.get("imo", "")
    result.ship_name = fields.get("ship_name", "")
    result.longitude_raw = fields.get("lon", "") or fields.get("longitude", "")
    result.latitude_raw = fields.get("lat", "") or fields.get("latitude", "")
    result.raw_updatetime = fields.get("updatetime", "") or fields.get("raw_updatetime", "")
    result.destination = fields.get("destination", "")
    result.eta = fields.get("eta", "")
    result.nav_status = normalize_nav_status(fields.get("navstatus", "") or fields.get("nav_status", ""))

    result.longitude_decimal, result.longitude_valid, lon_error = normalize_coord(result.longitude_raw, expected_axis="lon")
    result.latitude_decimal, result.latitude_valid, lat_error = normalize_coord(result.latitude_raw, expected_axis="lat")
    position_errors = []
    if lon_error and lon_error != "missing":
        position_errors.append(f"经度{lon_error}")
    if lat_error and lat_error != "missing":
        position_errors.append(f"纬度{lat_error}")
    result.position_error = "；".join(position_errors)

    result.normalized_updatetime, result.updatetime_valid, result.updatetime_error, result.updatetime_suggestion = normalize_time(result.raw_updatetime)

    result.speed = _to_float(fields.get("speed", ""))
    result.heading = _to_float(fields.get("heading", ""))
    result.course = _to_float(fields.get("course", ""))
    result.draft = _to_float(fields.get("draft", ""))
    if result.heading is not None and not 0 <= result.heading <= 360:
        result.suspicious_fields.append("船首向")
    if result.course is not None and not 0 <= result.course <= 360:
        result.suspicious_fields.append("航迹向")
    if result.speed is not None and result.speed > 80:
        result.suspicious_fields.append("航速")
    if result.draft is not None and result.draft > 40:
        result.suspicious_fields.append("吃水")

    if not result.mmsi:
        result.missing_required_fields.append("MMSI")
    if not result.longitude_raw:
        result.missing_required_fields.append("经度")
    elif not result.longitude_valid:
        result.missing_required_fields.append("有效经度")
    if not result.latitude_raw:
        result.missing_required_fields.append("纬度")
    elif not result.latitude_valid:
        result.missing_required_fields.append("有效纬度")
    if not result.raw_updatetime:
        result.missing_required_fields.append("更新时间")
    elif not result.updatetime_valid:
        result.missing_required_fields.append("具体时分" if result.updatetime_error == "缺少具体时分" else "有效更新时间")

    result.need_user_confirmation = bool(result.suspicious_fields or result.position_error or (result.raw_updatetime and not result.updatetime_valid))
    result.can_write = (
        bool(result.mmsi)
        and result.longitude_valid
        and result.latitude_valid
        and result.updatetime_valid
        and not result.suspicious_fields
        and not result.need_user_confirmation
    )
    result.normalized_fields = {
        "mmsi": result.mmsi,
        "lon": result.longitude_raw,
        "lat": result.latitude_raw,
        "longitude_decimal": result.longitude_decimal,
        "latitude_decimal": result.latitude_decimal,
        "updatetime": result.normalized_updatetime,
        "speed": result.speed,
        "heading": result.heading,
        "course": result.course,
        "draft": result.draft,
        "navstatus": result.nav_status,
        "destination": result.destination,
        "eta": result.eta,
    }
    result.user_confirmation_message = build_preflight_message(result)
    return result


def build_preflight_message(result: NormalizedShipUpdate) -> str:
    if result.updatetime_error == "年份格式疑似有误" and result.updatetime_suggestion:
        recognized = ["MMSI"]
        if result.longitude_valid and result.latitude_valid:
            recognized.append("经纬度")
        if result.speed is not None:
            recognized.append("航速")
        if result.heading is not None:
            recognized.append("船艏向")
        if result.course is not None:
            recognized.append("航迹向")
        if result.draft is not None:
            recognized.append("吃水")
        return (
            f"已识别到{'、'.join(recognized)}，但更新时间 {result.raw_updatetime} 疑似年份多输入了一个 2。"
            f"请确认是否为 {result.updatetime_suggestion}，确认后我再继续更新船位。"
        )
    if "missing_direction" in result.position_error:
        return "已识别到坐标数值，但缺少方向字母。请确认经度 E/W、纬度 N/S 后我再继续更新船位。"
    if result.suspicious_fields:
        return "以下字段看起来异常，请确认后我再继续更新船位：" + "、".join(result.suspicious_fields)
    if result.updatetime_error == "缺少具体时分":
        return f"更新时间 {result.raw_updatetime} 缺少具体时分。请补充时分，例如 2026-07-04 14:43，确认后我再继续更新船位。"
    missing = [item for item in result.missing_required_fields if item]
    if missing:
        return "更新船位缺少必填字段：" + "、".join(missing) + "。请补充后我再更新；当前仅会按本轮明确提供的信息写入。"
    if result.position_error:
        return f"坐标格式需要确认：{result.position_error}。请补充标准经纬度后我再继续更新。"
    return ""
