from __future__ import annotations

import asyncio
import json
import os
import re
import time
from dataclasses import dataclass
from typing import Any, AsyncIterator, Iterator

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from openai import OpenAI

from llm_gateway import build_chat_model, resolve_role_base_url, safe_default_headers

from agents.customer_ceshi_v2.contracts import InspectMediaRequest, MediaAsset, Observation, ToolCall
from agents.customer_ceshi_v2.perception import PerceptionService
from agents.customer_ceshi_v2.models import MultimodalPerceptionClient
from agents.customer_ceshi_v2.tools import CapabilityRegistry, DENIED_TOOL_NAMES
from agents.customer_ceshi_v2.tracing import safe_trace


CHECKPOINT_NAMESPACE = "customer_ceshi_responses"
DEFAULT_MAX_STEPS = 8
_HIGH_RISK = re.compile(r"(谁.*(?:制定|发布|划定)|是否支持|能否.*(?:使用|完成)|统计口径|法律|法规|会员|权限|(?:按钮|字段|错误).*(?:存在|显示)|(?:成功|已完成))")


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
    }


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
    """Small graph-compatible facade that cannot share production checkpoints."""

    def __init__(self, runtime: "NativeToolRuntime") -> None:
        self.runtime = runtime

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
        return self.runtime.invoke(input, self.scoped_config(config))

    async def ainvoke(self, input: dict[str, Any], config: dict[str, Any] | None = None, **_: Any) -> dict[str, Any]:
        return await asyncio.to_thread(self.invoke, input, config)

    def stream(self, input: dict[str, Any], config: dict[str, Any] | None = None, **_: Any) -> Iterator[dict[str, Any]]:
        yield {"customer_ceshi_responses": self.invoke(input, config)}

    async def astream(self, input: dict[str, Any], config: dict[str, Any] | None = None, **_: Any) -> AsyncIterator[dict[str, Any]]:
        yield {"customer_ceshi_responses": await self.ainvoke(input, config)}


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        return "\n".join(str(part.get("text", "")) for part in content if isinstance(part, dict) and part.get("type") in {"text", "input_text"}).strip()
    return str(content or "").strip()


def _latest_human(messages: list[Any]) -> HumanMessage | None:
    for message in reversed(messages or []):
        if isinstance(message, HumanMessage) or getattr(message, "type", "") == "human":
            return message
    return None


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
    return {"type": "function", "function": {"name": tool.name, "description": str(getattr(tool, "description", ""))[:1200], "parameters": parameters}}


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


