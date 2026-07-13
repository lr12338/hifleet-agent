#!/usr/bin/env python3
"""Run safe live probes for the customer-support retrieval tools.

This script intentionally reports configuration availability only; it never
prints credentials, raw request headers, cookies, or environment values.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from skills.knowledge_qa.tools import local_kb_search, web_search, web_search_agent_browser

QUERIES = [
    "HiFleet 筛选船队记忆功能",
    "HiFleet CCTV GB28181 接入价格异常",
    "怎么绘制区域标注",
]


def invoke(tool: Any, arguments: dict[str, Any]) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        raw = tool.invoke(arguments)
        payload = json.loads(raw) if isinstance(raw, str) else dict(raw)
        return {
            "status": str(payload.get("status") or "ok"),
            "can_answer": bool(payload.get("can_answer")),
            "item_count": len(payload.get("items") or payload.get("pages") or []),
            "best_urls": list(payload.get("best_urls") or [])[:3],
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
        }
    except Exception as exc:  # Diagnostic only; no stack trace or environment output.
        return {"status": "runtime_error", "error_type": type(exc).__name__, "latency_ms": round((time.perf_counter() - started) * 1000, 1)}


def main() -> None:
    load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)
    report: dict[str, Any] = {
        "kind": "live_retrieval_probe",
        "credential_presence": {
            "structured_web_search": any(bool(os.getenv(name)) for name in ("ark_websearch_api_key", "ARK_WEBSEARCH_API_KEY", "VOLC_WEB_SEARCH_API_KEY")),
        },
        "cases": [],
    }
    for query in QUERIES:
        local = invoke(local_kb_search, {"query": query, "top_k": 3})
        web = invoke(web_search, {"query": query, "count": 3})
        browser = invoke(web_search_agent_browser, {"query": query, "target_urls": "", "site_hint": "HiFleet"})
        report["cases"].append({"query": query, "local_kb_search": local, "web_search": web, "web_search_agent_browser": browser})
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
