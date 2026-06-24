from __future__ import annotations

from typing import Any


def build_browser_bridge_payload(
    query: str,
    target_urls: str,
    site_hint: str,
    parsed: dict[str, Any],
    raw_output: str = "",
    parse_error: str = "",
) -> dict[str, Any]:
    pages = list(parsed.get("pages") or []) if isinstance(parsed, dict) else []
    can_answer = bool(pages)
    raw_summary = ""
    raw_status = "ok" if parsed else "parse_empty"
    if isinstance(parsed, dict) and parsed:
        raw_summary = str(parsed.get("summary") or parsed.get("reason") or "")
        raw_status = str(parsed.get("status") or parsed.get("type") or "parsed")
    elif raw_output:
        raw_summary = raw_output.strip()[:300]
        raw_status = "raw_text"
    if parse_error:
        raw_status = "parse_error"
    return {
        "tool": "web_search_agent_browser",
        "query": query,
        "status": "ok" if pages else "no_hit",
        "can_answer": can_answer,
        "should_continue": False,
        "continue_with": "none",
        "confidence": "medium" if pages else "low",
        "summary": "已抓取到候选页面正文证据" if pages else "未抓取到可用的官方页面正文",
        "items": [],
        "pages": pages,
        "best_urls": [str(page.get("url", "")).strip() for page in pages[:3] if str(page.get("url", "")).strip()],
        "recommended_next_action": "基于页面正文保守回答用户" if pages else "返回保守回复并建议人工核查",
        "trace": {
            "site_hint": site_hint,
            "target_urls": target_urls,
            "target_urls_present": bool(str(target_urls or "").strip()),
            "reason": "browser_deep_search_result",
            "raw_status": raw_status,
            "raw_summary": raw_summary,
            "parse_error": parse_error,
        },
    }
