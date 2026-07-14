#!/usr/bin/env python3
"""Evaluate HiFleet multimodal customer-support cases without fabricating runs.

Default mode validates the fixture/understanding contract only. ``--direct-graph``
executes the local customer-support graph. ``--run-api`` is intentionally opt-in
and requires environment-provided endpoint credentials; neither mode serializes
raw attachment URLs or credential values into output artifacts.
"""
from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import os
import sys
import time
from pathlib import Path
from typing import Any

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage

from agents.customer_support_understanding import build_customer_understanding
from agents.multimodal_contracts import evidence_coverage


def _load_cases(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return [dict(item) for item in list(payload.get("cases") or [])]


def _safe_media_part(path: Path) -> dict[str, Any]:
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    if mime.startswith("image/"):
        return {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{encoded}"}}
    return {"type": "file_url", "file_url": {"url": f"data:{mime};base64,{encoded}"}}


def _mask_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _mask_value(item) for key, item in value.items() if key not in {"url", "file_url", "image_url"}}
    if isinstance(value, list):
        return [_mask_value(item) for item in value]
    return value


def _contract_result(case: dict[str, Any]) -> dict[str, Any]:
    files = [ROOT / str(value) for value in list(case.get("input_files") or [])]
    unavailable = str(case.get("status") or "") == "missing_fixture" or not files or any(not path.is_file() for path in files)
    result: dict[str, Any] = {
        "case_id": str(case.get("case_id") or ""),
        "input_files": [str(path.relative_to(ROOT)) for path in files],
        "user_text": str(case.get("user_text") or ""),
        "current_commit": os.popen("git rev-parse --short HEAD").read().strip(),
        "expected_scenario": str(case.get("expected_scenario") or ""),
        "allowed_tools": list(case.get("allowed_tools") or []),
        "forbidden_tools": list(case.get("forbidden_tools") or []),
        "required_facts": list(case.get("required_facts") or []),
        "forbidden_content": list(case.get("forbidden_content") or []),
        "execution_mode": "contract_only",
        "failure_code": "missing_fixture" if unavailable else "not_executed",
    }
    if unavailable:
        result.update({
            "scenario": result["expected_scenario"],
            "perception_result": {},
            "understanding_result": {},
            "extracted_entities": [],
            "current_media_preserved": False,
            "context_message_count": 0,
            "dropped_context_count": 0,
            "task_plan": {},
            "required_claims": [],
            "tool_call_sequence": [],
            "tool_inputs_summary": [],
            "evidence_items": [],
            "evidence_coverage": evidence_coverage([], []),
            "final_confidence": "low",
            "final_answer": "",
            "latency_by_stage": {},
        })
        return result

    attachment_type = "file"
    mime = mimetypes.guess_type(files[-1].name)[0] or ""
    if mime.startswith("image/"):
        attachment_type = "image"
    elif mime.startswith("audio/"):
        attachment_type = "audio"
    elif mime.startswith("video/"):
        attachment_type = "video"
    perception = {"attachment_type": attachment_type, "confidence": "low"}
    understanding = build_customer_understanding(
        result["user_text"],
        has_media=True,
        has_file_attachment=attachment_type == "file",
        perception=perception,
    ).model_dump()
    result.update({
        "perception_result": perception,
        "understanding_result": understanding,
        "scenario": understanding.get("multimodal_scenario"),
        "extracted_entities": list(understanding.get("ship_identities") or []),
        "current_media_preserved": True,
        "context_message_count": 1,
        "dropped_context_count": 0,
        "task_plan": {"scenario": understanding.get("multimodal_scenario"), "required_claims": understanding.get("required_claims") or []},
        "required_claims": list(understanding.get("required_claims") or []),
        "tool_call_sequence": [],
        "tool_inputs_summary": [],
        "evidence_items": [],
        "evidence_coverage": evidence_coverage([], understanding.get("required_claims") or []),
        "final_confidence": str(understanding.get("confidence") or "low"),
        "final_answer": "",
        "latency_by_stage": {},
    })
    return result


def _run_direct_graph(result: dict[str, Any]) -> dict[str, Any]:
    if result.get("failure_code") == "missing_fixture":
        return result
    started = time.monotonic()
    try:
        # The evaluator runs outside FastAPI's async lifecycle. It must not try
        # to initialize the production PostgreSQL saver in a plain CLI process.
        os.environ["COZE_CHECKPOINTER_MODE"] = "memory"
        from agents.agent import build_agent

        parts = [{"type": "text", "text": str(result["user_text"])}]
        for relative_path in result["input_files"]:
            parts.append(_safe_media_part(ROOT / relative_path))
        graph = build_agent()
        thread_id = f"multimodal-eval:{result['case_id']}"
        output = graph.invoke(
            {"messages": [HumanMessage(content=parts)], "session_id": thread_id},
            config={"configurable": {"thread_id": thread_id}},
        )
    except Exception as exc:
        result.update({
            "execution_mode": "direct_graph",
            "failure_code": "direct_graph_failed",
            "failure_detail": str(exc)[:500],
            "elapsed_ms": round((time.monotonic() - started) * 1000),
        })
        return result
    trace = dict(output.get("route_trace") or {})
    reasoning = dict(trace.get("reasoning_trace") or {})
    evidence_items = list(trace.get("evidence_items") or [])
    understanding = dict(reasoning.get("understanding_result") or result.get("understanding_result") or {})
    result.update({
        "execution_mode": "direct_graph",
        "failure_code": str(trace.get("fallback_reason") or ""),
        "perception_result": _mask_value(output.get("perception_result") or result.get("perception_result") or {}),
        "understanding_result": _mask_value(understanding),
        "scenario": str(understanding.get("multimodal_scenario") or result.get("scenario") or ""),
        "extracted_entities": list(understanding.get("ship_identities") or []),
        "current_media_preserved": bool((reasoning.get("perception_summary") or {}).get("current_media_preserved")),
        "context_message_count": int((reasoning.get("perception_summary") or {}).get("input_message_count") or 1),
        "dropped_context_count": int((reasoning.get("perception_summary") or {}).get("dropped_irrelevant_context_count") or 0),
        "task_plan": _mask_value({"route": trace.get("route"), "task_type": trace.get("task_type"), "tool_bundle": trace.get("tool_bundle")}),
        "required_claims": list(understanding.get("required_claims") or []),
        "tool_call_sequence": list(trace.get("tool_call_sequence") or output.get("generated_tool_calls") or []),
        "tool_inputs_summary": _mask_value(list(trace.get("tool_inputs") or [])),
        "evidence_items": _mask_value(evidence_items),
        "evidence_coverage": evidence_coverage(evidence_items, understanding.get("required_claims") or []),
        "final_confidence": str(trace.get("answer_confidence") or "low"),
        "final_answer": str(output.get("generated_answer") or ""),
        "latency_by_stage": _mask_value(trace.get("latency_hotspot") or {}),
        "elapsed_ms": round((time.monotonic() - started) * 1000),
    })
    return result


def _run_api(result: dict[str, Any], *, timeout_seconds: int) -> dict[str, Any]:
    """Call a configured local or remote ``/run`` endpoint with no saved secrets."""
    if result.get("failure_code") == "missing_fixture":
        return result
    endpoint = os.getenv("MULTIMODAL_EVAL_API_URL", "").strip().rstrip("/")
    if not endpoint:
        result.update({"execution_mode": "run_api", "failure_code": "api_not_configured"})
        return result
    if not endpoint.endswith("/run"):
        endpoint = f"{endpoint}/run"
    parts = [{"type": "text", "text": str(result["user_text"])}]
    for relative_path in result["input_files"]:
        parts.append(_safe_media_part(ROOT / relative_path))
    headers = {"Content-Type": "application/json"}
    token = os.getenv("MULTIMODAL_EVAL_API_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    payload = {
        "messages": [{"role": "user", "content": parts}],
        "session_id": f"multimodal-eval:{result['case_id']}",
        "user_id": "multimodal-evaluator",
        "source_channel": "websdk",
        "agent_profile": "customer_support",
    }
    started = time.monotonic()
    try:
        response = requests.post(endpoint, json=payload, headers=headers, timeout=timeout_seconds)
        body = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
    except requests.RequestException as exc:
        result.update({
            "execution_mode": "run_api",
            "failure_code": "api_request_failed",
            "failure_detail": str(exc)[:300],
            "elapsed_ms": round((time.monotonic() - started) * 1000),
        })
        return result
    route_trace = dict(body.get("route_trace") or {})
    reasoning = dict(route_trace.get("reasoning_trace") or {})
    understanding = dict(reasoning.get("understanding_result") or result.get("understanding_result") or {})
    evidence_items = list(route_trace.get("evidence_items") or [])
    messages = list(body.get("messages") or [])
    final_answer = str(body.get("generated_answer") or body.get("answer") or "")
    if not final_answer:
        final_answer = str(next((item.get("content") for item in reversed(messages) if isinstance(item, dict) and str(item.get("type") or item.get("role") or "").lower() in {"ai", "assistant"}), ""))
    result.update({
        "execution_mode": "run_api",
        "failure_code": "" if response.ok else f"api_http_{response.status_code}",
        "api_status_code": response.status_code,
        "perception_result": _mask_value(body.get("perception_result") or result.get("perception_result") or {}),
        "understanding_result": _mask_value(understanding),
        "scenario": str(understanding.get("multimodal_scenario") or result.get("scenario") or ""),
        "extracted_entities": list(understanding.get("ship_identities") or []),
        "current_media_preserved": bool((reasoning.get("perception_summary") or {}).get("current_media_preserved")),
        "context_message_count": int((reasoning.get("perception_summary") or {}).get("input_message_count") or 1),
        "dropped_context_count": int((reasoning.get("perception_summary") or {}).get("dropped_irrelevant_context_count") or 0),
        "task_plan": _mask_value({"route": route_trace.get("route"), "task_type": route_trace.get("task_type"), "tool_bundle": route_trace.get("tool_bundle")}),
        "required_claims": list(understanding.get("required_claims") or []),
        "tool_call_sequence": list(route_trace.get("tool_call_sequence") or body.get("generated_tool_calls") or []),
        "tool_inputs_summary": _mask_value(list(route_trace.get("tool_inputs") or [])),
        "evidence_items": _mask_value(evidence_items),
        "evidence_coverage": evidence_coverage(evidence_items, understanding.get("required_claims") or []),
        "final_confidence": str(route_trace.get("answer_confidence") or "low"),
        "final_answer": final_answer,
        "latency_by_stage": _mask_value(route_trace.get("latency_hotspot") or {}),
        "elapsed_ms": round((time.monotonic() - started) * 1000),
    })
    return result


def _render_markdown(results: list[dict[str, Any]]) -> str:
    rows = ["# HiFleet 多模态客服运行评测", "", "| Case | Mode | Scenario | Tools | Confidence | Failure |", "|---|---|---|---|---|---|"]
    for item in results:
        rows.append("| {case_id} | {execution_mode} | {scenario} | {tools} | {confidence} | {failure} |".format(
            case_id=item.get("case_id", ""), execution_mode=item.get("execution_mode", ""), scenario=item.get("scenario", ""),
            tools=", ".join(item.get("tool_call_sequence") or []), confidence=item.get("final_confidence", ""), failure=item.get("failure_code", ""),
        ))
    return "\n".join(rows) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", default="all", help="M01…M15 or all")
    parser.add_argument("--direct-graph", action="store_true")
    parser.add_argument("--run-api", action="store_true", help="Call MULTIMODAL_EVAL_API_URL[/run]; optional bearer token comes from MULTIMODAL_EVAL_API_TOKEN.")
    parser.add_argument("--output-json", type=Path, default=ROOT / "artifacts" / "multimodal_customer_support_eval_run.json")
    parser.add_argument("--output-md", type=Path, default=ROOT / "artifacts" / "multimodal_customer_support_eval_run.md")
    parser.add_argument("--timeout", type=int, default=180)
    args = parser.parse_args()
    load_dotenv(ROOT / ".env")
    if args.run_api and args.direct_graph:
        parser.error("choose either --direct-graph or --run-api")
    selected = [item for item in _load_cases(ROOT / "tests/fixtures/multimodal_customer_support/cases.json") if args.case == "all" or item.get("case_id") == args.case]
    if not selected:
        parser.error(f"unknown case: {args.case}")
    results = [_contract_result(item) for item in selected]
    if args.direct_graph:
        results = [_run_direct_graph(item) for item in results]
    elif args.run_api:
        results = [_run_api(item, timeout_seconds=args.timeout) for item in results]
    mode = "direct_graph" if args.direct_graph else "run_api" if args.run_api else "contract_only"
    payload = {"cases": results, "mode": mode, "timeout_seconds": args.timeout}
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.output_md.parent.mkdir(parents=True, exist_ok=True)
    args.output_md.write_text(_render_markdown(results), encoding="utf-8")
    print(json.dumps({"output_json": str(args.output_json), "output_md": str(args.output_md), "case_count": len(results)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
