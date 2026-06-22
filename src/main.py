import argparse
import asyncio
import json
import traceback
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, Iterable, AsyncIterable, AsyncGenerator, Optional, List
from urllib.parse import urlparse

# 在所有其他导入之前加载 .env 文件
from dotenv import load_dotenv


def _resolve_workspace_path() -> str:
    """优先使用显式配置，其次回退到项目根目录。"""
    configured = os.getenv("COZE_WORKSPACE_PATH")
    if configured:
        return configured
    return os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


_workspace_path = _resolve_workspace_path()
os.environ.setdefault("COZE_WORKSPACE_PATH", _workspace_path)
_env_file = os.path.join(_workspace_path, ".env")
load_dotenv(_env_file)

import cozeloop
import uvicorn
import time
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse, JSONResponse, FileResponse
from langchain_core.runnables import RunnableConfig
from langgraph.graph.state import CompiledStateGraph
from coze_coding_utils.runtime_ctx.context import new_context, Context
from coze_coding_utils.helper import graph_helper
from coze_coding_utils.log.node_log import LOG_FILE
from coze_coding_utils.log.write_log import setup_logging, request_context
from coze_coding_utils.log.config import LOG_LEVEL
from coze_coding_utils.error.classifier import ErrorClassifier
from coze_coding_utils.helper.stream_runner import AgentStreamRunner, agent_stream_handler, RunOpt
from agents.profiles import PROFILE_HEADER, resolve_profile_id, set_current_agent_profile
from agents.customer_support_stream_debug import (
    DebugRuntimeCursor,
    build_customer_support_debug_events,
    build_customer_support_debug_events_from_update,
)
from llm_config import load_llm_config, messages_have_multimodal_content, resolve_model_selection
from utils.llm_route_state import clear_current_llm_route, set_current_llm_route

setup_logging(
    log_file=LOG_FILE,
    max_bytes=100 * 1024 * 1024, # 100MB
    backup_count=5,
    log_level=LOG_LEVEL,
    use_json_format=True,
    console_output=True
)

logger = logging.getLogger(__name__)
from coze_coding_utils.helper.agent_helper import to_stream_input
from coze_coding_utils.openai.handler import OpenAIChatHandler
from coze_coding_utils.log.err_trace import extract_core_stack
from coze_coding_utils.log.loop_trace import init_run_config, init_agent_config
from admin_api import router as admin_router

try:
    from observability.writer import (
        ensure_observability_schema as _ensure_observability_schema,
        schedule_api_call_log as _schedule_api_call_log,
        schedule_agent_error_log as _schedule_agent_error_log,
    )
except Exception as obs_import_err:
    logger.warning(f"[Observability] writer import failed, fallback to no-op: {obs_import_err}")

    def _ensure_observability_schema() -> None:
        return

    def _schedule_api_call_log(payload: Dict[str, Any]) -> None:
        return

    def _schedule_agent_error_log(payload: Dict[str, Any]) -> None:
        return


# 超时配置常量
TIMEOUT_SECONDS = 900  # 15分钟
HEADER_X_INTENT_HINT = "x-intent-hint"


def _ensure_context_headers(ctx: Optional[Context]) -> Dict[str, Any]:
    """Return a mutable headers dict for the current context.

    Some runtime contexts do not expose a headers attribute. In that case we
    create one lazily so downstream code can keep using a dict-like contract.
    """
    if ctx is None:
        return {}
    headers = getattr(ctx, "headers", None)
    if isinstance(headers, dict):
        return headers
    try:
        headers = {}
        setattr(ctx, "headers", headers)
        return headers
    except Exception:
        return {}


def is_feature_enabled(name: str, default: bool = False) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


def _content_segments_to_text(segments: List[Dict[str, Any]]) -> str:
    parts: List[str] = []
    for seg in segments:
        if seg.get("type") == "text":
            text = seg.get("text", "")
            if text:
                parts.append(str(text))
    return "\n".join(parts).strip()


