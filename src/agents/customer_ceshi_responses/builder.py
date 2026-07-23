from __future__ import annotations

import json
import logging
import mimetypes
import os
import re
import threading
import time
from datetime import datetime
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Iterator
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from openai import OpenAI
from pydantic import ValidationError

from llm_gateway import build_chat_model, resolve_role_base_url, safe_default_headers

from agents.customer_ceshi_v2.contracts import InspectMediaRequest, MediaAsset, Observation, PerceptionPacket, ToolCall
from agents.customer_ceshi_v2.tools import CapabilityRegistry, DENIED_TOOL_NAMES
from agents.profiles import read_profile_prompt
from agents.customer_ceshi_v2.tracing import safe_trace
from agents.customer_ceshi_responses.ship_updates import PositionNormalizer, ShipIdentityNormalizer, ShipUpdateDraftStore, StaticFieldNormalizer, TimeNormalizer
from agents.customer_ceshi_responses.claim_guard import guard_claims, limit_reply
from agents.customer_ceshi_responses.scenarios import classify as classify_scenario
from skills.adapters.customer_ceshi import build_customer_ceshi_bundle
from skills.core.contracts import ToolDescriptor as SharedToolDescriptor
from skills.core.policy import resolve_skill_runtime
from skills.ship_info_update import validate_position_update, validate_static_update


CHECKPOINT_NAMESPACE = "customer_ceshi_responses"
logger = logging.getLogger(__name__)
DEFAULT_MAX_STEPS = 8
DEFAULT_CONTEXT_MAX_ROUNDS = 10
DEFAULT_CONTEXT_RECENT_FULL_ROUNDS = 3
READ_ONLY_TOOL_NAMES = {
    "local_kb_search", "web_search", "verify_public_page",
    "ship_search", "get_ship_position", "get_ship_archive", "get_psc_records", "get_ship_trajectory",
    "get_ship_call_ports", "get_ship_voyages", "get_last_departure", "get_current_stop", "search_ports", "get_port_detail",
    "inspect_media",
}
UPDATE_CANDIDATE_TOOL_NAME = "submit_ship_update_candidate"
PREPARE_SHIP_UPDATE_TOOL_NAME = "prepare_ship_update"
COMMIT_SHIP_UPDATE_TOOL_NAME = "commit_ship_update"
CANCEL_SHIP_UPDATE_TOOL_NAME = "cancel_ship_update"
MEDIA_UPDATE_EVIDENCE_TOOL_NAME = "record_media_update_candidate"
_SEARCH_TOOL_NAMES = {"local_kb_search", "web_search", "verify_public_page"}
_UPDATE_INTENT = re.compile(r"(?:上传|更新|修改|补充|更正|录入).{0,24}(?:船位|位置|航速|航向|静态信息|船名|imo|呼号|船型|目的港|eta|吃水)|(?:船位|位置|航速|航向|静态信息|船名|imo|呼号|船型|目的港|eta|吃水).{0,24}(?:上传|更新|修改|补充|更正|录入)", re.I)
_MEDIA_UPDATE_COMMAND = re.compile(r"(?:更新|上传|修改|更正|录入).{0,24}(?:船位|位置|ais|数据)?|(?:根据|按).{0,24}(?:图片|上图|附件|ais).{0,24}(?:更新|上传|修改)", re.I)
_POSITION_UPDATE_TIME = re.compile(r"\d{4,5}[-/]\d{1,2}[-/]\d{1,2}\s+\d{1,2}:?\d{2}(?::\d{2})?(?:\s*\(?UTC(?:[+-]\d{1,2})?\)?)?", re.I)
_CONFIRM_ONLY = re.compile(r"^(?:请)?(?:确认|确认执行|执行确认|好的确认|好，?确认)$", re.I)
_PLACEHOLDER_VALUE = re.compile(r"^(?:--|—|－|n/?a|未知|无|null|none)$", re.I)
_MEDIA_UPDATE_FIELDS = (
    "operation_type", "mmsi", "ship_name", "imo", "lon", "lat", "updatetime", "speed", "heading", "course",
    "destination", "eta", "draft", "navstatus", "ship_type", "minotype", "length", "width", "dwt", "flag", "callsign", "built_year",
)
_ACTION_SUCCESS_CLAIM = re.compile(r"(?:(?:本次|已|已经|操作|写入|上传).{0,8}(?:更新|上传|写入).{0,8}(?:成功|完成)|(?:更新|上传|写入)已(?:成功|完成))", re.I)
_NO_RESULT_CLAIM = re.compile(r"(?:未找到|没有(?:查询到|找到)|暂无).{0,12}(?:船位|数据|结果|记录)", re.I)
_QUERY_TOOL_NAMES = {"ship_search", "get_ship_position", "get_ship_archive", "get_psc_records", "get_ship_trajectory", "get_ship_call_ports", "get_ship_voyages", "get_last_departure", "get_current_stop", "search_ports", "get_port_detail"}


@dataclass(frozen=True)
class CapabilityMatrix:
    responses: bool = False
    responses_tools: bool = False
    previous_response_id: bool = False
    chat_function_calling: bool = False
    streaming: bool = False
    reason: str = "not_probed"

    def as_dict(self) -> dict[str, Any]:
        return {
            "responses": self.responses,
            "responses_tools": self.responses_tools,
            "previous_response_id": self.previous_response_id,
            "chat_function_calling": self.chat_function_calling,
            "streaming": self.streaming,
            "reason": self.reason,
        }


def runtime_config(cfg: dict[str, Any]) -> dict[str, Any]:
    config = dict(cfg.get("config") or cfg or {})
    nested = config.get("customer_ceshi_runtime")
    values = dict(nested) if isinstance(nested, dict) else {}
    return {
        "mode": str(values.get("mode") or config.get("customer_ceshi_runtime_mode") or "legacy_v2"),
        "fallback_mode": str(values.get("fallback_mode") or config.get("customer_ceshi_fallback_mode") or "chat_function_calling"),
        "responses_enabled": bool(values.get("responses_enabled", config.get("customer_ceshi_responses_enabled", True))),
        "chat_fallback_enabled": bool(values.get("chat_fallback_enabled", config.get("customer_ceshi_chat_fallback_enabled", True))),
        "legacy_v2_enabled": bool(values.get("legacy_v2_enabled", config.get("customer_ceshi_legacy_v2_enabled", True))),
        "text_model": dict(values.get("text_model") or {}),
        "responses": dict(values.get("responses") or {}),
        "chat_fallback": dict(values.get("chat_fallback") or {}),
        "context": dict(values.get("context") or {}),
    }


def _nested_config(config: dict[str, Any], *keys: str) -> dict[str, Any]:
    value: Any = config
    for key in keys:
        if not isinstance(value, dict):
            return {}
        value = value.get(key)
    return dict(value) if isinstance(value, dict) else {}


def _responses_settings(config: dict[str, Any], provider: str) -> dict[str, Any]:
    runtime = _nested_config(config, "customer_ceshi_runtime")
    responses = dict(runtime.get("responses") or {})
    return dict(responses.get(provider) or {})


def _context_settings(config: dict[str, Any]) -> dict[str, Any]:
    runtime = _nested_config(config, "customer_ceshi_runtime")
    values = dict(runtime.get("context") or {})
    return {
        "max_rounds": max(1, int(values.get("max_rounds", DEFAULT_CONTEXT_MAX_ROUNDS))),
        "recent_full_rounds": max(0, int(values.get("recent_full_rounds", DEFAULT_CONTEXT_RECENT_FULL_ROUNDS))),
        "summary_chars_per_turn": max(120, int(values.get("summary_chars_per_turn", 600))),
        "full_chars_per_turn": max(200, int(values.get("full_chars_per_turn", 1600))),
        "max_facts_per_turn": max(0, int(values.get("max_facts_per_turn", 3))),
        "session_ttl_seconds": max(60, int(values.get("session_ttl_seconds", 86400))),
    }


def _search_settings(config: dict[str, Any]) -> dict[str, int]:
    values = _nested_config(config, "customer_ceshi_runtime", "search")
    return {
        "max_local_kb_calls": max(1, int(values.get("max_local_kb_calls", 2))),
        "max_web_calls": max(1, int(values.get("max_web_calls", 2))),
        "max_facts": max(1, int(values.get("max_facts", 3))),
        "max_sources": max(1, int(values.get("max_sources", 2))),
    }


def _direct_update_enabled(config: dict[str, Any]) -> bool:
    values = _nested_config(config, "customer_ceshi_runtime", "direct_updates")
    return bool(values.get("enabled", False))


def _media_evidence_settings(config: dict[str, Any]) -> dict[str, Any]:
    values = _nested_config(config, "customer_ceshi_runtime", "direct_updates")
    return {
        "ttl_seconds": max(60, int(values.get("media_evidence_ttl_seconds", 600))),
        "minimum_confidence": str(values.get("media_evidence_minimum_confidence", "high")).lower(),
    }


@dataclass
class ConversationTurn:
    user_text: str
    answer_text: str
    facts: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)


class ConversationMemory:
    """In-process, sanitized session memory; provider response chains never cross turns."""

    def __init__(self, settings: dict[str, Any]) -> None:
        self.settings = settings
        self._turns: dict[str, list[ConversationTurn]] = {}

    @staticmethod
    def _clip(value: str, size: int) -> str:
        value = re.sub(r"\s+", " ", str(value or "")).strip()
        return value if len(value) <= size else f"{value[:size - 1]}…"

    def _prune(self) -> None:
        cutoff = time.time() - self.settings["session_ttl_seconds"]
        for key, turns in list(self._turns.items()):
            kept = [turn for turn in turns if turn.created_at >= cutoff]
            if kept:
                self._turns[key] = kept[-self.settings["max_rounds"]:]
            else:
                self._turns.pop(key, None)

    def render(self, session_key: str) -> tuple[str, int, bool]:
        self._prune()
        turns = self._turns.get(session_key, [])[-self.settings["max_rounds"]:]
        if not turns:
            return "", 0, False
        recent_count = min(self.settings["recent_full_rounds"], len(turns))
        summary_turns = turns[:-recent_count] if recent_count else turns
        recent_turns = turns[-recent_count:] if recent_count else []
        sections: list[str] = ["已确认的历史对话（仅供理解上下文；不得把它当作当前事实重新断言）："]
        if summary_turns:
            sections.append("较早轮次摘要：")
            for index, turn in enumerate(summary_turns, start=1):
                facts = "；".join(self._clip(fact, 160) for fact in turn.facts[: self.settings["max_facts_per_turn"]])
                source = f"；已验证事实：{facts}" if facts else ""
                sections.append(
                    f"[{index}] 用户：{self._clip(turn.user_text, self.settings['summary_chars_per_turn'])}"
                    f"；回复：{self._clip(turn.answer_text, self.settings['summary_chars_per_turn'])}{source}"
                )
        if recent_turns:
            sections.append("最近完整轮次：")
            for index, turn in enumerate(recent_turns, start=len(summary_turns) + 1):
                sections.append(
                    f"[{index}] 用户：{self._clip(turn.user_text, self.settings['full_chars_per_turn'])}\n"
                    f"助手：{self._clip(turn.answer_text, self.settings['full_chars_per_turn'])}"
                )
        return "\n".join(sections), len(turns), bool(summary_turns)

    def record(self, session_key: str, *, user_text: str, answer_text: str, observations: list[dict[str, Any]] | None = None) -> None:
        self._prune()
        facts: list[str] = []
        sources: list[str] = []
        for observation in observations or []:
            if observation.get("status") not in {"success", "partial"}:
                continue
            sources.append(str(observation.get("capability") or ""))
            facts.extend(str(item) for item in observation.get("facts", []) if str(item).strip())
        turn = ConversationTurn(
            user_text=self._clip(user_text, self.settings["full_chars_per_turn"]),
            answer_text=self._clip(answer_text, self.settings["full_chars_per_turn"]),
            facts=[self._clip(item, 240) for item in facts[: self.settings["max_facts_per_turn"]]],
            sources=[source for source in sources if source][: self.settings["max_facts_per_turn"]],
        )
        self._turns.setdefault(session_key, []).append(turn)
        self._turns[session_key] = self._turns[session_key][-self.settings["max_rounds"]:]


@dataclass
class MediaUpdateEvidence:
    """Short-lived, structured fields extracted from the latest media request only."""

    fields: dict[str, str]
    media_types: tuple[str, ...]
    source_turn_id: str
    created_at: float = field(default_factory=time.time)


class MediaUpdateEvidenceLedger:
    """Keep updateable media facts outside conversational model context and raw media storage."""

    def __init__(self, settings: dict[str, Any]) -> None:
        self.settings = settings
        self._entries: dict[str, MediaUpdateEvidence] = {}

    def _prune(self) -> None:
        cutoff = time.time() - self.settings["ttl_seconds"]
        for key, evidence in list(self._entries.items()):
            if evidence.created_at < cutoff:
                self._entries.pop(key, None)

    def get(self, session_key: str) -> MediaUpdateEvidence | None:
        self._prune()
        return self._entries.get(session_key)

    def put(self, session_key: str, evidence: MediaUpdateEvidence) -> None:
        self._prune()
        self._entries[session_key] = evidence

    def clear(self, session_key: str) -> None:
        self._entries.pop(session_key, None)


_PROCESS_MEMORY_LOCK = threading.Lock()
_PROCESS_MEMORY: ConversationMemory | None = None
_PROCESS_MEMORY_SETTINGS: dict[str, Any] | None = None
_PROCESS_MEDIA_EVIDENCE: MediaUpdateEvidenceLedger | None = None
_PROCESS_MEDIA_EVIDENCE_SETTINGS: dict[str, Any] | None = None


def _shared_conversation_memory(config: dict[str, Any]) -> ConversationMemory:
    """Reuse customer_ceshi memory across host graph rebuilds within one process."""
    global _PROCESS_MEMORY, _PROCESS_MEMORY_SETTINGS
    settings = _context_settings(config)
    with _PROCESS_MEMORY_LOCK:
        if _PROCESS_MEMORY is None or _PROCESS_MEMORY_SETTINGS != settings:
            _PROCESS_MEMORY = ConversationMemory(settings)
            _PROCESS_MEMORY_SETTINGS = settings
        return _PROCESS_MEMORY


def _shared_media_update_evidence(config: dict[str, Any]) -> MediaUpdateEvidenceLedger:
    global _PROCESS_MEDIA_EVIDENCE, _PROCESS_MEDIA_EVIDENCE_SETTINGS
    settings = _media_evidence_settings(config)
    with _PROCESS_MEMORY_LOCK:
        if _PROCESS_MEDIA_EVIDENCE is None or _PROCESS_MEDIA_EVIDENCE_SETTINGS != settings:
            _PROCESS_MEDIA_EVIDENCE = MediaUpdateEvidenceLedger(settings)
            _PROCESS_MEDIA_EVIDENCE_SETTINGS = settings
        return _PROCESS_MEDIA_EVIDENCE


def probe_capabilities(client: Any | None) -> CapabilityMatrix:
    """Probe only client behavior; no model name implies support."""
    if client is None:
        return CapabilityMatrix(reason="client_unavailable")
    chat = tools = streaming = False
    try:
        client.invoke([HumanMessage(content="Reply with OK.")])
        chat = True
    except Exception as exc:
        return CapabilityMatrix(reason=f"chat_unavailable:{type(exc).__name__}")
    try:
        bound = client.bind_tools([{"type": "function", "function": {"name": "capability_probe", "description": "probe", "parameters": {"type": "object", "properties": {}}}}])
        bound.invoke([HumanMessage(content="Call capability_probe.")])
        tools = True
    except Exception:
        pass
    try:
        next(iter(client.stream([HumanMessage(content="Reply with OK.")])) )
        streaming = True
    except Exception:
        pass
    # LangChain ChatOpenAI does not expose a provider-independent Responses API.
    # A dedicated adapter may set this matrix after probing its own responses client.
    return CapabilityMatrix(chat_function_calling=tools, streaming=streaming, reason="chat_probe")


