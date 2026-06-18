from __future__ import annotations

import os
import re
from typing import Any, Optional
from urllib.parse import urlparse

from skills.knowledge_qa.local_kb_runtime import normalize_query_text, keyword_tokens

VOLC_WEB_SEARCH_DEFAULT_COUNT = int(os.getenv("VOLC_WEB_SEARCH_DEFAULT_COUNT", "5"))
DEEP_VARIANTS_MAX = int(os.getenv("SMART_SEARCH_DEEP_VARIANTS_MAX", "3"))
HIFLEET_SITES = "hifleet.com|help.hifleet.com|www.hifleet.com|www.hifleet.com/wp/communities|ttse.hifleet.com"
HIFLEET_COMMUNITY_URL = "https://www.hifleet.com/wp/communities"
DEFAULT_HELP_CENTER_URL = "https://www.hifleet.com/helpcenter/?i18n=zh"

DOMAIN_AUTHORITY = {
    "hifleet.com": 1.0, "help.hifleet.com": 1.0, "www.hifleet.com": 1.0, "ttse.hifleet.com": 1.0,
    "msa.gov.cn": 0.95, "mot.gov.cn": 0.90, "imo.org": 0.95,
    "xindemarinenews.com": 0.85, "schinese.shippingazette.com": 0.80,
    "worldmaritimenews.com": 0.80, "seatrade-maritime.com": 0.75,
    "baike.baidu.com": 0.50, "zhihu.com": 0.40, "wikipedia.org": 0.60,
}

QUERY_EXPANSION_HINTS = {
    "船位更新慢": ["AIS 数据延迟 原因", "船位刷新慢 原因 处理", "hifleet 船位 更新 延迟"],
    "报警系统": ["hifleet 报警系统 告警 说明", "航运 告警 系统 配置 常见问题", "ais 告警 误报 漏报 处理"],
}

TROUBLESHOOTING_FASTPATH_MARKERS = ["无反应", "更新慢", "加载失败", "不显示", "不刷新", "异常", "故障", "报错", "报警"]


def looks_like_authoritative_data_query(query: str) -> bool:
    q = normalize_query_text(query)
    return any(marker in q for marker in ["今日", "今天", "最新", "长江水位", "水位", "指数", "行情", "运价"])


def looks_like_hifleet_product_query(query: str) -> bool:
    q = normalize_query_text(query)
    product_markers = ["hifleet", "船队在线", "功能", "产品", "社区", "帮助中心", "筛选", "船队", "视频监控", "岸基", "点验"]
    return any(marker in q for marker in product_markers)


def is_hifleet_official_url(url: str) -> bool:
    host = (urlparse(url or "").hostname or "").lower()
    return host == "hifleet.com" or host.endswith(".hifleet.com")


def rewrite_web_search_query(query: str) -> str:
    q = str(query or "").strip()
    if not q:
        return ""
    if looks_like_authoritative_data_query(q):
        if "长江水位" in q and "长江海事局" not in q and "交通运输部" not in q:
            return "今日长江水位 长江海事局 交通运输部"
        return normalize_query_text(q).replace(" ", " ")
    if looks_like_hifleet_product_query(q):
        parts: list[str] = ["hifleet"]
        grouped_markers = [
            ("筛选船队", ["筛选船队", "筛选", "船队"]),
            ("记忆功能", ["记忆功能", "记忆"]),
            ("智能视频监控", ["智能视频监控", "视频监控"]),
            ("岸基值班", ["岸基值班", "点验"]),
            ("帮助中心", ["帮助中心"]),
        ]
        for label, markers in grouped_markers:
            if any(marker in q for marker in markers) and label not in parts:
                parts.append(label)
        if len(parts) > 1:
            return " ".join(parts[:5])
    tokens = keyword_tokens(q)
    filtered = []
    for token in tokens:
        if token in {"有", "吗", "怎么", "如何", "是什么", "使用说明", "产品功能"}:
            continue
        if token not in filtered:
            filtered.append(token)
    if looks_like_hifleet_product_query(q) and "hifleet" not in filtered:
        filtered.insert(0, "hifleet")
    return " ".join(filtered[:5]) or q


