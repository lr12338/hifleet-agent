"""Safe attachment metadata helpers for customer_support."""
from __future__ import annotations

import json
from urllib.parse import urlparse

from langchain.tools import tool

from utils.file.file import infer_file_category


@tool
def inspect_media_attachment(file_url: str, declared_type: str = "") -> str:
    """Return safe metadata for an image/audio/video/file URL without exposing internals."""
    category, suffix = infer_file_category(file_url or "")
    parsed = urlparse(file_url or "")
    payload = {
        "category": declared_type or category,
        "suffix": suffix,
        "has_url": bool(parsed.scheme in {"http", "https"} and parsed.netloc),
        "filename": (parsed.path.rsplit("/", 1)[-1] or "attachment")[:120],
        "can_analyze_with_multimodal_model": (declared_type or category) in {"image", "audio", "video"},
    }
    return json.dumps(payload, ensure_ascii=False)
