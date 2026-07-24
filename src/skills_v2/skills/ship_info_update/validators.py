"""Deterministic validation used before a draft can enter the commit state."""
from __future__ import annotations

from datetime import datetime
import re
from typing import Any


def _number(value: Any, *, field: str, minimum: float | None = None, maximum: float | None = None) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if (minimum is not None and parsed < minimum) or (maximum is not None and parsed > maximum):
        return None
    return parsed


def validate_position_update(fields: dict[str, Any]) -> list[str]:
    invalid: list[str] = []
    if not str(fields.get("mmsi", "")).isdigit() or len(str(fields.get("mmsi", ""))) != 9:
        invalid.append("mmsi")
    if _number(fields.get("lon"), field="lon", minimum=-180, maximum=180) is None:
        invalid.append("lon")
    if _number(fields.get("lat"), field="lat", minimum=-90, maximum=90) is None:
        invalid.append("lat")
    normalized_time = re.sub(r"\s+(?:UTC[+-]\d{1,2}|LT)$", "", str(fields.get("updatetime", "")).strip(), flags=re.I)
    try:
        datetime.strptime(normalized_time, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        invalid.append("updatetime")
    for name in ("speed", "draft"):
        if name in fields and _number(fields[name], field=name, minimum=0) is None:
            invalid.append(name)
    return invalid


def validate_static_update(fields: dict[str, Any]) -> list[str]:
    invalid: list[str] = []
    if not str(fields.get("mmsi", "")).isdigit() or len(str(fields.get("mmsi", ""))) != 9:
        invalid.append("mmsi")
    if len([key for key in fields if key != "mmsi" and fields[key] not in (None, "")]) == 0:
        invalid.append("update_fields")
    for name in ("length", "width"):
        if name in fields and _number(fields[name], field=name, minimum=0.000001) is None:
            invalid.append(name)
    for name in ("dwt", "draft"):
        if name in fields and _number(fields[name], field=name, minimum=0) is None:
            invalid.append(name)
    if "built_year" in fields and (not str(fields["built_year"]).isdigit() or not 1800 <= int(fields["built_year"]) <= datetime.now().year + 1):
        invalid.append("built_year")
    if fields.get("ship_type") and fields.get("minotype") and str(fields["ship_type"]).strip() != str(fields["minotype"]).strip():
        invalid.append("ship_type_minotype_conflict")
    return invalid
