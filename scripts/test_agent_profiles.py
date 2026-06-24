#!/usr/bin/env python3
"""Smoke tests for profile routing and employee sandbox gating."""
import json
import os
import sys
from typing import Any

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from agents.profiles import get_profile, resolve_profile_id, set_current_agent_profile
from coze_coding_utils.log.write_log import request_context
from coze_coding_utils.runtime_ctx.context import new_context
from skills.employee_workspace import tools as employee_workspace_tools
from skills.skill_loader import SkillLoader


def tool_names(profile_id: str):
    profile = get_profile(profile_id)
    disabled = set(profile.disabled_tools or [])
    return [
        tool.name
        for tool in SkillLoader.get_tools_by_skill_names(profile.skills)
        if tool.name not in disabled
    ]


def main() -> int:
    assert resolve_profile_id(source_channel="websdk") == "customer_support"
    assert resolve_profile_id(source_channel="hifleet_mp", requested_profile="employee_assistant") == "customer_support"
    assert resolve_profile_id(source_channel="employee_api") == "customer_support"
    assert resolve_profile_id(source_channel="websdk", requested_profile="employee_assistant") == "customer_support"
    assert resolve_profile_id(headers={"x-agent-profile": "employee_assistant"}) == "customer_support"
    assert resolve_profile_id(requested_profile="customer_ceshi") == "customer_ceshi"

    customer_tools = tool_names("customer_support")
    employee_tools = tool_names("customer_ceshi")
    assert "local_kb_search" in customer_tools
    assert "web_search" in customer_tools
    assert "web_search_agent_browser" in customer_tools
    assert "upsert_local_kb_entry" in customer_tools
    assert "run_sandboxed_python" not in customer_tools
    assert "download_public_file_to_artifact" not in customer_tools
    assert "inspect_tabular_file" not in customer_tools
    assert "inspect_customer_file" not in customer_tools
    assert "upload_customer_artifact" not in customer_tools
    assert "upload_ship_position" in customer_tools
    assert "update_ship_static_info" in customer_tools
    assert "run_sandboxed_python" in employee_tools
    assert "upload_ship_position" in employee_tools
    assert "upsert_local_kb_entry" in employee_tools

    request_context.set(new_context(method="test_agent_profiles"))
    set_current_agent_profile("customer_support")
    blocked = employee_workspace_tools.run_sandboxed_python.invoke({"code": "print(1 + 1)"})
    assert "only available in customer_ceshi" in blocked

    set_current_agent_profile("customer_ceshi")
    original_run_in_docker = employee_workspace_tools._run_in_docker

    def fake_run_in_docker(_job_dir: Any, input_file_name: str = "") -> dict[str, Any]:
        assert input_file_name == ""
        return {
            "exit_code": 0,
            "stdout": "2\n",
            "stderr": "",
            "container_id": "test-container",
            "image": "python:3.11-slim",
            "elapsed_ms": 1,
        }

    employee_workspace_tools._run_in_docker = fake_run_in_docker
    try:
        allowed = json.loads(employee_workspace_tools.run_sandboxed_python.invoke({"code": "print(1 + 1)"}))
    finally:
        employee_workspace_tools._run_in_docker = original_run_in_docker

    assert allowed["exit_code"] == 0
    assert allowed["stdout"].strip() == "2"

    print("agent profile smoke tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
