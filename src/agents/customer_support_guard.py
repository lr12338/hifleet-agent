"""Customer-facing output guard for customer_support."""
from __future__ import annotations

import re
from urllib.parse import urlparse

SENSITIVE_REFUSAL = "抱歉，这部分属于系统内部安全信息，不能提供。我可以继续协助您处理 HiFleet 平台使用、船舶查询或业务问题。"

_SENSITIVE_PATTERNS = [
    re.compile(r"\b(?:api[_-]?key|token|secret|password|passwd|hifleet_key\d*)\b", re.IGNORECASE),
    re.compile(r"(?:^|[\s'\"`])/(?:home|root|etc|var|tmp)/(?:[^\s'\"`]+)", re.IGNORECASE),
    re.compile(r"\.env\b", re.IGNORECASE),
    re.compile(r"(?:system prompt|profile prompt|tool registry|工具注册表|提示词|内部路由|源码路径)", re.IGNORECASE),
]

_INTERNAL_TOOL_NAMES = [
    "smart_search",
    "agent_browser_deep_search",
    "verify_public_page",
    "ship_search",
    "get_ship_position",
    "get_ship_archive",
    "get_ship_trajectory",
    "get_ship_call_ports",
    "upload_ship_position",
    "update_ship_static_info",
    "run_sandboxed_python",
    "inspect_tabular_file",
    "download_public_file_to_artifact",
]


def contains_sensitive_output(text: str) -> bool:
    value = text or ""
    return any(pattern.search(value) for pattern in _SENSITIVE_PATTERNS)


def sanitize_customer_output(text: str) -> str:
    value = (text or "").strip()
    if not value:
        return value
    if contains_sensitive_output(value):
        return SENSITIVE_REFUSAL
    value = re.sub(r"【互联网搜索结果（增强版）】", "", value)
    value = re.sub(r"【互联网搜索结果】", "", value)
    value = re.sub(r"【公开网页深度检索】", "", value)
    value = re.sub(r"【Hifleet官方站内搜索】", "", value)
    value = re.sub(r"【HiFleet 官方资料】", "", value)
    value = re.sub(r"【回答指导】", "", value)
    value = re.sub(r"(?mi)^综合摘要[:：]?\s*", "", value)
    value = re.sub(r"(?mi)^查询\d+[（(].*?[）)][:：]?\s*", "", value)
    value = re.sub(r"(?mi)^问题[:：].*$", "", value)
    value = re.sub(r"(?mi)^📋\s*\**AI摘要\**[:：]\s*", "", value)
    value = re.sub(r"(?mi)^\[Query\d+:.*$", "", value)
    value = re.sub(r"\[HTMLLINK_\d+\]", "", value)
    value = re.sub(r"(?mi)^#{1,6}\s*", "", value)
    value = re.sub(r"(?mi)^来源[:：]\s*$", "", value)
    value = re.sub(r"(?mi)^内容摘要[:：]\s*", "", value)
    value = re.sub(r"(?mi)^详细内容[:：]\s*", "", value)
    value = re.sub(r"(?mis)<a\s+href=\"[^\"]+\">.*?</a>", "", value)
    value = re.sub(r"(?mi)^如需更多帮助，请继续补充.*$", "", value)
    value = re.sub(r"(?mi)^请用中文回复。?\s*$", "", value)
    value = re.sub(r"(?mi)^下载APP,?手机查船更方便.*$", "", value)
    value = re.sub(r"(?mi)^.*手机查船更方便.*$", "", value)
    value = re.sub(r"(?mi)^.*服务电话:400-963-6899.*$", "", value)
    value = re.sub(r"(?mi)^.*微信:hifleetkhzs.*$", "", value)
    value = re.sub(r"(?mi)^.*微信客服：?hifleetkhzs请用中文回复。?.*$", "微信客服：hifleetkhzs", value)
    for name in _INTERNAL_TOOL_NAMES:
        value = re.sub(re.escape(name), "内部分析", value, flags=re.IGNORECASE)
    value = re.sub(r"SMART_SEARCH_[A-Z0-9_]+", "官方知识检索命中", value)
    value = re.sub(r"\n[ \t]+\n", "\n\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value).strip()
    return value


def validate_customer_links(text: str, allowed_domains: set[str] | None = None) -> tuple[bool, list[str]]:
    domains = allowed_domains or set()
    urls = [u.rstrip(".,;!?，。；！？）】》") for u in re.findall(r"https?://[^\s)）\]】>\"']+", text or "")]
    invalid: list[str] = []
    for url in urls:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            invalid.append(url)
            continue
        if domains:
            host = parsed.netloc.lower()
            if not any(host == d or host.endswith("." + d) for d in domains):
                invalid.append(url)
    return not invalid, invalid
