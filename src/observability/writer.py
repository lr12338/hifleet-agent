from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

from . import repository
from .schemas import AgentErrorCreate, ApiCallCreate, ToolInvocationCreate

logger = logging.getLogger(__name__)

_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="obs-writer")


def _submit(fn: Callable[..., Any], *args: Any) -> None:
    future = _EXECUTOR.submit(fn, *args)

    def _done_callback(f):
        exc = f.exception()
        if exc:
            logger.warning(f"[Observability] async write failed: {exc}")

    future.add_done_callback(_done_callback)


def ensure_observability_schema() -> None:
    try:
        repository.ensure_schema()
    except Exception as exc:
        logger.warning(f"[Observability] ensure schema failed: {exc}")


async def log_api_call(item: ApiCallCreate) -> None:
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(_EXECUTOR, repository.insert_api_call, item)


async def log_tool_invocation(item: ToolInvocationCreate) -> None:
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(_EXECUTOR, repository.insert_tool_invocation, item)


async def log_agent_error(item: AgentErrorCreate) -> None:
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(_EXECUTOR, repository.insert_agent_error, item)


def schedule_api_call_log(payload: dict[str, Any]) -> None:
    try:
        model = ApiCallCreate.model_validate(payload)
        _submit(repository.insert_api_call, model)
    except Exception as exc:
        logger.warning(f"[Observability] schedule api call failed: {exc}")


def schedule_tool_invocation_log(payload: dict[str, Any]) -> None:
    try:
        model = ToolInvocationCreate.model_validate(payload)
        _submit(repository.insert_tool_invocation, model)
    except Exception as exc:
        logger.warning(f"[Observability] schedule tool invocation failed: {exc}")


def schedule_agent_error_log(payload: dict[str, Any]) -> None:
    try:
        model = AgentErrorCreate.model_validate(payload)
        _submit(repository.insert_agent_error, model)
    except Exception as exc:
        logger.warning(f"[Observability] schedule agent error failed: {exc}")
