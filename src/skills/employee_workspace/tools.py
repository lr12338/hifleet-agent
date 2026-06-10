"""Employee-only file and Python sandbox tools."""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import textwrap
import time
from pathlib import Path
from typing import Any, Dict

import pandas as pd
from langchain.tools import tool
from coze_coding_utils.log.write_log import request_context
from skills.common.tool_result import ToolResult, emit_tool_metric
from agents.profiles import get_current_agent_profile_id


WORKSPACE_ROOT = Path(os.getenv("COZE_WORKSPACE_PATH", Path.cwd())).resolve()
ARTIFACT_ROOT = Path(os.getenv("HIFLEET_AGENT_ARTIFACT_DIR", "/tmp/hifleet_agent_artifacts")).resolve()
SANDBOX_TIMEOUT_SEC = int(os.getenv("HIFLEET_PY_SANDBOX_TIMEOUT_SEC", "20"))
MAX_CODE_CHARS = int(os.getenv("HIFLEET_PY_SANDBOX_MAX_CODE_CHARS", "12000"))

BLOCKED_CODE_PATTERNS = [
    r"\bos\.system\s*\(",
    r"\bsubprocess\.",
    r"\bsocket\.",
    r"\bshutil\.rmtree\s*\(",
    r"\bPath\s*\(\s*['\"]/",
    r"\bopen\s*\(\s*['\"]/(etc|root|home|var|usr|proc|sys|dev)",
]


def _emit(tool_name: str, result: ToolResult, tool_args: Dict[str, Any] | None = None) -> None:
    ctx = request_context.get()
    emit_tool_metric(
        tool_name,
        getattr(ctx, "run_id", "") if ctx else "",
        result,
        tool_args=tool_args,
        layer_trace={"sandbox": True},
    )


def _is_employee_profile() -> bool:
    if get_current_agent_profile_id() == "employee_assistant":
        return True
    ctx = request_context.get()
    headers = getattr(ctx, "headers", {}) if ctx else {}
    if isinstance(headers, dict):
        return headers.get("x-agent-profile") == "employee_assistant"
    return False


def _resolve_allowed_path(path_text: str) -> Path:
    raw = Path(path_text).expanduser()
    path = raw if raw.is_absolute() else WORKSPACE_ROOT / raw
    resolved = path.resolve()
    allowed_roots = [WORKSPACE_ROOT, ARTIFACT_ROOT, Path("/tmp").resolve()]
    if not any(str(resolved).startswith(str(root)) for root in allowed_roots):
        raise ValueError("path is outside allowed workspace or artifact directories")
    return resolved


def _blocked_reason(code: str) -> str:
    for pattern in BLOCKED_CODE_PATTERNS:
        if re.search(pattern, code):
            return f"blocked unsafe pattern: {pattern}"
    return ""


@tool
def inspect_tabular_file(file_path: str, max_rows: int = 5) -> str:
    """
    Inspect a CSV/XLSX file for internal employee analysis.

    Args:
        file_path: Path under the workspace, /tmp, or artifact directory.
        max_rows: Number of preview rows to return.
    """
    started = time.time()
    args = {"file_path": file_path, "max_rows": max_rows}
    try:
        if not _is_employee_profile():
            raise PermissionError("inspect_tabular_file is only available in employee_assistant profile")
        path = _resolve_allowed_path(file_path)
        if not path.exists() or not path.is_file():
            raise FileNotFoundError(f"file not found: {path}")

        suffix = path.suffix.lower()
        if suffix == ".csv":
            df = pd.read_csv(path)
        elif suffix in (".xlsx", ".xls"):
            df = pd.read_excel(path)
        else:
            raise ValueError("only CSV/XLS/XLSX files are supported")

        preview = df.head(max(1, min(int(max_rows), 20))).to_dict(orient="records")
        payload = {
            "file": str(path),
            "shape": [int(df.shape[0]), int(df.shape[1])],
            "columns": [str(c) for c in df.columns],
            "preview": preview,
        }
        message = json.dumps(payload, ensure_ascii=False, default=str)
        _emit(
            "inspect_tabular_file",
            ToolResult("ok", "TABULAR_FILE_INSPECTED", message, latency_ms=int((time.time() - started) * 1000), source="employee_workspace", data=payload),
            args,
        )
        return message
    except Exception as exc:
        message = f"文件检查失败：{exc}"
        _emit(
            "inspect_tabular_file",
            ToolResult("error", "TABULAR_FILE_INSPECT_FAILED", message, retriable=False, latency_ms=int((time.time() - started) * 1000), source="employee_workspace"),
            args,
        )
        return message


