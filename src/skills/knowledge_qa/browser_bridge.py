from __future__ import annotations

from typing import Any


def build_browser_bridge_payload(query: str, target_urls: str, site_hint: str, parsed: dict[str, Any]) -> dict[str, Any]:
    pages = list(parsed.get("pages") or []) if isinstance(parsed, dict) else []
    can_answer = bool(pages)
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
            "reason": "browser_deep_search_result",
        },
    }
