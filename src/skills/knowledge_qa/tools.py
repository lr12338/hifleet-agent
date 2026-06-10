"""
统一智能搜索工具 — 单一入口，5层搜索策略

架构设计：
  对外：1个 @tool（smart_search）
  对内：5层搜索策略自动路由，无需LLM决策

搜索链路：
  第1层: 术语速查（毫秒级，100%准确）
  第2层: 知识库FAQ（优先级最高，官方标准答案）
  第3层: 知识库Wiki（补充背景知识）
  第4层: 官网站内搜索（Hifleet官方内容）
  第5层: 网页搜索+全文抓取（互联网信息）

终止条件：
  - 第1层命中 → 直接返回
  - 第2层找到高相关FAQ → 返回FAQ + 第3层补充
  - 第4层找到官网内容 → 返回官网 + 第5层补充（仅depth=deep时）
  - 第5层兜底 → 增强搜索（全文+权威度+AI摘要）

depth参数控制搜索深度：
  - "quick":  第1-3层（知识库内，快速）
  - "normal": 第1-4层（+官网，默认）
  - "deep":   第1-5层（+互联网深度搜索，最全）
"""
from langchain.tools import tool
from coze_coding_utils.log.write_log import request_context
from coze_coding_utils.runtime_ctx.context import new_context
import logging
import os
import re
import time
from typing import Optional
from urllib.parse import urlparse
import requests
import threading

from openai import OpenAI
from skills.common.tool_result import ToolResult, emit_tool_metric

logger = logging.getLogger(__name__)
DEFAULT_HELP_CENTER_URL = "https://www.hifleet.com/helpcenter/?i18n=zh"

# 性能优化参数（可通过环境变量覆盖）
SMART_SEARCH_CACHE_TTL_SEC = int(os.getenv("SMART_SEARCH_CACHE_TTL_SEC", "600"))
URL_CHECK_TIMEOUT_SEC = float(os.getenv("SMART_SEARCH_URL_TIMEOUT_SEC", "2.0"))
URL_CHECK_TOP_N = int(os.getenv("SMART_SEARCH_URL_TOP_N", "2"))
DEEP_VARIANTS_MAX = int(os.getenv("SMART_SEARCH_DEEP_VARIANTS_MAX", "3"))

_SEARCH_CACHE_LOCK = threading.Lock()
_SEARCH_CACHE: dict = {}


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
    # 常见故障咨询聚类，提升缓存命中
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

# ══════════════════════════════════════════════════
# 第1层：平台术语速查表
# ══════════════════════════════════════════════════
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

# ══════════════════════════════════════════════════
# 第2-3层：知识库检索配置
# ══════════════════════════════════════════════════
OUTPUTS_DATASET = "hifleet_cs_outputs_v2"
WIKI_DATASET = "hifleet_cs_wiki_v2"
OUTPUTS_TOP_K = 5
OUTPUTS_MIN_SCORE = 0.30
WIKI_TOP_K = 3
WIKI_MIN_SCORE = 0.30

# ══════════════════════════════════════════════════
# 第4层：官网站内搜索配置
# ══════════════════════════════════════════════════
HIFLEET_SITES = "hifleet.com,help.hifleet.com,www.hifleet.com"

# ══════════════════════════════════════════════════
# 第5层：域名权威度加权表
# ══════════════════════════════════════════════════
DOMAIN_AUTHORITY = {
    "hifleet.com": 1.0, "help.hifleet.com": 1.0,
    "msa.gov.cn": 0.95, "mot.gov.cn": 0.90,
    "imo.org": 0.95,
    "xindemarinenews.com": 0.85,
    "schinese.shippingazette.com": 0.80,
    "worldmaritimenews.com": 0.80,
    "seatrade-maritime.com": 0.75,
    "baike.baidu.com": 0.50,
    "zhihu.com": 0.40,
    "wikipedia.org": 0.60,
}

# 弱命中/无命中时的查询扩展词，提升“平台问题”场景检索召回
QUERY_EXPANSION_HINTS = {
    "船位更新慢": [
        "AIS 数据延迟 原因",
        "船位刷新慢 原因 处理",
        "hifleet 船位 更新 延迟",
    ],
    "报警系统": [
        "hifleet 报警系统 告警 说明",
        "航运 告警 系统 配置 常见问题",
        "ais 告警 误报 漏报 处理",
    ],
}

