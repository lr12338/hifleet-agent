#!/usr/bin/env python3
"""Conservatively measure customer_ceshi update parsing against independent evidence.

Legacy agent replies are never read as Gold. Position expectations are taken only
from successful historical structured upload-tool summaries; missing-field labels
come from the sanitized independent dialog review categories.
"""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from agents.customer_ceshi_responses.ship_updates import PositionNormalizer, ShipIdentityNormalizer, StaticFieldNormalizer, TimeNormalizer


_NUMBER = r"[-+]?\d+(?:\.\d+)?"
_EXPECTED_LONGITUDE = re.compile(rf"(?:经度|longitude)\s*[:：]\s*({_NUMBER})", re.I)
_EXPECTED_LATITUDE = re.compile(rf"(?:纬度|latitude)\s*[:：]\s*({_NUMBER})", re.I)
_EXPECTED_MMSI = re.compile(r"MMSI\s*[:：]\s*(\d{9})", re.I)
_TIME_CANDIDATE = re.compile(r"\d{4,5}[-/]\d{1,2}[-/]\d{1,2}\s+\d{1,2}:?\d{2}(?::\d{2})?(?:\s*\(?(?:UTC(?:[+-]\d{1,2})?)\)?)?", re.I)
_REFERENCE_COORDINATE = re.compile(r"(?P<degree>\d{1,3})\s*(?:°|-)\s*(?P<minutes>\d{1,2}(?:\.\d+)?)\s*(?:['′])?\s*(?P<hemisphere>[NSEW])", re.I)


def _summaries(record: dict[str, Any]) -> str:
    values: list[str] = []
    for tool in record.get("tools") or []:
        if not isinstance(tool, dict) or not tool.get("success"):
            continue
        values.append(str(tool.get("result_summary") or ""))
    return "\n".join(values)


def _expected_position(record: dict[str, Any]) -> dict[str, Any] | None:
    summary = _summaries(record)
    longitude = _EXPECTED_LONGITUDE.search(summary)
    latitude = _EXPECTED_LATITUDE.search(summary)
    mmsi = _EXPECTED_MMSI.search(summary)
    if not (longitude and latitude and mmsi):
        return None
    longitude_value = float(longitude.group(1))
    latitude_value = float(latitude.group(1))
    if not -180 <= longitude_value <= 180 or not -90 <= latitude_value <= 90:
        return None
    return {"mmsi": mmsi.group(1), "longitude": longitude_value, "latitude": latitude_value}


def _actual_position(text: str) -> dict[str, Any]:
    position = PositionNormalizer().normalize(text)
    identity = ShipIdentityNormalizer().normalize(text)
    match = _TIME_CANDIDATE.search(text)
    timestamp = TimeNormalizer().normalize(match.group(0) if match else "")
    return {"mmsi": identity["mmsi"], "longitude": position.get("longitude"), "latitude": position.get("latitude"), "coordinates_ok": position.get("confidence") == "deterministic", "time": timestamp}


def _reference_position(text: str) -> dict[str, float] | None:
    values: dict[str, float] = {}
    for match in _REFERENCE_COORDINATE.finditer(text or ""):
        value = float(match.group("degree")) + float(match.group("minutes")) / 60
        hemisphere = match.group("hemisphere").upper()
        if hemisphere in {"W", "S"}:
            value = -value
        values["longitude" if hemisphere in {"E", "W"} else "latitude"] = value
    return values if set(values) == {"longitude", "latitude"} else None


def _has_current_position_evidence(text: str) -> bool:
    actual = _actual_position(text)
    return bool(actual["mmsi"] and actual["coordinates_ok"] and actual["time"].get("value"))


def _is_current_static_update(text: str) -> bool:
    value = text.lower()
    return bool(
        re.search(r"(?<!\d)\d{9}(?!\d)", text)
        and any(token in value for token in ("更新", "上传", "修改", "更正", "录入"))
        and any(token in value for token in ("目的港", "eta", "船名", "name", "船型", "类型", "船旗", "呼号", "吃水", "静态"))
    )


def _rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator * 100, 3) if denominator else None


