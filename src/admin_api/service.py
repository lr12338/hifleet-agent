from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
from fastapi import HTTPException, UploadFile

from observability import repository
from observability.schemas import LogListFilters
from llm_config import export_llm_config_view, load_llm_config, resolve_model_selection, save_llm_config

from .schemas import AdminTestRunRequest, ArkAttachment, ArkChatRequest, ChatDebugSessionSaveRequest, DashboardSummaryQuery, LLMConfigRequest, LogListQuery, SessionListQuery


def list_logs(query: LogListQuery) -> dict[str, Any]:
    filters = LogListFilters.model_validate(query.model_dump())
    return repository.query_api_calls(filters)


def get_log_detail(run_id: str) -> dict[str, Any]:
    return repository.query_log_detail(run_id)


def _extract_text_preview(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, dict) and item.get("type") == "text":
                text = item.get("text")
                if isinstance(text, str) and text.strip():
                    parts.append(text.strip())
        return " ".join(parts)
    if isinstance(value, dict):
        if isinstance(value.get("text"), str):
            return value["text"]
        if isinstance(value.get("answer"), str):
            return value["answer"]
    return ""


def _extract_user_preview(request_json: Any) -> str:
    if not isinstance(request_json, dict):
        return ""
    messages = request_json.get("messages")
    if isinstance(messages, list):
        for item in reversed(messages):
            if isinstance(item, dict) and str(item.get("role", "")).lower() == "user":
                text = _extract_text_preview(item.get("content"))
                if text:
                    return text[:80]
    return ""


def _extract_assistant_preview(response_json: Any) -> str:
    if not isinstance(response_json, dict):
        return ""
    messages = response_json.get("messages")
    if isinstance(messages, list):
        for item in reversed(messages):
            if not isinstance(item, dict):
                continue
            if str(item.get("type", "")).lower() in {"ai", "assistant"}:
                text = _extract_text_preview(item.get("content"))
                if text:
                    return text[:120]
    return _extract_text_preview(response_json)[:120]


def list_sessions(query: SessionListQuery) -> dict[str, Any]:
    filters = LogListFilters.model_validate(query.model_dump())
    result = repository.query_session_summaries(filters)
    for item in result["items"]:
        item["title"] = _extract_user_preview(item.get("request_json")) or item.get("session_id")
        item["last_message"] = _extract_assistant_preview(item.get("response_json"))
    return result


def get_session_timeline(session_id: str) -> dict[str, Any]:
    calls = repository.query_session_calls(session_id)
    user_id = calls[0].get("user_id") if calls else None
    source_channel = calls[0].get("source_channel") if calls else None
    agent_profile = calls[0].get("agent_profile") if calls else None
    summary = {
        "turn_count": len(calls),
        "error_count": sum(1 for call in calls if call.get("status") == "error"),
        "avg_latency_ms": round(sum(int(call.get("latency_ms") or 0) for call in calls) / len(calls)) if calls else 0,
        "latest_run_id": calls[-1].get("run_id") if calls else None,
    }
    return {
        "session_id": session_id,
        "user_id": user_id,
        "source_channel": source_channel,
        "agent_profile": agent_profile,
        "summary": summary,
        "calls": calls,
    }


def get_dashboard_summary(query: DashboardSummaryQuery) -> dict[str, Any]:
    filters = LogListFilters.model_validate(
        {
            "start_time": query.start_time,
            "end_time": query.end_time,
            "page": 1,
            "page_size": 20,
        }
    )
    return repository.query_dashboard_summary(filters)



def get_llm_config() -> dict[str, Any]:
    return export_llm_config_view(load_llm_config())


def update_llm_config(req: LLMConfigRequest) -> dict[str, Any]:
    cfg = load_llm_config()
    cfg['config']['text_model'] = req.text_model.strip()
    cfg['config']['multimodal_model'] = req.multimodal_model.strip()
    cfg['config']['thinking_type'] = req.thinking_type
    cfg['config']['deep_thinking_enabled'] = req.thinking_type != 'disabled'
    cfg['config']['model'] = cfg['config']['text_model']
    normalized = save_llm_config(cfg)
    return export_llm_config_view(normalized)