TROUBLESHOOTING_FASTPATH_MARKERS = [
    "无反应", "更新慢", "加载失败", "不显示", "不刷新", "异常", "故障", "报错", "报警",
]

# ══════════════════════════════════════════════════
# 辅助函数
# ══════════════════════════════════════════════════

def _match_glossary(query: str) -> Optional[str]:
    """第1层：术语速查表匹配"""
    query_lower = query.lower().strip()
    for term, definition in PLATFORM_GLOSSARY.items():
        if term in query_lower:
            return term, definition
    return None


def _should_use_helpcenter_fastpath(query: str) -> bool:
    q = (query or "").strip()
    return any(marker in q for marker in TROUBLESHOOTING_FASTPATH_MARKERS)


def _search_knowledge_base(query: str, ctx) -> dict:
    """第2-3层：知识库检索（FAQ优先+Wiki补充）"""
    from coze_coding_dev_sdk import KnowledgeClient, Config

    config = Config()
    client = KnowledgeClient(config=config, ctx=ctx)

    results = {"faq": [], "wiki": []}

    # 第2层：FAQ/标准回复
    try:
        outputs_resp = client.search(
            query=query,
            table_names=[OUTPUTS_DATASET],
            top_k=OUTPUTS_TOP_K,
            min_score=OUTPUTS_MIN_SCORE,
        )
        if outputs_resp and outputs_resp.chunks:
            for chunk in outputs_resp.chunks:
                source_type = _detect_source_type(chunk.content)
                results["faq"].append({
                    "score": chunk.score,
                    "content": chunk.content,
                    "source_type": source_type,
                })
    except Exception as e:
        logger.warning(f"FAQ search error: {e}")

    # 第3层：Wiki补充
    try:
        wiki_resp = client.search(
            query=query,
            table_names=[WIKI_DATASET],
            top_k=WIKI_TOP_K,
            min_score=WIKI_MIN_SCORE,
        )
        if wiki_resp and wiki_resp.chunks:
            for chunk in wiki_resp.chunks:
                results["wiki"].append({
                    "score": chunk.score,
                    "content": chunk.content,
                })
    except Exception as e:
        logger.warning(f"Wiki search error: {e}")

    return results


def _detect_source_type(content: str) -> str:
    """检测知识库条目的来源类型"""
    faq_markers = ["【关键词】", "【问题】", "【答案】", "【分类】", "【转人工场景】"]
    wiki_markers = ["## ", "# ", "=== ", "---"]

    faq_count = sum(1 for m in faq_markers if m in content)
    wiki_count = sum(1 for m in wiki_markers if m in content)

    if faq_count >= 2:
        return "faq"
    elif wiki_count >= 1:
        return "wiki"
    return "unknown"


def _search_hifleet_site(query: str, ctx) -> list:
    """第4层：Hifleet官网站内搜索（火山联网搜索）"""
    if _should_use_helpcenter_fastpath(query):
        return [{
            "title": "HiFleet 帮助中心",
            "url": DEFAULT_HELP_CENTER_URL,
            "snippet": "官方平台使用与问题排查文档入口",
            "full_content": "",
            "content_quality": "official_fastpath",
        }]

    results = []
    try:
        search = _ark_web_search(
            query=query,
            site_hint=HIFLEET_SITES,
            count=3,
        )
        for item in search.get("items", []):
            url = item.get("url", "")
            if "hifleet" not in url:
                continue
            results.append({
                "title": item.get("title", ""),
                "url": url,
                "snippet": _sanitize_snippet_text(item.get("snippet", "")),
                "full_content": "",
                "content_quality": "snippet" if item.get("snippet") else "link_only",
            })
        results = _filter_accessible_items(results, require_hifleet_domain=True)
        if not results:
            results = [{
                "title": "HiFleet 帮助中心",
                "url": DEFAULT_HELP_CENTER_URL,
                "snippet": "官方平台使用与问题排查文档入口",
                "full_content": "",
                "content_quality": "official_fallback",
            }]
    except Exception as e:
        logger.warning(f"Hifleet site search error: {e}")

    return results