def evaluate(records: list[dict[str, Any]]) -> dict[str, Any]:
    updates = [record for record in records if any(token in str(record.get("business_category") or "") for token in ("船位更新", "静态信息", "目的港", "ETA"))]
    checked = correct = 0
    failed_position_cases: list[str] = []
    tool_evidence_conflicts: list[str] = []
    reviewed_missing_checked = reviewed_missing_corrected = 0
    reviewed_missing_still_incomplete = 0
    static_checked = static_with_fields = 0
    static_cases: list[str] = []
    ambiguous_checked = ambiguous_correct = 0
    ambiguous_cases: list[str] = []

    for record in updates:
        category = str(record.get("business_category") or "")
        text = str(record.get("user_input") or "")
        case_id = str(record.get("case_id") or "")
        if "船位更新" in category:
            expected = _expected_position(record)
            actual = _actual_position(text)
            if expected is not None and _has_current_position_evidence(text):
                reference = _reference_position(text)
                if reference is not None and (
                    abs(reference["longitude"] - expected["longitude"]) >= 1e-4
                    or abs(reference["latitude"] - expected["latitude"]) >= 1e-4
                ):
                    tool_evidence_conflicts.append(case_id)
                    continue
                checked += 1
                position_ok = (
                    actual["mmsi"] == expected["mmsi"]
                    and actual["coordinates_ok"]
                    and abs(float(actual["longitude"]) - expected["longitude"]) < 1e-4
                    and abs(float(actual["latitude"]) - expected["latitude"]) < 1e-4
                )
                if position_ok:
                    correct += 1
                else:
                    failed_position_cases.append(case_id)
            if ("缺经度" in category or "缺时间" in category) and "[image_url]" not in text and text.strip() not in {"确认", "确认。"}:
                reviewed_missing_checked += 1
                if actual["coordinates_ok"] and actual["time"].get("value"):
                    reviewed_missing_corrected += 1
                else:
                    reviewed_missing_still_incomplete += 1
            if re.search(r"\b\d{5}-\d{1,2}-\d{1,2}\b", text):
                ambiguous_checked += 1
                if actual["time"].get("requires_confirmation"):
                    ambiguous_correct += 1
                else:
                    ambiguous_cases.append(case_id)
        if any(token in category for token in ("静态信息", "目的港", "ETA")) and _is_current_static_update(text):
            static_checked += 1
            fields = StaticFieldNormalizer().normalize(text)
            if fields["fields"]:
                static_with_fields += 1
            else:
                static_cases.append(case_id)

    return {
        "source": "sanitized_dialog_cases; legacy_agent_reply_excluded",
        "update_case_count": len(updates),
        "position_tool_evidence": {
            "checked": checked,
            "correct": correct,
            "accuracy_percent": _rate(correct, checked),
            "failed_case_ids": failed_position_cases[:100],
            "excluded_conflicting_legacy_tool_evidence": tool_evidence_conflicts[:100],
        },
        "legacy_missing_field_label_correction": {
            "checked": reviewed_missing_checked,
            "current_parser_detected_complete_fields": reviewed_missing_corrected,
            "current_parser_still_incomplete": reviewed_missing_still_incomplete,
        },
        "ambiguous_year_safety": {
            "checked": ambiguous_checked,
            "correct": ambiguous_correct,
            "accuracy_percent": _rate(ambiguous_correct, ambiguous_checked),
            "failed_case_ids": ambiguous_cases[:100],
        },
        "static_field_coverage": {
            "checked": static_checked,
            "with_at_least_one_valid_field": static_with_fields,
            "coverage_percent": _rate(static_with_fields, static_checked),
            "missing_case_ids": static_cases[:100],
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=Path("reports/customer_support_dialogs/dialog_cases.jsonl"))
    parser.add_argument("--output", type=Path, default=Path("reports/customer_ceshi_eval/parser_metrics.json"))
    args = parser.parse_args()
    records = [json.loads(line) for line in args.input.read_text(encoding="utf-8").splitlines() if line.strip()]
    metrics = evaluate(records)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(metrics, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(metrics, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
