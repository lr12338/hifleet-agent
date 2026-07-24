from __future__ import annotations

import hashlib
import json
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


_COORDINATE = re.compile(r"(?P<value>\d{1,3}(?:\.\d+)?)(?:\s*(?:°|-|\s)\s*(?P<minutes>\d{1,2}(?:\.\d+)?)[′']?(?:\s*(?P<seconds>\d{1,2}(?:\.\d+)?)″?)?)?\s*(?P<hemisphere>[NSEW])", re.I)
_PREFIX_COORDINATE = re.compile(r"(?<![\d°′'])(?P<hemisphere>[NSEW])(?P<value>\d{1,3}(?:\.\d+)?)(?:\s*(?:°|-|\s)\s*(?P<minutes>\d{1,2}(?:\.\d+)?)[′']?(?:\s*(?P<seconds>\d{1,2}(?:\.\d+)?)″?)?)?", re.I)
_LABELLED_DECIMAL = re.compile(r"(?P<label>经度|longitude|lon|纬度|latitude|lat)\s*[:：]?\s*(?P<value>[+-]?\d{1,3}(?:\.\d+)?)(?![\d.°′'″])", re.I)
_LABELLED_DMS = re.compile(r"(?P<label>经度|longitude|lon|纬度|latitude|lat)\s*[:：]?\s*(?P<degrees>\d{1,3}(?:\.\d+)?)\s*°\s*(?P<minutes>\d{1,2}(?:\.\d+)?)[′']?(?:\s*(?P<seconds>\d{1,2}(?:\.\d+)?)″?\s*)?(?P<hemisphere>[NSEW])?", re.I)
_TIME = re.compile(r"^(?P<year>\d{4,5})[-/](?P<month>\d{1,2})[-/](?P<day>\d{1,2})\s+(?P<hour>\d{1,2})(?::?(?P<minute>\d{2}))(?::(?P<second>\d{2}))?(?:\s*\(?(?P<tz>UTC(?:[+-]\d{1,2})?)\)?)?$", re.I)
_PLACEHOLDER = re.compile(r"^(?:--(?:\s*/\s*--)?|-|—|－|n/?a|未知|无|null|none)$", re.I)


def _coordinate_value(match: re.Match[str]) -> tuple[str, float, str]:
    degree = float(match.group("value"))
    minutes = float(match.group("minutes") or 0)
    seconds = float(match.group("seconds") or 0)
    hemisphere = match.group("hemisphere").upper()
    value = degree + minutes / 60 + seconds / 3600
    if hemisphere in {"W", "S"}:
        value = -value
    return ("longitude" if hemisphere in {"E", "W"} else "latitude"), value, match.group(0)


class PositionNormalizer:
    def normalize(self, text: str) -> dict[str, Any]:
        values: dict[str, float] = {}
        originals: dict[str, str] = {}
        for match in list(_PREFIX_COORDINATE.finditer(text or "")) + list(_COORDINATE.finditer(text or "")):
            key, value, original = _coordinate_value(match)
            values.setdefault(key, value)
            originals.setdefault(key, original)
        for match in _LABELLED_DECIMAL.finditer(text or ""):
            label = match.group("label").lower()
            key = "longitude" if label in {"经度", "longitude", "lon"} else "latitude"
            values[key] = float(match.group("value"))
            originals[key] = match.group(0)
        for match in _LABELLED_DMS.finditer(text or ""):
            hemisphere = (match.group("hemisphere") or "").upper()
            if hemisphere:
                key = "longitude" if hemisphere in {"E", "W"} else "latitude"
            else:
                label = match.group("label").lower()
                key = "longitude" if label in {"经度", "longitude", "lon"} else "latitude"
            degree = float(match.group("degrees"))
            minutes = float(match.group("minutes") or 0)
            seconds = float(match.group("seconds") or 0)
            value = degree + minutes / 60 + seconds / 3600
            if hemisphere in {"W", "S"}:
                value = -value
            values.setdefault(key, value)
            originals.setdefault(key, match.group(0))
        errors = []
        if "longitude" in values and not -180 <= values["longitude"] <= 180:
            errors.append("longitude_out_of_range")
        if "latitude" in values and not -90 <= values["latitude"] <= 90:
            errors.append("latitude_out_of_range")
        return {**values, "original_values": originals, "confidence": "deterministic" if len(values) == 2 and not errors else "low", "validation_errors": errors}


class TimeNormalizer:
    def normalize(self, text: str) -> dict[str, Any]:
        value = (text or "").strip()
        match = _TIME.fullmatch(value)
        if not match:
            return {"value": None, "requires_confirmation": False, "validation_errors": ["unsupported_time_format"]}
        parts = match.groupdict()
        year = int(parts["year"])
        requires_confirmation = len(parts["year"]) == 5
        if requires_confirmation and parts["year"].startswith("2"):
            year = int(parts["year"][1:])
        try:
            parsed = datetime(year, int(parts["month"]), int(parts["day"]), int(parts["hour"]), int(parts["minute"]), int(parts["second"] or 0))
        except ValueError:
            return {"value": None, "requires_confirmation": requires_confirmation, "validation_errors": ["invalid_time"]}
        suffix = f" {parts['tz'].upper()}" if parts.get("tz") else ""
        return {"value": parsed.strftime("%Y-%m-%d %H:%M:%S") + suffix, "requires_confirmation": requires_confirmation, "validation_errors": []}