def _search_web_enhanced(query: str, ctx) -> dict:
    """第5层：增强版网页搜索（火山联网搜索）"""
    result = {"items": [], "summary": ""}

    try:
        search = _ark_web_search(query=query, count=5)
        result["summary"] = search.get("summary", "")

        for item in search.get("items", []):
            url = item.get("url", "")
            domain = urlparse(url).netloc.lower().lstrip("www.")
            authority = DOMAIN_AUTHORITY.get(domain, 0.3)
            result["items"].append({
                "title": item.get("title", ""),
                "url": url,
                "snippet": _sanitize_snippet_text(item.get("snippet", "")),
                "authority": authority,
                "authority_label": _get_authority_label(authority),
                "full_content": "",
            })
        result["items"] = _filter_accessible_items(result["items"], require_hifleet_domain=False)
        if not result["items"]:
            result["items"] = [{
                "title": "HiFleet 帮助中心",
                "url": DEFAULT_HELP_CENTER_URL,
                "snippet": "官方平台使用与问题排查文档入口",
                "authority": 1.0,
                "authority_label": "🟢 权威",
                "full_content": "",
            }]
    except Exception as e:
        logger.warning(f"Web search error: {e}")

    return result


def _expand_query_variants(query: str) -> list:
    variants = [query]
    q = query.strip()
    for key, extra in QUERY_EXPANSION_HINTS.items():
        if key in q:
            variants.extend(extra)
    # 通用扩展：平台故障/异常类问题补上排障检索词
    troubleshooting_markers = ["异常", "失败", "慢", "延迟", "告警", "报警", "无法", "不显示", "不刷新"]
    if any(m in q for m in troubleshooting_markers):
        variants.extend([
            f"{q} 原因",
            f"{q} 解决办法",
            f"Hifleet {q} 常见问题",
        ])
    # 去重
    dedup = []
    for v in variants:
        vv = v.strip()
        if vv and vv not in dedup:
            dedup.append(vv)
    return dedup[:max(1, DEEP_VARIANTS_MAX)]


def _search_web_deep_multi(query: str, ctx) -> dict:
    """
    深度检索：多查询词并行思路（串行执行），合并结果并按权威度排序。
    """
    merged = {"items": [], "summary": ""}
    variants = _expand_query_variants(query)
    for i, q in enumerate(variants):
        chunk = _search_web_enhanced(q, ctx)
        if chunk.get("summary"):
            merged["summary"] += f"\n[Query{i+1}:{q}] {chunk['summary'][:500]}"
        merged["items"].extend(chunk.get("items", []))

    # 去重 URL + 权威度排序
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
    return merged


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
            # 统一帮助中心链接，避免返回不可访问或历史路径
            if "help.hifleet.com" in url:
                item["url"] = DEFAULT_HELP_CENTER_URL
            filtered.append(item)
    return filtered


def _sanitize_snippet_text(text: str) -> str:
    if not text:
        return ""
    # 去掉摘要中的URL，避免模型误引用未校验链接
    return re.sub(r"https?://[^\s)]+", "", text).strip()


def _get_env_value(*keys: str) -> str:
    for key in keys:
        v = os.getenv(key)
        if v and v.strip():
            return v.strip()
    return ""


def _ark_web_search(query: str, site_hint: str = "", count: int = 5) -> dict:
    """
    使用火山 Ark 的 web_search_options 做联网搜索。
    兼容两类 key：
    - ARK_WEBSEARCH_API_KEY / ark_websearch_api_key（若是 ark- 格式）
    - COZE_WORKLOAD_IDENTITY_API_KEY（回退）
    """
    api_key = _get_env_value("ARK_WEBSEARCH_API_KEY", "ark_websearch_api_key")
    if not api_key.startswith("ark-"):
        api_key = _get_env_value("COZE_WORKLOAD_IDENTITY_API_KEY")

    if not api_key:
        raise RuntimeError("未配置可用的 Ark API Key")

    base_url = _get_env_value("COZE_INTEGRATION_MODEL_BASE_URL")
    if not base_url:
        raise RuntimeError("未配置 COZE_INTEGRATION_MODEL_BASE_URL")

    model = _get_env_value("ARK_WEBSEARCH_MODEL")
    if not model:
        model = "doubao-seed-2-0-lite-260428"

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

    # 从回答文本中提取链接，组装为结构化条目
    urls = re.findall(r"https?://[^\s)]+", content)
    uniq_urls = []
    for u in urls:
        if u not in uniq_urls:
            uniq_urls.append(u)

    items = []
    for i, u in enumerate(uniq_urls[:count], start=1):
        items.append({
            "title": f"联网搜索结果{i}",
            "url": u,
            "snippet": content[:500],
        })
    if not items and content:
        items.append({
            "title": "火山联网搜索摘要",
            "url": "",
            "snippet": content[:500],
        })

    return {
        "summary": content[:1500],
        "items": items,
    }


