"""V2 skill loader: assembles tool implementations from V2 skill adapters.

This is the physical-decoupling replacement for the legacy ``SkillLoader``. It
only imports V2 skill adapters (``skills_v2.skills.*``) and never imports
``skills.*``. Each adapter exposes its own ``@tool`` callables, so descriptors
and runtime tools share one source of truth inside V2.
"""
from __future__ import annotations

from typing import Any

from skills_v2.skills.hifleet_data import adapter as _hifleet_data_adapter
from skills_v2.skills.knowledge_retrieval import adapter as _knowledge_retrieval_adapter
from skills_v2.skills.ship_info_update import adapter as _ship_info_update_adapter
from skills_v2.skills.web_search import adapter as _web_search_adapter


def _collect_tools() -> dict[str, Any]:
    tools: dict[str, Any] = {}
    for module in (_knowledge_retrieval_adapter, _web_search_adapter):
        for name in getattr(module, "__all__", []):
            tool_obj = getattr(module, name, None)
            if tool_obj is not None and getattr(tool_obj, "name", None):
                tools[tool_obj.name] = tool_obj
    for tool_obj in _hifleet_data_adapter.get_hifleet_data_tools():
        tools[tool_obj.name] = tool_obj
    for tool_obj in _ship_info_update_adapter.get_ship_update_tools():
        tools[tool_obj.name] = tool_obj
    return tools


_TOOL_REGISTRY: dict[str, Any] = {}


def _registry() -> dict[str, Any]:
    if not _TOOL_REGISTRY:
        _TOOL_REGISTRY.update(_collect_tools())
    return _TOOL_REGISTRY


def get_tool(name: str) -> Any | None:
    return _registry().get(name)


def get_tools_by_names(names: list[str]) -> list[Any]:
    registry = _registry()
    tools: list[Any] = []
    for name in names or []:
        tool_obj = registry.get(name)
        if tool_obj is not None:
            tools.append(tool_obj)
    return tools


def available_tool_names() -> list[str]:
    return sorted(_registry().keys())