def should_use_helpcenter_fastpath(query: str) -> bool:
    q = (query or "").strip()
    return any(marker in q for marker in TROUBLESHOOTING_FASTPATH_MARKERS)


def resolve_authority_score(domain: str, authority_level: Optional[int]) -> float:
    if domain in DOMAIN_AUTHORITY:
        return DOMAIN_AUTHORITY[domain]
    if authority_level == 1:
        return 0.95
    if authority_level == 2:
        return 0.80
    if authority_level == 3:
        return 0.60
    if authority_level == 4:
        return 0.35
    return 0.30


def get_authority_label(score: float) -> str:
    if score >= 0.9:
        return "🟢 权威"
    if score >= 0.7:
        return "🟡 可信"
    if score >= 0.5:
        return "🟠 一般"
    return "🔴 待验证"


def sanitize_snippet_text(text: str) -> str:
    if not text:
        return ""
    return re.sub(r"https?://[^\s)]+", "", text).strip()


def expand_query_variants(query: str) -> list[str]:
    variants = [query]
    q = query.strip()
    for key, extra in QUERY_EXPANSION_HINTS.items():
        if key in q:
            variants.extend(extra)
    troubleshooting_markers = ["异常", "失败", "慢", "延迟", "告警", "报警", "无法", "不显示", "不刷新"]
    if any(m in q for m in troubleshooting_markers):
        variants.extend([f"{q} 原因", f"{q} 解决办法", f"Hifleet {q} 常见问题"])
    dedup = []
    for v in variants:
        vv = v.strip()
        if vv and vv not in dedup:
            dedup.append(vv)
    return dedup[: max(1, DEEP_VARIANTS_MAX)]


def is_specific_page(url: str) -> bool:
    lowered = (url or "").lower()
    if not lowered.startswith(("http://", "https://")):
        return False
    if "/wp/communities/" in lowered:
        return lowered.count("/") >= 5 and not lowered.rstrip("/").endswith("/wp/communities")
    generic_markers = ["/wp/community/", "/helpcenter/", "/category/", "/tag/", "/announcement", "/download/"]
    if any(marker in lowered for marker in generic_markers):
        return False
    if lowered.endswith(("/", ".com", ".cn")):
        return False
    return lowered.count("/") >= 3


def is_directory_page(url: str, title: str = "", snippet: str = "") -> bool:
    lowered_url = (url or "").lower()
    lowered_title = normalize_query_text(title)
    lowered_snippet = normalize_query_text(snippet)
    return (
        lowered_url.rstrip("/").endswith("/wp/communities")
        or any(marker in lowered_url for marker in ["/helpcenter/", "/category/", "/tag/", "/announcement"])
        or any(marker in lowered_title for marker in ["帮助中心", "官方社区", "更新日志", "公告"])
        or any(marker in lowered_snippet for marker in ["入口", "目录", "列表", "首页"])
    )


def is_aggregated_page(url: str, title: str = "", site_name: str = "") -> bool:
    text = " ".join([url or "", title or "", site_name or ""]).lower()
    return any(marker in text for marker in ["app store", "下载", "问答", "announcement", "category"])


def has_specific_fact(text: str) -> bool:
    lowered = normalize_query_text(text)
    return bool(
        re.search(r"\b20\d{2}[-/年]\d{1,2}", lowered)
        or re.search(r"\b\d+(?:\.\d+)?\b", lowered)
        or any(marker in lowered for marker in ["支持", "可以", "新增", "上线", "功能", "记忆", "接入", "权限"])
    )


