"""Opt-in live capability probe for the isolated customer_ceshi runtime.

This utility never prints credentials, response text, reasoning content, or raw provider IDs.
Run it only against a non-production test session with explicitly configured credentials.
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any

from openai import OpenAI


def _response_id(response: Any) -> str:
    return str(getattr(response, "id", "") or (response.get("id", "") if isinstance(response, dict) else ""))


def _calls(response: Any) -> list[dict[str, Any]]:
    output = getattr(response, "output", None) or (response.get("output", []) if isinstance(response, dict) else [])
    calls: list[dict[str, Any]] = []
    for item in output or []:
        item_type = getattr(item, "type", "") or (item.get("type", "") if isinstance(item, dict) else "")
        if item_type != "function_call":
            continue
        get = item.get if isinstance(item, dict) else lambda key, default=None: getattr(item, key, default)
        calls.append({"name": str(get("name", "")), "call_id": str(get("call_id", "") or get("id", ""))})
    return calls


def _probe_multimodal_tools(*, api_key: str, base_url: str, tool: dict[str, Any]) -> dict[str, Any]:
    media_url = os.getenv("CUSTOMER_CESHI_MULTIMODAL_PROBE_URL", "").strip()
    if not media_url:
        return {"status": "SKIPPED", "reason": "set CUSTOMER_CESHI_MULTIMODAL_PROBE_URL to enable a public-image Doubao probe"}
    model = os.getenv("CUSTOMER_CESHI_MULTIMODAL_MODEL", "doubao-seed-2-1-pro-260628").strip()
    result = {"status": "FAILED", "responses": False, "function_call": False, "previous_response_id": False, "error": ""}
    try:
        client = OpenAI(api_key=api_key, base_url=os.getenv("COZE_INTEGRATION_MULTIMODAL_BASE_URL", base_url).strip() or base_url)
        first = client.responses.create(
            model=model,
            input=[{"role": "user", "content": [
                {"type": "input_image", "image_url": media_url, "detail": "low"},
                {"type": "input_text", "text": "Call capability_echo exactly once with step 1. Do not answer in prose."},
            ]}],
            tools=[tool],
        )
        result["responses"] = bool(_response_id(first))
        calls = _calls(first)
        result["function_call"] = len(calls) == 1 and calls[0]["name"] == "capability_echo"
        if calls:
            second = client.responses.create(
                model=model,
                previous_response_id=_response_id(first),
                input=[{"type": "function_call_output", "call_id": calls[0]["call_id"], "output": json.dumps({"step": 1})}],
                tools=[tool],
            )
            result["previous_response_id"] = bool(_response_id(second))
        result["status"] = "PASSED" if all(result[key] for key in ("responses", "function_call", "previous_response_id")) else "FAILED"
    except Exception as exc:
        body = getattr(exc, "body", {})
        body = body if isinstance(body, dict) else {}
        error = body.get("error") if isinstance(body.get("error"), dict) else body
        details = [type(exc).__name__]
        if getattr(exc, "status_code", None) is not None:
            details.append(f"status={exc.status_code}")
        if isinstance(error, dict) and error.get("code"):
            details.append(f"code={str(error['code'])[:80]}")
        if isinstance(error, dict) and error.get("param"):
            details.append(f"param={str(error['param'])[:80]}")
        result["error"] = ";".join(details)
    return result


def main() -> int:
    if os.getenv("CUSTOMER_CESHI_REAL_MODEL_TEST", "").lower() not in {"1", "true", "yes"}:
        print(json.dumps({"status": "SKIPPED", "reason": "set CUSTOMER_CESHI_REAL_MODEL_TEST=1 to enable live requests"}, ensure_ascii=False))
        return 0
    api_key = os.getenv("COZE_WORKLOAD_IDENTITY_API_KEY", "").strip()
    base_url = os.getenv("COZE_INTEGRATION_MODEL_BASE_URL", "").strip()
    model = os.getenv("CUSTOMER_CESHI_RESPONSES_MODEL", "deepseek-v4-flash-260425").strip()
    if not api_key or not base_url:
        print(json.dumps({"status": "SKIPPED", "reason": "COZE_WORKLOAD_IDENTITY_API_KEY and COZE_INTEGRATION_MODEL_BASE_URL are required"}, ensure_ascii=False))
        return 0

    result = {"status": "FAILED", "responses": False, "function_call": False, "previous_response_id": False, "error": ""}
    tool = {
        "type": "function",
        "name": "capability_echo",
        "description": "Return the supplied step unchanged. This is a no-side-effect capability probe.",
        "parameters": {"type": "object", "properties": {"step": {"type": "integer"}}, "required": ["step"]},
    }
    try:
        client = OpenAI(api_key=api_key, base_url=base_url)
        first = client.responses.create(
            model=model,
            input="Call capability_echo exactly once with step 1. Do not answer in prose.",
            tools=[tool],
        )
        result["responses"] = bool(_response_id(first))
        calls = _calls(first)
        result["function_call"] = len(calls) == 1 and calls[0]["name"] == "capability_echo"
        if calls:
            second = client.responses.create(
                model=model,
                previous_response_id=_response_id(first),
                input=[{"type": "function_call_output", "call_id": calls[0]["call_id"], "output": json.dumps({"step": 1})}],
                tools=[tool],
            )
            result["previous_response_id"] = bool(_response_id(second))
        result["status"] = "PASSED" if all(result[key] for key in ("responses", "function_call", "previous_response_id")) else "FAILED"
    except Exception as exc:
        body = getattr(exc, "body", {})
        body = body if isinstance(body, dict) else {}
        error = body.get("error") if isinstance(body.get("error"), dict) else body
        details = [type(exc).__name__]
        if getattr(exc, "status_code", None) is not None:
            details.append(f"status={exc.status_code}")
        if isinstance(error, dict) and error.get("code"):
            details.append(f"code={str(error['code'])[:80]}")
        if isinstance(error, dict) and error.get("param"):
            details.append(f"param={str(error['param'])[:80]}")
        result["error"] = ";".join(details)
    result["multimodal"] = _probe_multimodal_tools(api_key=api_key, base_url=base_url, tool=tool) if result["status"] == "PASSED" else {"status": "SKIPPED", "reason": "text Responses capability probe failed"}
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] == "PASSED" and result["multimodal"]["status"] in {"PASSED", "SKIPPED"} else 1


if __name__ == "__main__":
    sys.exit(main())
