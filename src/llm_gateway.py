from __future__ import annotations

import os
from typing import Any

from coze_coding_utils.runtime_ctx.context import default_headers
from langchain_openai import ChatOpenAI

from llm_config import DEFAULT_MULTIMODAL_MODEL, DEFAULT_TEXT_MODEL, build_thinking_payload, resolve_thinking_settings
from utils.llm_route_state import get_current_llm_route


def safe_default_headers(ctx: Any) -> dict[str, str]:
    if not ctx:
        return {}
    try:
        return default_headers(ctx)
    except Exception:
        return {}


def _config_values(cfg: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(cfg, dict):
        return {}
    nested = cfg.get("config")
    return dict(nested) if isinstance(nested, dict) else dict(cfg)


def resolve_role_base_url(cfg: dict[str, Any], role: str) -> str:
    config = _config_values(cfg)
    env_key_by_role = {
        "text": "text_model_base_url_env",
        "multimodal": "multimodal_model_base_url_env",
        "json": "json_model_base_url_env",
    }
    env_name = str(config.get(env_key_by_role.get(role, "")) or "").strip()
    if env_name:
        value = os.getenv(env_name, "").strip()
        if value:
            return value
    return os.getenv("COZE_INTEGRATION_MODEL_BASE_URL", "").strip()


def resolve_runtime_llm_settings(
    cfg: dict[str, Any],
    *,
    role: str = "text",
    allow_runtime_model_override: bool = True,
) -> dict[str, Any]:
    config = _config_values(cfg)
    route = get_current_llm_route()
    requested_model = str(route.get("model", "")).strip()
    requested_modality = str(route.get("modality", "")).strip().lower()
    requested_thinking = str(route.get("thinking_type", "")).strip()
    requested_effort = str(route.get("reasoning_effort", "")).strip()
    if role == "multimodal":
        default_model = str(config.get("multimodal_model") or DEFAULT_MULTIMODAL_MODEL).strip()
        default_thinking = config.get("multimodal_thinking_type") or config.get("thinking_type") or "enabled"
    elif role == "json":
        default_model = str(
            config.get("customer_support_json_model")
            or config.get("customer_support_reasoning_model")
            or config.get("text_model")
            or config.get("model")
            or DEFAULT_TEXT_MODEL
        ).strip()
        default_thinking = config.get("customer_support_json_thinking_type") or config.get("thinking_type") or "enabled"
    else:
        default_model = str(config.get("text_model") or config.get("model") or DEFAULT_TEXT_MODEL).strip()
        default_thinking = config.get("text_thinking_type") or config.get("thinking_type") or "enabled"
    thinking = resolve_thinking_settings(
        requested_thinking or default_thinking,
        requested_effort or config.get("reasoning_effort") or "medium",
    )
    matching_modality = role != "multimodal" or requested_modality in {"", "multimodal"}
    model_override_applied = bool(requested_model and allow_runtime_model_override and matching_modality)
    return {
        "model": requested_model if model_override_applied else default_model,
        "requested_model": requested_model,
        "requested_modality": requested_modality,
        "model_override_applied": model_override_applied,
        **thinking,
    }


def build_chat_model(
    ctx: Any,
    cfg: dict[str, Any],
    *,
    role: str,
    streaming: bool = False,
    model_override: str = "",
    temperature: float | None = None,
    timeout: float | int | None = None,
    allow_runtime_model_override: bool = True,
    chat_model_class: type[ChatOpenAI] | None = None,
) -> ChatOpenAI | None:
    config = _config_values(cfg)
    api_key = os.getenv("COZE_WORKLOAD_IDENTITY_API_KEY", "").strip()
    base_url = resolve_role_base_url(cfg, role)
    if not api_key or not base_url:
        return None
    settings = resolve_runtime_llm_settings(
        cfg,
        role=role,
        allow_runtime_model_override=allow_runtime_model_override,
    )
    model = str(model_override or settings["model"]).strip()
    factory = chat_model_class or ChatOpenAI
    return factory(
        model=model,
        api_key=api_key,
        base_url=base_url,
        temperature=float(config.get("temperature", 0.7) if temperature is None else temperature),
        streaming=streaming,
        timeout=config.get("timeout", 600) if timeout is None else timeout,
        extra_body={"thinking": build_thinking_payload(settings["thinking_type"], settings["reasoning_effort"])},
        default_headers=safe_default_headers(ctx),
    )