class _NamespacedRuntime:
    """Checkpointed graph facade isolated from the production customer_support graph."""

    def __init__(self, runtime: "NativeToolRuntime") -> None:
        self.runtime = runtime
        graph = StateGraph(dict)
        graph.add_node("customer_ceshi_responses", self._run_native_loop)
        graph.add_edge(START, "customer_ceshi_responses")
        graph.add_edge("customer_ceshi_responses", END)
        self._graph = graph.compile(checkpointer=MemorySaver())

    def _run_native_loop(self, state: dict[str, Any], config: RunnableConfig) -> dict[str, Any]:
        return self.runtime.invoke(dict(state), config)

    @staticmethod
    def scoped_config(config: dict[str, Any] | None) -> dict[str, Any]:
        scoped = dict(config or {})
        configurable = dict(scoped.get("configurable") or {})
        thread_id = str(configurable.get("thread_id") or "default")
        prefix = f"{CHECKPOINT_NAMESPACE}:"
        configurable["thread_id"] = thread_id if thread_id.startswith(prefix) else f"{prefix}{thread_id}"
        configurable["checkpoint_ns"] = CHECKPOINT_NAMESPACE
        scoped["configurable"] = configurable
        return scoped

    def invoke(self, input: dict[str, Any], config: dict[str, Any] | None = None, **_: Any) -> dict[str, Any]:
        return self._graph.invoke(input, self.scoped_config(config))

    async def ainvoke(self, input: dict[str, Any], config: dict[str, Any] | None = None, **_: Any) -> dict[str, Any]:
        return await self._graph.ainvoke(input, self.scoped_config(config))

    def stream(self, input: dict[str, Any], config: dict[str, Any] | None = None, **kwargs: Any) -> Iterator[Any]:
        yield from self._graph.stream(input, self.scoped_config(config), **kwargs)

    async def astream(self, input: dict[str, Any], config: dict[str, Any] | None = None, **kwargs: Any) -> AsyncIterator[Any]:
        async for update in self._graph.astream(input, self.scoped_config(config), **kwargs):
            yield update

    def get_graph(self) -> Any:
        """Expose the compiled graph for the host loop tracer."""
        return self._graph.get_graph()

    def __getattr__(self, name: str) -> Any:
        """Preserve the CompiledStateGraph inspection surface used by host tracing."""
        return getattr(self._graph, name)


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        return "\n".join(str(part.get("text", "")) for part in content if isinstance(part, dict) and part.get("type") in {"text", "input_text"}).strip()
    return str(content or "").strip()


_HTML_ANCHOR = re.compile(r'<a\s+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', re.I | re.S)
_MARKDOWN_LINK = re.compile(r'\[([^\]]+)\]\((https?://[^)\s]+)\)')
_MEDIA_CANDIDATE_MARKER = re.compile(r'"?media_update_candidate"?', re.I)
_SHIP_POSITION_QUERY = re.compile(r"船位|当前位置|实时坐标|现在到哪|到哪了|在哪里|在哪儿|位置", re.I)
_MMSI_IN_TEXT = re.compile(r"MMSI\s*[:：]\s*(\d{9})", re.I)


def _wechat_ship_link(mmsi: str) -> str:
    return (
        "https://open.weixin.qq.com/connect/oauth2/authorize?"
        "appid=wx9d402b54c1d84ebf&"
        "redirect_uri=http://www.hifleet.com/wap-simple/index.html&"
        f"response_type=code&scope=snsapi_base&state={mmsi}#wechat_redirect"
    )


def _fact_line(value: str, *labels: str) -> str:
    labels_pattern = "|".join(re.escape(label) for label in labels)
    match = re.search(rf"(?:^|\n)(?:{labels_pattern})\s*[:：]\s*([^\n]+)", value, re.I)
    return match.group(1).strip() if match else ""


def _ship_search_position_card(value: str) -> str:
    """Normalize ship_search current-dynamic text into the customer_support-style WeChat card."""
    text = str(value or "")
    mmsi_match = _MMSI_IN_TEXT.search(text)
    if not mmsi_match:
        return ""
    mmsi = mmsi_match.group(1)
    name = _fact_line(text, "船舶名称")
    ship_type = _fact_line(text, "船型")
    flag = _fact_line(text, "船旗")
    size = _fact_line(text, "船长/船宽", "船舶尺寸")
    coordinates = _fact_line(text, "经度/纬度", "实时坐标")
    updated = _fact_line(text, "更新时间", "更新于")
    status = _fact_line(text, "航行状态")
    speed = _fact_line(text, "航速")
    heading = _fact_line(text, "船首向/航迹向", "航首向")
    draft = _fact_line(text, "当前吃水", "吃水")
    destination = _fact_line(text, "船报目的港", "目的港")
    imo_match = re.search(r"IMO\s*[:：]\s*([^\n|]+)", text, re.I)
    imo = imo_match.group(1).strip() if imo_match else ""
    lines: list[str] = [name or f"MMSI {mmsi}", f"MMSI: {mmsi}" + (f" | IMO: {imo}" if imo and imo not in {"-", "--"} else "")]
    if flag or ship_type:
        lines.append("船旗: " + (flag or "-") + (f" | 船型: {ship_type}" if ship_type else ""))
    if size:
        normalized_size = re.sub(r"\s*/\s*", " / ", size.replace("米/", "米 / "))
        lines.append(f"船舶尺寸: {normalized_size}")
    if coordinates:
        lines.append(f"实时坐标：{coordinates}")
    lines.append(f"点击查看：{_wechat_ship_link(mmsi)}")
    if updated:
        lines.append(f"更新于: {updated}")
    if status or draft:
        lines.append("航行状态：" + (status or "-") + (f" | 吃水: {draft}" if draft else ""))
    if speed or heading:
        lines.append("航速: " + (speed or "-") + (f" | 船首向/航迹向: {heading}" if heading else ""))
    if destination and destination not in {"-", "--"}:
        lines.append(f"目的港: {destination}")
    lines.append("数据来源于 HIFLEET 全球 AIS 网络，定位可能存在延迟，仅供参考航行决策。")
    return "\n".join(lines)


def _media_envelope_from_text(value: str) -> tuple[str, dict[str, Any] | None, bool]:
    """Extract a structured multimodal envelope even if the provider wraps it in prose/fences."""
    text = str(value or "").strip()
    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", text):
        try:
            payload, length = decoder.raw_decode(text[match.start():])
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict) or "media_update_candidate" not in payload:
            continue
        answer = payload.get("answer")
        remainder = f"{text[:match.start()]}{text[match.start() + length:]}".strip()
        if isinstance(answer, str) and answer.strip():
            return answer.strip(), dict(payload.get("media_update_candidate") or {}), True
        return remainder, dict(payload.get("media_update_candidate") or {}), True
    return text, None, bool(_MEDIA_CANDIDATE_MARKER.search(text))


def _wechat_plain_text(value: str, *, media_fallback: str = "") -> str:
    """Render only customer-visible text suitable for WeChat; never expose model envelopes."""
    text, candidate, had_media_candidate = _media_envelope_from_text(value)
    original = str(value or "")
    if had_media_candidate and candidate is not None and not re.search(r'"answer"\s*:', original):
        return media_fallback or "已识别到附件信息，请发送具体问题或明确说明需要查询、更新的内容。"
    if had_media_candidate and not text:
        return media_fallback or "已识别到附件信息，请发送具体问题或明确说明需要查询、更新的内容。"
    if had_media_candidate and _MEDIA_CANDIDATE_MARKER.search(text):
        answer_match = re.search(r'"answer"\s*:\s*"((?:\\.|[^"\\])*)"', original, re.S)
        if answer_match:
            try:
                text = json.loads(f'"{answer_match.group(1)}"').strip()
            except json.JSONDecodeError:
                text = answer_match.group(1).replace("\\n", "\n").strip()
        else:
            prefix = _MEDIA_CANDIDATE_MARKER.split(text, maxsplit=1)[0]
            text = re.sub(r"[,{\[]?\s*['\"]?answer['\"]?\s*:\s*", "", prefix).strip(" \n`{[\":,")
        if not text:
            return media_fallback or "已识别到附件信息，请发送具体问题或明确说明需要查询、更新的内容。"

    def anchor(match: re.Match[str]) -> str:
        url, label = match.group(1).strip(), re.sub(r"\s+", "", match.group(2)).strip()
        return f"{label or '点击查看'}：{url}"

    text = _HTML_ANCHOR.sub(anchor, text)
    text = _MARKDOWN_LINK.sub(lambda match: f"{match.group(1).strip()}：{match.group(2).strip()}", text)
    text = re.sub(r"```(?:[a-zA-Z0-9_+-]+)?\s*", "", text)
    text = text.replace("```", "")
    text = re.sub(r"(?<!\\)\*\*(.*?)\*\*", r"\1", text)
    text = re.sub(r"(?<!\\)__(.*?)__", r"\1", text)
    text = text.replace("`", "")
    text = re.sub(r"(?<!\w)_(.+?)_(?!\w)", r"\1", text)
    text = text.replace("*", "")
    cleaned: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        line = re.sub(r"^#{1,6}\s*", "", line)
        line = re.sub(r"^>\s*", "", line)
        line = re.sub(r"^(?:[-+*]|[•·])\s+", "", line)
        line = re.sub(r"^(\d+)\s*[.)、]\s*", r"\1. ", line)
        if line:
            cleaned.append(line)
    if len(cleaned) > 1 and cleaned[-1] == cleaned[0]:
        cleaned.pop()
    return "\n".join(cleaned[:12]).strip() or (media_fallback if had_media_candidate else "")


def _wechat_position_result(user_text: str, observations: list[dict[str, Any]]) -> str:
    """Use the verified ship-position tool card instead of a model rewrite for position queries."""
    if not _SHIP_POSITION_QUERY.search(user_text or ""):
        return ""
    cards: list[str] = []
    for observation in observations:
        if observation.get("capability") not in {"get_ship_position", "ship_search"} or observation.get("status") not in {"success", "partial"}:
            continue
        for fact in observation.get("facts") or []:
            raw_fact = str(fact)
            card = _ship_search_position_card(raw_fact) if observation.get("capability") == "ship_search" else _wechat_plain_text(raw_fact)
            if card and ("MMSI:" in card or "点击查看" in card or "实时坐标" in card):
                cards.append(card)
    return "\n\n".join(dict.fromkeys(cards))


def _message_type(message: Any) -> str:
    if isinstance(message, dict):
        role = str(message.get("role") or message.get("type") or "").lower()
        return {"user": "human", "assistant": "ai"}.get(role, role)
    return str(getattr(message, "type", "") or "").lower()


def _message_content(message: Any) -> Any:
    return message.get("content", "") if isinstance(message, dict) else getattr(message, "content", "")


def _latest_human(messages: list[Any]) -> HumanMessage | None:
    for message in reversed(messages or []):
        if isinstance(message, HumanMessage) or _message_type(message) == "human":
            return message if isinstance(message, HumanMessage) else HumanMessage(content=_message_content(message))
    return None


def _orchestrator_messages(messages: list[Any], assets: list[MediaAsset]) -> list[Any]:
    """Keep raw multimodal payloads out of the text orchestrator's Chat fallback."""
    asset_note = "\nAvailable attached assets: " + ", ".join(f"{asset.asset_id}:{asset.kind}" for asset in assets) if assets else ""
    normalized: list[Any] = []
    for message in messages:
        message_type = _message_type(message)
        content = _message_content(message)
        if message_type == "human" or isinstance(message, HumanMessage):
            normalized.append(HumanMessage(content=f"{_text(content)}{asset_note}".strip()))
        elif message_type == "system" or isinstance(message, SystemMessage):
            normalized.append(SystemMessage(content=_text(content)))
        elif message_type == "ai" or isinstance(message, AIMessage):
            normalized.append(AIMessage(content=_text(content)))
    return normalized


def _assets(message: Any | None) -> list[MediaAsset]:
    output: list[MediaAsset] = []
    content = getattr(message, "content", []) if message else []
    if not isinstance(content, list):
        return output
    kind_by_type = {"image_url": "image", "input_audio": "audio", "video_url": "video", "file_url": "file"}
    for index, part in enumerate(content):
        if not isinstance(part, dict) or part.get("type") not in kind_by_type:
            continue
        detail = part.get(part["type"], {}) or {}
        url = str(detail.get("url", "")) if isinstance(detail, dict) else ""
        if url:
            output.append(MediaAsset(asset_id=f"asset-{index}", kind=kind_by_type[part["type"]], url=url))
    return output


def _tool_schema(tool: Any) -> dict[str, Any]:
    schema_model = getattr(tool, "args_schema", None)
    parameters = schema_model.model_json_schema() if schema_model is not None and hasattr(schema_model, "model_json_schema") else {"type": "object", "properties": {}}
    description = str(getattr(tool, "description", ""))[:900]
    description += "\nRead-only observation tool. Return source-backed facts, empty-result meaning, and retryable errors; do not decide whether the agent can answer or should continue."
    return {"type": "function", "function": {"name": tool.name, "description": description, "parameters": parameters}}


def _update_candidate_schema(*, responses: bool) -> dict[str, Any]:
    parameters = {
        "type": "object",
        "properties": {
            "operation_type": {"type": "string", "enum": ["position_update", "static_update"]},
            "mmsi": {"type": "string", "description": "Current user-provided nine-digit MMSI."},
            "ship_name": {"type": "string", "description": "Current user-provided ship name, if any."},
            "imo": {"type": "string", "description": "Current user-provided IMO, if any."},
            "lon": {"type": "string"}, "lat": {"type": "string"}, "updatetime": {"type": "string"},
            "speed": {"type": "string"}, "heading": {"type": "string"}, "course": {"type": "string"},
            "destination": {"type": "string"}, "eta": {"type": "string"}, "draft": {"type": "string"}, "navstatus": {"type": "string"},
            "ship_type": {"type": "string"}, "minotype": {"type": "string"}, "length": {"type": "string"}, "width": {"type": "string"},
            "dwt": {"type": "string"}, "flag": {"type": "string"}, "callsign": {"type": "string"}, "built_year": {"type": "string"},
        },
        "required": ["operation_type"],
    }
    item = {
        "type": "function",
        "name": UPDATE_CANDIDATE_TOOL_NAME,
        "description": "Internal customer_ceshi update submission. Use only when the current user explicitly asks to update a ship and every supplied value comes from this current request. The runtime validates identity and required fields before any write.",
        "parameters": parameters,
    }
    return item if responses else {"type": "function", "function": {key: value for key, value in item.items() if key != "type"}}


def _ship_update_transaction_schemas(*, responses: bool) -> list[dict[str, Any]]:
    from skills.ship_info_update import transaction_descriptors

    descriptors = transaction_descriptors()
    return [descriptor.responses_schema() if responses else descriptor.chat_schema() for descriptor in descriptors]


