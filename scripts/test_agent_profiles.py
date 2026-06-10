#!/usr/bin/env python3
"""Smoke tests for profile routing and employee sandbox gating."""
import json
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from agents.profiles import get_profile, resolve_profile_id, set_current_agent_profile
from coze_coding_utils.log.write_log import request_context
from coze_coding_utils.runtime_ctx.context import new_context
from skills.employee_workspace.tools import run_sandboxed_python
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
    assert resolve_profile_id(source_channel="wechat_mp") == "customer_support"
    assert resolve_profile_id(source_channel="admin_panel") == "employee_assistant"
    assert resolve_profile_id(source_channel="employee_api") == "employee_assistant"

    customer_tools = tool_names("customer_support")
    employee_tools = tool_names("employee_assistant")
    assert "smart_search" in customer_tools
    assert "run_sandboxed_python" not in customer_tools
    assert "upload_ship_position" not in customer_tools
    assert "update_ship_static_info" not in customer_tools
    assert "run_sandboxed_python" in employee_tools
    assert "upload_ship_position" in employee_tools

    request_context.set(new_context(method="test_agent_profiles"))
    set_current_agent_profile("customer_support")
    blocked = run_sandboxed_python.invoke({"code": "print(1 + 1)"})
    assert "only available in employee_assistant" in blocked

    set_current_agent_profile("employee_assistant")
    allowed = json.loads(run_sandboxed_python.invoke({"code": "print(1 + 1)"}))
    assert allowed["returncode"] == 0
    assert allowed["stdout"].strip() == "2"

    print("agent profile smoke tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
