#!/usr/bin/env python3
"""Create conservative customer_ceshi regression candidates from sanitized dialog JSONL."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


def _scenario(record: dict[str, Any]) -> str:
    category = str(record.get("business_category") or "")
    if "船位更新" in category:
        return "position_update"
    if "静态" in category or "目的港" in category or "ETA" in category:
        return "static_update"
    if "船舶查询" in category:
        return "ship_lookup"
    if "会员" in category or "权限" in category:
        return "membership_permissions"
    if "平台" in category:
        return "platform_operation"
    if "投诉" in category:
        return "complaint_feedback"
    return "troubleshooting"


def _case(record: dict[str, Any]) -> dict[str, Any]:
    scenario = _scenario(record)
    risk = str(record.get("risk_level") or "P2")
    user_input = str(record.get("user_input") or "")
    forbidden = list(record.get("forbidden_claims") or [])
    expected_tools = []
    expected_any_tools: list[str] = []
    ambiguities: list[str] = []
    if scenario == "ship_lookup":
        expected_tools = ["ship_search", "get_ship_position"]
    elif scenario in {"platform_operation", "membership_permissions"}:
        expected_any_tools = ["local_kb_search", "web_search", "verify_public_page", "agent_browser_deep_search"]
    elif scenario == "position_update":
        expected_tools = ["prepare_ship_update"]
        if re.search(r"\b\d{5}-\d{1,2}-\d{1,2}\b", user_input) or not re.search(r"(?<!\d)\d{9}(?!\d)", user_input):
            expected_tools = []
            if re.search(r"\b\d{5}-\d{1,2}-\d{1,2}\b", user_input):
                ambiguities.append("suspicious_five_digit_year_requires_user_confirmation")
            if not re.search(r"(?<!\d)\d{9}(?!\d)", user_input):
                ambiguities.append("current_turn_mmsi_required")
    if scenario == "static_update":
        forbidden.extend(["不得根据内部写工具断言用户前台有编辑入口", "不得承诺立即生效"])
    status = "manual_review_required" if risk in {"P0", "P1"} else "validated"
    return {
        "case_id": str(record.get("case_id") or ""),
        "scenario": scenario,
        "task_type": scenario,
        "input_messages": [{"role": "user", "content": user_input}],
        "provided_fields": {},
        "missing_fields": [],
        "ambiguities": ambiguities,
        "expected_tools": expected_tools,
        "expected_any_tools": expected_any_tools,
        "forbidden_tools": ["upload_ship_position", "update_ship_static_info"],
        "required_claims": [],
        "forbidden_claims": sorted(set(forbidden)),
        "reply_contract": {"max_chinese_chars": 180, "do_not_use_agent_reply_as_gold": True},
        "risk_level": risk,
        "gold_status": status,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=Path("reports/customer_support_dialogs/dialog_cases.jsonl"))
    parser.add_argument("--output-dir", type=Path, default=Path("reports/customer_ceshi_eval"))
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()
    records = [json.loads(line) for line in args.input.read_text(encoding="utf-8").splitlines() if line.strip()]
    if args.limit:
        records = records[: args.limit]
    cases = [_case(record) for record in records]
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "validated_cases.json").write_text(json.dumps(cases, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    manual = [case for case in cases if case["gold_status"] == "manual_review_required"]
    (args.output_dir / "manual_review_required.json").write_text(json.dumps(manual, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    matrix: dict[str, int] = {}
    for case in cases:
        matrix[case["scenario"]] = matrix.get(case["scenario"], 0) + 1
    (args.output_dir / "scenario_matrix.json").write_text(json.dumps(matrix, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = ["# customer_ceshi Validated Case Candidates", "", "Old agent replies are not Gold Answers. P0/P1 cases require independent/human review.", "", f"- Candidates: {len(cases)}", f"- Manual review required: {len(manual)}", ""]
    lines.extend(f"- `{name}`: {count}" for name, count in sorted(matrix.items()))
    (args.output_dir / "validated_cases.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