@tool
def run_sandboxed_python(code: str) -> str:
    """
    Run a short Python script in an isolated temporary directory for internal analysis.

    Args:
        code: Python code. Generated artifacts should be written under ARTIFACT_DIR.
    """
    started = time.time()
    args = {"code_chars": len(code or "")}
    try:
        if not _is_employee_profile():
            raise PermissionError("run_sandboxed_python is only available in employee_assistant profile")
        if not code or len(code) > MAX_CODE_CHARS:
            raise ValueError(f"code must be non-empty and <= {MAX_CODE_CHARS} chars")
        reason = _blocked_reason(code)
        if reason:
            raise PermissionError(reason)

        ARTIFACT_ROOT.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="hifleet_py_", dir="/tmp") as tmpdir:
            sandbox_dir = Path(tmpdir).resolve()
            script_path = sandbox_dir / "task.py"
            output_dir = ARTIFACT_ROOT / sandbox_dir.name
            output_dir.mkdir(parents=True, exist_ok=True)

            prelude = f"""
import os
from pathlib import Path
ARTIFACT_DIR = Path({str(output_dir)!r})
ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
"""
            script_path.write_text(textwrap.dedent(prelude) + "\n" + code, encoding="utf-8")
            env = {
                "PATH": os.getenv("PATH", ""),
                "PYTHONNOUSERSITE": "1",
                "PYTHONUNBUFFERED": "1",
                "ARTIFACT_DIR": str(output_dir),
            }
            proc = subprocess.run(
                ["python3", "-I", str(script_path)],
                cwd=str(sandbox_dir),
                env=env,
                text=True,
                capture_output=True,
                timeout=SANDBOX_TIMEOUT_SEC,
                check=False,
            )
            artifacts = [str(p) for p in output_dir.glob("**/*") if p.is_file()]
            payload = {
                "returncode": proc.returncode,
                "stdout": proc.stdout[-4000:],
                "stderr": proc.stderr[-4000:],
                "artifact_dir": str(output_dir),
                "artifacts": artifacts[:50],
            }
            message = json.dumps(payload, ensure_ascii=False)
            status = "ok" if proc.returncode == 0 else "error"
            code_name = "PYTHON_SANDBOX_OK" if proc.returncode == 0 else "PYTHON_SANDBOX_NONZERO"
            _emit(
                "run_sandboxed_python",
                ToolResult(status, code_name, message, retriable=proc.returncode != 0, latency_ms=int((time.time() - started) * 1000), source="employee_workspace", data=payload),
                args,
            )
            return message
    except subprocess.TimeoutExpired:
        message = f"Python sandbox timed out after {SANDBOX_TIMEOUT_SEC}s"
        _emit(
            "run_sandboxed_python",
            ToolResult("error", "PYTHON_SANDBOX_TIMEOUT", message, retriable=True, latency_ms=int((time.time() - started) * 1000), source="employee_workspace"),
            args,
        )
        return message
    except Exception as exc:
        message = f"Python沙盒执行失败：{exc}"
        _emit(
            "run_sandboxed_python",
            ToolResult("error", "PYTHON_SANDBOX_FAILED", message, retriable=False, latency_ms=int((time.time() - started) * 1000), source="employee_workspace"),
            args,
        )
        return message
