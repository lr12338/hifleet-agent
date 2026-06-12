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

TaskType = str
Route = str

KNOWLEDGE_BUNDLE = ["smart_search"]
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
BROWSER_FALLBACK_BUNDLE = ["smart_search"]

HIGH_COST_CAPABILITIES_BY_TASK = {
    "platform_knowledge": [],
    "platform_troubleshooting": [],
    "ship_single_query": [],
    "ship_multi_step_analysis": [],
    "ship_stats": [],
    "ship_update": [],
    "unsupported": [],
}

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
    last_ship_name: str = ""
    last_ship_mmsi: str = ""
    last_ship_imo: str = ""
    last_ship_source: str = ""


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
    previous = user_texts[-2] if len(user_texts) >= 2 else ""
    return ConversationContext(
        latest_user_text=latest,
        previous_user_text=previous,
        recent_user_questions=user_texts[:-1],
        last_ship_name=last_ship_name,
        last_ship_mmsi=last_ship_mmsi,
        last_ship_imo=last_ship_imo,
        last_ship_source=last_ship_source,
    )


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

    memory_markers = ["上面", "上述", "刚才", "刚刚", "之前", "上一个", "上一条", "这艘船", "该船", "这个船", "哪个船", "总结", "汇总"]
    if any(m in q for m in memory_markers):
        if any(m in q for m in ["轨迹", "挂靠", "航次", "目的港", "停靠", "离港", "船位", "档案", "psc"]):
            return RouteDecision("ship_context", "ship_context_followup", SHIP_VOYAGE_BUNDLE, "simple", reason="ship follow-up from conversation context")
        return RouteDecision("conversation", "conversation_memory", [], "simple", fallback_allowed=False, reason="conversation memory question")

    write_markers = ["更新", "上传", "修改", "补录", "update"]
    troubleshooting_markers = ["异常", "失败", "无法", "不显示", "不刷新", "更新慢", "更新很慢", "更新这么慢", "这么慢", "太慢", "延迟", "收不到", "报错", "告警", "报警", "加载失败"]
    platform_markers = ["hifleet", "船队在线", "平台", "功能", "教程", "怎么", "如何", "规则", "配置", "帮助", "绿点", "岸基值班"]
    explicit_write_context = any(m in lower for m in ["上传", "补录", "修改", "更新静态", "更新船位"]) or bool(entities.mmsi and re.search(r"(经度|纬度|lon|lat|speed|heading|course|ship_name|船名|呼号|更新时间)", q, flags=re.IGNORECASE))
    is_troubleshooting = any(m in lower for m in troubleshooting_markers)
    if explicit_write_context and any(m in lower for m in ["船位", "静态", "ais", "位置", "mmsi"]):
        return RouteDecision("ship_update", "ship_update", SHIP_UPDATE_BUNDLE, "simple", reason="explicit ship write operation")

    if is_troubleshooting and (any(m in lower for m in platform_markers) or any(m in context.previous_user_text.lower() for m in platform_markers)):
        return RouteDecision("knowledge", "platform_troubleshooting", KNOWLEDGE_BUNDLE, "simple", search_depth="normal", reason="platform troubleshooting")

    has_ship_entity = bool(entities.mmsi or entities.imo or entities.ship_name or context.last_ship_mmsi or context.last_ship_imo or context.last_ship_name)
    voyage_markers = ["历史轨迹", "轨迹", "历史挂靠", "挂靠", "航次", "上一港", "上次离港", "当前停船", "停在哪", "停靠", "最近靠港", "目的港", "一致"]
    if has_ship_entity and any(m in lower for m in voyage_markers):
        return RouteDecision("ship_complex", "ship_multi_step_analysis", SHIP_VOYAGE_BUNDLE, "complex", reason="voyage or multi-step ship analysis")

    stats_markers = ["海峡", "通航", "区域", "范围内", "bbox", "polygon", "红海绕航", "港口", "port", "船舶列表"]
    if entities.strait or entities.area or entities.bbox or any(m in lower for m in stats_markers):
        return RouteDecision("ship_stats", "ship_stats", SHIP_STATS_BUNDLE, "simple", reason="area/strait/port statistics")

    if not has_ship_entity and is_troubleshooting:
        return RouteDecision("knowledge", "platform_troubleshooting", KNOWLEDGE_BUNDLE, "simple", search_depth="normal", reason="platform troubleshooting")

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


