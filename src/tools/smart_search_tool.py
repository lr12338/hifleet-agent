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
from typing import Optional

logger = logging.getLogger(__name__)

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
    """第4层：Hifleet官网站内搜索"""
    from coze_coding_dev_sdk import SearchClient, Config
    from coze_coding_dev_sdk.fetch import FetchClient

    config = Config()
    search_client = SearchClient(config=config, ctx=ctx)
    fetch_client = FetchClient(config=config, ctx=ctx)

    results = []
    try:
        search_resp = search_client.search(query=f"site:{HIFLEET_SITES} {query}")
        items = list(search_resp.web_items or [])

        for item in items[:3]:
            entry = {"title": getattr(item, 'title', ''), "url": getattr(item, 'url', ''),
                     "snippet": getattr(item, 'snippet', ''), "full_content": ""}

            # 尝试全文抓取
            try:
                url = entry["url"]
                if url and 'hifleet' in url:
                    fetch_resp = fetch_client.fetch(url)
                    if fetch_resp and hasattr(fetch_resp, 'content') and fetch_resp.content:
                        entry["full_content"] = fetch_resp.content[:3000]
            except Exception:
                pass

            results.append(entry)
    except Exception as e:
        logger.warning(f"Hifleet site search error: {e}")

    return results


def _search_web_enhanced(query: str, ctx) -> dict:
    """第5层：增强版网页搜索（全文抓取+权威度+AI摘要）"""
    from coze_coding_dev_sdk import SearchClient, Config
    from coze_coding_dev_sdk.fetch import FetchClient
    from urllib.parse import urlparse

    config = Config()
    search_client = SearchClient(config=config, ctx=ctx)
    fetch_client = FetchClient(config=config, ctx=ctx)

    result = {"items": [], "summary": ""}

    try:
        search_resp = search_client.search(query=query)
        items = list(search_resp.web_items or [])
        if hasattr(search_resp, 'summary') and search_resp.summary:
            result["summary"] = search_resp.summary

        for item in items[:5]:
            url = getattr(item, 'url', '')
            domain = urlparse(url).netloc.lower().lstrip("www.")
            authority = DOMAIN_AUTHORITY.get(domain, 0.3)

            entry = {
                "title": getattr(item, 'title', ''),
                "url": url,
                "snippet": getattr(item, 'snippet', ''),
                "authority": authority,
                "authority_label": _get_authority_label(authority),
                "full_content": "",
            }

            # Top3全文抓取
            if authority >= 0.4 and len([e for e in result["items"] if e["full_content"]]) < 3:
                try:
                    if url:
                        fetch_resp = fetch_client.fetch(url)
                        if fetch_resp and hasattr(fetch_resp, 'content') and fetch_resp.content:
                            entry["full_content"] = fetch_resp.content[:3000]
                except Exception:
                    pass

            result["items"].append(entry)

    except Exception as e:
        logger.warning(f"Web search error: {e}")

    return result


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

    logger.info(f"[smart_search] query='{query}', depth='{depth}'")

    # ── 第1层：术语速查 ──
    glossary_match = _match_glossary(query)
    if glossary_match:
        term, definition = glossary_match
        logger.info(f"[smart_search] Layer1 glossary hit: '{term}'")
        return _format_glossary_result(term, definition)

    # ── 第2-3层：知识库检索 ──
    kb_results = _search_knowledge_base(query, ctx)

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
            return kb_output
        # normal/deep：继续搜官网补充
        site_output = ""
        if depth in ("normal", "deep"):
            site_results = _search_hifleet_site(query, ctx)
            if site_results:
                site_output = "\n\n" + _format_site_result(site_results, query)

        # deep模式：再加互联网搜索
        web_output = ""
        if depth == "deep":
            web_results = _search_web_enhanced(query, ctx)
            if web_results.get("items"):
                web_output = "\n\n" + _format_web_result(web_results)

        return kb_output + site_output + web_output

    # FAQ无高质量匹配，继续逐层
    kb_output = _format_knowledge_result(kb_results) if (faq_items or kb_results.get("wiki")) else ""

    # ── 第4层：官网站内搜索 ──
    site_output = ""
    if depth in ("normal", "deep"):
        site_results = _search_hifleet_site(query, ctx)
        has_site_content = any(item.get("full_content") for item in site_results)

        if site_results:
            site_output = _format_site_result(site_results, query)

        # 有官网内容 + normal模式 → 够用了
        if has_site_content and depth == "normal":
            if kb_output:
                return kb_output + "\n\n" + site_output
            return site_output

    # ── 第5层：互联网搜索 ──
    web_output = ""
    if depth == "deep" or (depth == "normal" and not site_output and not kb_output):
        web_results = _search_web_enhanced(query, ctx)
        if web_results.get("items"):
            web_output = _format_web_result(web_results)

    # 组装最终输出
    final_parts = []
    if kb_output:
        final_parts.append(kb_output)
    if site_output:
        final_parts.append(site_output)
    if web_output:
        final_parts.append(web_output)

    if not final_parts:
        return (
            "未找到相关信息。\n\n"
            "建议：\n"
            "1. 尝试换一种描述方式重新搜索\n"
            "2. 联系人工客服：400-963-6899\n"
            "3. 微信客服：hifleetkhzs\n"
            "4. 访问帮助中心：https://help.hifleet.com"
        )

    return "\n\n".join(final_parts)
