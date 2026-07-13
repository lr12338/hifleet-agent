from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from skills.knowledge_qa.web_search_runtime import is_directory_page, is_specific_page


def _is_official_specific_relevant_page(page: dict[str, Any], query: str) -> bool:
    if "can_support_answer" in page:
        return bool(page.get("can_support_answer"))
    url = str(page.get("url") or "").strip()
    title = str(page.get("title") or "")
    excerpt = str(page.get("excerpt") or page.get("body") or "")
    host = (urlparse(url).hostname or "").lower()
    if not (host == "hifleet.com" or host.endswith(".hifleet.com")):
        return False
    if not is_specific_page(url) or is_directory_page(url, title, excerpt):
        return False
    query_tokens = [token.lower() for token in str(query or "").split() if len(token) >= 2 and token.lower() not in {"hifleet", "怎么", "如何"}]
    haystack = f"{title} {excerpt} {url}".lower()
    if query_tokens and any(token in haystack for token in query_tokens):
        return True
    compact_query = re.sub(r"\s+", "", str(query or "").lower())
    compact_haystack = re.sub(r"\s+", "", haystack)
    chinese_bigrams = {compact_query[index:index + 2] for index in range(len(compact_query) - 1) if re.fullmatch(r"[\u4e00-\u9fff]{2}", compact_query[index:index + 2])}
    return len([token for token in chinese_bigrams if token in compact_haystack]) >= 2


def build_browser_bridge_payload(
    query: str,
    target_urls: str,
    site_hint: str,
    parsed: dict[str, Any],
    raw_output: str = "",
    parse_error: str = "",
) -> dict[str, Any]:
    raw_pages = list(parsed.get("pages") or []) if isinstance(parsed, dict) else []
    pages = [page for page in raw_pages if isinstance(page, dict) and _is_official_specific_relevant_page(page, query)]
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
    browser_status = str(parsed.get("status") or "") if isinstance(parsed, dict) else ""
    status = "ok" if pages else (browser_status or ("generic_or_irrelevant_page" if raw_pages else "no_hit"))
    summary = "已抓取到候选页面正文证据" if pages else {
        "browser_cli_missing": "页面核验环境当前不可用，未将其视为检索成功",
        "browser_doctor_failed": "页面核验环境当前不可用，未将其视为检索成功",
        "browser_open_timeout": "候选页面抓取超时，未获得可引用正文",
        "browser_empty_body": "候选页面未返回可用正文",
        "browser_irrelevant_page": "抓取到的页面不是可引用的具体相关页面",
        "browser_no_candidates": "未找到可安全抓取的候选页面",
        "invalid_query": "页面核验关键词无效",
    }.get(status, "未抓取到可用的官方页面正文")
    return {
        "tool": "web_search_agent_browser",
        "query": query,
        "status": status,
        "can_answer": can_answer,
        "should_continue": False,
        "continue_with": "none",
        "confidence": "medium" if pages else "low",
        "summary": summary,
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
            "failure_code": browser_status if browser_status != "ok" else "",
            "raw_page_count": len(raw_pages),
            "relevant_page_count": len(pages),
        },
    }
