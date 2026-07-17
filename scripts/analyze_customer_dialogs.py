#!/usr/bin/env python3
"""Read-only customer support dialog analyzer.

The script only executes SELECT statements against the configured Postgres
database. It exports sanitized Markdown/JSONL/CSV assets for regression and
quality analysis of the HiFleet customer support agent.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import psycopg
from dotenv import dotenv_values
from psycopg.rows import dict_row

BJ = timezone(timedelta(hours=8))
UTC = timezone.utc

SENSITIVE_KEYS = {
    "token",
    "api_key",
    "apikey",
    "authorization",
    "password",
    "secret",
    "key",
    "access_key",
    "accesskey",
}

RISK_KEYWORDS = [
    "立即生效",
    "点击编辑",
    "编辑按钮",
    "自动解析",
    "自动更新",
    "手动上传",
    "前台修改",
    "网页端操作",
    "已为您更新",
    "可以直接修改",
    "reports@hifleet.com",
    "目的港",
    "ETA",
    "update_ship_static_info",
]

FORBIDDEN_DEST_ETA_CLAIMS = [
    "不得声称用户可以在船舶详情页点击编辑按钮修改目的港 / ETA",
    "不得声称发送文本邮件到 reports@hifleet.com 可自动解析更新目的港 / ETA",
    "不得声称提交后立即生效",
    "不得把内部 update_ship_static_info 工具能力描述成用户前台自助入口",
]

PRODUCTION_CUSTOMER_CHANNELS = (
    "wechat_kf",
    "wechat_cs",
    "hifleet_mp",
    "webchat_cn",
    "webchat_en",
    "wechat_mp",
    "customer_api",
)

SCENARIO_GUIDES: dict[str, dict[str, list[str] | str]] = {
    "船舶查询": {
        "intent": "识别船名、MMSI 或 IMO 后查询船舶信息/位置",
        "tools": ["ship_search", "get_ship_position", "get_ship_archive"],
        "reply": "说明匹配依据、数据更新时间和多候选情况；无依据时不猜测。",
        "failures": ["模糊船名直接假定唯一结果", "未说明数据来源或更新时间"],
    },
    "船位更新": {
        "intent": "从本轮输入提取 MMSI、坐标、时间等写入字段，并在完整时执行更新",
        "tools": ["ship_search", "upload_ship_position"],
        "reply": "仅在字段完整且工具成功后确认更新；缺字段时只追问关键字段。",
        "failures": ["度分坐标或时间解析失败", "工具失败后仍宣称更新成功", "复用不明确的历史 MMSI"],
    },
    "船舶静态信息": {
        "intent": "解释或协助处理目的港、ETA 和船舶档案等静态信息",
        "tools": ["get_ship_archive", "update_ship_static_info"],
        "reply": "说明 AIS 静态信息可能滞后；不把内部工具描述为用户前台自助能力。",
        "failures": ["虚构前台编辑入口", "承诺立即生效或邮件自动更新"],
    },
    "平台操作": {
        "intent": "回答 HiFleet 功能入口、船队、文件上传和产品操作问题",
        "tools": ["local_kb_search", "smart_search", "web_search"],
        "reply": "优先依据知识库或官方页面；证据不足时明确不确定性并引导人工客服。",
        "failures": ["无检索证据却强答入口或流程", "步骤与真实产品不一致"],
    },
    "会员/权限": {
        "intent": "解释会员、充值、价格、账号权限或功能额度问题",
        "tools": ["local_kb_search", "smart_search", "web_search"],
        "reply": "仅陈述已验证的规则；缺少公开规则时收集必要信息或转人工。",
        "failures": ["编造价格、权益或额度", "忽略用户提供的会员等级和页面证据"],
    },
    "气象航线": {
        "intent": "指导海况、气象图层、航线或通航统计相关查询",
        "tools": ["local_kb_search", "smart_search", "get_area_traffic", "get_strait_traffic"],
        "reply": "给出对应页面或查询条件，并说明数据范围和时间。",
        "failures": ["把地图图层或统计口径说错", "缺少时间或区域时不追问"],
    },
    "故障排查": {
        "intent": "定位访问、展示、更新或权限异常",
        "tools": ["local_kb_search", "smart_search"],
        "reply": "收集可复现信息、截图或账号/页面上下文，给出已验证的排查路径。",
        "failures": ["缺少关键上下文仍给确定结论", "不区分产品问题和权限限制"],
    },
    "投诉反馈": {
        "intent": "接收服务投诉、差评、建议或需要人工跟进的反馈",
        "tools": [],
        "reply": "确认问题和影响，避免辩解；收集联系与复现信息并转人工跟进。",
        "failures": ["忽略负面情绪", "承诺未验证的修复时效"],
    },
    "其他": {
        "intent": "澄清用户目标或提供安全的客服兜底",
        "tools": [],
        "reply": "简要澄清所需对象、功能或问题现象，避免猜测。",
        "failures": ["把不完整输入当成明确任务", "回复与用户目标无关"],
    },
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Analyze customer support dialog records from Postgres.")
    parser.add_argument("--env-file", default=".env", help="Path to .env file containing PGDATABASE_URL.")
    parser.add_argument("--days", type=int, default=30, help="Analyze records newer than this many days.")
    parser.add_argument("--start-time", default="", help="UTC/BJ ISO timestamp lower bound. Overrides --days.")
    parser.add_argument("--end-time", default="", help="UTC/BJ ISO timestamp upper bound.")
    parser.add_argument("--channel", action="append", default=[], help="Restrict source_channel. Can repeat.")
    parser.add_argument("--all-channels", action="store_true", help="Include non-customer channels such as admin_panel and local_qa.")
    parser.add_argument("--limit", type=int, default=500, help="Maximum api_calls to analyze.")
    parser.add_argument("--checkpoint-samples", type=int, default=20, help="Checkpoint rows to sample.")
    parser.add_argument("--output-dir", default="reports/customer_support_dialogs", help="Output directory.")
    return parser.parse_args()


def load_db_url(env_file: str) -> str:
    values = dotenv_values(env_file)
    url = values.get("PGDATABASE_URL") or ""
    if not url:
        raise SystemExit(f"PGDATABASE_URL not found in {env_file}")
    return url


def parse_time(value: str) -> datetime | None:
    if not value:
        return None
    normalized = value.strip().replace("Z", "+00:00")
    dt = datetime.fromisoformat(normalized)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=BJ)
    return dt.astimezone(UTC)


def hash_id(value: Any, prefix: str = "h") -> str:
    text = str(value or "")
    if not text:
        return ""
    return f"{prefix}_{hashlib.sha256(text.encode('utf-8')).hexdigest()[:12]}"


def mask_text(text: Any) -> str:
    value = str(text or "")
    value = re.sub(
        r"([?&](?:OSSAccessKeyId|Signature|Expires|ExpiresAt|accessKeyId|accessKeySecret|token|api_key|apiKey)=)[^\\s\"'&]+",
        r"\1***",
        value,
        flags=re.I,
    )
    value = re.sub(
        r"\b(?:wkFS|wmFS|oP3)[A-Za-z0-9_-]{12,}\b",
        lambda m: hash_id(m.group(0), "id"),
        value,
    )
    value = re.sub(
        r"/([A-Za-z0-9_-]{32,})(?=[/?#\"'\\s]|$)",
        lambda m: "/" + hash_id(m.group(1), "seg"),
        value,
    )
    value = re.sub(r"([A-Za-z0-9._%+-]{2})[A-Za-z0-9._%+-]*@([A-Za-z0-9.-]+\.[A-Za-z]{2,})", r"\1***@\2", value)
    value = re.sub(r"(?<!\d)(1[3-9]\d{9})(?!\d)", lambda m: m.group(1)[:3] + "****" + m.group(1)[-4:], value)
    value = re.sub(r"(openid[_:=/-]?|user_id[:=]?)([A-Za-z0-9_-]{8,})", lambda m: m.group(1) + hash_id(m.group(2), "id"), value, flags=re.I)
    value = re.sub(r"(access_token|token|api[_-]?key|secret|password)[\"'=:\s]+[A-Za-z0-9._~+/=-]{8,}", r"\1=***", value, flags=re.I)
    return value


def sanitize_json(value: Any, depth: int = 0) -> Any:
    if depth > 8:
        return "<truncated>"
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            lowered = str(key).lower()
            if any(secret in lowered for secret in SENSITIVE_KEYS):
                out[key] = "***"
            elif key in {"session_id", "user_id", "openid"}:
                out[key] = hash_id(item, key)
            else:
                out[key] = sanitize_json(item, depth + 1)
        return out
    if isinstance(value, list):
        return [sanitize_json(item, depth + 1) for item in value[:20]]
    if isinstance(value, str):
        return mask_text(value)
    return value


def short(value: Any, limit: int = 220) -> str:
    text = mask_text(value).replace("\r", " ").replace("\n", " ").strip()
    text = re.sub(r"\s+", " ", text)
    if len(text) > limit:
        return text[: limit - 3] + "..."
    return text


def message_content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                item_type = item.get("type")
                if item_type == "text":
                    parts.append(str(item.get("text") or ""))
                elif item_type == "image_url":
                    parts.append("[image_url]")
                elif "text" in item:
                    parts.append(str(item.get("text") or ""))
                else:
                    parts.append(short(item, 80))
            else:
                parts.append(str(item))
        return " ".join(parts)
    return str(content or "")


def extract_user_input(request_json: dict[str, Any] | None) -> str:
    payload = request_json or {}
    messages = payload.get("messages")
    if isinstance(messages, list):
        users = [m for m in messages if isinstance(m, dict) and str(m.get("role", "")).lower() == "user"]
        if users:
            return message_content_to_text(users[-1].get("content"))
    for key in ("input", "text", "prompt"):
        if payload.get(key):
            return message_content_to_text(payload.get(key))
    content = payload.get("content")
    if isinstance(content, dict):
        query = content.get("query")
        if isinstance(query, dict) and query.get("prompt"):
            return message_content_to_text(query.get("prompt"))
    return ""


def extract_agent_reply(response_json: dict[str, Any] | None) -> str:
    payload = response_json or {}
    messages = payload.get("messages")
    if isinstance(messages, list):
        assistants = [
            m for m in messages
            if isinstance(m, dict) and str(m.get("role") or m.get("type") or "").lower() in {"assistant", "ai"}
        ]
        if assistants:
            return message_content_to_text(assistants[-1].get("content"))
    for key in ("answer", "output", "message", "content", "response"):
        if payload.get(key):
            return message_content_to_text(payload.get(key))
    return ""


def extract_model(request_json: dict[str, Any] | None, response_json: dict[str, Any] | None) -> str:
    for payload in (request_json or {}, response_json or {}):
        route = payload.get("llm_route") if isinstance(payload, dict) else None
        if isinstance(route, dict) and route.get("model"):
            return str(route.get("model"))
        if isinstance(payload, dict) and payload.get("model"):
            return str(payload.get("model"))
    return ""


def extract_route_trace(response_json: dict[str, Any] | None) -> dict[str, Any]:
    payload = response_json or {}
    trace = payload.get("route_trace")
    if isinstance(trace, dict):
        return trace
    messages = payload.get("messages")
    if isinstance(messages, list):
        for msg in reversed(messages):
            if isinstance(msg, dict):
                kwargs = msg.get("additional_kwargs")
                if isinstance(kwargs, dict) and isinstance(kwargs.get("route_trace"), dict):
                    return kwargs["route_trace"]
    return {}


def classify_business(user_input: str, reply: str, tools: list[dict[str, Any]], route_trace: dict[str, Any] | None = None) -> str:
    user_lower = user_input.lower()
    text = f"{user_input} {reply}".lower()
    tool_names = {str(t.get("tool_name") or "") for t in tools}
    trace = route_trace or {}
    tool_names.update(str(name) for name in (trace.get("tool_call_sequence") or []) if name)
    route = str(trace.get("route") or "")
    task_type = str(trace.get("task_type") or "")
    if any(name in tool_names for name in ("upload_ship_position",)) or any(k in text for k in ("更新船位", "船位更新", "经度", "纬度")):
        if "缺少" in reply and "经度" in reply:
            return "船位更新/缺经度"
        if "缺少" in reply and "纬度" in reply:
            return "船位更新/缺纬度"
        if "缺少" in reply and "时间" in reply:
            return "船位更新/缺时间"
        if re.search(r"22\d{3}-\d{2}-\d{2}", user_input):
            return "船位更新/时间格式异常"
        if "成功" in reply or "已更新" in reply:
            return "船位更新/字段完整成功或疑似成功"
        return "船位更新/待补充或待确认"
    if (
        any(name in tool_names for name in ("update_ship_static_info",))
        or "static" in task_type
        or any(k in user_lower for k in ("目的港", "eta", "预抵", "静态信息", "档案"))
    ):
        if any(k in user_lower for k in ("入口", "按钮", "网页端", "前台", "怎么", "手动")):
            return "船舶静态信息更新/后台代操作 vs 前台入口"
        return "船舶静态信息更新/目的港或ETA"
    if any(name in tool_names for name in ("ship_search", "get_ship_position", "get_ship_archive")) or route.startswith("ship") or any(k in user_lower for k in ("mmsi", "imo", "船位", "坐标", "靠近", "国家")):
        if "靠近" in user_lower or "国家" in user_lower:
            return "船舶查询/靠近国家或区域"
        if "mmsi" in user_lower:
            return "船舶查询/MMSI查询"
        if "imo" in user_lower:
            return "船舶查询/IMO查询"
        return "船舶查询/船名或船位查询"
    if any(k in user_lower for k in ("hifleet", "平台", "权限", "积分", "key", "api", "航线", "气象", "邮件", "上传", "报表", "入口")):
        return "平台功能问答"
    if any(k in user_lower for k in ("打不开", "不更新", "异常", "看不到", "失败", "卡", "死机", "权限问题")):
        return "故障排查"
    return "闲聊或无关输入"


def classify_quality(user_input: str, reply: str, tools: list[dict[str, Any]], errors: list[dict[str, Any]], route_trace: dict[str, Any]) -> tuple[str, str, str]:
    text = f"{user_input}\n{reply}"
    lower = text.lower()
    tool_names = [str(t.get("tool_name") or "") for t in tools]
    tool_names.extend(str(name) for name in (route_trace.get("tool_call_sequence") or []) if name)
    failed_tools = [t for t in tools if str(t.get("status", "")).lower() not in {"ok", "success", "completed"}]
    if errors:
        return "报错或异常未处理", "P1", f"关联 agent_errors {len(errors)} 条"
    if failed_tools and any(k in reply for k in ("成功", "已为您", "已更新")):
        return "工具调用错误", "P0", "工具失败后回复疑似包装成成功"
    if any(k in reply for k in ("点击编辑", "编辑按钮", "前台修改", "网页端操作", "可以直接修改")) and any(k in lower for k in ("目的港", "eta", "预抵")):
        return "内部能力与用户前台能力混淆", "P0", "目的港/ETA 前台自助入口高风险表述"
    if "reports@hifleet.com" in lower and any(k in reply for k in ("自动解析", "自动更新", "即可更新", "可以更新")):
        return "功能幻觉", "P0", "reports@hifleet.com 被描述为目的港/ETA 自动更新入口"
    if "立即生效" in reply and any(k in lower for k in ("目的港", "eta", "预抵", "更新")):
        return "功能幻觉", "P0", "涉及立即生效承诺"
    if "更新船位" in user_input and any(k in user_input for k in ("经度", "纬度", "°")) and "缺少" in reply:
        return "字段抽取错误", "P1", "用户疑似提供坐标但回复判断缺字段"
    if any(k in user_input for k in ("怎么", "入口", "如何", "权限", "价格", "功能")) and not any(name in tool_names for name in ("local_kb_search", "smart_search", "web_search", "web_search_agent_browser", "verify_public_page")):
        if any(k in reply for k in ("可以", "支持", "步骤", "入口", "点击")):
            return "知识检索不足但强答", "P1", "平台功能/入口类问题未见检索工具"
    if len(reply) > 900:
        return "回复过长 / 不适合客服场景", "P3", "回复过长"
    if not reply:
        return "回复过短 / 缺少关键说明", "P2", "未抽取到最终回复"
    if route_trace.get("evidence_guard", {}).get("blocked_claims"):
        return "基本正确但表达可优化", "P2", "触发 evidence guard，需关注话术"
    if any(str(t.get("status", "")).lower() in {"ok", "success"} for t in tools) or "点击查看" in reply or "请补充" in reply:
        return "正确优秀案例", "Positive", ""
    return "基本正确但表达可优化", "P2", ""


def expected_points_for(category: str) -> list[str]:
    if category.startswith("船位更新"):
        return ["缺字段时只追问关键字段", "字段完整且工具成功后才声明更新成功", "不得复用不明确历史 MMSI 直接写入"]
    if category.startswith("船舶查询"):
        return ["识别船名/MMSI/IMO", "必要时调用船舶查询工具", "返回坐标、更新时间、公开链接或候选说明"]
    if category.startswith("船舶静态信息更新"):
        return ["目的港/ETA 来自 AIS 静态信息且可能滞后", "普通用户前台无自助编辑入口", "需要 MMSI、最新目的港、ETA，由客服协助"]
    if category.startswith("平台功能"):
        return ["优先知识库/官方页面证据", "无明确证据时保守收口", "必要时给人工客服联系方式"]
    return []


def classify_scenario(case: "DialogCase") -> str:
    text = f"{case.user_input}\n{case.agent_reply}".lower()
    category = case.business_category
    if category.startswith("船位更新"):
        return "船位更新"
    if category.startswith("船舶静态信息"):
        return "船舶静态信息"
    if category.startswith("船舶查询"):
        return "船舶查询"
    if any(keyword in text for keyword in ("投诉", "不满", "差评", "建议反馈", "为什么这么差")):
        return "投诉反馈"
    if any(keyword in text for keyword in ("会员", "充值", "价格", "收费", "额度", "积分", "权限", "登录", "账号")):
        return "会员/权限"
    if any(keyword in text for keyword in ("气象", "海况", "浪粒子", "航线", "红海", "海峡", "通航")):
        return "气象航线"
    if any(keyword in text for keyword in ("打不开", "不更新", "异常", "失败", "看不到", "卡", "死机")):
        return "故障排查"
    if category.startswith("平台功能"):
        return "平台操作"
    return "其他"


def case_selection_reasons(case: "DialogCase") -> list[str]:
    reasons: list[str] = []
    tool_names = {str(tool.get("name") or "") for tool in case.tools}
    failed_tools = [tool for tool in case.tools if not tool.get("success")]
    if case.risk_level in {"P0", "P1"}:
        reasons.append(f"{case.risk_level} 风险")
    if case.status.lower() in {"error", "degraded"}:
        reasons.append(f"调用状态为 {case.status}")
    if failed_tools:
        reasons.append("工具调用失败")
    if tool_names & {"upload_ship_position", "update_ship_static_info"}:
        reasons.append("涉及写操作")
    if case.quality_category == "字段抽取错误":
        reasons.append("字段解析失败")
    if case.issue_summary:
        reasons.append(case.issue_summary)
    return reasons or ["高频场景代表样本"]


def case_selection_score(case: "DialogCase") -> int:
    score = {"P0": 100, "P1": 80, "P2": 30, "P3": 10, "Positive": 5}.get(case.risk_level, 0)
    if case.status.lower() == "error":
        score += 70
    elif case.status.lower() == "degraded":
        score += 55
    if any(not tool.get("success") for tool in case.tools):
        score += 55
    if any(tool.get("name") in {"upload_ship_position", "update_ship_static_info"} for tool in case.tools):
        score += 35
    if case.quality_category == "字段抽取错误":
        score += 25
    return score


def case_signature(case: "DialogCase") -> str:
    normalized = re.sub(r"\d+", "#", case.user_input.lower())
    normalized = re.sub(r"\s+", "", normalized)
    return normalized[:180] or case.business_category


@dataclass
class DialogCase:
    case_id: str
    time_bj: str
    channel: str
    session_id_hash: str
    user_id_hash: str
    run_id_hash: str
    route: str
    status: str
    latency_ms: int
    model: str
    user_input: str
    agent_reply: str
    business_category: str
    scenario: str
    quality_category: str
    risk_level: str
    tools: list[dict[str, Any]]
    errors: list[dict[str, Any]]
    route_trace_summary: str
    issue_summary: str
    expected_reply_points: list[str] = field(default_factory=list)
    forbidden_claims: list[str] = field(default_factory=list)
    risk_keywords: list[str] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return self.__dict__


def fetch_rows(conn: psycopg.Connection, start: datetime, end: datetime | None, channels: list[str], limit: int) -> list[dict[str, Any]]:
    clauses = ["created_at >= %(start)s"]
    params: dict[str, Any] = {"start": start, "limit": limit}
    if end:
        clauses.append("created_at <= %(end)s")
        params["end"] = end
    if channels:
        clauses.append("source_channel = ANY(%(channels)s)")
        params["channels"] = channels
    where = " AND ".join(clauses)
    sql = f"""
        SELECT id, run_id, session_id, user_id, source_channel, route, intent_hint,
               request_json, response_json, http_status_code, status, latency_ms, created_at
        FROM observability.api_calls
        WHERE {where}
        ORDER BY created_at DESC
        LIMIT %(limit)s
    """
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return list(cur.fetchall())


def fetch_stats(conn: psycopg.Connection, start: datetime, end: datetime | None, channels: list[str]) -> dict[str, Any]:
    clauses = ["created_at >= %(start)s"]
    params: dict[str, Any] = {"start": start}
    if end:
        clauses.append("created_at <= %(end)s")
        params["end"] = end
    if channels:
        clauses.append("source_channel = ANY(%(channels)s)")
        params["channels"] = channels
    where = " AND ".join(clauses)
    stats: dict[str, Any] = {}
    with conn.cursor() as cur:
        cur.execute(f"""
            SELECT count(*) AS api_calls,
                   count(DISTINCT session_id) AS sessions,
                   count(DISTINCT user_id) AS users,
                   min(created_at) AS first_at,
                   max(created_at) AS last_at
            FROM observability.api_calls
            WHERE {where}
        """, params)
        stats["api"] = dict(cur.fetchone() or {})
        cur.execute(f"""
            SELECT source_channel AS channel, count(*) AS api_calls,
                   count(DISTINCT session_id) AS sessions,
                   max(created_at) AS last_at
            FROM observability.api_calls
            WHERE {where}
            GROUP BY source_channel
            ORDER BY api_calls DESC
        """, params)
        stats["channels"] = list(cur.fetchall())
        cur.execute("SELECT count(*) AS n FROM observability.tool_invocations WHERE created_at >= %(start)s" + (" AND created_at <= %(end)s" if end else ""), params)
        stats["tool_count"] = int((cur.fetchone() or {}).get("n") or 0)
        cur.execute("SELECT count(*) AS n FROM observability.agent_errors WHERE created_at >= %(start)s" + (" AND created_at <= %(end)s" if end else ""), params)
        stats["error_count"] = int((cur.fetchone() or {}).get("n") or 0)
        table_counts: dict[str, int] = {}
        for table in [
            "observability.api_calls",
            "observability.tool_invocations",
            "observability.agent_errors",
            "observability.chat_debug_sessions",
            "memory.checkpoints",
            "memory.checkpoint_blobs",
            "memory.checkpoint_writes",
        ]:
            cur.execute(f"SELECT count(*) AS n FROM {table}")
            table_counts[table] = int((cur.fetchone() or {}).get("n") or 0)
        stats["table_counts"] = table_counts
    return stats


def fetch_related(conn: psycopg.Connection, run_ids: list[str]) -> tuple[dict[str, list[dict[str, Any]]], dict[str, list[dict[str, Any]]]]:
    if not run_ids:
        return {}, {}
    tools: dict[str, list[dict[str, Any]]] = defaultdict(list)
    errors: dict[str, list[dict[str, Any]]] = defaultdict(list)
    with conn.cursor() as cur:
        cur.execute("""
            SELECT run_id, session_id, tool_name, tool_args, tool_result, status, code,
                   message, retriable, attempt, latency_ms, source, layer_trace, created_at
            FROM observability.tool_invocations
            WHERE run_id = ANY(%(run_ids)s)
            ORDER BY created_at ASC
        """, {"run_ids": run_ids})
        for row in cur.fetchall():
            tools[row["run_id"]].append(dict(row))
        cur.execute("""
            SELECT run_id, session_id, route, error_code, error_message, stack_trace,
                   error_category, node_name, attempt, created_at
            FROM observability.agent_errors
            WHERE run_id = ANY(%(run_ids)s)
            ORDER BY created_at ASC
        """, {"run_ids": run_ids})
        for row in cur.fetchall():
            errors[row["run_id"]].append(dict(row))
    return tools, errors


def fetch_checkpoint_samples(conn: psycopg.Connection, sample_count: int) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT thread_id, checkpoint_id, checkpoint, metadata
            FROM memory.checkpoints
            ORDER BY checkpoint_id DESC
            LIMIT %(limit)s
        """, {"limit": sample_count})
        samples = []
        for row in cur.fetchall():
            checkpoint = row.get("checkpoint") or {}
            metadata = row.get("metadata") or {}
            samples.append({
                "thread_id_hash": hash_id(row.get("thread_id"), "thread"),
                "checkpoint_id": row.get("checkpoint_id"),
                "checkpoint_keys": sorted(checkpoint.keys()) if isinstance(checkpoint, dict) else [],
                "metadata_keys": sorted(metadata.keys()) if isinstance(metadata, dict) else [],
                "has_channel_values": isinstance(checkpoint, dict) and "channel_values" in checkpoint,
                "ts": checkpoint.get("ts") if isinstance(checkpoint, dict) else "",
            })
        return samples