def _media_update_evidence_schema() -> dict[str, Any]:
    """Internal-only media evidence recorder exposed to the Doubao Responses loop."""
    properties = {
        "operation_type": {"type": "string", "enum": ["position_update", "static_update"]},
        "mmsi": {"type": "string"}, "ship_name": {"type": "string"}, "imo": {"type": "string"},
        "lon": {"type": "string"}, "lat": {"type": "string"}, "updatetime": {"type": "string"},
        "speed": {"type": "string"}, "heading": {"type": "string"}, "course": {"type": "string"},
        "destination": {"type": "string"}, "eta": {"type": "string"}, "draft": {"type": "string"}, "navstatus": {"type": "string"},
        "ship_type": {"type": "string"}, "minotype": {"type": "string"}, "length": {"type": "string"}, "width": {"type": "string"},
        "dwt": {"type": "string"}, "flag": {"type": "string"}, "callsign": {"type": "string"}, "built_year": {"type": "string"},
        "missing_fields": {"type": "array", "items": {"type": "string"}},
        "validation_errors": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
    }
    return {
        "type": "function",
        "name": MEDIA_UPDATE_EVIDENCE_TOOL_NAME,
        "description": "Internal-only. Call only when the currently attached media clearly contains complete AIS fields that may be reused for a later explicit ship update. Never call for ordinary screenshots, product pages, or uncertain OCR.",
        "parameters": {"type": "object", "properties": properties, "required": ["operation_type", "mmsi", "confidence"]},
    }


def _session_key(payload: dict[str, Any], config: dict[str, Any]) -> str:
    configurable = dict(config.get("configurable") or {})
    tenant = str(payload.get("tenant_id") or payload.get("tenant") or "default")
    user = str(payload.get("user_id") or "anonymous")
    session = str(payload.get("session_id") or configurable.get("thread_id") or "default")
    return f"{tenant}:{user}:{session}"


def _provider_error_summary(exc: Exception) -> str:
    """Keep only stable provider diagnostics; never persist raw request/response text."""
    status_code = getattr(exc, "status_code", None)
    body = getattr(exc, "body", None)
    body = body if isinstance(body, dict) else {}
    error = body.get("error") if isinstance(body.get("error"), dict) else body
    code = str(error.get("code") or error.get("type") or "") if isinstance(error, dict) else ""
    parameter = str(error.get("param") or "") if isinstance(error, dict) else ""
    details = [type(exc).__name__]
    if status_code is not None:
        details.append(f"status={status_code}")
    if code:
        details.append(f"code={code[:80]}")
    if parameter:
        details.append(f"param={parameter[:80]}")
    return ";".join(details)


def _media_schema() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "inspect_media",
            "description": "Use Doubao only to inspect an attached image, audio, or video. It returns observations, uncertainty, and limitations; it does not answer the user.",
            "parameters": {
                "type": "object",
                "properties": {
                    "asset_id": {"type": "string", "description": "ID of an attached asset."},
                    "objective": {"type": "string", "description": "What to inspect."},
                    "questions": {"type": "array", "items": {"type": "string"}},
                    "mode": {"type": "string", "enum": ["broad_scan", "ocr", "entity_extract", "field_extract", "visual_detail", "timeline", "transcription", "targeted_verify"]},
                    "expected_fields": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["asset_id", "objective"],
            },
        },
    }


