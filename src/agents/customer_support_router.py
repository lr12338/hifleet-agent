"""Customer support routing and execution harness.

This module keeps the customer_support path fast by doing deterministic
classification before any LLM/tool-agent execution. The LLM still exists as a
fallback, but ordinary support and ship queries first receive a narrowed tool
bundle and an explicit execution plan.
"""
from __future__ import annotations

import logging
import json
import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable
from urllib.parse import urlparse

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage

logger = logging.getLogger(__name__)

HELP_CENTER_URL = "https://www.hifleet.com/helpcenter/?i18n=zh"
HIFLEET_ACCOUNT_PAGE_HINT = "可在【关于】→【账号】里查看当前账号权限范围。"

TaskType = str
Route = str

KNOWLEDGE_BUNDLE = ["smart_search", "agent_browser_deep_search"]
MULTIMODAL_BUNDLE = ["inspect_media_attachment", "smart_search"]
FILE_BUNDLE = ["inspect_customer_file", "upload_customer_artifact"]
BROWSER_VERIFY_BUNDLE = ["verify_public_page", "smart_search", "agent_browser_deep_search"]
SHIP_QUERY_BUNDLE = ["ship_search", "get_ship_position", "get_ship_archive", "get_psc_records"]
SHIP_STATS_BUNDLE = [
    "get_area_traffic",
    "get_strait_traffic",
    "get_avoid_redsea_traffic",
    "search_ports",
    "get_port_detail",
]
SHIP_VOYAGE_BUNDLE = [
    "ship_search",
    "get_ship_position",
    "get_ship_archive",
    "get_ship_trajectory",
    "get_ship_call_ports",
    "get_ship_voyages",
    "get_last_departure",
    "get_current_stop",
]
SHIP_UPDATE_BUNDLE = ["ship_search", "upload_ship_position", "update_ship_static_info"]
BROWSER_FALLBACK_BUNDLE = ["smart_search", "agent_browser_deep_search"]

HIGH_COST_CAPABILITIES_BY_TASK = {
    "platform_knowledge": [],
    "platform_troubleshooting": [],
    "ship_single_query": [],
    "ship_multi_step_analysis": [],
    "ship_stats": [],
    "ship_update": [],
    "unsupported": [],
}

HARNESSED_ROUTES = {"ship_single", "ship_complex", "ship_context", "ship_stats", "ship_update", "file_task", "browser_verify"}
PLANNER_DIRECT_ROUTES = {"knowledge", "chart_symbol", "multimodal_understanding", "conversation"}

GENERIC_SHIP_NAME_STOPWORDS = {
    "查询",
    "查",
    "船舶",
    "当前",
    "最近",
    "历史",
    "某船",
    "该船",
    "此船",
    "这艘船",
    "这个船",
    "hifleet",
    "平台",
    "已按复杂船舶问题链路完成查询与校验。",
}

HIFLEET_CONTEXT_CLEAR_MARKERS = {"清理上下文", "清空上下文", "重置上下文", "清除上下文", "清空会话", "重置会话"}
HIFLEET_ACCOUNT_MARKERS = {"免费版", "基础版", "专业版", "账号", "权限", "会员"}
HIFLEET_FEATURE_MARKERS = {
    "hifleet",
    "船队在线",
    "平台",
    "海图",
    "全球海图",
    "中国海图",
    "avcs",
    "航线",
    "气象",
    "气象预报",
    "气象海况",
    "历史轨迹",
    "轨迹气象",
    "岸基值班",
    "船舶点验",
    "绿点",
    "账号",
}
HIFLEET_HOWTO_MARKERS = {"如何", "怎么", "怎样", "为什么", "步骤", "入口", "查询"}
HIFLEET_PERMISSION_QUERY_MARKERS = {"历史轨迹", "轨迹", "气象预报", "气象导航", "海图", "权限", "能看多久", "几天", "多久前", "可查", "多少天"}
GENERIC_CONTEXT_TOKENS = {"如何", "怎么", "怎样", "为什么", "步骤", "查询"}

# 船位概念类词汇（非指定船舶查询，而是平台功能问题）
SHIP_CONCEPT_TROUBLESHOOTING_PATTERNS = [
    ("船位", ["慢", "延迟", "不刷新", "不更新", "不准", "为什么", "沟通", "频率"]),
    ("ais", ["慢", "延迟", "不刷新", "不更新", "不准", "为什么", "接收", "信号"]),
    ("轨迹", ["不显示", "不刷新", "看不了", "无反应", "加载"]),
]


@dataclass
class MessageEntities:
    urls: list[str] = field(default_factory=list)
    imo: str = ""
    mmsi: str = ""
    ship_name: str = ""
    port: str = ""
    area: str = ""
    strait: str = ""
    bbox: str = ""
    start_date: str = ""
    end_date: str = ""


@dataclass
class Attachment:
    type: str
    url: str
    filename: str = ""


@dataclass
class RouteDecision:
    route: Route
    task_type: TaskType
    tool_bundle: list[str]
    complexity: str
    search_depth: str = "quick"
    fallback_allowed: bool = True
    high_cost_capabilities: list[str] = field(default_factory=list)
    reason: str = ""


@dataclass
class HarnessTrace:
    run_id: str
    session_id: str = ""
    route: str = ""
    task_type: str = ""
    tool_bundle: list[str] = field(default_factory=list)
    entity_resolution: dict[str, Any] = field(default_factory=dict)
    tool_call_sequence: list[str] = field(default_factory=list)
    loop_count: int = 0
    check_result: dict[str, Any] = field(default_factory=dict)
    fallback_reason: str = ""
    latency_hotspot: dict[str, int] = field(default_factory=dict)
    answer_confidence: str = "low"


@dataclass
class ConversationContext:
    latest_user_text: str = ""
    previous_user_text: str = ""
    recent_user_questions: list[str] = field(default_factory=list)
    compressed_recent_user_questions: list[str] = field(default_factory=list)
    relevant_recent_user_questions: list[str] = field(default_factory=list)
    context_summary: str = ""
    last_ship_name: str = ""
    last_ship_mmsi: str = ""
    last_ship_imo: str = ""
    last_ship_source: str = ""


def extract_attachments(messages: list[AnyMessage]) -> list[Attachment]:
    attachments: list[Attachment] = []

    def add_from_content(content: Any) -> None:
        if not isinstance(content, list):
            return
        for item in content:
            if not isinstance(item, dict):
                continue
            item_type = str(item.get("type", "")).strip().lower()
            url = ""
            if item_type == "image_url":
                url = str((item.get("image_url") or {}).get("url", "")).strip()
                media_type = "image"
            elif item_type == "video_url":
                url = str((item.get("video_url") or {}).get("url", "")).strip()
                media_type = "video"
            elif item_type == "input_audio":
                url = str((item.get("input_audio") or {}).get("url", "")).strip()
                media_type = "audio"
            elif item_type == "file_url":
                url = str((item.get("file_url") or {}).get("url", "")).strip()
                media_type = "file"
            else:
                continue
            if url:
                filename = url.split("?", 1)[0].rstrip("/").rsplit("/", 1)[-1] or "attachment"
                attachments.append(Attachment(type=media_type, url=url, filename=filename))

    for msg in messages:
        if isinstance(msg, HumanMessage):
            add_from_content(msg.content)
        elif isinstance(msg, dict) and str(msg.get("role", "")).lower() == "user":
            add_from_content(msg.get("content"))
    return attachments


def normalize_message_text(text: str) -> str:
    text = (text or "").replace("\u200b", "").replace("\ufeff", "")
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return normalize_message_text(content)
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                value = str(item.get("text", "")).strip()
                if value:
                    parts.append(value)
        return normalize_message_text("\n".join(parts))
    return normalize_message_text(str(content or ""))


def latest_user_text(messages: list[AnyMessage]) -> str:
    for msg in reversed(messages):
        if isinstance(msg, HumanMessage):
            return content_to_text(msg.content)
        if isinstance(msg, dict) and str(msg.get("role", "")).lower() == "user":
            return content_to_text(msg.get("content", ""))
    return ""


def _first_non_empty_line(text: str) -> str:
    for line in (text or "").splitlines():
        candidate = line.strip()
        if candidate:
            return candidate
    return ""


def _clean_ship_name_candidate(value: str) -> str:
    candidate = normalize_message_text(value)
    if not candidate:
        return ""
    if candidate in GENERIC_SHIP_NAME_STOPWORDS:
        return ""
    if any(marker in candidate for marker in ["当前船位", "船舶档案", "历史轨迹", "上一离港", "历史挂靠", "航次/目的港校验", "校验提示"]):
        return ""
    if candidate.startswith("{") or candidate.startswith("["):
        return ""
    return candidate[:80]


def _compress_context_text(text: str, max_chars: int = 90) -> str:
    value = normalize_message_text(text)
    if not value:
        return ""
    value = re.sub(r"https?://[^\s]+", "", value)
    value = re.sub(r"\s+", " ", value).strip()
    if len(value) <= max_chars:
        return value
    cut = value[:max_chars].rstrip(" ，。；;,.!?！？:")
    return cut + "..."


def _context_topic_tokens(text: str) -> set[str]:
    normalized = normalize_message_text(text).lower()
    if not normalized:
        return set()
    tokens: set[str] = set()
    for marker in (
        HIFLEET_ACCOUNT_MARKERS
        | HIFLEET_FEATURE_MARKERS
        | HIFLEET_HOWTO_MARKERS
        | HIFLEET_PERMISSION_QUERY_MARKERS
        | HIFLEET_CONTEXT_CLEAR_MARKERS
        | {"船位", "轨迹", "挂靠", "航次", "海图", "图标", "符号", "截图", "文件", "表格", "报告", "上传", "下载", "账号", "权限", "社区", "官网"}
    ):
        if marker and marker.lower() in normalized:
            tokens.add(marker.lower())
    tokens.update(re.findall(r"\b\d{7,9}\b", normalized))
    return tokens


def _select_relevant_questions(previous_questions: list[str], latest: str, *, limit: int = 4) -> list[str]:
    if not previous_questions:
        return []
    latest_tokens = _context_topic_tokens(latest)
    allow_history_fallback = any(marker in latest for marker in ["上面", "上述", "刚才", "上一条", "总结", "这艘船", "该船"]) or not latest
    scored: list[tuple[int, int, str]] = []
    for idx, item in enumerate(previous_questions):
        compressed = _compress_context_text(item)
        if not compressed:
            continue
        item_tokens = _context_topic_tokens(item)
        shared_tokens = latest_tokens & item_tokens
        significant_overlap = shared_tokens - GENERIC_CONTEXT_TOKENS
        overlap = len(significant_overlap)
        score = overlap * 3
        if latest and any(marker in latest for marker in ["上面", "上述", "刚才", "上一条", "总结", "这艘船", "该船"]):
            score += 2
        if overlap > 0 and any(token in item.lower() for token in ["mmsi", "imo", "船名", "hifleet", "船队在线"]):
            score += 1
        scored.append((score, idx, compressed))

    ranked = [(score, idx, item) for score, idx, item in sorted(scored, key=lambda value: (value[0], value[1]), reverse=True) if score > 0]
    if ranked:
        selected = sorted(ranked[:limit], key=lambda value: value[1])
        ordered = []
        for _, _, item in selected:
            if item and item not in ordered:
                ordered.append(item)
        return ordered[-limit:]
    if allow_history_fallback:
        fallback = [_compress_context_text(item) for item in previous_questions[-limit:] if _compress_context_text(item)]
        ordered = []
        for item in fallback:
            if item and item not in ordered:
                ordered.append(item)
        return ordered[-limit:]
    return []


def _build_context_summary(latest: str, relevant_questions: list[str], last_ship_name: str, last_ship_mmsi: str, last_ship_imo: str) -> str:
    parts: list[str] = []
    if relevant_questions:
        parts.append("最近相关问题：" + " / ".join(relevant_questions[-3:]))
    ship_bits = [bit for bit in [last_ship_name, f"MMSI {last_ship_mmsi}" if last_ship_mmsi else "", f"IMO {last_ship_imo}" if last_ship_imo else ""] if bit]
    if ship_bits:
        parts.append("最近船舶上下文：" + "，".join(ship_bits))
    if not parts and latest:
        parts.append("当前问题：" + _compress_context_text(latest))
    return "；".join(parts)[:300]


