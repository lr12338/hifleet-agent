from dataclasses import dataclass, asdict
from typing import Any, Dict, Optional
import logging

from utils.session_state import get_current_session_id

logger = logging.getLogger(__name__)


@dataclass
class ToolResult:
    status: str
    code: str
    message: str
    retriable: bool = False
    latency_ms: int = 0
    source: str = ""
    data: Optional[Dict[str, Any]] = None


def to_user_text(result: ToolResult) -> str:
    return result.message


def emit_tool_metric(
    tool_name: str,
    run_id: str,
    result: ToolResult,
    *,
    tool_args: Optional[Dict[str, Any]] = None,
    layer_trace: Optional[Dict[str, Any]] = None,
):
    payload = asdict(result)
    payload["tool_name"] = tool_name
    payload["run_id"] = run_id
    trace_payload = dict(layer_trace or {})
    session_id = trace_payload.get("session_id") or get_current_session_id()
    attempt = int(trace_payload.get("attempt") or 0)
    logger.info(f"[ToolMetric] {payload}")
    if not run_id:
        return
    try:
        from observability import schedule_tool_invocation_log

        schedule_tool_invocation_log(
            {
                "run_id": run_id,
                "session_id": session_id,
                "tool_name": tool_name,
                "tool_args": tool_args,
                "tool_result": payload.get("data") or {"message": result.message},
                "status": result.status,
                "code": result.code,
                "message": result.message,
                "retriable": result.retriable,
                "attempt": attempt,
                "latency_ms": result.latency_ms,
                "source": result.source or None,
                "layer_trace": trace_payload or None,
            }
        )
    except Exception as exc:
        logger.warning(f"[ToolMetric] write observability failed: {exc}")