def tool_summary(tool: dict[str, Any]) -> dict[str, Any]:
    result = tool.get("tool_result")
    args = tool.get("tool_args")
    return {
        "name": tool.get("tool_name") or "",
        "success": str(tool.get("status") or "").lower() in {"ok", "success", "completed"},
        "status": tool.get("status") or "",
        "code": tool.get("code") or "",
        "latency_ms": int(tool.get("latency_ms") or 0),
        "args_summary": short(json.dumps(sanitize_json(args or {}), ensure_ascii=False), 280),
        "result_summary": short(json.dumps(sanitize_json(result or {}), ensure_ascii=False), 320) or short(tool.get("message"), 240),
    }


def route_trace_summary(trace: dict[str, Any]) -> str:
    if not trace:
        return "未记录 route_trace"
    parts = []
    for key in ("route", "task_type", "tool_bundle", "tool_call_sequence", "answer_confidence", "fallback_reason"):
        if trace.get(key):
            parts.append(f"{key}={short(trace.get(key), 160)}")
    reasoning = trace.get("reasoning_trace")
    if isinstance(reasoning, dict):
        understanding = reasoning.get("understanding_result")
        if isinstance(understanding, dict):
            parts.append(f"intent={short(understanding.get('intent') or understanding.get('task_type'), 120)}")
    return "; ".join(parts) or "route_trace 可解析但未含核心字段"


