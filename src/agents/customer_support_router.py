"""Customer support routing and execution harness.

This module keeps the customer_support path fast by doing deterministic
classification before any LLM/tool-agent execution. The LLM still exists as a
fallback, but ordinary support and ship queries first receive a narrowed tool
bundle and an explicit execution plan.
"""
from __future__ import annotations

import logging
import json
import os
import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable
from urllib.parse import urlparse

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from llm_config import build_thinking_payload

logger = logging.getLogger(__name__)

HELP_CENTER_URL = "https://www.hifleet.com/helpcenter/?i18n=zh"
HIFLEET_CHART_ICON_GUIDE_URL = "https://www.hifleet.com/wp/communities/fleet/haitutubiaoshuoming"
HIFLEET_ACCOUNT_PAGE_HINT = "可在【关于】→【账号】里查看当前账号权限范围。"

TaskType = str
Route = str

KNOWLEDGE_BUNDLE = ["local_kb_search", "web_search", "web_search_agent_browser"]
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
HIFLEET_ACCOUNT_MARKERS = {"免费", "免费版", "免费用户", "免费账号", "基础版", "专业版", "账号", "权限", "会员"}
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
HIFLEET_POSITION_DISPLAY_MARKERS = {"船位", "最新船位", "实时船位", "位置", "不更新", "看不到最新"}
GENERIC_CONTEXT_TOKENS = {"如何", "怎么", "怎样", "为什么", "步骤", "查询"}
T1_EVAL_PROMPT = """你是 HiFleet 客服检索评估器。
只根据用户问题和 T1 检索结果，判断当前证据是否足够直接回答，还是必须升级到浏览器深挖。
只返回 JSON，不要输出解释性文本。

JSON 结构：
{
  "can_answer_now": true,
  "should_escalate_to_browser": false,
  "answer_basis": "kb|official_site|official_community|authoritative_public_data|insufficient",
  "best_urls": ["https://..."],
  "reason": "一句话说明",
  "confidence": "high|medium|low"
}
"""

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
    reasoning_trace: dict[str, Any] = field(default_factory=dict)


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


def _extract_ship_context_from_summary(text: str) -> tuple[str, str, str]:
    """Recover lightweight entity memory from compressed history summaries."""
    if "历史上下文摘要" not in (text or ""):
        return "", "", ""
    mmsi = ""
    imo = ""
    ship_name = ""
    mmsi_match = re.search(r"\bMMSI\s*[:：]?\s*(\d{9})\b", text, flags=re.IGNORECASE)
    imo_match = re.search(r"\bIMO\s*[:：]?\s*(\d{7})\b", text, flags=re.IGNORECASE)
    name_match = re.search(r"船名\s*[:：]?\s*([^，,；;\n]+)", text)
    if mmsi_match:
        mmsi = mmsi_match.group(1)
    if imo_match:
        imo = imo_match.group(1)
    if name_match:
        ship_name = _clean_ship_name_candidate(name_match.group(1))
    return ship_name, mmsi, imo


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
        elif isinstance(msg, SystemMessage):
            text = content_to_text(msg.content)
            source_type = "summary" if "历史上下文摘要" in text else "system"
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
            elif msg_type == "system" or role == "system":
                text = content_to_text(msg.get("content", ""))
                source_type = "summary" if "历史上下文摘要" in text else "system"

        if text:
            if source_type == "summary":
                summary_name, summary_mmsi, summary_imo = _extract_ship_context_from_summary(text)
                last_ship_name = summary_name or last_ship_name
                last_ship_mmsi = summary_mmsi or last_ship_mmsi
                last_ship_imo = summary_imo or last_ship_imo
                last_ship_source = text
                continue
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
    troubleshooting_markers = ["异常", "失败", "无法", "上传不了", "上传失败", "不显示", "不刷新", "更新慢", "更新很慢", "更新这么慢", "这么慢", "太慢", "延迟", "收不到", "报错", "告警", "报警", "加载失败", "卡顿", "网速", "死机"]
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
    if has_account_marker and any(marker in q for marker in HIFLEET_POSITION_DISPLAY_MARKERS):
        return RouteDecision("knowledge", "platform_knowledge", KNOWLEDGE_BUNDLE, "simple", search_depth="quick", reason="account position display knowledge")

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
    understanding_result: dict[str, Any] | None = None,
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
        primary_depth = decision.search_depth or "quick"
        plan: list[dict[str, Any]] = []
        for query in _knowledge_query_candidates_from_understanding(understanding_result, text, limit=5):
            plan.append({"hypothesis_id": "H1", "query": query, "depth": primary_depth, "source_priority": source_priority, "purpose": "从知识库和官方资料回答核心问题"})
            if len(plan) >= 5:
                break
        expansion_query = _generate_knowledge_expansion_query(text, decision, understanding_result)
        existing_queries = {normalize_message_text(str(item.get("query") or "")) for item in plan}
        if len(plan) < 5 and expansion_query and normalize_message_text(expansion_query) not in existing_queries:
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
    understanding_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    missing_slot = _planner_missing_slot(decision, entities, attachments, perception)
    question_type = _planner_question_type(decision)
    hypotheses = _planner_hypotheses(decision, perception)
    search_plan = _planner_search_plan(text, decision, perception, attachments, hypotheses, understanding_result)
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
    return any(
        marker in text
        for marker in (
            "未检索到足够可信",
            "未找到精确的FAQ匹配",
            "未找到",
            "暂无",
            "信息不足",
            "缺少可直接回答",
            "证据不足",
            "摘要不足",
        )
    )


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
    browser_payload = _parse_browser_evidence(text)
    if browser_payload:
        summary, links, _ = _browser_evidence_to_answer_parts(browser_payload)
        return summary, links
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


def _is_low_hifleet_context_device_complaint(question: str) -> bool:
    q = normalize_message_text(question).lower()
    device_markers = ["电脑", "死机", "网速", "卡", "卡顿", "浏览器崩溃", "打不开网页"]
    hifleet_markers = list(HIFLEET_FEATURE_MARKERS | HIFLEET_ACCOUNT_MARKERS) + ["页面", "按钮", "上传", "船位", "轨迹", "hifleet"]
    return any(marker in q for marker in device_markers) and not any(marker in q for marker in hifleet_markers)


def _build_authoritative_data_expansion_query(question: str) -> str:
    q = normalize_message_text(question)
    if not q:
        return ""
    if "长江水位" in q:
        return f"{q} 长江海事局 交通运输部"
    if "水位" in q:
        return f"{q} 官方公告"
    if any(marker in q for marker in ["指数", "行情", "运价"]):
        return f"{q} 官方数据"
    return q


def _understanding_primary_query(understanding_result: dict[str, Any] | None, fallback_text: str) -> str:
    result = dict(understanding_result or {})
    candidates = result.get("search_query_candidates")
    if isinstance(candidates, list):
        for item in candidates:
            query = normalize_message_text(str(item or ""))
            if query:
                return query
    return _rewrite_hifleet_knowledge_query(fallback_text)


