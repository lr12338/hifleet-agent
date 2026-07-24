"""Redaction and size-limiting for DebugEvent payloads.

Signed URLs, authorization headers, API keys, tokens and cookies must never be
persisted or rendered. This module redacts sensitive keys/values recursively and
shrinks oversized content to a summary + hash so the workbench never stores full
tool bodies or signed attachment URLs.
"""
from __future__ import annotations

import hashlib
import re
from typing import Any

# Keys whose values are always secret.
_SENSITIVE_KEY = re.compile(
    r"^(api[_-]?key|authorization|cookie|password|passwd|secret|token|access[_-]?token|"
    r"refresh[_-]?token|confirmation[_-]?token|bearer|x-admin-api-key|signature|sig|"
    r"aws[_-]?access[_-]?key[_-]?id|aws[_-]?secret[_-]?access[_-]?key|oss[_-]?access[_-]?key[_-]?id|"
    r"oss[_-]?access[_-]?key[_-]?secret|private[_-]?key|client[_-]?secret)$",
    re.I,
)

# Inline secret patterns inside string values.
_SENSITIVE_VALUE = re.compile(
    r"(api[_-]?key|authorization|bearer|(?:access|refresh|confirmation)[_-]?token|password|secret|"
    r"signature|X-Amz-Signature|X-Amz-Credential|x-oss-signature)\s*[:=]\s*[^\s,;}&]+",
    re.I,
)
# Long-lived signed URL query params that leak access (AWS S3 / Aliyun OSS / generic).
_SENSITIVE_QUERY_PARAM = re.compile(
    r"^(signature|sig|x-amz-signature|x-amz-credential|x-amz-security-token|x-oss-signature|"
    r"x-oss-credential|accesskeyid|access-key-id|token|expires|secrets?|apikey|api_key)$",
    re.I,
)

_MAX_STRING_LEN = 4000
_MAX_LIST_LEN = 50
_MAX_TOTAL_BYTES = 64_000


def _hash_preview(text: str) -> str:
    digest = hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()[:12]
    return f"sha256:{digest}"


def truncate(value: str, limit: int = _MAX_STRING_LEN) -> str:
    if len(value) <= limit:
        return value
    return value[:limit] + f"…[truncated {len(value) - limit} chars; {_hash_preview(value)}]"


def redact_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): "[redacted]" if _SENSITIVE_KEY.search(str(k)) else redact_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact_value(v) for v in value[:_MAX_LIST_LEN]]
    if isinstance(value, str):
        cleaned = _SENSITIVE_VALUE.sub(
            lambda m: f"{m.group(0).split(':', 1)[0]}:[redacted]" if ":" in m.group(0) else f"{m.group(0).split('=', 1)[0]}=[redacted]",
            value,
        )
        return truncate(cleaned)
    return value


def sanitize_url(url: str) -> str:
    """Redact signed-URL query parameters while preserving host/path/object key.

    e.g. ``https://bucket.oss.aliyuncs.com/key?Expires=1&Signature=abc`` ->
    ``https://bucket.oss.aliyuncs.com/key?Expires=***&Signature=***``
    """
    if not isinstance(url, str) or not url:
        return url
    if "?" not in url:
        return url
    base, query = url.split("?", 1)
    parts: list[str] = []
    for pair in query.split("&"):
        if not pair:
            continue
        if "=" in pair:
            key, _val = pair.split("=", 1)
        else:
            key = pair
        if _SENSITIVE_QUERY_PARAM.search(key):
            parts.append(f"{key}=***")
        else:
            parts.append(pair)
    return f"{base}?{'&'.join(parts)}" if parts else base


def redact_event_data(data: dict[str, Any]) -> dict[str, Any]:
    """Redact and size-limit the ``data`` blob of a DebugEvent in place-safe copy."""
    redacted = redact_value(data) if isinstance(data, dict) else redact_value({"value": data})
    if not isinstance(redacted, dict):
        redacted = {"value": redacted}
    # Enforce a soft total size cap by truncating large string leaves further.
    encoded = repr(redacted)
    if len(encoded.encode("utf-8", errors="ignore")) > _MAX_TOTAL_BYTES:
        redacted = {"preview": truncate(encoded, _MAX_TOTAL_BYTES // 2), "truncated": True, "hash": _hash_preview(encoded)}
    return redacted


def redact_headers(headers: dict[str, str] | None) -> dict[str, str]:
    """Return a safe subset of HTTP headers for display."""
    if not headers:
        return {}
    safe: dict[str, str] = {}
    for key, value in headers.items():
        if _SENSITIVE_KEY.search(str(key)):
            safe[str(key)] = "***"
        else:
            safe[str(key)] = truncate(str(value))
    return safe
