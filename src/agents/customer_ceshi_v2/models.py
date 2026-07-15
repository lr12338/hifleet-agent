from __future__ import annotations

import json
import logging
import threading
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from llm_config import DEFAULT_MULTIMODAL_MODEL, DEFAULT_TEXT_MODEL, build_thinking_payload
from llm_gateway import build_chat_model, resolve_runtime_llm_settings

from .capabilities import ModelCapabilities, probe_text_capabilities
from .contracts import AgentDecision, EvidenceReview, InspectMediaRequest, MediaAsset, Observation, PerceptionPacket
from .media_input import ImageInputPreparer, MediaPreparationError

logger = logging.getLogger(__name__)
_MEDIA_GATEWAY_SLOTS = threading.BoundedSemaphore(value=8)


class ModelGatewayError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool, model_calls: int = 0):
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.model_calls = model_calls


def _classify_gateway_error(exc: Exception) -> ModelGatewayError:
    text = str(exc).lower()
    if any(token in text for token in ("error while downloading", "download image", "image download", "failed to download", "invalid image url")):
        return ModelGatewayError("media_download_failed", "模型服务无法读取图片。", retryable=True)
    if any(token in text for token in ("401", "403", "authentication", "unauthorized", "forbidden")):
        return ModelGatewayError("model_auth_failed", "模型服务认证失败。", retryable=False)
    if any(token in text for token in ("timeout", "timed out", "readtimeout", "connecttimeout")):
        return ModelGatewayError("model_timeout", "模型服务响应超时。", retryable=True)
    return ModelGatewayError("model_unavailable", "模型服务暂时不可用。", retryable=True)


def _decision_thinking(*, assets: list[dict[str, Any]], observations: list[dict[str, Any]], step_count: int) -> dict[str, str]:
    if assets or len(observations) > 2 or step_count > 1:
        return build_thinking_payload("enabled", "medium")
    return build_thinking_payload("disabled", "low")


def _response_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
                continue
            if not isinstance(item, dict):
                continue
            text = item.get("text") or item.get("content")
            if isinstance(text, str):
                parts.append(text)
        if parts:
            return "\n".join(parts)
    if isinstance(content, dict):
        text = content.get("text") or content.get("content")
        if isinstance(text, str):
            return text
    return json.dumps(content, ensure_ascii=False)


def _json_object(content: Any) -> dict[str, Any]:
    text = _response_text(content)
    candidate = text.strip()
    if candidate.startswith("```"):
        candidate = candidate.split("\n", 1)[1] if "\n" in candidate else ""
        if candidate.rstrip().endswith("```"):
            candidate = candidate.rstrip()[:-3].rstrip()
    start = candidate.find("{")
    if start < 0:
        raise ValueError("model response does not contain a JSON object")
    parsed, _ = json.JSONDecoder().raw_decode(candidate[start:])
    if not isinstance(parsed, dict):
        raise ValueError("model response JSON root must be an object")
    return parsed


