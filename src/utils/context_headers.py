"""Best-effort request header storage for runtime contexts with slots."""
from __future__ import annotations

from typing import Any

_CONTEXT_HEADERS_BY_ID: dict[int, dict[str, Any]] = {}


def ensure_context_headers(ctx: Any) -> dict[str, Any]:
    if ctx is None:
        return {}
    headers = getattr(ctx, "headers", None)
    if isinstance(headers, dict):
        return headers
    cached = _CONTEXT_HEADERS_BY_ID.get(id(ctx))
    if isinstance(cached, dict):
        return cached
    try:
        headers = {}
        setattr(ctx, "headers", headers)
        return headers
    except Exception:
        return _CONTEXT_HEADERS_BY_ID.setdefault(id(ctx), {})


def get_context_headers(ctx: Any) -> dict[str, Any]:
    return ensure_context_headers(ctx)