def _understanding_summary_for_trace(understanding_result: dict[str, Any] | None) -> dict[str, Any]:
    result = dict(understanding_result or {})
    candidates = list(result.get("search_query_candidates", []) or [])
    return {
        "query_type": str(result.get("query_type", "")),
        "rewritten_user_need": str(result.get("rewritten_user_need", "")),
        "search_keywords": list(result.get("search_keywords", []) or []),
        "understanding_primary_query": str(candidates[0] if candidates else ""),
        "should_prefer_local_kb": bool(result.get("should_prefer_local_kb")),
        "should_limit_to_hifleet_sites": bool(result.get("should_limit_to_hifleet_sites")),
    }


def _rewrite_hifleet_knowledge_query(question: str) -> str:
    """Rewrite query to add HiFleet product context for better KB retrieval."""
    q = normalize_message_text(question)
    if not q:
        return q
    if _looks_like_authoritative_data_query(q):
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


def _generate_knowledge_expansion_query(text: str, decision: RouteDecision, understanding_result: dict[str, Any] | None = None) -> str:
    """Generate a complementary search query from a different angle."""
    result = dict(understanding_result or {})
    candidates = list(result.get("search_query_candidates", []) or [])
    if len(candidates) >= 2:
        query = normalize_message_text(str(candidates[1] or ""))
        if query:
            return query
    q = normalize_message_text(text).lower()
    if _looks_like_authoritative_data_query(text):
        return _build_authoritative_data_expansion_query(text)
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


def _knowledge_query_candidates_from_understanding(understanding_result: dict[str, Any] | None, fallback_text: str, limit: int = 5) -> list[str]:
    result = dict(understanding_result or {})
    candidates: list[str] = []
    raw_candidates = result.get("search_query_candidates")
    if isinstance(raw_candidates, list):
        for item in raw_candidates:
            query = normalize_message_text(str(item or ""))
            if query and query not in candidates:
                candidates.append(query)
    fallback_query = _understanding_primary_query(result, fallback_text)
    if fallback_query and fallback_query not in candidates:
        candidates.append(fallback_query)
    return candidates[: max(1, limit)]