def list_chat_debug_sessions(limit: int = 20) -> dict[str, Any]:
    items = repository.query_chat_debug_sessions(limit=limit)
    return {"items": items}


def save_chat_debug_session(req: ChatDebugSessionSaveRequest) -> dict[str, Any]:
    repository.upsert_chat_debug_session(
        session_key=req.session_key,
        title=req.title,
        status=req.status,
        meta_session_id=req.meta_session_id,
        user_id=req.user_id,
        source_channel=req.source_channel,
        model=req.model,
        payload=req.payload,
    )
    return {"ok": True, "session_key": req.session_key}


def remove_chat_debug_session(session_key: str) -> dict[str, Any]:
    repository.delete_chat_debug_session(session_key)
    return {"ok": True, "session_key": session_key}


def _default_target_agent_url() -> str:
    configured = os.getenv("AGENT_BASE_URL", "").strip()
    if configured:
        return configured.rstrip("/")
    return "http://127.0.0.1:10123"


async def proxy_test_run(req: AdminTestRunRequest) -> dict[str, Any]:
    base_url = (req.target_agent_url or _default_target_agent_url()).rstrip("/")
    url = f"{base_url}{req.endpoint}"
    timeout = httpx.Timeout(req.timeout_s)
    headers = {"x-run-id": req.run_id} if req.run_id else None
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(url, json=req.payload, headers=headers)
    return {
        "target_url": url,
        "status_code": response.status_code,
        "headers": dict(response.headers),
        "body": _try_json(response),
    }


def _try_json(response: httpx.Response) -> Any:
    try:
        return response.json()
    except Exception:
        return {"raw_text": response.text}


async def stream_test_run(req: AdminTestRunRequest):
    base_url = (req.target_agent_url or _default_target_agent_url()).rstrip("/")
    url = f"{base_url}{req.endpoint}"
    timeout = httpx.Timeout(req.timeout_s)

    client = httpx.AsyncClient(timeout=timeout)
    headers = {"x-run-id": req.run_id} if req.run_id else None
    request = client.build_request("POST", url, json=req.payload, headers=headers)
    response = await client.send(request, stream=True)

    async def _iterator():
        try:
            async for chunk in response.aiter_raw():
                if chunk:
                    yield chunk
        finally:
            await response.aclose()
            await client.aclose()

    return response, _iterator()


def _build_oss_public_url(bucket_name: str, object_key: str, endpoint: str) -> str:
    normalized = (endpoint or "").strip().rstrip("/")
    if normalized.startswith("http://"):
        host = normalized[len("http://") :]
        scheme = "http"
    elif normalized.startswith("https://"):
        host = normalized[len("https://") :]
        scheme = "https"
    else:
        host = normalized
        scheme = "https"
    if not host:
        raise HTTPException(status_code=500, detail="OSS_ENDPOINT is not configured")
    return f"{scheme}://{bucket_name}.{host}/{object_key}"


def _sanitize_filename(filename: str) -> str:
    base = Path(filename or "file").name
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "_", base).strip("._")
    return cleaned or "file"


def _build_object_key(filename: str) -> str:
    now = datetime.utcnow()
    date_part = now.strftime("%Y/%m/%d")
    safe_name = _sanitize_filename(filename)
    return f"admin_uploads/{date_part}/{uuid.uuid4().hex}_{safe_name}"


