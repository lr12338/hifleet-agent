#!/usr/bin/env python3
"""Execute P0/P1 customer_ceshi HTTP candidates without retaining prompts or replies.

The runner is operational-only: it validates response status, tool boundaries and
reply-length contracts. It does not treat legacy answers or model output as gold.
Write paths remain governed by the customer_ceshi draft/dry-run configuration.
"""
from __future__ import annotations

import argparse
import json
import time
import uuid
from collections import Counter
from pathlib import Path
from typing import Any

import requests


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = ROOT / "reports/customer_ceshi_eval/manual_review_required.json"
DEFAULT_OUTPUT = ROOT / "reports/customer_ceshi_eval/p0_p1_http_e2e.json"


def _chinese_char_count(text: str) -> int:
    return sum("\u4e00" <= char <= "\u9fff" for char in text)


def _safe_result(case: dict[str, Any], response: requests.Response | None, elapsed_ms: int, error: str = "") -> dict[str, Any]:
    payload: dict[str, Any] = {}
    if response is not None:
        try:
            loaded = response.json()
            payload = loaded if isinstance(loaded, dict) else {}
        except ValueError:
            error = error or "non_json_response"
    metrics = payload.get("metrics") if isinstance(payload.get("metrics"), dict) else {}
    tool_names = [str(name) for name in payload.get("generated_tool_calls", []) if str(name)]
    expected = {str(name) for name in case.get("expected_tools", [])}
    expected_any = {str(name) for name in case.get("expected_any_tools", [])}
    forbidden = {str(name) for name in case.get("forbidden_tools", [])}
    answer = str(payload.get("generated_answer") or payload.get("candidate_answer") or payload.get("message") or "")
    max_chars = int((case.get("reply_contract") or {}).get("max_chinese_chars") or 0)
    checks = {
        "http_ok": response is not None and response.ok,
        "response_ok": str(payload.get("status") or "") in {"success", "degraded"},
        "expected_tools_ok": expected.issubset(set(tool_names)),
        "expected_any_tools_ok": not expected_any or bool(expected_any & set(tool_names)),
        "forbidden_tools_ok": not bool(forbidden & set(tool_names)),
        "reply_length_ok": not max_chars or _chinese_char_count(answer) <= max_chars,
        "no_error": not error,
    }
    return {
        "case_id": str(case.get("case_id") or ""),
        "scenario": str(case.get("scenario") or ""),
        "risk_level": str(case.get("risk_level") or ""),
        "http_status": response.status_code if response is not None else 0,
        "status": str(payload.get("status") or "failed"),
        "latency_ms": elapsed_ms,
        "tool_names": tool_names,
        "tool_calls": int(metrics.get("tool_calls") or len(tool_names)),
        "checks": checks,
        "passed": all(checks.values()),
        "error": error[:160],
    }


def run_case(case: dict[str, Any], *, base_url: str, timeout: int) -> dict[str, Any]:
    messages = case.get("input_messages") if isinstance(case.get("input_messages"), list) else []
    payload = {
        "messages": messages,
        "session_id": f"customer_ceshi:e2e:p0p1:{case.get('case_id')}:{uuid.uuid4().hex[:8]}",
        "user_id": "customer_ceshi_e2e",
        "source_channel": "customer_api",
        "agent_profile": "customer_ceshi",
    }
    started = time.perf_counter()
    try:
        response = requests.post(f"{base_url.rstrip('/')}/run", json=payload, timeout=timeout)
        return _safe_result(case, response, int((time.perf_counter() - started) * 1000))
    except requests.RequestException as exc:
        return _safe_result(case, None, int((time.perf_counter() - started) * 1000), type(exc).__name__)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--base-url", default="http://127.0.0.1:10123")
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()
    cases = json.loads(args.input.read_text(encoding="utf-8"))
    if not isinstance(cases, list):
        raise ValueError("input must be a JSON array")
    if args.limit:
        cases = cases[: args.limit]
    results = [run_case(case, base_url=args.base_url, timeout=args.timeout) for case in cases if isinstance(case, dict)]
    summary = {
        "generated_at": "2026-07-17",
        "kind": "operational_http_e2e_not_semantic_gold",
        "case_count": len(results),
        "passed": sum(item["passed"] for item in results),
        "failed": sum(not item["passed"] for item in results),
        "scenarios": dict(Counter(item["scenario"] for item in results)),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({"summary": summary, "results": results}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
