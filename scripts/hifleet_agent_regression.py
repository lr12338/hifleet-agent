#!/usr/bin/env python3
"""HiFleet customer_support routed-agent regression runner.

Default mode is safe for production-like environments: it exercises real read
APIs and validates write-operation gating without mutating ship data. Pass
--include-write with explicit coordinates to run the real update call.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from agents.customer_support_router import (  # noqa: E402
    classify_message,
    execute_complex_ship_chain,
    execute_knowledge_chain,
    execute_simple_ship_chain,
    execute_stats_chain,
    execute_update_chain,
    extract_entities,
    make_trace,
)
from skills import SkillLoader  # noqa: E402


READ_TOOL_NAMES = [
    "smart_search",
    "ship_search",
    "get_ship_position",
    "get_ship_archive",
    "get_psc_records",
    "get_area_traffic",
    "get_strait_traffic",
    "get_ship_trajectory",
    "get_ship_call_ports",
    "get_ship_voyages",
    "get_last_departure",
    "get_current_stop",
    "get_avoid_redsea_traffic",
    "search_ports",
    "get_port_detail",
]
WRITE_TOOL_NAMES = ["upload_ship_position", "update_ship_static_info"]


def _mask(text: str) -> str:
    text = text or ""
    for key in ("HIFLEET_API_KEY", "api_key", "hifleet_key1", "hifleet_key2", "HIFLEET_TTSE_KEY"):
        val = os.getenv(key, "")
        if val:
            text = text.replace(val, "***")
    return text


def _snippet(text: str, limit: int = 900) -> str:
    text = _mask(str(text or "")).strip()
    text = re.sub(r"\s+", " ", text)
    return text[:limit]


def _has_any(text: str, needles: list[str]) -> bool:
    lowered = text.lower()
    return any(n.lower() in lowered for n in needles)


def _tool_map(include_write: bool) -> dict[str, Any]:
    names = READ_TOOL_NAMES + (WRITE_TOOL_NAMES if include_write else [])
    tools = SkillLoader.get_tools_by_names(names)
    return {tool.name: tool for tool in tools}


def _run_routed_case(case: dict[str, Any], tools: dict[str, Any]) -> dict[str, Any]:
    query = case["query"]
    entities = extract_entities(query)
    decision = classify_message(query, entities)
    trace = make_trace(decision, entities, session_id="regression")
    started = time.perf_counter()
    error = ""
    output = ""
    try:
        if case.get("executor") == "knowledge":
            output = execute_knowledge_chain(query, decision, tools, trace)
        elif decision.route == "knowledge":
            output = execute_knowledge_chain(query, decision, tools, trace)
        elif decision.route == "ship_single":
            output = execute_simple_ship_chain(query, decision, entities, tools, trace)
        elif decision.route == "ship_stats":
            output = execute_stats_chain(query, entities, tools, trace)
        elif decision.route == "ship_complex":
            output = execute_complex_ship_chain(query, entities, tools, trace)
        elif decision.route == "ship_update":
            output = execute_update_chain(query, entities, tools, trace)
        else:
            error = f"unsupported route: {decision.route}"
    except Exception as exc:  # noqa: BLE001
        error = f"{type(exc).__name__}: {exc}"
    latency_ms = int((time.perf_counter() - started) * 1000)
    output_text = str(output or "")
    expected_route = case.get("expected_route")
    required = list(case.get("required_substrings") or [])
    forbidden = list(case.get("forbidden_substrings") or [])
    checks = {
        "route_ok": expected_route in (None, decision.route),
        "required_ok": all(_has_any(output_text, [item]) for item in required),
        "forbidden_ok": not any(_has_any(output_text, [item]) for item in forbidden),
        "no_exception": not error,
        "latency_ok": latency_ms <= int(case.get("max_latency_ms", 30000)),
    }
    passed = all(checks.values())
    return {
        "id": case["id"],
        "query": query,
        "route": decision.route,
        "task_type": decision.task_type,
        "tool_bundle": decision.tool_bundle,
        "tool_call_sequence": trace.tool_call_sequence,
        "loop_count": trace.loop_count,
        "check_result": trace.check_result,
        "fallback_reason": trace.fallback_reason,
        "answer_confidence": trace.answer_confidence,
        "latency_ms": latency_ms,
        "checks": checks,
        "passed": passed,
        "error": _mask(error),
        "snippet": _snippet(output_text),
    }


def build_cases(args: argparse.Namespace) -> list[dict[str, Any]]:
    end = datetime.now()
    start_30d = end - timedelta(days=30)
    start_7d = end - timedelta(days=7)
    today = end.strftime("%Y-%m-%d")
    yesterday = (end - timedelta(days=1)).strftime("%Y-%m-%d")
    return [
        {
            "id": "knowledge_glossary_fast",
            "query": "HiFleet 地图上的绿点是什么意思？",
            "expected_route": "knowledge",
            "executor": "knowledge",
            "required_substrings": ["绿点"],
            "max_latency_ms": 5000,
        },
        {
            "id": "ship_position_mmsi",
            "query": f"查询 MMSI {args.query_mmsi} 船位",
            "expected_route": "ship_single",
            "required_substrings": [args.query_mmsi, "MMSI"],
            "max_latency_ms": 15000,
        },
        {
            "id": "ship_position_name",
            "query": f"查询 {args.query_ship} 船位",
            "expected_route": "ship_single",
            "required_substrings": [args.query_mmsi, "MMSI"],
            "max_latency_ms": 20000,
        },
        {
            "id": "ship_archive_mmsi",
            "query": f"查询 MMSI {args.query_mmsi} 船舶档案",
            "expected_route": "ship_single",
            "required_substrings": ["基本", "MMSI"],
            "max_latency_ms": 20000,
        },
        {
            "id": "ship_psc_imo",
            "query": "查询 IMO 9613886 PSC 检查记录",
            "expected_route": "ship_single",
            "required_substrings": ["PSC", "检查"],
            "max_latency_ms": 20000,
        },
        {
            "id": "ship_complex_last_port_voyage",
            "query": (
                f"查询 MMSI {args.query_mmsi} 目的港是什么，"
                f"{start_30d.strftime('%Y-%m-%d')} 到 {end.strftime('%Y-%m-%d')} 最近挂靠港是否与航次一致"
            ),
            "expected_route": "ship_complex",
            "required_substrings": [args.query_mmsi, "当前船位"],
            "max_latency_ms": 45000,
        },
        {
            "id": "ship_complex_track_last_port",
            "query": (
                f"查询 {args.query_ship} 近期轨迹，"
                f"{start_30d.strftime('%Y-%m-%d')} 到 {end.strftime('%Y-%m-%d')} 上一次停靠在哪个港口"
            ),
            "expected_route": "ship_complex",
            "required_substrings": [args.query_mmsi, "历史轨迹", "上一离港"],
            "max_latency_ms": 60000,
        },
        {
            "id": "strait_traffic_mandeb",
            "query": f"查询曼德海峡 {yesterday} 到 {today} 通航统计",
            "expected_route": "ship_stats",
            "required_substrings": ["曼德", "合计"],
            "max_latency_ms": 20000,
        },
        {
            "id": "area_traffic_bbox",
            "query": "查询 bbox 118.8,32.1,119.0,32.2 区域内当前船舶列表",
            "expected_route": "ship_stats",
            "required_substrings": ["区域", "船"],
            "max_latency_ms": 20000,
        },
        {
            "id": "avoid_redsea_daily",
            "query": f"查询红海绕航 {start_7d.strftime('%Y-%m-%d')} 到 {today} 每日统计",
            "expected_route": "ship_stats",
            "required_substrings": ["获取数据成功"],
            "max_latency_ms": 20000,
        },
        {
            "id": "update_guard_missing_fields",
            "query": f"更新 MMSI {args.update_mmsi} 船位",
            "expected_route": "ship_update",
            "required_substrings": ["未提供", "更新"],
            "max_latency_ms": 5000,
        },
    ]


def append_write_case(cases: list[dict[str, Any]], args: argparse.Namespace) -> None:
    if not args.include_write:
        return
    if args.write_lon is None and args.write_lat is None and args.write_speed is None:
        raise SystemExit("--include-write requires at least one update field: --write-lon/--write-lat/--write-speed")
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    fields = []
    if args.write_lon is not None:
        fields.append(f"经度 {args.write_lon}")
    if args.write_lat is not None:
        fields.append(f"纬度 {args.write_lat}")
    if args.write_speed is not None:
        fields.append(f"航速 {args.write_speed}")
    cases.append(
        {
            "id": "update_position_real",
            "query": (
                f"更新 MMSI {args.update_mmsi} 船位 "
                f"{' '.join(fields)} 更新时间 {now}"
            ),
            "expected_route": "ship_update",
            "required_substrings": [args.update_mmsi],
            "forbidden_substrings": ["未提供任何可更新的数据"],
            "max_latency_ms": 20000,
        }
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Run HiFleet customer_support regression scenarios")
    parser.add_argument("--query-ship", default="yuming")
    parser.add_argument("--query-mmsi", default="414726000")
    parser.add_argument("--update-mmsi", default="710001")
    parser.add_argument("--include-write", action="store_true", help="Run real write API update; requires explicit lon/lat")
    parser.add_argument("--write-lon", default=None)
    parser.add_argument("--write-lat", default=None)
    parser.add_argument("--write-speed", default="0")
    parser.add_argument("--output", default=str(ROOT / "artifacts" / "hifleet_agent_regression_report.json"))
    args = parser.parse_args()

    include_write_tools = True
    tools = _tool_map(include_write_tools)
    cases = build_cases(args)
    append_write_case(cases, args)

    results = [_run_routed_case(case, tools) for case in cases]
    summary = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "query_ship": args.query_ship,
        "query_mmsi": args.query_mmsi,
        "update_mmsi": args.update_mmsi,
        "include_write": args.include_write,
        "total": len(results),
        "passed": sum(1 for item in results if item["passed"]),
        "failed": sum(1 for item in results if not item["passed"]),
        "results": results,
    }

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps({k: v for k, v in summary.items() if k != "results"}, ensure_ascii=False, indent=2))
    for item in results:
        status = "PASS" if item["passed"] else "FAIL"
        print(f"{status} {item['id']} route={item['route']} latency={item['latency_ms']}ms tools={item['tool_call_sequence']}")
        if item["error"]:
            print(f"  error={item['error']}")
        if not item["passed"]:
            print(f"  checks={item['checks']}")
            print(f"  snippet={item['snippet']}")
    print(f"REPORT {out_path}")
    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
