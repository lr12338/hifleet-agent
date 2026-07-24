from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import os
import re
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from fastapi import HTTPException, Request, UploadFile

from debug_events.redaction import redact_headers

from observability import repository
from observability.schemas import LogListFilters
from llm_config import build_thinking_payload, export_llm_config_view, load_llm_config, resolve_model_selection, save_llm_config
from storage.s3.s3_storage import S3SyncStorage

from .schemas import AdminTestRunRequest, ArkAttachment, ArkChatRequest, ChatDebugSessionSaveRequest, DashboardSummaryQuery, LLMConfigRequest, LogListQuery, SessionListQuery

logger = logging.getLogger(__name__)


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
    cfg['config']['reasoning_effort'] = req.reasoning_effort
    cfg['config']['deep_thinking_enabled'] = req.thinking_type != 'disabled'
    cfg['config']['model'] = cfg['config']['text_model']
    for key in (
        'text_thinking_type',
        'multimodal_thinking_type',
        'customer_support_json_thinking_type',
        'text_model_base_url_env',
        'multimodal_model_base_url_env',
        'json_model_base_url_env',
    ):
        value = getattr(req, key, None)
        if value is not None:
            cfg['config'][key] = str(value).strip()
    normalized = save_llm_config(cfg)
    return export_llm_config_view(normalized)


def list_chat_debug_sessions(limit: int = 20) -> dict[str, Any]:
    items = repository.query_chat_debug_sessions(limit=limit)
    return {"items": items}


def save_chat_debug_session(req: ChatDebugSessionSaveRequest) -> dict[str, Any]:
    # Persist explicit contract fields inside the payload JSONB so they survive
    # restore and remain queryable without a schema migration. The full session
    # object (including meta.endpoint/response_mode/agent_profile) is the source
    # of truth; these top-level fields make the contract explicit.
    enriched_payload = dict(req.payload) if isinstance(req.payload, dict) else {"value": req.payload}
    contract = {
        "agent_profile": req.agent_profile,
        "endpoint": req.endpoint,
        "response_mode": req.response_mode,
    }
    if isinstance(enriched_payload.get("meta"), dict):
        for key, value in contract.items():
            if value is not None:
                enriched_payload["meta"].setdefault(key, value)
    enriched_payload["_contract"] = {k: v for k, v in contract.items() if v is not None}
    repository.upsert_chat_debug_session(
        session_key=req.session_key,
        title=req.title,
        status=req.status,
        meta_session_id=req.meta_session_id,
        user_id=req.user_id,
        source_channel=req.source_channel,
        model=req.model,
        payload=enriched_payload,
    )
    return {"ok": True, "session_key": req.session_key}


def remove_chat_debug_session(session_key: str) -> dict[str, Any]:
    repository.delete_chat_debug_session(session_key)
    return {"ok": True, "session_key": session_key}


_BLOCKED_METADATA_HOSTS = {"169.254.169.254", "metadata.google.internal", "metadata.azure.com"}


def _default_target_agent_url() -> str:
    configured = os.getenv("AGENT_BASE_URL", "").strip()
    if configured:
        return configured.rstrip("/")
    return "http://127.0.0.1:10123"


def _allowlist_targets() -> list[str]:
    """Targets the admin proxy may forward to. Defaults to the configured Agent URL."""
    targets: list[str] = []
    configured = os.getenv("AGENT_BASE_URL", "").strip()
    if configured:
        targets.append(configured.rstrip("/"))
    extra = os.getenv("AGENT_ALLOWLIST", "").strip()
    if extra:
        for item in extra.split(","):
            item = item.strip().rstrip("/")
            if item:
                targets.append(item)
    if not targets:
        targets.append(_default_target_agent_url())
    return targets


def _host_key(url: str) -> str:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    port = parsed.port
    return f"{host}:{port}" if port else host