def build_conversation_context(messages: list[AnyMessage]) -> ConversationContext:
    user_texts: list[str] = []
    last_ship_name = ""
    last_ship_mmsi = ""
    last_ship_imo = ""
    last_ship_source = ""

    for msg in messages:
        text = ""
        source_type = ""
        if isinstance(msg, HumanMessage):
            text = content_to_text(msg.content)
            source_type = "human"
            if text:
                user_texts.append(text)
        elif isinstance(msg, AIMessage):
            text = content_to_text(msg.content)
            source_type = "ai"
        elif isinstance(msg, dict):
            msg_type = str(msg.get("type", "")).lower()
            role = str(msg.get("role", "")).lower()
            if msg_type == "human" or role == "user":
                text = content_to_text(msg.get("content", ""))
                source_type = "human"
                if text:
                    user_texts.append(text)
            elif msg_type == "ai" or role == "assistant":
                text = content_to_text(msg.get("content", ""))
                source_type = "ai"

        if text:
            ship_entities = extract_entities(text)
            parsed_mmsi = ship_entities.mmsi or _parse_first_mmsi(text)
            parsed_imo = ship_entities.imo or _parse_first_imo(text)
            parsed_name = ship_entities.ship_name
            if not parsed_name and (parsed_mmsi or parsed_imo):
                first_line = _first_non_empty_line(text)
                if first_line and "MMSI:" not in first_line and "IMO:" not in first_line and not first_line.startswith("{"):
                    parsed_name = first_line[:80]
            parsed_name = _clean_ship_name_candidate(parsed_name)
            if source_type == "ai" and not (parsed_mmsi or parsed_imo):
                parsed_name = ""
            if parsed_mmsi or parsed_imo or parsed_name:
                last_ship_mmsi = parsed_mmsi or last_ship_mmsi
                last_ship_imo = parsed_imo or last_ship_imo
                last_ship_name = parsed_name or last_ship_name
                last_ship_source = text

    latest = user_texts[-1] if user_texts else ""
    previous_questions = user_texts[:-1]
    compressed_recent_questions = [_compress_context_text(item) for item in previous_questions if _compress_context_text(item)]
    relevant_recent_questions = _select_relevant_questions(previous_questions, latest)
    previous = relevant_recent_questions[-1] if relevant_recent_questions else (compressed_recent_questions[-1] if compressed_recent_questions else "")
    return ConversationContext(
        latest_user_text=latest,
        previous_user_text=previous,
        recent_user_questions=previous_questions,
        compressed_recent_user_questions=compressed_recent_questions[-8:],
        relevant_recent_user_questions=relevant_recent_questions,
        context_summary=_build_context_summary(latest, relevant_recent_questions, last_ship_name, last_ship_mmsi, last_ship_imo),
        last_ship_name=last_ship_name,
        last_ship_mmsi=last_ship_mmsi,
        last_ship_imo=last_ship_imo,
        last_ship_source=last_ship_source,
    )


def build_llm_context_window(context: ConversationContext, *, limit: int = 3) -> dict[str, Any]:
    relevant_questions = list(context.relevant_recent_user_questions[-limit:])
    previous_user_text = relevant_questions[-1] if relevant_questions else ""
    return {
        "previous_user_text": previous_user_text,
        "recent_user_questions": relevant_questions,
        "context_summary": context.context_summary,
    }


def extract_entities(text: str) -> MessageEntities:
    normalized = normalize_message_text(text)
    urls = [u.rstrip(".,;!?，。；！？）】》") for u in re.findall(r"https?://[^\s)）\]】>\"']+", normalized)]
    imo_match = re.search(r"(?:IMO[:：\s]*)?(\b\d{7}\b)", normalized, flags=re.IGNORECASE)
    explicit_mmsi = re.search(r"MMSI[:：\s]*(\d{5,9})", normalized, flags=re.IGNORECASE)
    mmsi_match = explicit_mmsi or re.search(r"\b(\d{9})\b", normalized, flags=re.IGNORECASE)
    bbox_match = re.search(r"(-?\d+(?:\.\d+)?\s*,\s*-?\d+(?:\.\d+)?\s*,\s*-?\d+(?:\.\d+)?\s*,\s*-?\d+(?:\.\d+)?)", normalized)
    dates = re.findall(r"\b(20\d{2}-\d{1,2}-\d{1,2})\b", normalized)

    strait = ""
    for name in ("曼德海峡", "苏伊士运河", "好望角", "霍尔木兹海峡", "Hormuz", "Suez", "Bab el-Mandeb", "Cape of Good Hope"):
        if name.lower() in normalized.lower():
            strait = name
            break

    area = ""
    for name in ("红海", "波斯湾", "北太平洋", "南海", "马六甲", "地中海", "Red Sea", "Persian Gulf", "North Pacific"):
        if name.lower() in normalized.lower():
            area = name
            break

    port = ""
    port_match = re.search(r"(?:港口|港|port)\s*[:：]?\s*([A-Za-z\u4e00-\u9fff -]{2,40})", normalized, flags=re.IGNORECASE)
    if port_match:
        port = port_match.group(1).strip()

    ship_name = ""
    if not (mmsi_match or imo_match):
        ship_match = re.search(r"(?:船名|vessel|ship)\s*[:：]?\s*([A-Za-z0-9][A-Za-z0-9 ._-]{1,40})", normalized, flags=re.IGNORECASE)
        if ship_match:
            ship_name = ship_match.group(1).strip()
        else:
            bare_ship_match = re.search(r"(?:查询|查|search|where is)?\s*([A-Za-z][A-Za-z0-9 ._-]{2,40}?)(?:\s+|的|近期|最近|当前|历史){0,4}(?:船位|位置|档案|轨迹|挂靠|航次|psc)", normalized, flags=re.IGNORECASE)
            if bare_ship_match:
                ship_name = bare_ship_match.group(1).strip()
            else:
                cn_ship_match = re.search(r"(?:查询|查)?\s*([\u4e00-\u9fffA-Za-z0-9·._-]{2,40}?)(?:的|近期|最近|当前|历史)?(?:船位|位置|档案|轨迹|挂靠|航次|psc)", normalized, flags=re.IGNORECASE)
                if cn_ship_match:
                    candidate = cn_ship_match.group(1).strip()
                    stopwords = GENERIC_SHIP_NAME_STOPWORDS
                    if candidate and candidate not in stopwords:
                        ship_name = candidate

    return MessageEntities(
        urls=urls,
        imo=imo_match.group(1) if imo_match else "",
        mmsi=mmsi_match.group(1) if mmsi_match else "",
        ship_name=ship_name,
        port=port,
        area=area,
        strait=strait,
        bbox=bbox_match.group(1).replace(" ", "") if bbox_match else "",
        start_date=dates[0] if dates else "",
        end_date=dates[1] if len(dates) > 1 else (dates[0] if dates else ""),
    )


def classify_message(text: str, entities: MessageEntities, context: ConversationContext | None = None) -> RouteDecision:
    q = normalize_message_text(text)
    lower = q.lower()
    context = context or ConversationContext()
    relevant_context_text = " ".join(context.relevant_recent_user_questions or [])
    has_hifleet_feature = any(marker in lower for marker in HIFLEET_FEATURE_MARKERS)
    has_account_marker = any(marker in q for marker in HIFLEET_ACCOUNT_MARKERS)
    is_howto = any(marker in q for marker in HIFLEET_HOWTO_MARKERS)

    if any(marker in q for marker in HIFLEET_CONTEXT_CLEAR_MARKERS):
        return RouteDecision("conversation", "conversation_memory", [], "simple", fallback_allowed=False, reason="context clear request")

    memory_markers = ["上面", "上述", "刚才", "刚刚", "之前", "上一个", "上一条", "这艘船", "该船", "这个船", "哪个船", "总结", "汇总"]
    if any(m in q for m in memory_markers):
        if any(m in q for m in ["轨迹", "挂靠", "航次", "目的港", "停靠", "离港", "船位", "档案", "psc"]):
            return RouteDecision("ship_context", "ship_context_followup", SHIP_VOYAGE_BUNDLE, "simple", reason="ship follow-up from conversation context")
        return RouteDecision("conversation", "conversation_memory", [], "simple", fallback_allowed=False, reason="conversation memory question")

    write_markers = ["更新", "上传", "修改", "补录", "update"]
    troubleshooting_markers = ["异常", "失败", "无法", "上传不了", "上传失败", "不显示", "不刷新", "更新慢", "更新很慢", "更新这么慢", "这么慢", "太慢", "延迟", "收不到", "报错", "告警", "报警", "加载失败"]
    platform_markers = ["hifleet", "船队在线", "平台", "功能", "教程", "怎么", "如何", "规则", "配置", "帮助", "绿点", "岸基值班"]
    explicit_write_context = any(m in lower for m in ["上传", "补录", "修改", "更新静态", "更新船位"]) or bool(entities.mmsi and re.search(r"(经度|纬度|lon|lat|speed|heading|course|ship_name|船名|呼号|更新时间)", q, flags=re.IGNORECASE))
    is_troubleshooting = any(m in lower for m in troubleshooting_markers)
    if entities.urls and any(m in lower for m in ["核验", "验证", "官网", "官方", "社区", "链接", "网页"]):
        return RouteDecision("browser_verify", "browser_verify", BROWSER_VERIFY_BUNDLE, "complex", search_depth="normal", reason="public page verification")
    if explicit_write_context and any(m in lower for m in ["船位", "静态", "ais", "位置", "mmsi"]):
        return RouteDecision("ship_update", "ship_update", SHIP_UPDATE_BUNDLE, "simple", reason="explicit ship write operation")

    # ══ 优先级 1：账号权限类问题（免费版/基础版/专业版 + 功能查询）══
    if has_account_marker and any(marker in q for marker in HIFLEET_PERMISSION_QUERY_MARKERS):
        return RouteDecision("knowledge", "platform_knowledge", KNOWLEDGE_BUNDLE, "simple", search_depth="quick", reason="account permission knowledge")

    # ══ 优先级 2：船位/AIS 概念性故障排查（非指定船舶查询）══
    _is_ship_concept_troubleshooting = False
    for concept_word, trouble_indicators in SHIP_CONCEPT_TROUBLESHOOTING_PATTERNS:
        if concept_word in lower and any(ind in lower for ind in trouble_indicators):
            _is_ship_concept_troubleshooting = True
            break
    if _is_ship_concept_troubleshooting and not (entities.mmsi or entities.imo or entities.ship_name):
        return RouteDecision("knowledge", "platform_troubleshooting", KNOWLEDGE_BUNDLE, "simple", search_depth="normal", reason="ship concept troubleshooting (not specific ship)")

    # ══ 优先级 3：平台故障排查（明确 troubleshooting 标记 + 平台关键词）══
    if is_troubleshooting and (any(m in lower for m in platform_markers) or any(m in relevant_context_text.lower() for m in platform_markers)):
        return RouteDecision("knowledge", "platform_troubleshooting", KNOWLEDGE_BUNDLE, "simple", search_depth="normal", reason="platform troubleshooting")

    # ══ 优先级 4：HiFleet 功能查询（平台特性 + howto + 无明确船舶实体）══
    if (has_hifleet_feature or is_howto) and not (entities.mmsi or entities.imo or entities.ship_name):
        # 含有账号标记或平台特性词 + 航线词汇，应走知识库而非船舶查询
        voyage_terms_in_q = any(m in lower for m in ["历史轨迹", "轨迹", "挂靠", "航次", "气象导航", "海图"])
        if voyage_terms_in_q:
            return RouteDecision("knowledge", "platform_knowledge", KNOWLEDGE_BUNDLE, "simple", search_depth="normal", reason="platform feature knowledge (not ship analysis)")

    has_ship_entity = bool(entities.mmsi or entities.imo or entities.ship_name or context.last_ship_mmsi or context.last_ship_imo or context.last_ship_name)
    voyage_markers = ["历史轨迹", "轨迹", "历史挂靠", "挂靠", "航次", "上一港", "上次离港", "当前停船", "停在哪", "停靠", "最近靠港", "目的港", "一致"]
    if has_ship_entity and any(m in lower for m in voyage_markers):
        return RouteDecision("ship_complex", "ship_multi_step_analysis", SHIP_VOYAGE_BUNDLE, "complex", reason="voyage or multi-step ship analysis")

    stats_markers = ["海峡", "通航", "区域", "范围内", "bbox", "polygon", "红海绕航", "港口", "port", "船舶列表"]
    # 具体统计查询（有地理实体或日期）优先走 ship_stats
    has_geo_entity = bool(entities.strait or entities.area or entities.bbox)
    has_stats_signal = any(m in lower for m in ["通航", "统计", "绕航", "船舶列表"])
    if has_geo_entity or (any(m in lower for m in stats_markers) and (has_stats_signal or entities.start_date)):
        return RouteDecision("ship_stats", "ship_stats", SHIP_STATS_BUNDLE, "simple", reason="area/strait/port statistics")
    if is_howto and any(marker in q for marker in ["区域", "海峡", "历史数据", "过往历史", "bbox", "区域船舶"]):
        return RouteDecision("knowledge", "platform_knowledge", KNOWLEDGE_BUNDLE, "simple", search_depth="quick", reason="area/strait how-to")

    if not has_ship_entity and is_troubleshooting:
        return RouteDecision("knowledge", "platform_troubleshooting", KNOWLEDGE_BUNDLE, "simple", search_depth="normal", reason="platform troubleshooting")

    if has_hifleet_feature and not has_ship_entity and any(m in lower for m in voyage_markers):
        return RouteDecision("knowledge", "platform_knowledge", KNOWLEDGE_BUNDLE, "simple", search_depth="quick", reason="feature knowledge not ship analysis")

    if any(m in lower for m in voyage_markers):
        return RouteDecision("ship_complex", "ship_multi_step_analysis", SHIP_VOYAGE_BUNDLE, "complex", reason="voyage or multi-step ship analysis")

    ship_markers = ["船位", "船舶档案", "档案", "psc", "港口国监督", "mmsi", "imo", "位置", "船舶信息"]
    if entities.mmsi or entities.imo or entities.ship_name or any(m in lower for m in ship_markers):
        return RouteDecision("ship_single", "ship_single_query", SHIP_QUERY_BUNDLE, "simple", reason="single ship query")

    if is_troubleshooting:
        return RouteDecision("knowledge", "platform_troubleshooting", KNOWLEDGE_BUNDLE, "simple", search_depth="normal", reason="platform troubleshooting")

    if any(m in lower for m in platform_markers):
        return RouteDecision("knowledge", "platform_knowledge", KNOWLEDGE_BUNDLE, "simple", search_depth="quick", reason="platform knowledge")

    return RouteDecision("knowledge", "platform_knowledge", KNOWLEDGE_BUNDLE, "simple", search_depth="quick", reason="default customer support knowledge")


