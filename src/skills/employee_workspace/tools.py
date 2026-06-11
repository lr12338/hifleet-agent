"""Employee-only file and Python sandbox tools."""
from __future__ import annotations

import ast
import json
import logging
import os
import shutil
import time
import uuid
from pathlib import Path
from typing import Any, Dict
from urllib.parse import urlparse

import docker
import pandas as pd
import requests
from docker.errors import DockerException, ImageNotFound
from langchain.tools import tool

from agents.profiles import get_current_agent_profile_id
from coze_coding_utils.log.write_log import request_context
from observability import schedule_agent_error_log
from skills.common.tool_result import ToolResult, emit_tool_metric
from utils.session_state import get_current_session_id

logger = logging.getLogger(__name__)

WORKSPACE_ROOT = Path(os.getenv("COZE_WORKSPACE_PATH", Path.cwd())).resolve()
ARTIFACT_ROOT = Path(os.getenv("HIFLEET_AGENT_ARTIFACT_DIR", "/tmp/hifleet_agent_artifacts")).resolve()
SANDBOX_TIMEOUT_SEC = int(os.getenv("HIFLEET_PY_SANDBOX_TIMEOUT_SEC", "20"))
MAX_CODE_CHARS = int(os.getenv("HIFLEET_PY_SANDBOX_MAX_CODE_CHARS", "12000"))
MAX_STDIO_CHARS = int(os.getenv("HIFLEET_PY_SANDBOX_STDIO_CHARS", "8000"))
DOCKER_IMAGE = os.getenv("HIFLEET_PY_SANDBOX_IMAGE", "python:3.11-slim")
DOCKER_IMAGE_CANDIDATES = os.getenv("HIFLEET_PY_SANDBOX_IMAGE_CANDIDATES", "")
DOCKER_AUTO_PULL = os.getenv("HIFLEET_PY_SANDBOX_AUTO_PULL", "1").strip().lower() in {"1", "true", "yes", "on"}
SANDBOX_VOLUME = os.getenv("HIFLEET_PY_SANDBOX_VOLUME", str(ARTIFACT_ROOT))
SANDBOX_VOLUME_MOUNT = os.getenv("HIFLEET_PY_SANDBOX_VOLUME_MOUNT", "/workspace/artifacts")
DOCKER_MEM_LIMIT = os.getenv("HIFLEET_PY_SANDBOX_MEM_LIMIT", "512m")
DOCKER_CPU_QUOTA = int(os.getenv("HIFLEET_PY_SANDBOX_CPU_QUOTA", "50000"))
DOCKER_USER = os.getenv("HIFLEET_PY_SANDBOX_USER", "1000:1000")
PUBLIC_FILE_TIMEOUT_SEC = int(os.getenv("HIFLEET_PUBLIC_FILE_TIMEOUT_SEC", "30"))
PUBLIC_FILE_MAX_MB = int(os.getenv("HIFLEET_PUBLIC_FILE_MAX_MB", "100"))
ALLOWED_PUBLIC_SUFFIXES = {".csv", ".xls", ".xlsx"}
ALLOWED_IMPORTS = {
    "collections",
    "csv",
    "datetime",
    "json",
    "math",
    "numpy",
    "openpyxl",
    "os",
    "pandas",
    "pathlib",
    "statistics",
    "typing",
}
BLOCKED_BUILTINS = {"compile", "eval", "exec", "getattr", "setattr", "__import__"}
_RESOLVED_SANDBOX_IMAGE = ""


class SandboxSecurityError(PermissionError):
    code = "ERR_SANDBOX_SECURITY"


class SafePythonVisitor(ast.NodeVisitor):
    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            root_name = alias.name.split(".")[0]
            if root_name not in ALLOWED_IMPORTS:
                raise SandboxSecurityError(f"import blocked: {alias.name}")

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        root_name = (node.module or "").split(".")[0]
        if root_name not in ALLOWED_IMPORTS:
            raise SandboxSecurityError(f"from import blocked: {node.module}")

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Name) and node.func.id in BLOCKED_BUILTINS:
            raise SandboxSecurityError(f"builtin blocked: {node.func.id}")
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr.startswith("__"):
            raise SandboxSecurityError(f"dunder access blocked: {node.attr}")
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if node.id.startswith("__"):
            raise SandboxSecurityError(f"dunder name blocked: {node.id}")
        self.generic_visit(node)


