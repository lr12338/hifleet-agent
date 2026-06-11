#!/usr/bin/env python3
"""Smoke tests for employee_assistant plan/act/check/loop graph behavior."""
import asyncio
import json
import os
import sys
from typing import Any

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from agents import agent as agent_module
from agents.profiles import set_current_agent_profile
from coze_coding_utils.runtime_ctx.context import new_context
from langchain_core.messages import AIMessage, HumanMessage
from skills.employee_workspace import tools as employee_workspace_tools


class FakeCodegenLLM:
    async def ainvoke(self, _messages: list[Any]) -> AIMessage:
        return AIMessage(content="```python\nprint('loop generated code')\n```")


class FakeDelegateAgent:
    async def ainvoke(self, payload: dict[str, Any], context: Any = None) -> dict[str, Any]:
        return {
            "status": "delegated",
            "messages": [AIMessage(content=f"delegated: {payload.get('messages')}")],
        }


class FakeTool:
    def __init__(self, fn):
        self._fn = fn

    def invoke(self, args: dict[str, Any]) -> str:
        return self._fn(args)


async def run_employee_loop_smoke() -> None:
    set_current_agent_profile("employee_assistant")
    ctx = new_context(method="test_employee_agent_loop", headers={"x-agent-profile": "employee_assistant"})

    original_build_standard_agent = agent_module._build_standard_agent
    original_build_llm = agent_module._build_llm
    original_get_memory_saver = agent_module.get_memory_saver
    original_inspect_tool = employee_workspace_tools.inspect_tabular_file
    original_run_tool = employee_workspace_tools.run_sandboxed_python
    original_download_tool = employee_workspace_tools.download_public_file_to_artifact

    attempts: list[int] = []

    def fake_download(_args: dict[str, Any]) -> str:
        return json.dumps({"local_path": "/tmp/downloaded/orders.xlsx"}, ensure_ascii=False)

    def fake_inspect(args: dict[str, Any]) -> str:
        assert args["file_path"] == "/tmp/downloaded/orders.xlsx"
        return json.dumps(
            {
                "file": "/tmp/downloaded/orders.xlsx",
                "columns": ["customer", "amount"],
                "dtypes": {"customer": "object", "amount": "float64"},
                "missing_values": {"customer": 0, "amount": 0},
                "preview": [{"customer": "A", "amount": 10.0}],
                "schema": [
                    {"name": "customer", "dtype": "object", "missing": 0},
                    {"name": "amount", "dtype": "float64", "missing": 0},
                ],
            },
            ensure_ascii=False,
        )

    def fake_run(args: dict[str, Any]) -> str:
        attempts.append(int(args["attempt"]))
        assert args["input_file_path"] == "/tmp/downloaded/orders.xlsx"
        if len(attempts) == 1:
            return json.dumps(
                {
                    "exit_code": 1,
                    "stdout": "",
                    "stderr": "Traceback: wrong column",
                    "artifact_check": {"ok": True},
                },
                ensure_ascii=False,
            )
        return json.dumps(
            {
                "exit_code": 0,
                "stdout": "done",
                "stderr": "",
                "artifact_check": {"ok": True},
                "artifacts": ["/workspace/artifacts/output/result.xlsx"],
            },
            ensure_ascii=False,
        )

    agent_module._build_standard_agent = lambda *args, **kwargs: FakeDelegateAgent()
    agent_module._build_llm = lambda *args, **kwargs: FakeCodegenLLM()
    agent_module.get_memory_saver = lambda: None
    employee_workspace_tools.download_public_file_to_artifact = FakeTool(fake_download)
    employee_workspace_tools.inspect_tabular_file = FakeTool(fake_inspect)
    employee_workspace_tools.run_sandboxed_python = FakeTool(fake_run)

    try:
        graph = agent_module.build_agent(ctx)
        result = await asyncio.wait_for(
            graph.ainvoke(
                {
                    "messages": [HumanMessage(content="分析 https://example.com/orders.xlsx 并生成 result.xlsx")],
                    "session_id": "employee-loop-smoke",
                    "user_id": "u1",
                    "source_channel": "employee_api",
                    "agent_profile": "employee_assistant",
                },
                context=ctx,
            ),
            timeout=10,
        )
    finally:
        agent_module._build_standard_agent = original_build_standard_agent
        agent_module._build_llm = original_build_llm
        agent_module.get_memory_saver = original_get_memory_saver
        employee_workspace_tools.download_public_file_to_artifact = original_download_tool
        employee_workspace_tools.inspect_tabular_file = original_inspect_tool
        employee_workspace_tools.run_sandboxed_python = original_run_tool

    assert attempts == [1, 2]
    assert result["status"] == "success"
    assert "已完成受控数据任务" in result["messages"][-1].content


async def run_delegate_smoke() -> None:
    set_current_agent_profile("employee_assistant")
    ctx = new_context(method="test_employee_agent_delegate", headers={"x-agent-profile": "employee_assistant"})

    original_build_standard_agent = agent_module._build_standard_agent
    original_build_llm = agent_module._build_llm
    original_get_memory_saver = agent_module.get_memory_saver

    agent_module._build_standard_agent = lambda *args, **kwargs: FakeDelegateAgent()
    agent_module._build_llm = lambda *args, **kwargs: FakeCodegenLLM()
    agent_module.get_memory_saver = lambda: None
    try:
        graph = agent_module.build_agent(ctx)
        result = await asyncio.wait_for(
            graph.ainvoke(
                {
                    "messages": [HumanMessage(content="帮我总结一下 HiFleet 轨迹功能")],
                    "session_id": "employee-delegate-smoke",
                    "user_id": "u2",
                    "source_channel": "employee_api",
                    "agent_profile": "employee_assistant",
                },
                context=ctx,
            ),
            timeout=10,
        )
    finally:
        agent_module._build_standard_agent = original_build_standard_agent
        agent_module._build_llm = original_build_llm
        agent_module.get_memory_saver = original_get_memory_saver

    assert result["status"] == "delegated"


def main() -> int:
    asyncio.run(run_employee_loop_smoke())
    asyncio.run(run_delegate_smoke())
    print("employee agent loop smoke tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
