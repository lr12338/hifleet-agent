#!/usr/bin/env python3
"""Execute the public Shared Skills V2 cases through `/run` without masking gaps."""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import yaml


def _post(url: str, payload: dict, timeout: float) -> tuple[int, dict | None, str]:
    request = Request(url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"), headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urlopen(request, timeout=timeout) as response:  # noqa: S310 - caller supplies a test endpoint.
            body = response.read().decode("utf-8", errors="replace")
            return int(response.status), json.loads(body), ""
    except HTTPError as error:
        return int(error.code), None, error.read().decode("utf-8", errors="replace")[:500]
    except (URLError, json.JSONDecodeError) as error:
        return 0, None, str(error)[:500]


def _attachment_content(case: dict, base_url: str) -> tuple[list[dict], str]:
    attachment = case.get("attachment")
    if not attachment:
        return [], ""
    if not base_url:
        return [], "attachment_url_not_supplied"
    url = f"{base_url.rstrip('/')}/{str(attachment).lstrip('/')}"
    return [{"type": "image_url", "image_url": {"url": url}}], ""


def _evaluate(case: dict, result: dict) -> dict:
    metrics = dict(result.get("metrics") or {})
    tool_names = list(metrics.get("tool_names") or result.get("generated_tool_calls") or [])
    answer = str(result.get("generated_answer") or result.get("answer") or "")
    forbidden_tools = set(case.get("forbidden_tools") or [])
    allowed_tools = set(case.get("allowed_tools") or [])
    unexpected_tools = sorted(set(tool_names) - allowed_tools)
    forbidden_called = sorted(set(tool_names) & forbidden_tools)
    forbidden_claims = [claim for claim in list(case.get("forbidden_claims") or []) if claim and claim in answer]
    passed = result.get("status") == "success" and not forbidden_called and not unexpected_tools and not forbidden_claims
    return {
        "status": "passed" if passed else "failed",
        "http_status": 200,
        "tool_names": tool_names,
        "unexpected_tools": unexpected_tools,
        "forbidden_tools_called": forbidden_called,
        "forbidden_claims_found": forbidden_claims,
        "answer": answer,
        "runtime": dict(metrics.get("skills_runtime") or {}),
        "guard_result": metrics.get("guard_result"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--profile", default="customer_ceshi", choices=("customer_ceshi", "customer_support"))
    parser.add_argument("--cases", type=Path, default=Path("docs/shared_skills_v2/REGRESSION_CASES.yaml"))
    parser.add_argument("--attachment-base-url", default="")
    parser.add_argument("--timeout", type=float, default=120)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    cases = list((yaml.safe_load(args.cases.read_text(encoding="utf-8")) or {}).get("cases") or [])
    report_cases: list[dict] = []
    for case in cases:
        media, blocked_reason = _attachment_content(case, args.attachment_base_url)
        if blocked_reason:
            report_cases.append({"id": case.get("id"), "status": "blocked", "reason": blocked_reason})
            continue
        messages = [{"role": "system", "content": "请使用中文。不得执行任何写入操作。"}, {"role": "user", "content": case.get("input", "")}]
        if media:
            messages[-1]["content"] = [*media, {"type": "text", "text": case.get("input", "")}]
        payload = {
            "user_id": "skills-v2-regression",
            "session_id": f"skills-v2-{case.get('session_group') or case.get('id', 'case')}",
            "source_channel": "websdk",
            "agent_profile": args.profile,
            "messages": messages,
        }
        started = time.monotonic()
        http_status, result, error = _post(args.base_url.rstrip("/") + "/run", payload, args.timeout)
        if result is None:
            entry = {"id": case.get("id"), "status": "failed", "http_status": http_status, "error": error}
        else:
            entry = _evaluate(case, result)
            entry["id"] = case.get("id")
        entry["elapsed_ms"] = int((time.monotonic() - started) * 1000)
        report_cases.append(entry)
    summary = {
        "schema_version": 1,
        "profile": args.profile,
        "generated_at_epoch": int(time.time()),
        "cases": report_cases,
        "counts": {status: sum(1 for item in report_cases if item.get("status") == status) for status in ("passed", "failed", "blocked")},
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["counts"]["failed"] == 0 and summary["counts"]["blocked"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