async def upload_admin_file(file: UploadFile) -> dict[str, Any]:
    if not file.filename:
        raise HTTPException(status_code=400, detail="Missing file name")

    max_mb = int(os.getenv("ADMIN_UPLOAD_MAX_MB", "100"))
    raw_content = await file.read()
    if not raw_content:
        raise HTTPException(status_code=400, detail="Empty file")
    if len(raw_content) > max_mb * 1024 * 1024:
        raise HTTPException(status_code=400, detail=f"File too large, max {max_mb}MB")

    bucket_name = os.getenv("OSS_BUCKET_NAME", "").strip()
    endpoint = os.getenv("OSS_ENDPOINT", "").strip()
    if not bucket_name:
        raise HTTPException(status_code=500, detail="OSS_BUCKET_NAME is not configured")
    if not endpoint:
        raise HTTPException(status_code=500, detail="OSS_ENDPOINT is not configured")

    for env_key in ("OSS_ACCESS_KEY_ID", "OSS_ACCESS_KEY_SECRET"):
        if not os.getenv(env_key, "").strip():
            raise HTTPException(status_code=500, detail=f"{env_key} is not configured")

    try:
        import alibabacloud_oss_v2 as oss
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"OSS SDK not installed: {exc}") from exc

    object_key = _build_object_key(file.filename)
    content_type = file.content_type or "application/octet-stream"

    try:
        credentials_provider = oss.credentials.EnvironmentVariableCredentialsProvider()
        cfg = oss.config.load_default()
        cfg.credentials_provider = credentials_provider
        cfg.region = os.getenv("OSS_REGION", "cn-beijing")
        cfg.endpoint = endpoint
        client = oss.Client(cfg)

        put_req = oss.PutObjectRequest(
            bucket=bucket_name,
            key=object_key,
            body=raw_content,
            content_type=content_type,
        )
        result = client.put_object(put_req)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"OSS upload failed: {exc}") from exc

    return {
        "bucket": bucket_name,
        "key": object_key,
        "url": _build_oss_public_url(bucket_name, object_key, endpoint),
        "content_type": content_type,
        "size": len(raw_content),
        "etag": getattr(result, "etag", None),
    }


def _sse_event(event_name: str, data: dict[str, Any]) -> bytes:
    payload = json.dumps(data, ensure_ascii=False)
    return f"event: {event_name}\ndata: {payload}\n\n".encode("utf-8")


def _extract_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = [part for part in (_extract_text(item) for item in value) if part]
        return "\n".join(parts)
    if isinstance(value, dict):
        for key in (
            "text",
            "delta",
            "content",
            "output_text",
            "reasoning",
            "summary",
            "arguments",
            "result",
        ):
            if key in value:
                text = _extract_text(value.get(key))
                if text:
                    return text
        if "content" in value and isinstance(value["content"], list):
            return _extract_text(value["content"])
    return ""


def _thinking_payload(thinking_type: str) -> dict[str, str]:
    return {"type": str(thinking_type or "disabled").strip() or "disabled"}


def _normalize_ark_content(attachments: list[ArkAttachment], text: str | None) -> list[dict[str, Any]]:
    content: list[dict[str, Any]] = []
    for item in attachments:
        if item.type == "image":
            content.append({"type": "input_image", "image_url": item.url})
        elif item.type == "audio":
            content.append({"type": "input_audio", "audio_url": item.url})
        elif item.type == "video":
            content.append({"type": "input_video", "video_url": item.url})
    if text and text.strip():
        content.append({"type": "input_text", "text": text.strip()})
    return content


def _classify_ark_event(event_name: str, payload: dict[str, Any]) -> tuple[str | None, dict[str, Any] | None]:
    name = (event_name or payload.get("type") or "").strip()
    lowered = name.lower()
    text = _extract_text(payload)

    if lowered in {"response.created", "response.in_progress", "message_start"}:
        return "message_start", {"event": name or "message_start"}
    if "reasoning" in lowered or "thinking" in lowered:
        return "thinking", {"text": text or _extract_text(payload.get("delta"))}
    if "function_call_arguments" in lowered or "tool_request" in lowered or "function_call" in lowered:
        tool_name = (
            payload.get("name")
            or payload.get("tool_name")
            or payload.get("function_name")
            or (payload.get("item") or {}).get("name")
        )
        args = payload.get("arguments") or payload.get("tool_args") or (payload.get("item") or {}).get("arguments")
        return "tool_request", {"tool_name": tool_name, "arguments": args, "raw": payload}
    if "tool_response" in lowered or "function_call_output" in lowered or "tool_result" in lowered:
        tool_name = payload.get("tool_name") or payload.get("name") or (payload.get("item") or {}).get("name")
        result = payload.get("result") or payload.get("output") or (payload.get("item") or {}).get("output")
        return "tool_response", {"tool_name": tool_name, "result": result, "raw": payload}
    if "output_text" in lowered or "answer" in lowered or "message.delta" in lowered or "content_part" in lowered:
        if text:
            return "answer", {"text": text}
    if lowered in {"response.completed", "message_end", "done"} or lowered.endswith("completed"):
        return "message_end", {"event": name or "message_end", "raw": payload}
    if text:
        return "answer", {"text": text}
    return None, None


