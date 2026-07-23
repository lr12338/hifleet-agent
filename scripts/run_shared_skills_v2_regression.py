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


REQUIRED_CASE_FIELDS = {
    "id",
    "input",
    "scenario",
    "allowed_tools",
    "forbidden_tools",
    "evidence",
    "forbidden_claims",
    "follow_up_allowed",
    "semantic_score",
}


def validate_cases(cases: list[dict]) -> None:
    seen_ids: set[str] = set()
    for case in cases:
        if not isinstance(case, dict):
            raise ValueError("case_must_be_mapping")
        case_id = str(case.get("id") or "")
        if not case_id or case_id in seen_ids:
            raise ValueError(f"case_id_missing_or_duplicate:{case_id or '<missing>'}")
        seen_ids.add(case_id)
        missing = sorted(REQUIRED_CASE_FIELDS - set(case))
        if missing:
            raise ValueError(f"case_missing_required_fields:{case_id}:{','.join(missing)}")
        if not isinstance(case["allowed_tools"], list) or not isinstance(case["forbidden_tools"], list):
            raise ValueError(f"case_tool_lists_invalid:{case_id}")
        if set(case["allowed_tools"]) & set(case["forbidden_tools"]):
            raise ValueError(f"case_tool_policy_conflict:{case_id}")
        steps = case.get("steps")
        if steps is not None and (not isinstance(steps, list) or not steps):
            raise ValueError(f"case_steps_invalid:{case_id}")


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
    required_answer_any = [str(item) for item in list(case.get("answer_must_include_any") or []) if str(item)]
    missing_answer_requirement = bool(required_answer_any) and not any(item in answer for item in required_answer_any)
    expected_draft_status = str(case.get("expected_draft_status") or "")
    actual_draft_status = str(metrics.get("update_draft_status") or "")
    draft_status_mismatch = bool(expected_draft_status) and actual_draft_status != expected_draft_status
    max_tool_calls = case.get("max_tool_calls")
    too_many_tool_calls = isinstance(max_tool_calls, int) and len(tool_names) > max_tool_calls
    passed = (
        result.get("status") == "success"
        and not forbidden_called
        and not unexpected_tools
        and not forbidden_claims
        and not missing_answer_requirement
        and not draft_status_mismatch
        and not too_many_tool_calls
    )
    return {
        "status": "passed" if passed else "failed",
        "http_status": 200,
        "tool_names": tool_names,
        "unexpected_tools": unexpected_tools,
        "forbidden_tools_called": forbidden_called,
        "forbidden_claims_found": forbidden_claims,
        "missing_answer_requirement": required_answer_any if missing_answer_requirement else [],
        "expected_draft_status": expected_draft_status,
        "actual_draft_status": actual_draft_status,
        "max_tool_calls": max_tool_calls,
        "too_many_tool_calls": too_many_tool_calls,
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
    parser.add_argument("--case", action="append", dest="case_ids", default=[], help="Run only one or more case IDs.")
    args = parser.parse_args()
    cases = list((yaml.safe_load(args.cases.read_text(encoding="utf-8")) or {}).get("cases") or [])
    validate_cases(cases)
    selected_case_ids = set(args.case_ids)
    if selected_case_ids:
        cases = [case for case in cases if case.get("id") in selected_case_ids]
        missing_case_ids = selected_case_ids - {str(case.get("id")) for case in cases}
        if missing_case_ids:
            raise ValueError(f"unknown_case_ids:{','.join(sorted(missing_case_ids))}")
    report_cases: list[dict] = []
    for case in cases:
        if case.get("execution") == "contract_only":
            report_cases.append({"id": case.get("id"), "status": "blocked", "reason": "contract_fixture_required"})
            continue
        media, blocked_reason = _attachment_content(case, args.attachment_base_url)
        if blocked_reason:
            report_cases.append({"id": case.get("id"), "status": "blocked", "reason": blocked_reason})
            continue
        steps = list(case.get("steps") or [{"input": case.get("input", "")}])
        step_results: list[dict] = []
        started = time.monotonic()
        for index, step in enumerate(steps):
            text = str(step.get("input") or case.get("input") or "")
            step_media = media if index == 0 else []
            messages = [{"role": "system", "content": "请使用中文。不得执行任何写入操作。"}, {"role": "user", "content": text}]
            if step_media:
                messages[-1]["content"] = [*step_media, {"type": "text", "text": text}]
            session_key = str(step.get("session_group") or case.get("session_group") or case.get("id", "case"))
            payload = {
                "user_id": "skills-v2-regression",
                "session_id": f"skills-v2-{session_key}",
                "source_channel": "websdk",
                "agent_profile": args.profile,
                "messages": messages,
            }
            http_status, result, error = _post(args.base_url.rstrip("/") + "/run", payload, args.timeout)
            if result is None:
                evaluated = {"status": "failed", "http_status": http_status, "error": error}
            else:
                step_case = {**case, **dict(step.get("expect") or {})}
                evaluated = _evaluate(step_case, result)
            step_results.append(evaluated)
        entry = dict(step_results[-1])
        entry["status"] = "passed" if all(item.get("status") == "passed" for item in step_results) else "failed"
        entry["id"] = case.get("id")
        entry["steps"] = step_results
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