def _merge_knowledge_search_plan(
    question: str,
    decision: RouteDecision,
    search_plan: list[dict[str, Any]],
    understanding_result: dict[str, Any],
    default_depth: str,
) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []

    def add(query: str, *, depth: str = "", hypothesis_id: str = "H1", purpose: str = "回答当前问题", source_priority: list[str] | None = None) -> None:
        normalized_query = normalize_message_text(query)
        if not normalized_query:
            return
        if any(normalize_message_text(str(item.get("query") or "")) == normalized_query for item in merged):
            return
        resolved_depth = (depth or default_depth or "normal").strip().lower()
        if resolved_depth not in {"quick", "normal", "deep"}:
            resolved_depth = default_depth or "normal"
        merged.append(
            {
                "hypothesis_id": hypothesis_id or "H1",
                "query": normalized_query,
                "depth": resolved_depth,
                "source_priority": list(source_priority or ["local_kb", "official_site", "official_community", "public_web"]),
                "purpose": purpose or "回答当前问题",
            }
        )

    for item in search_plan or []:
        if not isinstance(item, dict):
            continue
        add(
            str(item.get("query") or ""),
            depth=str(item.get("depth") or ""),
            hypothesis_id=str(item.get("hypothesis_id") or "H1"),
            purpose=str(item.get("purpose") or "回答当前问题"),
            source_priority=list(item.get("source_priority") or []),
        )
        if len(merged) >= 5:
            break

    for query in _knowledge_query_candidates_from_understanding(understanding_result, question, limit=5):
        add(query, depth=default_depth, hypothesis_id="H1", purpose="多关键词补充检索")
        if len(merged) >= 5:
            break

    expansion_query = _generate_knowledge_expansion_query(question, decision, understanding_result)
    if len(merged) < 5:
        add(expansion_query, depth="normal", hypothesis_id="H2", purpose="补充产品能力、步骤或常见问题")

    if not merged:
        add(_rewrite_hifleet_knowledge_query(question), depth=default_depth)
    return merged[:5]


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

    if ("免费" in q or "免费版" in q or "免费用户" in q) and any(marker in q for marker in ["最新船位", "实时船位", "看不到", "不显示", "船位"]):
        return (
            "免费账号在网站上看不到最新船位，通常是因为船位数据存在延迟或部分实时能力受账号权限限制。\n\n"
            "建议您先这样确认：\n"
            "1. 刷新页面或重新搜索目标船舶，确认不是页面缓存。\n"
            "2. 等待一段时间后再查看，免费版显示的数据可能不是完全实时。\n"
            "3. 到【关于】→【账号】查看当前账号权限范围。\n\n"
            "如果同一条船长时间没有任何更新，请提供船名或 MMSI，我再帮您核查是否是 AIS 信号或船舶数据问题。"
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
    evidence_links = [str(item.get("url", "")).strip() for item in (evidence_items or []) if str(item.get("url", "")).strip()]
    for link in evidence_links:
        if link not in links:
            links.append(link)
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
            answer_parts.append(summary)
        elif any(marker in q for marker in ["如何", "怎么", "怎样", "为什么"]):
            answer_parts.append(summary)
        else:
            answer_parts.append(summary)
        # 补充信息（来自多源证据综合）
        if supplementary_info:
            answer_parts.append(f"\n\n补充说明：\n{supplementary_info}")
        # 官方链接引导
        official_links = [link for link in links if "hifleet.com" in link]
        if official_links:
            answer_parts.append(f"\n\n参考链接：{official_links[0]}")
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


def _read_structured_search_trace(query: str, depth: str) -> dict[str, Any]:
    try:
        from skills.knowledge_qa.tools import get_structured_search_trace

        return get_structured_search_trace(query, depth)
    except Exception:
        return {}


def _looks_like_specific_content_url(url: str) -> bool:
    lowered = (url or "").lower()
    if not lowered.startswith(("http://", "https://")):
        return False
    generic_endings = (
        "/",
        "/wp/communities",
        "/wp/community/",
        "/helpcenter/",
        "/helpcenter/?i18n=zh",
        "/helpcenter/?i18n=en",
        "/data/index.html",
        "/account/index.html?type=account",
    )
    if any(lowered.endswith(item) for item in generic_endings):
        return False
    if lowered.count("/") <= 2:
        return False
    return True


def _is_official_hifleet_url(url: str) -> bool:
    host = (urlparse(url or "").hostname or "").lower()
    return host == "hifleet.com" or host.endswith(".hifleet.com")


def _looks_like_directory_only_item(item: dict[str, Any]) -> bool:
    url = str(item.get("url", "")).strip()
    snippet = normalize_message_text(str(item.get("snippet") or item.get("summary") or ""))
    title = normalize_message_text(str(item.get("title") or ""))
    return (
        (not _looks_like_specific_content_url(url))
        or any(marker in url.lower() for marker in ["/wp/communities", "/helpcenter/?i18n=", "/swgg/"])
        or any(marker in snippet for marker in ["入口", "首页", "列表", "目录"])
        or any(marker in title for marker in ["官方社区", "帮助中心", "水位公告"])
    )


def _looks_like_hifleet_product_query(question: str) -> bool:
    q = normalize_message_text(question).lower()
    product_markers = ["hifleet", "视频监控", "监控", "岸基", "点验", "船队", "筛选", "功能", "产品", "社区"]
    return any(marker in q for marker in product_markers)


def _looks_like_authoritative_data_query(question: str) -> bool:
    q = normalize_message_text(question).lower()
    return any(marker in q for marker in ["今日", "今天", "最新", "长江水位", "指数", "水位", "行情", "运价"])


def _contains_specific_fact(text: str) -> bool:
    lowered = normalize_message_text(text).lower()
    return bool(
        re.search(r"\b20\d{2}-\d{1,2}-\d{1,2}\b", lowered)
        or re.search(r"\b\d+(?:\.\d+)?\b", lowered)
        or any(marker in lowered for marker in ["可查看", "支持", "记忆", "权限", "价格", "场景", "接入"])
    )


def _json_object_from_text(text: str) -> dict[str, Any]:
    raw = (text or "").strip()
    if not raw:
        return {}
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else {}
    except Exception:
        match = re.search(r"\{.*\}", raw, flags=re.DOTALL)
        if not match:
            return {}
        try:
            value = json.loads(match.group(0))
            return value if isinstance(value, dict) else {}
        except Exception:
            return {}


def _invoke_t1_eval_llm(question: str, structured_trace: dict[str, Any], evidence_items: list[dict[str, Any]]) -> dict[str, Any]:
    api_key = os.getenv("COZE_WORKLOAD_IDENTITY_API_KEY", "").strip()
    base_url = os.getenv("COZE_INTEGRATION_MODEL_BASE_URL", "").strip()
    if not api_key or not base_url:
        return {}
    items = []
    for item in (structured_trace.get("items") or [])[:5]:
        if not isinstance(item, dict):
            continue
        items.append(
            {
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "summary": item.get("summary", ""),
                "snippet": item.get("snippet", ""),
                "authority_level": item.get("authority_level"),
                "authority_desc": item.get("authority_desc", ""),
            }
        )
    if not items:
        return {}
    llm = ChatOpenAI(
        model=os.getenv("HIFLEET_T1_EVAL_MODEL", "deepseek-v4-flash-260425"),
        api_key=api_key,
        base_url=base_url,
        temperature=0.1,
        streaming=False,
        timeout=60,
        extra_body={"thinking": build_thinking_payload("disabled")},
    )
    payload = {
        "question": question,
        "search_summary": structured_trace.get("summary", ""),
        "items": items,
        "evidence_count": len(evidence_items),
    }
    try:
        raw = llm.invoke(
            [
                {"role": "system", "content": T1_EVAL_PROMPT},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ]
        )
    except Exception:
        return {}
    content = getattr(raw, "content", raw)
    if isinstance(content, list):
        content = "\n".join(str(item.get("text", "")) if isinstance(item, dict) else str(item) for item in content)
    return _json_object_from_text(str(content or ""))


def _evaluate_t1_results(
    question: str,
    decision: RouteDecision,
    outputs: list[str],
    evidence_items: list[dict[str, Any]],
    structured_trace: dict[str, Any],
) -> dict[str, Any]:
    items = [item for item in list(structured_trace.get("items") or []) if isinstance(item, dict)]
    official_specific_urls = [
        str(item.get("url", "")).strip()
        for item in items
        if _is_official_hifleet_url(str(item.get("url", ""))) and _looks_like_specific_content_url(str(item.get("url", "")))
    ]
    authoritative_specific_urls = [
        str(item.get("url", "")).strip()
        for item in items
        if int(item.get("authority_level") or 0) == 1 and _looks_like_specific_content_url(str(item.get("url", "")))
    ]
    snippet_text = "\n".join(
        normalize_message_text(str(item.get("summary") or item.get("snippet") or ""))
        for item in items[:5]
    )
    directory_only = bool(items) and all(_looks_like_directory_only_item(item) for item in items[:3])
    long_tail_hifleet = _looks_like_hifleet_product_query(question) and any(marker in normalize_message_text(question) for marker in ["记忆", "筛选", "详细内容", "验证", "核验"])
    data_query = _looks_like_authoritative_data_query(question)

    if data_query and authoritative_specific_urls and _contains_specific_fact(snippet_text):
        return {
            "decision": "short_circuit",
            "reason": "authoritative public data contains specific facts",
            "best_urls": authoritative_specific_urls[:3],
            "fallback_reason": "t1_short_circuit_authoritative",
            "confidence": "high",
            "used_llm": False,
        }
    if official_specific_urls and _contains_specific_fact(snippet_text):
        return {
            "decision": "short_circuit",
            "reason": "official hifleet page contains specific details",
            "best_urls": official_specific_urls[:3],
            "fallback_reason": "t1_short_circuit_hifleet_official",
            "confidence": "high",
            "used_llm": False,
        }
    if directory_only:
        return {
            "decision": "escalate",
            "reason": "only directory or entry pages were found",
            "best_urls": [str(item.get("url", "")).strip() for item in items[:3] if str(item.get("url", "")).strip()],
            "fallback_reason": "t1_escalate_directory_only",
            "confidence": "high",
            "used_llm": False,
        }
    if long_tail_hifleet and (not official_specific_urls or not _contains_specific_fact(snippet_text)):
        return {
            "decision": "escalate",
            "reason": "hifleet long-tail feature lacks concrete detail",
            "best_urls": [str(item.get("url", "")).strip() for item in items[:3] if str(item.get("url", "")).strip()],
            "fallback_reason": "t1_escalate_insufficient_detail",
            "confidence": "high",
            "used_llm": False,
        }
    llm_result = _invoke_t1_eval_llm(question, structured_trace, evidence_items)
    if llm_result:
        should_escalate = bool(llm_result.get("should_escalate_to_browser"))
        can_answer = bool(llm_result.get("can_answer_now"))
        best_urls = [str(item).strip() for item in llm_result.get("best_urls", []) if str(item).strip()]
        if should_escalate and not can_answer:
            return {
                "decision": "escalate",
                "reason": str(llm_result.get("reason", "")) or "llm eval requested browser escalation",
                "best_urls": best_urls,
                "fallback_reason": "t1_escalate_llm_eval",
                "confidence": str(llm_result.get("confidence", "medium")),
                "used_llm": True,
            }
        if can_answer:
            return {
                "decision": "short_circuit",
                "reason": str(llm_result.get("reason", "")) or "llm eval accepted T1 evidence",
                "best_urls": best_urls,
                "fallback_reason": "t1_short_circuit_llm_eval",
                "confidence": str(llm_result.get("confidence", "medium")),
                "used_llm": True,
            }
    if all(is_no_hit_text(output) for output in outputs):
        return {
            "decision": "escalate",
            "reason": "all T1 outputs were weak or empty",
            "best_urls": [],
            "fallback_reason": "smart_search_empty_agent_browser_fallback",
            "confidence": "medium",
            "used_llm": False,
        }
    return {
        "decision": "short_circuit",
        "reason": "T1 evidence is usable without browser",
        "best_urls": [str(item.get("url", "")).strip() for item in items[:2] if str(item.get("url", "")).strip()],
        "fallback_reason": "t1_short_circuit_default",
        "confidence": "medium",
        "used_llm": False,
    }


def _invoke_tool(tool_map: dict[str, Any], trace: HarnessTrace, name: str, args: dict[str, Any]) -> str:
    t0 = time.time()
    trace.tool_call_sequence.append(name)
    tool = tool_map[name]
    result = tool.invoke(args)
    trace.latency_hotspot[name] = int((time.time() - t0) * 1000)
    return str(result)


def _format_local_kb_payload(payload: dict[str, Any]) -> str:
    if not payload.get("can_answer"):
        return "未检索到足够可信的信息：本地知识库仅命中补充参考，不能直接回答当前问题。"
    try:
        from skills.knowledge_qa.local_kb_runtime import format_local_kb_response

        return format_local_kb_response(payload, HELP_CENTER_URL)
    except Exception:
        items = list(payload.get("items") or [])
        if not items:
            return str(payload.get("summary") or "本地知识库未命中可直接回答的结果。")
        parts = ["【优先匹配 - FAQ/标准回复】" if payload.get("can_answer") else "【主题说明（补充参考）】"]
        for item in items[:2]:
            content = normalize_message_text(str(item.get("content") or item.get("summary") or item.get("snippet") or ""))
            if content:
                parts.append(content)
        return "\n".join(parts).strip()


def _format_structured_web_payload(payload: dict[str, Any]) -> str:
    items = [item for item in list(payload.get("items") or []) if isinstance(item, dict)]
    parts: list[str] = []
    summary = normalize_message_text(str(payload.get("summary") or ""))
    if summary and summary not in {"未命中足够具体的资料", "当前结果存在站点污染或聚合页噪音，建议调整 query 或过滤条件"}:
        parts.append(summary)
    for item in items[:3]:
        title = normalize_message_text(str(item.get("title") or ""))
        snippet = normalize_message_text(str(item.get("summary") or item.get("snippet") or ""))
        url = str(item.get("url") or "").strip()
        line_parts = [part for part in [title, snippet[:260]] if part]
        if line_parts:
            parts.append("：".join(line_parts))
        if url:
            parts.append(url)
    if not parts:
        return "未检索到足够可信的信息"
    return "\n".join(parts).strip()


def _format_browser_bridge_payload(payload: dict[str, Any]) -> str:
    browser_payload = _browser_bridge_to_legacy_payload(payload)
    if not browser_payload:
        return "未检索到足够可信的信息"
    summary, links, _ = _browser_evidence_to_answer_parts(browser_payload)
    return "\n".join(part for part in [summary, *links[:2]] if part).strip() or "未检索到足够可信的信息"


def _output_from_structured_payload(payload: dict[str, Any], fallback_output: str) -> str:
    tool_name = str(payload.get("tool") or "")
    if tool_name == "local_kb_search":
        return _format_local_kb_payload(payload)
    if tool_name == "web_search":
        return _format_structured_web_payload(payload)
    if tool_name == "web_search_agent_browser":
        return _format_browser_bridge_payload(payload)
    return fallback_output


def _evidence_items_from_structured_payload(
    payload: dict[str, Any],
    *,
    source_name: str,
    query: str,
    depth: str,
    hypothesis_id: str = "H1",
    purpose: str = "",
) -> list[dict[str, Any]]:
    tool_name = str(payload.get("tool") or source_name)
    if not payload.get("can_answer") and tool_name == "local_kb_search":
        return []
    if tool_name == "web_search_agent_browser":
        browser_payload = _browser_bridge_to_legacy_payload(payload)
        if browser_payload:
            evidence_items = _evidence_items_from_tool_output(
                json.dumps(browser_payload, ensure_ascii=False),
                source_name=source_name,
                query=query,
                depth=depth,
                hypothesis_id=hypothesis_id,
                purpose=purpose or "受控 HiFleet 官方公开页面核验",
            )
            for item in evidence_items:
                item["source_name"] = source_name
            return evidence_items
    items = [item for item in list(payload.get("items") or []) if isinstance(item, dict)]
    if not items:
        if not payload.get("can_answer"):
            return []
        return _evidence_items_from_tool_output(
            _output_from_structured_payload(payload, ""),
            source_name=source_name,
            query=query,
            depth=depth,
            hypothesis_id=hypothesis_id,
            purpose=purpose,
        )
    evidence_items: list[dict[str, Any]] = []
    for item in items[:5]:
        url = str(item.get("url") or item.get("source") or "").strip()
        source_type = str(item.get("source_type") or "")
        if not source_type:
            if item.get("is_hifleet_official"):
                source_type = "official_site"
            elif "wp/communit" in url:
                source_type = "official_community"
            elif "hifleet.com" in url:
                source_type = "official_site"
            elif tool_name == "local_kb_search":
                source_type = "local_kb"
            else:
                source_type = "public_web"
        snippet = normalize_message_text(str(item.get("content") or item.get("summary") or item.get("snippet") or ""))
        authority = float(item.get("authority") or (0.95 if source_type in {"local_kb", "official_site", "official_community"} else 0.6))
        relevance = float(item.get("score") or item.get("relevance") or (0.9 if payload.get("can_answer") else 0.65))
        evidence_items.append(
            {
                "source_type": source_type,
                "source_name": source_name,
                "url": url if url.startswith(("http://", "https://")) else "",
                "snippet": snippet[:240],
                "supports": [hypothesis_id],
                "conflicts": [],
                "authority": authority,
                "relevance": relevance,
                "query": query,
                "depth": depth,
                "purpose": purpose,
                "title": str(item.get("title") or ""),
            }
        )
    return evidence_items


def _new_knowledge_retrieval_trace(understanding_summary: dict[str, Any], query: str) -> dict[str, Any]:
    return {
        "understanding_query_type": understanding_summary.get("query_type", ""),
        "understanding_keywords": list(understanding_summary.get("search_keywords", []) or []),
        "understanding_primary_query": understanding_summary.get("understanding_primary_query", ""),
        "understanding_rewritten_need": understanding_summary.get("rewritten_user_need", ""),
        "t0_kb_hit": False,
        "t0_can_answer": False,
        "t0_result_count": 0,
        "t1_query": query,
        "t1_payload_meta": {},
        "t1_source_count": 0,
        "t1_official_source_count": 0,
        "t1_used_ark_fallback": False,
        "t1_can_answer": False,
        "t1_continue_with": "",
        "t1_eval_decision": "",
        "t1_eval_reason": "",
        "t2_triggered": False,
        "t2_tool": "",
        "t2_target_urls": [],
        "layers": [],
    }


def _append_layer(retrieval_trace: dict[str, Any], layer: str, payload: dict[str, Any]) -> None:
    payload_trace = dict(payload.get("trace") or {})
    source_breakdown = dict(payload_trace.get("source_breakdown") or {})
    risk_flags = list(payload_trace.get("risk_flags") or [])
    retrieval_trace.setdefault("layers", []).append(
        {
            "layer": layer,
            "tool": str(payload.get("tool") or ""),
            "can_answer": bool(payload.get("can_answer")),
            "should_continue": bool(payload.get("should_continue")),
            "continue_with": str(payload.get("continue_with") or ""),
            "status": str(payload.get("status") or ""),
            "result_count": len(list(payload.get("items") or payload.get("pages") or [])),
            "faq_count": int(source_breakdown.get("faq") or 0),
            "wiki_count": int(source_breakdown.get("wiki") or 0),
            "risk_flags": risk_flags,
            "answerability_reason": str(payload_trace.get("web_answerability_reason") or payload.get("summary") or ""),
        }
    )


def _trace_snapshot(trace_data: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in dict(trace_data or {}).items() if key not in {"query_traces"}}


def _question_needs_step_complete_answer(question: str) -> bool:
    q = normalize_message_text(question).lower()
    action_markers = ["绘制", "创建", "设置", "添加", "编辑", "保存", "配置", "上传", "导出"]
    howto_markers = ["怎么", "如何", "怎样", "步骤", "教程", "入口", "在哪", "操作"]
    return any(marker in q for marker in action_markers) or (
        any(marker in q for marker in howto_markers)
        and any(marker in q for marker in ["标注", "电子围栏", "报警", "航线", "船队", "账号"])
    )


def _answer_has_step_completeness(text: str) -> bool:
    value = normalize_message_text(text)
    has_entry = any(marker in value for marker in ["入口", "右上角", "进入", "打开", "页面", "标注", "我的标注", "更多"])
    has_action = any(marker in value for marker in ["点击", "选择", "拖动", "绘制", "填写", "添加", "编辑", "设置"])
    has_finish = any(marker in value for marker in ["保存", "确定", "完成", "结束", "闭合", "生效"])
    return has_entry and has_action and has_finish


def _build_conservative_step_answer(question: str, evidence_items: list[dict[str, Any]]) -> str:
    links = []
    for item in evidence_items:
        url = str(item.get("url") or "").strip()
        if url and url not in links:
            links.append(url)
    suffix = f"\n\n可先参考官方帮助中心：{HELP_CENTER_URL}"
    if links:
        suffix = f"\n\n我能先给您一个可核验的官方链接：{links[0]}"
    return (
        "目前检索到的信息还不足以确认完整操作步骤，因此我不能直接把它整理成标准教程。\n\n"
        "我只能先确认这是 HiFleet 平台功能相关问题；如果您方便，请补充当前所在页面或截图，我再按页面入口和按钮继续核验。"
        + suffix
    )


def _has_high_confidence_step_evidence(evidence_items: list[dict[str, Any]]) -> bool:
    for item in evidence_items:
        if str(item.get("source_name") or "") in {"local_kb_search", "web_search", "web_search_agent_browser"} and float(item.get("relevance") or 0.0) >= 0.85:
            snippet = normalize_message_text(str(item.get("snippet") or ""))
            if _answer_has_step_completeness(snippet) or any(marker in snippet for marker in ["点击", "选择", "保存", "导出", "设置", "添加"]):
                return True
    return False


def _ensure_step_answer_completeness(question: str, answer: str, evidence_items: list[dict[str, Any]]) -> str:
    if not _question_needs_step_complete_answer(question):
        return answer
    if _answer_has_step_completeness(answer):
        return answer
    if _has_high_confidence_step_evidence(evidence_items):
        return answer
    return _build_conservative_step_answer(question, evidence_items)


def _invoke_three_layer_knowledge_chain(
    question: str,
    *,
    query: str,
    depth: str,
    decision: RouteDecision,
    tool_map: dict[str, Any],
    trace: HarnessTrace,
    understanding_summary: dict[str, Any],
    hypothesis_id: str = "H1",
    purpose: str = "回答当前客服问题",
) -> tuple[list[str], list[dict[str, Any]], dict[str, Any]]:
    outputs: list[str] = []
    evidence_items: list[dict[str, Any]] = []
    retrieval_trace = _new_knowledge_retrieval_trace(understanding_summary, query)
    site_hint = "hifleet.com" if understanding_summary.get("should_limit_to_hifleet_sites") or _looks_like_hifleet_product_query(question) else ""

    local_payload: dict[str, Any] = {}
    local_output = ""
    if "local_kb_search" in tool_map:
        raw_local = _invoke_tool(tool_map, trace, "local_kb_search", {"query": query})
        local_payload = _parse_tool_json(raw_local)
        local_output = _output_from_structured_payload(local_payload, raw_local)
        outputs.append(local_output)
        evidence_items.extend(
            _evidence_items_from_structured_payload(
                local_payload,
                source_name="local_kb_search",
                query=query,
                depth="local",
                hypothesis_id=hypothesis_id,
                purpose=purpose,
            )
        )
        _append_layer(retrieval_trace, "T0", local_payload)
        retrieval_trace["t0_kb_hit"] = bool((local_payload.get("items") or []))
        retrieval_trace["t0_can_answer"] = bool(local_payload.get("can_answer"))
        retrieval_trace["t0_result_count"] = len(list(local_payload.get("items") or []))
        if local_payload.get("can_answer"):
            retrieval_trace["t1_eval_decision"] = "short_circuit"
            retrieval_trace["t1_eval_reason"] = "local_kb_search can_answer=true"
            return outputs, evidence_items, retrieval_trace
    elif "smart_search" in tool_map:
        # Compatibility path for old tests/callers that inject only smart_search.
        raw = _invoke_tool(tool_map, trace, "smart_search", {"query": query, "depth": depth})
        outputs.append(raw)
        evidence_items.extend(
            _evidence_items_from_tool_output(
                raw,
                source_name="smart_search",
                query=query,
                depth=depth,
                hypothesis_id=hypothesis_id,
                purpose=purpose,
            )
        )
        retrieval_trace["t1_eval_decision"] = "compat_smart_search"
        retrieval_trace["t1_eval_reason"] = "three-layer tools unavailable"
        return outputs, evidence_items, retrieval_trace

    web_payload: dict[str, Any] = {}
    if "web_search" in tool_map:
        search_type = "web_summary" if depth == "deep" else "web"
        web_args = {"query": query, "search_type": search_type}
        if site_hint:
            web_args["sites"] = site_hint
        raw_web = _invoke_tool(tool_map, trace, "web_search", web_args)
        web_payload = _parse_tool_json(raw_web)
        web_output = _output_from_structured_payload(web_payload, raw_web)
        outputs.append(web_output)
        evidence_items.extend(
            _evidence_items_from_structured_payload(
                web_payload,
                source_name="web_search",
                query=str(web_payload.get("query") or query),
                depth=search_type,
                hypothesis_id=hypothesis_id,
                purpose=purpose,
            )
        )
        _append_layer(retrieval_trace, "T1", web_payload)
        web_trace = dict(web_payload.get("trace") or {})
        result_profile = dict(web_trace.get("result_profile") or {})
        retrieval_trace.update(
            {
                "t1_query": str(web_payload.get("query") or query),
                "t1_payload_meta": dict(web_trace.get("request_profile") or {}),
                "t1_source_count": int(result_profile.get("result_count") or len(list(web_payload.get("items") or []))),
                "t1_official_source_count": int(result_profile.get("official_count") or result_profile.get("authoritative_count") or 0),
                "t1_used_ark_fallback": bool(web_trace.get("used_ark_fallback")),
                "t1_can_answer": bool(web_payload.get("can_answer")),
                "t1_continue_with": str(web_payload.get("continue_with") or ""),
                "t2_target_urls": list(web_payload.get("best_urls") or []),
            }
        )
        if web_payload.get("can_answer"):
            retrieval_trace["t1_eval_decision"] = "short_circuit"
            retrieval_trace["t1_eval_reason"] = "web_search can_answer=true"
            return outputs, evidence_items, retrieval_trace

    should_browser = (
        str(web_payload.get("continue_with") or "") == "agent_browser"
        and bool(web_payload.get("best_urls"))
        and "web_search_agent_browser" in tool_map
    )
    if should_browser:
        retrieval_trace["t2_triggered"] = True
        retrieval_trace["t2_tool"] = "web_search_agent_browser"
        browser_output_raw = _invoke_tool(
            tool_map,
            trace,
            "web_search_agent_browser",
            {
                "query": query,
                "target_urls": "\n".join(str(url) for url in list(web_payload.get("best_urls") or [])),
                "site_hint": site_hint,
            },
        )
        browser_payload = _parse_tool_json(browser_output_raw)
        browser_output = _output_from_structured_payload(browser_payload, browser_output_raw)
        outputs.append(browser_output)
        evidence_items.extend(
            _evidence_items_from_structured_payload(
                browser_payload,
                source_name="web_search_agent_browser",
                query=query,
                depth="browser",
                hypothesis_id=hypothesis_id,
                purpose="受控公开页面正文核验",
            )
        )
        _append_layer(retrieval_trace, "T2", browser_payload)
        retrieval_trace["t1_eval_decision"] = "browser_escalated"
        retrieval_trace["t1_eval_reason"] = str(web_payload.get("summary") or "web_search requested agent_browser")
        return outputs, evidence_items, retrieval_trace

    if not retrieval_trace.get("t1_eval_decision"):
        if outputs and all(is_no_hit_text(item) for item in outputs):
            retrieval_trace["t1_eval_decision"] = "no_hit"
            retrieval_trace["t1_eval_reason"] = "three-layer search returned weak results"
        else:
            retrieval_trace["t1_eval_decision"] = "short_circuit"
            retrieval_trace["t1_eval_reason"] = "available evidence selected without browser escalation"
    return outputs, evidence_items, retrieval_trace


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


def _parse_tool_json(output: str) -> dict[str, Any]:
    try:
        value = json.loads(output or "")
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _parse_browser_evidence(text: str) -> dict[str, Any] | None:
    payload = _parse_json_blob(text)
    if not payload or payload.get("type") != "hifleet_browser_evidence":
        return None
    pages = payload.get("pages")
    if not isinstance(pages, list) or not pages:
        return None
    return payload


def _browser_bridge_to_legacy_payload(payload: dict[str, Any]) -> dict[str, Any]:
    pages = list(payload.get("pages") or [])
    if not pages:
        return {}
    return {
        "type": "hifleet_browser_evidence",
        "query": payload.get("query", ""),
        "pages": pages,
    }


def _browser_evidence_to_answer_parts(payload: dict[str, Any]) -> tuple[str, list[str], list[dict[str, Any]]]:
    pages = [page for page in payload.get("pages", []) if isinstance(page, dict)]
    summary_parts: list[str] = []
    links: list[str] = []
    evidence_items: list[dict[str, Any]] = []
    for page in pages[:3]:
        title = normalize_message_text(str(page.get("title") or "HiFleet 官方页面"))
        url = str(page.get("url") or "").strip()
        excerpt = normalize_message_text(str(page.get("excerpt") or ""))
        official = bool(page.get("official"))
        source_query = normalize_message_text(str(page.get("source_query") or payload.get("query") or ""))
        screenshot_path = str(page.get("screenshot_path") or "").strip()
        image_count = int(page.get("image_count") or 0)
        if not excerpt:
            continue
        source_type = "official_community" if "wp/communit" in url else "official_site" if official else "public_web"
        line = f"{title}：{excerpt[:260]}"
        if image_count >= 3:
            line += "（页面包含较多图片）"
        summary_parts.append(line)
        if url and url not in links:
            links.append(url)
        evidence_items.append(
            {
                "source_type": source_type,
                "source_name": "agent_browser_deep_search",
                "url": url,
                "snippet": excerpt[:240],
                "supports": ["H1"],
                "conflicts": [],
                "authority": 0.95 if official else 0.6,
                "relevance": 0.85 if official else 0.55,
                "query": source_query or str(payload.get("query") or ""),
                "depth": "browser",
                "title": title,
                "screenshot_path": screenshot_path,
                "image_count": image_count,
                "used_snapshot": bool(page.get("used_snapshot")),
            }
        )
    return "\n".join(summary_parts[:2]).strip(), links, evidence_items


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

    understanding_result = dict((trace.reasoning_trace or {}).get("understanding_result", {}) or {})
    understanding_summary = _understanding_summary_for_trace(understanding_result)
    depth = decision.search_depth or ("normal" if understanding_summary.get("query_type") in {"authoritative_public_data", "shipping_general_knowledge", "hifleet_troubleshooting"} else "quick")
    query = _understanding_primary_query(understanding_result, text)
    queries = _merge_knowledge_search_plan(text, decision, [], understanding_result, depth)
    if "local_kb_search" not in tool_map and "web_search" not in tool_map and "smart_search" in tool_map:
        queries = queries[:1]
    outputs: list[str] = []
    evidence_items: list[dict[str, Any]] = []
    retrieval_trace = _new_knowledge_retrieval_trace(understanding_summary, query)
    retrieval_trace["query_plan"] = [item.get("query", "") for item in queries]
    retrieval_trace["query_traces"] = []
    for item in queries:
        chain_outputs, chain_evidence, chain_trace = _invoke_three_layer_knowledge_chain(
            text,
            query=str(item.get("query") or query),
            depth=str(item.get("depth") or depth),
            decision=decision,
            tool_map=tool_map,
            trace=trace,
            understanding_summary=understanding_summary,
            hypothesis_id=str(item.get("hypothesis_id") or "H1"),
            purpose=str(item.get("purpose") or "回答当前客服问题"),
        )
        outputs.extend(chain_outputs)
        evidence_items.extend(chain_evidence)
        retrieval_trace.setdefault("query_traces", []).append(_trace_snapshot(chain_trace))
        if chain_trace.get("t2_triggered") or chain_trace.get("t1_can_answer") or chain_trace.get("t0_can_answer"):
            retrieval_trace.update(chain_trace)
            break
        if len(chain_evidence) > len(retrieval_trace.get("layers", [])):
            retrieval_trace.update(chain_trace)
    output = _select_best_evidence_output(outputs, evidence_items)
    if retrieval_trace.get("t2_triggered"):
        trace.fallback_reason = trace.fallback_reason or "official_browser_verification"
    elif retrieval_trace.get("t1_eval_decision") == "no_hit":
        trace.fallback_reason = trace.fallback_reason or "knowledge_three_layer_no_hit"

    ok, invalid = validate_links(output)
    evidence_summary = review_evidence_items(evidence_items)
    trace.check_result = {
        "links_ok": ok,
        "invalid_links": invalid,
        "planned_queries": [item.get("query", "") for item in queries],
        "evidence_count": len(evidence_items),
        "official_support_count": evidence_summary["official_support_count"],
        "multi_query_synthesis": len(queries) > 1,
        "evidence_summary": evidence_summary,
    }
    trace.answer_confidence = evidence_summary["confidence"] if ok and not is_no_hit_text(output) else "medium"
    trace.reasoning_trace = _build_reasoning_trace(
        text,
        queries,
        evidence_items,
        "先直接回答用户核心问题，再补必要操作步骤或核验来源。",
    )
    trace.reasoning_trace["understanding_summary"] = understanding_summary
    trace.reasoning_trace["retrieval_trace"] = retrieval_trace
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
    answer = _format_general_knowledge_answer(text, output, evidence_items=evidence_items)
    return _ensure_step_answer_completeness(text, answer, evidence_items)


def execute_planned_knowledge_chain(
    question: str,
    decision: RouteDecision,
    search_plan: list[dict[str, Any]],
    tool_map: dict[str, Any],
    trace: HarnessTrace,
) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
    understanding_result = dict((trace.reasoning_trace or {}).get("understanding_result", {}) or {})
    understanding_summary = _understanding_summary_for_trace(understanding_result)
    primary_query = _understanding_primary_query(understanding_result, question)
    default_depth = decision.search_depth or ("normal" if understanding_summary.get("query_type") in {"authoritative_public_data", "shipping_general_knowledge", "hifleet_troubleshooting"} else "quick")
    queries = _merge_knowledge_search_plan(question, decision, search_plan, understanding_result, default_depth)
    if "local_kb_search" not in tool_map and "web_search" not in tool_map and "smart_search" in tool_map:
        queries = queries[:1]
    outputs: list[str] = []
    evidence_items: list[dict[str, Any]] = []
    retrieval_trace = _new_knowledge_retrieval_trace(understanding_summary, primary_query)
    retrieval_trace["query_plan"] = [item.get("query", "") for item in queries]
    retrieval_trace["query_traces"] = []

    for item in queries:
        query = str(item.get("query", "")).strip() or primary_query
        depth = str(item.get("depth", "")).strip() or default_depth
        chain_outputs, chain_evidence, chain_trace = _invoke_three_layer_knowledge_chain(
            question,
            query=query,
            depth=depth,
            decision=decision,
            tool_map=tool_map,
            trace=trace,
            understanding_summary=understanding_summary,
            hypothesis_id=str(item.get("hypothesis_id", "H1")),
            purpose=str(item.get("purpose", "")) or "回答当前问题",
        )
        outputs.extend(chain_outputs)
        evidence_items.extend(chain_evidence)
        retrieval_trace.setdefault("query_traces", []).append(_trace_snapshot(chain_trace))
        if chain_trace.get("t2_triggered") or chain_trace.get("t1_can_answer") or chain_trace.get("t0_can_answer"):
            chain_trace["query_plan"] = [item.get("query", "") for item in queries]
            chain_trace["query_traces"] = list(retrieval_trace.get("query_traces") or [])
            retrieval_trace = chain_trace
            break
        if len(chain_evidence) > len(retrieval_trace.get("layers", [])):
            retrieval_trace = chain_trace

    output = _select_best_evidence_output(outputs, evidence_items)
    if retrieval_trace.get("t2_triggered"):
        trace.fallback_reason = trace.fallback_reason or "official_browser_verification"
    elif outputs and all(is_no_hit_text(item) for item in outputs):
        trace.fallback_reason = trace.fallback_reason or "knowledge_three_layer_no_hit"

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
        "evidence_summary": evidence_summary,
    }
    trace.answer_confidence = evidence_summary["confidence"]
    trace.reasoning_trace = _build_reasoning_trace(
        question,
        queries,
        evidence_items,
        "先给结论，再按必要步骤/说明组织客服回复，最后保留一个官方链接或关键追问。",
    )
    trace.reasoning_trace["understanding_summary"] = understanding_summary
    trace.reasoning_trace["retrieval_trace"] = retrieval_trace
    if "上传" in question and "航线" in question and any(marker in question for marker in ["不了", "失败", "怎么办", "无法"]):
        return _format_route_upload_troubleshooting(output), evidence_items, evidence_summary
    if decision.task_type == "platform_troubleshooting":
        return _format_platform_troubleshooting_answer(question, output), evidence_items, evidence_summary
    answer = _format_general_knowledge_answer(question, output, evidence_items=evidence_items)
    return _ensure_step_answer_completeness(question, answer, evidence_items), evidence_items, evidence_summary