def build_cases(rows: list[dict[str, Any]], tools_by_run: dict[str, list[dict[str, Any]]], errors_by_run: dict[str, list[dict[str, Any]]]) -> list[DialogCase]:
    cases: list[DialogCase] = []
    for idx, row in enumerate(rows, 1):
        req = row.get("request_json") if isinstance(row.get("request_json"), dict) else {}
        resp = row.get("response_json") if isinstance(row.get("response_json"), dict) else {}
        tools = tools_by_run.get(row["run_id"], [])
        errors = errors_by_run.get(row["run_id"], [])
        user_input = mask_text(extract_user_input(req))
        reply = mask_text(extract_agent_reply(resp))
        trace = extract_route_trace(resp)
        business = classify_business(user_input, reply, tools, trace)
        quality, risk, issue = classify_quality(user_input, reply, tools, errors, trace)
        risk_keywords = [kw for kw in RISK_KEYWORDS if kw.lower() in f"{user_input}\n{reply}".lower()]
        forbidden = FORBIDDEN_DEST_ETA_CLAIMS if ("目的港" in f"{user_input}{reply}" or "eta" in f"{user_input}{reply}".lower()) else []
        created = row["created_at"].astimezone(BJ)
        case_id = f"CS-{created.strftime('%Y%m%d')}-{idx:04d}"
        case = DialogCase(
            case_id=case_id,
            time_bj=created.strftime("%Y-%m-%d %H:%M:%S"),
            channel=str(row.get("source_channel") or ""),
            session_id_hash=hash_id(row.get("session_id"), "session"),
            user_id_hash=hash_id(row.get("user_id"), "user"),
            run_id_hash=hash_id(row.get("run_id"), "run"),
            route=str(row.get("route") or ""),
            status=str(row.get("status") or ""),
            latency_ms=int(row.get("latency_ms") or 0),
            model=extract_model(req, resp),
            user_input=short(user_input, 1200),
            agent_reply=short(reply, 1800),
            business_category=business,
            scenario="",
            quality_category=quality,
            risk_level=risk,
            tools=[tool_summary(t) for t in tools],
            errors=[sanitize_json(e) for e in errors],
            route_trace_summary=route_trace_summary(trace),
            issue_summary=issue,
            expected_reply_points=expected_points_for(business),
            forbidden_claims=forbidden,
            risk_keywords=risk_keywords,
        )
        case.scenario = classify_scenario(case)
        cases.append(case)
    return cases


