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
HOW_TO_QUERY_MARKERS = ["怎么", "如何", "怎样", "步骤", "教程", "入口", "在哪", "哪里", "绘制", "创建", "设置", "添加", "编辑", "保存", "操作", "使用"]
ISSUE_FEEDBACK_MARKERS = ["不显示", "保存不了", "无法", "失败", "报错", "异常", "找不到", "不能", "没反应", "不触发", "打不开", "加载失败"]
HOW_TO_STEP_MARKERS = ["点击", "选择", "打开", "进入", "填写", "保存", "确定", "右击", "右键", "双击", "拖动", "绘制", "闭合", "添加", "编辑", "设置", "展开"]
GENERIC_PAGE_MARKERS = ["帮助中心", "官方社区", "目录", "列表", "首页", "视频", "演示视频"]
AUTOMOTIVE_FLEET_MARKERS = ("汽车车队", "车辆管理", "司机管理", "车队司机", "网约车", "货车司机", "车辆调度")
CRITICAL_QUERY_PHRASES = (
    "CCTV",
    "GB28181",
    "接入",
    "价格",
    "异常",
    "报错",
    "权限",
    "保存",
    "不显示",
    "不刷新",
    "失败",
)


def looks_like_authoritative_data_query(query: str) -> bool:
    q = normalize_query_text(query)
    return any(marker in q for marker in ["今日", "今天", "最新", "长江水位", "水位", "指数", "行情", "运价"])


def looks_like_hifleet_product_query(query: str) -> bool:
    q = normalize_query_text(query)
    product_markers = ["hifleet", "船队在线", "功能", "产品", "社区", "帮助中心", "筛选", "船队", "视频监控", "岸基", "点验"]
    return any(marker in q for marker in product_markers)


def classify_question_for_evidence(query: str) -> str:
    q = normalize_query_text(query)
    if any(marker in q for marker in ISSUE_FEEDBACK_MARKERS):
        return "issue_feedback"
    if any(marker in q for marker in HOW_TO_QUERY_MARKERS):
        return "how_to_operate"
    if looks_like_authoritative_data_query(query):
        return "authoritative_public_data"
    if looks_like_hifleet_product_query(query):
        return "feature_intro"
    return "general"


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
            ("筛选船队", ["筛选船队", "筛选"]),
            ("记忆功能", ["记忆功能", "记忆"]),
            ("智能视频监控", ["智能视频监控", "视频监控"]),
            ("岸基值班", ["岸基值班", "点验"]),
            ("帮助中心", ["帮助中心"]),
        ]
        for label, markers in grouped_markers:
            if any(marker in q for marker in markers) and label not in parts:
                parts.append(label)
        for phrase in CRITICAL_QUERY_PHRASES:
            if phrase.lower() in q.lower() and phrase not in parts:
                parts.append(phrase)
        if len(parts) > 1:
            return " ".join(parts[:8])
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
        or any(marker in lowered_title for marker in ["帮助中心", "官方社区", "更新日志", "公告", "演示视频"])
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


def has_operation_step_signal(text: str) -> bool:
    lowered = normalize_query_text(text)
    return any(marker in lowered for marker in HOW_TO_STEP_MARKERS)


def operation_evidence_count(text: str) -> int:
    lowered = normalize_query_text(text)
    groups = [
        ["入口", "右上角", "标注", "我的标注", "更多", "区域回放", "页面", "进入", "打开"],
        ["点击", "选择", "拖动", "绘制", "添加", "编辑", "设置", "填写"],
        ["保存", "确定", "完成", "结束", "闭合", "右击", "右键", "双击"],
        ["圆形", "矩形", "多边形", "区域标注", "电子围栏"],
        ["报警", "规则", "通知", "监控对象", "网页提醒", "邮件"],
    ]
    return sum(1 for markers in groups if any(marker in lowered for marker in markers))


def is_generic_page_evidence(title: str, summary: str, url: str = "") -> bool:
    text = normalize_query_text(" ".join([title or "", summary or "", url or ""]))
    return any(marker in text for marker in GENERIC_PAGE_MARKERS)


def is_automotive_fleet_noise(query: str, title: str, summary: str, url: str = "") -> bool:
    if not looks_like_hifleet_product_query(query):
        return False
    text = normalize_query_text(" ".join([title or "", summary or "", url or ""]))
    return any(marker in text for marker in AUTOMOTIVE_FLEET_MARKERS)


def query_term_coverage(query: str, title: str, summary: str, url: str = "") -> float:
    compact_query = re.sub(r"\s+", "", normalize_query_text(query)).replace("hifleet", "")
    compact_haystack = re.sub(r"\s+", "", normalize_query_text(" ".join([title or "", summary or "", url or ""])))
    query_bigrams = {
        compact_query[index:index + 2]
        for index in range(len(compact_query) - 1)
        if re.fullmatch(r"[\u4e00-\u9fff]{2}", compact_query[index:index + 2])
    }
    query_terms = {
        token.lower()
        for token in keyword_tokens(query)
        if len(token) >= 3 and token.lower() != "hifleet"
    }
    matched_bigrams = sum(1 for token in query_bigrams if token in compact_haystack)
    matched_terms = sum(1 for token in query_terms if token in compact_haystack)
    bigram_coverage = matched_bigrams / max(1, len(query_bigrams))
    term_coverage = matched_terms / max(1, len(query_terms)) if query_terms else 0.0
    return round(max(bigram_coverage, term_coverage), 3)


