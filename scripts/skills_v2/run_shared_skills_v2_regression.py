#!/usr/bin/env python3
"""Execute the public Shared Skills V2 cases through `/run` without masking gaps.

A case only reaches ``semantic_passed`` when a real image travelled through a real
``/run`` (or ``/stream_run``) into the model *and* its structured semantic
assertions are satisfied. HTTP 200, an ``inspect_media`` call, or the absence of a
fixed forbidden string never count as a semantic pass on their own.
"""
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
OPTIONAL_SEMANTIC_FIELDS = (
    "required_observations",
    "required_uncertainty",
    "forbidden_certainty",
    "required_layer_distinctions",
    "evidence_requirements",
    "max_local_kb_calls",
    "max_web_calls",
    "expected_draft_status",
    "answer_must_include_any",
    "human_label",
    "fixture_quality",
)
RUN_STATUSES = (
    "fixture_prepared",
    "invalid_fixture",
    "mock_only",
    "real_http_passed",
    "semantic_passed",
    "failed",
    "blocked",
)


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
        semantic_score = str(case.get("semantic_score") or "").strip()
        if not semantic_score or semantic_score.lower() in {"none", "n/a", "-"}:
            raise ValueError(f"case_semantic_score_empty:{case_id}")
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


def _tool_counts(tool_names: list[str]) -> dict[str, int]:
    local_kb = sum(1 for name in tool_names if name == "local_kb_search")
    web = sum(1 for name in tool_names if name == "web_search")
    media = sum(1 for name in tool_names if name == "inspect_media")
    return {"local_kb": local_kb, "web": web, "media": media, "total": len(tool_names)}


def _evaluate(case: dict, result: dict, *, has_real_media: bool = False) -> dict:
    """Score one step. Semantic pass requires a real image plus satisfied assertions."""
    metrics = dict(result.get("metrics") or {})
    tool_names = list(metrics.get("tool_names") or result.get("generated_tool_calls") or [])
    answer = str(result.get("generated_answer") or result.get("answer") or "")
    counts = _tool_counts(tool_names)

    forbidden_tools = set(case.get("forbidden_tools") or [])
    allowed_tools = set(case.get("allowed_tools") or [])
    unexpected_tools = sorted(set(tool_names) - allowed_tools)
    forbidden_called = sorted(set(tool_names) & forbidden_tools)

    forbidden_claims = [claim for claim in list(case.get("forbidden_claims") or []) if claim and claim in answer]
    forbidden_certainty = [claim for claim in list(case.get("forbidden_certainty") or []) if claim and claim in answer]

    required_observations = [str(item) for item in list(case.get("required_observations") or []) if str(item)]
    missing_observations = [item for item in required_observations if item not in answer]
    required_uncertainty = [str(item) for item in list(case.get("required_uncertainty") or []) if str(item)]
    has_uncertainty = (not required_uncertainty) or any(item in answer for item in required_uncertainty)
    required_layer_distinctions = [str(item) for item in list(case.get("required_layer_distinctions") or []) if str(item)]
    missing_layer_distinctions = [item for item in required_layer_distinctions if item not in answer]

    required_answer_any = [str(item) for item in list(case.get("answer_must_include_any") or []) if str(item)]
    missing_answer_requirement = bool(required_answer_any) and not any(item in answer for item in required_answer_any)

    expected_draft_status = str(case.get("expected_draft_status") or "")
    actual_draft_status = str(metrics.get("update_draft_status") or "")
    draft_status_mismatch = bool(expected_draft_status) and actual_draft_status != expected_draft_status

    max_tool_calls = case.get("max_tool_calls")
    too_many_tool_calls = isinstance(max_tool_calls, int) and counts["total"] > max_tool_calls
    max_local_kb = case.get("max_local_kb_calls")
    too_many_local_kb = isinstance(max_local_kb, int) and counts["local_kb"] > max_local_kb
    max_web = case.get("max_web_calls")
    too_many_web = isinstance(max_web, int) and counts["web"] > max_web

    semantic_assertions_present = bool(
        required_observations or required_uncertainty or required_layer_distinctions or forbidden_certainty
    )
    semantic_assertions_ok = (
        not missing_observations
        and has_uncertainty
        and not missing_layer_distinctions
        and not forbidden_certainty
    )
    policy_ok = (
        not forbidden_called
        and not unexpected_tools
        and not forbidden_claims
        and not missing_answer_requirement
        and not draft_status_mismatch
        and not too_many_tool_calls
        and not too_many_local_kb
        and not too_many_web
    )
    http_ok = result.get("status") == "success"

    semantic_ok = (not semantic_assertions_present) or semantic_assertions_ok
    if not http_ok or not policy_ok or not semantic_ok:
        status = "failed"
    elif has_real_media and semantic_assertions_present and semantic_assertions_ok:
        status = "semantic_passed"
    else:
        status = "real_http_passed"

    return {
        "status": status,
        "http_status": 200,
        "tool_names": tool_names,
        "tool_counts": counts,
        "unexpected_tools": unexpected_tools,
        "forbidden_tools_called": forbidden_called,
        "forbidden_claims_found": forbidden_claims,
        "forbidden_certainty_found": forbidden_certainty,
        "missing_observations": missing_observations,
        "missing_layer_distinctions": missing_layer_distinctions,
        "has_uncertainty": has_uncertainty,
        "missing_answer_requirement": required_answer_any if missing_answer_requirement else [],
        "expected_draft_status": expected_draft_status,
        "actual_draft_status": actual_draft_status,
        "max_tool_calls": max_tool_calls,
        "too_many_tool_calls": too_many_tool_calls,
        "too_many_local_kb": too_many_local_kb,
        "too_many_web": too_many_web,
        "semantic_assertions_present": semantic_assertions_present,
        "semantic_assertions_ok": semantic_assertions_ok,
        "answer": answer,
        "runtime": dict(metrics.get("skills_runtime") or {}),
        "guard_result": metrics.get("guard_result"),
    }