class ResponsesMediaPerception:
    """Restricted Doubao adapter used exclusively by DeepSeek's inspect_media tool."""

    def __init__(self, client: Any | None, config: dict[str, Any]) -> None:
        self.client = client
        self.config = config
        self.model = str(_responses_settings(config, "doubao").get("image_video", {}).get("model") or _responses_settings(config, "doubao").get("model") or "")

    @staticmethod
    def _normalize_packet_payload(payload: dict[str, Any], asset: MediaAsset, request: InspectMediaRequest) -> dict[str, Any]:
        normalized = dict(payload)
        factual_summary = normalized.get("factual_summary", "")
        if isinstance(factual_summary, (dict, list)):
            factual_summary = json.dumps(factual_summary, ensure_ascii=False, separators=(",", ":"))
        normalized["factual_summary"] = str(factual_summary or "")
        confidence = normalized.get("overall_confidence", "low")
        if isinstance(confidence, (int, float)):
            confidence = "high" if confidence >= 0.8 else "medium" if confidence >= 0.5 else "low"
        normalized["overall_confidence"] = str(confidence).lower() if str(confidence).lower() in {"high", "medium", "low"} else "low"
        fields = normalized.get("fields", [])
        if isinstance(fields, dict):
            fields = [{"name": name, "value": value} for name, value in fields.items()]
        if not isinstance(fields, list):
            fields = []
        normalized_fields: list[dict[str, Any]] = []
        for item in fields:
            if isinstance(item, str):
                item = {"name": "observed_detail", "value": item}
            if not isinstance(item, dict):
                continue
            field = dict(item)
            field["name"] = str(field.get("name") or "observed_detail")
            field["asset_id"] = str(field.get("asset_id") or asset.asset_id)
            field["status"] = str(field.get("status") or "observed").lower()
            if field["status"] not in {"observed", "inferred", "uncertain", "placeholder", "conflict"}:
                field["status"] = "uncertain"
            field["confidence"] = str(field.get("confidence") or "medium").lower()
            if field["confidence"] not in {"high", "medium", "low"}:
                field["confidence"] = "low"
            normalized_fields.append(field)
        normalized["fields"] = normalized_fields
        for key in ("ocr_blocks", "visual_objects", "entities", "transcript", "events", "evidence_refs"):
            value = normalized.get(key, [])
            normalized[key] = [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []
        for key in ("visual_features", "limitations", "unresolved_questions", "conflicts"):
            value = normalized.get(key, [])
            normalized[key] = [
                json.dumps(item, ensure_ascii=False, separators=(",", ":")) if isinstance(item, (dict, list)) else str(item)
                for item in value
                if item is not None
            ] if isinstance(value, list) else []
        normalized.update({
            "asset_id": asset.asset_id,
            "media_type": asset.kind,
            "requested_objective": request.objective,
            "requested_questions": request.questions,
        })
        return normalized

    @staticmethod
    def _file_name(asset: MediaAsset) -> str:
        if asset.filename:
            return re.sub(r"[^A-Za-z0-9._-]+", "_", asset.filename)[-160:] or "attachment"
        path_name = urlparse(asset.url).path.rsplit("/", 1)[-1]
        if path_name:
            return re.sub(r"[^A-Za-z0-9._-]+", "_", path_name)[-160:] or "attachment"
        extension = {"image": ".png", "video": ".mp4", "audio": ".wav"}.get(asset.kind, "")
        return f"attachment{extension}"

    def _upload_for_perception(self, asset: MediaAsset, settings: dict[str, Any]) -> str:
        max_bytes = max(1_048_576, int(settings.get("upload_max_bytes", 32 * 1024 * 1024)))
        timeout_seconds = max(5, int(settings.get("upload_timeout_seconds", 45)))
        request = Request(asset.url, headers={"User-Agent": "customer-ceshi-media-perception/1.0"})
        with urlopen(request, timeout=timeout_seconds) as source:
            content_length = source.headers.get("Content-Length")
            if content_length and int(content_length) > max_bytes:
                raise ValueError("media_attachment_too_large")
            content = source.read(max_bytes + 1)
            content_type = source.headers.get_content_type() or mimetypes.guess_type(self._file_name(asset))[0] or "application/octet-stream"
        if not content:
            raise ValueError("media_attachment_empty")
        if len(content) > max_bytes:
            raise ValueError("media_attachment_too_large")
        created = self.client.files.create(
            file=(self._file_name(asset), content, content_type),
            purpose="user_data",
        )
        file_id = str(getattr(created, "id", "") or (created.get("id", "") if isinstance(created, dict) else ""))
        if not file_id:
            raise RuntimeError("media_file_upload_missing_id")
        status = str(getattr(created, "status", "") or (created.get("status", "") if isinstance(created, dict) else "")).lower()
        deadline = time.monotonic() + timeout_seconds
        while status == "processing" and time.monotonic() < deadline:
            time.sleep(0.5)
            created = self.client.files.retrieve(file_id)
            status = str(getattr(created, "status", "") or (created.get("status", "") if isinstance(created, dict) else "")).lower()
        if status in {"failed", "error", "cancelled", "canceled", "processing"}:
            raise RuntimeError(f"media_file_processing_{status or 'unknown'}")
        return file_id

    def _delete_uploaded_file(self, file_id: str) -> None:
        try:
            self.client.files.delete(file_id)
        except Exception:
            pass

    def inspect(self, asset: MediaAsset, request: InspectMediaRequest) -> PerceptionPacket | Observation:
        if self.client is None:
            return Observation(status="upstream_error", capability="inspect_media", warnings=["media_perception_unavailable"], retry_allowed=True)
        settings = _responses_settings(self.config, "doubao")
        model_settings = dict(settings.get("image_video") or {}) if asset.kind in {"image", "video"} else dict(settings.get("audio") or {})
        model = str(model_settings.get("model") or settings.get("model") or self.model)
        content: list[dict[str, Any]] = [{
            "type": "input_text",
            "text": (
                "你是多模态感知组件，不是客服 Agent。只返回 JSON："
                "asset_id、media_type、model、requested_objective、requested_questions、factual_summary、"
                "ocr_blocks、visual_objects、entities、fields、visual_features、overall_confidence、limitations。"
                "只记录明确可见/可听内容；区分 observed、inferred、uncertain、placeholder、conflict；"
                "若附件带有箭头、圆圈、方框、荧光笔等人工标注，必须优先观察这些标注指向或圈出的区域，"
                "分别记录标注样式、被标注对象、附近文字、所在页面/图层和空间关系；不得因截图整体复杂而忽略标注区域。"
                "对海图或地图截图，factual_summary必须说明可见图层、图例/控件、标注对象与周边船舶/区域的关系；"
                "不得回答产品规则、不得调用工具、不得执行更新、不得生成客服话术。\n"
                + json.dumps(request.model_dump(), ensure_ascii=False)
            ),
        }]
        if asset.kind not in {"image", "video", "audio"}:
            return Observation(status="invalid_input", capability="inspect_media", warnings=["unsupported_media_type"], retry_allowed=False)
        uploaded_file_id = ""
        try:
            uploaded_file_id = self._upload_for_perception(asset, settings)
            media_item: dict[str, Any] = {"type": f"input_{asset.kind}", "file_id": uploaded_file_id}
            if asset.kind == "image":
                media_item["detail"] = str(settings.get("image_detail", "high"))
            elif asset.kind == "video":
                media_item["fps"] = float(settings.get("video_fps", 1))
            content.append(media_item)
            response = self.client.responses.create(
                model=model,
                input=[{"role": "user", "content": content}],
                store=True,
                max_output_tokens=min(int(settings.get("max_output_tokens", 2048)), 2048),
                extra_body={"thinking": {"type": "disabled"}},
            )
            raw = NativeToolRuntime._responses_text(response)
            parsed = json.loads(raw[raw.find("{"):raw.rfind("}") + 1])
            parsed = self._normalize_packet_payload(parsed, asset, request)
            parsed["model"] = model
            return PerceptionPacket.model_validate(parsed)
        except ValidationError as exc:
            invalid_paths = [".".join(str(part) for part in error.get("loc") or ()) for error in exc.errors()]
            return Observation(
                status="upstream_error",
                capability="inspect_media",
                warnings=[f"media_perception_packet_invalid:{','.join(invalid_paths[:6]) or 'unknown'}"],
                retry_allowed=True,
            )
        except Exception as exc:
            return Observation(status="upstream_error", capability="inspect_media", warnings=[f"media_perception_error:{_provider_error_summary(exc)}"], retry_allowed=True)
        finally:
            if uploaded_file_id:
                self._delete_uploaded_file(uploaded_file_id)


class NativeToolRuntime:
    def __init__(self, *, client: Any, registry: CapabilityRegistry, perception: Any = None, config: dict[str, Any], mode: str, responses_client: Any | None = None, profile_prompt: str = "", tool_descriptors: tuple[SharedToolDescriptor, ...] = (), skill_runtime_metadata: dict[str, Any] | None = None) -> None:
        self.client = client
        self.registry = registry
        self.perception = perception
        self.config = config
        self.mode = mode
        self.responses_client = responses_client
        self.profile_prompt = profile_prompt.strip()
        self.tool_descriptors = tuple(tool_descriptors)
        self.skill_runtime_metadata = dict(skill_runtime_metadata or {})
        self.direct_updates_enabled = _direct_update_enabled(config)
        self.search_settings = _search_settings(config)
        if self.skill_runtime_metadata.get("mode") == "v2":
            self.search_settings["max_local_kb_calls"] = 1
            self.search_settings["max_web_calls"] = 1
        self._last_response_usage: dict[str, Any] = {}
        self.max_steps = int(config.get("customer_ceshi_max_steps", config.get("customer_ceshi_v2_max_steps", DEFAULT_MAX_STEPS)))
        self.max_tool_calls = int(config.get("customer_ceshi_max_tool_calls", config.get("customer_ceshi_v2_max_tool_calls", 8)))
        self.max_media_calls = int(config.get("customer_ceshi_max_media_calls", config.get("customer_ceshi_v2_max_media_calls", 4)))
        draft_path = _nested_config(config, "customer_ceshi_runtime", "direct_updates").get("draft_store_path")
        self.drafts = ShipUpdateDraftStore(draft_path)
        self.dry_run_writes = bool(_nested_config(config, "customer_ceshi_runtime", "direct_updates").get("dry_run", False))
        self._active_scenario = None

    def _read_only_chat_schemas(self, *, disabled_tool_names: set[str] | None = None) -> list[dict[str, Any]]:
        disabled = disabled_tool_names or set()
        if self.tool_descriptors:
            return [descriptor.chat_schema() for descriptor in self.tool_descriptors if descriptor.read_only and descriptor.name not in disabled and self.registry.has(descriptor.name)]
        return [_tool_schema(tool) for name, tool in self.registry._tools.items() if name in READ_ONLY_TOOL_NAMES and name not in disabled]

    def _bound_client(self, *, exclude_media: bool = False, disabled_tool_names: set[str] | None = None) -> Any:
        tools = self._read_only_chat_schemas(disabled_tool_names=disabled_tool_names)
        if self.perception is not None and not exclude_media:
            tools.append(_media_schema())
        tools.extend(_ship_update_transaction_schemas(responses=False))
        if self._active_scenario is not None:
            tools = [tool for tool in tools if tool["function"]["name"] in self._active_scenario.allowed_tools]
        return self.client.bind_tools(tools)

    def _responses_tools(self, *, inspect_only: bool = False, exclude_media: bool = False, disabled_tool_names: set[str] | None = None) -> list[dict[str, Any]]:
        chat_schemas = self._read_only_chat_schemas(disabled_tool_names=disabled_tool_names)
        tools = [
            {
                "type": "function",
                "name": schema["function"]["name"],
                "description": schema["function"]["description"],
                "parameters": schema["function"]["parameters"],
            }
            for schema in chat_schemas
        ]
        if self.perception is not None and not exclude_media:
            tools.append({
                "type": "function",
                "name": _media_schema()["function"]["name"],
                "description": _media_schema()["function"]["description"],
                "parameters": _media_schema()["function"]["parameters"],
            })
        tools.extend(_ship_update_transaction_schemas(responses=True))
        if self._active_scenario is not None:
            tools = [tool for tool in tools if tool["name"] in self._active_scenario.allowed_tools]
        if inspect_only:
            tools = [tool for tool in tools if tool["name"] == "inspect_media"]
        return tools

    def _exhausted_search_tools(self, search_counts: dict[str, int], *, is_chart_symbol: bool = False) -> set[str]:
        exhausted: set[str] = set()
        limits = {
            "local_kb_search": 1 if is_chart_symbol else self.search_settings["max_local_kb_calls"],
            "web_search": 1 if is_chart_symbol else self.search_settings["max_web_calls"],
        }
        for name, limit in limits.items():
            if search_counts.get(name, 0) >= limit:
                exhausted.add(name)
        return exhausted

    @staticmethod
    def _has_internal_retrieval_evidence(observation: Observation) -> bool:
        if observation.status not in {"success", "partial"}:
            return False
        data = observation.data if isinstance(observation.data, dict) else {}
        return any(
            isinstance(item, dict) and bool(str(item.get("content") or item.get("snippet") or "").strip())
            for item in list(data.get("items") or [])
        )

    def _responses_request_options(self) -> dict[str, Any]:
        values = _responses_settings(self.config, "deepseek")
        options: dict[str, Any] = {
            "store": bool(values.get("store", True)),
            "max_output_tokens": int(values.get("max_output_tokens", self.config.get("max_tokens", 8192))),
            "temperature": values.get("temperature", self.config.get("temperature", 0.2)),
            "top_p": values.get("top_p", self.config.get("top_p", 0.9)),
            "tool_choice": values.get("tool_choice", "auto"),
        }
        extra_body: dict[str, Any] = {}
        thinking = values.get("thinking")
        if isinstance(thinking, dict):
            extra_body["thinking"] = thinking
        elif str(values.get("thinking_type", self.config.get("thinking_type", "enabled"))) == "enabled":
            extra_body["thinking"] = {"type": "enabled"}
        context_management = values.get("context_management")
        if isinstance(context_management, dict) and context_management.get("edits"):
            extra_body["context_management"] = context_management
        if extra_body:
            options["extra_body"] = extra_body
        return {key: value for key, value in options.items() if value is not None}

    def _responses_create(self, request: dict[str, Any]) -> Any:
        """Retry once without optional sampling/reasoning fields for partial gateways."""
        try:
            return self.responses_client.responses.create(**request)
        except Exception:
            optional = {"store", "max_output_tokens", "temperature", "top_p", "tool_choice", "extra_body"}
            reduced = {key: value for key, value in request.items() if key not in optional}
            if len(reduced) == len(request):
                raise
            return self.responses_client.responses.create(**reduced)

    @staticmethod
    def _responses_calls(response: Any) -> list[dict[str, Any]]:
        output = getattr(response, "output", None) or (response.get("output", []) if isinstance(response, dict) else [])
        calls: list[dict[str, Any]] = []
        for item in output or []:
            item_type = getattr(item, "type", None) or (item.get("type") if isinstance(item, dict) else "")
            if item_type != "function_call":
                continue
            get = (lambda key, default=None: getattr(item, key, default)) if not isinstance(item, dict) else item.get
            arguments = get("arguments", "{}")
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments)
                except json.JSONDecodeError:
                    arguments = {}
            calls.append({"name": str(get("name", "")), "args": dict(arguments or {}), "id": str(get("call_id", None) or get("id", ""))})
        return calls

    @staticmethod
    def _responses_text(response: Any) -> str:
        value = getattr(response, "output_text", None) or (response.get("output_text") if isinstance(response, dict) else "")
        if isinstance(value, dict):
            value = value.get("text") or value.get("value") or value.get("content") or ""
        if value:
            return str(value).strip()
        output = getattr(response, "output", None) or (response.get("output", []) if isinstance(response, dict) else [])
        text: list[str] = []
        for item in output or []:
            content = getattr(item, "content", None) or (item.get("content", []) if isinstance(item, dict) else [])
            if isinstance(content, str):
                text.append(content)
                continue
            for part in content or []:
                value = getattr(part, "text", None) or (part.get("text") if isinstance(part, dict) else "") or (part.get("output_text") if isinstance(part, dict) else "")
                if isinstance(value, dict):
                    value = value.get("text") or value.get("value") or value.get("content") or ""
                if value:
                    text.append(str(value))
        return "\n".join(text).strip()

    def _capture_response_usage(self, response: Any) -> None:
        usage = getattr(response, "usage", None) or (response.get("usage", {}) if isinstance(response, dict) else {})
        if not isinstance(usage, dict):
            usage = usage.model_dump() if hasattr(usage, "model_dump") else {}
        input_details = usage.get("input_tokens_details") or {}
        output_details = usage.get("output_tokens_details") or {}
        self._last_response_usage = {
            "context_tokens": usage.get("input_tokens"),
            "output_tokens": usage.get("output_tokens"),
            "cache_hits": input_details.get("cached_tokens", 0),
            "reasoning_tokens": output_details.get("reasoning_tokens"),
            "provider_status": getattr(response, "status", "") or (response.get("status", "") if isinstance(response, dict) else ""),
        }

    @staticmethod
    def _normalized_query(arguments: dict[str, Any]) -> str:
        return re.sub(r"\s+", "", str(arguments.get("query") or "")).lower()

    @staticmethod
    def _parse_observation_value(value: Any) -> dict[str, Any] | None:
        if isinstance(value, dict):
            return value
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
            except json.JSONDecodeError:
                return None
            return parsed if isinstance(parsed, dict) else None
        return None

    @staticmethod
    def _evidence_texts(data: dict[str, Any], raw_facts: list[Any], *, limit: int) -> tuple[list[str], list[str]]:
        facts: list[str] = []
        sources: list[str] = []

        def add_fact(value: Any) -> None:
            text = re.sub(r"\s+", " ", str(value or "")).strip()
            if text and text not in facts:
                facts.append(text[:700])

        def add_source(value: Any) -> None:
            source = str(value or "").strip()
            if source.startswith(("http://", "https://")) and source not in sources:
                sources.append(source[:400])

        for key in ("summary", "text", "content", "message"):
            add_fact(data.get(key))
        items = data.get("items")
        if isinstance(items, list):
            for item in items[:limit]:
                if not isinstance(item, dict):
                    continue
                title = str(item.get("title") or "").strip()
                detail = item.get("summary") or item.get("snippet") or item.get("content") or item.get("excerpt")
                add_fact(f"{title}：{detail}" if title and detail else title or detail)
                add_source(item.get("url"))
        for key in ("best_urls", "urls", "sources"):
            values = data.get(key)
            if isinstance(values, str):
                values = [values]
            if isinstance(values, list):
                for value in values[:limit]:
                    add_source(value)
        for raw_fact in raw_facts:
            parsed = NativeToolRuntime._parse_observation_value(raw_fact)
            if parsed is not None:
                nested_facts, nested_sources = NativeToolRuntime._evidence_texts(parsed, [], limit=limit)
                for fact in nested_facts:
                    add_fact(fact)
                for source in nested_sources:
                    add_source(source)
            else:
                add_fact(raw_fact)
        return facts[:limit], sources[:2]

    def _compact_observation(self, observation: Observation) -> dict[str, Any]:
        data = dict(observation.data or {})
        facts, sources = self._evidence_texts(data, list(observation.facts or []), limit=self.search_settings["max_facts"])
        for source in list(observation.sources or [])[: self.search_settings["max_sources"]]:
            if str(source).startswith(("http://", "https://")) and str(source) not in sources:
                sources.append(str(source)[:400])
        sources = sources[: self.search_settings["max_sources"]]
        compact_data = {
            key: data[key]
            for key in ("query", "status", "summary", "can_answer", "recommended_next_action", "continue_with", "confidence")
            if key in data
        }
        return {
            "tool": observation.capability,
            "status": observation.status,
            "capability": observation.capability,
            "facts": facts,
            "sources": sources,
            "data": compact_data,
            "warnings": [str(item)[:200] for item in (observation.warnings or [])[:2]],
            "retry_allowed": observation.retry_allowed,
        }

    @staticmethod
    def _can_answer_from(observation: Observation) -> bool:
        data = dict(observation.data or {})
        next_action = str(data.get("recommended_next_action") or "")
        return bool(data.get("can_answer")) or "直接基于当前检索结果回答" in next_action

    @staticmethod
    def _current_value(text: str, value: Any) -> bool:
        normalized = re.sub(r"\s+", "", str(text or "")).lower()
        candidate = re.sub(r"\s+", "", str(value or "")).lower()
        return bool(candidate and candidate in normalized)

    @staticmethod
    def _valid_update_value(value: Any) -> bool:
        return bool(str(value or "").strip()) and not _PLACEHOLDER_VALUE.fullmatch(str(value).strip())

    @staticmethod
    def _valid_update_time(value: Any) -> bool:
        text = str(value or "").strip()
        if not text:
            return False
        try:
            datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return False
        return True

    def _execute_update_candidate(
        self,
        arguments: dict[str, Any],
        current_user_text: str,
        *,
        trusted_media_fields: dict[str, Any] | None = None,
    ) -> Observation:
        if not self.direct_updates_enabled:
            return Observation(status="forbidden", capability=UPDATE_CANDIDATE_TOOL_NAME, warnings=["direct_updates_disabled"], retry_allowed=False)
        has_explicit_intent = bool(_UPDATE_INTENT.search(current_user_text or ""))
        has_media_command = bool(trusted_media_fields and (_MEDIA_UPDATE_COMMAND.search(current_user_text or "") or _CONFIRM_ONLY.fullmatch(str(current_user_text or "").strip())))
        if not has_explicit_intent and not has_media_command:
            return Observation(status="invalid_input", capability=UPDATE_CANDIDATE_TOOL_NAME, warnings=["explicit_current_turn_update_intent_required"], retry_allowed=False, suggested_fix="请明确说明本次要更新的船舶信息。")
        trusted = {key: str(value).strip() for key, value in dict(trusted_media_fields or {}).items() if key in _MEDIA_UPDATE_FIELDS and self._valid_update_value(value)}
        merged = {key: str(arguments.get(key) or trusted.get(key) or "").strip() for key in _MEDIA_UPDATE_FIELDS}
        operation = merged["operation_type"]
        mmsi = re.sub(r"\s+", "", merged["mmsi"])
        if not re.fullmatch(r"\d{9}", mmsi):
            return Observation(status="invalid_input", capability=UPDATE_CANDIDATE_TOOL_NAME, warnings=["current_turn_mmsi_required"], retry_allowed=False, suggested_fix="请提供本次需要更新船舶的 9 位 MMSI。")
        if not self._current_value(current_user_text, mmsi) and trusted.get("mmsi") != mmsi:
            warning = "untrusted_mmsi" if trusted else "current_turn_mmsi_required"
            return Observation(status="invalid_input", capability=UPDATE_CANDIDATE_TOOL_NAME, warnings=[warning], retry_allowed=False, suggested_fix="请在当前消息提供 MMSI，或明确引用本会话刚上传的附件数据。")
        if operation == "position_update":
            required = ("lon", "lat", "updatetime")
            missing = [field for field in required if not self._valid_update_value(merged[field])]
            if missing:
                return Observation(status="invalid_input", capability=UPDATE_CANDIDATE_TOOL_NAME, warnings=[f"missing_required_fields:{','.join(missing)}"], retry_allowed=False, suggested_fix="更新船位请补充经度、纬度和用户明确提供的更新时间。")
            if not self._valid_update_time(merged["updatetime"]):
                return Observation(status="invalid_input", capability=UPDATE_CANDIDATE_TOOL_NAME, warnings=["invalid_updatetime"], retry_allowed=False, suggested_fix="请提供可识别的更新时间，例如 2026-07-15 18:00:00。")
            if any(not self._current_value(current_user_text, merged[field]) and trusted.get(field) != merged[field] for field in required):
                return Observation(status="invalid_input", capability=UPDATE_CANDIDATE_TOOL_NAME, warnings=["untrusted_position_values"], retry_allowed=False)
            from skills.hifleet_ship_service.tools import upload_ship_position

            allowed = ("mmsi", "lon", "lat", "speed", "heading", "course", "destination", "eta", "draft", "updatetime", "navstatus", "ship_name")
            tool_args = {field: merged[field] for field in allowed}
            tool_args["mmsi"] = mmsi
            raw = upload_ship_position.invoke(tool_args)
            return self._write_observation("upload_ship_position", raw, tool_args)
        if operation == "static_update":
            allowed = ("ship_name", "imo", "ship_type", "minotype", "length", "width", "dwt", "flag", "callsign", "built_year", "destination", "eta", "draft")
            provided = {field: merged[field] for field in allowed if self._valid_update_value(merged[field])}
            if not provided:
                return Observation(status="invalid_input", capability=UPDATE_CANDIDATE_TOOL_NAME, warnings=["static_field_required"], retry_allowed=False, suggested_fix="请至少提供一项要更新的静态字段。")
            if any(not self._current_value(current_user_text, value) and trusted.get(field) != value for field, value in provided.items()):
                return Observation(status="invalid_input", capability=UPDATE_CANDIDATE_TOOL_NAME, warnings=["untrusted_static_values"], retry_allowed=False)
            from skills.hifleet_ship_service.tools import update_ship_static_info

            raw = update_ship_static_info.invoke({"mmsi": mmsi, **provided})
            return self._write_observation("update_ship_static_info", raw, {"mmsi": mmsi, **provided})
        return Observation(status="invalid_input", capability=UPDATE_CANDIDATE_TOOL_NAME, warnings=["unsupported_operation_type"], retry_allowed=False)

    def _draft_operation(self, name: str, arguments: dict[str, Any], session_key: str) -> Observation:
        if name == CANCEL_SHIP_UPDATE_TOOL_NAME:
            self.drafts.cancel(session_key)
            return Observation(status="success", capability=name, facts=["已取消当前待确认的更新草稿。"], retry_allowed=False)
        if name == COMMIT_SHIP_UPDATE_TOOL_NAME:
            draft = self.drafts.get(session_key)
            supplied_draft_id = str(arguments.get("draft_id") or "")
            if draft is None or (supplied_draft_id and supplied_draft_id != draft.draft_id) or arguments.get("confirmed") is not True:
                return Observation(status="invalid_input", capability=name, warnings=["explicit_current_draft_confirmation_required"], retry_allowed=False)
            if self.dry_run_writes:
                return Observation(
                    status="partial",
                    capability=name,
                    facts=["更新草稿已通过 dry-run 校验；未执行生产写入。"],
                    data={"adapter_status": "accepted", "dry_run": True, "transaction_id": f"dryrun:{draft.draft_id}", "draft_id": draft.draft_id},
                    warnings=["dry_run_no_production_write"],
                    retry_allowed=False,
                )
            return Observation(status="forbidden", capability=name, warnings=["write_adapter_not_enabled"], facts=["草稿已确认，但当前环境未启用写入 Adapter，因此未执行更新。"], retry_allowed=False)
        operation = str(arguments.get("operation_type") or "")
        target = {"mmsi": str(arguments.get("mmsi") or "").strip()}
        fields = dict(arguments.get("fields") or {})
        for key in ("longitude", "latitude", "updatetime"):
            if arguments.get(key) not in (None, ""):
                fields[key] = arguments[key]
        if operation == "position_update":
            coordinates = PositionNormalizer().normalize(" ".join(str(fields.get(key) or "") for key in ("longitude", "latitude")))
            if coordinates.get("confidence") == "deterministic":
                fields["longitude"] = coordinates["longitude"]
                fields["latitude"] = coordinates["latitude"]
            normalized_time = TimeNormalizer().normalize(str(fields.get("updatetime") or ""))
            if normalized_time.get("value"):
                fields["updatetime"] = normalized_time["value"]
            invalid_fields = validate_position_update({
                "mmsi": target["mmsi"],
                "lon": fields.get("lon", fields.get("longitude")),
                "lat": fields.get("lat", fields.get("latitude")),
                "updatetime": fields.get("updatetime"),
                **{key: fields[key] for key in ("speed", "draft") if key in fields},
            })
        elif operation == "static_update":
            invalid_fields = validate_static_update({"mmsi": target["mmsi"], **fields})
        else:
            invalid_fields = ["operation_type"]
        if invalid_fields:
            return Observation(
                status="invalid_input",
                capability=PREPARE_SHIP_UPDATE_TOOL_NAME,
                warnings=[f"invalid_fields:{','.join(invalid_fields)}"],
                data={"invalid_fields": invalid_fields, "operation_type": operation},
                suggested_fix="请修正所有标记的字段后再生成更新草稿。",
                retry_allowed=False,
            )
        draft = self.drafts.prepare(session_key=session_key, operation_type=operation, target=target, fields=fields, field_sources={key: "current_turn_text" for key in fields})
        return Observation(status="success", capability=name, facts=["已生成更新草稿；请核对后明确确认。"], data={"draft_id": draft.draft_id, "operation_type": draft.operation_type, "target": draft.target, "fields": draft.fields, "missing_fields": draft.missing_fields, "invalid_fields": [], "requires_confirmation": True, "expires_at": draft.expires_at, "draft_hash": draft.draft_hash}, retry_allowed=False)

    def _prepare_text_position_update(self, text: str, session_key: str) -> Observation | None:
        """Prepare only a complete, unambiguous current-turn position update."""
        scenario = classify_scenario(text)
        if scenario is None or scenario.name != "position_update":
            return None
        mmsi_match = re.search(r"(?<!\d)(\d{9})(?!\d)", text or "")
        coordinates = PositionNormalizer().normalize(text)
        time_match = _POSITION_UPDATE_TIME.search(text or "")
        normalized_time = TimeNormalizer().normalize(time_match.group(0) if time_match else "")
        missing: list[str] = []
        if mmsi_match is None:
            missing.append("9 位 MMSI")
        if coordinates.get("confidence") != "deterministic":
            missing.append("可识别的经纬度")
        if not normalized_time.get("value"):
            missing.append("可识别的更新时间")
        if missing:
            return Observation(
                status="invalid_input",
                capability=PREPARE_SHIP_UPDATE_TOOL_NAME,
                warnings=["missing_or_invalid_position_update_fields"],
                suggested_fix=f"请补充{'、'.join(missing)}后再生成更新草稿。",
                retry_allowed=False,
            )
        if normalized_time.get("requires_confirmation"):
            return Observation(
                status="invalid_input",
                capability=PREPARE_SHIP_UPDATE_TOOL_NAME,
                warnings=["ambiguous_five_digit_year_requires_confirmation"],
                suggested_fix="检测到五位年份时间，请明确确认正确的四位年份后再生成更新草稿。",
                retry_allowed=False,
            )
        return self._draft_operation(
            PREPARE_SHIP_UPDATE_TOOL_NAME,
            {
                "operation_type": "position_update",
                "mmsi": mmsi_match.group(1),
                "longitude": str(coordinates["original_values"]["longitude"]),
                "latitude": str(coordinates["original_values"]["latitude"]),
                "updatetime": str(normalized_time["value"]),
            },
            session_key,
        )

    def _prepare_text_static_update(self, text: str, session_key: str) -> Observation | None:
        scenario = classify_scenario(text)
        if scenario is None or scenario.name != "static_update":
            return None
        identity = ShipIdentityNormalizer().normalize(text)
        normalized = StaticFieldNormalizer().normalize(text)
        if not identity["mmsi"]:
            return Observation(
                status="invalid_input",
                capability=PREPARE_SHIP_UPDATE_TOOL_NAME,
                warnings=["current_turn_mmsi_required"],
                suggested_fix="更新船舶静态信息请在当前消息提供 9 位 MMSI。",
                retry_allowed=False,
            )
        fields = dict(normalized["fields"])
        if not fields:
            return Observation(
                status="invalid_input",
                capability=PREPARE_SHIP_UPDATE_TOOL_NAME,
                warnings=["valid_static_field_required"],
                suggested_fix="请至少提供一项有效静态字段，例如船名、IMO、船型、目的港、ETA或吃水。",
                retry_allowed=False,
            )
        return self._draft_operation(
            PREPARE_SHIP_UPDATE_TOOL_NAME,
            {"operation_type": "static_update", "mmsi": identity["mmsi"], "fields": fields},
            session_key,
        )

    def _prepare_text_update(self, text: str, session_key: str) -> Observation | None:
        return self._prepare_text_position_update(text, session_key) or self._prepare_text_static_update(text, session_key)

    @staticmethod
    def _write_observation(capability: str, raw: Any, arguments: dict[str, Any]) -> Observation:
        payload = raw if isinstance(raw, dict) else {}
        if not payload and isinstance(raw, str):
            try:
                candidate = json.loads(raw)
                payload = candidate if isinstance(candidate, dict) else {}
            except json.JSONDecodeError:
                payload = {}
        adapter_status = str(payload.get("status") or "unknown").lower()
        status = "success" if adapter_status == "success" else "upstream_error"
        message = str(payload.get("message") or payload.get("code") or raw or "write_service_did_not_return_structured_status")
        return Observation(
            status=status,
            capability=capability,
            facts=[message[:1200]],
            data={
                "adapter_status": adapter_status,
                "transaction_id": str(payload.get("transaction_id") or ""),
                "updated_fields": list(payload.get("updated_fields") or []) if adapter_status == "success" else [],
                "mmsi": arguments.get("mmsi", ""),
            },
            retry_allowed=False,
        )

    def _invoke_responses(self, *, human: HumanMessage | None, messages: list[Any], assets: list[MediaAsset], context_block: str, session_key: str) -> tuple[str, list[dict[str, Any]], list[str], int, int, str, str]:
        if self.responses_client is None:
            raise RuntimeError("responses_client_unavailable")
        user_text = _text(getattr(human, "content", ""))
        inbound_system_text = "\n".join(_text(_message_content(message)) for message in messages if isinstance(message, SystemMessage) or _message_type(message) == "system")
        asset_manifest = ""
        if assets:
            asset_manifest = "当前附件仅可通过 inspect_media 读取：" + json.dumps(
                [{"asset_id": asset.asset_id, "type": asset.kind} for asset in assets], ensure_ascii=False
            )
        initial_input = "\n\n".join(part for part in (self._system(assets).content, inbound_system_text, context_block, asset_manifest, user_text) if part)
        settings = _responses_settings(self.config, "deepseek")
        request: dict[str, Any] = {
            "model": str(settings.get("model") or getattr(self.client, "model_name", "") or getattr(self.client, "model", "") or self.config.get("customer_ceshi_v2_text_model") or self.config.get("text_model")),
            "input": initial_input,
            "tools": self._responses_tools(inspect_only=bool(assets and self.perception is not None)),
        }
        request.update(self._responses_request_options())
        if assets and self.perception is not None:
            request["tool_choice"] = "required"
        elif self._active_scenario is not None and self._active_scenario.name in {"platform_operation", "membership_permissions"}:
            request["tool_choice"] = "required"
        response = self._responses_create(request)
        self._capture_response_usage(response)
        model_calls = tool_calls = media_calls = 0
        observations: list[dict[str, Any]] = []
        names: list[str] = []
        asset_map = {asset.asset_id: asset for asset in assets}
        media_recovery_used = False
        search_fingerprints: set[str] = set()
        search_counts: dict[str, int] = {"local_kb_search": 0, "web_search": 0}
        is_chart_symbol = self._active_scenario is not None and self._active_scenario.name == "multimodal_symbol"
        finalize_after_internal_evidence = False
        tool_call_limit = 3 if is_chart_symbol else self.max_tool_calls
        current_user_text = user_text
        response_id = str(getattr(response, "id", "") or (response.get("id", "") if isinstance(response, dict) else ""))
        for _ in range(self.max_steps):
            model_calls += 1
            calls = self._responses_calls(response)
            if not calls:
                if assets and self.perception is not None and not media_recovery_used:
                    media_recovery_used = True
                    recovery_observations: list[dict[str, Any]] = []
                    for asset in assets:
                        observation = self._execute(
                            "inspect_media",
                            {
                                "asset_id": asset.asset_id,
                                "objective": "读取当前附件中的可见事实，服务于用户当前问题。",
                                "questions": [user_text] if user_text else [],
                                "mode": "broad_scan",
                            },
                            asset_map,
                        )
                        media_calls += 1
                        tool_calls += 1
                        names.append("inspect_media")
                        observed = observation.model_dump()
                        observed["evidence_id"] = f"e-{len(observations) + 1}"
                        observations.append(observed)
                        compact = self._compact_observation(observation)
                        compact["evidence_id"] = observed["evidence_id"]
                        recovery_observations.append(compact)
                    request = {
                        "model": request["model"],
                        "input": initial_input + "\n\n附件观察结果（仅作为事实证据）：\n" + json.dumps(recovery_observations, ensure_ascii=False),
                        "tools": self._responses_tools(exclude_media=is_chart_symbol and media_calls >= 1),
                        **self._responses_request_options(),
                    }
                    response = self._responses_create(request)
                    self._capture_response_usage(response)
                    response_id = str(getattr(response, "id", "") or (response.get("id", "") if isinstance(response, dict) else ""))
                    continue
                return self._responses_text(response), observations, names, model_calls, tool_calls, response_id, "stop"
            outputs: list[dict[str, Any]] = []
            for call in calls:
                if tool_calls >= tool_call_limit:
                    final_request = {
                        "model": request["model"],
                        "input": "工具调用预算已到上限。请仅基于本轮已返回的附件观察和检索证据，直接给出简洁、校准后的客户回复；不要再调用工具，也不要提及内部预算。",
                        "tools": [],
                        **self._responses_request_options(),
                        "tool_choice": "none",
                    }
                    if response_id:
                        final_request["previous_response_id"] = response_id
                    final_response = self._responses_create(final_request)
                    self._capture_response_usage(final_response)
                    final_response_id = str(getattr(final_response, "id", "") or (final_response.get("id", "") if isinstance(final_response, dict) else ""))
                    return self._responses_text(final_response), observations, names, model_calls + 1, tool_calls, final_response_id, "tool_budget_finalized"
                name, arguments = call["name"], call["args"]
                if name in DENIED_TOOL_NAMES:
                    observation = Observation(status="forbidden", capability=name, warnings=["write_tools_disabled"], retry_allowed=False)
                elif name in {PREPARE_SHIP_UPDATE_TOOL_NAME, COMMIT_SHIP_UPDATE_TOOL_NAME, CANCEL_SHIP_UPDATE_TOOL_NAME}:
                    observation = self._draft_operation(name, arguments, session_key)
                elif name == UPDATE_CANDIDATE_TOOL_NAME:
                    observation = Observation(status="forbidden", capability=name, warnings=["legacy_direct_update_tool_disabled"], retry_allowed=False)
                elif name == "inspect_media" and media_calls >= (1 if is_chart_symbol else self.max_media_calls):
                    observation = Observation(status="forbidden", capability=name, warnings=["media_budget_exhausted"], retry_allowed=False, suggested_fix="请基于当前附件观察和已有证据直接回答，不要再次读取同一附件。")
                else:
                    fingerprint = f"{name}:{self._normalized_query(arguments)}" if name in _SEARCH_TOOL_NAMES else ""
                    limit = self.search_settings["max_local_kb_calls"] if name == "local_kb_search" else self.search_settings["max_web_calls"] if name == "web_search" else None
                    if is_chart_symbol and name in {"local_kb_search", "web_search"}:
                        limit = 1
                    if fingerprint and fingerprint in search_fingerprints:
                        observation = Observation(status="forbidden", capability=name, warnings=["duplicate_search_query"], retry_allowed=False, suggested_fix="请基于已有证据回答，或提出一个不同的明确证据缺口。")
                    elif limit is not None and search_counts.get(name, 0) >= limit:
                        observation = Observation(status="forbidden", capability=name, warnings=["search_budget_exhausted"], retry_allowed=False, suggested_fix="请基于当前检索结果直接回答。")
                    else:
                        observation = self._execute(name, arguments, {asset.asset_id: asset for asset in assets})
                        if fingerprint:
                            search_fingerprints.add(fingerprint)
                            search_counts[name] = search_counts.get(name, 0) + 1
                if name == "inspect_media":
                    media_calls += 1
                tool_calls += 1
                names.append(name)
                observed = observation.model_dump()
                observed["evidence_id"] = f"e-{len(observations) + 1}"
                observations.append(observed)
                compact = self._compact_observation(observation)
                compact["evidence_id"] = observed["evidence_id"]
                outputs.append({"type": "function_call_output", "call_id": call["id"], "output": json.dumps(compact, ensure_ascii=False)})
                # Tool metadata can describe coverage but never decides that the model must stop.
                if (
                    name == "local_kb_search"
                    and self._active_scenario is not None
                    and self._active_scenario.name in {"platform_operation", "membership_permissions"}
                    and self._has_internal_retrieval_evidence(observation)
                ):
                    finalize_after_internal_evidence = True
            if finalize_after_internal_evidence:
                final_request = {
                    "model": request["model"],
                    "input": outputs,
                    "tools": [],
                    **self._responses_request_options(),
                    "tool_choice": "none",
                }
                if response_id:
                    final_request["previous_response_id"] = response_id
                final_response = self._responses_create(final_request)
                self._capture_response_usage(final_response)
                final_response_id = str(getattr(final_response, "id", "") or (final_response.get("id", "") if isinstance(final_response, dict) else ""))
                return self._responses_text(final_response), observations, names, model_calls + 1, tool_calls, final_response_id, "internal_evidence_finalized"
            request = {
                "model": request["model"],
                "input": outputs,
                "tools": self._responses_tools(
                    exclude_media=is_chart_symbol and media_calls >= 1,
                    disabled_tool_names=self._exhausted_search_tools(search_counts, is_chart_symbol=is_chart_symbol),
                ),
                **self._responses_request_options(),
            }
            if response_id:
                request["previous_response_id"] = response_id
            response = self._responses_create(request)
            self._capture_response_usage(response)
            response_id = str(getattr(response, "id", "") or (response.get("id", "") if isinstance(response, dict) else ""))
        return "", observations, names, model_calls, tool_calls, response_id, "max_steps"

    def _system(self, assets: list[MediaAsset]) -> SystemMessage:
        scenario_instruction = ""
        if self._active_scenario is not None and self._active_scenario.name == "multimodal_symbol":
            scenario_instruction = (
                "For a chart-symbol question with media, inspect the attachment first, then retrieve a chart legend or directly relevant product evidence "
                "before naming the symbol. Use at most one inspect_media call and one evidence lookup; prefer local_kb_search, and use web_search only when local evidence has no relevant fact. "
                "After those bounded calls, answer from the evidence already returned instead of repeating a tool. Visible color or shape alone is never enough to name it. "
                "If no evidence source verifies its identity, state the uncertainty and give the one best verification step."
            )
        return SystemMessage(content="\n\n".join(part for part in (
            self.profile_prompt,
            "You are the sole customer_ceshi orchestrator. Use the minimum tools needed. Do not emit a custom action JSON protocol or expose internal tool details. "
            "For product knowledge, decide yourself whether the evidence covers the user's core question; tool metadata never decides completion. "
            "Never repeat an equivalent search query. Ship writes require an explicit prepare/confirm/commit workflow; all direct write tools are forbidden. "
            "Do not claim a high-risk product capability, policy, UI element, media detail, or operation success without supporting evidence. "
            "Reply in concise Chinese plain text: ordinary answers should usually be 80–180 Chinese characters; troubleshooting uses at most four lines. "
            "For a simple greeting, respond naturally in one short sentence and do not enumerate capabilities or add a menu.",
            scenario_instruction,
        ) if part))

    def _execute(self, name: str, arguments: dict[str, Any], assets: dict[str, MediaAsset]) -> Observation:
        if self._active_scenario is not None and name not in self._active_scenario.allowed_tools:
            return Observation(status="forbidden", capability=name, warnings=[f"tool_not_allowed_for_scenario:{self._active_scenario.name}"], retry_allowed=False)
        if name == "inspect_media":
            if self.perception is None:
                return Observation(status="forbidden", capability=name, warnings=["media_perception_unavailable"], retry_allowed=False)
            asset = assets.get(str(arguments.get("asset_id") or ""))
            if asset is None:
                return Observation(status="invalid_input", capability=name, warnings=["unknown_media_asset"], retry_allowed=False)
            packet = self.perception.inspect(
                asset,
                InspectMediaRequest(
                    asset_id=asset.asset_id,
                    objective=str(arguments.get("objective") or "观察附件"),
                    questions=[str(item) for item in arguments.get("questions", []) if item],
                    mode=str(arguments.get("mode") or "broad_scan"),
                    expected_fields=[str(item) for item in arguments.get("expected_fields", []) if item],
                ),
            )
            if isinstance(packet, Observation):
                return packet
            perception_facts = [str(packet.factual_summary or "").strip()]
            if packet.suspected_symbol:
                perception_facts.append(f"疑似视觉对象：{packet.suspected_symbol}")
            perception_facts.extend(str(item).strip() for item in packet.visual_features if str(item).strip())
            for field in packet.fields[:6]:
                value = str(field.value or field.raw_text or "").strip()
                if value:
                    perception_facts.append(f"观察字段 {field.name}：{value}（{field.status}/{field.confidence}）")
            for block in packet.ocr_blocks[:4]:
                text = str(block.get("text") or block.get("content") or block.get("value") or "").strip()
                if text:
                    perception_facts.append(f"OCR：{text}")
            for item in packet.visual_objects[:4]:
                label = str(item.get("label") or item.get("name") or item.get("type") or item.get("description") or "").strip()
                if label:
                    perception_facts.append(f"视觉对象：{label}")
            return Observation(
                status="success",
                capability=name,
                facts=[fact[:500] for fact in perception_facts if fact][:10],
                data={"perception": packet.model_dump()},
                warnings=list(packet.limitations or []),
                sources=[f"media:{asset.asset_id}"],
                retry_allowed=False,
            )
        if name not in READ_ONLY_TOOL_NAMES:
            return Observation(status="forbidden", capability=name, warnings=["tool_not_in_customer_ceshi_read_only_allowlist"], retry_allowed=False)
        started = time.monotonic()
        observation = self.registry.invoke(ToolCall(name=name, arguments=arguments))
        observation.data.pop("information_gain", None)
        observation.data["tool_latency_ms"] = int((time.monotonic() - started) * 1000)
        return observation

    @staticmethod
    def _guard(answer: str, observations: list[dict[str, Any]]) -> tuple[str, str]:
        """Keep only outcome checks that cannot be safely delegated to semantic model reasoning."""
        accepted_writes = [
            item for item in observations
            if item.get("capability") == COMMIT_SHIP_UPDATE_TOOL_NAME
            and str((item.get("data") or {}).get("adapter_status") or "").lower() in {"accepted", "pending", "unknown"}
        ]
        if accepted_writes:
            return "更新请求已通过测试校验，但未执行生产写入；当前不能确认船舶信息已经更新完成。", "accepted_write_not_confirmed"
        successful_writes = [item for item in observations if item.get("capability") in {"upload_ship_position", "update_ship_static_info"} and item.get("status") == "success"]
        if _ACTION_SUCCESS_CLAIM.search(answer or "") and not successful_writes:
            return "本次更新结果尚未获得系统成功确认，请稍后查询核对或补充信息后重试。", "blocked_unconfirmed_write"
        if _ACTION_SUCCESS_CLAIM.search(answer or "") and successful_writes:
            return answer, "not_required"
        query_observations = [item for item in observations if item.get("capability") in _QUERY_TOOL_NAMES]
        confirmed_empty = any(item.get("status") == "not_found" for item in query_observations)
        if _NO_RESULT_CLAIM.search(answer or "") and query_observations and not confirmed_empty:
            return "当前没有获得对应查询工具的无结果反馈，暂不能确认没有数据。请提供 MMSI、查询时间或页面现象后再核验。", "blocked_unconfirmed_empty_result"
        answer, blocked_claims = guard_claims(answer, observations)
        if blocked_claims:
            return answer, f"blocked_unsupported_claims:{len(blocked_claims)}"
        answerable = [
            item for item in observations
            if item.get("status") in {"success", "partial"}
            and bool((item.get("data") or {}).get("can_answer"))
        ]
        evidence_ids = ",".join(str(item.get("evidence_id") or "") for item in answerable if item.get("evidence_id"))
        return answer, f"model_grounded:{evidence_ids}" if evidence_ids else "not_required"

    def invoke(self, payload: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        started = time.monotonic()
        self._last_response_usage = {}
        messages = list(payload.get("messages") or [])
        human = _latest_human(messages)
        assets = _assets(human)
        self._active_scenario = classify_scenario(_text(getattr(human, "content", "")), has_media=bool(assets))
        asset_map = {asset.asset_id: asset for asset in assets}
        context_block = str(payload.get("_customer_ceshi_context") or "").strip()
        model_messages: list[Any] = [self._system(list(asset_map.values()))]
        if context_block:
            model_messages.append(SystemMessage(content=context_block))
        model_messages.extend(_orchestrator_messages(messages, list(asset_map.values())))
        observations: list[dict[str, Any]] = []
        tool_names: list[str] = []
        model_calls = tool_calls = media_calls = 0
        answer = ""
        finish_reason = "max_steps"
        fallback_reason = ""
        provider_error = ""
        runtime_settings = dict(self.config.get("customer_ceshi_runtime") or {})
        try:
            if self.mode == "responses":
                try:
                    answer, observations, tool_names, model_calls, tool_calls, provider_response_id, finish_reason = self._invoke_responses(human=human, messages=messages, assets=list(asset_map.values()), context_block=context_block, session_key=str(payload.get("_customer_ceshi_session_key") or "default"))
                    media_calls = sum(1 for name in tool_names if name == "inspect_media")
                    if answer:
                        answer = _wechat_position_result(_text(getattr(human, "content", "")), observations) or answer
                        answer, guard_result = self._guard(answer, observations)
                        return self._result(answer, observations, tool_names, model_calls, tool_calls, media_calls, finish_reason, guard_result, started, provider_response_id, fallback_reason, provider_error)
                except Exception as exc:
                    provider_error = _provider_error_summary(exc)
                    fallback_reason = f"responses_unavailable:{provider_error}"
                    fallback_mode = str(runtime_settings.get("fallback_mode") or self.config.get("customer_ceshi_fallback_mode") or "chat_function_calling")
                    chat_enabled = bool(runtime_settings.get("chat_fallback_enabled", self.config.get("customer_ceshi_chat_fallback_enabled", True)))
                    if fallback_mode != "chat_function_calling" or not chat_enabled:
                        answer = "实验客服链的 Responses 能力暂时不可用，且当前配置未允许切换到 Chat Function Calling；未回退到生产客服链。"
                        return self._result(answer, observations, tool_names, model_calls, tool_calls, media_calls, "responses_unavailable_no_chat_fallback", "not_required", started, "", fallback_reason, provider_error)
            is_chart_symbol = self._active_scenario is not None and self._active_scenario.name == "multimodal_symbol"
            search_fingerprints: set[str] = set()
            search_counts: dict[str, int] = {"local_kb_search": 0, "web_search": 0}
            for _ in range(self.max_steps):
                model = self._bound_client(
                    exclude_media=is_chart_symbol and media_calls >= 1,
                    disabled_tool_names=self._exhausted_search_tools(search_counts, is_chart_symbol=is_chart_symbol),
                )
                response = model.invoke(model_messages)
                model_calls += 1
                calls = list(getattr(response, "tool_calls", []) or [])
                if not calls:
                    answer = _text(getattr(response, "content", ""))
                    finish_reason = "stop"
                    break
                model_messages.append(response)
                for call in calls:
                    if tool_calls >= self.max_tool_calls:
                        finish_reason = "tool_budget"
                        break
                    name = str(call.get("name") or "")
                    arguments = dict(call.get("args") or call.get("arguments") or {})
                    if name in DENIED_TOOL_NAMES:
                        observation = Observation(status="forbidden", capability=name, warnings=["write_tools_disabled"], retry_allowed=False)
                    elif name in {PREPARE_SHIP_UPDATE_TOOL_NAME, COMMIT_SHIP_UPDATE_TOOL_NAME, CANCEL_SHIP_UPDATE_TOOL_NAME}:
                        observation = self._draft_operation(name, arguments, str(payload.get("_customer_ceshi_session_key") or "default"))
                    elif name == UPDATE_CANDIDATE_TOOL_NAME:
                        observation = Observation(status="forbidden", capability=name, warnings=["legacy_direct_update_tool_disabled"], retry_allowed=False)
                    elif name == "inspect_media" and media_calls >= (1 if is_chart_symbol else self.max_media_calls):
                        observation = Observation(status="forbidden", capability=name, warnings=["media_budget_exhausted"], retry_allowed=False, suggested_fix="请基于当前附件观察和已有证据直接回答，不要再次读取同一附件。")
                    else:
                        fingerprint = f"{name}:{self._normalized_query(arguments)}" if name in _SEARCH_TOOL_NAMES else ""
                        limit = self.search_settings["max_local_kb_calls"] if name == "local_kb_search" else self.search_settings["max_web_calls"] if name == "web_search" else None
                        if is_chart_symbol and name in {"local_kb_search", "web_search"}:
                            limit = 1
                        if fingerprint and fingerprint in search_fingerprints:
                            observation = Observation(status="forbidden", capability=name, warnings=["duplicate_search_query"], retry_allowed=False, suggested_fix="请基于已有证据直接回答，或只提出一个不同的明确证据缺口。")
                        elif limit is not None and search_counts.get(name, 0) >= limit:
                            observation = Observation(status="forbidden", capability=name, warnings=["search_budget_exhausted"], retry_allowed=False, suggested_fix="请基于当前检索结果直接回答。")
                        else:
                            observation = self._execute(name, arguments, asset_map)
                            if fingerprint:
                                search_fingerprints.add(fingerprint)
                                search_counts[name] = search_counts.get(name, 0) + 1
                    if name == "inspect_media":
                        media_calls += 1
                    tool_calls += 1
                    tool_names.append(name)
                    observed = observation.model_dump()
                    observed["evidence_id"] = f"e-{len(observations) + 1}"
                    observations.append(observed)
                    compact = self._compact_observation(observation)
                    compact["evidence_id"] = observed["evidence_id"]
                    model_messages.append(ToolMessage(content=json.dumps(compact, ensure_ascii=False), tool_call_id=str(call.get("id") or name)))
                if finish_reason == "tool_budget":
                    break
            if not answer:
                answer = "我暂时无法在当前工具预算内获得足够证据，请补充更具体的信息或稍后重试。"
        except Exception as exc:
            answer = "实验客服链遇到暂时性错误，未切换到生产客服链；请稍后重试或补充必要信息。"
            finish_reason = f"error:{type(exc).__name__}"
        answer = _wechat_position_result(_text(getattr(human, "content", "")), observations) or answer
        answer, guard_result = self._guard(answer, observations)
        return self._result(answer, observations, tool_names, model_calls, tool_calls, media_calls, finish_reason, guard_result, started, "", fallback_reason, provider_error)

    def _result(self, answer: str, observations: list[dict[str, Any]], tool_names: list[str], model_calls: int, tool_calls: int, media_calls: int, finish_reason: str, guard_result: str, started: float, provider_response_id: str, fallback_reason: str, provider_error: str) -> dict[str, Any]:
        answer = limit_reply(_wechat_plain_text(answer))
        tool_latency_ms = sum(int((item.get("data") or {}).get("tool_latency_ms", 0)) for item in observations)
        media_observations = [item for item in observations if item.get("capability") == "inspect_media"]
        media_statuses = sorted({str(item.get("status") or "unknown") for item in media_observations})
        media_error_codes = sorted({
            str(warning)[:160]
            for item in media_observations
            if str(item.get("status") or "") != "success"
            for warning in list(item.get("warnings") or [])
        })[:4]
        metrics = {
            "runtime_mode": "chat_function_calling" if fallback_reason else self.mode,
            "requested_runtime_mode": self.mode,
            "effective_runtime": "chat_function_calling" if fallback_reason else self.mode,
            "orchestrator_model": str(getattr(self.client, "model_name", "") or getattr(self.client, "model", "")),
            "perception_model": str(getattr(self.perception, "model", "") or getattr(getattr(self.perception, "client", None), "model", "")),
            "model_calls": model_calls,
            "tool_calls": tool_calls,
            "tool_names": list(tool_names),
            "media_calls": media_calls,
            "media_statuses": media_statuses,
            "media_error_codes": media_error_codes,
            "cache_hits": self._last_response_usage.get("cache_hits", 0),
            "latency_ms": int((time.monotonic() - started) * 1000),
            "tool_latency_ms": tool_latency_ms,
            "context_tokens": self._last_response_usage.get("context_tokens"),
            "output_tokens": self._last_response_usage.get("output_tokens"),
            "reasoning_tokens": self._last_response_usage.get("reasoning_tokens"),
            "provider_status": self._last_response_usage.get("provider_status", ""),
            "skills_runtime": self.skill_runtime_metadata,
            "reasoning_level": str(self.config.get("reasoning_effort") or ""),
            "finish_reason": finish_reason,
            "fallback_reason": fallback_reason,
            "provider_error": provider_error,
            "guard_result": guard_result,
            "response_id_suffix": provider_response_id[-12:] if provider_response_id else "",
            "output_length": len(answer),
            "scenario": self._active_scenario.name if self._active_scenario is not None else "",
        }
        trace = safe_trace({
            "agent": "customer_ceshi_responses",
            "checkpoint_namespace": CHECKPOINT_NAMESPACE,
            "runtime_mode": self.mode,
            "provider_response_id": provider_response_id[-12:] if provider_response_id else "",
            "tool_calls": tool_names,
            "observations": observations,
            "metrics": metrics,
            "skills_runtime": self.skill_runtime_metadata,
        })
        degraded = finish_reason.startswith("error:") or finish_reason.startswith("responses_unavailable")
        return {"phase": "done", "status": "degraded" if degraded else "success", "generated_answer": answer, "messages": [AIMessage(content=answer)], "generated_tool_calls": tool_names, "observations": observations, "metrics": metrics, "route_trace": trace}


class SingleModelCustomerCeshiRuntime:
    """Select one model per request and retain only sanitized conversational memory."""

    def __init__(
        self,
        *,
        text_runtime: NativeToolRuntime,
        multimodal_responses_client: Any | None = None,
        multimodal_client: Any | None = None,
        config: dict[str, Any] | None = None,
        memory: ConversationMemory | None = None,
        media_evidence: MediaUpdateEvidenceLedger | None = None,
    ) -> None:
        self.text_runtime = text_runtime
        self.multimodal_responses_client = multimodal_responses_client
        self.multimodal_client = multimodal_client
        self.config = dict(config or {})
        self.memory = memory or ConversationMemory(_context_settings(self.config))
        self.media_evidence = media_evidence or MediaUpdateEvidenceLedger(_media_evidence_settings(self.config))

    @staticmethod
    def _inbound_system(messages: list[Any]) -> str:
        return "\n".join(
            _text(_message_content(message))
            for message in messages
            if isinstance(message, SystemMessage) or _message_type(message) == "system"
        ).strip()

    @staticmethod
    def _user_memory_text(human: HumanMessage | None, assets: list[MediaAsset]) -> str:
        text = _text(getattr(human, "content", ""))
        if not assets:
            return text
        names = "、".join({"image": "图片", "video": "视频", "audio": "音频"}.get(asset.kind, "文件") for asset in assets)
        return f"用户发送了{names}。{text}".strip()

    @staticmethod
    def _media_content(human: HumanMessage | None, context_block: str, system_text: str, settings: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
        raw = getattr(human, "content", "") if human is not None else ""
        parts = raw if isinstance(raw, list) else []
        content: list[dict[str, Any]] = []
        kinds: list[str] = []
        prefix = "\n\n".join(part for part in (
            "你是 customer_ceshi 的唯一多模态客服助手。基于当前媒体、当前问题和受控历史完成理解、工具调用和最终回复。"
            "产品规则、时间范围、数量限制或船舶数据需要核验时，使用最少的只读工具；工具结果足以回答时立即总结，不得重复同义检索。"
            "不得臆测 URL、文件名或不可见内容。只有当前附件清晰包含完整 AIS 更新字段时，才调用 record_media_update_candidate；该内部函数绝不能面向客户提及。"
            "客户回复使用简洁中文纯文本，不输出 JSON、工具名、候选字段或内部流程。",
            system_text,
            context_block,
        ) if part)
        if prefix:
            content.append({"type": "input_text", "text": prefix})
        for part in parts:
            if not isinstance(part, dict):
                continue
            part_type = str(part.get("type") or "")
            detail = part.get(part_type) if part_type in {"image_url", "video_url", "audio_url", "input_audio"} else None
            detail = detail if isinstance(detail, dict) else {}
            url = str(detail.get("url") or "").strip()
            if not url:
                continue
            if part_type == "image_url":
                item: dict[str, Any] = {"type": "input_image", "image_url": url, "detail": str(settings.get("image_detail", "high"))}
                pixel_limit = settings.get("image_pixel_limit")
                if isinstance(pixel_limit, dict):
                    item["image_pixel_limit"] = pixel_limit
                content.append(item)
                kinds.append("image")
            elif part_type == "video_url":
                fps = float(settings.get("video_fps", 1))
                if not 0.2 <= fps <= 5:
                    raise ValueError("customer_ceshi video_fps must be between 0.2 and 5")
                content.append({"type": "input_video", "video_url": url, "fps": fps})
                kinds.append("video")
            elif part_type in {"audio_url", "input_audio"}:
                content.append({"type": "input_audio", "audio_url": url})
                kinds.append("audio")
        user_text = _text(raw)
        if user_text:
            content.append({"type": "input_text", "text": user_text})
        if not kinds:
            raise ValueError("customer_ceshi multimodal request has no valid public media URL")
        return content, kinds

    @staticmethod
    def _response_text(response: Any) -> str:
        return NativeToolRuntime._responses_text(response)

    @staticmethod
    def _response_usage(response: Any) -> dict[str, Any]:
        usage = getattr(response, "usage", None) or (response.get("usage", {}) if isinstance(response, dict) else {})
        if not isinstance(usage, dict):
            usage = usage.model_dump() if hasattr(usage, "model_dump") else {}
        input_details = usage.get("input_tokens_details") or {}
        output_details = usage.get("output_tokens_details") or {}
        return {
            "context_tokens": usage.get("input_tokens"),
            "output_tokens": usage.get("output_tokens"),
            "reasoning_tokens": output_details.get("reasoning_tokens"),
            "cache_hits": input_details.get("cached_tokens", 0),
            "provider_status": getattr(response, "status", "") or (response.get("status", "") if isinstance(response, dict) else ""),
        }

    def _multimodal_options(self, settings: dict[str, Any]) -> dict[str, Any]:
        options: dict[str, Any] = {
            "store": bool(settings.get("store", True)),
            "max_output_tokens": int(settings.get("max_output_tokens", self.config.get("max_tokens", 8192))),
            "temperature": settings.get("temperature", self.config.get("temperature", 0.2)),
            "top_p": settings.get("top_p", self.config.get("top_p", 0.9)),
        }
        thinking = settings.get("thinking")
        if isinstance(thinking, dict):
            options["thinking"] = thinking
        elif str(settings.get("thinking_type", self.config.get("multimodal_thinking_type", "enabled"))) == "enabled":
            options["thinking"] = {"type": "enabled"}
        reasoning_effort = settings.get("reasoning_effort", self.config.get("reasoning_effort"))
        if reasoning_effort:
            options["reasoning_effort"] = reasoning_effort
        return {key: value for key, value in options.items() if value is not None}

    @staticmethod
    def _normalized_media_candidate(candidate: dict[str, Any] | None, settings: dict[str, Any]) -> dict[str, str] | None:
        if not isinstance(candidate, dict):
            return None
        confidence = str(candidate.get("confidence") or "").lower()
        if confidence != str(settings["minimum_confidence"]).lower():
            return None
        if candidate.get("missing_fields") or candidate.get("validation_errors"):
            return None
        fields = {key: str(candidate.get(key) or "").strip() for key in _MEDIA_UPDATE_FIELDS}
        operation = fields["operation_type"]
        if not re.fullmatch(r"\d{9}", re.sub(r"\s+", "", fields["mmsi"])):
            return None
        if operation == "position_update":
            if not all(fields[key] and not _PLACEHOLDER_VALUE.fullmatch(fields[key]) for key in ("lon", "lat", "updatetime")):
                return None
            if not NativeToolRuntime._valid_update_time(fields["updatetime"]):
                return None
        elif operation == "static_update":
            if not any(fields[key] and not _PLACEHOLDER_VALUE.fullmatch(fields[key]) for key in ("ship_name", "imo", "ship_type", "minotype", "length", "width", "dwt", "flag", "callsign", "built_year", "destination", "eta", "draft")):
                return None
        else:
            return None
        fields["mmsi"] = re.sub(r"\s+", "", fields["mmsi"])
        return {key: value for key, value in fields.items() if value and not _PLACEHOLDER_VALUE.fullmatch(value)}

    def _create_multimodal_response(self, request: dict[str, Any]) -> Any:
        if self.multimodal_responses_client is None:
            raise RuntimeError("multimodal_responses_client_unavailable")
        try:
            return self.multimodal_responses_client.responses.create(**request)
        except Exception:
            optional = {"store", "max_output_tokens", "temperature", "top_p", "thinking", "reasoning_effort", "text"}
            reduced = {key: value for key, value in request.items() if key not in optional}
            if len(reduced) == len(request):
                raise
            return self.multimodal_responses_client.responses.create(**reduced)

    def _result(self, *, answer: str, status: str, started: float, model: str, media_types: list[str], memory_rounds: int, context_compacted: bool, finish_reason: str, usage: dict[str, Any] | None = None, provider_error: str = "", observations: list[dict[str, Any]] | None = None, tool_names: list[str] | None = None, model_calls: int | None = None, tool_calls: int | None = None, provider_response_id: str = "", fallback_reason: str = "", media_response_text: str = "", media_candidate_status: str = "not_requested") -> dict[str, Any]:
        answer = limit_reply(_wechat_plain_text(answer, media_fallback="我暂时无法从该附件中读取足够信息，请上传更清晰的文件或补充文字说明。"))
        observations = list(observations or [])
        tool_names = list(tool_names or [])
        metrics = {
            "runtime_mode": "multimodal_responses",
            "requested_runtime_mode": "multimodal_responses",
            "effective_runtime": "multimodal_responses",
            "orchestrator_model": model,
            "perception_model": "",
            "model_calls": model_calls if model_calls is not None else (1 if status == "success" else 0),
            "tool_calls": tool_calls if tool_calls is not None else 0,
            "media_calls": 1 if media_types else 0,
            "latency_ms": int((time.monotonic() - started) * 1000),
            "finish_reason": finish_reason,
            "guard_result": "not_required",
            "context_rounds": memory_rounds,
            "context_turns": memory_rounds,
            "context_compacted": context_compacted,
            "provider_error": provider_error,
            "fallback_reason": fallback_reason,
            "media_response_text": "present" if media_response_text.strip() else "empty",
            "media_candidate_status": media_candidate_status,
            "response_id_suffix": provider_response_id[-12:] if provider_response_id else "",
            "output_length": len(answer),
            "scenario": "multimodal_symbol" if media_types else "",
            "skills_runtime": self.skill_runtime_metadata,
            **(usage or {}),
        }
        trace = safe_trace({
            "agent": "customer_ceshi_responses",
            "checkpoint_namespace": CHECKPOINT_NAMESPACE,
            "runtime_mode": "multimodal_responses",
            "orchestrator_model": model,
            "media_types": media_types,
            "provider_response_id": provider_response_id[-12:] if provider_response_id else "",
            "tool_calls": tool_names,
            "observations": observations,
            "metrics": metrics,
            "skills_runtime": self.skill_runtime_metadata,
        })
        return {"phase": "done", "status": status, "generated_answer": answer, "messages": [AIMessage(content=answer)], "generated_tool_calls": tool_names, "observations": observations, "metrics": metrics, "route_trace": trace}

    @staticmethod
    def _update_feedback(evidence: MediaUpdateEvidence, observation: Observation) -> str:
        fields = evidence.fields
        identity = fields.get("ship_name") or "未提供船名"
        if observation.status == "success":
            updated = "、".join((observation.data or {}).get("updated_fields") or []) or "船位信息"
            position = "；".join(
                part for part in (
                    f"经度 {fields.get('lon')}" if fields.get("lon") else "",
                    f"纬度 {fields.get('lat')}" if fields.get("lat") else "",
                    f"更新时间 {fields.get('updatetime')}" if fields.get("updatetime") else "",
                ) if part
            )
            return f"已完成船舶信息更新。\n船舶：{identity}（MMSI {fields.get('mmsi')}）\n已更新：{updated}\n数据：{position}\n执行结果：更新成功"
        detail = "；".join(str(item) for item in observation.facts[:1]) or "写入服务未返回成功结果。"
        return f"未执行成功，船舶数据尚未更新。\n船舶：{identity}（MMSI {fields.get('mmsi')}）\n已校验数据：经度 {fields.get('lon', '未提供')}，纬度 {fields.get('lat', '未提供')}，更新时间 {fields.get('updatetime', '未提供')}\n原因：{detail}\n建议：请核对字段后重新发送明确的更新请求。"

    def _try_media_update(self, human: HumanMessage | None, session_key: str) -> dict[str, Any] | None:
        evidence = self.media_evidence.get(session_key)
        text = _text(getattr(human, "content", ""))
        if evidence is None:
            return None
        explicit = bool(_MEDIA_UPDATE_COMMAND.search(text))
        confirmed = bool(_CONFIRM_ONLY.fullmatch(text))
        if not explicit and not confirmed:
            self.media_evidence.clear(session_key)
            return None
        observation = self.text_runtime._execute_update_candidate(
            evidence.fields,
            text,
            trusted_media_fields=evidence.fields,
        )
        self.media_evidence.clear(session_key)
        answer = _wechat_plain_text(self._update_feedback(evidence, observation))
        metrics = {
            "runtime_mode": "media_update_preflight",
            "requested_runtime_mode": "media_update_preflight",
            "effective_runtime": "media_update_preflight",
            "orchestrator_model": "",
            "perception_model": "",
            "model_calls": 0,
            "tool_calls": 1,
            "media_calls": 0,
            "context_rounds": 0,
            "context_turns": 0,
            "context_compacted": False,
            "finish_reason": observation.status,
            "guard_result": "not_required",
            "media_update_evidence": True,
            "provider_error": "",
            "response_id_suffix": "",
        }
        trace = safe_trace({"agent": "customer_ceshi_responses", "checkpoint_namespace": CHECKPOINT_NAMESPACE, "runtime_mode": "media_update_preflight", "tool_calls": [UPDATE_CANDIDATE_TOOL_NAME], "observations": [observation.model_dump()], "metrics": metrics})
        return {"phase": "done", "status": "success" if observation.status == "success" else "degraded", "generated_answer": answer, "messages": [AIMessage(content=answer)], "generated_tool_calls": [UPDATE_CANDIDATE_TOOL_NAME], "observations": [observation.model_dump()], "metrics": metrics, "route_trace": trace}

    def _media_tools(self) -> list[dict[str, Any]]:
        read_only = [_tool_schema(tool) for name, tool in self.text_runtime.registry._tools.items() if name in READ_ONLY_TOOL_NAMES]
        tools = [
            {
                "type": "function",
                "name": schema["function"]["name"],
                "description": schema["function"]["description"],
                "parameters": schema["function"]["parameters"],
            }
            for schema in read_only
        ]
        tools.append(_media_update_evidence_schema())
        return tools

    def _record_media_update_evidence(self, arguments: dict[str, Any], *, session_key: str, media_types: list[str]) -> Observation:
        fields = self._normalized_media_candidate(arguments, _media_evidence_settings(self.config))
        if not fields:
            return Observation(
                status="invalid_input",
                capability=MEDIA_UPDATE_EVIDENCE_TOOL_NAME,
                warnings=["media_candidate_failed_validation"],
                retry_allowed=False,
            )
        source_turn_id = f"{int(time.time() * 1000)}:{','.join(media_types)}"
        self.media_evidence.put(session_key, MediaUpdateEvidence(fields=fields, media_types=tuple(media_types), source_turn_id=source_turn_id))
        return Observation(
            status="success",
            capability=MEDIA_UPDATE_EVIDENCE_TOOL_NAME,
            facts=["当前附件的 AIS 更新字段已通过受控校验并暂存。"],
            data={"recorded": True, "field_count": len(fields)},
            retry_allowed=False,
        )

    def _media_observation(self, name: str, arguments: dict[str, Any], *, session_key: str, media_types: list[str]) -> Observation:
        if name == MEDIA_UPDATE_EVIDENCE_TOOL_NAME:
            return self._record_media_update_evidence(arguments, session_key=session_key, media_types=media_types)
        if name in DENIED_TOOL_NAMES or name == UPDATE_CANDIDATE_TOOL_NAME:
            return Observation(status="forbidden", capability=name, warnings=["write_tools_disabled"], retry_allowed=False)
        return self.text_runtime._execute(name, arguments, {})

    def _invoke_multimodal(self, messages: list[Any], human: HumanMessage, assets: list[MediaAsset], session_key: str, context_block: str, memory_rounds: int, context_compacted: bool) -> dict[str, Any]:
        started = time.monotonic()
        # A new attachment must never leave an older AIS candidate available for writing.
        self.media_evidence.clear(session_key)
        general = _responses_settings(self.config, "doubao")
        input_content, media_types = self._media_content(human, context_block, self._inbound_system(messages), general)
        model_settings = dict(general.get("image_video") or {}) if any(kind in {"image", "video"} for kind in media_types) else dict(general.get("audio") or {})
        settings = {**general, **model_settings}
        model = str(settings.get("model") or ("doubao-seed-2-1-pro-260628" if any(kind in {"image", "video"} for kind in media_types) else "doubao-seed-2-0-lite-260428"))
        request = {
            "model": model,
            "input": [{"role": "user", "content": input_content}],
            "tools": self._media_tools(),
            **self._multimodal_options(settings),
        }
        request["tool_choice"] = settings.get("tool_choice", "auto")
        observations: list[dict[str, Any]] = []
        tool_names: list[str] = []
        model_calls = tool_calls = 0
        media_candidate_status = "not_requested"
        fallback_reason = ""
        try:
            try:
                response = self._create_multimodal_response(request)
            except Exception as exc:
                fallback_reason = f"doubao_responses_tools_unavailable:{_provider_error_summary(exc)}"
                request.pop("tools", None)
                request.pop("tool_choice", None)
                response = self._create_multimodal_response(request)
            usage = self._response_usage(response)
            response_id = str(getattr(response, "id", "") or (response.get("id", "") if isinstance(response, dict) else ""))
            answer = ""
            finish_reason = "stop"
            search_fingerprints: set[str] = set()
            search_counts = {"local_kb_search": 0, "web_search": 0}
            for _ in range(self.text_runtime.max_steps):
                model_calls += 1
                calls = NativeToolRuntime._responses_calls(response) if "tools" in request else []
                if not calls:
                    answer = self._response_text(response)
                    break
                outputs: list[dict[str, Any]] = []
                force_final = False
                exhausted = False
                for call in calls:
                    name, arguments = call["name"], call["args"]
                    if tool_calls >= self.text_runtime.max_tool_calls:
                        exhausted = True
                        break
                    if name in _SEARCH_TOOL_NAMES:
                        fingerprint = f"{name}:{self.text_runtime._normalized_query(arguments)}"
                        limit = self.text_runtime.search_settings["max_local_kb_calls"] if name == "local_kb_search" else self.text_runtime.search_settings["max_web_calls"] if name == "web_search" else None
                        if not fingerprint or fingerprint in search_fingerprints or (limit is not None and search_counts.get(name, 0) >= limit):
                            observation = Observation(status="forbidden", capability=name, warnings=["duplicate_or_budgeted_search"], retry_allowed=False)
                        else:
                            search_fingerprints.add(fingerprint)
                            search_counts[name] = search_counts.get(name, 0) + 1
                            observation = self._media_observation(name, arguments, session_key=session_key, media_types=media_types)
                    else:
                        observation = self._media_observation(name, arguments, session_key=session_key, media_types=media_types)
                    tool_calls += 1
                    tool_names.append(name)
                    observed = observation.model_dump()
                    observed["evidence_id"] = f"e-{len(observations) + 1}"
                    observations.append(observed)
                    compact = self.text_runtime._compact_observation(observation)
                    compact["evidence_id"] = observed["evidence_id"]
                    outputs.append({"type": "function_call_output", "call_id": call["id"], "output": json.dumps(compact, ensure_ascii=False)})
                    if name == MEDIA_UPDATE_EVIDENCE_TOOL_NAME:
                        media_candidate_status = "recorded" if observation.status == "success" else "rejected"
                    force_final = force_final or self.text_runtime._can_answer_from(observation)
                if exhausted:
                    finish_reason = "tool_budget"
                    request["tool_choice"] = "none"
                elif force_final:
                    request["tool_choice"] = "none"
                if not outputs:
                    break
                request = {"model": model, "input": outputs, "tools": self._media_tools(), **self._multimodal_options(settings)}
                if force_final or exhausted:
                    request["tool_choice"] = "none"
                if response_id:
                    request["previous_response_id"] = response_id
                response = self._create_multimodal_response(request)
                usage = self._response_usage(response)
                response_id = str(getattr(response, "id", "") or (response.get("id", "") if isinstance(response, dict) else ""))
                if exhausted:
                    answer = self._response_text(response)
                    break
            if not answer:
                answer = self._response_text(response) or "我暂时无法从附件和现有资料中确认该问题。请补充具体功能页面、账号权限或异常时间后再核验。"
            answer = _wechat_position_result(_text(getattr(human, "content", "")), observations) or answer
            _, guard_result = self.text_runtime._guard(answer, observations)
            evidence = self.media_evidence.get(session_key)
            if evidence and _MEDIA_UPDATE_COMMAND.search(_text(getattr(human, "content", ""))):
                direct = self._try_media_update(human, session_key)
                if direct is not None:
                    direct_metrics = dict(direct["metrics"])
                    direct_metrics.update({"model_calls": model_calls, "tool_calls": tool_calls + 1, "runtime_mode": "multimodal_responses", "requested_runtime_mode": "multimodal_responses", "orchestrator_model": model, "media_update_evidence": True, "guard_result": guard_result, **usage})
                    direct["metrics"] = direct_metrics
                    direct["route_trace"] = safe_trace({**dict(direct["route_trace"]), "runtime_mode": "multimodal_responses", "orchestrator_model": model, "media_types": media_types, "metrics": direct_metrics})
                    return direct
            self.memory.record(session_key, user_text=self._user_memory_text(human, assets), answer_text=answer)
            result = self._result(answer=answer, status="success", started=started, model=model, media_types=media_types, memory_rounds=memory_rounds, context_compacted=context_compacted, finish_reason=finish_reason, usage=usage, observations=observations, tool_names=tool_names, model_calls=model_calls, tool_calls=tool_calls, provider_response_id=response_id, fallback_reason=fallback_reason, media_response_text=self._response_text(response), media_candidate_status=media_candidate_status)
            result["metrics"]["media_update_evidence"] = bool(self.media_evidence.get(session_key))
            result["metrics"]["guard_result"] = guard_result
            result["route_trace"]["metrics"] = result["metrics"]
            return result
        except Exception as exc:
            return self._result(answer="当前附件读取失败，请稍后重试或重新上传文件。", status="degraded", started=started, model=model, media_types=media_types, memory_rounds=memory_rounds, context_compacted=context_compacted, finish_reason=f"error:{type(exc).__name__}", provider_error=_provider_error_summary(exc), observations=observations, tool_names=tool_names, model_calls=model_calls, tool_calls=tool_calls, fallback_reason=fallback_reason, media_candidate_status=media_candidate_status)

    def invoke(self, payload: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        messages = list(payload.get("messages") or [])
        human = _latest_human(messages)
        assets = _assets(human)
        self.text_runtime._active_scenario = classify_scenario(_text(getattr(human, "content", "")), has_media=bool(assets))
        session_key = _session_key(payload, config)
        context_block, memory_rounds, context_compacted = self.memory.render(session_key)
        # DeepSeek remains the business orchestrator even when attachments are present.
        # Doubao is available only through NativeToolRuntime.inspect_media.
        confirmation_text = _text(getattr(human, "content", "")).strip().strip("。！？!?，,；;")
        draft_session_key = f"customer_ceshi:{session_key}"
        if _CONFIRM_ONLY.fullmatch(confirmation_text) and self.text_runtime.drafts.get(draft_session_key) is not None:
            started = time.monotonic()
            observation = self.text_runtime._draft_operation(
                COMMIT_SHIP_UPDATE_TOOL_NAME,
                {"confirmed": True},
                draft_session_key,
            )
            if observation.status == "partial":
                answer = "更新请求已通过测试校验，但未执行生产写入；当前不能确认船舶信息已经更新完成。"
                guard_result = "accepted_write_not_confirmed"
            elif observation.status == "invalid_input":
                answer = "当前没有可确认的有效更新草稿，请先提供需要更新的船舶信息。"
                guard_result = "no_pending_draft"
            else:
                answer = "当前更新草稿未能提交，请核对草稿状态后重试。"
                guard_result = "draft_commit_not_completed"
            result = self.text_runtime._result(
                answer,
                [observation.model_dump()],
                [COMMIT_SHIP_UPDATE_TOOL_NAME],
                0,
                1,
                0,
                "stop",
                guard_result,
                started,
                "",
                "",
                "",
            )
            result["metrics"].update({"context_rounds": memory_rounds, "context_turns": memory_rounds, "context_compacted": context_compacted, "update_draft_status": str((observation.data or {}).get("adapter_status") or observation.status)})
            result["route_trace"]["metrics"] = result["metrics"]
            return result
        if not assets:
            preflight = self.text_runtime._prepare_text_update(_text(getattr(human, "content", "")), draft_session_key)
            if preflight is not None:
                started = time.monotonic()
                if preflight.status == "success":
                    fields = dict(preflight.data or {}).get("fields") or {}
                    target = dict(preflight.data or {}).get("target") or {}
                    field_preview = "、".join(f"{key} {value}" for key, value in fields.items())
                    answer = f"已生成待确认的船舶更新草稿：MMSI {target.get('mmsi', '未提供')}；{field_preview}。请核对无误后回复“确认”。"
                    guard_result = "draft_prepared"
                else:
                    answer = str(preflight.suggested_fix or "请补充完整船位更新字段后再试。")
                    guard_result = "position_update_fields_required"
                result = self.text_runtime._result(
                    answer,
                    [preflight.model_dump()],
                    [PREPARE_SHIP_UPDATE_TOOL_NAME],
                    0,
                    1,
                    0,
                    "stop",
                    guard_result,
                    started,
                    "",
                    "",
                    "",
                )
                result["metrics"].update({
                    "context_rounds": memory_rounds,
                    "context_turns": memory_rounds,
                    "context_compacted": context_compacted,
                    "update_draft_status": "prepared" if preflight.status == "success" else preflight.status,
                })
                result["route_trace"]["metrics"] = result["metrics"]
                return result
        direct = self._try_media_update(human, session_key)
        if direct is not None:
            direct["metrics"].update({"context_rounds": memory_rounds, "context_turns": memory_rounds, "context_compacted": context_compacted})
            direct["route_trace"]["metrics"] = direct["metrics"]
            return direct
        request = dict(payload)
        request["_customer_ceshi_context"] = context_block
        request["_customer_ceshi_session_key"] = f"customer_ceshi:{session_key}"
        result = self.text_runtime.invoke(request, config)
        answer = str(result.get("generated_answer") or "")
        if answer and result.get("status") == "success":
            self.memory.record(session_key, user_text=self._user_memory_text(human, []), answer_text=answer, observations=list(result.get("observations") or []))
        metrics = dict(result.get("metrics") or {})
        metrics["context_rounds"] = memory_rounds
        metrics["context_turns"] = memory_rounds
        metrics["context_compacted"] = context_compacted
        result["metrics"] = metrics
        trace = dict(result.get("route_trace") or {})
        trace["metrics"] = metrics
        result["route_trace"] = safe_trace(trace)
        return result

def build_customer_ceshi_responses_agent(ctx: Any, cfg: dict[str, Any], workspace_path: str, profile: Any, intent_hint: str = "") -> _NamespacedRuntime:
    config = dict(cfg.get("config") or cfg or {})
    text_settings = _nested_config(config, "customer_ceshi_runtime", "text_model")
    deepseek_settings = _responses_settings(config, "deepseek")
    client = getattr(ctx, "customer_ceshi_responses_client", None) if ctx is not None else None
    client = client or build_chat_model(ctx, cfg, role="text", streaming=True, model_override=str(deepseek_settings.get("model") or text_settings.get("model") or config.get("customer_ceshi_responses_text_model") or config.get("customer_ceshi_v2_text_model") or config.get("text_model") or ""), timeout=text_settings.get("timeout_seconds", config.get("customer_ceshi_responses_timeout_seconds", config.get("customer_ceshi_v2_timeout_seconds", 30))), allow_runtime_model_override=False)
    registry = getattr(ctx, "customer_ceshi_responses_tool_registry", None) if ctx is not None else None
    v2_bundle = None
    v2_fallback_reason = ""
    requested_v2 = resolve_skill_runtime("customer_ceshi", workspace_path) == "v2"
    if registry is None and requested_v2:
        try:
            v2_bundle = build_customer_ceshi_bundle(workspace_path)
            registry = CapabilityRegistry(
                tools=list(v2_bundle.tools),
                shared_descriptors=v2_bundle.descriptors,
                enforce_known_public_urls=True,
            )
        except Exception as exc:
            v2_fallback_reason = type(exc).__name__
            logger.warning("customer_ceshi Skills V2 is unavailable; using the existing constrained runtime: %s", v2_fallback_reason)
    registry = registry or CapabilityRegistry(
        skill_names=list(getattr(profile, "skills", []) or []),
        enforce_known_public_urls=True,
    )
    if client is None:
        raise RuntimeError("customer_ceshi native tool runtime is unavailable: model credentials or base URL are missing")
    runtime = runtime_config(cfg)
    responses_client = getattr(ctx, "customer_ceshi_responses_api_client", None) if ctx is not None else None
    if responses_client is None and runtime["mode"] == "responses":
        api_key = os.getenv("COZE_WORKLOAD_IDENTITY_API_KEY", "").strip()
        base_url = resolve_role_base_url(cfg, "text")
        if api_key and base_url:
            responses_client = OpenAI(api_key=api_key, base_url=base_url, default_headers=safe_default_headers(ctx))
    selected_mode = "responses" if runtime["mode"] == "responses" and runtime["responses_enabled"] and responses_client is not None else "chat_function_calling"
    if selected_mode == "chat_function_calling" and not runtime["chat_fallback_enabled"]:
        raise RuntimeError("Responses API is unavailable and chat_function_calling fallback is disabled")
    if runtime["mode"] == "responses" and selected_mode == "chat_function_calling" and runtime["fallback_mode"] != "chat_function_calling":
        raise RuntimeError("Responses API is unavailable and configured fallback_mode is not chat_function_calling")
    multimodal_responses_client = getattr(ctx, "customer_ceshi_multimodal_responses_api_client", None) if ctx is not None else None
    if multimodal_responses_client is None:
        api_key = os.getenv("COZE_WORKLOAD_IDENTITY_API_KEY", "").strip()
        base_url = resolve_role_base_url(cfg, "multimodal")
        if api_key and base_url:
            multimodal_responses_client = OpenAI(api_key=api_key, base_url=base_url, default_headers=safe_default_headers(ctx))
    perception = ResponsesMediaPerception(multimodal_responses_client, config)
    skill_prompt = v2_bundle.prompt if v2_bundle is not None else ""
    text_runtime = NativeToolRuntime(
        client=client,
        registry=registry,
        perception=perception,
        config=config,
        mode=selected_mode,
        responses_client=responses_client,
        profile_prompt="\n\n---\n\n".join(part for part in (read_profile_prompt(profile), skill_prompt) if part),
        tool_descriptors=v2_bundle.descriptors if v2_bundle is not None else (),
        skill_runtime_metadata={
            "mode": "v2" if v2_bundle is not None else ("legacy_constrained" if requested_v2 else "legacy"),
            "source_versions": dict(v2_bundle.source_versions) if v2_bundle is not None else {},
            **({"fallback_reason": v2_fallback_reason} if v2_fallback_reason else {}),
        },
    )
    return _NamespacedRuntime(
        SingleModelCustomerCeshiRuntime(
            text_runtime=text_runtime,
            multimodal_responses_client=multimodal_responses_client,
            config=config,
            memory=_shared_conversation_memory(config),
            media_evidence=_shared_media_update_evidence(config),
        )
    )
