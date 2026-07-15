from __future__ import annotations

import re
from typing import Any

_SENSITIVE = re.compile(r"(reasoning_content|api[_-]?key|authorization|token|password|/home/[^\s]+|[A-Z_]{3,}=\S+)", re.I)


def safe_trace(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): "[redacted]" if _SENSITIVE.search(str(key)) else safe_trace(item) for key, item in value.items()}
    if isinstance(value, list):
        return [safe_trace(item) for item in value]
    if isinstance(value, str):
        return _SENSITIVE.sub("[redacted]", value)
    return value
