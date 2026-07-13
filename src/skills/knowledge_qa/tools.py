"""
knowledge_qa 对外工具导出层。

说明：
- 保持 knowledge_qa 为单一 skill
- 对外暴露三工具 + smart_search 兼容 facade
- 内部实现已拆到 runtime / bridge 模块
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from typing import Any, Optional
from urllib.parse import urlparse

import requests
from langchain.tools import tool
from openai import OpenAI

from coze_coding_utils.log.write_log import request_context
from coze_coding_utils.runtime_ctx.context import new_context
from skills.common.tool_result import ToolResult, emit_tool_metric
from skills.knowledge_qa.browser_bridge import build_browser_bridge_payload
from skills.knowledge_qa.local_kb_runtime import (
    LOCAL_KB_TOP_K_DEFAULT,
    format_local_kb_response,
    normalize_query_text,
    search_local_kb_structured,
)
from skills.knowledge_qa.web_search_runtime import (
    DEFAULT_HELP_CENTER_URL,
    DEEP_VARIANTS_MAX,
    DOMAIN_AUTHORITY,
    HIFLEET_COMMUNITY_URL,
    HIFLEET_SITES,
    QUERY_EXPANSION_HINTS,
    TROUBLESHOOTING_FASTPATH_MARKERS,
    VOLC_WEB_SEARCH_DEFAULT_COUNT,
    analyze_web_search_result,
    expand_query_variants,
    format_web_result,
    get_authority_label,
    is_hifleet_official_url,
    looks_like_authoritative_data_query,
    looks_like_hifleet_product_query,
    resolve_authority_score,
    rewrite_web_search_query,
    sanitize_snippet_text,
    should_use_helpcenter_fastpath,
)

logger = logging.getLogger(__name__)
VOLC_WEB_SEARCH_URL = "https://open.feedcoopapi.com/search_api/web_search"
SMART_SEARCH_CACHE_TTL_SEC = int(os.getenv("SMART_SEARCH_CACHE_TTL_SEC", "600"))
URL_CHECK_TIMEOUT_SEC = float(os.getenv("SMART_SEARCH_URL_TIMEOUT_SEC", "2.0"))
URL_CHECK_TOP_N = int(os.getenv("SMART_SEARCH_URL_TOP_N", "2"))
VOLC_WEB_SEARCH_TIMEOUT_SEC = float(os.getenv("VOLC_WEB_SEARCH_TIMEOUT_SEC", "15"))

_SEARCH_CACHE_LOCK = threading.Lock()
_SEARCH_CACHE: dict = {}
_STRUCTURED_TRACE_LOCK = threading.Lock()
_STRUCTURED_TRACE_CACHE: dict[str, dict[str, Any]] = {}

PLATFORM_GLOSSARY = {
    "绿点": "地图上的绿点/绿色三角/绿色菱形代表渔船、运鱼船、网位仪等渔业相关船舶或设备。黄色代表普通商船（散货船、集装箱船、油轮等）。",
    "绿点图": "地图上的绿点/绿色三角/绿色菱形代表渔船、运鱼船、网位仪等渔业相关船舶或设备。黄色代表普通商船（散货船、集装箱船、油轮等）。",
    "船舶颜色": "HiFleet地图上船舶颜色区分船型：绿色=渔船、运鱼船、网位仪等渔业相关；黄色=普通商船（散货船、集装箱船、油轮等）。",
    "三角图标": "船舶的另一种显示模式，三角方向表示航向。绿色三角=渔船等渔业相关；黄色三角=普通商船。",
    "岸基值班": "HiFleet岸基值班与船舶点验系统，深度贴合海事第17号通告三项指引，用AIS+智能视频+气象+告警+风险五维融合，实现自动点验、航线风险管控、航行+视频一体化告警等，帮助航运企业安全管理数字化。",
    "船舶点验": "岸基值班系统的核心功能，依据海事第17号通告要求，自动对船舶进行状态核验，支持定期点验和临时点验。",
    "AIS": "船舶自动识别系统（Automatic Identification System），船舶通过VHF频段自动播报船位、航速、航向等信息，是船舶监控的基础数据源。",
    "DTU": "数据传输单元，安装在船舶上用于实时回传AIS数据的设备。HiFleet提供自研DTU和通用DTU两种方案。",
    "ETA": "预计到达时间（Estimated Time of Arrival），基于船舶当前航速和航线计算的目的港到达时间。",
    "CII": "碳强度指标（Carbon Intensity Indicator），IMO要求船舶年度运营碳强度达标，评级A-E。",
    "EEXI": "现有船舶能效指数（Energy Efficiency Existing Ship Index），IMO要求现有船舶满足的能效标准。",
    "PSC": "港口国监督（Port State Control），港口国对到港外国船舶实施的检查，确保符合国际公约要求。",
}


def _emit_search_metric(ctx, result: ToolResult):
    run_id = getattr(ctx, "run_id", "")
    emit_tool_metric(
        "smart_search",
        run_id,
        result,
        layer_trace={
            "method": getattr(ctx, "method", ""),
            "source_channel": getattr(ctx, "source_channel", ""),
        },
    )


def _cache_key(query: str, depth: str) -> str:
    q = query.strip().lower()
    q = re.sub(r"\s+", "", q)
    q = re.sub(r"[，。！？、,.!?：:;；（）()【】\[\]\"'`]", "", q)
    if "轨迹" in q and any(m in q for m in ("无反应", "异常", "故障", "加载失败", "不显示")):
        q = "轨迹故障排查"
    elif "船位" in q and any(m in q for m in ("更新慢", "延迟", "不刷新")):
        q = "船位更新慢"
    return f"{depth}::{q[:80]}"


def _cache_get(query: str, depth: str) -> Optional[str]:
    if SMART_SEARCH_CACHE_TTL_SEC <= 0:
        return None
    key = _cache_key(query, depth)
    now = time.time()
    with _SEARCH_CACHE_LOCK:
        item = _SEARCH_CACHE.get(key)
        if not item:
            return None
        if now - item["ts"] > SMART_SEARCH_CACHE_TTL_SEC:
            _SEARCH_CACHE.pop(key, None)
            return None
        return item["value"]


def _cache_set(query: str, depth: str, value: str):
    if SMART_SEARCH_CACHE_TTL_SEC <= 0:
        return
    key = _cache_key(query, depth)
    with _SEARCH_CACHE_LOCK:
        _SEARCH_CACHE[key] = {"value": value, "ts": time.time()}


def _structured_trace_key(query: str, depth: str) -> str:
    return _cache_key(query, depth)


def _cache_structured_trace(query: str, depth: str, value: dict[str, Any]):
    if SMART_SEARCH_CACHE_TTL_SEC <= 0:
        return
    key = _structured_trace_key(query, depth)
    with _STRUCTURED_TRACE_LOCK:
        _STRUCTURED_TRACE_CACHE[key] = {"value": value, "ts": time.time()}


def get_structured_search_trace(query: str, depth: str = "normal") -> dict[str, Any]:
    if SMART_SEARCH_CACHE_TTL_SEC <= 0:
        return {}
    key = _structured_trace_key(query, depth)
    now = time.time()
    with _STRUCTURED_TRACE_LOCK:
        item = _STRUCTURED_TRACE_CACHE.get(key)
        if not item:
            return {}
        if now - item["ts"] > SMART_SEARCH_CACHE_TTL_SEC:
            _STRUCTURED_TRACE_CACHE.pop(key, None)
            return {}
        value = item.get("value")
    return dict(value) if isinstance(value, dict) else {}


def _match_glossary(query: str) -> Optional[tuple[str, str]]:
    query_lower = query.lower().strip()
    for term, definition in PLATFORM_GLOSSARY.items():
        if term in query_lower:
            return term, definition
    return None


def _new_structured_search_trace(query: str, depth: str) -> dict[str, Any]:
    return {
        "query": query,
        "depth": depth,
        "t0_kb_hit": False,
        "layers": [],
        "t1_query": "",
        "t1_payload_meta": {},
        "t1_source_count": 0,
        "t1_official_source_count": 0,
        "t1_used_ark_fallback": False,
        "source_scope": "",
        "items": [],
        "summary": "",
    }


def _search_knowledge_base(query: str, ctx) -> dict:
    from coze_coding_dev_sdk import Config, KnowledgeClient

    config = Config()
    client = KnowledgeClient(config=config, ctx=ctx)
    results = {"faq": [], "wiki": []}
    try:
        outputs_resp = client.search(query=query, table_names=["hifleet_cs_outputs_v2"], top_k=5, min_score=0.30)
        if outputs_resp and outputs_resp.chunks:
            for chunk in outputs_resp.chunks:
                results["faq"].append({"score": chunk.score, "content": chunk.content, "source_type": _detect_source_type(chunk.content)})
    except Exception as e:
        logger.warning(f"FAQ search error: {e}")
    try:
        wiki_resp = client.search(query=query, table_names=["hifleet_cs_wiki_v2"], top_k=3, min_score=0.30)
        if wiki_resp and wiki_resp.chunks:
            for chunk in wiki_resp.chunks:
                results["wiki"].append({"score": chunk.score, "content": chunk.content})
    except Exception as e:
        logger.warning(f"Wiki search error: {e}")
    return results


def _detect_source_type(content: str) -> str:
    faq_markers = ["【关键词】", "【问题】", "【答案】", "【分类】", "【转人工场景】"]
    wiki_markers = ["## ", "# ", "=== ", "---"]
    faq_count = sum(1 for m in faq_markers if m in content)
    wiki_count = sum(1 for m in wiki_markers if m in content)
    if faq_count >= 2:
        return "faq"
    if wiki_count >= 1:
        return "wiki"
    return "unknown"


def _is_url_accessible(url: str, timeout_sec: float = URL_CHECK_TIMEOUT_SEC) -> bool:
    if not url or not url.startswith(("http://", "https://")):
        return False
    try:
        resp = requests.head(url, timeout=timeout_sec, allow_redirects=True)
        if 200 <= resp.status_code < 400:
            return True
        resp = requests.get(url, timeout=timeout_sec, allow_redirects=True)
        return 200 <= resp.status_code < 400
    except Exception:
        return False


def _filter_accessible_items(items: list, require_hifleet_domain: bool = False, top_n: int = URL_CHECK_TOP_N) -> list:
    filtered = []
    if top_n <= 0:
        top_n = len(items)
    checked = 0
    for item in items:
        url = str(item.get("url", "")).strip()
        if not url:
            continue
        if require_hifleet_domain and "hifleet.com" not in url:
            continue
        if checked >= top_n:
            break
        checked += 1
        if _is_url_accessible(url):
            if "help.hifleet.com" in url:
                item["url"] = DEFAULT_HELP_CENTER_URL
            filtered.append(item)
    return filtered


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


def _search_hifleet_site(query: str, ctx, *, return_trace: bool = False) -> list | tuple[list, dict[str, Any]]:
    trace_result: dict[str, Any] = {}
    if should_use_helpcenter_fastpath(query):
        results = [{"title": "HiFleet 帮助中心", "url": DEFAULT_HELP_CENTER_URL, "snippet": "官方平台使用与问题排查文档入口", "full_content": "", "content_quality": "official_fastpath"}]
        return (results, trace_result) if return_trace else results
    results = []
    try:
        search = _web_search(query=query, count=5, search_type="web", sites=HIFLEET_SITES, need_summary=True, need_content=False, need_url=True, query_rewrite=True, auth_info_level=1, content_format="text")
        trace_result = search
        for item in search.get("items", []):
            url = item.get("url", "")
            if "hifleet" not in url:
                continue
            results.append(
                {
                    "title": item.get("title", ""),
                    "url": url,
                    "snippet": sanitize_snippet_text(item.get("summary") or item.get("snippet", "")),
                    "full_content": "",
                    "content_quality": "summary" if item.get("summary") else "snippet" if item.get("snippet") else "link_only",
                    "publish_time": item.get("publish_time", ""),
                    "authority_level": item.get("authority_level", 1),
                }
            )
        results = _filter_accessible_items(results, require_hifleet_domain=True)
        if not results:
            results = [{"title": "HiFleet 官方社区", "url": HIFLEET_COMMUNITY_URL, "snippet": "HiFleet 官方社区与产品信息入口", "full_content": "", "content_quality": "official_fallback"}]
    except Exception as e:
        logger.warning(f"Hifleet site search error: {e}")
    return (results, trace_result) if return_trace else results


def _search_web_enhanced(query: str, ctx, *, return_trace: bool = False) -> dict | tuple[dict, dict[str, Any]]:
    result = {"items": [], "summary": ""}
    trace_result: dict[str, Any] = {}
    try:
        search = _web_search(query=query, count=VOLC_WEB_SEARCH_DEFAULT_COUNT, search_type="web", need_summary=True, need_content=False, need_url=True, query_rewrite=False, auth_info_level=0, content_format="text")
        trace_result = search
        result["summary"] = search.get("summary", "")
        for item in search.get("items", []):
            url = item.get("url", "")
            domain = urlparse(url).netloc.lower().lstrip("www.")
            authority = resolve_authority_score(domain, item.get("authority_level"))
            result["items"].append(
                {
                    "title": item.get("title", ""),
                    "url": url,
                    "snippet": sanitize_snippet_text(item.get("summary") or item.get("snippet", "")),
                    "authority": authority,
                    "authority_label": get_authority_label(authority),
                    "full_content": "",
                    "publish_time": item.get("publish_time", ""),
                    "site_name": item.get("site_name", ""),
                }
            )
        result["items"] = _filter_accessible_items(result["items"], require_hifleet_domain=False)
        if not result["items"]:
            result["items"] = [{"title": "HiFleet 帮助中心", "url": DEFAULT_HELP_CENTER_URL, "snippet": "官方平台使用与问题排查文档入口", "authority": 1.0, "authority_label": "🟢 权威", "full_content": ""}]
    except Exception as e:
        logger.warning(f"Web search error: {e}")
    return (result, trace_result) if return_trace else result


def _search_web_deep_single(query: str) -> dict:
    result = {"items": [], "summary": "", "payload_meta": {}, "query": query, "used_ark_fallback": False}
    try:
        search = _web_search(query=query, count=VOLC_WEB_SEARCH_DEFAULT_COUNT, search_type="web_summary", need_summary=True, need_content=False, need_url=True, query_rewrite=True, auth_info_level=0, content_format="text")
        result["summary"] = search.get("summary", "")
        result["payload_meta"] = dict(search.get("payload_meta") or {})
        result["used_ark_fallback"] = bool(search.get("used_ark_fallback"))
        for item in search.get("items", []):
            url = item.get("url", "")
            domain = urlparse(url).netloc.lower().lstrip("www.")
            authority = resolve_authority_score(domain, item.get("authority_level"))
            result["items"].append(
                {
                    "title": item.get("title", ""),
                    "url": url,
                    "snippet": sanitize_snippet_text(item.get("summary") or item.get("snippet", "")),
                    "authority": authority,
                    "authority_label": get_authority_label(authority),
                    "full_content": "",
                    "publish_time": item.get("publish_time", ""),
                    "site_name": item.get("site_name", ""),
                }
            )
        result["items"] = _filter_accessible_items(result["items"], require_hifleet_domain=False)
    except Exception as e:
        logger.warning(f"Deep web search error: {e}")
    return result


def _search_web_deep_multi(query: str, ctx, *, return_trace: bool = False) -> dict | tuple[dict, dict[str, Any]]:
    merged = {"items": [], "summary": ""}
    trace_candidate: dict[str, Any] = {}
    variants = expand_query_variants(query)
    for i, q in enumerate(variants):
        chunk = _search_web_deep_single(q)
        if chunk.get("summary"):
            merged["summary"] += f"\n查询{i+1}（{q}）：{chunk['summary'][:500]}"
        merged["items"].extend(chunk.get("items", []))
        if not trace_candidate and chunk.get("payload_meta"):
            trace_candidate = chunk
    uniq = {}
    for item in merged["items"]:
        url = item.get("url", "")
        key = url or f"{item.get('title','')}-{item.get('snippet','')[:60]}"
        old = uniq.get(key)
        if old is None or item.get("authority", 0) > old.get("authority", 0):
            uniq[key] = item
    items = sorted(list(uniq.values()), key=lambda x: x.get("authority", 0), reverse=True)
    merged["items"] = items[:8]
    merged["summary"] = merged["summary"][:1800]
    if trace_candidate:
        trace_candidate = {**trace_candidate, "items": merged["items"], "summary": merged["summary"]}
    return (merged, trace_candidate) if return_trace else merged


def _format_glossary_result(term: str, definition: str) -> str:
    return f"【术语：{term}】\n{definition}"


_normalize_query_text = normalize_query_text
_looks_like_authoritative_data_query = looks_like_authoritative_data_query
_looks_like_hifleet_product_query = looks_like_hifleet_product_query
_is_hifleet_official_url = is_hifleet_official_url
_rewrite_web_search_query = rewrite_web_search_query
_sanitize_snippet_text = sanitize_snippet_text
_should_use_helpcenter_fastpath = should_use_helpcenter_fastpath
_resolve_authority_score = resolve_authority_score
_get_authority_label = get_authority_label
_expand_query_variants = expand_query_variants
_analyze_web_search_result = analyze_web_search_result
_search_local_kb_structured = search_local_kb_structured
_format_web_result = format_web_result


def _format_knowledge_result(kb_results: dict) -> str:
    parts = []
    faq_items = kb_results.get("faq", [])
    wiki_items = kb_results.get("wiki", [])
    has_faq = any(item["source_type"] == "faq" and item["score"] >= 0.40 for item in faq_items)
    if has_faq:
        parts.append("【优先匹配 - FAQ/标准回复】")
        for item in faq_items:
            if item["source_type"] == "faq" and item["score"] >= 0.40:
                parts.append(f"\n**相关度: {item['score']:.2f}**\n{item['content']}")
    elif faq_items:
        top = faq_items[0]
        if top["score"] >= 0.35:
            parts.append("【可能相关 - 标准回复（相关度较低）】")
            parts.append(f"\n**相关度: {top['score']:.2f}**\n{top['content']}")
    if wiki_items:
        parts.append("\n【主题说明（补充参考）】")
        for item in wiki_items[:2]:
            parts.append(f"\n**相关度: {item['score']:.2f}**\n{item['content'][:500]}")
    return "\n".join(parts)


def _format_site_result(site_results: list, query: str) -> str:
    if not site_results:
        return ""
    parts = ["【HiFleet 官方资料】"]
    for item in site_results:
        source_label = "官方社区" if "wp/communities" in str(item.get("url", "")) else "官网/帮助中心"
        parts.append(f"\n**{item['title']}**")
        parts.append(f"来源：{source_label}")
        if item["full_content"]:
            parts.append(f"内容摘要：{item['full_content'][:800]}...")
        elif item["snippet"]:
            parts.append(f"摘要：{item['snippet']}")
        if item.get("publish_time"):
            parts.append(f"发布时间：{item['publish_time']}")
        parts.append(f"🔗 {item['url']}")
    if DEFAULT_HELP_CENTER_URL not in [str(i.get("url", "")) for i in site_results]:
        parts.append(f"\n🔗 官方帮助中心入口：{DEFAULT_HELP_CENTER_URL}")
    if HIFLEET_COMMUNITY_URL not in [str(i.get("url", "")) for i in site_results]:
        parts.append(f"🔗 官方社区入口：{HIFLEET_COMMUNITY_URL}")
    return "\n".join(parts)


def _format_browser_response(payload: dict[str, Any]) -> str:
    pages = list(payload.get("pages") or [])
    if not pages:
        return "未检索到足够可信的信息"
    lines = ["【公开资料参考】"]
    for page in pages[:2]:
        lines.append(f"\n**{page.get('title', 'HiFleet 页面')}**")
        lines.append(f"摘要: {page.get('excerpt', '')}")
        lines.append(f"🔗 {page.get('url', '')}")
    return "\n".join(lines)


@tool
def local_kb_search(query: str, top_k: int = LOCAL_KB_TOP_K_DEFAULT) -> str:
    """检索本地 docs/RAG 知识库，并返回结构化结果供 agent 判断是否继续联网搜索。"""
    payload = search_local_kb_structured(query, top_k=top_k)
    return json.dumps(payload, ensure_ascii=False)


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


@tool
def web_search_agent_browser(query: str, target_urls: str = "", site_hint: str = "") -> str:
    """基于候选 URL 或 HiFleet 官方域范围做受限深抓，返回结构化页面证据。"""
    from skills.browser_verify.tools import agent_browser_deep_search

    browser_output = agent_browser_deep_search.invoke({"query": query, "target_urls": target_urls, "site_hint": site_hint})
    parse_error = ""
    try:
        parsed = json.loads(browser_output)
    except Exception as exc:
        parsed = {}
        parse_error = type(exc).__name__
    payload = build_browser_bridge_payload(query, target_urls, site_hint, parsed, browser_output, parse_error)
    return json.dumps(payload, ensure_ascii=False)


@tool
def smart_search(query: str, depth: str = "normal") -> str:
    """兼容旧链路的统一搜索入口，内部按 local_kb_search 与 web_search 的顺序编排。"""
    ctx = request_context.get() or new_context(method="smart_search")
    depth = depth.lower().strip()
    if depth not in ("quick", "normal", "deep"):
        depth = "normal"
    cached = _cache_get(query, depth)
    if cached:
        logger.info(f"[smart_search] cache hit: query='{query}', depth='{depth}'")
        return cached

    glossary_match = _match_glossary(query)
    layer_trace = []
    structured_trace = _new_structured_search_trace(query, depth)
    if glossary_match:
        term, definition = glossary_match
        output = _format_glossary_result(term, definition)
        layer_trace.append({"layer": "L1", "hit": True, "reason": "glossary"})
        structured_trace["layers"] = list(layer_trace)
        _cache_set(query, depth, output)
        _cache_structured_trace(query, depth, structured_trace)
        return output

    kb_payload = search_local_kb_structured(query, top_k=LOCAL_KB_TOP_K_DEFAULT)
    layer_trace.append({"layer": "L2-L3", "hit": bool(kb_payload.get("items"))})
    structured_trace["t0_kb_hit"] = bool(kb_payload.get("items"))
    kb_output = format_local_kb_response(kb_payload, DEFAULT_HELP_CENTER_URL) if kb_payload.get("items") else ""
    if kb_payload.get("can_answer") and depth == "quick":
        structured_trace["layers"] = list(layer_trace)
        _cache_set(query, depth, kb_output)
        _cache_structured_trace(query, depth, structured_trace)
        return kb_output

    web_output = ""
    if depth in {"normal", "deep"} or not kb_payload.get("can_answer"):
        web_payload = json.loads(
            web_search.invoke({"query": query, "search_type": "web_summary" if depth == "deep" else "web", "count": VOLC_WEB_SEARCH_DEFAULT_COUNT})
        )
        request_profile = dict(((web_payload.get("trace") or {}).get("request_profile") or {}))
        result_items = list(web_payload.get("items") or [])
        structured_trace["t1_query"] = str(web_payload.get("query") or query)
        structured_trace["t1_payload_meta"] = request_profile
        structured_trace["t1_source_count"] = len(result_items)
        structured_trace["t1_official_source_count"] = sum(1 for item in result_items if item.get("is_hifleet_official") or item.get("is_authoritative"))
        structured_trace["t1_used_ark_fallback"] = bool(((web_payload.get("trace") or {}).get("used_ark_fallback")))
        structured_trace["items"] = result_items
        structured_trace["summary"] = str(web_payload.get("summary") or "")
        structured_trace["source_scope"] = "web"
        structured_trace["question_class"] = str(((web_payload.get("trace") or {}).get("question_class") or ""))
        structured_trace["risk_flags"] = list(((web_payload.get("trace") or {}).get("risk_flags") or []))
        structured_trace["web_answerability_reason"] = str(((web_payload.get("trace") or {}).get("web_answerability_reason") or web_payload.get("summary") or ""))
        layer_trace.append({"layer": "L4-L5", "hit": bool(result_items), "continue_with": web_payload.get("continue_with", "")})
        if web_payload.get("can_answer"):
            web_output = format_web_result({"summary": web_payload.get("summary", ""), "items": result_items})
        elif result_items and kb_output and structured_trace["question_class"] not in {"how_to_operate", "issue_feedback"}:
            web_output = format_web_result({"summary": web_payload.get("summary", ""), "items": result_items})

    final_parts = [part for part in [kb_output, web_output] if part]
    if not final_parts:
        output = (
            "抱歉，当前未检索到足够可信的公开信息。\n\n"
            "建议：\n"
            "1. 补充更具体信息（船名/MMSI/发生时间/异常现象）以便继续排查\n"
            "2. 联系人工客服：400-963-6899（微信：hifleetkhzs）\n"
            f"3. 访问帮助中心：{DEFAULT_HELP_CENTER_URL}"
        )
    else:
        output = "\n\n".join(final_parts)
    structured_trace["layers"] = list(layer_trace)
    _cache_set(query, depth, output)
    _cache_structured_trace(query, depth, structured_trace)
    _emit_search_metric(ctx, ToolResult(status="ok", code="SMART_SEARCH_OK", message=output, source="search", data={"layer_trace": layer_trace}))
    return output


__all__ = [
    "DEFAULT_HELP_CENTER_URL",
    "HIFLEET_COMMUNITY_URL",
    "HIFLEET_SITES",
    "VOLC_WEB_SEARCH_URL",
    "_analyze_web_search_result",
    "_ark_web_search",
    "_build_search_payload_meta",
    "_build_structured_web_search_response",
    "_build_volc_web_search_payload",
    "_cache_get",
    "_cache_set",
    "_cache_structured_trace",
    "_detect_source_type",
    "_expand_query_variants",
    "_filter_accessible_items",
    "_format_browser_response",
    "_format_glossary_result",
    "_format_knowledge_result",
    "_format_site_result",
    "_format_web_result",
    "_get_env_value",
    "_get_authority_label",
    "_is_hifleet_official_url",
    "_is_url_accessible",
    "_looks_like_authoritative_data_query",
    "_looks_like_hifleet_product_query",
    "_match_glossary",
    "_new_structured_search_trace",
    "_normalize_query_text",
    "_normalize_web_search_result",
    "_resolve_authority_score",
    "_rewrite_web_search_query",
    "_sanitize_snippet_text",
    "_search_hifleet_site",
    "_search_knowledge_base",
    "_search_local_kb_structured",
    "_search_web_deep_multi",
    "_search_web_deep_single",
    "_search_web_enhanced",
    "_should_use_helpcenter_fastpath",
    "_volc_web_search",
    "_web_search",
    "get_structured_search_trace",
    "local_kb_search",
    "smart_search",
    "web_search",
    "web_search_agent_browser",
]
