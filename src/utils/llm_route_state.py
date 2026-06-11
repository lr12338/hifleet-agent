from __future__ import annotations

from contextvars import ContextVar
from typing import Any

_CURRENT_LLM_ROUTE: ContextVar[dict[str, Any]] = ContextVar('current_llm_route', default={})


def set_current_llm_route(route: dict[str, Any] | None) -> None:
    _CURRENT_LLM_ROUTE.set(dict(route or {}))


def get_current_llm_route() -> dict[str, Any]:
    return dict(_CURRENT_LLM_ROUTE.get() or {})


def clear_current_llm_route() -> None:
    _CURRENT_LLM_ROUTE.set({})