def resolve_entities_with_context(entities: MessageEntities, context: ConversationContext) -> MessageEntities:
    if entities.mmsi or entities.imo or entities.ship_name:
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
    if not context.recent_user_questions:
        return "当前会话里还没有可总结的上一轮问题。"
    if any(m in q for m in ["哪个船", "哪一个船", "这艘船", "该船", "上一个我问的是哪个船"]):
        if context.last_ship_name or context.last_ship_mmsi or context.last_ship_imo:
            ship_bits = [bit for bit in [context.last_ship_name, f"MMSI：{context.last_ship_mmsi}" if context.last_ship_mmsi else "", f"IMO：{context.last_ship_imo}" if context.last_ship_imo else ""] if bit]
            return "你上面查询的船舶是" + "，".join(ship_bits) + "。"
        return "当前会话里没有识别到明确的船舶标识。"
    lines = []
    for idx, item in enumerate(context.recent_user_questions[-8:], start=1):
        lines.append(f"{idx}. {item}")
    return "你上面主要问了这些问题：\n" + "\n".join(lines)


def is_kb_effective_hit(search_output: str) -> bool:
    text = search_output or ""
    return any(marker in text for marker in ("SMART_SEARCH_L1_HIT", "【优先匹配 - FAQ/标准回复】", "从平台术语速查表中匹配到"))


def is_no_hit_text(output: str) -> bool:
    text = output or ""
    return any(marker in text for marker in ("未检索到足够可信", "未找到精确的FAQ匹配", "未找到", "暂无", "信息不足"))


def validate_links(text: str, checker: Callable[[str], bool] | None = None) -> tuple[bool, list[str]]:
    links = [u.rstrip(".,;!?，。；！？）】》") for u in re.findall(r"https?://[^\s)）\]】>\"']+", text or "")]
    if not links:
        return True, []
    if checker is None:
        try:
            from skills.knowledge_qa.tools import _is_url_accessible

            checker = _is_url_accessible
        except Exception:
            checker = lambda url: bool(urlparse(url).scheme in ("http", "https"))
    invalid = [url for url in links if not checker(url)]
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
    depth = decision.search_depth or "quick"
    output = _invoke_tool(tool_map, trace, "smart_search", {"query": text, "depth": depth})
    if depth == "quick" and is_no_hit_text(output):
        trace.fallback_reason = "quick_kb_weak_hit"
        output = _invoke_tool(tool_map, trace, "smart_search", {"query": text, "depth": "normal"})
    if "未检索到足够可信" in output and decision.task_type == "platform_troubleshooting":
        trace.fallback_reason = "normal_search_empty"
        output = _invoke_tool(tool_map, trace, "smart_search", {"query": text, "depth": "deep"})

    ok, invalid = validate_links(output)
    trace.check_result = {"links_ok": ok, "invalid_links": invalid}
    trace.answer_confidence = "high" if ok and not is_no_hit_text(output) else "medium"
    if invalid:
        cleaned = output
        for url in invalid:
            cleaned = cleaned.replace(url, "")
        output = cleaned.strip() + f"\n\n可访问的官方帮助中心：{HELP_CENTER_URL}"
        trace.fallback_reason = trace.fallback_reason or "invalid_links_removed"
    return output


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
    if not mmsi and entities.ship_name:
        search = _invoke_tool(tool_map, trace, "ship_search", {"keyword": entities.ship_name})
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