def _get_authority_label(score: float) -> str:
    if score >= 0.9:
        return "🟢 权威"
    elif score >= 0.7:
        return "🟡 可信"
    elif score >= 0.5:
        return "🟠 一般"
    return "🔴 待验证"


def _format_glossary_result(term: str, definition: str) -> str:
    """格式化术语速查结果"""
    return (
        f"从平台术语速查表中匹配到以下标准解释：\n\n"
        f"【术语：{term}】\n"
        f"{definition}\n\n"
        f"【回答指导】\n"
        f"- 这是官方标准解释，请直接使用上述定义回答用户，禁止猜测或编造其他解释。"
    )


def _format_knowledge_result(kb_results: dict) -> str:
    """格式化知识库检索结果"""
    parts = []
    faq_items = kb_results.get("faq", [])
    wiki_items = kb_results.get("wiki", [])

    has_faq = any(item["source_type"] == "faq" and item["score"] >= 0.40 for item in faq_items)

    if has_faq:
        parts.append("【优先匹配 - FAQ/标准回复】")
        for item in faq_items:
            if item["source_type"] == "faq" and item["score"] >= 0.40:
                parts.append(f"\n**相关度: {item['score']:.2f}**\n{item['content']}")
    else:
        # 无精确FAQ，展示最高分结果
        if faq_items:
            top = faq_items[0]
            if top["score"] >= 0.35:
                parts.append("【可能相关 - 标准回复（相关度较低）】")
                parts.append(f"\n**相关度: {top['score']:.2f}**\n{top['content']}")

    # Wiki补充
    if wiki_items:
        parts.append("\n【主题说明（补充参考）】")
        for item in wiki_items[:2]:
            parts.append(f"\n**相关度: {item['score']:.2f}**\n{item['content'][:500]}")

    # 回答指导
    if has_faq:
        parts.append("\n---\n【回答指导】\n- 找到精确FAQ匹配，优先使用标准答案回复。")
    else:
        parts.append(
            "\n---\n【回答指导】\n"
            "- 未找到精确的FAQ匹配，请基于主题说明谨慎回答，避免编造信息。\n"
            "- 如需更全面的信息，可调用smart_search(depth='deep')进行深度搜索。"
        )

    return "\n".join(parts)


def _format_site_result(site_results: list, query: str) -> str:
    """格式化站内搜索结果"""
    if not site_results:
        return ""

    parts = [f"【Hifleet官方站内搜索】"]
    for item in site_results:
        parts.append(f"\n**{item['title']}**")
        if item["full_content"]:
            parts.append(f"内容摘要：{item['full_content'][:800]}...")
        elif item["snippet"]:
            parts.append(f"摘要：{item['snippet']}")
        parts.append(f"🔗 {item['url']}")

    if DEFAULT_HELP_CENTER_URL not in [str(i.get("url", "")) for i in site_results]:
        parts.append(f"\n🔗 官方帮助中心入口：{DEFAULT_HELP_CENTER_URL}")

    parts.append("\n---\n【回答指导】\n- 以上来自Hifleet官方网站，可直接引用。")
    return "\n".join(parts)


def _format_web_result(web_results: dict) -> str:
    """格式化网页搜索结果"""
    parts = ["【互联网搜索结果（增强版）】"]

    if web_results.get("summary"):
        parts.append(f"\n📋 **AI摘要**：{web_results['summary'][:1000]}")

    for item in web_results.get("items", []):
        parts.append(f"\n**{item['title']}** {item['authority_label']}")
        if item["snippet"]:
            parts.append(f"摘要: {item['snippet'][:300]}")
        if item["full_content"]:
            parts.append(f"详细内容: {item['full_content'][:800]}...")
        if item["url"]:
            parts.append(f"🔗 {item['url']}")

    parts.append(
        "\n---\n【回答指导】\n"
        "- 🟢权威来源可直接引用，🟡可信来源需交叉验证，🟠一般来源仅供参考，🔴待验证来源需谨慎。\n"
        "- 综合多个来源回答，标注信息来源。"
    )
    return "\n".join(parts)