class NativeToolRuntime:
    def __init__(self, *, client: Any, registry: CapabilityRegistry, perception: PerceptionService, config: dict[str, Any], mode: str, responses_client: Any | None = None) -> None:
        self.client = client
        self.registry = registry
        self.perception = perception
        self.config = config
        self.mode = mode
        self.responses_client = responses_client
        self._response_ids: dict[str, str] = {}
        self.max_steps = int(config.get("customer_ceshi_max_steps", config.get("customer_ceshi_v2_max_steps", DEFAULT_MAX_STEPS)))
        self.max_tool_calls = int(config.get("customer_ceshi_max_tool_calls", config.get("customer_ceshi_v2_max_tool_calls", 8)))
        self.max_media_calls = int(config.get("customer_ceshi_max_media_calls", config.get("customer_ceshi_v2_max_media_calls", 4)))

    def _bound_client(self) -> Any:
        tools = [_tool_schema(tool) for tool in self.registry._tools.values()] + [_media_schema()]
        return self.client.bind_tools(tools)

    def _responses_tools(self) -> list[dict[str, Any]]:
        return [_tool_schema(tool) for tool in self.registry._tools.values()] + [_media_schema()]

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
        if value:
            return str(value).strip()
        output = getattr(response, "output", None) or (response.get("output", []) if isinstance(response, dict) else [])
        text: list[str] = []
        for item in output or []:
            content = getattr(item, "content", None) or (item.get("content", []) if isinstance(item, dict) else [])
            for part in content or []:
                value = getattr(part, "text", None) or (part.get("text") if isinstance(part, dict) else "")
                if value:
                    text.append(str(value))
        return "\n".join(text).strip()

    def _invoke_responses(self, *, human: HumanMessage | None, assets: list[MediaAsset], session_key: str) -> tuple[str, list[dict[str, Any]], list[str], int, int, str, str]:
        if self.responses_client is None:
            raise RuntimeError("responses_client_unavailable")
        user_text = _text(getattr(human, "content", ""))
        request: dict[str, Any] = {
            "model": str(getattr(self.client, "model_name", "") or getattr(self.client, "model", "") or self.config.get("customer_ceshi_v2_text_model") or self.config.get("text_model")),
            "input": [{"role": "system", "content": self._system(assets).content}, {"role": "user", "content": user_text}],
            "tools": self._responses_tools(),
        }
        previous_response_id = self._response_ids.get(session_key)
        if previous_response_id:
            request["previous_response_id"] = previous_response_id
            request["input"] = [{"role": "user", "content": user_text}]
        response = self.responses_client.responses.create(**request)
        model_calls = tool_calls = media_calls = 0
        observations: list[dict[str, Any]] = []
        names: list[str] = []
        response_id = str(getattr(response, "id", "") or (response.get("id", "") if isinstance(response, dict) else ""))
        if response_id:
            self._response_ids[session_key] = response_id
        for _ in range(self.max_steps):
            model_calls += 1
            calls = self._responses_calls(response)
            if not calls:
                return self._responses_text(response), observations, names, model_calls, tool_calls, response_id, "stop"
            outputs: list[dict[str, Any]] = []
            for call in calls:
                if tool_calls >= self.max_tool_calls:
                    return "", observations, names, model_calls, tool_calls, response_id, "tool_budget"
                name, arguments = call["name"], call["args"]
                if name in DENIED_TOOL_NAMES:
                    observation = Observation(status="forbidden", capability=name, warnings=["write_tools_disabled"], retry_allowed=False)
                elif name == "inspect_media" and media_calls >= self.max_media_calls:
                    observation = Observation(status="forbidden", capability=name, warnings=["media_budget_exhausted"], retry_allowed=False)
                else:
                    observation = self._execute(name, arguments, {asset.asset_id: asset for asset in assets})
                if name == "inspect_media":
                    media_calls += 1
                tool_calls += 1
                names.append(name)
                observed = observation.model_dump()
                observations.append(observed)
                outputs.append({"type": "function_call_output", "call_id": call["id"], "output": json.dumps(observed, ensure_ascii=False)})
            request = {"model": request["model"], "input": outputs, "tools": self._responses_tools()}
            if response_id:
                request["previous_response_id"] = response_id
            response = self.responses_client.responses.create(**request)
            response_id = str(getattr(response, "id", "") or (response.get("id", "") if isinstance(response, dict) else ""))
            if response_id:
                self._response_ids[session_key] = response_id
        return "", observations, names, model_calls, tool_calls, response_id, "max_steps"

    def _system(self, assets: list[MediaAsset]) -> SystemMessage:
        media = ", ".join(f"{asset.asset_id}:{asset.kind}" for asset in assets) or "none"
        return SystemMessage(content=(
            "You are the sole customer_ceshi orchestrator. Decide whether to call native tools, observe their returned facts, and continue until you can answer. "
            "Never emit a custom action JSON protocol. Read-only tools only. inspect_media delegates perception to Doubao and is not a second decision agent. "
            "Do not claim a high-risk product capability, policy, UI element, media detail, or operation success without an observation that supports it. "
            f"Current attached assets: {media}."
        ))

    def _execute(self, name: str, arguments: dict[str, Any], assets: dict[str, MediaAsset]) -> Observation:
        if name == "inspect_media":
            asset = assets.get(str(arguments.get("asset_id") or ""))
            if asset is None:
                return Observation(status="invalid_input", capability=name, warnings=["unknown_asset_id"], retry_allowed=True)
            request = InspectMediaRequest.model_validate({**arguments, "asset_id": asset.asset_id})
            observations, _ = self.perception.inspect([request], [asset], max_calls=1)
            if not observations:
                return Observation(status="temporary_error", capability=name, warnings=["empty_perception_result"], retry_allowed=True)
            observation = observations[0]
            packet = dict((observation.data or {}).get("perception_packet") or {})
            if packet.get("model") == "local_visual_fallback":
                return Observation(
                    status="not_found",
                    capability=name,
                    data={"asset_id": asset.asset_id},
                    warnings=["local_business_inference_disabled"],
                    retry_allowed=True,
                    suggested_fix="请重新上传清晰附件，以便由多模态感知模型核验。",
                )
            return observation
        return self.registry.invoke(ToolCall(name=name, arguments=arguments))

    @staticmethod
    def _guard(answer: str, observations: list[dict[str, Any]]) -> tuple[str, str]:
        if not _HIGH_RISK.search(answer):
            return answer, "not_required"
        evidence = " ".join(" ".join(item.get("facts", [])) for item in observations if item.get("status") in {"success", "partial"})
        if evidence:
            return answer, "supported"
        return "我暂时没有获得足以确认该高风险结论的证据；请提供更清晰的附件、具体页面信息，或允许我继续核验。", "blocked_no_evidence"

    def invoke(self, payload: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        started = time.monotonic()
        messages = list(payload.get("messages") or [])
        human = _latest_human(messages)
        assets = _assets(human)
        model_messages: list[Any] = [self._system(assets), *messages]
        asset_map = {asset.asset_id: asset for asset in assets}
        observations: list[dict[str, Any]] = []
        tool_names: list[str] = []
        model_calls = tool_calls = media_calls = 0
        answer = ""
        finish_reason = "max_steps"
        try:
            session_key = str((config.get("configurable") or {}).get("thread_id") or payload.get("session_id") or "default")
            if self.mode == "responses":
                try:
                    answer, observations, tool_names, model_calls, tool_calls, provider_response_id, finish_reason = self._invoke_responses(human=human, assets=assets, session_key=session_key)
                    media_calls = sum(1 for name in tool_names if name == "inspect_media")
                    if answer:
                        answer, guard_result = self._guard(answer, observations)
                        return self._result(answer, observations, tool_names, model_calls, tool_calls, media_calls, finish_reason, guard_result, started, provider_response_id)
                except Exception as exc:
                    if not bool(self.config.get("customer_ceshi_chat_fallback_enabled", True)):
                        raise
                    finish_reason = f"responses_fallback:{type(exc).__name__}"
            model = self._bound_client()
            for _ in range(self.max_steps):
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
                    elif name == "inspect_media" and media_calls >= self.max_media_calls:
                        observation = Observation(status="forbidden", capability=name, warnings=["media_budget_exhausted"], retry_allowed=False)
                    else:
                        observation = self._execute(name, arguments, asset_map)
                    if name == "inspect_media":
                        media_calls += 1
                    tool_calls += 1
                    tool_names.append(name)
                    observed = observation.model_dump()
                    observations.append(observed)
                    model_messages.append(ToolMessage(content=json.dumps(observed, ensure_ascii=False), tool_call_id=str(call.get("id") or name)))
                if finish_reason == "tool_budget":
                    break
            if not answer:
                answer = "我暂时无法在当前工具预算内获得足够证据，请补充更具体的信息或稍后重试。"
        except Exception as exc:
            answer = "实验客服链遇到暂时性错误，未切换到生产客服链；请稍后重试或补充必要信息。"
            finish_reason = f"error:{type(exc).__name__}"
        answer, guard_result = self._guard(answer, observations)
        return self._result(answer, observations, tool_names, model_calls, tool_calls, media_calls, finish_reason, guard_result, started, "")

    def _result(self, answer: str, observations: list[dict[str, Any]], tool_names: list[str], model_calls: int, tool_calls: int, media_calls: int, finish_reason: str, guard_result: str, started: float, provider_response_id: str) -> dict[str, Any]:
        metrics = {
            "runtime_mode": self.mode,
            "orchestrator_model": str(getattr(self.client, "model_name", "") or getattr(self.client, "model", "")),
            "perception_model": str(getattr(getattr(self.perception, "client", None), "model", "")),
            "model_calls": model_calls,
            "tool_calls": tool_calls,
            "media_calls": media_calls,
            "cache_hits": 0,
            "latency_ms": int((time.monotonic() - started) * 1000),
            "finish_reason": finish_reason,
            "guard_result": guard_result,
        }
        trace = safe_trace({"agent": "customer_ceshi_responses", "checkpoint_namespace": CHECKPOINT_NAMESPACE, "runtime_mode": self.mode, "provider_response_id": provider_response_id[-12:] if provider_response_id else "", "tool_calls": tool_names, "observations": observations, "metrics": metrics})
        return {"phase": "done", "status": "success" if not finish_reason.startswith("error:") else "degraded", "generated_answer": answer, "messages": [AIMessage(content=answer)], "generated_tool_calls": tool_names, "observations": observations, "metrics": metrics, "route_trace": trace}


def build_customer_ceshi_responses_agent(ctx: Any, cfg: dict[str, Any], workspace_path: str, profile: Any, intent_hint: str = "") -> _NamespacedRuntime:
    config = dict(cfg.get("config") or cfg or {})
    client = getattr(ctx, "customer_ceshi_responses_client", None) if ctx is not None else None
    client = client or build_chat_model(ctx, cfg, role="text", streaming=True, model_override=str(config.get("customer_ceshi_responses_text_model") or config.get("customer_ceshi_v2_text_model") or config.get("text_model") or ""), timeout=config.get("customer_ceshi_responses_timeout_seconds", config.get("customer_ceshi_v2_timeout_seconds", 30)), allow_runtime_model_override=False)
    registry = getattr(ctx, "customer_ceshi_responses_tool_registry", None) if ctx is not None else None
    registry = registry or CapabilityRegistry(skill_names=list(getattr(profile, "skills", []) or []))
    perception = getattr(ctx, "customer_ceshi_responses_perception_service", None) if ctx is not None else None
    if perception is None:
        perception_client = getattr(ctx, "customer_ceshi_responses_perception_client", None) if ctx is not None else None
        perception = PerceptionService(perception_client or MultimodalPerceptionClient(config, ctx=ctx))
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
    return _NamespacedRuntime(NativeToolRuntime(client=client, registry=registry, perception=perception, config=config, mode=selected_mode, responses_client=responses_client))
