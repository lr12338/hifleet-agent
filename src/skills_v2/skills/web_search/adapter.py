"""V2 web_search adapter: exposes only ``web_search``.

Single-shot public web search. Never opens, clicks or operates pages; never
includes verify_public_page / agent_browser_deep_search / web_search_agent_browser.
Results keep URL, title, snippet and source type; weak/conflicting/non-official
sources are low-strength evidence only.
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any
from urllib.parse import urlparse

import requests
from langchain.tools import tool
from openai import OpenAI

from .web_search_runtime import (
    HIFLEET_SITES,
    VOLC_WEB_SEARCH_DEFAULT_COUNT,
    analyze_web_search_result,
    looks_like_authoritative_data_query,
    looks_like_hifleet_product_query,
    rewrite_web_search_query,
)

logger = logging.getLogger(__name__)
VOLC_WEB_SEARCH_URL = "https://open.feedcoopapi.com/search_api/web_search"
VOLC_WEB_SEARCH_TIMEOUT_SEC = float(os.getenv("VOLC_WEB_SEARCH_TIMEOUT_SEC", "15"))


def _get_env_value(*keys: str) -> str:
    for key in keys:
        v = os.getenv(key)
        if v and v.strip():
            return v.strip()
    return ""


def _build_volc_web_search_payload(
    query: str,
    *,
    search_type: str,
    count: int,
    sites: str = "",
    need_summary: bool = True,
    need_content: bool = False,
    need_url: bool = True,
    query_rewrite: bool = False,
    auth_info_level: int = 0,
    block_hosts: str = "",
    time_range: str = "",
    content_format: str = "text",
) -> dict:
    payload = {
        "Query": query[:100],
        "SearchType": search_type,
        "Count": max(1, min(int(count or VOLC_WEB_SEARCH_DEFAULT_COUNT), 50)),
        "NeedSummary": bool(need_summary),
        "QueryControl": {"QueryRewrite": bool(query_rewrite)},
    }
    if search_type == "web_summary":
        payload["NeedSummary"] = True
    filter_payload = {"NeedContent": bool(need_content), "NeedUrl": bool(need_url)}
    if sites:
        filter_payload["Sites"] = sites
    if block_hosts.strip():
        filter_payload["BlockHosts"] = block_hosts.strip()
    if auth_info_level in (0, 1):
        filter_payload["AuthInfoLevel"] = auth_info_level
    payload["Filter"] = filter_payload
    if time_range:
        payload["TimeRange"] = time_range
    if content_format in ("text", "markdown"):
        payload["ContentFormats"] = content_format
    return payload


def _extract_summary_from_choices(choices: list) -> str:
    parts = []
    for choice in choices or []:
        message = choice.get("Message") or {}
        delta = choice.get("Delta") or {}
        content = message.get("Content") or delta.get("Content") or ""
        if content:
            parts.append(content)
    return "".join(parts).strip()


def _normalize_web_search_result(payload: dict) -> dict:
    result = (payload or {}).get("Result") or payload or {}
    items = []
    for item in result.get("WebResults") or []:
        items.append(
            {
                "title": item.get("Title", ""),
                "site_name": item.get("SiteName", ""),
                "url": item.get("Url", ""),
                "snippet": item.get("Snippet", ""),
                "summary": item.get("Summary", ""),
                "content": item.get("Content", ""),
                "publish_time": item.get("PublishTime", ""),
                "authority_level": item.get("AuthInfoLevel"),
                "authority_desc": item.get("AuthInfoDes", ""),
                "rank_score": item.get("RankScore"),
                "content_format": item.get("ContentFormats", ""),
            }
        )
    summary = _extract_summary_from_choices(result.get("Choices") or [])
    return {
        "summary": summary,
        "items": items,
        "search_context": result.get("SearchContext") or {},
        "time_cost": result.get("TimeCost"),
        "log_id": result.get("LogId", ""),
        "card_results": result.get("CardResults"),
        "usage": result.get("Usage"),
    }


def _build_search_payload_meta(payload: dict) -> dict[str, Any]:
    filter_payload = dict(payload.get("Filter") or {})
    return {
        "Query": str(payload.get("Query", "")),
        "SearchType": str(payload.get("SearchType", "")),
        "Count": payload.get("Count"),
        "NeedSummary": bool(payload.get("NeedSummary")),
        "ContentFormats": str(payload.get("ContentFormats", "")),
        "Filter": {
            "NeedContent": bool(filter_payload.get("NeedContent")),
            "NeedUrl": bool(filter_payload.get("NeedUrl")),
            "AuthInfoLevel": filter_payload.get("AuthInfoLevel", 0),
            "Sites": str(filter_payload.get("Sites", "")),
            "BlockHosts": str(filter_payload.get("BlockHosts", "")),
        },
    }


def _build_structured_web_search_response(
    query: str,
    payload_meta: dict[str, Any],
    search_result: dict[str, Any],
    *,
    source_scope: str,
    used_ark_fallback: bool,
) -> dict[str, Any]:
    return {
        "query": query,
        "search_type": payload_meta.get("SearchType", ""),
        "summary": search_result.get("summary", ""),
        "items": list(search_result.get("items", []) or []),
        "search_context": dict(search_result.get("search_context") or {}),
        "source_scope": source_scope,
        "used_ark_fallback": used_ark_fallback,
        "payload_meta": payload_meta,
        "log_id": search_result.get("log_id", ""),
        "time_cost": search_result.get("time_cost"),
    }


def _volc_web_search(
    query: str,
    *,
    count: int = VOLC_WEB_SEARCH_DEFAULT_COUNT,
    search_type: str = "web",
    sites: str = "",
    need_summary: bool = True,
    need_content: bool = False,
    need_url: bool = True,
    query_rewrite: bool = False,
    auth_info_level: int = 0,
    block_hosts: str = "",
    time_range: str = "",
    content_format: str = "markdown",
) -> dict:
    api_key = _get_env_value("VOLC_WEB_SEARCH_API_KEY", "WEB_SEARCH_API_KEY", "TORCHLIGHT_API_KEY", "ARK_WEBSEARCH_API_KEY", "ark_websearch_api_key")
    if not api_key:
        raise RuntimeError("未配置火山联网搜索 API Key")
    payload = _build_volc_web_search_payload(
        query,
        search_type=search_type,
        count=count,
        sites=sites,
        need_summary=need_summary,
        need_content=need_content,
        need_url=need_url,
        query_rewrite=query_rewrite,
        auth_info_level=auth_info_level,
        block_hosts=block_hosts,
        time_range=time_range,
        content_format=content_format,
    )
    payload_meta = _build_search_payload_meta(payload)
    response = requests.post(
        VOLC_WEB_SEARCH_URL,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=payload,
        timeout=VOLC_WEB_SEARCH_TIMEOUT_SEC,
    )
    response.raise_for_status()
    data = response.json()
    error = ((data or {}).get("ResponseMetadata") or {}).get("Error")
    if error:
        raise RuntimeError(f"volc_web_search_error:{error.get('Code')}:{error.get('Message')}")
    normalized = _normalize_web_search_result(data)
    return _build_structured_web_search_response(query, payload_meta, normalized, source_scope="web", used_ark_fallback=False)


def _ark_web_search(query: str, site_hint: str = "", count: int = 5) -> dict:
    api_key = _get_env_value("ARK_WEBSEARCH_API_KEY", "ark_websearch_api_key")
    if not api_key.startswith("ark-"):
        api_key = _get_env_value("COZE_WORKLOAD_IDENTITY_API_KEY")
    if not api_key:
        raise RuntimeError("未配置可用的 Ark API Key")
    base_url = _get_env_value("COZE_INTEGRATION_MODEL_BASE_URL")
    if not base_url:
        raise RuntimeError("未配置 COZE_INTEGRATION_MODEL_BASE_URL")
    model = _get_env_value("ARK_WEBSEARCH_MODEL") or "doubao-seed-2-0-lite-260428"
    user_query = (
        "请执行联网搜索并回答问题。要求：\n"
        "1) 结论后给出可访问的来源链接（http/https）；\n"
        "2) 信息不确定时明确说明；\n"
        f"3) 用户问题：{query}"
    )
    if site_hint:
        user_query = (
            "请执行联网搜索并回答问题。要求：\n"
            f"1) 优先搜索并引用以下站点：{site_hint}；\n"
            "2) 返回可访问的来源链接（http/https）；\n"
            f"3) 用户问题：{query}"
        )
    client = OpenAI(api_key=api_key, base_url=base_url)
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": user_query}],
        web_search_options={"enable": True},
        temperature=0.2,
    )
    content = (resp.choices[0].message.content or "").strip()
    urls = re.findall(r"https?://[^\s)]+", content)
    uniq_urls = []
    for u in urls:
        if u not in uniq_urls:
            uniq_urls.append(u)
    items = [{"title": f"联网搜索结果{i}", "url": u, "snippet": content[:500]} for i, u in enumerate(uniq_urls[:count], start=1)]
    if not items and content:
        items.append({"title": "火山联网搜索摘要", "url": "", "snippet": content[:500]})
    return {"summary": content[:1500], "items": items}


def _web_search(query: str, **kwargs) -> dict:
    try:
        return _volc_web_search(query, **kwargs)
    except Exception as primary_error:
        logger.warning("Structured web search failed, fallback to Ark: %s", primary_error)
        try:
            fallback = _ark_web_search(query=query, site_hint=kwargs.get("sites", ""), count=kwargs.get("count", VOLC_WEB_SEARCH_DEFAULT_COUNT))
        except Exception as fallback_error:
            raise RuntimeError(
                f"web_search_unavailable:primary={type(primary_error).__name__};fallback={type(fallback_error).__name__}"
            ) from fallback_error
        payload = _build_volc_web_search_payload(
            query,
            search_type=str(kwargs.get("search_type") or "web"),
            count=int(kwargs.get("count", VOLC_WEB_SEARCH_DEFAULT_COUNT)),
            sites=str(kwargs.get("sites", "")),
            need_summary=bool(kwargs.get("need_summary", True)),
            need_content=bool(kwargs.get("need_content", False)),
            need_url=bool(kwargs.get("need_url", True)),
            query_rewrite=bool(kwargs.get("query_rewrite", False)),
            auth_info_level=int(kwargs.get("auth_info_level", 0) or 0),
            block_hosts=str(kwargs.get("block_hosts", "")),
            time_range=str(kwargs.get("time_range", "")),
            content_format=str(kwargs.get("content_format", "text")),
        )
        return _build_structured_web_search_response(query, _build_search_payload_meta(payload), fallback, source_scope="web", used_ark_fallback=True)


def _blocked_hostnames(block_hosts: str) -> set[str]:
    return {part.strip().lower().lstrip(".") for part in re.split(r"[|,\s]+", block_hosts or "") if part.strip()}


def _is_blocked_result_url(url: str, blocked_hosts: set[str]) -> bool:
    host = (urlparse(url or "").hostname or "").lower()
    return bool(host) and any(host == blocked or host.endswith(f".{blocked}") for blocked in blocked_hosts)


def _apply_local_block_hosts(raw_result: dict[str, Any], block_hosts: str) -> tuple[dict[str, Any], int]:
    blocked_hosts = _blocked_hostnames(block_hosts)
    if not blocked_hosts:
        return raw_result, 0
    filtered = dict(raw_result)
    items = list(filtered.get("items") or [])
    kept = [item for item in items if not _is_blocked_result_url(str(item.get("url") or ""), blocked_hosts)]
    filtered["items"] = kept
    return filtered, len(items) - len(kept)


@tool
def web_search(
    query: str,
    search_type: str = "web",
    count: int = VOLC_WEB_SEARCH_DEFAULT_COUNT,
    sites: str = "",
    block_hosts: str = "",
    query_rewrite: bool = False,
) -> str:
    """执行结构化联网搜索，并返回结构化分析报告。"""
    rewritten_query = rewrite_web_search_query(query)
    query_type = "authoritative_public_data" if looks_like_authoritative_data_query(rewritten_query) else "hifleet_product" if looks_like_hifleet_product_query(rewritten_query) else "general_public_info"
    resolved_sites = sites.strip()
    if not resolved_sites and query_type == "hifleet_product":
        resolved_sites = HIFLEET_SITES
    if query_type == "authoritative_public_data":
        resolved_sites = ""
    try:
        raw_result = _web_search(
            rewritten_query,
            count=count,
            search_type=search_type,
            sites=resolved_sites,
            need_summary=True,
            need_content=False,
            need_url=True,
            query_rewrite=query_rewrite,
            auth_info_level=0,
            block_hosts=block_hosts,
            content_format="text",
        )
    except RuntimeError as exc:
        return json.dumps(
            {
                "tool": "web_search",
                "query": rewritten_query,
                "status": "unavailable",
                "can_answer": False,
                "should_continue": True,
                "continue_with": "agent_browser",
                "confidence": "low",
                "summary": "联网检索当前不可用，未将其视为无结果",
                "items": [],
                "best_urls": [],
                "recommended_next_action": "尝试页面核验或返回保守回复",
                "trace": {
                    "query_type": query_type,
                    "failure_code": str(exc),
                    "used_ark_fallback": False,
                },
            },
            ensure_ascii=False,
        )
    raw_result, locally_blocked_count = _apply_local_block_hosts(raw_result, block_hosts)
    request_profile = dict(raw_result.get("payload_meta") or {})
    request_profile.setdefault("Filter", {})
    request_profile["Filter"]["BlockHosts"] = block_hosts
    analysis = analyze_web_search_result(rewritten_query, request_profile, raw_result)
    payload = {
        "tool": "web_search",
        "query": rewritten_query,
        "status": "ok",
        "can_answer": bool(analysis["analysis"]["can_answer"]),
        "should_continue": bool(analysis["analysis"]["should_continue"]),
        "continue_with": str(analysis["analysis"]["continue_with"]),
        "confidence": "high" if analysis["analysis"]["can_answer"] else ("medium" if analysis["items"] else "low"),
        "summary": str(analysis["analysis"]["reason"]),
        "items": analysis["items"],
        "best_urls": list(analysis["analysis"]["best_urls"]),
        "recommended_next_action": "直接基于当前检索结果回答用户" if analysis["analysis"]["can_answer"] else "继续抓取具体页面" if analysis["analysis"]["continue_with"] == "agent_browser" else "调整 query 或过滤条件后重试",
        "trace": {
            "query_type": query_type,
            "question_class": str(analysis["analysis"].get("question_class") or analysis["result_profile"].get("question_class") or ""),
            "request_profile": analysis["request_profile"],
            "result_profile": analysis["result_profile"],
            "risk_flags": list(analysis["analysis"]["risk_flags"]),
            "web_answerability_reason": str(analysis["analysis"].get("reason") or ""),
            "used_ark_fallback": bool(raw_result.get("used_ark_fallback")),
            "block_hosts_applied_locally": bool(block_hosts),
            "blocked_result_count": locally_blocked_count,
        },
    }
    return json.dumps(payload, ensure_ascii=False)





__all__ = ["web_search"]