def _emit(tool_name: str, result: ToolResult, tool_args: Dict[str, Any] | None = None, *, layer_trace: Dict[str, Any] | None = None) -> None:
    ctx = request_context.get()
    trace = {"sandbox": True, **(layer_trace or {})}
    emit_tool_metric(
        tool_name,
        getattr(ctx, "run_id", "") if ctx else "",
        result,
        tool_args=tool_args,
        layer_trace=trace,
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


def _ast_guard(code: str) -> None:
    tree = ast.parse(code, mode="exec")
    SafePythonVisitor().visit(tree)


def _current_context() -> tuple[str, str, str]:
    ctx = request_context.get()
    run_id = getattr(ctx, "run_id", "") if ctx else ""
    session_id = get_current_session_id() or ""
    route = getattr(ctx, "method", "") if ctx else ""
    return run_id, session_id, route


def _log_agent_error(error_code: str, message: str, *, stack_trace: str = "", node_name: str = "employee_workspace", attempt: int = 0) -> None:
    run_id, session_id, route = _current_context()
    if not run_id:
        return
    try:
        schedule_agent_error_log(
            {
                "run_id": run_id,
                "session_id": session_id or None,
                "route": f"/{route}" if route else None,
                "error_code": error_code,
                "error_message": message,
                "stack_trace": stack_trace or None,
                "error_category": "security" if error_code == SandboxSecurityError.code else None,
                "node_name": node_name,
                "attempt": int(attempt or 0),
            }
        )
    except Exception as exc:
        logger.warning(f"[EmployeeWorkspace] failed to schedule agent error log: {exc}")


def _prepare_job_dir() -> tuple[Path, Path, Path, Path]:
    job_dir = ARTIFACT_ROOT / f"sandbox_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"
    output_dir = job_dir / "output"
    input_dir = job_dir / "input"
    script_path = job_dir / "task.py"
    output_dir.mkdir(parents=True, exist_ok=True)
    input_dir.mkdir(parents=True, exist_ok=True)
    return job_dir, output_dir, input_dir, script_path


def _mount_spec() -> dict[str, dict[str, str]]:
    return {SANDBOX_VOLUME: {"bind": SANDBOX_VOLUME_MOUNT, "mode": "rw"}}


def _container_paths(job_dir: Path, input_file_name: str = "") -> tuple[str, str, str]:
    container_job_dir = f"{SANDBOX_VOLUME_MOUNT.rstrip('/')}/{job_dir.name}"
    input_path = f"{container_job_dir}/input/{input_file_name}" if input_file_name else ""
    return f"{container_job_dir}/task.py", f"{container_job_dir}/output", input_path


def _sandbox_image_candidates() -> list[str]:
    candidates = [DOCKER_IMAGE]
    if DOCKER_IMAGE_CANDIDATES:
        candidates.extend([item.strip() for item in DOCKER_IMAGE_CANDIDATES.split(",") if item.strip()])
    deduped: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        if candidate not in seen:
            deduped.append(candidate)
            seen.add(candidate)
    return deduped


def _resolve_sandbox_image(client: docker.DockerClient) -> str:
    global _RESOLVED_SANDBOX_IMAGE
    if _RESOLVED_SANDBOX_IMAGE:
        return _RESOLVED_SANDBOX_IMAGE

    errors: list[str] = []
    for candidate in _sandbox_image_candidates():
        try:
            client.images.get(candidate)
            _RESOLVED_SANDBOX_IMAGE = candidate
            return candidate
        except ImageNotFound:
            errors.append(f"missing:{candidate}")
        except Exception as exc:
            errors.append(f"inspect:{candidate}:{exc}")

    if DOCKER_AUTO_PULL:
        for candidate in _sandbox_image_candidates():
            try:
                client.images.pull(candidate)
                _RESOLVED_SANDBOX_IMAGE = candidate
                return candidate
            except Exception as exc:
                errors.append(f"pull:{candidate}:{exc}")

    raise DockerException("no usable sandbox image found; candidates=" + ", ".join(_sandbox_image_candidates()) + "; errors=" + " | ".join(errors))


def _sanitize_filename(filename: str) -> str:
    base = Path(filename or "file").name
    sanitized = "".join(ch if ch.isalnum() or ch in {".", "_", "-"} else "_" for ch in base).strip("._")
    return sanitized or "file"


def _download_target_path(file_url: str, filename: str = "") -> Path:
    parsed = urlparse(file_url)
    guessed = filename or Path(parsed.path).name or f"download_{uuid.uuid4().hex}.bin"
    safe_name = _sanitize_filename(guessed)
    return ARTIFACT_ROOT / "downloads" / f"{uuid.uuid4().hex}_{safe_name}"


def _copy_input_file(source_path: str, input_dir: Path) -> str:
    resolved = _resolve_allowed_path(source_path)
    if not resolved.exists() or not resolved.is_file():
        raise FileNotFoundError(f"input file not found: {resolved}")
    copied_name = _sanitize_filename(resolved.name)
    shutil.copy2(resolved, input_dir / copied_name)
    return copied_name


def _run_in_docker(job_dir: Path, input_file_name: str = "") -> dict[str, Any]:
    client = docker.from_env()
    script_path_in_container, output_dir_in_container, input_path_in_container = _container_paths(job_dir, input_file_name=input_file_name)
    container = None
    started = time.time()
    try:
        image_name = _resolve_sandbox_image(client)
        container = client.containers.run(
            image=image_name,
            command=["python", script_path_in_container],
            detach=True,
            network_mode="none",
            read_only=True,
            mem_limit=DOCKER_MEM_LIMIT,
            cpu_quota=DOCKER_CPU_QUOTA,
            user=DOCKER_USER,
            working_dir=f"{SANDBOX_VOLUME_MOUNT.rstrip('/')}/{job_dir.name}",
            volumes=_mount_spec(),
            environment={
                "ARTIFACT_DIR": output_dir_in_container,
                "INPUT_FILE": input_path_in_container,
                "PYTHONNOUSERSITE": "1",
                "PYTHONUNBUFFERED": "1",
            },
            tmpfs={"/tmp": "rw,noexec,nosuid,size=64m"},
        )
        wait_result = container.wait(timeout=SANDBOX_TIMEOUT_SEC)
        stdout = container.logs(stdout=True, stderr=False).decode("utf-8", errors="ignore")[-MAX_STDIO_CHARS:]
        stderr = container.logs(stdout=False, stderr=True).decode("utf-8", errors="ignore")[-MAX_STDIO_CHARS:]
        return {
            "exit_code": int(wait_result.get("StatusCode", 1)),
            "stdout": stdout,
            "stderr": stderr,
            "container_id": container.id,
            "image": image_name,
            "elapsed_ms": int((time.time() - started) * 1000),
        }
    except Exception as exc:
        if container is not None:
            try:
                container.remove(force=True)
            except Exception:
                pass
        raise DockerException(f"docker sandbox failed: {exc}") from exc
    finally:
        if container is not None:
            try:
                container.remove(force=True)
            except Exception:
                pass
        try:
            client.close()
        except Exception:
            pass


def _validate_expected_artifact(expected_artifact: str, output_dir: Path) -> dict[str, Any]:
    expected = (expected_artifact or "").strip()
    if not expected:
        return {"ok": True, "skipped": True, "reason": "no expected_artifact provided"}

    candidate = Path(expected)
    target = (output_dir / candidate.name).resolve() if candidate.is_absolute() else (output_dir / candidate).resolve()
    if not str(target).startswith(str(output_dir.resolve())):
        return {"ok": False, "reason": "expected_artifact escaped artifact directory", "path": str(target)}
    if not target.exists():
        return {"ok": False, "reason": "expected_artifact missing", "path": str(target)}
    size = int(target.stat().st_size)
    if size <= 0:
        return {"ok": False, "reason": "expected_artifact empty", "path": str(target), "size": size}
    return {"ok": True, "path": str(target), "size": size}


@tool
def download_public_file_to_artifact(file_url: str, filename: str = "") -> str:
    """Download a public CSV/XLS/XLSX file into the artifact directory for employee analysis."""
    started = time.time()
    args = {"file_url": file_url, "filename": filename}
    try:
        if not _is_employee_profile():
            raise PermissionError("download_public_file_to_artifact is only available in employee_assistant profile")
        parsed = urlparse(file_url)
        if parsed.scheme not in {"http", "https"}:
            raise ValueError("only public http/https file URLs are supported")
        target_path = _download_target_path(file_url, filename=filename)
        suffix = target_path.suffix.lower()
        if suffix not in ALLOWED_PUBLIC_SUFFIXES:
            raise ValueError("only CSV/XLS/XLSX public files are supported")
        target_path.parent.mkdir(parents=True, exist_ok=True)
        with requests.get(file_url, stream=True, timeout=PUBLIC_FILE_TIMEOUT_SEC) as response:
            response.raise_for_status()
            downloaded = 0
            with target_path.open("wb") as f:
                for chunk in response.iter_content(chunk_size=64 * 1024):
                    if not chunk:
                        continue
                    downloaded += len(chunk)
                    if downloaded > PUBLIC_FILE_MAX_MB * 1024 * 1024:
                        raise ValueError(f"download exceeds {PUBLIC_FILE_MAX_MB}MB limit")
                    f.write(chunk)
            payload = {
                "file_url": file_url,
                "local_path": str(target_path),
                "size_bytes": downloaded,
                "content_type": response.headers.get("content-type", ""),
                "status_code": response.status_code,
            }
        message = json.dumps(payload, ensure_ascii=False)
        _emit(
            "download_public_file_to_artifact",
            ToolResult("ok", "PUBLIC_FILE_DOWNLOADED", message, latency_ms=int((time.time() - started) * 1000), source="employee_workspace", data=payload),
            args,
            layer_trace={"phase": "download", "session_id": get_current_session_id() or ""},
        )
        return message
    except Exception as exc:
        message = f"公共文件下载失败：{exc}"
        _emit(
            "download_public_file_to_artifact",
            ToolResult("error", "PUBLIC_FILE_DOWNLOAD_FAILED", message, retriable=True, latency_ms=int((time.time() - started) * 1000), source="employee_workspace"),
            args,
            layer_trace={"phase": "download", "session_id": get_current_session_id() or ""},
        )
        return message


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

        preview_df = df.head(max(1, min(int(max_rows), 20)))
        preview_records = preview_df.astype(object).where(pd.notna(preview_df), None).to_dict(orient="records")
        payload = {
            "file": str(path),
            "shape": [int(df.shape[0]), int(df.shape[1])],
            "columns": [str(c) for c in df.columns],
            "dtypes": {str(col): str(dtype) for col, dtype in df.dtypes.items()},
            "missing_values": {str(col): int(df[col].isna().sum()) for col in df.columns},
            "preview": preview_records,
            "schema": [
                {"name": str(col), "dtype": str(df[col].dtype), "missing": int(df[col].isna().sum())}
                for col in df.columns
            ],
        }
        message = json.dumps(payload, ensure_ascii=False, default=str)
        _emit(
            "inspect_tabular_file",
            ToolResult("ok", "TABULAR_FILE_INSPECTED", message, latency_ms=int((time.time() - started) * 1000), source="employee_workspace", data=payload),
            args,
            layer_trace={"phase": "plan", "session_id": get_current_session_id() or ""},
        )
        return message
    except Exception as exc:
        message = f"文件检查失败：{exc}"
        _emit(
            "inspect_tabular_file",
            ToolResult("error", "TABULAR_FILE_INSPECT_FAILED", message, retriable=False, latency_ms=int((time.time() - started) * 1000), source="employee_workspace"),
            args,
            layer_trace={"phase": "plan", "session_id": get_current_session_id() or ""},
        )
        return message


@tool
def run_sandboxed_python(code: str, expected_artifact: str = "", attempt: int = 0, input_file_path: str = "") -> str:
    """
    Run a short Python script in a Docker sibling sandbox for internal analysis.

    Args:
        code: Python code. Generated artifacts should be written under ARTIFACT_DIR.
        expected_artifact: Expected artifact name relative to ARTIFACT_DIR.
        attempt: Current loop attempt for observability.
        input_file_path: Optional local source file copied into the sandbox as INPUT_FILE.
    """
    started = time.time()
    args = {
        "code_chars": len(code or ""),
        "expected_artifact": expected_artifact,
        "attempt": int(attempt or 0),
        "input_file_path": input_file_path,
    }
    try:
        if not _is_employee_profile():
            raise PermissionError("run_sandboxed_python is only available in employee_assistant profile")
        if not code or len(code) > MAX_CODE_CHARS:
            raise ValueError(f"code must be non-empty and <= {MAX_CODE_CHARS} chars")

        try:
            _ast_guard(code)
        except SandboxSecurityError as exc:
            _log_agent_error(SandboxSecurityError.code, str(exc), node_name="employee_ast_guard", attempt=attempt)
            payload = {"error_code": SandboxSecurityError.code, "error_message": str(exc)}
            message = json.dumps(payload, ensure_ascii=False)
            _emit(
                "run_sandboxed_python",
                ToolResult("error", SandboxSecurityError.code, message, retriable=False, latency_ms=int((time.time() - started) * 1000), source="employee_workspace", data=payload),
                args,
                layer_trace={"phase": "check", "attempt": int(attempt or 0), "session_id": get_current_session_id() or "", "security_blocked": True},
            )
            return message

        ARTIFACT_ROOT.mkdir(parents=True, exist_ok=True)
        job_dir, output_dir, input_dir, script_path = _prepare_job_dir()
        input_file_name = _copy_input_file(input_file_path, input_dir) if input_file_path else ""
        prelude = (
            "import os\n"
            "from pathlib import Path\n\n"
            "ARTIFACT_DIR = Path(os.environ['ARTIFACT_DIR']).resolve()\n"
            "ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)\n"
            "_input = os.environ.get('INPUT_FILE', '').strip()\n"
            "INPUT_FILE = Path(_input).resolve() if _input else None\n"
            "print(f'[sandbox] artifact_dir={ARTIFACT_DIR}')\n"
            "print(f'[sandbox] input_file={INPUT_FILE}')\n"
        )
        script_path.write_text(prelude + "\n" + code, encoding="utf-8")

        runtime = _run_in_docker(job_dir, input_file_name=input_file_name)
        artifact_check = _validate_expected_artifact(expected_artifact, output_dir)
        artifacts = [str(p) for p in output_dir.glob("**/*") if p.is_file()]
        payload = {
            "exit_code": runtime["exit_code"],
            "stdout": runtime["stdout"],
            "stderr": runtime["stderr"],
            "artifact_dir": str(output_dir),
            "artifacts": artifacts[:50],
            "artifact_check": artifact_check,
            "expected_artifact": expected_artifact,
            "input_file_path": input_file_path,
            "container_id": runtime["container_id"],
            "image": runtime["image"],
        }
        success = runtime["exit_code"] == 0 and artifact_check.get("ok", True)
        code_name = "PYTHON_SANDBOX_OK"
        if runtime["exit_code"] != 0:
            code_name = "PYTHON_SANDBOX_NONZERO"
        elif not artifact_check.get("ok", True):
            code_name = "PYTHON_SANDBOX_ARTIFACT_CHECK_FAILED"
        message = json.dumps(payload, ensure_ascii=False)
        _emit(
            "run_sandboxed_python",
            ToolResult("ok" if success else "error", code_name, message, retriable=not success, latency_ms=int((time.time() - started) * 1000), source="employee_workspace", data=payload),
            args,
            layer_trace={"phase": "check", "attempt": int(attempt or 0), "session_id": get_current_session_id() or "", "security_blocked": False},
        )
        return message
    except DockerException as exc:
        message = f"Python沙盒执行失败：{exc}"
        _emit(
            "run_sandboxed_python",
            ToolResult("error", "PYTHON_SANDBOX_DOCKER_FAILED", message, retriable=True, latency_ms=int((time.time() - started) * 1000), source="employee_workspace"),
            args,
            layer_trace={"phase": "check", "attempt": int(attempt or 0), "session_id": get_current_session_id() or ""},
        )
        return message
    except Exception as exc:
        message = f"Python沙盒执行失败：{exc}"
        _emit(
            "run_sandboxed_python",
            ToolResult("error", "PYTHON_SANDBOX_FAILED", message, retriable=False, latency_ms=int((time.time() - started) * 1000), source="employee_workspace"),
            args,
            layer_trace={"phase": "check", "attempt": int(attempt or 0), "session_id": get_current_session_id() or ""},
        )
        return message
