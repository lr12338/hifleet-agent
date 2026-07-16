from __future__ import annotations

import re
from typing import Any

_SENSITIVE_KEY = re.compile(r"^(reasoning_content|api[_-]?key|authorization|password|token|access[_-]?token|refresh[_-]?token|confirmation[_-]?token)$", re.I)
_SENSITIVE_VALUE = re.compile(r"(api[_-]?key|authorization|(?:access|refresh|confirmation)[_-]?token|password)\s*[:=]\s*[^\s,;]+|/home/[^\s]+|[A-Z_]*(?:API_KEY|AUTHORIZATION|PASSWORD|TOKEN)=[^\s]+", re.I)


def safe_trace(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): "[redacted]" if _SENSITIVE_KEY.search(str(key)) else safe_trace(item) for key, item in value.items()}
    if isinstance(value, list):
        return [safe_trace(item) for item in value]
    if isinstance(value, str):
        return _SENSITIVE_VALUE.sub("[redacted]", value)
    return value