def _format_platform_troubleshooting_answer(question: str, search_output: str) -> str:
    q = normalize_message_text(question).lower()
    if _is_low_hifleet_context_device_complaint(question):
        return (
            "如果是在使用 HiFleet 页面时出现卡顿、无响应或浏览器异常，可能和网络、浏览器缓存、页面资源加载有关。\n\n"
            "请先告诉我一个关键信息：卡顿发生在哪个 HiFleet 页面或点击了哪个操作？我再按对应功能帮您排查。"
        )
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
    observations = normalize_message_text(str(perception.get("visible_features") or perception.get("summary") or perception.get("observations") or ""))
    visible_text = normalize_message_text(str(perception.get("visible_text") or ""))
    suspected = normalize_message_text(str(perception.get("suspected_issue") or perception.get("suspected_symbol") or ""))
    if route == "chart_symbol":
        return " ".join(part for part in ["HiFleet 全球海图 图标 含义", visible_text, observations, text] if part)
    return " ".join(part for part in [text, suspected, visible_text, observations] if part) or f"HiFleet {attachment_type} 附件问题"


def _extract_urls_from_text(text: str) -> list[str]:
    urls: list[str] = []
    for url in re.findall(r"https?://[^\s)）\]】>\"']+", text or ""):
        clean = url.rstrip(".,;!?，。；！？）】》")
        if clean and clean not in urls:
            urls.append(clean)
    return urls