def _normalize_decision_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Accept harmless provider serialization differences before validation."""
    normalized = dict(payload)
    for string_key in ("answer_draft", "question", "perception_goal"):
        if normalized.get(string_key) is None:
            normalized[string_key] = ""
    for list_key in ("tool_calls", "asset_ids", "media_requests", "claims"):
        if normalized.get(list_key) is None:
            normalized[list_key] = []
    if normalized.get("write_proposal") in (None, "", [], {}):
        normalized["write_proposal"] = None

    media_requests = normalized.get("media_requests")
    if not isinstance(media_requests, list):
        return normalized

    normalized_requests: list[Any] = []
    for request in media_requests:
        if not isinstance(request, dict):
            normalized_requests.append(request)
            continue
        item = dict(request)
        if not item.get("objective"):
            item["objective"] = normalized.get("perception_goal") or "Inspect the attachment for relevant observed facts."
        for optional_key in ("region", "time_range"):
            if item.get(optional_key) in (None, "", []):
                item[optional_key] = None
        for list_key in ("questions", "expected_fields"):
            if item.get(list_key) is None:
                item[list_key] = []
        normalized_requests.append(item)
    normalized["media_requests"] = normalized_requests
    return normalized


class TextReasoningClient:
    """DeepSeek gateway. Provider-specific reasoning fields never leave this boundary."""

    role = "deepseek_orchestrator"

    def __init__(self, config: dict[str, Any], ctx: Any | None = None, client: Any | None = None):
        self.config = dict(config)
        self.ctx = ctx
        settings = resolve_runtime_llm_settings(config, role="json", allow_runtime_model_override=False)
        self.model = settings["model"] or DEFAULT_TEXT_MODEL
        self.model_role = "json"
        self.model_override_ignored = bool(settings["requested_model"] and not settings["model_override_applied"])
        self.last_decision_model_calls = 0
        self._client = client or self._build_client(config, ctx)
        logger.info(
            "[CustomerCeshiV2Gateway] role=%s model=%s external_model_ignored=%s",
            self.model_role,
            self.model,
            self.model_override_ignored,
        )

    @staticmethod
    def _build_client(config: dict[str, Any], ctx: Any | None = None) -> ChatOpenAI | None:
        timeout = float(config.get("customer_ceshi_v2_timeout_seconds", config.get("timeout", 30)))
        return build_chat_model(
            ctx,
            config,
            role="json",
            temperature=0.1,
            timeout=timeout,
            allow_runtime_model_override=False,
        )

    def _invoke(self, messages: list[Any], *, thinking: dict[str, str], operation: str) -> Any:
        if self._client is None:
            raise ModelGatewayError("model_gateway_not_configured", "模型网关未配置。", retryable=False)
        try:
            if operation in {"decision", "decision_format_repair"}:
                self.last_decision_model_calls += 1
            return self._client.invoke(messages, extra_body={"thinking": thinking})
        except Exception as exc:
            error = _classify_gateway_error(exc)
            logger.warning(
                "[CustomerCeshiV2Gateway] operation=%s role=%s code=%s retryable=%s model=%s error=%s",
                operation,
                self.model_role,
                error.code,
                error.retryable,
                self.model,
                str(exc)[:240],
            )
            error.model_calls = self.last_decision_model_calls
            raise error from exc

    def decide(self, *, task_goal: str, observations: list[dict[str, Any]], assets: list[dict[str, Any]], descriptors: list[dict[str, Any]], step_count: int) -> AgentDecision:
        self.last_decision_model_calls = 0
        safe_assets = [
            {
                "asset_id": item.get("asset_id", ""),
                "kind": item.get("kind", ""),
                "filename": item.get("filename", ""),
                "metadata": item.get("metadata", {}),
            }
            for item in assets
        ]
        payload = {"task_goal": task_goal, "observations": observations, "media_assets": safe_assets, "tools": descriptors, "step_count": step_count, "protocol": "Return JSON only: action, tool_calls, media_requests, question, write_proposal, answer_draft, claims. Actions: call_tools, ask_user, propose_write, finish. Put attachment inspections in media_requests while action is call_tools."}
        messages = [SystemMessage(content="You are a customer-support orchestrator. Select only supported capabilities. Never claim unsupported facts."), HumanMessage(content=json.dumps(payload, ensure_ascii=False))]
        try:
            response = self._invoke(messages, thinking=_decision_thinking(assets=assets, observations=observations, step_count=step_count), operation="decision")
            return AgentDecision.model_validate(_normalize_decision_payload(_json_object(response.content)))
        except ModelGatewayError:
            raise
        except Exception as exc:
            try:
                repair = self._invoke(messages + [HumanMessage(content="Your previous response was invalid. Return only valid AgentDecision JSON matching the stated protocol.")], thinking=build_thinking_payload("disabled", "low"), operation="decision_format_repair")
                return AgentDecision.model_validate(_normalize_decision_payload(_json_object(repair.content)))
            except ModelGatewayError:
                raise
            except Exception as repair_exc:
                logger.warning(
                    "[CustomerCeshiV2Gateway] operation=decision validation=%s model=%s role=%s attempts=%s repaired=true",
                    str(repair_exc)[:240],
                    self.model,
                    self.model_role,
                    self.last_decision_model_calls,
                )
                raise ModelGatewayError(
                    "model_invalid_response",
                    "模型返回格式异常。",
                    retryable=True,
                    model_calls=self.last_decision_model_calls,
                ) from exc

    def probe_capabilities(self) -> ModelCapabilities:
        return probe_text_capabilities(self._client)

    def review_evidence(self, *, goal: str, answer: str, claims: list[str], observations: list[dict[str, Any]]) -> EvidenceReview:
        payload = {"goal": goal, "candidate_answer": answer, "claims": claims, "observations": observations, "protocol": "Return JSON only: ready, supported_claims, unsupported_claims, missing_required_facts, recommended_action, repaired_answer. A source supports only facts it explicitly states."}
        response = self._invoke([SystemMessage(content="Review customer-support claims against supplied evidence. Do not infer unsupported product capabilities, authorship, policy, or success."), HumanMessage(content=json.dumps(payload, ensure_ascii=False))], thinking=build_thinking_payload("enabled", "medium"), operation="evidence_review")
        return EvidenceReview.model_validate(_json_object(response.content))


class MultimodalPerceptionClient:
    role = "doubao_perception"

    def __init__(self, config: dict[str, Any], ctx: Any | None = None, client: Any | None = None, image_preparer: ImageInputPreparer | None = None):
        self.config = dict(config)
        self.ctx = ctx
        settings = resolve_runtime_llm_settings(config, role="multimodal")
        self.model = settings["model"] or DEFAULT_MULTIMODAL_MODEL
        self.model_role = "multimodal"
        self.model_override_ignored = bool(settings["requested_model"] and not settings["model_override_applied"])
        self.image_preparer = image_preparer or ImageInputPreparer(config)
        self.last_media_model_calls = 0
        timeout = float(config.get("customer_ceshi_v2_media_timeout_seconds", 12))
        self.hard_timeout_seconds = float(config.get("customer_ceshi_v2_media_hard_timeout_seconds", 15))
        self._client = client or build_chat_model(ctx, config, role="multimodal", temperature=0.1, timeout=timeout)
        logger.info(
            "[CustomerCeshiV2Gateway] role=%s model=%s external_model_ignored=%s",
            self.model_role,
            self.model,
            self.model_override_ignored,
        )

    def inspect_packet(self, asset: MediaAsset, request: InspectMediaRequest) -> PerceptionPacket | Observation:
        if self._client is None:
            return Observation(status="temporary_error", capability="inspect_media", warnings=["Multimodal perception service is unavailable."], retry_allowed=False)
        detail = bool((asset.metadata or {}).get("perception_detail"))
        content: list[dict[str, Any]] = [{"type": "text", "text": "Return JSON only matching PerceptionPacket. Extract concrete visual features before any interpretation: colors, shapes, center marks, line styles, labels, and relative positions. For chart symbols, fill suspected_symbol only when the image supports it; otherwise leave it empty and state uncertainty. Include only observed media facts; mark uncertainty explicitly. " + json.dumps(request.model_dump(), ensure_ascii=False)}]
        if asset.kind == "image":
            try:
                prepared = self.image_preparer.prepare(asset.url, detail=detail)
            except MediaPreparationError as exc:
                return Observation(
                    status="temporary_error" if exc.retryable else "invalid_input",
                    capability="inspect_media",
                    data={"asset_id": asset.asset_id, "media_diagnostics": {"media_delivery": "none", "media_perception_variant": "detail" if detail else "primary"}},
                    warnings=[exc.code],
                    retry_allowed=exc.retryable,
                    suggested_fix="请稍后重试或重新上传清晰图片。",
                )
            asset.metadata = {**dict(asset.metadata or {}), "media_input": prepared.diagnostics}
            local_hint = dict(prepared.diagnostics.get("media_local_hint") or {})
            if local_hint and not detail:
                return PerceptionPacket(
                    asset_id=asset.asset_id,
                    media_type="image",
                    model="local_visual_fallback",
                    requested_objective=request.objective,
                    requested_questions=request.questions,
                    factual_summary=str(local_hint.get("summary") or ""),
                    visual_features=[str(item) for item in local_hint.get("visual_features", []) if item],
                    suspected_symbol=str(local_hint.get("suspected_symbol") or ""),
                    overall_confidence=str(local_hint.get("confidence") or "low"),
                    limitations=["该结论由本地可见特征初步判断，建议结合平台图例或原始海图复核。"],
                    evidence_refs=[{"type": "local_visual_feature_fallback"}],
                )
            content.append({"type": "image_url", "image_url": {"url": prepared.data_url}})
        elif asset.kind == "audio":
            content.append({"type": "input_audio", "input_audio": {"url": asset.url}})
        elif asset.kind == "video":
            content.append({"type": "video_url", "video_url": {"url": asset.url}})
        try:
            response = self._invoke_with_hard_timeout([HumanMessage(content=content)])
            parsed = _json_object(response.content)
            parsed.setdefault("asset_id", asset.asset_id)
            parsed.setdefault("media_type", asset.kind)
            parsed.setdefault("model", self.model)
            parsed.setdefault("requested_objective", request.objective)
            parsed.setdefault("requested_questions", request.questions)
            return PerceptionPacket.model_validate(parsed)
        except Exception as exc:
            error = _classify_gateway_error(exc)
            logger.warning("[CustomerCeshiV2Gateway] operation=media_perception role=%s code=%s retryable=%s model=%s detail=%s error=%s", self.model_role, error.code, error.retryable, self.model, detail, str(exc)[:240])
            return Observation(status="temporary_error", capability="inspect_media", data={"asset_id": asset.asset_id, "media_diagnostics": dict((asset.metadata or {}).get("media_input") or {})}, warnings=[error.code], retry_allowed=error.retryable, suggested_fix="请稍后重试媒体识别。")

    def _invoke_with_hard_timeout(self, messages: list[Any]) -> Any:
        if not _MEDIA_GATEWAY_SLOTS.acquire(blocking=False):
            raise ModelGatewayError("model_busy", "图片识别服务繁忙。", retryable=True)
        done = threading.Event()
        result: dict[str, Any] = {}

        def run() -> None:
            try:
                result["response"] = self._client.invoke(messages, extra_body={"thinking": build_thinking_payload("disabled", "low")})
            except BaseException as exc:  # Re-raise in the request thread with original classification.
                result["error"] = exc
            finally:
                _MEDIA_GATEWAY_SLOTS.release()
                done.set()

        self.last_media_model_calls += 1
        threading.Thread(target=run, name="customer-ceshi-vision", daemon=True).start()
        if not done.wait(timeout=max(0.1, self.hard_timeout_seconds)):
            logger.warning(
                "[CustomerCeshiV2Gateway] operation=media_perception_hard_timeout role=%s model=%s timeout_seconds=%s",
                self.model_role,
                self.model,
                self.hard_timeout_seconds,
            )
            raise TimeoutError("media perception hard timeout")
        if "error" in result:
            raise result["error"]
        return result["response"]