def _pre_run_status(case: dict, base_url: str) -> tuple[str, str]:
    """Return (status, reason) for cases that must not hit the live endpoint."""
    fixture_quality = str(case.get("fixture_quality") or "").strip().lower()
    execution = str(case.get("execution") or "").strip().lower()
    if fixture_quality == "invalid":
        return "invalid_fixture", "fixture_quality_invalid"
    if execution == "mock_only":
        return "mock_only", "execution_mock_only"
    if execution == "contract_only":
        return "blocked", "contract_fixture_required"
    attachment = case.get("attachment")
    if attachment:
        media, reason = _attachment_content(case, base_url)
        if not media:
            if fixture_quality == "valid":
                return "fixture_prepared", reason
            return "blocked", reason
    return "", ""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--profile", default="customer_ceshi", choices=("customer_ceshi", "customer_support"))
    parser.add_argument("--cases", type=Path, default=Path("docs/skills_v2/REGRESSION_CASES.yaml"))
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
        status, reason = _pre_run_status(case, args.attachment_base_url)
        if status:
            report_cases.append({"id": case.get("id"), "status": status, "reason": reason})
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
                evaluated = _evaluate(step_case, result, has_real_media=bool(step_media))
            step_results.append(evaluated)
        step_statuses = [item.get("status") for item in step_results]
        if any(status == "failed" for status in step_statuses):
            case_status = "failed"
        elif any(status == "semantic_passed" for status in step_statuses):
            case_status = "semantic_passed"
        else:
            case_status = "real_http_passed"
        entry = dict(step_results[-1])
        entry["status"] = case_status
        entry["id"] = case.get("id")
        entry["steps"] = step_results
        entry["elapsed_ms"] = int((time.monotonic() - started) * 1000)
        report_cases.append(entry)
    summary = {
        "schema_version": 1,
        "profile": args.profile,
        "generated_at_epoch": int(time.time()),
        "cases": report_cases,
        "counts": {status: sum(1 for item in report_cases if item.get("status") == status) for status in RUN_STATUSES},
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["counts"]["failed"] == 0 and summary["counts"]["blocked"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