def chart_symbol_initial_identification(perception: dict[str, Any]) -> str:
    features = normalize_message_text(str(perception.get("visible_features") or perception.get("summary") or ""))
    visible_text = normalize_message_text(str(perception.get("visible_text") or ""))
    if not features and not visible_text:
        return "图中符号特征不够清晰"
    return "，".join(part for part in [features, f"可见文字：{visible_text}" if visible_text else ""] if part)


def _chart_symbol_verified_url(search_output: str) -> str:
    urls = _extract_urls_from_text(search_output)
    for url in urls:
        if "hifleet.com/wp/communities/fleet/haitutubiaoshuoming" in url:
            return url
    for url in urls:
        if "hifleet.com" in url and ("haitutubiaoshuoming" in url or "海图" in search_output or "图标" in search_output):
            return url
    return ""


def _chart_symbol_evidence_matches(features: str, search_output: str) -> bool:
    merged = normalize_message_text(f"{features} {search_output}").lower()
    has_shape = any(marker in merged for marker in ["红色圆形", "红色圆圈", "红色环形", "中心黑点", "黑色实心圆点", "黑点"])
    has_meaning = any(marker in merged for marker in ["安全水域浮标", "safe water", "safe water mark"])
    return has_shape and has_meaning


def format_unverified_chart_symbol_answer(perception: dict[str, Any]) -> str:
    initial = chart_symbol_initial_identification(perception).rstrip("。.!！")
    return f"针对您输入的图标，我初步识别为：{initial}。未检索到准确官方内容，请您联系人工客服再确认。"


