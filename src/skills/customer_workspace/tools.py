"""Controlled file helpers for external customer support."""
from __future__ import annotations

import json
import mimetypes
import os
import uuid
from pathlib import Path

from langchain.tools import tool

from storage.s3.s3_storage import S3SyncStorage
from utils.file.file import File, FileOps, infer_file_category

MAX_SNIPPET_CHARS = 3000


def _first_env(*keys: str) -> str:
    for key in keys:
        value = os.getenv(key, "").strip()
        if value:
            return value
    return ""


def _storage_config() -> dict[str, str]:
    endpoint = _first_env("COZE_BUCKET_ENDPOINT_URL", "oss.endpoint", "OSS_ENDPOINT")
    bucket = _first_env("COZE_BUCKET_NAME", "oss.bucketName", "OSS_BUCKET_NAME")
    access_key = _first_env("COZE_BUCKET_ACCESS_KEY", "oss.accessKeyId", "OSS_ACCESS_KEY_ID")
    secret_key = _first_env("COZE_BUCKET_SECRET_KEY", "oss.accessKeySecret", "OSS_ACCESS_KEY_SECRET")
    return {
        "endpoint": endpoint,
        "bucket": bucket,
        "access_key": access_key,
        "secret_key": secret_key,
        "expire": _first_env("oss.signedUrlExpireSeconds", "OSS_SIGNED_URL_EXPIRE_SECONDS", "COZE_BUCKET_SIGNED_URL_EXPIRE_SECONDS") or "600",
        "provider": "aliyun_oss" if "aliyuncs.com" in endpoint.lower() or _first_env("oss.bucketName", "OSS_BUCKET_NAME") else "s3",
    }


def _storage() -> S3SyncStorage:
    cfg = _storage_config()
    return S3SyncStorage(
        endpoint_url=cfg["endpoint"],
        access_key=cfg["access_key"],
        secret_key=cfg["secret_key"],
        bucket_name=cfg["bucket"],
    )


def _sanitize_object_name(name: str) -> str:
    base = Path(name or "artifact").name
    cleaned = "".join(ch if ch.isalnum() or ch in {".", "_", "-"} else "_" for ch in base).strip("._")
    return cleaned or "artifact"


def _upload_artifact_aliyun(path: Path, name: str, content_type: str) -> dict[str, str]:
    cfg = _storage_config()
    if not all([cfg["endpoint"], cfg["bucket"], cfg["access_key"], cfg["secret_key"]]):
        raise RuntimeError("OSS storage is not configured")
    try:
        import oss2
    except Exception as exc:
        raise RuntimeError("oss2 SDK is not installed") from exc
    key = f"customer_artifacts/{uuid.uuid4().hex}_{_sanitize_object_name(name)}"
    bucket = oss2.Bucket(oss2.Auth(cfg["access_key"], cfg["secret_key"]), cfg["endpoint"], cfg["bucket"])
    bucket.put_object(key, path.read_bytes(), headers={"Content-Type": content_type})
    return {"key": key, "url": bucket.sign_url("GET", key, int(cfg["expire"]))}


@tool
def inspect_customer_file(file_url: str, max_chars: int = MAX_SNIPPET_CHARS) -> str:
    """Inspect a customer-provided public file and return a safe text/schema summary."""
    category, suffix = infer_file_category(file_url or "")
    if category not in {"document", "image", "audio", "video"}:
        return json.dumps({"ok": False, "reason": "unsupported_file_type", "category": category, "suffix": suffix}, ensure_ascii=False)
    if category in {"image", "audio", "video"}:
        return json.dumps({"ok": True, "category": category, "suffix": suffix, "text": "", "needs_multimodal": True}, ensure_ascii=False)
    text = FileOps.extract_text(File(url=file_url, file_type="document"))
    return json.dumps(
        {
            "ok": not text.startswith("[FileOps Error]"),
            "category": category,
            "suffix": suffix,
            "text": text[: max(200, min(int(max_chars or MAX_SNIPPET_CHARS), MAX_SNIPPET_CHARS))],
            "truncated": len(text) > MAX_SNIPPET_CHARS,
        },
        ensure_ascii=False,
    )


@tool
def upload_customer_artifact(local_path: str, file_name: str = "") -> str:
    """Upload an existing generated artifact to configured S3/OSS and return a presigned URL."""
    path = Path(local_path).expanduser().resolve()
    allowed_roots = [Path(os.getenv("HIFLEET_AGENT_ARTIFACT_DIR", "/tmp/hifleet_agent_artifacts")).resolve(), Path("/tmp").resolve()]
    if not any(str(path).startswith(str(root)) for root in allowed_roots) or not path.is_file():
        return json.dumps({"ok": False, "reason": "artifact_not_found_or_not_allowed"}, ensure_ascii=False)
    name = file_name or path.name
    content_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
    cfg = _storage_config()
    if cfg["provider"] == "aliyun_oss":
        uploaded = _upload_artifact_aliyun(path, name, content_type)
        key = uploaded["key"]
        url = uploaded["url"]
    else:
        storage = _storage()
        key = storage.upload_file(file_content=path.read_bytes(), file_name=name, content_type=content_type)
        url = storage.generate_presigned_url(key=key, expire_time=int(cfg["expire"]))
    return json.dumps({"ok": True, "url": url, "file_name": name}, ensure_ascii=False)
