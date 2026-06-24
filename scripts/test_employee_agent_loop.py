#!/usr/bin/env python3
"""Smoke tests for the employee_assistant compatibility alias entrypoint."""
import asyncio
import os
import sys
from typing import Any
from types import SimpleNamespace

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from agents import agent as agent_module
from agents.profiles import set_current_agent_profile
from langchain_core.messages import AIMessage, HumanMessage


class FakeDelegateAgent:
    def __init__(self):
        self.payloads: list[dict[str, Any]] = []

    async def ainvoke(self, payload: dict[str, Any], context: Any = None) -> dict[str, Any]:
        self.payloads.append(payload)
        return {
            "status": "delegated",
            "messages": [AIMessage(content=f"standard agent: {payload.get('intent_hint', '')}")],
        }


async def run_employee_standard_agent_smoke() -> None:
    set_current_agent_profile("employee_assistant")
    ctx = SimpleNamespace(
        run_id="test_employee_agent_standard",
        headers={"x-agent-profile": "employee_assistant", "x-intent-hint": "knowledge"},
    )

    original_build_standard_agent = agent_module._build_standard_agent
    original_build_customer_support_agent = agent_module._build_lightweight_customer_support_agent

    fake_agent = FakeDelegateAgent()
    build_calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def fake_build_customer_support_agent(*args, **kwargs):
        build_calls.append((args, kwargs))
        return fake_agent

    def fail_build_standard_agent(*args, **kwargs):
        raise AssertionError("customer_ceshi-only standard path should not be used for employee_assistant alias")

    agent_module._build_standard_agent = fail_build_standard_agent
    agent_module._build_lightweight_customer_support_agent = fake_build_customer_support_agent
    try:
        graph = agent_module.build_agent(ctx)
        assert graph is fake_agent
        result = await asyncio.wait_for(
            graph.ainvoke(
                {
                    "messages": [HumanMessage(content="查询育明船位")],
                    "session_id": "employee-standard-smoke",
                    "user_id": "u2",
                    "source_channel": "employee_api",
                    "agent_profile": "employee_assistant",
                    "intent_hint": "knowledge",
                },
                context=ctx,
            ),
            timeout=10,
        )
    finally:
        agent_module._build_standard_agent = original_build_standard_agent
        agent_module._build_lightweight_customer_support_agent = original_build_customer_support_agent

    assert result["status"] == "delegated"
    assert build_calls
    assert build_calls[-1][0][3].profile_id == "customer_support"
    assert build_calls[-1][1]["intent_hint"] == "knowledge"
    assert fake_agent.payloads[-1]["messages"][-1].content == "查询育明船位"


def main() -> int:
    asyncio.run(run_employee_standard_agent_smoke())
    print("employee standard agent smoke tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