def classify_multimodal_message(text: str, attachments: list[Attachment], base_decision: RouteDecision) -> RouteDecision:
    q = normalize_message_text(text).lower()
    if not attachments:
        return base_decision
    has_image = any(item.type == "image" for item in attachments)
    has_audio = any(item.type == "audio" for item in attachments)
    has_video = any(item.type == "video" for item in attachments)
    has_file = any(item.type == "file" for item in attachments)
    if has_file or any(marker in q for marker in ["文件", "表格", "excel", "csv", "pdf", "报告", "生成"]):
        return RouteDecision("file_task", "file_task", FILE_BUNDLE, "complex", reason="file attachment")
    if has_image and any(marker in q for marker in ["海图", "符号", "图标", "图中", "截图", "标志", "是什么意思"]):
        return RouteDecision("chart_symbol", "chart_symbol", MULTIMODAL_BUNDLE, "complex", search_depth="deep", reason="chart symbol screenshot")
    if has_audio:
        return RouteDecision("multimodal_understanding", "audio_understanding", MULTIMODAL_BUNDLE, "complex", reason="audio attachment")
    if has_video:
        return RouteDecision("multimodal_understanding", "video_understanding", MULTIMODAL_BUNDLE, "complex", reason="video attachment")
    if has_image:
        return RouteDecision("multimodal_understanding", "image_understanding", MULTIMODAL_BUNDLE, "complex", reason="image attachment")
    return base_decision


def _planner_question_type(decision: RouteDecision) -> str:
    if decision.route in {"ship_single", "ship_complex", "ship_context"}:
        return "ship_query"
    if decision.route == "ship_update":
        return "ship_update"
    if decision.route == "ship_stats":
        return "ship_stats"
    if decision.route == "file_task":
        return "file_task"
    if decision.task_type == "platform_troubleshooting":
        return "troubleshooting"
    if decision.route == "browser_verify":
        return "verification"
    if decision.route in {"chart_symbol", "multimodal_understanding"}:
        return "multimodal"
    if decision.route == "conversation":
        return "conversation"
    return "definition"


def _planner_missing_slot(
    decision: RouteDecision,
    entities: MessageEntities,
    attachments: list[Attachment],
    perception: dict[str, Any],
) -> dict[str, str]:
    if decision.route in {"ship_single", "ship_complex", "ship_context"} and not (entities.mmsi or entities.imo or entities.ship_name):
        return {
            "field": "ship_identifier",
            "question": "请提供 9 位 MMSI、IMO 或唯一船名，我再继续帮您查询。",
        }
    if decision.route == "ship_update" and not entities.mmsi:
        return {
            "field": "mmsi",
            "question": "请提供 9 位 MMSI，我再为您继续处理更新。",
        }
    if decision.route in {"chart_symbol", "multimodal_understanding"} and attachments:
        confidence = str((perception or {}).get("confidence", "")).lower()
        if confidence in {"low", "very_low"}:
            return {
                "field": "clear_attachment",
                "question": "请补一张更清晰的截图，最好把您想确认的位置圈出来，我再继续为您判断。",
            }
    if decision.route == "browser_verify" and not entities.urls:
        return {
            "field": "public_url",
            "question": "请提供需要核验的公开网页链接，我再继续帮您确认。",
        }
    return {}


def _planner_hypotheses(
    decision: RouteDecision,
    perception: dict[str, Any],
) -> list[dict[str, Any]]:
    suspected = normalize_message_text(str((perception or {}).get("suspected_symbol") or (perception or {}).get("suspected_issue") or ""))
    observations = normalize_message_text(str((perception or {}).get("summary") or ""))
    if decision.route == "chart_symbol":
        if "安全水域" in suspected or ("红色" in observations and "黑点" in observations):
            return [
                {"id": "H1", "label": "安全水域浮标", "reason": "截图特征接近安全水域浮标常见表达。", "confidence": "medium", "status": "active"},
                {"id": "H2", "label": "普通航标或图层标注", "reason": "仍需结合 HiFleet 图层资料排除简化图形。", "confidence": "low", "status": "active"},
            ]
        if "锚" in suspected or any(marker in observations for marker in ["小圈", "空心圆"]):
            return [
                {"id": "H1", "label": "锚地或锚泊区域范围标识", "reason": "截图中出现多个小圈，符合区域图层标识特征。", "confidence": "medium", "status": "active"},
                {"id": "H2", "label": "普通图层标注点", "reason": "需排除非锚地的区域图层。", "confidence": "low", "status": "active"},
            ]
        return [{"id": "H1", "label": suspected or "海图符号识别", "reason": "需要结合截图和官方资料确认符号含义。", "confidence": "low", "status": "active"}]
    if decision.task_type == "platform_troubleshooting":
        return [
            {"id": "H1", "label": "文件格式或字段内容异常", "reason": "故障排查优先检查文件和字段。", "confidence": "medium", "status": "active"},
            {"id": "H2", "label": "浏览器/网络/缓存问题", "reason": "弱网、缓存和浏览器兼容性是常见原因。", "confidence": "low", "status": "active"},
            {"id": "H3", "label": "账号权限或平台状态异常", "reason": "权限和平台状态也需要保留为候选原因。", "confidence": "low", "status": "active"},
        ]
    if decision.route in {"knowledge", "browser_verify"}:
        return [{"id": "H1", "label": "官方资料可直接回答", "reason": "优先查本地知识库和 HiFleet 官方资料。", "confidence": "medium", "status": "active"}]
    if decision.route == "multimodal_understanding":
        return [{"id": "H1", "label": suspected or "附件内容识别", "reason": "先基于感知结果理解用户要确认的对象或异常。", "confidence": "medium", "status": "active"}]
    return []