def analyze_web_search_result(query: str, request_profile: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    items = []
    best_urls: list[str] = []
    question_class = classify_question_for_evidence(query)
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
        item_has_step_signal = has_operation_step_signal(" ".join([title, summary]))
        item_operation_evidence_count = operation_evidence_count(" ".join([title, summary]))
        item_is_generic_page = is_generic_page_evidence(title, summary, url)
        item_is_automotive_noise = is_automotive_fleet_noise(query, title, summary, url)
        item_query_coverage = query_term_coverage(query, title, summary, url)
        if item_is_automotive_noise:
            continue
        if looks_like_hifleet_product_query(query) and not is_hifleet_official and item_query_coverage <= 0:
            continue
        if looks_like_hifleet_product_query(query) and is_hifleet_official and item_query_coverage < 0.2:
            continue
        if looks_like_hifleet_product_query(query) and is_hifleet_official and item_query_coverage <= 0 and (page_is_directory or page_is_aggregated or item_is_generic_page):
            continue
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
            "is_generic_page_evidence": item_is_generic_page,
            "has_specific_fact": item_has_specific_fact,
            "has_operation_step_signal": item_has_step_signal,
            "operation_evidence_count": item_operation_evidence_count,
            "authority": resolve_authority_score((urlparse(url).hostname or "").lower(), authority_level),
            "query_term_coverage": item_query_coverage,
        }
        items.append(item)
        if page_is_specific and (is_hifleet_official or is_authoritative) and url and url not in best_urls:
            best_urls.append(url)

    items.sort(
        key=lambda item: (
            not item["is_hifleet_official"],
            not item["is_specific_page"],
            -float(item["query_term_coverage"]),
            -float(item["authority"]),
            -float(item.get("rank_score") or 0),
        )
    )
    best_urls = [
        item["url"]
        for item in items
        if (
            item["is_specific_page"]
            and (item["is_hifleet_official"] or item["is_authoritative"])
            and float(item["query_term_coverage"]) >= 0.5
            and item["url"]
        )
    ][:3]

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
    if question_class in {"how_to_operate", "issue_feedback"}:
        risk_flags.append("needs_step_or_resolution_evidence")
        if items and all(item["is_directory_page"] or item["is_aggregated_page"] or item["is_generic_page_evidence"] for item in items[:3]):
            risk_flags.append("tutorial_generic_pages_only")
        if items and not any(item["has_operation_step_signal"] and int(item["operation_evidence_count"]) >= 2 for item in items[:5]):
            risk_flags.append("insufficient_step_evidence")

    can_answer = False
    continue_with = "none"
    reason = "未命中足够具体的资料"
    if question_class in {"how_to_operate", "issue_feedback"}:
        if any(
            item["is_hifleet_official"]
            and item["is_specific_page"]
            and item["has_operation_step_signal"]
            and int(item["operation_evidence_count"]) >= 2
            and not item["is_directory_page"]
            and not item["is_generic_page_evidence"]
            for item in items
        ):
            can_answer = True
            reason = "命中 HiFleet 官方具体页面且包含步骤/处置证据"
        elif best_urls:
            continue_with = "agent_browser"
            reason = "教程或排障问题需要抓取具体页面正文核验"
        elif risk_flags:
            continue_with = "web_search_refine"
            reason = "当前结果缺少可直接回答的步骤或处置证据"
    elif any(item["is_hifleet_official"] and item["is_specific_page"] and item["has_specific_fact"] for item in items):
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
            "question_class": question_class,
        },
        "items": items,
        "analysis": {
            "can_answer": can_answer,
            "should_continue": not can_answer,
            "continue_with": continue_with,
            "reason": reason,
            "best_urls": best_urls[:3],
            "risk_flags": risk_flags,
            "question_class": question_class,
        },
    }


def format_web_result(web_results: dict) -> str:
    parts = ["【公开资料参考】"]
    if web_results.get("summary"):
        parts.append(f"\n综合摘要：{web_results['summary'][:1000]}")
    for item in web_results.get("items", []):
        authority_label = item.get("authority_label")
        if authority_label is None and item.get("authority") is not None:
            authority_label = get_authority_label(float(item["authority"]))
        if authority_label is None:
            authority_label = "🔴 待验证"
        parts.append(f"\n**{item['title']}** {authority_label}")
        if item.get("snippet"):
            parts.append(f"摘要: {item['snippet'][:300]}")
        if item.get("site_name"):
            parts.append(f"站点: {item['site_name']}")
        if item.get("publish_time"):
            parts.append(f"发布时间: {item['publish_time']}")
        if item.get("url"):
            parts.append(f"🔗 {item['url']}")
    return "\n".join(parts)
