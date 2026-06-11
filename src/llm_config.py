from __future__ import annotations
import json
import os
from pathlib import Path
from typing import Any, Literal
LLM_CONFIG_RELATIVE_PATH = 'config/agent_llm_config.json'
DEFAULT_TEXT_MODEL = 'doubao-seed-2-0-pro-260215'
DEFAULT_MULTIMODAL_MODEL = 'doubao-seed-2-0-lite-260428'
DEFAULT_THINKING_TYPE: Literal['enabled', 'disabled', 'auto'] = 'disabled'
HEADER_LLM_MODEL = 'x-hifleet-llm-model'
HEADER_LLM_MODALITY = 'x-hifleet-llm-modality'
HEADER_LLM_THINKING_TYPE = 'x-hifleet-llm-thinking-type'
TEXT_MODEL_PRESETS = [
    'doubao-seed-2-0-pro-260215',
    'deepseek-v4-pro-260425',
    'deepseek-v4-flash-260425',
    'doubao-seed-2-0-mini-260428',
]
MULTIMODAL_MODEL_PRESETS = [
    'doubao-seed-2-0-lite-260428',
]
MULTIMODAL_MESSAGE_TYPES = {
    'image_url',
    'video_url',
    'input_audio',
    'input_image',
    'input_video',
}
def resolve_workspace_path() -> str:
    configured = os.getenv('COZE_WORKSPACE_PATH')
    if configured:
        return configured
    return str(Path(__file__).resolve().parent.parent)
def llm_config_path(workspace_path: str | None = None) -> Path:
    base = Path(workspace_path or resolve_workspace_path())
    return (base / LLM_CONFIG_RELATIVE_PATH).resolve()
def normalize_thinking_type(value: Any) -> Literal['enabled', 'disabled', 'auto']:
    normalized = str(value or '').strip().lower()
    if normalized in {'enabled', 'disabled', 'auto'}:
        return normalized
    return DEFAULT_THINKING_TYPE
def normalize_llm_config(raw: dict[str, Any] | None) -> dict[str, Any]:
    data = dict(raw or {})
    config = dict(data.get('config') or {})
    text_model = str(config.get('text_model') or config.get('model') or DEFAULT_TEXT_MODEL).strip() or DEFAULT_TEXT_MODEL
    multimodal_model = str(config.get('multimodal_model') or DEFAULT_MULTIMODAL_MODEL).strip() or DEFAULT_MULTIMODAL_MODEL
    thinking_type = normalize_thinking_type(
        config.get('thinking_type')
        or ('enabled' if config.get('deep_thinking_enabled') else DEFAULT_THINKING_TYPE)
    )
    config['text_model'] = text_model
    config['multimodal_model'] = multimodal_model
    config['model'] = text_model
    config['thinking_type'] = thinking_type
    config['deep_thinking_enabled'] = thinking_type != 'disabled'
    data['config'] = config
    data.setdefault('sp', 'System prompt dynamically assembled from config/system_prompt_base.md + skills/*/SKILL.md')
    data.setdefault('tools', [])
    return data
def load_llm_config(workspace_path: str | None = None) -> dict[str, Any]:
    path = llm_config_path(workspace_path)
    if not path.exists():
        return normalize_llm_config({})
    return normalize_llm_config(json.loads(path.read_text(encoding='utf-8')))
def save_llm_config(config: dict[str, Any], workspace_path: str | None = None) -> dict[str, Any]:
    normalized = normalize_llm_config(config)
    path = llm_config_path(workspace_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(normalized, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return normalized
def messages_have_multimodal_content(messages: list[Any] | None) -> bool:
    for msg in messages or []:
        if not isinstance(msg, dict):
            continue
        content = msg.get('content')
        if not isinstance(content, list):
            continue
        for item in content:
            if not isinstance(item, dict):
                continue
            if str(item.get('type', '')).strip().lower() in MULTIMODAL_MESSAGE_TYPES:
                return True
    return False
def resolve_model_selection(
    config: dict[str, Any] | None,
    *,
    has_multimodal_input: bool,
    requested_model: str = '',
    requested_thinking: str = '',
) -> dict[str, Any]:
    normalized = normalize_llm_config(config)
    cfg = normalized['config']
    modality: Literal['text', 'multimodal'] = 'multimodal' if has_multimodal_input else 'text'
    default_model = cfg['multimodal_model'] if has_multimodal_input else cfg['text_model']
    model = str(requested_model or default_model).strip() or default_model
    thinking_type = normalize_thinking_type(requested_thinking or cfg.get('thinking_type'))
    return {
        'model': model,
        'modality': modality,
        'thinking_type': thinking_type,
        'deep_thinking_enabled': thinking_type != 'disabled',
    }
def export_llm_config_view(config: dict[str, Any] | None) -> dict[str, Any]:
    normalized = normalize_llm_config(config)
    cfg = normalized['config']
    return {
        'text_model': cfg['text_model'],
        'multimodal_model': cfg['multimodal_model'],
        'thinking_type': cfg['thinking_type'],
        'deep_thinking_enabled': bool(cfg.get('deep_thinking_enabled')),
        'text_model_presets': TEXT_MODEL_PRESETS,
        'multimodal_model_presets': MULTIMODAL_MODEL_PRESETS,
    }