def _is_blocked_metadata_target(url: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if host in _BLOCKED_METADATA_HOSTS:
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_link_local or ip in ipaddress.ip_network("169.254.0.0/16")


def _resolve_allowed_target_url(req: AdminTestRunRequest) -> str:
    """Resolve the agent target URL strictly from the allowlist to prevent SSRF.

    A caller-supplied ``target_agent_url`` is only honoured when its host:port is
    in the configured allowlist; cloud metadata / link-local addresses are always
    rejected.
    """
    allowlist = _allowlist_targets()
    requested = (req.target_agent_url or "").strip().rstrip("/")
    if requested:
        if _is_blocked_metadata_target(requested):
            raise HTTPException(status_code=400, detail="target_agent_url points to a blocked metadata endpoint")
        allowed_hosts = {_host_key(t) for t in allowlist}
        if _host_key(requested) not in allowed_hosts:
            raise HTTPException(status_code=400, detail="target_agent_url is not in the SSRF allowlist")
        return requested
    return allowlist[0]


def _graded_timeout(req: AdminTestRunRequest) -> httpx.Timeout:
    """Separate connect/read/write/pool timeouts instead of one blunt total."""
    total = max(1, int(req.timeout_s))
    return httpx.Timeout(connect=min(10, total), read=total, write=min(30, total), pool=min(10, total))


def _proxy_headers(req: AdminTestRunRequest) -> dict[str, str]:
    return {"x-run-id": req.run_id} if req.run_id else {}


def _internal_debug_header() -> dict[str, str]:
    """Internal debug token injected only by the admin proxy (server-side).

    External callers passing the same header name cannot enable debug trace
    unless they know the server-side token; the agent validates it.
    """
    token = os.getenv("INTERNAL_DEBUG_TRACE_TOKEN", "").strip()
    return {"x-internal-debug-trace": token} if token else {}


async def proxy_test_run(req: AdminTestRunRequest) -> dict[str, Any]:
    base = _resolve_allowed_target_url(req)
    url = f"{base}{req.endpoint}"
    started = time.perf_counter()
    timeout = _graded_timeout(req)
    headers = _proxy_headers(req)
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(url, json=req.payload, headers=headers)
    latency_ms = int((time.perf_counter() - started) * 1000)
    body = _try_json(response)
    run_id = str((body.get("run_id") if isinstance(body, dict) else "") or req.run_id or "")
    logger.info("/run proxy completed: run_id=%s status=%s latency_ms=%s", run_id, response.status_code, latency_ms)
    return {
        "target_url": url,
        "status_code": response.status_code,
        "headers": redact_headers(dict(response.headers)),
        "body": body,
        "latency_ms": latency_ms,
        "run_id": run_id,
    }


def _try_json(response: httpx.Response) -> Any:
    try:
        return response.json()
    except Exception:
        return {"raw_text": response.text}


_STREAM_IDLE_TIMEOUT = 15.0


async def stream_test_run(req: AdminTestRunRequest, client_request: Request):
    """Proxy a real SSE stream from the agent with SSRF guard, graded timeouts,
    upstream status validation, client-disconnect detection, heartbeat on idle,
    and guaranteed upstream close in ``finally``."""
    base = _resolve_allowed_target_url(req)
    url = f"{base}{req.endpoint}"
    timeout = _graded_timeout(req)
    headers = {**_proxy_headers(req), **_internal_debug_header()}

    client = httpx.AsyncClient(timeout=timeout)
    upstream_request = client.build_request("POST", url, json=req.payload, headers=headers)
    response = await client.send(upstream_request, stream=True)

    if response.status_code >= 400:
        body = await response.aread()
        await response.aclose()
        await client.aclose()
        detail = body.decode("utf-8", errors="ignore") or "upstream error"
        logger.warning("/stream_run proxy upstream error: status=%s", response.status_code)
        raise HTTPException(status_code=response.status_code, detail=detail)

    run_id = req.run_id or ""

    async def _iterator():
        outcome = "ended"
        try:
            raw_iter = response.aiter_raw()
            while True:
                if await client_request.is_disconnected():
                    outcome = "client_disconnected"
                    logger.info("/stream_run proxy client disconnected: run_id=%s", run_id)
                    break
                try:
                    chunk = await asyncio.wait_for(raw_iter.__anext__(), timeout=_STREAM_IDLE_TIMEOUT)
                except asyncio.TimeoutError:
                    yield b": heartbeat\n\n"
                    continue
                except StopAsyncIteration:
                    break
                if chunk:
                    yield chunk
        except asyncio.CancelledError:
            outcome = "cancelled"
            raise
        except Exception as exc:  # noqa: BLE001 - log and end the stream cleanly
            outcome = f"failed:{type(exc).__name__}"
            logger.exception("/stream_run proxy iterator error: run_id=%s", run_id)
        finally:
            await response.aclose()
            await client.aclose()
            logger.info("/stream_run proxy stream %s: run_id=%s", outcome, run_id)

    return response, _iterator()


async def cancel_test_run(run_id: str) -> dict[str, Any]:
    """Forward a cancel to the agent's /cancel/{run_id} (allowlist-guarded)."""
    base = _resolve_allowed_target_url(AdminTestRunRequest(payload={}))
    url = f"{base}/cancel/{run_id}"
    timeout = httpx.Timeout(connect=5, read=10, write=5, pool=5)
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(url)
    logger.info("/cancel proxy: run_id=%s status=%s", run_id, response.status_code)
    return {"status_code": response.status_code, "body": _try_json(response)}


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


def _first_env(*keys: str) -> str:
    for key in keys:
        value = os.getenv(key, "").strip()
        if value:
            return value
    return ""


def _resolve_upload_storage_config() -> dict[str, str]:
    """Resolve admin upload storage config, supporting both legacy OSS_* and COZE_BUCKET_* names."""
    dotted_bucket = _first_env("oss.bucketName")
    legacy_bucket = _first_env("OSS_BUCKET_NAME")
    dotted_endpoint = _first_env("oss.endpoint")
    legacy_endpoint = _first_env("OSS_ENDPOINT")
    cfg = {
        "bucket_name": _first_env("COZE_BUCKET_NAME", "oss.bucketName", "OSS_BUCKET_NAME", "S3_BUCKET_NAME", "AWS_BUCKET_NAME"),
        "endpoint": _first_env("COZE_BUCKET_ENDPOINT_URL", "oss.endpoint", "OSS_ENDPOINT", "S3_ENDPOINT_URL", "AWS_ENDPOINT_URL"),
        "access_key": _first_env("COZE_BUCKET_ACCESS_KEY", "oss.accessKeyId", "OSS_ACCESS_KEY_ID", "AWS_ACCESS_KEY_ID"),
        "secret_key": _first_env("COZE_BUCKET_SECRET_KEY", "oss.accessKeySecret", "OSS_ACCESS_KEY_SECRET", "AWS_SECRET_ACCESS_KEY"),
        "region": _first_env("COZE_BUCKET_REGION", "OSS_REGION", "AWS_REGION") or "cn-beijing",
        "signed_url_expire_seconds": _first_env("oss.signedUrlExpireSeconds", "OSS_SIGNED_URL_EXPIRE_SECONDS", "COZE_BUCKET_SIGNED_URL_EXPIRE_SECONDS") or "600",
    }
    endpoint = cfg["endpoint"].lower()
    cfg["provider"] = "aliyun_oss" if dotted_bucket or legacy_bucket or dotted_endpoint or legacy_endpoint or "aliyuncs.com" in endpoint else "s3"
    missing = [name for name in ("bucket_name", "endpoint", "access_key", "secret_key") if not cfg[name]]
    if missing:
        aliases = {
            "bucket_name": "COZE_BUCKET_NAME or oss.bucketName or OSS_BUCKET_NAME",
            "endpoint": "COZE_BUCKET_ENDPOINT_URL or oss.endpoint or OSS_ENDPOINT",
            "access_key": "COZE_BUCKET_ACCESS_KEY or oss.accessKeyId or OSS_ACCESS_KEY_ID",
            "secret_key": "COZE_BUCKET_SECRET_KEY or oss.accessKeySecret or OSS_ACCESS_KEY_SECRET",
        }
        detail = "Storage upload is not configured. Missing: " + ", ".join(aliases[name] for name in missing)
        raise HTTPException(status_code=500, detail=detail)
    return cfg


def _upload_to_aliyun_oss(*, cfg: dict[str, str], object_key: str, content: bytes, content_type: str) -> dict[str, Any]:
    try:
        import oss2
    except Exception as exc:
        raise RuntimeError("oss2 SDK is not installed; install dependency `oss2` to use Aliyun OSS upload") from exc

    auth = oss2.Auth(cfg["access_key"], cfg["secret_key"])
    bucket = oss2.Bucket(auth, cfg["endpoint"], cfg["bucket_name"])
    result = bucket.put_object(object_key, content, headers={"Content-Type": content_type})
    expire_seconds = int(cfg.get("signed_url_expire_seconds") or "600")
    signed_url = bucket.sign_url("GET", object_key, expire_seconds)
    return {
        "key": object_key,
        "url": signed_url,
        "etag": getattr(result, "etag", None),
    }


def _upload_to_s3_compatible(*, cfg: dict[str, str], object_key: str, content: bytes, content_type: str) -> dict[str, Any]:
    storage = S3SyncStorage(
        endpoint_url=cfg["endpoint"],
        access_key=cfg["access_key"],
        secret_key=cfg["secret_key"],
        bucket_name=cfg["bucket_name"],
        region=cfg["region"],
    )
    key = storage.upload_file(file_content=content, file_name=object_key, content_type=content_type)
    try:
        url = storage.generate_presigned_url(key=key, bucket=cfg["bucket_name"], expire_time=int(cfg.get("signed_url_expire_seconds") or "600"))
    except Exception:
        url = _build_oss_public_url(cfg["bucket_name"], key, cfg["endpoint"])
    return {"key": key, "url": url, "etag": None}


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

    storage_cfg = _resolve_upload_storage_config()
    content_type = file.content_type or "application/octet-stream"
    object_key = _build_object_key(file.filename)

    try:
        if storage_cfg.get("provider") == "aliyun_oss":
            upload_result = _upload_to_aliyun_oss(cfg=storage_cfg, object_key=object_key, content=raw_content, content_type=content_type)
        else:
            upload_result = _upload_to_s3_compatible(cfg=storage_cfg, object_key=object_key, content=raw_content, content_type=content_type)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Storage upload failed: {exc}") from exc

    return {
        "bucket": storage_cfg["bucket_name"],
        "key": upload_result["key"],
        "url": upload_result["url"],
        "content_type": content_type,
        "size": len(raw_content),
        "etag": upload_result.get("etag"),
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


def _thinking_payload(thinking_type: str, reasoning_effort: str = "") -> dict[str, str]:
    return build_thinking_payload(thinking_type, reasoning_effort)


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
        requested_reasoning_effort=str(req.reasoning_effort or "").strip(),
    )
    request_body = {
        "model": resolved["model"],
        "thinking": _thinking_payload(resolved["thinking_type"], resolved["reasoning_effort"]),
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
                    "reasoning_effort": resolved["reasoning_effort"],
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
