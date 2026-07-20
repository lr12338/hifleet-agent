#!/usr/bin/env python3
"""Opt-in real-model probe for customer_ceshi's isolated Chat fallback."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agents.customer_ceshi_responses.builder import NativeToolRuntime
from agents.customer_ceshi_v2.tools import CapabilityRegistry
from llm_gateway import build_chat_model
from scripts.probe_customer_ceshi_responses import record_capability


class _DeliberatelyUnavailableResponses:
    class responses:
        @staticmethod
        def create(**_kwargs):
            raise RuntimeError("deliberate_responses_probe_failure")


def main() -> int:
    if os.getenv("CUSTOMER_CESHI_REAL_MODEL_TEST", "").lower() not in {"1", "true", "yes"}:
        print(json.dumps({"status": "SKIPPED", "reason": "set CUSTOMER_CESHI_REAL_MODEL_TEST=1 to enable live requests"}, ensure_ascii=False))
        return 0
    load_dotenv(ROOT / ".env")
    config = json.loads((ROOT / "config/agent_llm_config.json").read_text(encoding="utf-8"))["config"]
    chat = build_chat_model(None, config, role="text", streaming=False, model_override="deepseek-v4-flash-260425", allow_runtime_model_override=False)
    if chat is None:
        print(json.dumps({"status": "SKIPPED", "reason": "text_model_credentials_unavailable"}, ensure_ascii=False))
        return 0
    runtime = NativeToolRuntime(
        client=chat,
        registry=CapabilityRegistry(tools=[]),
        config=config,
        mode="responses",
        responses_client=_DeliberatelyUnavailableResponses(),
    )
    result = runtime.invoke(
        {"messages": [HumanMessage(content="您好")], "_customer_ceshi_session_key": "customer_ceshi:fallback:probe"},
        {},
    )
    metrics = dict(result.get("metrics") or {})
    safe = {
        "status": result.get("status"),
        "answer_length": len(str(result.get("generated_answer") or "")),
        "runtime_mode": metrics.get("runtime_mode"),
        "fallback_reason": metrics.get("fallback_reason"),
        "provider_error": metrics.get("provider_error"),
        "model_calls": metrics.get("model_calls"),
        "tool_calls": metrics.get("tool_calls"),
    }
    print(json.dumps(safe, ensure_ascii=False))
    passed = safe["status"] == "success" and safe["runtime_mode"] == "chat_function_calling" and str(safe["fallback_reason"]).startswith("responses_unavailable:")
    record_capability("chat_function_calling_fallback", passed=passed, kind="real_chat_fallback_probe")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