# ══════════════════════════════════════════════════
# 统一搜索入口
# ══════════════════════════════════════════════════

@tool
def smart_search(query: str, depth: str = "normal") -> str:
    """
    智能搜索：统一知识库+官网+互联网搜索入口，自动5层路由。

    搜索策略（自动执行，无需手动选择）：
    - 第1层: 术语速查（平台概念100%准确）
    - 第2层: 知识库FAQ（官方标准答案）
    - 第3层: 知识库Wiki（补充背景知识）
    - 第4层: 官网站内搜索（Hifleet官方内容）
    - 第5层: 互联网增强搜索（全文+权威度+AI摘要）

    depth参数说明：
    - "quick":  仅搜索知识库（第1-3层），速度快，适合简单问题
    - "normal": 知识库+官网（第1-4层），默认，适合大多数场景
    - "deep":   全部5层搜索+全文抓取，适合复杂分析、行业研究、实时资讯

    适用场景：
    - Hifleet平台使用问题 → depth="quick"或"normal"
    - 航运行业知识/政策法规 → depth="normal"
    - 最新运价/市场动态/深度分析 → depth="deep"

    Args:
        query: 搜索关键词或问题
        depth: 搜索深度 - "quick"/"normal"/"deep"，默认"normal"
    """
    ctx = request_context.get() or new_context(method="smart_search")
    depth = depth.lower().strip()
    if depth not in ("quick", "normal", "deep"):
        depth = "normal"

    cached = _cache_get(query, depth)
    if cached:
        logger.info(f"[smart_search] cache hit: query='{query}', depth='{depth}'")
        return cached

    logger.info(f"[smart_search] query='{query}', depth='{depth}'")
    t0 = time.time()
    layer_trace = []

    # ── 第1层：术语速查 ──
    glossary_match = _match_glossary(query)
    if glossary_match:
        term, definition = glossary_match
        logger.info(f"[smart_search] Layer1 glossary hit: '{term}'")
        output = _format_glossary_result(term, definition)
        layer_trace.append({"layer": "L1", "hit": True, "reason": "glossary"})
        logger.info(f"[smart_search] layer_trace={layer_trace}")
        _emit_search_metric(
            ctx,
            ToolResult(status="ok", code="SMART_SEARCH_L1_HIT", message=output, latency_ms=int((time.time() - t0) * 1000), source="glossary", data={"layer_trace": layer_trace}),
        )
        _cache_set(query, depth, output)
        return output

    # ── 第2-3层：知识库检索 ──
    kb_results = _search_knowledge_base(query, ctx)
    layer_trace.append({"layer": "L2-L3", "hit": bool(kb_results.get("faq") or kb_results.get("wiki"))})

    # 检查FAQ是否有高质量匹配
    faq_items = kb_results.get("faq", [])
    has_high_quality_faq = any(
        item["source_type"] == "faq" and item["score"] >= 0.45
        for item in faq_items
    )

    if has_high_quality_faq:
        kb_output = _format_knowledge_result(kb_results)
        # quick模式：只返回知识库结果
        if depth == "quick":
            logger.info(f"[smart_search] layer_trace={layer_trace}")
            _emit_search_metric(
                ctx,
                ToolResult(status="ok", code="SMART_SEARCH_KB_QUICK", message=kb_output, latency_ms=int((time.time() - t0) * 1000), source="knowledge_base", data={"layer_trace": layer_trace}),
            )
            _cache_set(query, depth, kb_output)
            return kb_output
        # normal/deep：继续搜官网补充
        site_output = ""
        if depth in ("normal", "deep"):
            site_results = _search_hifleet_site(query, ctx)
            if site_results:
                site_output = "\n\n" + _format_site_result(site_results, query)
                layer_trace.append({"layer": "L4", "hit": True, "count": len(site_results)})

        # deep模式：再加互联网搜索
        web_output = ""
        if depth == "deep":
            web_results = _search_web_enhanced(query, ctx)
            if web_results.get("items") or web_results.get("summary"):
                web_output = "\n\n" + _format_web_result(web_results)
                layer_trace.append({"layer": "L5", "hit": True, "count": len(web_results.get("items", []))})

        output = kb_output + site_output + web_output
        logger.info(f"[smart_search] layer_trace={layer_trace}")
        _emit_search_metric(
            ctx,
            ToolResult(status="ok", code="SMART_SEARCH_KB_PRIORITY", message=output, latency_ms=int((time.time() - t0) * 1000), source="knowledge_base", data={"layer_trace": layer_trace}),
        )
        _cache_set(query, depth, output)
        return output

    # FAQ无高质量匹配，继续逐层（并在 normal 模式积极触发深搜补强）
    kb_output = _format_knowledge_result(kb_results) if (faq_items or kb_results.get("wiki")) else ""
    kb_top_score = max([item.get("score", 0.0) for item in faq_items], default=0.0)

    # ── 第4层：官网站内搜索 ──
    site_output = ""
    if depth in ("normal", "deep"):
        site_results = _search_hifleet_site(query, ctx)
        has_site_content = any((item.get("full_content") or item.get("snippet")) for item in site_results)

        if site_results:
            site_output = _format_site_result(site_results, query)
            layer_trace.append({"layer": "L4", "hit": True, "count": len(site_results)})

        # 有官网内容 + normal模式 → 够用了
        if has_site_content and depth == "normal":
            if kb_output:
                return kb_output + "\n\n" + site_output
            logger.info(f"[smart_search] layer_trace={layer_trace}")
            _emit_search_metric(
                ctx,
                ToolResult(status="ok", code="SMART_SEARCH_SITE_SHORTCUT", message=site_output, latency_ms=int((time.time() - t0) * 1000), source="hifleet_site", data={"layer_trace": layer_trace}),
            )
            _cache_set(query, depth, site_output)
            return site_output

    # ── 第5层：互联网搜索 ──
    web_output = ""
    # 性能优先：normal 模式仅在“站内和知识库都弱”时触发深搜
    should_force_deep = depth == "deep" or (depth == "normal" and (not site_output and not kb_output and kb_top_score < 0.40))
    if should_force_deep:
        web_results = _search_web_deep_multi(query, ctx)
        if web_results.get("items") or web_results.get("summary"):
            web_output = _format_web_result(web_results)
            layer_trace.append({"layer": "L5", "hit": True, "count": len(web_results.get("items", []))})

    # 组装最终输出
    final_parts = []
    if kb_output:
        final_parts.append(kb_output)
    if site_output:
        final_parts.append(site_output)
    if web_output:
        final_parts.append(web_output)

    if not final_parts:
        # 最终兜底：再尝试一次深搜，尽量不给“空回复”
        web_results = _search_web_deep_multi(query, ctx)
        if web_results.get("items") or web_results.get("summary"):
            output = _format_web_result(web_results)
            layer_trace.append({"layer": "L5", "hit": True, "reason": "final_fallback"})
            logger.info(f"[smart_search] layer_trace={layer_trace}")
            _emit_search_metric(
                ctx,
                ToolResult(status="ok", code="SMART_SEARCH_WEB_FALLBACK", message=output, latency_ms=int((time.time() - t0) * 1000), source="web_search", data={"layer_trace": layer_trace}),
            )
            _cache_set(query, depth, output)
            return output

        output = (
            "抱歉，当前未检索到足够可信的公开信息。\n\n"
            "建议：\n"
            "1. 补充更具体信息（船名/MMSI/发生时间/异常现象）以便继续排查\n"
            "2. 联系人工客服：400-963-6899（微信：hifleetkhzs）\n"
            f"3. 访问帮助中心：{DEFAULT_HELP_CENTER_URL}"
        )
        logger.info(f"[smart_search] layer_trace={layer_trace}")
        _emit_search_metric(
            ctx,
            ToolResult(status="error", code="SMART_SEARCH_EMPTY", message=output, retriable=False, latency_ms=int((time.time() - t0) * 1000), source="search", data={"layer_trace": layer_trace}),
        )
        _cache_set(query, depth, output)
        return output

    output = "\n\n".join(final_parts)
    logger.info(f"[smart_search] layer_trace={layer_trace}")
    _emit_search_metric(
        ctx,
        ToolResult(status="ok", code="SMART_SEARCH_OK", message=output, latency_ms=int((time.time() - t0) * 1000), source="search", data={"layer_trace": layer_trace}),
    )
    _cache_set(query, depth, output)
    return output