def format_verified_chart_symbol_answer(perception: dict[str, Any], search_output: str) -> str:
    initial = chart_symbol_initial_identification(perception)
    url = _chart_symbol_verified_url(search_output)
    if url and _chart_symbol_evidence_matches(initial, search_output):
        return (
            "这个图标在 HiFleet 全球海图中对应“安全水域浮标”。\n\n"
            f"识别依据：您提供的图标特征为 {initial}，与官方海图图标说明中的对应图示一致。\n"
            f"验证链接：{url}"
        )
    return format_unverified_chart_symbol_answer(perception)


def _guess_evidence_source_type(output: str) -> str:
    browser_payload = _parse_browser_evidence(output)
    if browser_payload:
        pages = browser_payload.get("pages") or []
        if any(isinstance(page, dict) and "wp/communit" in str(page.get("url", "")) for page in pages):
            return "official_community"
        if any(isinstance(page, dict) and page.get("official") for page in pages):
            return "official_site"
        return "public_web"
    lowered = (output or "").lower()
    if "helpcenter" in lowered or "www.hifleet.com" in lowered:
        return "official_site"
    if "wp/communities" in lowered:
        return "official_community"
    if "faq" in lowered or "标准回复" in lowered or "smart_search_l1_hit" in lowered:
        return "local_kb"
    return "public_web"