async def stream_ark_chat(req: ArkChatRequest):
    api_key = os.getenv("ARK_API_KEY", "").strip() or os.getenv("COZE_WORKLOAD_IDENTITY_API_KEY", "").strip()
    if not api_key:
        raise HTTPException(status_code=500, detail="ARK_API_KEY is not configured")

    ark_url = os.getenv("ARK_RESPONSES_URL", "https://ark.cn-beijing.volces.com/api/v3/responses").strip()
    input_content = _normalize_ark_content(req.attachments, req.text)
    if not input_content:
        raise HTTPException(status_code=400, detail="text or attachments is required")

    resolved = resolve_model_selection(
        load_llm_config(),
        has_multimodal_input=bool(req.attachments),
        requested_model=str(req.model or "").strip(),
        requested_thinking=str(req.thinking or "").strip(),
    )
    request_body = {
        "model": resolved["model"],
        "thinking": _thinking_payload(resolved["thinking_type"]),
        "stream": True,
        "input": [
            {
                "role": "user",
                "content": input_content,
            }
        ],
    }

    timeout = httpx.Timeout(300.0, connect=30.0)
    client = httpx.AsyncClient(timeout=timeout)
    response = await client.send(
        client.build_request(
            "POST",
            ark_url,
            json=request_body,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        ),
        stream=True,
    )

    if response.status_code >= 400:
        body = await response.aread()
        await response.aclose()
        await client.aclose()
        raise HTTPException(status_code=response.status_code, detail=body.decode("utf-8", errors="ignore") or "Ark request failed")

    async def _iterator():
        try:
            yield _sse_event(
                "message_start",
                {
                    "session_id": req.session_id,
                    "user_id": req.user_id,
                    "source_channel": req.source_channel,
                    "model": resolved["model"],
                    "thinking": resolved["thinking_type"],
                    "modality": resolved["modality"],
                },
            )
            current_event = "message"
            data_lines: list[str] = []

            async for raw_line in response.aiter_lines():
                line = raw_line.strip()
                if not line:
                    if data_lines:
                        raw_payload = "\n".join(data_lines)
                        if raw_payload != "[DONE]":
                            try:
                                payload = json.loads(raw_payload)
                            except Exception:
                                payload = {"raw": raw_payload}
                            normalized_name, normalized_payload = _classify_ark_event(current_event, payload)
                            if normalized_name and normalized_payload:
                                yield _sse_event(normalized_name, normalized_payload)
                        data_lines = []
                        current_event = "message"
                    continue

                if line.startswith("event:"):
                    current_event = line[len("event:") :].strip() or "message"
                    continue
                if line.startswith("data:"):
                    data_lines.append(line[len("data:") :].strip())

            if data_lines:
                raw_payload = "\n".join(data_lines)
                if raw_payload != "[DONE]":
                    try:
                        payload = json.loads(raw_payload)
                    except Exception:
                        payload = {"raw": raw_payload}
                    normalized_name, normalized_payload = _classify_ark_event(current_event, payload)
                    if normalized_name and normalized_payload:
                        yield _sse_event(normalized_name, normalized_payload)

            yield _sse_event("message_end", {"event": "message_end"})
        finally:
            await response.aclose()
            await client.aclose()

    return _iterator(), request_body, resolved
