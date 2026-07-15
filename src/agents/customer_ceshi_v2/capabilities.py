from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ModelCapabilities:
    text: bool = False
    thinking: bool = False
    native_tool_calls: bool = False
    json_output: bool = False
    multi_turn_tool_results: bool = False
    streaming: bool = False


def probe_text_capabilities(client: Any) -> ModelCapabilities:
    """Probe actual client behavior; each unavailable feature stays explicitly false."""
    if client is None:
        return ModelCapabilities()
    try:
        client.invoke("capability probe: reply OK")
    except Exception:
        return ModelCapabilities()
    thinking = False
    json_output = False
    native_tool_calls = False
    streaming = False
    try:
        client.invoke("capability probe: reply OK", extra_body={"thinking": {"type": "enabled", "reasoning_effort": "minimal"}})
        thinking = True
    except Exception:
        pass
    try:
        client.invoke("Return JSON only: {\"ok\": true}")
        json_output = True
    except Exception:
        pass
    try:
        bound = client.bind_tools([])
        bound.invoke("capability probe: reply OK")
        native_tool_calls = True
    except Exception:
        pass
    try:
        iterator = client.stream("capability probe: reply OK")
        next(iter(iterator))
        streaming = True
    except Exception:
        pass
    return ModelCapabilities(text=True, thinking=thinking, native_tool_calls=native_tool_calls, json_output=json_output, multi_turn_tool_results=native_tool_calls, streaming=streaming)
