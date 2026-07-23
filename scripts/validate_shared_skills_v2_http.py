#!/usr/bin/env python3
"""Run a redacted, safe external `/run` or `/stream_run` Skills V2 check."""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def _post(url: str, payload: dict, timeout: float) -> tuple[int, bytes]:
    request = Request(url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"), headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urlopen(request, timeout=timeout) as response:  # noqa: S310 - caller explicitly selects local/test endpoint.
            return int(response.status), response.read()
    except HTTPError as error:
        return int(error.code), error.read()
    except URLError as error:
        return 0, str(error.reason).encode("utf-8", errors="replace")


def _run_summary(http_status: int, body: bytes, profile: str, elapsed_ms: int) -> dict:
    try:
        result = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {"http_status": http_status, "profile": profile, "elapsed_ms": elapsed_ms, "status": "invalid_json"}
    metrics = dict(result.get("metrics") or {})
    route_trace = dict(result.get("route_trace") or {})
    shadow = dict(route_trace.get("skills_v2_shadow") or {})
    return {
        "http_status": http_status,
        "status": result.get("status"),
        "profile": profile,
        "runtime_mode": metrics.get("runtime_mode"),
        "model_calls": metrics.get("model_calls"),
        "tool_names": list(metrics.get("tool_names") or result.get("generated_tool_calls") or []),
        "guard_result": metrics.get("guard_result"),
        "draft_status": route_trace.get("draft_status"),
        "skills_runtime": metrics.get("skills_runtime"),
        "shadow": {
            "status": shadow.get("status"),
            "dry_run": shadow.get("dry_run"),
            "write_state": shadow.get("write_state"),
            "executed_tools": shadow.get("executed_tools"),
        } if shadow else {},
        "answer": result.get("generated_answer") or result.get("answer"),
        "elapsed_ms": elapsed_ms,
    }


def _stream_summary(http_status: int, body: bytes, profile: str, elapsed_ms: int) -> dict:
    text = body.decode("utf-8", errors="replace")
    events = [line.partition(":")[2].strip() for line in text.splitlines() if line.startswith("event:")]
    answer = ""
    for line in text.splitlines():
        if not line.startswith("data:"):
            continue
        try:
            payload = json.loads(line[5:].strip())
        except json.JSONDecodeError:
            continue
        content = dict(payload.get("content") or {})
        if payload.get("type") == "answer":
            answer = str(content.get("answer") or "")
    return {"http_status": http_status, "status": "success" if http_status == 200 else "failed", "profile": profile, "events": events, "answer": answer, "elapsed_ms": elapsed_ms}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:10123")
    parser.add_argument("--profile", choices=("customer_ceshi", "customer_support"), default="customer_ceshi")
    parser.add_argument("--message", default="你好，请简短说明你能协助什么。")
    parser.add_argument("--stream", action="store_true")
    parser.add_argument("--timeout", type=float, default=120)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    endpoint = "/stream_run" if args.stream else "/run"
    payload = {
        "user_id": "skills-v2-validation",
        "session_id": f"skills-v2-{args.profile}-validation",
        "source_channel": "websdk",
        "agent_profile": args.profile,
        "messages": [
            {"role": "system", "content": "请使用中文。不要执行任何写入操作。"},
            {"role": "user", "content": args.message},
        ],
    }
    started = time.monotonic()
    status, body = _post(args.base_url.rstrip("/") + endpoint, payload, args.timeout)
    elapsed_ms = int((time.monotonic() - started) * 1000)
    report = _stream_summary(status, body, args.profile, elapsed_ms) if args.stream else _run_summary(status, body, args.profile, elapsed_ms)
    serialized = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    if args.report:
        args.report.write_text(serialized + "\n", encoding="utf-8")
    print(serialized)
    return 0 if status == 200 else 1


if __name__ == "__main__":
    raise SystemExit(main())
