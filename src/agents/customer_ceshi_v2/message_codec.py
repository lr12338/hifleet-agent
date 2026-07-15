from __future__ import annotations

from typing import Any


HIDDEN_REASONING_FIELDS = {"reasoning_content", "reasoning", "analysis", "thought"}


def model_turn_payload(message: Any) -> dict[str, Any]:
    """Keep provider fields for model continuation, never for readable traces."""
    additional = dict(getattr(message, "additional_kwargs", {}) or {})
    return {"content": getattr(message, "content", ""), "provider_fields": additional}


def visible_message_summary(message: Any) -> str:
    return str(getattr(message, "content", ""))