class ShipIdentityNormalizer:
    def normalize(self, text: str) -> dict[str, Any]:
        mmsi = re.search(r"(?<!\d)(\d{9})(?!\d)", text or "")
        imo = re.search(r"\bIMO\s*[:：#-]?\s*(\d{7})\b", text or "", re.I)
        return {
            "mmsi": mmsi.group(1) if mmsi else "",
            "imo": imo.group(1) if imo else "",
            "confidence": "deterministic" if mmsi else "low",
        }


class StaticFieldNormalizer:
    _ALIASES = {
        "ship_name": ("AIS船名", "船名", "ship name", "name"),
        "imo": ("IMO",),
        "ship_type": ("船型", "船舶类型", "类型", "ship type"),
        "flag": ("船旗", "船籍", "flag"),
        "callsign": ("呼号", "call sign", "callsign"),
        "built_year": ("建造年份", "建造年", "built year"),
        "destination": ("目的港", "destination"),
        "eta": ("ETA",),
        "draft": ("吃水", "draught", "draft"),
        "length": ("船长", "长度", "length"),
        "width": ("船宽", "宽度", "width"),
        "dwt": ("载重吨", "DWT"),
    }

    def normalize(self, text: str) -> dict[str, Any]:
        fields: dict[str, str] = {}
        invalid: list[str] = []
        for key, aliases in self._ALIASES.items():
            for alias in aliases:
                match = re.search(rf"(?:^|[，,；;\n\s]){re.escape(alias)}(?:\s*[:：=]\s*|\s*\n\s*)([^，,；;\n]+)", text or "", re.I)
                if match is None:
                    continue
                value = match.group(1).strip().strip("。.")
                if not value or _PLACEHOLDER.fullmatch(value):
                    invalid.append(key)
                else:
                    fields[key] = value
                break
        identity = ShipIdentityNormalizer().normalize(text)
        if identity["imo"] and "imo" not in fields:
            fields["imo"] = identity["imo"]
        compact = re.search(
            r"(?:目的港\s*/?\s*ETA|ETA)\s*[:：]?\s*([A-Za-z][A-Za-z0-9 _-]{1,80})\s*/\s*(\d{4}[-/]\d{1,2}[-/]\d{1,2}[\s,]+\d{1,2}:?\d{2}(?::\d{2})?(?:\s*\(?(?:UTC(?:[+-]\d{1,2})?|LT)\)?)?)",
            text or "",
            re.I,
        )
        if compact:
            fields.setdefault("destination", compact.group(1).strip())
            fields.setdefault("eta", compact.group(2).replace(",", " ").strip())
        if "destination" not in fields:
            after_mmsi = re.search(
                r"\b\d{9}\b\s*[，,：:]?\s*([A-Za-z][A-Za-z0-9 _-]{1,80})\s*/\s*(\d{4}[-/]\d{1,2}[-/]\d{1,2}\s+\d{1,2}:?\d{2}(?::\d{2})?(?:\s*\(?(?:UTC(?:[+-]\d{1,2})?|LT)\)?)?)",
                text or "",
                re.I,
            )
            if after_mmsi:
                fields["destination"] = after_mmsi.group(1).strip()
                fields.setdefault("eta", after_mmsi.group(2).strip())
        return {"fields": fields, "invalid_fields": sorted(set(invalid)), "confidence": "deterministic" if fields else "low"}


@dataclass
class ShipUpdateDraft:
    operation_type: str
    target: dict[str, Any]
    fields: dict[str, Any]
    field_sources: dict[str, str]
    missing_fields: list[str] = field(default_factory=list)
    ambiguities: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    requires_confirmation: bool = True
    draft_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    expires_at: int = field(default_factory=lambda: int(time.time()) + 600)

    @property
    def draft_hash(self) -> str:
        payload = repr((self.operation_type, self.target, sorted(self.fields.items()), self.expires_at))
        return hashlib.sha256(payload.encode()).hexdigest()[:16]


class ShipUpdateDraftStore:
    def __init__(self, path: str | Path | None = None) -> None:
        self._drafts: dict[str, ShipUpdateDraft] = {}
        configured = path or os.getenv("CUSTOMER_CESHI_DRAFT_STORE_PATH")
        self.path = Path(configured) if configured else None
        self._load()

    def _load(self) -> None:
        if self.path is None or not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            for key, value in dict(raw).items():
                draft = ShipUpdateDraft(**value)
                if draft.expires_at >= time.time():
                    self._drafts[str(key)] = draft
            self._persist()
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            self._drafts = {}

    def _persist(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {key: draft.__dict__ for key, draft in self._drafts.items()}
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        temporary.replace(self.path)

    def prepare(self, *, session_key: str, operation_type: str, target: dict[str, Any], fields: dict[str, Any], field_sources: dict[str, str]) -> ShipUpdateDraft:
        required = ["mmsi"] + (["longitude", "latitude", "updatetime"] if operation_type == "position_update" else [])
        missing = [name for name in required if not str(target.get(name) or fields.get(name) or "").strip()]
        draft = ShipUpdateDraft(operation_type=operation_type, target=target, fields=fields, field_sources=field_sources, missing_fields=missing)
        self._drafts[session_key] = draft
        self._persist()
        return draft

    def get(self, session_key: str) -> ShipUpdateDraft | None:
        draft = self._drafts.get(session_key)
        if draft is not None and draft.expires_at < time.time():
            self._drafts.pop(session_key, None)
            self._persist()
            return None
        return draft

    def cancel(self, session_key: str) -> None:
        self._drafts.pop(session_key, None)
        self._persist()