def analyze_web_search_result(query: str, request_profile: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    items = []
    best_urls: list[str] = []
    official_count = 0
    authoritative_count = 0
    specific_page_count = 0
    directory_page_count = 0
    aggregated_page_count = 0
    for raw_item in list(result.get("items") or []):
        url = str(raw_item.get("url", "")).strip()
        title = str(raw_item.get("title", "")).strip()
        summary = str(raw_item.get("summary") or raw_item.get("snippet") or "").strip()
        site_name = str(raw_item.get("site_name", "")).strip()
        authority_level = raw_item.get("authority_level")
        is_hifleet_official = is_hifleet_official_url(url)
        is_authoritative = int(authority_level or 0) == 1
        page_is_specific = is_specific_page(url)
        page_is_directory = is_directory_page(url, title, summary)
        page_is_aggregated = is_aggregated_page(url, title, site_name)
        item_has_specific_fact = has_specific_fact(summary)
        if is_hifleet_official:
            official_count += 1
        if is_authoritative:
            authoritative_count += 1
        if page_is_specific:
            specific_page_count += 1
        if page_is_directory:
            directory_page_count += 1
        if page_is_aggregated:
            aggregated_page_count += 1
        item = {
            "title": title,
            "url": url,
            "site_name": site_name,
            "summary": str(raw_item.get("summary", "")).strip(),
            "snippet": str(raw_item.get("snippet", "")).strip(),
            "publish_time": str(raw_item.get("publish_time", "")).strip(),
            "authority_level": authority_level,
            "authority_desc": str(raw_item.get("authority_desc", "")).strip(),
            "rank_score": raw_item.get("rank_score"),
            "is_hifleet_official": is_hifleet_official,
            "is_authoritative": is_authoritative,
            "is_specific_page": page_is_specific,
            "is_directory_page": page_is_directory,
            "is_aggregated_page": page_is_aggregated,
            "has_specific_fact": item_has_specific_fact,
        }
        items.append(item)
        if page_is_specific and (is_hifleet_official or is_authoritative) and url and url not in best_urls:
            best_urls.append(url)

    risk_flags: list[str] = []
    request_sites = str(((request_profile.get("Filter") or {}).get("Sites", ""))).strip()
    if looks_like_authoritative_data_query(query) and request_sites:
        risk_flags.append("site_filter_polluted")
    if result.get("used_ark_fallback"):
        risk_flags.append("ark_fallback_used")
    if items and all(item["is_directory_page"] or item["is_aggregated_page"] for item in items[:3]):
        risk_flags.append("only_aggregated_pages")
    if items and not any(item["has_specific_fact"] for item in items[:3]):
        risk_flags.append("no_specific_fact")

    can_answer = False
    continue_with = "none"
    reason = "未命中足够具体的资料"
    if any(item["is_hifleet_official"] and item["is_specific_page"] and item["has_specific_fact"] for item in items):
        can_answer = True
        reason = "命中 HiFleet 官方具体页面且包含明确事实"
    elif looks_like_authoritative_data_query(query) and any(item["is_authoritative"] and item["is_specific_page"] and item["has_specific_fact"] for item in items):
        can_answer = True
        reason = "命中权威公共页面且包含明确事实"
    elif best_urls:
        continue_with = "agent_browser"
        reason = "已有候选具体页面，但摘要信息不足，建议继续抓取正文"
    elif risk_flags:
        continue_with = "web_search_refine"
        reason = "当前结果存在站点污染或聚合页噪音，建议调整 query 或过滤条件"

    return {
        "request_profile": request_profile,
        "result_profile": {
            "result_count": len(items),
            "official_count": official_count,
            "authoritative_count": authoritative_count,
            "specific_page_count": specific_page_count,
            "directory_page_count": directory_page_count,
            "aggregated_page_count": aggregated_page_count,
            "used_ark_fallback": bool(result.get("used_ark_fallback")),
        },
        "items": items,
        "analysis": {
            "can_answer": can_answer,
            "should_continue": not can_answer,
            "continue_with": continue_with,
            "reason": reason,
            "best_urls": best_urls[:3],
            "risk_flags": risk_flags,
        },
    }


def format_web_result(web_results: dict) -> str:
    parts = ["【公开资料参考】"]
    if web_results.get("summary"):
        parts.append(f"\n综合摘要：{web_results['summary'][:1000]}")
    for item in web_results.get("items", []):
        parts.append(f"\n**{item['title']}** {item['authority_label']}")
        if item["snippet"]:
            parts.append(f"摘要: {item['snippet'][:300]}")
        if item.get("site_name"):
            parts.append(f"站点: {item['site_name']}")
        if item.get("publish_time"):
            parts.append(f"发布时间: {item['publish_time']}")
        if item["url"]:
            parts.append(f"🔗 {item['url']}")
    return "\n".join(parts)