def _build_multimodal_content_from_input(input_payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    content: List[Dict[str, Any]] = []
    text = str(input_payload.get("text", "")).strip()
    image_url = str(input_payload.get("image_url", "")).strip()
    audio_url = str(input_payload.get("audio_url", "")).strip()
    audio_format = str(input_payload.get("audio_format", "") or input_payload.get("format", "")).strip()
    video_url = str(input_payload.get("video_url", "")).strip()

    if image_url:
        content.append({"type": "image_url", "image_url": {"url": image_url}})
    if audio_url:
        audio_obj: Dict[str, Any] = {"url": audio_url}
        if audio_format:
            audio_obj["format"] = audio_format
        content.append({"type": "input_audio", "input_audio": audio_obj})
    if video_url:
        content.append({"type": "video_url", "video_url": {"url": video_url}})
    if text:
        content.append({"type": "text", "text": text})
    return content


def normalize_request_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    统一请求结构为 messages 形态，便于单链路处理。
    兼容：
    - websdk: messages / input
    - wechat_mp / wechat_kf / 兼容渠道: content.query.prompt
    """
    normalized = dict(payload)
    source_channel = str(normalized.get("source_channel", "")).strip()

    # 1) 微信/兼容 prompt 格式 -> messages
    # 不强依赖 source_channel，避免 wechat_kf 等同构渠道因未命中白名单而被静默拒绝。
    prompts = (
        normalized.get("content", {})
        .get("query", {})
        .get("prompt", [])
    )
    if "messages" not in normalized and isinstance(prompts, list) and prompts:
        if source_channel in ("wechat_mp", "wechat_kf") or normalized.get("content", {}).get("query"):
            content_segments: List[Dict[str, Any]] = []
            for prompt in prompts:
                prompt_type = str(prompt.get("type", "text")).strip()
                prompt_content = prompt.get("content", {}) or {}
                if prompt_type == "text":
                    text = str(prompt_content.get("text", "")).strip()
                    if text:
                        content_segments.append({"type": "text", "text": text})
                elif prompt_type == "image":
                    url = str(prompt_content.get("url", "")).strip()
                    if url:
                        content_segments.append({"type": "image_url", "image_url": {"url": url}})
                elif prompt_type == "voice":
                    url = str(prompt_content.get("url", "")).strip()
                    fmt = str(prompt_content.get("format", "")).strip()
                    if url:
                        audio_obj: Dict[str, Any] = {"url": url}
                        if fmt:
                            audio_obj["format"] = fmt
                        content_segments.append({"type": "input_audio", "input_audio": audio_obj})
                elif prompt_type == "video":
                    url = str(prompt_content.get("url", "")).strip()
                    if url:
                        content_segments.append({"type": "video_url", "video_url": {"url": url}})
                elif prompt_type in ("location", "link", "event"):
                    # 弱结构化内容降级为文本，避免静默丢弃
                    content_segments.append({
                        "type": "text",
                        "text": json.dumps({prompt_type: prompt_content}, ensure_ascii=False),
                    })

            text_only = _content_segments_to_text(content_segments)
            if len(content_segments) == 1 and content_segments[0].get("type") == "text":
                user_content: Any = text_only
            else:
                user_content = content_segments

            normalized["messages"] = [{"role": "user", "content": user_content}]
            if text_only and "input" not in normalized:
                normalized["input"] = text_only

    # 2) 非微信：input -> messages
    if "messages" not in normalized:
        if "input" in normalized:
            ipt = normalized.get("input")
            if isinstance(ipt, str):
                normalized["messages"] = [{"role": "user", "content": ipt}]
            elif isinstance(ipt, dict):
                if str(ipt.get("type", "")).strip().lower() == "multimodal":
                    content = _build_multimodal_content_from_input(ipt)
                    normalized["messages"] = [{"role": "user", "content": content or str(ipt)}]
                else:
                    normalized["messages"] = [{"role": "user", "content": json.dumps(ipt, ensure_ascii=False)}]
            else:
                normalized["messages"] = [{"role": "user", "content": str(ipt)}]
        elif "text" in normalized:
            normalized["messages"] = [{"role": "user", "content": str(normalized.get("text", ""))}]

    return normalized


def _validate_normalized_payload(payload: Dict[str, Any]):
    messages = payload.get("messages")
    if not isinstance(messages, list) or not messages:
        raise ValueError("payload.messages is required and must be a non-empty list")
    if not any(str(m.get("role", "")).lower() == "user" for m in messages if isinstance(m, dict)):
        raise ValueError("payload.messages must contain at least one user message")


def _extract_user_text(messages: List[Dict[str, Any]]) -> str:
    for msg in reversed(messages):
        if not isinstance(msg, dict) or str(msg.get("role", "")).lower() != "user":
            continue
        content = msg.get("content", "")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return _content_segments_to_text(content)
    return ""


def _filename_from_url(url: str) -> str:
    try:
        path = urlparse(url).path
    except Exception:
        path = ""
    name = os.path.basename(path or "").strip()
    return name or "attachment"


def _build_stream_prompt_from_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    `agent_stream_handler -> to_client_message()` 仍依赖旧版 `content.query.prompt`
    结构，这里把标准化后的 `messages` 回填成兼容格式，避免 `/stream_run`
    在流式链路里丢失用户输入。
    """
    for msg in reversed(messages):
        if not isinstance(msg, dict) or str(msg.get("role", "")).lower() != "user":
            continue
        content = msg.get("content", "")
        if isinstance(content, str):
            return [{"type": "text", "content": {"text": content}}]
        if not isinstance(content, list):
            continue

        prompt: List[Dict[str, Any]] = []
        for part in content:
            if not isinstance(part, dict):
                continue
            part_type = str(part.get("type", "")).strip().lower()
            if part_type == "text":
                text = str(part.get("text", "")).strip()
                if text:
                    prompt.append({"type": "text", "content": {"text": text}})
                continue

            file_url = ""
            file_format = ""
            prompt_type = "upload_file"
            if part_type == "image_url":
                file_url = str((part.get("image_url") or {}).get("url", "")).strip()
                prompt_type = "image"
            elif part_type == "video_url":
                file_url = str((part.get("video_url") or {}).get("url", "")).strip()
                prompt_type = "video"
            elif part_type == "input_audio":
                audio_obj = part.get("input_audio") or {}
                file_url = str(audio_obj.get("url", "")).strip()
                file_format = str(audio_obj.get("format", "")).strip()
                prompt_type = "voice"
            elif part_type == "file_url":
                file_url = str((part.get("file_url") or {}).get("url", "")).strip()

            if file_url:
                if prompt_type in {"image", "video", "voice"}:
                    content_payload: Dict[str, Any] = {"url": file_url}
                    if file_format:
                        content_payload["format"] = file_format
                    prompt.append({"type": prompt_type, "content": content_payload})
                else:
                    prompt.append(
                        {
                            "type": "upload_file",
                            "content": {
                                "upload_file": {
                                    "file_name": _filename_from_url(file_url),
                                    "file_path": "",
                                    "url": file_url,
                                }
                            },
                        }
                    )
        return prompt
    return []


def ensure_stream_compatible_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    if (
        isinstance(payload.get("content"), dict)
        and isinstance(payload["content"].get("query"), dict)
        and payload["content"]["query"].get("prompt")
    ):
        return payload

    prompt = _build_stream_prompt_from_messages(payload.get("messages", []))
    if not prompt:
        return payload

    adapted = dict(payload)
    adapted["content"] = {"query": {"prompt": prompt}}
    return adapted




def _resolve_request_llm_route(payload: Dict[str, Any]) -> Dict[str, Any]:
    cfg = load_llm_config()
    resolved = resolve_model_selection(
        cfg,
        has_multimodal_input=messages_have_multimodal_content(payload.get("messages")),
        requested_model=str(payload.get("model", "")).strip(),
        requested_thinking=str(payload.get("thinking", "")).strip(),
    )
    payload["llm_route"] = resolved
    return resolved


def classify_intent_hint(payload: Dict[str, Any]) -> str:
    """
    轻量意图提示：
    - ship: 船舶查询/更新
    - knowledge: 其他问题
    """
    messages = payload.get("messages", [])
    text = _extract_user_text(messages).lower()
    if not text:
        return "knowledge"

    # 先识别“平台问题/服务异常”语义，避免仅因出现“船位”被错误路由到 ship-only 工具集合
    knowledge_priority_patterns = [
        "更新慢", "延迟", "异常", "报警", "告警", "为什么", "怎么", "怎么办", "无法",
        "失败", "收不到", "看不到", "不显示", "不刷新", "不准确", "功能", "教程",
        "使用", "说明", "帮助", "规则", "配置", "服务异常", "系统异常",
    ]
    if any(k in text for k in knowledge_priority_patterns):
        return "knowledge"

    # ship: 明确数据查询/更新动作，或带船舶唯一标识
    ship_strong_patterns = [
        r"\bmmsi\b", r"\bimo\b", r"\b\d{9}\b", "查询船位", "更新船位", "上传船位",
        r"查.*船位", r"船位.*查", r"查.*位置", r"位置.*查",
        "船舶档案", "psc记录", "区域船舶", "海峡通航", "更新静态信息",
    ]
    for p in ship_strong_patterns:
        if re.search(p, text):
            return "ship"
    return "knowledge"


def _adapt_wechat_request(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    适配微信服务号请求格式
    
    微信服务端发送的格式：
    {
      "content": {
        "query": {
          "prompt": [{"type": "text", "content": {"text": "..."}}]
        }
      },
      "session_id": "wx_mp_{openid}",
      "user_id": "{openid}",
      "source_channel": "wechat_mp"
    }
    
    转换为标准格式：
    {
      "input": "用户消息",
      "session_id": "wx_mp_{openid}",
      "user_id": "{openid}",
      "source_channel": "wechat_mp"
    }
    """
    # 兼容旧入口：统一走 normalize_request_payload
    adapted = normalize_request_payload(payload)
    source_channel = adapted.get("source_channel", "")
    has_messages = bool(adapted.get("messages"))
    logger.info(
        f"[WechatAdapter] Adapted request: session_id={adapted.get('session_id', '')}, "
        f"user_id={adapted.get('user_id', '')}, source_channel={source_channel}, "
        f"has_messages={has_messages}"
    )
    return adapted


class AgentService:
    def __init__(self):
        # 用于跟踪正在运行的任务（使用asyncio.Task）
        self.running_tasks: Dict[str, asyncio.Task] = {}
        # 错误分类器
        self.error_classifier = ErrorClassifier()
        # stream runner
        self._agent_stream_runner = AgentStreamRunner()

    def _get_graph(self, ctx=Context):
        return graph_helper.get_agent_instance("agents.agent", ctx)

    @staticmethod
    def _sse_event(data: Any, event_id: Any = None) -> str:
        id_line = f"id: {event_id}\n" if event_id else ""
        return f"{id_line}event: message\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"

    async def explainable_stream_sse(self, payload: Dict[str, Any], ctx=None, run_opt: Optional[RunOpt] = None) -> AsyncGenerator[str, None]:
        """Stream safe reasoning/search/review events before the real agent stream."""
        if ctx is None:
            ctx = new_context(method="stream_sse")
        if run_opt is None:
            run_opt = RunOpt()

        profile_id = str((payload or {}).get("agent_profile", "") or "customer_support")
        if profile_id != "customer_support":
            for event in build_customer_support_debug_events(payload):
                yield self._sse_event(event)
                await asyncio.sleep(0)
            async for item in self.stream_sse(payload, ctx=ctx, run_opt=run_opt):
                yield item
            return

        run_id = ctx.run_id
        logger.info(f"Starting explainable stream with run_id: {run_id}")
        graph = self._get_graph(ctx)
        run_config = init_agent_config(graph, ctx)
        session_id = ""
        if isinstance(payload, dict):
            session_id = str(payload.get("session_id", "")).strip()
        thread_id = session_id or ctx.run_id
        if not isinstance(run_config, dict):
            run_config = {}
        configurable = run_config.get("configurable")
        if not isinstance(configurable, dict):
            configurable = {}
            run_config["configurable"] = configurable
        configurable["thread_id"] = thread_id
        cursor = DebugRuntimeCursor()

        try:
            async for chunk in graph.astream(
                payload,
                config=run_config,
                context=ctx,
                stream_mode=["updates"],
            ):
                if not (isinstance(chunk, tuple) and len(chunk) == 2):
                    continue
                mode, data = chunk
                if mode != "updates" or not isinstance(data, dict):
                    continue
                for event in build_customer_support_debug_events_from_update(data, cursor):
                    yield self._sse_event(event)
                    await asyncio.sleep(0)
        finally:
            self.running_tasks.pop(run_id, None)
            clear_current_llm_route()
        cozeloop.flush()

    def _get_stream_runner(self):
        return self._agent_stream_runner

    # 流式运行（原始迭代器）：本地调用使用
    def stream(self, payload: Dict[str, Any], run_config: RunnableConfig, ctx=Context) -> Iterable[Any]:
        graph = self._get_graph(ctx)
        stream_runner = self._get_stream_runner()
        for chunk in stream_runner.stream(payload, graph, run_config, ctx):
            yield chunk

    # 同步运行：本地/HTTP 通用
    async def run(self, payload: Dict[str, Any], ctx=None) -> Dict[str, Any]:
        if ctx is None:
            ctx = new_context("run")

        run_id = ctx.run_id
        logger.info(f"Starting run with run_id: {run_id}")

        try:
            graph = self._get_graph(ctx)
            # custom tracer
            run_config = init_run_config(graph, ctx)
            session_id = ""
            if isinstance(payload, dict):
                session_id = str(payload.get("session_id", "")).strip()
            thread_id = session_id or ctx.run_id
            run_config["configurable"] = {"thread_id": thread_id}
            logger.info(
                f"[AgentService.run] memory thread_id={thread_id}, "
                f"session_id={session_id}, run_id={ctx.run_id}"
            )

            # 直接调用，LangGraph会在当前任务上下文中执行
            # 如果当前任务被取消，LangGraph的执行也会被取消
            return await graph.ainvoke(payload, config=run_config, context=ctx)

        except asyncio.CancelledError:
            logger.info(f"Run {run_id} was cancelled")
            return {"status": "cancelled", "run_id": run_id, "message": "Execution was cancelled"}
        except Exception as e:
            # 使用错误分类器分类错误
            err = self.error_classifier.classify(e, {"node_name": "run", "run_id": run_id})
            # 记录详细的错误信息和堆栈跟踪
            logger.error(
                f"Error in AgentService.run: [{err.code}] {err.message}\n"
                f"Category: {err.category.name}\n"
                f"Traceback:\n{extract_core_stack()}"
            )
            # 保留原始异常堆栈，便于上层返回真正的报错位置
            raise
        finally:
            # 清理任务记录
            self.running_tasks.pop(run_id, None)

    # 流式运行（SSE 格式化）：HTTP 路由使用
    async def stream_sse(self, payload: Dict[str, Any], ctx=None, run_opt: Optional[RunOpt] = None) -> AsyncGenerator[str, None]:
        if ctx is None:
            ctx = new_context(method="stream_sse")
        if run_opt is None:
            run_opt = RunOpt()

        run_id = ctx.run_id
        logger.info(f"Starting stream with run_id: {run_id}")
        graph = self._get_graph(ctx)
        run_config = init_agent_config(graph, ctx)

        session_id = ""
        if isinstance(payload, dict):
            session_id = str(payload.get("session_id", "")).strip()
        thread_id = session_id or ctx.run_id
        if not isinstance(run_config, dict):
            run_config = {}
        configurable = run_config.get("configurable")
        if not isinstance(configurable, dict):
            configurable = {}
            run_config["configurable"] = configurable
        configurable["thread_id"] = thread_id
        logger.info(
            f"[AgentService.stream_sse] memory thread_id={thread_id}, "
            f"session_id={session_id}, run_id={ctx.run_id}"
        )

        try:
            async for chunk in self.astream(payload, graph, run_config=run_config, ctx=ctx, run_opt=run_opt):
                yield self._sse_event(chunk)
        finally:
            # 清理任务记录
            self.running_tasks.pop(run_id, None)
            clear_current_llm_route()
        cozeloop.flush()

    # 取消执行 - 使用asyncio的标准方式
    def cancel_run(self, run_id: str, ctx: Optional[Context] = None) -> Dict[str, Any]:
        """
        取消指定run_id的执行

        使用asyncio.Task.cancel()来取消任务,这是标准的Python异步取消机制。
        LangGraph会在节点之间检查CancelledError,实现优雅的取消。
        """
        logger.info(f"Attempting to cancel run_id: {run_id}")

        # 查找对应的任务
        if run_id in self.running_tasks:
            task = self.running_tasks[run_id]
            if not task.done():
                # 使用asyncio的标准取消机制
                # 这会在下一个await点抛出CancelledError
                task.cancel()
                logger.info(f"Cancellation requested for run_id: {run_id}")
                return {
                    "status": "success",
                    "run_id": run_id,
                    "message": "Cancellation signal sent, task will be cancelled at next await point"
                }
            else:
                logger.info(f"Task already completed for run_id: {run_id}")
                return {
                    "status": "already_completed",
                    "run_id": run_id,
                    "message": "Task has already completed"
                }
        else:
            logger.warning(f"No active task found for run_id: {run_id}")
            return {
                "status": "not_found",
                "run_id": run_id,
                "message": "No active task found with this run_id. Task may have already completed or run_id is invalid."
            }

    # Agent 流式执行：HTTP SSE 与本地调试共用
    async def astream(self, payload: Dict[str, Any], graph: CompiledStateGraph, run_config: RunnableConfig, ctx=Context, run_opt: Optional[RunOpt] = None) -> AsyncIterable[Any]:
        stream_runner = self._get_stream_runner()
        async for chunk in stream_runner.astream(payload, graph, run_config, ctx, run_opt):
            yield chunk


def _to_log_json(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if value is None:
        return {}
    return {"raw": str(value)}


def _log_api_call_event(
    *,
    run_id: str,
    route: str,
    status: str,
    http_status_code: int,
    latency_ms: int,
    payload: Optional[Dict[str, Any]] = None,
    response_json: Optional[Dict[str, Any]] = None,
    session_id: str = "",
    user_id: str = "",
    source_channel: str = "",
    intent_hint: str = "",
):
    _schedule_api_call_log(
        {
            "run_id": run_id,
            "session_id": session_id or None,
            "user_id": user_id or None,
            "source_channel": source_channel or None,
            "route": route,
            "intent_hint": intent_hint or None,
            "request_json": payload or {},
            "response_json": response_json or {},
            "http_status_code": http_status_code,
            "status": status,
            "latency_ms": max(0, int(latency_ms)),
        }
    )


def _log_agent_error_event(
    *,
    run_id: str,
    route: str,
    error_code: str,
    error_message: str,
    stack_trace: str,
    node_name: str,
    error_category: str = "",
):
    _schedule_agent_error_log(
        {
            "run_id": run_id,
            "route": route,
            "error_code": error_code,
            "error_message": error_message,
            "stack_trace": stack_trace,
            "error_category": error_category or None,
            "node_name": node_name,
        }
    )


service = AgentService()
app = FastAPI()
_ensure_observability_schema()
app.include_router(admin_router)

# 管理台前端静态资源目录（构建产物）
_ADMIN_UI_DIST_DIR = (Path(_workspace_path) / "frontend" / "dist").resolve()


def _admin_ui_index_file() -> Path:
    return _ADMIN_UI_DIST_DIR / "index.html"


@app.get("/admin-ui", include_in_schema=False)
@app.get("/admin-ui/{full_path:path}", include_in_schema=False)
async def admin_ui_entry(full_path: str = ""):
    index_file = _admin_ui_index_file()
    if not index_file.exists():
        return JSONResponse(
            status_code=503,
            content={
                "error": "admin_ui_not_built",
                "message": "Admin UI dist not found. Please run: cd frontend && npm run build",
            },
        )

    requested = (_ADMIN_UI_DIST_DIR / full_path).resolve()
    if full_path and str(requested).startswith(str(_ADMIN_UI_DIST_DIR)) and requested.is_file():
        return FileResponse(str(requested))
    return FileResponse(str(index_file))

# OpenAI 兼容接口处理器
openai_handler = OpenAIChatHandler(service)


HEADER_X_RUN_ID = "x-run-id"
@app.post("/run")
async def http_run(request: Request) -> Dict[str, Any]:
    global result
    started = time.perf_counter()
    raw_body = await request.body()
    try:
        body_text = raw_body.decode("utf-8")
    except Exception as e:
        body_text = str(raw_body)
        raise HTTPException(status_code=400,
                            detail=f"Invalid JSON format: {body_text}, traceback: {traceback.format_exc()}, error: {e}")

    ctx = new_context(method="run", headers=dict(request.headers))
    # 优先使用上游指定的 run_id，保证 cancel 能精确匹配
    upstream_run_id = request.headers.get(HEADER_X_RUN_ID)
    if upstream_run_id:
        ctx.run_id = upstream_run_id
    run_id = ctx.run_id
    request_context.set(ctx)
    session_id = ""
    user_id = ""
    source_channel = "websdk"
    intent_hint = ""
    agent_profile = ""
    payload: Dict[str, Any] = {}

    try:
        payload = await request.json()
        payload = normalize_request_payload(payload)
        _validate_normalized_payload(payload)
        
        # 提取会话信息并设置到全局上下文
        session_id = str(payload.get("session_id", "")).strip()
        user_id = str(payload.get("user_id", "")).strip()
        source_channel = str(payload.get("source_channel", "websdk")).strip() or "websdk"
        
        if session_id:
            from utils.session_state import set_current_session_id
            set_current_session_id(session_id)
            logger.info(f"[Run] Set session context: session_id={session_id}, user_id={user_id}")

        intent_hint = classify_intent_hint(payload)
        headers = _ensure_context_headers(ctx)
        agent_profile = resolve_profile_id(
            source_channel=source_channel,
            requested_profile=str(payload.get("agent_profile", "")).strip(),
            headers=headers,
        )
        llm_route = _resolve_request_llm_route(payload)
        payload["intent_hint"] = intent_hint
        payload["agent_profile"] = agent_profile
        set_current_agent_profile(agent_profile)
        set_current_llm_route(llm_route)
        headers[HEADER_X_INTENT_HINT] = intent_hint
        headers[PROFILE_HEADER] = agent_profile
        logger.info(
            f"Received request for /run: "
            f"run_id={run_id}, session_id={session_id}, "
            f"source_channel={source_channel}, profile={agent_profile}, intent_hint={intent_hint}, "
            f"llm_model={llm_route.get('model')}, llm_modality={llm_route.get('modality')}, llm_thinking={llm_route.get('thinking_type')}"
        )

        # 创建任务并记录 - 这是关键，让我们可以通过run_id取消任务
        task = asyncio.create_task(service.run(payload, ctx))
        service.running_tasks[run_id] = task

        try:
            result = await asyncio.wait_for(task, timeout=float(TIMEOUT_SECONDS))
        except asyncio.TimeoutError:
            logger.error(f"Run execution timeout after {TIMEOUT_SECONDS}s for run_id: {run_id}")
            task.cancel()
            try:
                result = await task
            except asyncio.CancelledError:
                timeout_result = {
                    "status": "timeout",
                    "run_id": run_id,
                    "message": f"Execution timeout: exceeded {TIMEOUT_SECONDS} seconds"
                }
                latency_ms = int((time.perf_counter() - started) * 1000)
                _log_api_call_event(
                    run_id=run_id,
                    route="/run",
                    status="timeout",
                    http_status_code=200,
                    latency_ms=latency_ms,
                    payload=payload,
                    response_json=timeout_result,
                    session_id=session_id,
                    user_id=user_id,
                    source_channel=source_channel,
                    intent_hint=intent_hint,
                )
                return timeout_result

        if not result:
            result = {}
        if isinstance(result, dict):
            result["run_id"] = run_id
            result.setdefault("llm_route", payload.get("llm_route", {}))
        latency_ms = int((time.perf_counter() - started) * 1000)
        _log_api_call_event(
            run_id=run_id,
            route="/run",
            status=str(result.get("status", "success")) if isinstance(result, dict) else "success",
            http_status_code=200,
            latency_ms=latency_ms,
            payload=payload,
            response_json=_to_log_json(result),
            session_id=session_id,
            user_id=user_id,
            source_channel=source_channel,
            intent_hint=intent_hint,
        )
        return result

    except ValueError as e:
        logger.error(f"Payload validation error in http_run: {e}")
        latency_ms = int((time.perf_counter() - started) * 1000)
        _log_api_call_event(
            run_id=run_id,
            route="/run",
            status="bad_request",
            http_status_code=400,
            latency_ms=latency_ms,
            payload=payload,
            response_json={"error_message": str(e)},
            session_id=session_id,
            user_id=user_id,
            source_channel=source_channel,
            intent_hint=intent_hint,
        )
        raise HTTPException(status_code=400, detail=str(e))

    except json.JSONDecodeError as e:
        logger.error(f"JSON decode error in http_run: {e}, traceback: {traceback.format_exc()}")
        latency_ms = int((time.perf_counter() - started) * 1000)
        _log_api_call_event(
            run_id=run_id,
            route="/run",
            status="bad_json",
            http_status_code=400,
            latency_ms=latency_ms,
            response_json={"error_message": "Invalid JSON format"},
            session_id=session_id,
            user_id=user_id,
            source_channel=source_channel,
            intent_hint=intent_hint,
        )
        raise HTTPException(status_code=400, detail=f"Invalid JSON format, {extract_core_stack()}")

    except asyncio.CancelledError:
        logger.info(f"Request cancelled for run_id: {run_id}")
        result = {"status": "cancelled", "run_id": run_id, "message": "Execution was cancelled"}
        latency_ms = int((time.perf_counter() - started) * 1000)
        _log_api_call_event(
            run_id=run_id,
            route="/run",
            status="cancelled",
            http_status_code=200,
            latency_ms=latency_ms,
            payload=payload,
            response_json=result,
            session_id=session_id,
            user_id=user_id,
            source_channel=source_channel,
            intent_hint=intent_hint,
        )
        return result

    except Exception as e:
        # 使用错误分类器获取错误信息
        error_response = service.error_classifier.get_error_response(e, {"node_name": "http_run", "run_id": run_id})
        logger.error(
            f"Unexpected error in http_run: [{error_response['error_code']}] {error_response['error_message']}, "
            f"traceback: {traceback.format_exc()}", exc_info=True
        )
        stack_trace = extract_core_stack()
        latency_ms = int((time.perf_counter() - started) * 1000)
        _log_api_call_event(
            run_id=run_id,
            route="/run",
            status="error",
            http_status_code=500,
            latency_ms=latency_ms,
            payload=payload,
            response_json={
                "error_code": error_response["error_code"],
                "error_message": error_response["error_message"],
            },
            session_id=session_id,
            user_id=user_id,
            source_channel=source_channel,
            intent_hint=intent_hint,
        )
        _log_agent_error_event(
            run_id=run_id,
            route="/run",
            error_code=error_response["error_code"],
            error_message=error_response["error_message"],
            stack_trace=stack_trace,
            node_name="http_run",
            error_category=error_response.get("error_category", ""),
        )
        raise HTTPException(
            status_code=500,
            detail={
                "error_code": error_response["error_code"],
                "error_message": error_response["error_message"],
                "stack_trace": stack_trace,
            }
        )
    finally:
        cozeloop.flush()



def _register_task(run_id: str, task: asyncio.Task):
    service.running_tasks[run_id] = task


@app.post("/stream_run")
async def http_stream_run(request: Request):
    started = time.perf_counter()
    ctx = new_context(method="stream_run", headers=dict(request.headers))
    # 优先使用上游指定的 run_id，保证 cancel 能精确匹配
    upstream_run_id = request.headers.get(HEADER_X_RUN_ID)
    if upstream_run_id:
        ctx.run_id = upstream_run_id
    request_context.set(ctx)
    raw_body = await request.body()
    payload: Dict[str, Any] = {}
    try:
        body_text = raw_body.decode("utf-8")
    except Exception as e:
        body_text = str(raw_body)
        _log_api_call_event(
            run_id=ctx.run_id,
            route="/stream_run",
            status="bad_json",
            http_status_code=400,
            latency_ms=int((time.perf_counter() - started) * 1000),
            response_json={"error_message": f"Invalid JSON format: {e}"},
        )
        raise HTTPException(status_code=400,
                            detail=f"Invalid JSON format: {body_text}, traceback: {extract_core_stack()}, error: {e}")
    run_id = ctx.run_id
    
    try:
        payload = await request.json()
    except json.JSONDecodeError as e:
        logger.error(f"JSON decode error in http_stream_run: {e}, traceback: {traceback.format_exc()}")
        _log_api_call_event(
            run_id=run_id,
            route="/stream_run",
            status="bad_json",
            http_status_code=400,
            latency_ms=int((time.perf_counter() - started) * 1000),
            response_json={"error_message": "Invalid JSON format"},
        )
        raise HTTPException(status_code=400, detail=f"Invalid JSON format:{extract_core_stack()}")
    
    try:
        payload = normalize_request_payload(payload)
        _validate_normalized_payload(payload)
    except ValueError as e:
        logger.error(f"Payload validation error in http_stream_run: {e}")
        _log_api_call_event(
            run_id=run_id,
            route="/stream_run",
            status="bad_request",
            http_status_code=400,
            latency_ms=int((time.perf_counter() - started) * 1000),
            payload=payload,
            response_json={"error_message": str(e)},
            session_id=str(payload.get("session_id", "")).strip() if isinstance(payload, dict) else "",
            user_id=str(payload.get("user_id", "")).strip() if isinstance(payload, dict) else "",
            source_channel=str(payload.get("source_channel", "websdk")).strip() if isinstance(payload, dict) else "websdk",
        )
        raise HTTPException(status_code=400, detail=str(e))
    
    # 提取会话信息并设置到全局上下文
    session_id = payload.get("session_id", "")
    user_id = payload.get("user_id", "")
    source_channel = payload.get("source_channel", "websdk")
    
    if session_id:
        # 设置当前会话ID（供工具使用）
        from utils.session_state import set_current_session_id
        set_current_session_id(session_id)
        logger.info(f"[StreamRun] Set session context: session_id={session_id}, user_id={user_id}")
    
    intent_hint = classify_intent_hint(payload)
    headers = _ensure_context_headers(ctx)
    agent_profile = resolve_profile_id(
        source_channel=source_channel,
        requested_profile=str(payload.get("agent_profile", "")).strip(),
        headers=headers,
    )
    llm_route = _resolve_request_llm_route(payload)
    payload["intent_hint"] = intent_hint
    payload["agent_profile"] = agent_profile
    set_current_agent_profile(agent_profile)
    set_current_llm_route(llm_route)
    headers[HEADER_X_INTENT_HINT] = intent_hint
    headers[PROFILE_HEADER] = agent_profile
    logger.info(
        f"Received request for /stream_run: "
        f"run_id={run_id}, session_id={session_id}, "
        f"source_channel={source_channel}, profile={agent_profile}, intent_hint={intent_hint}, "
        f"llm_model={llm_route.get('model')}, llm_modality={llm_route.get('modality')}, llm_thinking={llm_route.get('thinking_type')}"
    )
    stream_payload = ensure_stream_compatible_payload(payload)
    
    try:
        stream_generator = agent_stream_handler(
            payload=stream_payload,
            ctx=ctx,
            run_id=run_id,
            stream_sse_func=service.explainable_stream_sse,
            sse_event_func=service._sse_event,
            error_classifier=service.error_classifier,
            register_task_func=_register_task,
        )

        response = StreamingResponse(stream_generator, media_type="text/event-stream")
        _log_api_call_event(
            run_id=run_id,
            route="/stream_run",
            status="streaming",
            http_status_code=200,
            latency_ms=int((time.perf_counter() - started) * 1000),
            payload=payload,
            response_json={"message": "stream started"},
            session_id=session_id,
            user_id=user_id,
            source_channel=source_channel,
            intent_hint=intent_hint,
        )
        return response
    except Exception as e:
        error_response = service.error_classifier.get_error_response(e, {"node_name": "http_stream_run", "run_id": run_id})
        stack_trace = extract_core_stack()
        _log_api_call_event(
            run_id=run_id,
            route="/stream_run",
            status="error",
            http_status_code=500,
            latency_ms=int((time.perf_counter() - started) * 1000),
            payload=payload,
            response_json={
                "error_code": error_response["error_code"],
                "error_message": error_response["error_message"],
            },
            session_id=session_id,
            user_id=user_id,
            source_channel=source_channel,
            intent_hint=intent_hint,
        )
        _log_agent_error_event(
            run_id=run_id,
            route="/stream_run",
            error_code=error_response["error_code"],
            error_message=error_response["error_message"],
            stack_trace=stack_trace,
            node_name="http_stream_run",
            error_category=error_response.get("error_category", ""),
        )
        raise HTTPException(
            status_code=500,
            detail={
                "error_code": error_response["error_code"],
                "error_message": error_response["error_message"],
                "stack_trace": stack_trace,
            },
        )

@app.post("/cancel/{run_id}")
async def http_cancel(run_id: str, request: Request):
    """
    取消指定run_id的执行

    使用asyncio.Task.cancel()实现取消,这是Python标准的异步任务取消机制。
    LangGraph会在节点之间的await点检查CancelledError,实现优雅取消。
    """
    ctx = new_context(method="cancel", headers=request.headers)
    request_context.set(ctx)
    logger.info(f"Received cancel request for run_id: {run_id}")
    result = service.cancel_run(run_id, ctx)
    return result


@app.post("/v1/chat/completions")
async def openai_chat_completions(request: Request):
    """OpenAI Chat Completions API 兼容接口"""
    ctx = new_context(method="openai_chat", headers=request.headers)
    request_context.set(ctx)

    logger.info(f"Received request for /v1/chat/completions: run_id={ctx.run_id}")

    try:
        payload = await request.json()
        return await openai_handler.handle(payload, ctx)
    except json.JSONDecodeError as e:
        logger.error(f"JSON decode error in openai_chat_completions: {e}")
        raise HTTPException(status_code=400, detail="Invalid JSON format")
    finally:
        cozeloop.flush()


@app.get("/health")
async def health_check():
    try:
        # 这里可以添加更多的健康检查逻辑
        return {
            "status": "ok",
            "message": "Service is running",
        }
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e))


def parse_args():
    parser = argparse.ArgumentParser(description="Start FastAPI server")
    parser.add_argument("-m", type=str, default="http", help="Run mode, support http, flow, agent")
    parser.add_argument("-p", type=int, default=5000, help="HTTP server port")
    parser.add_argument("-i", type=str, default="", help="Input JSON string for flow/agent mode")
    return parser.parse_args()


def parse_input(input_str: str) -> Dict[str, Any]:
    """Parse input string, support both JSON string and plain text"""
    if not input_str:
        return {"text": "你好"}

    # Try to parse as JSON first
    try:
        return json.loads(input_str)
    except json.JSONDecodeError:
        # If not valid JSON, treat as plain text
        return {"text": input_str}

def start_http_server(port):
    checkpointer_mode = os.getenv("COZE_CHECKPOINTER_MODE", "auto").strip().lower()
    has_pg_url = bool(os.getenv("PGDATABASE_URL", "").strip())
    shared_memory_enabled = checkpointer_mode == "postgres" or (checkpointer_mode == "auto" and has_pg_url)
    # 只有共享持久化记忆可用时，默认提升并发worker；否则默认单worker避免会话串扰。
    if shared_memory_enabled:
        default_workers = max(2, min((os.cpu_count() or 2), 4))
    else:
        default_workers = 1
    workers = int(os.getenv("COZE_HTTP_WORKERS", str(default_workers)))
    reload = False
    if graph_helper.is_dev_env():
        reload = True

    logger.info(f"Start HTTP Server, Port: {port}, Workers: {workers}")
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=reload, workers=workers)

if __name__ == "__main__":
    args = parse_args()
    if args.m == "http":
        start_http_server(args.p)
    elif args.m == "flow":
        payload = parse_input(args.i)
        result = asyncio.run(service.run(payload))
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.m == "agent":
        agent_ctx = new_context(method="agent")
        for chunk in service.stream(
                {
                    "type": "query",
                    "session_id": "1",
                    "message": "你好",
                    "content": {
                        "query": {
                            "prompt": [
                                {
                                    "type": "text",
                                    "content": {"text": "现在几点了？请调用工具获取当前时间"},
                                }
                            ]
                        }
                    },
                },
                run_config={"configurable": {"session_id": "1"}},
                ctx=agent_ctx,
        ):
            print(chunk)