def _evidence_items_from_tool_output(
    output: str,
    *,
    source_name: str,
    query: str,
    depth: str,
    hypothesis_id: str = "H1",
    purpose: str = "",
) -> list[dict[str, Any]]:
    browser_payload = _parse_browser_evidence(output)
    if browser_payload:
        _, _, items = _browser_evidence_to_answer_parts(browser_payload)
        for item in items:
            item["supports"] = [hypothesis_id]
            item["purpose"] = purpose or "官方公开页面核验"
            item["query"] = item.get("query") or query
            item["depth"] = depth
        return items

    source_type = _guess_evidence_source_type(output)
    is_hit = not is_no_hit_text(output)
    summary, links = _extract_search_answer(output)
    url = links[0] if links else (HELP_CENTER_URL if "helpcenter" in output.lower() else "")
    authority = 0.95 if source_type in {"local_kb", "official_site", "official_community"} else 0.6
    return [
        {
            "source_type": source_type,
            "source_name": source_name,
            "url": url,
            "snippet": summary[:240],
            "supports": [hypothesis_id],
            "conflicts": [],
            "authority": authority,
            "relevance": 0.9 if is_hit else 0.3,
            "query": query,
            "depth": depth,
            "purpose": purpose,
        }
    ]


def _needs_browser_verification(question: str, evidence_items: list[dict[str, Any]], outputs: list[str]) -> bool:
    q = normalize_message_text(question).lower()
    strong_markers = [
        "验证",
        "核验",
        "社区",
        "官网",
        "官方",
        "发布",
        "详细内容",
        "今日",
        "今天",
        "最新",
        "长江水位",
        "浏览器开始记忆",
    ]
    if any(marker in q for marker in strong_markers):
        return True
    if all(is_no_hit_text(output) for output in outputs):
        return True
    has_official = any(item.get("source_type") in {"local_kb", "official_site", "official_community"} and item.get("relevance", 0) >= 0.7 for item in evidence_items)
    return not has_official and any(marker in q for marker in ["如何", "怎么", "使用", "功能", "报错", "入口", "图标", "圆圈"])