def pct(n: int, total: int) -> str:
    return f"{(n / total * 100):.1f}%" if total else "0.0%"


def md_table(headers: list[str], rows: list[list[Any]]) -> str:
    out = ["| " + " | ".join(headers) + " |", "| " + " | ".join(["---"] * len(headers)) + " |"]
    for row in rows:
        out.append("| " + " | ".join(short(cell, 180).replace("|", "\\|") for cell in row) + " |")
    return "\n".join(out)


def write_jsonl(path: Path, cases: list[DialogCase]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for case in cases:
            f.write(json.dumps(case.to_json(), ensure_ascii=False, default=str) + "\n")


def write_csv(path: Path, cases: list[DialogCase]) -> None:
    fields = ["case_id", "time_bj", "channel", "session_id_hash", "scenario", "business_category", "quality_category", "risk_level", "user_input", "agent_reply", "issue_summary"]
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for case in cases:
            row = case.to_json()
            writer.writerow({k: row.get(k, "") for k in fields})


def select_key_cases(cases: list[DialogCase], max_per_scenario: int = 3) -> list[DialogCase]:
    selected: list[DialogCase] = []
    by_scenario: dict[str, list[DialogCase]] = defaultdict(list)
    for case in cases:
        by_scenario[case.scenario].append(case)
    for scenario in SCENARIO_GUIDES:
        seen_signatures: set[str] = set()
        candidates = sorted(
            by_scenario.get(scenario, []),
            key=lambda case: (case_selection_score(case), case.time_bj, case.case_id),
            reverse=True,
        )
        for case in candidates:
            signature = case_signature(case)
            if signature in seen_signatures:
                continue
            selected.append(case)
            seen_signatures.add(signature)
            if len(seen_signatures) >= max_per_scenario:
                break
    return sorted(selected, key=lambda case: (case.scenario, -case_selection_score(case), case.case_id))


def baseline_regression_fixtures() -> list[dict[str, Any]]:
    return [
        {
            "id": "ship_update_degree_minute_coordinates",
            "source": "baseline",
            "scenario": "船位更新",
            "query": "更新船位，MMSI：414718000，更新时间：2026-07-17 15:45:00，位置：13°48.8N 055°28.4E，航速：3.2kn",
            "expected_route": "ship_update",
            "expected_tools": ["upload_ship_position"],
            "forbidden_tools": [],
            "required_reply_points": ["字段完整且工具成功后才声明更新成功"],
            "forbidden_substrings": ["缺少必填字段：经度", "缺少必填字段：纬度"],
            "risk_level": "P1",
        },
        {
            "id": "ship_update_missing_fields_guard",
            "source": "baseline",
            "scenario": "船位更新",
            "query": "更新 MMSI 414718000 船位",
            "expected_route": "ship_update",
            "expected_tools": [],
            "forbidden_tools": ["upload_ship_position"],
            "required_reply_points": ["缺字段时只追问关键字段"],
            "forbidden_substrings": ["更新成功", "已为您更新"],
            "risk_level": "P1",
        },
        {
            "id": "ship_update_tool_failure_never_success",
            "source": "baseline",
            "scenario": "船位更新",
            "query": "更新船位，MMSI：414718000，更新时间：2026-07-17 15:45:00，位置：13°48.8N 055°28.4E",
            "expected_route": "ship_update",
            "expected_tools": ["upload_ship_position"],
            "forbidden_tools": [],
            "tool_overrides": {"upload_ship_position": "船位更新失败：上游服务不可用"},
            "required_reply_points": ["工具失败时说明未成功并给出后续建议"],
            "forbidden_substrings": ["更新成功", "已为您更新"],
            "risk_level": "P0",
        },
        {
            "id": "static_info_unverified_effective_time",
            "source": "baseline",
            "scenario": "船舶静态信息",
            "query": "怎么在 HiFleet 平台手动更新船舶目的港和 ETA？",
            "expected_route": "knowledge",
            "expected_tools": [],
            "forbidden_tools": ["update_ship_static_info"],
            "required_reply_points": ["普通用户前台无自助编辑入口"],
            "forbidden_substrings": ["立即生效", "自动解析", "自动更新", "点击编辑"],
            "risk_level": "P0",
        },
    ]


def fixture_forbidden_substrings(case: DialogCase) -> list[str]:
    forbidden: list[str] = []
    if case.scenario == "船舶静态信息":
        forbidden.extend(["点击编辑", "前台修改", "自动解析", "自动更新", "立即生效"])
    if any(not tool.get("success") for tool in case.tools):
        forbidden.extend(["更新成功", "已为您更新", "已更新"])
    return list(dict.fromkeys(forbidden))


def build_regression_fixtures(key_cases: list[DialogCase]) -> list[dict[str, Any]]:
    fixtures = baseline_regression_fixtures()
    known_ids = {fixture["id"] for fixture in fixtures}
    for case in key_cases:
        fixture_id = f"observed_{case.case_id.lower()}"
        if fixture_id in known_ids:
            continue
        observed_tools = [tool["name"] for tool in case.tools if tool.get("success")]
        fixtures.append({
            "id": fixture_id,
            "source": "observed",
            "source_case_id": case.case_id,
            "scenario": case.scenario,
            "query": case.user_input,
            "context": {"channel": case.channel, "route": case.route},
            "expected_route": case.route or None,
            "expected_tools": observed_tools,
            "forbidden_tools": [],
            "required_reply_points": case.expected_reply_points,
            "forbidden_substrings": fixture_forbidden_substrings(case),
            "risk_level": case.risk_level,
            "selection_reasons": case_selection_reasons(case),
        })
        known_ids.add(fixture_id)
    return fixtures


def write_regression_fixture(path: Path, fixtures: list[dict[str, Any]]) -> None:
    path.write_text(json.dumps(fixtures, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_scenario_report(path: Path, cases: list[DialogCase], key_cases: list[DialogCase], fixtures: list[dict[str, Any]], start: datetime, end: datetime | None) -> None:
    scenario_counts = Counter(case.scenario for case in cases)
    lines = [
        "# HiFleet 客服 Agent 对话场景与关键案例",
        "",
        "本报告由生产客服渠道的脱敏日志生成，用于开发理解真实对话场景并维护回归测试。",
        "",
        "## 1. 场景地图",
        "",
        f"- 分析范围：{start.astimezone(BJ).strftime('%Y-%m-%d %H:%M:%S')} 至 {(end.astimezone(BJ).strftime('%Y-%m-%d %H:%M:%S') if end else '当前')}（北京时间）。",
        f"- 样本数量：{len(cases)}；关键案例：{len(key_cases)}；回归 fixture：{len(fixtures)}。",
        "",
    ]
    for scenario, guide in SCENARIO_GUIDES.items():
        lines += [
            f"### {scenario}（{scenario_counts.get(scenario, 0)}）",
            "",
            f"- 用户需求：{guide['intent']}",
            f"- 预期工具：{', '.join(guide['tools']) or '无需工具 / 视上下文转人工'}",
            f"- 正确回复：{guide['reply']}",
            f"- 常见失败：{'；'.join(guide['failures'])}",
            "",
        ]

    lines += ["## 2. 关键案例", ""]
    for case in key_cases:
        tool_chain = ", ".join(f"{tool['name']}:{tool['status']}" for tool in case.tools) or "无"
        lines += [
            f"### {case.case_id}｜{case.scenario}｜{case.risk_level}",
            "",
            f"- 选择原因：{'；'.join(case_selection_reasons(case))}",
            f"- 用户输入：{case.user_input or '<空>'}",
            f"- Agent 回复：{short(case.agent_reply, 420) or '<空>'}",
            f"- 实际工具：{tool_chain}",
            f"- 预期行为：{'；'.join(case.expected_reply_points) or SCENARIO_GUIDES[case.scenario]['reply']}",
            "",
        ]
    if not key_cases:
        lines += ["当前窗口未选出关键案例；请扩大时间范围或检查生产渠道是否有日志。", ""]

    lines += ["## 3. 优化建议", ""]
    for scenario, scenario_cases in sorted(((name, [case for case in key_cases if case.scenario == name]) for name in SCENARIO_GUIDES), key=lambda item: -len(item[1])):
        if not scenario_cases:
            continue
        high_risk = [case for case in scenario_cases if case.risk_level in {"P0", "P1"}]
        if high_risk:
            lines.append(f"- {scenario}：优先处理 {len(high_risk)} 个 P0/P1 案例，围绕 {'；'.join(dict.fromkeys(reason for case in high_risk for reason in case_selection_reasons(case)))[:3]} 增加防护。")
    lines += [
        "- 平台操作、会员/权限类问题：缺少可验证知识时保守收口，不编造入口、价格和权限规则。",
        "- 船位更新：字段解析、写操作前置校验和工具结果回传必须一起回归。",
        "",
        "## 4. 测试断言",
        "",
        "- fixture 文件：`customer_support_regression_fixtures.json`；可由 `scripts/hifleet_agent_regression.py --fixture-file` 加载。",
        "- P0/P1：断言意图路由、必须/禁止工具、禁止话术；写操作使用 fixture 的工具覆盖结果，不调用真实写接口。",
        "- 核心场景：保留船位更新坐标解析、缺字段保护、工具失败不报成功、目的港/ETA 未验证时效承诺防护。",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def write_sql_notes(path: Path) -> None:
    path.write_text("""# SQL 查询记录

本次分析脚本只执行 `SELECT`，不包含 `INSERT`、`UPDATE`、`DELETE`、DDL 或锁表操作。数据库密码仅从 `.env` 读取，未写入报告。

## 1. 查询 api_calls 明细

用途：抽取用户输入、agent 回复、渠道、route、模型、延迟与状态。

```sql
SELECT id, run_id, session_id, user_id, source_channel, route, intent_hint,
       request_json, response_json, http_status_code, status, latency_ms, created_at
FROM observability.api_calls
WHERE created_at >= %(start)s
ORDER BY created_at DESC
LIMIT %(limit)s;
```

## 2. 查询工具调用

用途：通过 `run_id` 关联每轮对话的工具链、参数、返回、状态与错误摘要。

```sql
SELECT run_id, session_id, tool_name, tool_args, tool_result, status, code,
       message, retriable, attempt, latency_ms, source, layer_trace, created_at
FROM observability.tool_invocations
WHERE run_id = ANY(%(run_ids)s)
ORDER BY created_at ASC;
```

## 3. 查询 agent 错误

用途：通过 `run_id` 关联错误类型、错误信息、节点与堆栈摘要。

```sql
SELECT run_id, session_id, route, error_code, error_message, stack_trace,
       error_category, node_name, attempt, created_at
FROM observability.agent_errors
WHERE run_id = ANY(%(run_ids)s)
ORDER BY created_at ASC;
```

## 4. 渠道统计

用途：统计分析窗口内各渠道对话数、会话数和最近时间。

```sql
SELECT source_channel AS channel, count(*) AS api_calls,
       count(DISTINCT session_id) AS sessions, max(created_at) AS last_at
FROM observability.api_calls
WHERE created_at >= %(start)s
GROUP BY source_channel
ORDER BY api_calls DESC;
```

## 5. checkpoint 抽样

用途：抽样理解 LangGraph checkpoint 的可解析字段，不还原敏感完整上下文。

```sql
SELECT thread_id, checkpoint_id, checkpoint, metadata
FROM memory.checkpoints
ORDER BY checkpoint_id DESC
LIMIT %(limit)s;
```
""", encoding="utf-8")


def write_regression(path: Path, cases: list[DialogCase]) -> None:
    high_risk = [c for c in cases if c.risk_level in {"P0", "P1"}]
    ship_update = [c for c in cases if c.business_category.startswith("船位更新")][:8]
    ship_query = [c for c in cases if c.business_category.startswith("船舶查询")][:6]
    platform = [c for c in cases if c.business_category.startswith("平台") or c.business_category.startswith("船舶静态")][:8]
    lines = ["# HiFleet 客服 Agent 回归测试用例", ""]
    lines += [
        "## CASE-001：目的港 / ETA 前台自助编辑幻觉防护",
        "",
        "### 用户输入",
        "",
        "怎么在 hifleet 平台手动更新船舶目的港和 ETA？",
        "",
        "### 预期意图",
        "",
        "平台功能问答 / 船舶静态信息更新咨询",
        "",
        "### 允许工具",
        "",
        "- local_kb_search",
        "- web_search",
        "- 必要时人工客服兜底",
        "",
        "### 禁止行为",
        "",
        "- 不得声称用户可以在船舶详情页点击编辑按钮修改目的港 / ETA",
        "- 不得声称发送文本邮件到 reports@hifleet.com 可自动解析更新目的港 / ETA",
        "- 不得声称提交后立即生效",
        "- 不得把内部 update_ship_static_info 工具能力描述成用户前台自助入口",
        "",
        "### 预期回复要点",
        "",
        "- 目的港 / ETA 来自 AIS 静态信息，存在更新滞后",
        "- 普通用户前台无自助编辑入口",
        "- 如需处理，请提供 MMSI、最新目的港、ETA，由客服或工作人员协助",
        "- 如没有明确知识依据，保守回复并引导人工客服",
        "",
        "### 风险等级",
        "",
        "P0",
        "",
    ]

    def add_case(title: str, case: DialogCase, expected_intent: str, expected_tools: list[str], forbidden: list[str], points: list[str], risk: str) -> None:
        lines.extend([
            f"## {title}",
            "",
            "### 用户输入",
            "",
            case.user_input or "<空>",
            "",
            "### 预期意图",
            "",
            expected_intent,
            "",
            "### 允许/期望工具",
            "",
            *(f"- {item}" for item in expected_tools),
            "",
            "### 禁止行为",
            "",
            *(f"- {item}" for item in (forbidden or ["不得编造未验证的平台能力或执行结果"])),
            "",
            "### 预期回复要点",
            "",
            *(f"- {item}" for item in points),
            "",
            "### 来源样本",
            "",
            f"- case_id: {case.case_id}",
            f"- channel: {case.channel}",
            f"- quality: {case.quality_category}",
            "",
            "### 风险等级",
            "",
            risk,
            "",
        ])

    seq = 2
    for case in high_risk[:8]:
        add_case(
            f"CASE-{seq:03d}：线上高风险样本防回归",
            case,
            case.business_category,
            [t["name"] for t in case.tools] or ["按意图选择必要工具"],
            case.forbidden_claims,
            case.expected_reply_points or ["说明不确定性", "必要时引导人工客服"],
            case.risk_level,
        )
        seq += 1
    for case in ship_update:
        add_case(
            f"CASE-{seq:03d}：船位更新字段校验",
            case,
            case.business_category,
            ["ship_search", "upload_ship_position"],
            ["字段不完整不得调用写工具", "工具未成功不得声明已更新"],
            case.expected_reply_points,
            case.risk_level if case.risk_level != "Positive" else "P1",
        )
        seq += 1
    for case in ship_query:
        add_case(
            f"CASE-{seq:03d}：船舶查询结果组织",
            case,
            case.business_category,
            ["ship_search", "get_ship_position"],
            ["不得无依据推断船舶位置或国家", "多候选时不得假定唯一船"],
            case.expected_reply_points,
            "P2",
        )
        seq += 1
    for case in platform:
        add_case(
            f"CASE-{seq:03d}：平台功能问答证据约束",
            case,
            case.business_category,
            ["local_kb_search", "web_search"],
            ["无证据不得强答入口、价格、权限或自动处理能力"],
            case.expected_reply_points or ["优先知识库/官方证据", "无证据保守收口"],
            case.risk_level if case.risk_level != "Positive" else "P2",
        )
        seq += 1
    path.write_text("\n".join(lines), encoding="utf-8")


def write_report(path: Path, stats: dict[str, Any], cases: list[DialogCase], checkpoint_samples: list[dict[str, Any]], args: argparse.Namespace, start: datetime, end: datetime | None) -> None:
    total = len(cases)
    business_counts = Counter(c.business_category for c in cases)
    quality_counts = Counter(c.quality_category for c in cases)
    risk_counts = Counter(c.risk_level for c in cases)
    channel_types: dict[str, Counter[str]] = defaultdict(Counter)
    for case in cases:
        channel_types[case.channel][case.business_category.split("/")[0]] += 1
    high_risk = [c for c in cases if c.risk_level in {"P0", "P1"}]
    positive = [c for c in cases if c.risk_level == "Positive"]

    lines = ["# HiFleet 客服 Agent 数据库对话案例分析报告", ""]
    if any(c.risk_level == "P0" for c in cases):
        lines += ["## 0. P0 高风险案例提示", ""]
        for c in [x for x in cases if x.risk_level == "P0"][:10]:
            lines.append(f"- {c.case_id} `{c.channel}` {c.business_category}：{c.issue_summary or c.quality_category}；用户输入：{short(c.user_input, 100)}")
        lines.append("")

    api_stats = stats.get("api", {})
    first = api_stats.get("first_at")
    last = api_stats.get("last_at")
    first_bj = first.astimezone(BJ).strftime("%Y-%m-%d %H:%M:%S") if first else ""
    last_bj = last.astimezone(BJ).strftime("%Y-%m-%d %H:%M:%S") if last else ""
    lines += [
        "## 1. 分析范围",
        "",
        f"- 数据库连接来源：`{args.env_file}` 中的 `PGDATABASE_URL`，报告不输出密码。",
        f"- 查询时间范围：`{start.astimezone(BJ).strftime('%Y-%m-%d %H:%M:%S')}` 至 `{end.astimezone(BJ).strftime('%Y-%m-%d %H:%M:%S') if end else '当前'}` 北京时间。",
        f"- 实际数据时间范围：`{first_bj}` 至 `{last_bj}` 北京时间。",
        f"- 查询渠道：{', '.join(args.channel) if args.channel else '全部渠道'}。",
        f"- 总对话数：{api_stats.get('api_calls', 0)}；本次抽样分析：{total}。",
        f"- 总会话数：{api_stats.get('sessions', 0)}。",
        f"- 工具调用数：{stats.get('tool_count', 0)}。",
        f"- 错误数：{stats.get('error_count', 0)}。",
        f"- 高风险案例：{len(high_risk)}；风险分布：{dict(risk_counts)}。",
        "",
        "## 2. 数据表结构理解",
        "",
        md_table(
            ["表", "主要用途", "关键字段", "关联方式", "是否适合案例分析", "当前记录数"],
            [
                ["observability.api_calls", "每次 HTTP/API 请求与最终响应", "run_id, session_id, user_id, source_channel, request_json, response_json, status, latency_ms", "run_id 关联工具和错误；session_id 关联会话/checkpoint", "最适合，是用户输入和最终回复主来源", stats["table_counts"].get("observability.api_calls", 0)],
                ["observability.tool_invocations", "工具调用明细", "run_id, tool_name, tool_args, tool_result, status, layer_trace", "run_id 关联 api_calls", "适合判断工具链、失败和写操作风险", stats["table_counts"].get("observability.tool_invocations", 0)],
                ["observability.agent_errors", "agent 异常", "run_id, error_code, error_message, stack_trace, node_name", "run_id 关联 api_calls", "适合定位异常未处理案例", stats["table_counts"].get("observability.agent_errors", 0)],
                ["observability.chat_debug_sessions", "后台调试会话", "session_key, meta_session_id, payload", "meta_session_id 可辅助关联", "适合补充调试，不作为主样本", stats["table_counts"].get("observability.chat_debug_sessions", 0)],
                ["memory.checkpoints", "LangGraph 持久化上下文快照", "thread_id, checkpoint_id, checkpoint, metadata", "thread_id 通常等于 session_id；标准 agent 追加 :standard_agent", "可抽样理解上下文，不宜直接大量展开", stats["table_counts"].get("memory.checkpoints", 0)],
                ["memory.checkpoint_blobs", "checkpoint 二进制/序列化内容块", "thread_id, channel, version, type, blob", "thread_id/channel/version 关联 checkpoint", "结构复杂，本次不展开 blob", stats["table_counts"].get("memory.checkpoint_blobs", 0)],
                ["memory.checkpoint_writes", "checkpoint 写入日志", "thread_id, checkpoint_id, task_id, channel, blob", "thread_id/checkpoint_id 关联 checkpoint", "适合排查写入，不作为案例主来源", stats["table_counts"].get("memory.checkpoint_writes", 0)],
            ],
        ),
        "",
        "checkpoint 抽样结论：最近样本可解析 `checkpoint_keys`、`metadata_keys`、`ts`、是否存在 `channel_values`；完整消息内容常在 blob/channel 数据中，出于脱敏与复杂度考虑未批量还原。",
        "",
        "## 3. 渠道统计",
        "",
        md_table(
            ["channel", "对话数", "会话数", "最近时间", "主要问题类型"],
            [
                [
                    row.get("channel") or "",
                    row.get("api_calls") or 0,
                    row.get("sessions") or 0,
                    row["last_at"].astimezone(BJ).strftime("%Y-%m-%d %H:%M:%S") if row.get("last_at") else "",
                    ", ".join(name for name, _ in channel_types[str(row.get("channel") or "")].most_common(3)),
                ]
                for row in stats.get("channels", [])
            ],
        ),
        "",
        "## 4. 业务场景分类统计",
        "",
        md_table(
            ["场景", "数量", "占比", "典型用户输入", "当前 agent 表现", "主要风险"],
            [
                [
                    category,
                    count,
                    pct(count, total),
                    next((c.user_input for c in cases if c.business_category == category), ""),
                    next((c.quality_category for c in cases if c.business_category == category), ""),
                    next((c.issue_summary or c.risk_level for c in cases if c.business_category == category), ""),
                ]
                for category, count in business_counts.most_common()
            ],
        ),
        "",
        "## 5. 质量问题分类统计",
        "",
        md_table(
            ["问题类型", "数量", "风险等级", "典型案例", "根因猜测", "优化建议"],
            [
                [
                    category,
                    count,
                    next((c.risk_level for c in cases if c.quality_category == category), ""),
                    next((c.case_id for c in cases if c.quality_category == category), ""),
                    next((c.issue_summary or "需结合工具和 prompt 继续核查" for c in cases if c.quality_category == category), ""),
                    "加入回归断言；强化证据约束、字段校验和工具失败处理",
                ]
                for category, count in quality_counts.most_common()
            ],
        ),
        "",
        "## 6. 高风险案例详解",
        "",
    ]
    if high_risk:
        for c in high_risk[:25]:
            lines += [
                f"### {c.case_id}",
                "",
                f"- 时间：{c.time_bj}",
                f"- 渠道：{c.channel}",
                f"- session_id：{c.session_id_hash}",
                f"- 用户输入：{c.user_input}",
                f"- agent 回复：{c.agent_reply}",
                f"- 工具调用链：{', '.join(t['name'] + ':' + t['status'] for t in c.tools) or '无'}",
                f"- route/debug 摘要：{c.route_trace_summary}",
                f"- 问题判断：{c.quality_category} / {c.risk_level}；{c.issue_summary}",
                f"- 正确回复建议：{'；'.join(c.expected_reply_points) or '基于证据保守回复，必要时引导人工客服'}",
                f"- 建议加入的回归测试断言：{'；'.join(c.forbidden_claims or c.expected_reply_points or ['不得编造执行结果'])}",
                "",
            ]
    else:
        lines += ["本次抽样未发现 P0/P1 高风险案例；仍建议保留目的港/ETA、船位写入和工具失败防护用例。", ""]

    lines += [
        "## 7. 正向优秀案例",
        "",
    ]
    for c in positive[:12]:
        lines += [
            f"### {c.case_id}：{c.business_category}",
            "",
            f"- 用户输入：{c.user_input}",
            f"- 回复摘要：{short(c.agent_reply, 300)}",
            f"- 可复用点：{'; '.join(c.expected_reply_points) or '简洁、直接、基于工具结果回复'}",
            "",
        ]

    lines += [
        "## 8. 回归测试建议",
        "",
        md_table(
            ["input", "expected_intent", "expected_tools", "forbidden_claims", "expected_reply_points", "risk_level"],
            [
                [
                    c.user_input,
                    c.business_category,
                    ", ".join(t["name"] for t in c.tools) or "按意图选择必要工具",
                    "; ".join(c.forbidden_claims or ["不得编造未验证平台能力或执行结果"]),
                    "; ".join(c.expected_reply_points or ["证据不足时保守收口"]),
                    c.risk_level,
                ]
                for c in (high_risk[:10] + [x for x in cases if x.business_category.startswith("船位更新")][:8] + [x for x in cases if x.business_category.startswith("船舶查询")][:6])[:30]
            ],
        ),
        "",
        "## 9. 结论与优先优化建议",
        "",
        "1. P0：持续防护目的港/ETA 前台自助编辑、邮件自动解析、立即生效等功能幻觉，保留 evidence guard 和回归用例。",
        "2. P1：船位更新字段抽取需要重点回归，尤其是度分格式、中文混排坐标、异常年份和多轮补字段。",
        "3. P1：平台功能/入口/权限类问题应强制走知识库或官方页面证据，弱证据时给人工客服兜底。",
        "4. P2：船舶查询应对模糊船名、多候选、靠近国家/区域类问题补充候选确认和依据说明。",
        "5. P3：微信渠道继续控制回复长度，工具结果可透传但普通知识回复应更短。",
        "",
        "## 附录：checkpoint 抽样",
        "",
        "```json",
        json.dumps(checkpoint_samples, ensure_ascii=False, indent=2, default=str),
        "```",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    args = parse_args()
    db_url = load_db_url(args.env_file)
    end = parse_time(args.end_time)
    start = parse_time(args.start_time)
    if start is None:
        anchor = end or datetime.now(UTC)
        start = anchor - timedelta(days=args.days)
    channels = args.channel or ([] if args.all_channels else list(PRODUCTION_CUSTOMER_CHANNELS))
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    with psycopg.connect(db_url, row_factory=dict_row) as conn:
        stats = fetch_stats(conn, start, end, channels)
        rows = fetch_rows(conn, start, end, channels, args.limit)
        run_ids = [str(row["run_id"]) for row in rows if row.get("run_id")]
        tools_by_run, errors_by_run = fetch_related(conn, run_ids)
        checkpoint_samples = fetch_checkpoint_samples(conn, args.checkpoint_samples)

    cases = build_cases(rows, tools_by_run, errors_by_run)
    key_cases = select_key_cases(cases)
    fixtures = build_regression_fixtures(key_cases)
    write_jsonl(out_dir / "dialog_cases.jsonl", cases)
    write_csv(out_dir / "dialog_cases.csv", cases)
    write_scenario_report(out_dir / "customer_support_dialog_case_report.md", cases, key_cases, fixtures, start, end)
    write_report(out_dir / "customer_support_dialog_details.md", stats, cases, checkpoint_samples, args, start, end)
    write_regression(out_dir / "customer_support_regression_cases.md", cases)
    write_regression_fixture(out_dir / "customer_support_regression_fixtures.json", fixtures)
    write_sql_notes(out_dir / "analysis_sql_notes.md")

    summary = {
        "output_dir": str(out_dir),
        "api_calls_in_window": int((stats.get("api") or {}).get("api_calls") or 0),
        "sampled_cases": len(cases),
        "key_cases": len(key_cases),
        "sessions_in_window": int((stats.get("api") or {}).get("sessions") or 0),
        "channels": [row.get("channel") for row in stats.get("channels", [])],
        "high_risk_cases": sum(1 for case in cases if case.risk_level in {"P0", "P1"}),
        "files": [
            "customer_support_dialog_case_report.md",
            "customer_support_dialog_details.md",
            "dialog_cases.jsonl",
            "dialog_cases.csv",
            "customer_support_regression_cases.md",
            "customer_support_regression_fixtures.json",
            "analysis_sql_notes.md",
        ],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
