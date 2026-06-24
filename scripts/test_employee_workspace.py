#!/usr/bin/env python3
"""Smoke tests for employee workspace schema inspection, public download, and sandbox controls."""
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from agents.profiles import set_current_agent_profile
from coze_coding_utils.log.write_log import request_context
from coze_coding_utils.runtime_ctx.context import new_context
from skills.employee_workspace import tools as employee_workspace_tools


class FakeResponse:
    def __init__(self, body: bytes, content_type: str = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"):
        self._body = body
        self.status_code = 200
        self.headers = {"content-type": content_type}

    def raise_for_status(self) -> None:
        return

    def iter_content(self, chunk_size: int = 65536):
        for idx in range(0, len(self._body), chunk_size):
            yield self._body[idx : idx + chunk_size]

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def main() -> int:
    request_context.set(new_context(method="test_employee_workspace"))
    set_current_agent_profile("customer_ceshi")

    with tempfile.TemporaryDirectory(prefix="employee_ws_") as tmpdir:
        csv_path = Path(tmpdir) / "quotes.csv"
        csv_path.write_text(
            "customer,amount,status\nA,10,ok\nB,,pending\n",
            encoding="utf-8",
        )
        inspected = json.loads(
            employee_workspace_tools.inspect_tabular_file.invoke(
                {"file_path": str(csv_path), "max_rows": 2}
            )
        )
        assert inspected["columns"] == ["customer", "amount", "status"]
        assert "dtypes" in inspected
        assert inspected["missing_values"]["amount"] == 1
        assert len(inspected["preview"]) == 2

    blocked = json.loads(
        employee_workspace_tools.run_sandboxed_python.invoke(
            {"code": "import subprocess\nprint(1)", "attempt": 1}
        )
    )
    assert blocked["error_code"] == "ERR_SANDBOX_SECURITY"

    original_get = employee_workspace_tools.requests.get
    employee_workspace_tools.requests.get = lambda *args, **kwargs: FakeResponse(b"fake-xlsx-body")
    try:
        downloaded = json.loads(
            employee_workspace_tools.download_public_file_to_artifact.invoke(
                {"file_url": "https://example.com/pricing.xlsx"}
            )
        )
    finally:
        employee_workspace_tools.requests.get = original_get
    assert downloaded["local_path"].endswith(".xlsx")
    assert Path(downloaded["local_path"]).exists()

    original_run_in_docker = employee_workspace_tools._run_in_docker

    def fake_ok(job_dir: Any, input_file_name: str = "") -> dict[str, Any]:
        assert input_file_name == "quotes.csv"
        copied_input = Path(job_dir) / "input" / input_file_name
        assert copied_input.exists()
        return {
            "exit_code": 0,
            "stdout": copied_input.read_text(encoding="utf-8"),
            "stderr": "",
            "container_id": "ok-container",
            "image": "python:3.11-slim",
            "elapsed_ms": 1,
        }

    def fake_nonzero(_job_dir: Any, input_file_name: str = "") -> dict[str, Any]:
        return {
            "exit_code": 1,
            "stdout": "",
            "stderr": "Traceback: bad column\n",
            "container_id": "fail-container",
            "image": "python:3.11-slim",
            "elapsed_ms": 1,
        }

    with tempfile.TemporaryDirectory(prefix="employee_input_") as tmpdir:
        input_path = Path(tmpdir) / "quotes.csv"
        input_path.write_text("customer,amount\nA,10\n", encoding="utf-8")
        employee_workspace_tools._run_in_docker = fake_ok
        try:
            ok_result = json.loads(
                employee_workspace_tools.run_sandboxed_python.invoke(
                    {"code": "print('analysis ok')", "attempt": 1, "input_file_path": str(input_path)}
                )
            )
        finally:
            employee_workspace_tools._run_in_docker = original_run_in_docker
        assert ok_result["exit_code"] == 0
        assert "customer,amount" in ok_result["stdout"]

    employee_workspace_tools._run_in_docker = fake_nonzero
    try:
        nonzero_result = json.loads(
            employee_workspace_tools.run_sandboxed_python.invoke(
                {"code": "print('boom')", "attempt": 2}
            )
        )
    finally:
        employee_workspace_tools._run_in_docker = original_run_in_docker
    assert nonzero_result["exit_code"] == 1
    assert "bad column" in nonzero_result["stderr"]

    print("employee workspace smoke tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