def _build_reasoning_trace(
    question: str,
    queries: list[dict[str, Any]],
    evidence_items: list[dict[str, Any]],
    answer_plan: str,
) -> dict[str, Any]:
    official_count = sum(1 for item in evidence_items if item.get("source_type") in {"official_site", "official_community", "local_kb"})
    query_values: list[str] = []
    for item in queries:
        query = str(item.get("query", "")).strip()
        if query and query not in query_values:
            query_values.append(query)
    snippets: list[str] = []
    for item in evidence_items:
        snippet = normalize_message_text(str(item.get("snippet", "")))
        if snippet and snippet not in snippets:
            snippets.append(snippet[:120])
    return {
        "intent_summary": f"用户想了解：{normalize_message_text(question)[:80]}",
        "search_plan": query_values[:5],
        "tool_summary": {
            "searched_query_count": len(query_values),
            "referenced_doc_count": len(evidence_items),
            "official_source_count": official_count,
        },
        "evidence_summary": snippets[:3],
        "answer_plan": answer_plan,
    }


def _select_best_evidence_output(outputs: list[str], evidence_items: list[dict[str, Any]]) -> str:
    """从多次搜索结果中选择最佳输出：优先高质量源，其次高相关度。"""
    if not outputs:
        return ""
    if len(outputs) == 1:
        return outputs[0]
    for output in outputs:
        if _answer_has_step_completeness(output) and not is_no_hit_text(output):
            return output
    # Browser-verified official pages are preferred for community/article/latest verification tasks.
    for i, item in enumerate(evidence_items):
        if (
            i < len(outputs)
            and item.get("source_name") in {"agent_browser_deep_search", "web_search_agent_browser"}
            and item.get("source_type") in {"official_site", "official_community"}
            and not is_no_hit_text(outputs[i])
        ):
            return outputs[i]
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
    observations = normalize_message_text(str(perception.get("visible_features") or perception.get("summary") or perception.get("observations") or ""))
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
        return format_verified_chart_symbol_answer(perception, search)
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
    observations = normalize_message_text(str(perception.get("visible_features") or perception.get("summary") or perception.get("observations") or ""))
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
        return format_verified_chart_symbol_answer(perception, search), evidence_items, evidence_summary
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
    browser_output = ""
    if "verify_public_page" in tool_map:
        verified = _invoke_tool(tool_map, trace, "verify_public_page", {"url": url})
    if "agent_browser_deep_search" in tool_map:
        browser_output = _invoke_tool(
            tool_map,
            trace,
            "agent_browser_deep_search",
            {"query": text, "target_urls": url, "site_hint": "HiFleet official page verification"},
        )
    search = ""
    if "smart_search" in tool_map:
        search = _invoke_tool(tool_map, trace, "smart_search", {"query": text, "depth": decision.search_depth or "normal"})
    browser_payload = _parse_browser_evidence(browser_output)
    if browser_payload:
        browser_summary, browser_links, browser_items = _browser_evidence_to_answer_parts(browser_payload)
        for item in browser_items:
            item["purpose"] = "公开网页最终核验"
        trace.check_result = {
            "url_present": True,
            "verified": bool(verified),
            "searched": bool(search),
            "browser_verified": True,
            "browser_image_evidence": any(item.get("image_count", 0) >= 3 for item in browser_items),
        }
        trace.reasoning_trace = _build_reasoning_trace(
            text,
            [{"query": item.get("query", text), "purpose": "公开网页最终核验"} for item in browser_items[:2]],
            browser_items,
            "优先基于浏览器核验后的公开页面正文作答",
        )
        trace.answer_confidence = "high"
        return format_customer_answer(
            "\n\n".join(part for part in [verified, browser_summary, *browser_links[:2], search] if part),
            heading="已核验公开来源：",
        )
    trace.check_result = {"url_present": True, "verified": bool(verified), "searched": bool(search), "browser_verified": False}
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
    default_start = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
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
            if ask_trajectory and not (entities.start_date or entities.end_date):
                parts.append("未指定时间范围，已默认查询近 7 天历史轨迹。")
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