def _planner_search_plan(
    text: str,
    decision: RouteDecision,
    perception: dict[str, Any],
    attachments: list[Attachment],
    hypotheses: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    source_priority = ["local_kb", "official_site", "official_community", "public_web"]
    if decision.route == "conversation":
        return []
    if decision.task_type == "platform_troubleshooting":
        if "上传" in text and "航线" in text:
            return [
                {"hypothesis_id": "H1", "query": "HiFleet 上传航线 失败 文件格式 要求", "depth": "normal", "source_priority": source_priority, "purpose": "确认常见上传失败原因"},
                {"hypothesis_id": "H1", "query": "HiFleet 计划航线 上传 经纬度 模板", "depth": "normal", "source_priority": source_priority, "purpose": "确认字段与模板要求"},
                {"hypothesis_id": "H2", "query": "HiFleet 上传航线 浏览器 网络 权限", "depth": "deep", "source_priority": source_priority, "purpose": "补充浏览器、网络和权限排查"},
            ]
        return [
            {"hypothesis_id": "H1", "query": text, "depth": decision.search_depth or "normal", "source_priority": source_priority, "purpose": "确认平台故障排查建议"},
            {"hypothesis_id": "H2", "query": f"HiFleet {text} 浏览器 网络 缓存", "depth": "deep", "source_priority": source_priority, "purpose": "补充网络与缓存排查"},
        ]
    if decision.route == "chart_symbol":
        attachment_type = attachments[-1].type if attachments else "image"
        query = build_multimodal_search_query(text, perception, decision.route, attachment_type)
        primary_id = hypotheses[0]["id"] if hypotheses else "H1"
        return [
            {"hypothesis_id": primary_id, "query": query, "depth": "deep", "source_priority": source_priority, "purpose": "结合截图特征确认海图符号含义"},
            {"hypothesis_id": primary_id, "query": f"HiFleet 海图 {normalize_message_text(str((perception or {}).get('suspected_symbol') or '符号'))}", "depth": "normal", "source_priority": source_priority, "purpose": "用疑似名称二次核验"},
        ]
    if decision.route == "multimodal_understanding":
        attachment_type = attachments[-1].type if attachments else "attachment"
        return [
            {"hypothesis_id": "H1", "query": build_multimodal_search_query(text, perception, decision.route, attachment_type), "depth": "normal", "source_priority": source_priority, "purpose": "结合附件感知结果补足检索"},
        ]
    if decision.route == "browser_verify":
        return [{"hypothesis_id": "H1", "query": text, "depth": decision.search_depth or "normal", "source_priority": source_priority, "purpose": "核验官方网页与公开信息"}]
    if decision.route == "knowledge":
        # 多角度检索：主查询 + 补充角度，确保覆盖平台功能、使用场景、常见问题
        primary_query = _rewrite_hifleet_knowledge_query(text)
        primary_depth = decision.search_depth or "quick"
        plan = [{"hypothesis_id": "H1", "query": primary_query, "depth": primary_depth, "source_priority": source_priority, "purpose": "从知识库和官方资料回答核心问题"}]
        # 生成补充检索词
        expansion_query = _generate_knowledge_expansion_query(text, decision)
        if expansion_query and expansion_query != primary_query:
            plan.append({"hypothesis_id": "H2", "query": expansion_query, "depth": "normal", "source_priority": source_priority, "purpose": "补充产品能力和使用场景信息"})
        return plan
    return []


def build_customer_support_plan(
    text: str,
    decision: RouteDecision,
    entities: MessageEntities,
    context: ConversationContext,
    attachments: list[Attachment],
    perception: dict[str, Any],
) -> dict[str, Any]:
    missing_slot = _planner_missing_slot(decision, entities, attachments, perception)
    question_type = _planner_question_type(decision)
    hypotheses = _planner_hypotheses(decision, perception)
    search_plan = _planner_search_plan(text, decision, perception, attachments, hypotheses)
    response_mode = "use_harness" if decision.route in HARNESSED_ROUTES else "direct_answer"
    if missing_slot and decision.route in PLANNER_DIRECT_ROUTES:
        response_mode = "ask_one_question"
    problem_frame = {
        "user_goal": text or context.latest_user_text or "回答当前客服问题",
        "question_type": question_type,
        "needs_context": bool(context.context_summary),
        "needs_attachment": decision.route in {"chart_symbol", "multimodal_understanding", "file_task"},
        "needs_search": decision.route in {"knowledge", "chart_symbol", "multimodal_understanding", "browser_verify"},
        "ambiguity_level": "high" if missing_slot else ("medium" if len(hypotheses) > 1 else "low"),
        "critical_unknown": missing_slot.get("field", ""),
    }
    decision_rationale = {
        "chosen_route": decision.route,
        "why_not_other_routes": [
            "不直接暴露内部执行细节，统一按客服话术收口。",
            "高风险船舶、写操作、文件和核验任务仍走确定性执行链。",
        ],
        "need_harness": response_mode == "use_harness",
        "response_mode": response_mode,
    }
    reasoning_public_trace = [
        {"phase": "understand", "text": f"已识别当前问题类型：{question_type}。"},
        {"phase": "hypothesis", "text": f"已形成 {len(hypotheses) or 1} 个候选解释，并优先保留最相关方向。"},
    ]
    if search_plan:
        reasoning_public_trace.append({"phase": "search_plan", "text": f"已规划 {len(search_plan)} 条检索方向，优先本地知识库和 HiFleet 官方资料。"})
    if missing_slot:
        reasoning_public_trace.append({"phase": "missing_slot", "text": f"当前最关键的缺失信息是：{missing_slot['field']}。"})
    return {
        "problem_frame": problem_frame,
        "hypotheses": hypotheses,
        "search_plan": search_plan,
        "missing_slot": missing_slot,
        "decision_rationale": decision_rationale,
        "reasoning_public_trace": reasoning_public_trace,
    }


def _multimodal_troubleshooting_markers() -> tuple[str, ...]:
    return (
        "error",
        "报错",
        "异常",
        "失败",
        "无法",
        "加载失败",
        "打不开",
        "不显示",
        "不刷新",
        "上传不了",
        "上传失败",
        "服务异常",
        "页面异常",
        "network",
    )


def is_multimodal_troubleshooting_signal(text: str, perception: dict[str, Any] | None = None) -> bool:
    merged = " ".join(
        part
        for part in [
            normalize_message_text(text),
            normalize_message_text(str((perception or {}).get("summary") or "")),
            normalize_message_text(str((perception or {}).get("visible_text") or "")),
            normalize_message_text(str((perception or {}).get("suspected_issue") or "")),
            normalize_message_text(str((perception or {}).get("suspected_symbol") or "")),
        ]
        if part
    ).lower()
    return any(marker in merged for marker in _multimodal_troubleshooting_markers())


def refine_multimodal_route_with_perception(
    text: str,
    attachments: list[Attachment],
    perception: dict[str, Any],
    base_decision: RouteDecision,
) -> RouteDecision:
    if not attachments:
        return base_decision
    if base_decision.route == "chart_symbol":
        return base_decision
    if is_multimodal_troubleshooting_signal(text, perception):
        return RouteDecision(
            "knowledge",
            "platform_troubleshooting",
            KNOWLEDGE_BUNDLE,
            "complex",
            search_depth="deep",
            reason="multimodal troubleshooting screenshot",
        )
    return base_decision


def should_use_ship_context(route: str) -> bool:
    return route in {"ship_single", "ship_complex", "ship_context", "ship_update"}


def resolve_entities_with_context(
    entities: MessageEntities,
    context: ConversationContext,
    *,
    allow_ship_context: bool = True,
) -> MessageEntities:
    if not allow_ship_context or entities.mmsi or entities.imo or entities.ship_name:
        return entities
    return MessageEntities(
        urls=list(entities.urls),
        imo=context.last_ship_imo,
        mmsi=context.last_ship_mmsi,
        ship_name=context.last_ship_name,
        port=entities.port,
        area=entities.area,
        strait=entities.strait,
        bbox=entities.bbox,
        start_date=entities.start_date,
        end_date=entities.end_date,
    )


def answer_conversation_memory(text: str, context: ConversationContext) -> str:
    q = normalize_message_text(text)
    if any(marker in q for marker in HIFLEET_CONTEXT_CLEAR_MARKERS):
        return (
            "好的，我接下来会按新问题重新理解，不再主动引用上面的业务上下文。\n\n"
            "如果您是通过接口接入并希望彻底清空历史记忆，请同时更换新的 session_id。"
        )
    if any(m in q for m in ["如何思索", "怎么思索", "检索资源", "审查确定", "总结逻辑", "详细介绍逻辑"]):
        return (
            "可以按“先识别问题类型，再找证据，再交叉校验，最后客服化回复”的方式理解：\n\n"
            "1. 先看用户输入和附件，判断是平台操作、故障排查、海图符号、船舶查询还是文件任务。若截图里有符号或报错，会先提取可见特征和文字。\n\n"
            "2. 再把问题改写成适合检索的关键词。例如海图符号会组合“HiFleet 全球海图、符号外观、疑似名称”，上传航线失败会组合“计划航线、上传航线、文件格式、经纬度格式、浏览器/权限”。\n\n"
            "3. 检索顺序优先使用本地客服知识库和产品资料；命中弱时再查 HiFleet 官网/官方社区；仍不足时参考公共网页，并降低确定性表述。\n\n"
            "4. 审查时主要看三点：来源是否可信，结论是否能被多个线索支持，链接是否可访问。涉及截图时，还会把图中可见特征和检索到的符号定义互相核对。\n\n"
            "5. 最终回复不展示内部工具、日志或原始过程，只给客户可执行结论：先结论，再解释原因，再给操作建议；信息不够时只追问一个最关键问题。"
        )
    history_items = context.relevant_recent_user_questions or context.compressed_recent_user_questions or context.recent_user_questions
    if not history_items:
        return "当前会话里还没有可总结的上一轮问题。"
    if any(m in q for m in ["哪个船", "哪一个船", "这艘船", "该船", "上一个我问的是哪个船"]):
        if context.last_ship_name or context.last_ship_mmsi or context.last_ship_imo:
            ship_bits = [bit for bit in [context.last_ship_name, f"MMSI：{context.last_ship_mmsi}" if context.last_ship_mmsi else "", f"IMO：{context.last_ship_imo}" if context.last_ship_imo else ""] if bit]
            return "你上面查询的船舶是" + "，".join(ship_bits) + "。"
        return "当前会话里没有识别到明确的船舶标识。"
    lines = []
    for idx, item in enumerate(history_items[-8:], start=1):
        lines.append(f"{idx}. {item}")
    return "你上面主要问了这些问题：\n" + "\n".join(lines)


def is_kb_effective_hit(search_output: str) -> bool:
    text = search_output or ""
    return any(marker in text for marker in ("SMART_SEARCH_L1_HIT", "【优先匹配 - FAQ/标准回复】", "从平台术语速查表中匹配到"))


def is_no_hit_text(output: str) -> bool:
    text = output or ""
    return any(marker in text for marker in ("未检索到足够可信", "未找到精确的FAQ匹配", "未找到", "暂无", "信息不足"))


def _strip_markdown(text: str) -> str:
    value = text or ""
    value = re.sub(r"\*\*(.*?)\*\*", r"\1", value)
    value = re.sub(r"^#{1,6}\s*", "", value)
    value = value.replace("📋", "").replace("🔗", "").strip()
    return value


def _extract_search_answer(search_output: str) -> tuple[str, list[str]]:
    text = normalize_message_text(search_output)
    if not text:
        return "", []
    parts: list[str] = []
    links: list[str] = []
    for raw_line in text.splitlines():
        line = _strip_markdown(raw_line).strip(" -")
        if not line:
            continue
        if re.match(r"^\[Query\d+:", line):
            continue
        if line.startswith("http://") or line.startswith("https://"):
            links.append(line)
            continue
        if line.startswith("【回答指导】") or line.startswith("【互联网搜索结果") or line.startswith("【Hifleet官方站内搜索】"):
            continue
        if line.startswith("从平台术语速查表中匹配到以下标准解释"):
            continue
        if line.startswith("来源："):
            continue
        if line.startswith("相关度:") or line.startswith("权威来源可直接引用") or line.startswith("综合多个来源回答"):
            continue
        if line.startswith("术语："):
            continue
        if any(marker in line for marker in ["请直接使用上述定义回答用户", "禁止猜测或编造其他解释", "如果您愿意，我也可以继续结合具体页面"]):
            continue
        if line.startswith("内容摘要：") or line.startswith("摘要：") or line.startswith("摘要:") or line.startswith("详细内容：") or line.startswith("详细内容:"):
            content = line.split("：", 1)[-1] if "：" in line else line.split(":", 1)[-1]
            content = content.strip()
            if content:
                parts.append(content.rstrip(".").rstrip("..."))
            continue
        if line.startswith("AI摘要：") or line.startswith("AI摘要:"):
            content = line.split("：", 1)[-1] if "：" in line else line.split(":", 1)[-1]
            content = content.strip()
            if content:
                parts.append(content.rstrip(".").rstrip("..."))
            continue
        if re.fullmatch(r"【[^】]+】", line):
            continue
        if line.startswith("http://") or line.startswith("https://"):
            links.append(line)
            continue
        if line.startswith("www."):
            links.append(f"https://{line}")
            continue
        parts.append(line)
    deduped_parts: list[str] = []
    for item in parts:
        if item and item not in deduped_parts:
            deduped_parts.append(item)
    deduped_links: list[str] = []
    for item in links:
        if item and item not in deduped_links:
            deduped_links.append(item)
    return "\n".join(deduped_parts[:3]).strip(), deduped_links


def _is_hifleet_business_query(question: str) -> bool:
    q = normalize_message_text(question).lower()
    return any(marker in q for marker in HIFLEET_FEATURE_MARKERS) or any(marker in q for marker in HIFLEET_ACCOUNT_MARKERS)


def _rewrite_hifleet_knowledge_query(question: str) -> str:
    """Rewrite query to add HiFleet product context for better KB retrieval."""
    q = normalize_message_text(question)
    if not q:
        return q
    if "hifleet" in q.lower() or "船队在线" in q:
        return q
    if any(marker in q for marker in HIFLEET_FEATURE_MARKERS | HIFLEET_ACCOUNT_MARKERS):
        return f"HiFleet {q}"
    # 对平台知识类问题补充 HiFleet 产品上下文
    platform_signal_words = {"海图", "航线", "轨迹", "气象", "船位", "岸基", "点验",
                             "告警", "报警", "监控", "导航", "ais", "dtu", "船舶跟踪"}
    if any(w in q.lower() for w in platform_signal_words):
        return f"HiFleet {q}"
    return q


def _generate_knowledge_expansion_query(text: str, decision: RouteDecision) -> str:
    """Generate a complementary search query from a different angle."""
    q = normalize_message_text(text).lower()
    # 账号权限类：补充功能说明
    if any(m in q for m in ["免费版", "基础版", "专业版", "账号", "权限", "会员"]):
        feature = ""
        for f in ["历史轨迹", "气象预报", "气象导航", "海图", "船位", "航线", "岸基值班"]:
            if f in q:
                feature = f
                break
        if feature:
            return f"HiFleet {feature} 功能说明 使用场景"
    # 功能询问类：补充操作步骤和常见问题
    if any(m in q for m in ["如何", "怎么", "怎样", "为什么"]):
        core_topic = ""
        for topic in ["气象导航", "全球海图", "中国海图", "历史轨迹", "岸基值班",
                      "船舶点验", "航线", "报警", "监控", "船位更新"]:
            if topic in q:
                core_topic = topic
                break
        if core_topic:
            return f"HiFleet {core_topic} 操作步骤 常见问题"
    # 故障排查类：补充解决方案
    if decision.task_type == "platform_troubleshooting":
        return f"HiFleet {text} 解决方案 处理方法"
    # 通用产品问题：补充产品功能介绍
    return f"HiFleet {text} 产品功能 使用说明"


def _try_direct_hifleet_knowledge_answer(question: str) -> str:
    q = normalize_message_text(question)
    lower = q.lower()
    compact = re.sub(r"\s+", "", lower)

    if any(marker in q for marker in HIFLEET_CONTEXT_CLEAR_MARKERS):
        return (
            "好的，后续我会按新问题重新理解，不再主动引用上面的业务上下文。\n\n"
            f"如果您是通过接口接入并希望彻底清空历史记忆，请同时更换新的 session_id。"
        )

    if "海图更新频率" in q or ("海图" in q and "更新" in q and "频率" in q):
        return (
            "如果您问的是 HiFleet 平台里的海图更新频率，目前可按这几类理解：\n"
            "1. 全球海图（Cmap 授权）支持年度更新、季度更新和每周更新。\n"
            "2. AVCS Online 海图与船上使用的 ENC 海图同步，为每周更新。\n"
            "3. 如果您问的是气象海况图层，数据每 6 小时更新一次，每天更新 4 次，最长可看未来 15 天。\n\n"
            "如果您方便，也可以告诉我是【全球海图 / AVCS 海图 / 气象海况】哪一类，我可以继续给您更精确的说明。"
        )

    if ("气象导航" in q or ("气象" in q and "导航" in q)) and any(marker in q for marker in ["如何", "怎么", "怎样", "使用", "功能"]):
        return (
            "HiFleet 气象导航功能可以帮助您基于实时气象数据规划最优航线。\n\n"
            "主要使用方式：\n"
            "1. 在计划航线页面，选择\"气象叠加\"可查看航线沿途的风、浪、流等气象预报。\n"
            "2. 通过\"气象路径优化\"功能，系统会自动推荐避开恶劣天气的备选航线。\n"
            "3. 气象数据每 6 小时更新一次，专业版最长可查看未来 15 天预报。\n\n"
            "入口：进入目标船舶 -> 计划/航线 -> 气象叠加/气象导航。\n\n"
            "如需更具体的操作指引，请告诉我您是想查看航线气象还是做航线气象优化。"
        )

    if "全球海图" in q and not any(m in q for m in ["更新", "频率", "符号"]):
        return (
            "HiFleet 全球海图（Global Chart）基于 Cmap 授权数据，覆盖全球航区，主要能力包括：\n\n"
            "1. 海图显示：支持全球电子海图浏览，包含航道、水深、灯标、助航标志等信息。\n"
            "2. 海图更新：支持年度/季度/每周更新。\n"
            "3. 航线叠加：可在海图上叠加计划航线和历史轨迹。\n"
            "4. 功能入口：平台首页 -> 全球海图。\n\n"
            "如您有关于海图符号、图层操作或权限范围的具体问题，请继续告诉我。"
        )

    if ("专业版" in q or "专业版账号" in q) and ("气象预报" in q or "气象" in q) and any(marker in q for marker in ["几天", "多久"]):
        return (
            "HiFleet 专业版可查看最长未来 15 天的气象预报。\n\n"
            "补充说明：气象数据每 6 小时一档，每天更新 4 次，可查看气压、风、浪、流等要素。"
        )

    if ("基础版" in q or "免费版" in q or "专业版" in q) and ("历史轨迹" in q or "轨迹" in q) and any(marker in q for marker in ["多久", "多久前", "可查", "能看"]):
        return (
            "按目前资料，HiFleet 不同账号的历史轨迹范围如下：\n"
            "1. 免费版：通常可查看近 3 个月。\n"
            "2. 基础版：可查看近 12 个月。\n"
            "3. 专业版：可查看近 36 个月。\n\n"
            f"{HIFLEET_ACCOUNT_PAGE_HINT}"
        )

    if ("区域" in q and any(marker in q for marker in ["历史数据", "过往历史"])) or ("海峡" in q and "历史数据" in q):
        return (
            "如果您要查询 HiFleet 的区域或海峡过往历史数据，可以按两种场景处理：\n"
            "1. 查区域当前船舶数量：提供区域名称、区域 ID 或坐标范围（bbox）。\n"
            "2. 查海峡过往通航统计：提供海峡名称和时间范围，我可以继续帮您查询历史统计数据。\n\n"
            "如果您现在就要查，请直接把区域名称或 bbox 发我；如果是海峡历史数据，请补充海峡名称和起止日期。"
        )

    return ""


def _format_general_knowledge_answer(question: str, search_output: str, *, evidence_items: list[dict[str, Any]] | None = None) -> str:
    """Format knowledge answer with structured output: conclusion → explanation → suggestion."""
    direct_answer = _try_direct_hifleet_knowledge_answer(question)
    if direct_answer:
        return format_customer_answer(direct_answer)
    summary, links = _extract_search_answer(search_output)
    q = normalize_message_text(question).lower()
    if any(marker in q for marker in ["图标", "符号"]) and any(marker in summary for marker in ["未提供", "缺少核心识别要素", "无法开展针对性的联网检索", "补充上传"]):
        return (
            "目前还不能直接判断这个图标或符号的含义，因为现有信息里缺少最关键的识别依据。\n\n"
            "请只补充一个关键信息：图标原图或更清晰的截图。我收到后会结合 HiFleet 官方资料继续为您确认。"
        )
    # 结构化回复：先结论，再详细说明，最后建议
    if summary:
        # 从多源证据中综合生成结构化回复
        supplementary_info = _extract_supplementary_evidence(evidence_items) if evidence_items else ""
        answer_parts = []
        if any(marker in q for marker in ["什么", "什么意思", "是什么", "哪些"]):
            answer_parts.append(f"根据目前检索到的官方资料，先给您结论：\n{summary}")
        elif any(marker in q for marker in ["如何", "怎么", "怎样", "为什么"]):
            answer_parts.append(summary)
        else:
            answer_parts.append(summary)
        # 补充信息（来自多源证据综合）
        if supplementary_info:
            answer_parts.append(f"\n\n补充说明：\n{supplementary_info}")
        # 官方链接引导
        if any(link.startswith(HELP_CENTER_URL) or "hifleet.com/wp/communities" in link for link in links):
            answer_parts.append(f"\n\n如需自助查看，可参考官方帮助中心：{HELP_CENTER_URL}")
        return format_customer_answer("".join(answer_parts))
    return (
        "我暂时还没有拿到足够明确的官方信息来直接下结论。\n\n"
        "请只补充一个最关键的细节，我再继续帮您核查。"
    )


def _extract_supplementary_evidence(evidence_items: list[dict[str, Any]] | None) -> str:
    """从多条证据中提取补充信息（仅取非主查询的高质量结果）。"""
    if not evidence_items or len(evidence_items) <= 1:
        return ""
    supplementary_snippets = []
    for i, item in enumerate(evidence_items[1:], 1):
        snippet = str(item.get("snippet", "")).strip()
        relevance = float(item.get("relevance", 0))
        if snippet and relevance >= 0.7 and len(snippet) > 20:
            # 去重：不重复主查询已有的内容
            main_snippet = str(evidence_items[0].get("snippet", ""))
            if snippet[:40] not in main_snippet:
                supplementary_snippets.append(snippet[:200])
    if not supplementary_snippets:
        return ""
    return "\n".join(f"- {s}" for s in supplementary_snippets[:2])


def validate_links(text: str, checker: Callable[[str], bool] | None = None) -> tuple[bool, list[str]]:
    links = [u.rstrip(".,;!?，。；！？）】》") for u in re.findall(r"https?://[^\s)）\]】>\"']+", text or "")]
    if not links:
        return True, []
    trusted_prefixes = (HELP_CENTER_URL, "https://www.hifleet.com/wp/communities")
    if checker is None:
        try:
            from skills.knowledge_qa.tools import _is_url_accessible

            checker = _is_url_accessible
        except Exception:
            checker = lambda url: bool(urlparse(url).scheme in ("http", "https"))
    invalid = [url for url in links if not url.startswith(trusted_prefixes) and not checker(url)]
    return not invalid, invalid


def _invoke_tool(tool_map: dict[str, Any], trace: HarnessTrace, name: str, args: dict[str, Any]) -> str:
    t0 = time.time()
    trace.tool_call_sequence.append(name)
    tool = tool_map[name]
    result = tool.invoke(args)
    trace.latency_hotspot[name] = int((time.time() - t0) * 1000)
    return str(result)


def _parse_first_mmsi(text: str) -> str:
    match = re.search(r"\b\d{9}\b", text or "")
    return match.group(0) if match else ""


def _parse_first_imo(text: str) -> str:
    match = re.search(r"\b\d{7}\b", text or "")
    return match.group(0) if match else ""


def _extract_label_value(text: str, labels: list[str]) -> str:
    for label in labels:
        match = re.search(rf"{re.escape(label)}[:：]\s*([^\n|]+)", text or "")
        if match:
            return match.group(1).strip()
    return ""


def _parse_json_blob(text: str) -> dict[str, Any] | None:
    try:
        value = json.loads(text or "")
        return value if isinstance(value, dict) else None
    except Exception:
        return None


def _summarize_last_departure(raw: str) -> str:
    blob = _parse_json_blob(raw)
    if not blob:
        return raw
    item = blob.get("list") or {}
    if not isinstance(item, dict):
        return raw
    portname = item.get("portname") or item.get("cnportname") or item.get("portcode") or "-"
    departtime = item.get("departtime") or "-"
    country = item.get("country") or ""
    country_text = f"（{country}）" if country else ""
    return f"上一离港港口：{portname}{country_text}\n离港时间：{departtime}"


def _summarize_call_ports(raw: str, limit: int = 3) -> str:
    blob = _parse_json_blob(raw)
    if not blob:
        return raw
    route_feature = ((blob.get("list") or {}).get("shipRouteFeature")) or []
    if not isinstance(route_feature, list) or not route_feature:
        return raw
    lines = []
    for item in route_feature[:limit]:
        if not isinstance(item, dict):
            continue
        portname = item.get("cnportname") or item.get("mportname") or item.get("portcode") or "-"
        leavetime = item.get("mleavetime") or item.get("mupdatetime") or "-"
        lines.append(f"- {portname} | 离港/更新：{leavetime}")
    if not lines:
        return raw
    return "最近挂靠记录：\n" + "\n".join(lines)


def _summarize_trajectory(raw: str, limit: int = 3) -> str:
    blob = _parse_json_blob(raw)
    if not blob:
        return raw
    ship_points = ((((blob.get("ships") or {}).get("offers") or {}).get("ship")) or [])
    if not isinstance(ship_points, list) or not ship_points:
        return raw
    first = ship_points[0] if isinstance(ship_points[0], dict) else {}
    last = ship_points[-1] if isinstance(ship_points[-1], dict) else {}
    points = []
    for item in ship_points[:limit]:
        if not isinstance(item, dict):
            continue
        points.append(f"- {item.get('ti', '-')} | {item.get('la', '-')},{item.get('lo', '-')} | 航速 {item.get('sp', '-') } 节")
    summary = [
        f"轨迹点数量：{len(ship_points)}",
        f"时间范围：{first.get('ti', '-')} -> {last.get('ti', '-')}",
        "轨迹示例：",
        *points,
    ]
    return "\n".join(summary)


def execute_knowledge_chain(text: str, decision: RouteDecision, tool_map: dict[str, Any], trace: HarnessTrace) -> str:
    direct_answer = _try_direct_hifleet_knowledge_answer(text)
    if direct_answer:
        trace.check_result = {"links_ok": True, "direct_business_answer": True}
        trace.answer_confidence = "high"
        return format_customer_answer(direct_answer)

    depth = decision.search_depth or "quick"
    query = _rewrite_hifleet_knowledge_query(text)
    output = _invoke_tool(tool_map, trace, "smart_search", {"query": query, "depth": depth})
    if depth == "quick" and is_no_hit_text(output):
        trace.fallback_reason = "quick_kb_weak_hit"
        output = _invoke_tool(tool_map, trace, "smart_search", {"query": query, "depth": "normal"})
    if "未检索到足够可信" in output and decision.task_type == "platform_troubleshooting":
        trace.fallback_reason = "normal_search_empty"
        output = _invoke_tool(tool_map, trace, "smart_search", {"query": query, "depth": "deep"})
    
    # Fallback to agent_browser_deep_search if smart_search still returns no hits
    if is_no_hit_text(output) and "agent_browser_deep_search" in tool_map:
        trace.fallback_reason = "smart_search_empty_agent_browser_fallback"
        browser_output = _invoke_tool(
            tool_map,
            trace,
            "agent_browser_deep_search",
            {"query": query},
        )
        if not is_no_hit_text(browser_output):
            output = browser_output

    ok, invalid = validate_links(output)
    trace.check_result = {"links_ok": ok, "invalid_links": invalid}
    trace.answer_confidence = "high" if ok and not is_no_hit_text(output) else "medium"
    if invalid:
        cleaned = output
        for url in invalid:
            cleaned = cleaned.replace(url, "")
        output = cleaned.strip() + f"\n\n可访问的官方帮助中心：{HELP_CENTER_URL}"
        trace.fallback_reason = trace.fallback_reason or "invalid_links_removed"
    if "上传" in text and "航线" in text and any(marker in text for marker in ["不了", "失败", "怎么办", "无法"]):
        return _format_route_upload_troubleshooting(output)
    if decision.task_type == "platform_troubleshooting":
        return _format_platform_troubleshooting_answer(text, output)
    return _format_general_knowledge_answer(text, output)


def execute_planned_knowledge_chain(
    question: str,
    decision: RouteDecision,
    search_plan: list[dict[str, Any]],
    tool_map: dict[str, Any],
    trace: HarnessTrace,
) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
    queries = search_plan or [{"query": _rewrite_hifleet_knowledge_query(question), "depth": decision.search_depth or "quick", "hypothesis_id": "H1", "purpose": "回答当前问题"}]
    outputs: list[str] = []
    evidence_items: list[dict[str, Any]] = []
    found_high_quality = False
    for item in queries:
        query = str(item.get("query", "")).strip() or _rewrite_hifleet_knowledge_query(question)
        depth = str(item.get("depth", "")).strip() or decision.search_depth or "quick"
        output = _invoke_tool(tool_map, trace, "smart_search", {"query": query, "depth": depth})
        outputs.append(output)
        source_type = _guess_evidence_source_type(output)
        is_hit = not is_no_hit_text(output)
        evidence_items.append(
            {
                "source_type": source_type,
                "source_name": "smart_search",
                "url": HELP_CENTER_URL if "helpcenter" in output.lower() else "",
                "snippet": _extract_search_answer(output)[0][:240],
                "supports": [str(item.get("hypothesis_id", "H1"))],
                "conflicts": [],
                "authority": 0.95 if "hifleet.com" in output.lower() else 0.75,
                "relevance": 0.9 if is_hit else 0.4,
                "query": query,
                "depth": depth,
                "purpose": str(item.get("purpose", "")),
            }
        )
        if is_hit and source_type in {"local_kb", "official_site", "official_community"}:
            found_high_quality = True
    # 如果所有查询都未命中，尝试升级搜索深度
    if not found_high_quality and outputs and all(is_no_hit_text(item) for item in outputs):
        trace.fallback_reason = trace.fallback_reason or "all_queries_weak_hit"
        escalated_query = _rewrite_hifleet_knowledge_query(question)
        escalated_output = _invoke_tool(tool_map, trace, "smart_search", {"query": escalated_query, "depth": "deep"})
        outputs.append(escalated_output)
        evidence_items.append({
            "source_type": _guess_evidence_source_type(escalated_output),
            "source_name": "smart_search",
            "url": "",
            "snippet": _extract_search_answer(escalated_output)[0][:240],
            "supports": ["H1"],
            "conflicts": [],
            "authority": 0.7,
            "relevance": 0.7 if not is_no_hit_text(escalated_output) else 0.3,
            "query": escalated_query,
            "depth": "deep",
            "purpose": "升级搜索深度后尝试获取更多信息",
        })
    
    # Fallback to agent_browser_deep_search if deep smart_search still returns no hits
    if all(is_no_hit_text(item) for item in outputs) and "agent_browser_deep_search" in tool_map:
        trace.fallback_reason = "smart_search_empty_agent_browser_fallback"
        browser_query = _rewrite_hifleet_knowledge_query(question)
        browser_output = _invoke_tool(
            tool_map,
            trace,
            "agent_browser_deep_search",
            {"query": browser_query},
        )
        if not is_no_hit_text(browser_output):
            outputs.append(browser_output)
            evidence_items.append({
                "source_type": "public_web",
                "source_name": "agent_browser_deep_search",
                "url": "",
                "snippet": _extract_search_answer(browser_output)[0][:240],
                "supports": ["H1"],
                "conflicts": [],
                "authority": 0.6,  # Lower authority for public web results
                "relevance": 0.6,
                "query": browser_query,
                "depth": "deep",
                "purpose": "受控公开网页深度检索兜底",
            })
    # 选择最佳输出：优先匹配高质量结果
    output = _select_best_evidence_output(outputs, evidence_items)
    ok, invalid = validate_links(output)
    if invalid:
        cleaned = output
        for url in invalid:
            cleaned = cleaned.replace(url, "")
        output = cleaned.strip() + f"\n\n可访问的官方帮助中心：{HELP_CENTER_URL}"
        trace.fallback_reason = trace.fallback_reason or "invalid_links_removed"
    evidence_summary = review_evidence_items(evidence_items)
    trace.check_result = {
        "links_ok": ok,
        "invalid_links": invalid,
        "planned_queries": [item.get("query", "") for item in queries],
        "evidence_count": len(evidence_items),
        "official_support_count": evidence_summary["official_support_count"],
        "multi_query_synthesis": len(queries) > 1,
    }
    trace.answer_confidence = evidence_summary["confidence"]
    if "上传" in question and "航线" in question and any(marker in question for marker in ["不了", "失败", "怎么办", "无法"]):
        return _format_route_upload_troubleshooting(output), evidence_items, evidence_summary
    if decision.task_type == "platform_troubleshooting":
        return _format_platform_troubleshooting_answer(question, output), evidence_items, evidence_summary
    return _format_general_knowledge_answer(question, output, evidence_items=evidence_items), evidence_items, evidence_summary


def _format_platform_troubleshooting_answer(question: str, search_output: str) -> str:
    q = normalize_message_text(question).lower()
    if any(marker in q for marker in ["error", "报错", "异常", "加载失败", "打不开", "不显示"]):
        return (
            "从当前信息看，这更像是 HiFleet 页面或网络加载异常，不是海图符号本身的问题。\n\n"
            "建议先按这个顺序排查：\n"
            "1. 先刷新页面或重新进入当前功能页，确认问题是否可稳定复现。\n"
            "2. 切换网络重试，例如从移动网络切到 Wi-Fi，排除链路抖动、DNS 或代理影响。\n"
            "3. 清理缓存后重新登录；如果是在微信内打开页面，也建议退出后重新进入。\n"
            "4. 如果只有个别功能报错，优先记录触发动作和时间点，便于继续定位。\n\n"
            f"如果还会出现这个弹窗，请只补充一个关键线索：报错出现前您点了哪个操作？\n\n可参考官方帮助中心：{HELP_CENTER_URL}"
        )
    return (
        "我先按平台故障排查思路给您结论：这类问题通常优先检查网络、页面缓存、账号权限和操作入口是否正确。\n\n"
        "建议顺序：\n"
        "1. 先确认当前功能入口、账号权限和操作步骤是否正确。\n"
        "2. 再切换浏览器或网络环境重试，排除缓存、Cookie、代理或弱网影响。\n"
        "3. 如果问题持续存在，保留报错时间点和截图，便于进一步核查。\n\n"
        f"如果您愿意，我继续排查时只需要一个关键信息：出现问题时的报错截图或具体报错文案。\n\n可参考官方帮助中心：{HELP_CENTER_URL}"
    )


def _format_route_upload_troubleshooting(search_output: str) -> str:
    return (
        "优先排查文件格式和内容问题，这是 HiFleet 航线上传失败最常见的原因。\n\n"
        "一、先检查文件本身\n"
        "1. 文件格式：HiFleet 支持常见航线文件，如 xls、csv、xml、rux、rx4、rtz 等，多数船舶 ECDIS 导出的航线文件可直接识别。\n"
        "2. Excel 文件：建议使用 .xls 或平台模板，避免带宏、加密、受保护或复杂格式的表格。\n"
        "3. 经度纬度：建议使用十进制度，例如 31.2304、121.4737；不要混用特殊符号、中文逗号或异常空格。\n"
        "4. 列结构：如果使用模板，列名、经纬度、转向点顺序应与模板一致；转向点过少或顺序异常也可能失败。\n\n"
        "二、再检查网络和浏览器\n"
        "1. 切换 Chrome 或 Edge 重新上传。\n"
        "2. 清理缓存/Cookie，或用无痕窗口重新登录。\n"
        "3. 如果在公司或船舶局域网内，尝试切换网络，避免防火墙或代理中断上传。\n\n"
        "三、确认账号权限和入口\n"
        "1. 确认账号已绑定目标船舶，并有船队管理/航线编辑权限。\n"
        "2. 上传入口建议从目标船舶进入“计划”页面，再选择文件上传。\n\n"
        "四、临时替代方式\n"
        "如果文件持续失败，可以先用手绘航线或航程规划生成计划航线；如是邮件登记用户，也可按登记方式发送 ECDIS 导出的航线文件。\n\n"
        "如果以上仍失败，请发我一个最关键的信息：上传失败时的报错截图。"
    )


def format_customer_answer(raw: str, *, heading: str = "") -> str:
    text = normalize_message_text(raw)
    for marker in ["【互联网搜索结果（增强版）】", "【Hifleet官方站内搜索】", "【回答指导】", "AI摘要", "回答指导"]:
        text = text.replace(marker, "")
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if not text:
        return "暂时没有获得足够可靠的信息。请补充一个关键细节后我再继续核查。"
    if heading and not text.startswith(heading):
        return f"{heading}\n\n{text}"
    return text


def build_multimodal_search_query(text: str, perception: dict[str, Any], route: str, attachment_type: str) -> str:
    observations = normalize_message_text(str(perception.get("summary") or perception.get("observations") or ""))
    visible_text = normalize_message_text(str(perception.get("visible_text") or ""))
    suspected = normalize_message_text(str(perception.get("suspected_issue") or perception.get("suspected_symbol") or ""))
    if route == "chart_symbol":
        return " ".join(part for part in ["HiFleet 全球海图 海图符号", suspected, visible_text, observations, text] if part)
    return " ".join(part for part in [text, suspected, visible_text, observations] if part) or f"HiFleet {attachment_type} 附件问题"


def _guess_evidence_source_type(output: str) -> str:
    lowered = (output or "").lower()
    if "helpcenter" in lowered or "www.hifleet.com" in lowered:
        return "official_site"
    if "wp/communities" in lowered:
        return "official_community"
    if "faq" in lowered or "标准回复" in lowered or "smart_search_l1_hit" in lowered:
        return "local_kb"
    return "public_web"


def _select_best_evidence_output(outputs: list[str], evidence_items: list[dict[str, Any]]) -> str:
    """从多次搜索结果中选择最佳输出：优先高质量源，其次高相关度。"""
    if not outputs:
        return ""
    if len(outputs) == 1:
        return outputs[0]
    # 优先返回本地知识库或官方站点的结果
    high_quality_sources = {"local_kb", "official_site", "official_community"}
    for i, item in enumerate(evidence_items):
        if i < len(outputs) and item.get("source_type") in high_quality_sources and not is_no_hit_text(outputs[i]):
            return outputs[i]
    # 其次返回任何命中的结果
    for i, output in enumerate(outputs):
        if not is_no_hit_text(output):
            return output
    # 全部未命中，返回最后一个（通常是升级搜索的结果）
    return outputs[-1]


def review_evidence_items(evidence_items: list[dict[str, Any]]) -> dict[str, Any]:
    support_count = len(evidence_items)
    official_support_count = sum(1 for item in evidence_items if item.get("source_type") in {"official_site", "official_community"})
    conflict_count = sum(1 for item in evidence_items if item.get("conflicts"))
    if support_count >= 2 and official_support_count >= 1 and conflict_count == 0:
        confidence = "high"
    elif support_count >= 1:
        confidence = "medium"
    else:
        confidence = "low"
    return {
        "best_hypothesis": (evidence_items[0].get("supports") or ["H1"])[0] if evidence_items else "",
        "support_count": support_count,
        "official_support_count": official_support_count,
        "conflict_count": conflict_count,
        "confidence": confidence,
        "can_answer_directly": support_count > 0,
    }


def _format_multimodal_troubleshooting_answer(
    observations: str,
    visible_text: str,
    suspected: str,
    search_output: str,
) -> str:
    issue_text = suspected or observations or "截图里显示页面出现异常"
    prompt = "如果方便，请只补充一个关键线索：报错出现前您点了哪个操作？"
    if "上传" in f"{issue_text} {visible_text}" and "航线" in f"{issue_text} {visible_text}":
        prompt = "如果方便，请只补充一个关键线索：上传失败时的报错截图。"
    return (
        f"从截图看，当前更像是 HiFleet 页面出现了通用异常提示，重点不是图中符号，而是页面/网络/加载故障。\n\n"
        f"初步判断：{issue_text}。\n\n"
        "建议先按这个顺序排查：\n"
        "1. 刷新页面或重新进入当前功能页，确认问题是否稳定复现。\n"
        "2. 切换 Wi-Fi/移动网络后重试，排除链路抖动、DNS 或代理影响。\n"
        "3. 退出后重新登录，必要时清理缓存；如果在微信内打开，也建议重新进入。\n"
        "4. 如果只在某个页面报错，请记录触发动作和时间点，便于继续定位。\n\n"
        f"{prompt}\n\n可参考官方帮助中心：{HELP_CENTER_URL}"
    )


def execute_multimodal_chain(
    text: str,
    attachments: list[Attachment],
    perception: dict[str, Any],
    decision: RouteDecision,
    tool_map: dict[str, Any],
    trace: HarnessTrace,
) -> str:
    if not attachments:
        trace.fallback_reason = "missing_attachment"
        trace.check_result = {"attachment_present": False}
        return "请上传需要分析的截图、语音、视频或文件，我会结合内容继续判断。"

    attachment = attachments[-1]
    metadata = ""
    if "inspect_media_attachment" in tool_map:
        metadata = _invoke_tool(tool_map, trace, "inspect_media_attachment", {"file_url": attachment.url, "declared_type": attachment.type})

    confidence = str(perception.get("confidence", "")).lower()
    observations = normalize_message_text(str(perception.get("summary") or perception.get("observations") or ""))
    visible_text = normalize_message_text(str(perception.get("visible_text") or ""))
    suspected = normalize_message_text(str(perception.get("suspected_issue") or perception.get("suspected_symbol") or ""))
    if confidence in {"low", "very_low"} and not observations and not visible_text and not suspected:
        trace.fallback_reason = "low_multimodal_confidence"
        trace.check_result = {"attachment_present": True, "confidence": confidence or "low"}
        return "这张截图/附件里的关键信息不够清晰。请补充截图中想确认的具体位置或重新上传更清晰的图片。"

    query = build_multimodal_search_query(text, perception, decision.route, attachment.type)

    if "smart_search" not in tool_map:
        trace.check_result = {"attachment_present": True, "metadata": metadata}
        trace.answer_confidence = "medium"
        return format_customer_answer(
            "\n".join(part for part in [observations, visible_text, suspected] if part)
            or "已收到附件，但当前缺少可用的检索工具。请补充文字描述，我会继续协助判断。",
            heading="我先根据附件做初步判断：",
        )

    search = _invoke_tool(tool_map, trace, "smart_search", {"query": query, "depth": decision.search_depth or "deep"})
    trace.check_result = {
        "attachment_present": True,
        "attachment_type": attachment.type,
        "confidence": confidence or "medium",
        "metadata_checked": bool(metadata),
        "query": query,
    }
    trace.answer_confidence = "high" if not is_no_hit_text(search) else "medium"
    evidence = []
    if observations:
        evidence.append(f"附件识别：{observations}")
    if visible_text:
        evidence.append(f"可见文字：{visible_text}")
    if suspected:
        evidence.append(f"疑似对象：{suspected}")
    evidence.append(search)
    if decision.route == "chart_symbol":
        return _format_chart_symbol_answer(suspected=suspected, observations=observations, search_output=search)
    if is_multimodal_troubleshooting_signal(text, perception):
        return _format_multimodal_troubleshooting_answer(observations, visible_text, suspected, search)
    return format_customer_answer("\n\n".join(evidence), heading="结论需要结合截图识别和资料检索判断：")


def execute_planned_multimodal_chain(
    question: str,
    attachments: list[Attachment],
    perception: dict[str, Any],
    decision: RouteDecision,
    search_plan: list[dict[str, Any]],
    tool_map: dict[str, Any],
    trace: HarnessTrace,
) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
    if not attachments:
        trace.fallback_reason = "missing_attachment"
        trace.check_result = {"attachment_present": False}
        return "请上传需要分析的截图、语音、视频或文件，我会结合内容继续判断。", [], review_evidence_items([])

    attachment = attachments[-1]
    metadata = ""
    if "inspect_media_attachment" in tool_map:
        metadata = _invoke_tool(tool_map, trace, "inspect_media_attachment", {"file_url": attachment.url, "declared_type": attachment.type})

    confidence = str(perception.get("confidence", "")).lower()
    observations = normalize_message_text(str(perception.get("summary") or perception.get("observations") or ""))
    visible_text = normalize_message_text(str(perception.get("visible_text") or ""))
    suspected = normalize_message_text(str(perception.get("suspected_issue") or perception.get("suspected_symbol") or ""))
    if confidence in {"low", "very_low"} and not observations and not visible_text and not suspected:
        trace.fallback_reason = "low_multimodal_confidence"
        trace.check_result = {"attachment_present": True, "confidence": confidence or "low"}
        return "这张截图/附件里的关键信息不够清晰。请补充截图中想确认的具体位置或重新上传更清晰的图片。", [], review_evidence_items([])

    queries = search_plan or [{"query": build_multimodal_search_query(question, perception, decision.route, attachment.type), "depth": decision.search_depth or "deep", "hypothesis_id": "H1"}]
    outputs: list[str] = []
    evidence_items: list[dict[str, Any]] = []
    if "smart_search" in tool_map:
        for item in queries:
            query = str(item.get("query", "")).strip()
            depth = str(item.get("depth", "")).strip() or decision.search_depth or "deep"
            output = _invoke_tool(tool_map, trace, "smart_search", {"query": query, "depth": depth})
            outputs.append(output)
            evidence_items.append(
                {
                    "source_type": _guess_evidence_source_type(output),
                    "source_name": "smart_search",
                    "url": HELP_CENTER_URL if "helpcenter" in output.lower() else "",
                    "snippet": _extract_search_answer(output)[0][:240],
                    "supports": [str(item.get("hypothesis_id", "H1"))],
                    "conflicts": [],
                    "authority": 0.95 if "hifleet.com" in output.lower() else 0.75,
                    "relevance": 0.9 if not is_no_hit_text(output) else 0.4,
                    "query": query,
                    "depth": depth,
                }
            )
            if not is_no_hit_text(output) and _guess_evidence_source_type(output) in {"local_kb", "official_site", "official_community"}:
                break
    search = outputs[-1] if outputs else ""
    evidence_summary = review_evidence_items(evidence_items)
    trace.check_result = {
        "attachment_present": True,
        "attachment_type": attachment.type,
        "confidence": confidence or "medium",
        "metadata_checked": bool(metadata),
        "planned_queries": [item.get("query", "") for item in queries],
        "evidence_count": len(evidence_items),
    }
    trace.answer_confidence = evidence_summary["confidence"]
    if decision.route == "chart_symbol":
        return _format_chart_symbol_answer(suspected=suspected, observations=observations, search_output=search), evidence_items, evidence_summary
    if is_multimodal_troubleshooting_signal(question, perception):
        return _format_multimodal_troubleshooting_answer(observations, visible_text, suspected, search), evidence_items, evidence_summary
    return format_customer_answer("\n\n".join(part for part in [observations, visible_text, suspected, search] if part), heading="结论需要结合附件识别和资料检索判断："), evidence_items, evidence_summary


def _format_chart_symbol_answer(suspected: str, observations: str, search_output: str) -> str:
    text = f"{suspected} {observations}"
    if "安全水域" in text or ("红色" in text and "黑点" in text):
        return (
            "这个红色圆形、中间带黑点的图形，在 HiFleet 全球海图的符号语境下，可按“安全水域浮标（Safe Water Mark）”理解，属于助航标志，不是危险物标。\n\n"
            "详细说明：\n"
            "1. 含义：安全水域浮标通常表示该标志周边为可安全通行水域，常用于航道中线、深水航路中心、港口进出口航道起点或开阔水域参考点。\n"
            "2. 图形特征：常见表现为红白相间的浮标/灯标；在电子海图或平台图层里，可能被简化显示成红色圆点、中心点或小圆形符号。\n"
            "3. 使用提醒：它和危险物、沉船、障碍物等符号不同，不能直接理解为风险点；但实际航行仍应结合海图比例尺、周边水深、航道和公告信息判断。\n\n"
            "如果您需要，我也可以继续帮您整理 HiFleet 全球海图里常见航标、障碍物、锚地等符号的对照说明。"
        )
    if "锚地" in text or "锚泊" in text or "小圈圈" in text or "空心圆" in text:
        return (
            "图中的深色空心小圈圈，更可能是海图图层里的锚地/锚泊区域范围标识，用来提示该水域附近存在锚泊、候泊或相关管制区域。\n\n"
            "详细说明：\n"
            "1. 含义：这类圆圈通常不是单船目标，也不是普通 AIS 船舶图标，而是海图或平台图层对特定水域范围的标注。\n"
            "2. 为什么会出现很多个：近岸、港口外锚地、航道附近经常有多个锚泊区或管制区，打开相应海图/标注图层后会集中显示。\n"
            "3. 使用建议：如果您是在判断航线或靠离港风险，建议同时查看水深、航道、禁航/限航区、航行警告和船舶密度，不要只看圆圈本身下结论。\n\n"
            "如果您希望我进一步确认某一个圈的具体名称或范围，请提供截图中该圈附近的地名/坐标，或放大后再截一张图。"
        )
    return format_customer_answer(
        "\n\n".join(part for part in [f"截图识别：{observations}" if observations else "", f"疑似符号：{suspected}" if suspected else "", search_output] if part),
        heading="这个符号需要结合截图特征和海图资料判断：",
    )


def execute_file_chain(
    text: str,
    attachments: list[Attachment],
    decision: RouteDecision,
    tool_map: dict[str, Any],
    trace: HarnessTrace,
) -> str:
    file_attachments = [item for item in attachments if item.type in {"file", "image", "audio", "video"}]
    if not file_attachments:
        trace.fallback_reason = "missing_file"
        trace.check_result = {"file_present": False}
        return "请上传需要分析的文件，我会先识别文件类型和内容，再给出处理建议。"

    attachment = file_attachments[-1]
    if "inspect_customer_file" not in tool_map:
        trace.fallback_reason = "file_tool_missing"
        trace.check_result = {"file_present": True, "file_tool_available": False}
        return "已收到文件，但当前文件解析工具未启用。请补充文件类型和报错截图，我会先按排查流程协助。"

    raw = _invoke_tool(tool_map, trace, "inspect_customer_file", {"file_url": attachment.url})
    trace.check_result = {"file_present": True, "inspected": True}
    trace.answer_confidence = "medium"
    return format_customer_answer(
        "已收到并检查文件。下面是可用于排查的内容摘要：\n"
        f"{raw}\n\n"
        "如果需要生成报告或标注图，我会在产物生成后返回可访问链接；若缺少必要字段，会只追问一个关键问题。",
    )


def execute_browser_verify_chain(text: str, entities: MessageEntities, decision: RouteDecision, tool_map: dict[str, Any], trace: HarnessTrace) -> str:
    if not entities.urls:
        trace.fallback_reason = "browser_verify_missing_url"
        trace.check_result = {"url_present": False}
        if "smart_search" in tool_map:
            return execute_knowledge_chain(text, decision, tool_map, trace)
        return "请提供需要核验的公开网页链接，我会优先核查 HiFleet 官网和官方社区信息。"

    url = entities.urls[0]
    verified = ""
    if "verify_public_page" in tool_map:
        verified = _invoke_tool(tool_map, trace, "verify_public_page", {"url": url})
    search = ""
    if "smart_search" in tool_map:
        search = _invoke_tool(tool_map, trace, "smart_search", {"query": text, "depth": decision.search_depth or "normal"})
    trace.check_result = {"url_present": True, "verified": bool(verified), "searched": bool(search)}
    trace.answer_confidence = "high" if verified or search else "medium"
    return format_customer_answer("\n\n".join(part for part in [verified, search] if part), heading="已核验公开来源：")


def execute_simple_ship_chain(text: str, decision: RouteDecision, entities: MessageEntities, tool_map: dict[str, Any], trace: HarnessTrace) -> str:
    mmsi = entities.mmsi
    imo = entities.imo
    if not (mmsi or imo) and entities.ship_name:
        search = _invoke_tool(tool_map, trace, "ship_search", {"keyword": entities.ship_name})
        mmsi = _parse_first_mmsi(search)
        imo = _parse_first_imo(search)
        if not mmsi and not imo:
            trace.check_result = {"entity_resolved": False}
            trace.fallback_reason = "ship_identifier_missing"
            return search

    lower = text.lower()
    if "psc" in lower or "港口国监督" in lower:
        if not imo:
            trace.fallback_reason = "psc_requires_imo"
            return "查询 PSC 数据需要 IMO 编号。请提供 IMO，或先提供唯一船名/MMSI 以便补全。"
        out = _invoke_tool(tool_map, trace, "get_psc_records", {"imo": imo})
    elif "档案" in lower or "船舶信息" in lower or "archive" in lower or "profile" in lower:
        out = _invoke_tool(tool_map, trace, "get_ship_archive", {"mmsi": mmsi, "imo": imo})
    else:
        if not mmsi:
            trace.fallback_reason = "position_requires_mmsi"
            return "查询船位需要 MMSI。请提供 9 位 MMSI，或提供唯一船名以便搜索。"
        out = _invoke_tool(tool_map, trace, "get_ship_position", {"mmsi": mmsi})

    trace.check_result = {"entity_resolved": bool(mmsi or imo), "has_result": not is_no_hit_text(out)}
    trace.answer_confidence = "high" if not is_no_hit_text(out) else "medium"
    return out


def execute_stats_chain(text: str, entities: MessageEntities, tool_map: dict[str, Any], trace: HarnessTrace) -> str:
    lower = text.lower()
    if "红海绕航" in lower or "绕航" in lower:
        out = _invoke_tool(tool_map, trace, "get_avoid_redsea_traffic", {"startdate": entities.start_date, "enddate": entities.end_date})
    elif entities.strait or "海峡" in lower or "通航" in lower:
        out = _invoke_tool(
            tool_map,
            trace,
            "get_strait_traffic",
            {"strait_name": entities.strait, "startdate": entities.start_date, "enddate": entities.end_date},
        )
    elif "港口" in lower or " port" in f" {lower}":
        out = _invoke_tool(tool_map, trace, "search_ports", {"port_name": entities.port or text[:60], "port_code": ""})
    else:
        out = _invoke_tool(tool_map, trace, "get_area_traffic", {"area_name": entities.area, "bbox": entities.bbox, "area_id": ""})
    trace.check_result = {"has_result": not is_no_hit_text(out)}
    trace.answer_confidence = "high" if not is_no_hit_text(out) else "medium"
    return out


def execute_update_chain(text: str, entities: MessageEntities, tool_map: dict[str, Any], trace: HarnessTrace) -> str:
    mmsi = entities.mmsi
    ship_name = _clean_ship_name_candidate(entities.ship_name)
    if not mmsi and ship_name and "ship_search" in tool_map:
        search = _invoke_tool(tool_map, trace, "ship_search", {"keyword": ship_name})
        mmsi = _parse_first_mmsi(search)
    if not mmsi:
        trace.fallback_reason = "update_requires_mmsi"
        trace.check_result = {"entity_resolved": False}
        return "更新船舶信息需要明确 MMSI。请提供 9 位 MMSI 和需要更新的字段。"

    lower = text.lower()
    if "静态" in lower or "船名" in lower or "呼号" in lower or "尺度" in lower or "船型" in lower:
        args = {"mmsi": mmsi}
        ship_name_match = re.search(r"(?:船名|ship_name|name)[:：\s]*([A-Za-z0-9 ._-]{2,40})", text, flags=re.IGNORECASE)
        if ship_name_match:
            args["ship_name"] = ship_name_match.group(1).strip()
        out = _invoke_tool(tool_map, trace, "update_ship_static_info", args)
    else:
        args = {"mmsi": mmsi}
        field_patterns = {
            "lon": r"(?:经度|lon|longitude)[:：\s]*(-?\d+(?:\.\d+)?)",
            "lat": r"(?:纬度|lat|latitude)[:：\s]*(-?\d+(?:\.\d+)?)",
            "speed": r"(?:航速|speed)[:：\s]*(\d+(?:\.\d+)?)",
            "heading": r"(?:航首向|heading)[:：\s]*(\d+(?:\.\d+)?)",
            "course": r"(?:航迹向|course)[:：\s]*(\d+(?:\.\d+)?)",
        }
        for key, pattern in field_patterns.items():
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if match:
                args[key] = match.group(1)
        time_match = re.search(r"(?:更新时间|updatetime)[:：\s]*(20\d{2}-\d{1,2}-\d{1,2}(?:\s+\d{1,2}:\d{1,2}:\d{1,2})?)", text, flags=re.IGNORECASE)
        if time_match:
            args["updatetime"] = time_match.group(1)
        out = _invoke_tool(tool_map, trace, "upload_ship_position", args)
    trace.check_result = {"entity_resolved": True, "write_result": "成功" in out or "更新成功" in out}
    trace.answer_confidence = "high" if trace.check_result["write_result"] else "medium"
    return out


def execute_complex_ship_chain(text: str, entities: MessageEntities, tool_map: dict[str, Any], trace: HarnessTrace, max_loops: int = 2) -> str:
    """Explicit plan -> act -> check -> loop harness for multi-step ship tasks."""
    mmsi = entities.mmsi
    imo = entities.imo
    notes: list[str] = []
    default_start = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
    default_end = datetime.now().strftime("%Y-%m-%d")
    start_date = entities.start_date or default_start
    end_date = entities.end_date or default_end
    lower = text.lower()
    ask_trajectory = any(k in lower for k in ["轨迹", "历史轨迹"])
    ask_calls = any(k in lower for k in ["挂靠", "最近靠港", "上一港", "上次离港", "停靠"])
    ask_voyages = any(k in lower for k in ["航次", "目的港", "一致"])
    ask_stop = any(k in lower for k in ["当前停船", "停在哪", "锚泊"])
    followup_only = ask_calls and not ask_trajectory and not ask_voyages and not ask_stop and "船位" not in lower and "档案" not in lower

    for loop in range(max_loops + 1):
        trace.loop_count = loop
        if not mmsi and entities.ship_name:
            search = _invoke_tool(tool_map, trace, "ship_search", {"keyword": entities.ship_name})
            mmsi = _parse_first_mmsi(search)
            imo = imo or _parse_first_imo(search)
            notes.append("identify_ship: " + ("ok" if mmsi else "failed"))
        if not mmsi:
            trace.fallback_reason = "complex_ship_missing_mmsi"
            trace.check_result = {"entity_resolved": False, "loop": loop}
            return "这个船舶分析需要先确定唯一 MMSI。请提供 9 位 MMSI，或提供更精确船名。"

        base_profile = ""
        position = ""
        trajectory = ""
        calls = ""
        voyages = ""
        last_departure = ""
        current_stop = ""

        if "get_ship_archive" in tool_map:
            base_profile = _invoke_tool(tool_map, trace, "get_ship_archive", {"mmsi": mmsi, "imo": imo})
        position = _invoke_tool(tool_map, trace, "get_ship_position", {"mmsi": mmsi})

        if ask_trajectory:
            trajectory = _invoke_tool(
                tool_map,
                trace,
                "get_ship_trajectory",
                {"mmsi": mmsi, "starttime": start_date, "endtime": end_date},
            )
        if ask_calls:
            calls = _invoke_tool(
                tool_map,
                trace,
                "get_ship_call_ports",
                {"mmsi": mmsi, "starttime": start_date, "endtime": end_date},
            )
            last_departure = _invoke_tool(tool_map, trace, "get_last_departure", {"mmsi": mmsi})
        if ask_voyages:
            voyages = _invoke_tool(tool_map, trace, "get_ship_voyages", {"mmsi": mmsi, "starttime": start_date, "endtime": end_date})
        if ask_stop:
            current_stop = _invoke_tool(tool_map, trace, "get_current_stop", {"mmsi": mmsi})

        required_ok = not is_no_hit_text(position)
        consistency_notes: list[str] = []
        position_type = _extract_label_value(position, ["船型"])
        archive_type = _extract_label_value(base_profile, ["类型", "船型"])
        if position_type and archive_type and position_type != archive_type:
            consistency_notes.append(f"船型字段不一致：实时船位返回“{position_type}”，档案返回“{archive_type}”，应以档案为准并保留数据源差异。")
        consistency_ok = bool(position) and not consistency_notes
        trace.check_result = {
            "entity_resolved": True,
            "position_ok": required_ok,
            "consistency_ok": consistency_ok,
            "consistency_notes": consistency_notes,
            "loop": loop,
        }
        if required_ok or loop >= max_loops:
            trace.answer_confidence = "high" if required_ok else "medium"
            identity = f"MMSI: {mmsi}" + (f" | IMO: {imo}" if imo else "")
            parts = ["已按复杂船舶问题链路完成查询与校验。", identity]
            if not followup_only:
                parts.append("\n【当前船位】\n" + position)
            if base_profile and not is_no_hit_text(base_profile) and (ask_trajectory or ask_voyages or "档案" in lower):
                parts.append("\n【船舶档案摘要】\n" + base_profile[:1600])
            if trajectory:
                parts.append("\n【历史轨迹】\n" + _summarize_trajectory(trajectory))
            if last_departure:
                parts.append("\n【上一离港】\n" + _summarize_last_departure(last_departure))
            if calls:
                parts.append("\n【历史挂靠】\n" + _summarize_call_ports(calls))
            if voyages:
                parts.append("\n【航次/目的港校验】\n" + voyages[:1600])
            if current_stop:
                parts.append("\n【当前停船】\n" + current_stop)
            if consistency_notes:
                parts.append("\n【校验提示】\n" + "\n".join(consistency_notes))
            if not required_ok:
                parts.append("\n校验提示：实时船位结果较弱，已停止继续重试，建议稍后重试或补充时间范围。")
                trace.fallback_reason = "position_weak_after_retry"
            return "\n\n".join(parts)

    trace.fallback_reason = "max_loop_exceeded"
    return "复杂船舶查询达到重试上限，暂未获得足够可靠结果。请补充 MMSI 和时间范围后重试。"


def make_trace(decision: RouteDecision, entities: MessageEntities, session_id: str = "", run_id: str = "") -> HarnessTrace:
    return HarnessTrace(
        run_id=run_id or str(uuid.uuid4()),
        session_id=session_id,
        route=decision.route,
        task_type=decision.task_type,
        tool_bundle=list(decision.tool_bundle),
        entity_resolution=asdict(entities),
    )
