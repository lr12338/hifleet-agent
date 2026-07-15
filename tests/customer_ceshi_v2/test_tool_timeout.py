import time

from agents.customer_ceshi_v2.contracts import ToolCall
from agents.customer_ceshi_v2.tools import CapabilityRegistry


class SlowTool:
    name = "slow"
    description = "slow"
    args_schema = None

    def invoke(self, arguments):
        time.sleep(0.05)
        return "late"


def test_tool_timeout_is_retryable_observation(monkeypatch):
    registry = CapabilityRegistry(tools=[SlowTool()])
    descriptor = registry.descriptors()[0]
    monkeypatch.setattr(registry, "descriptors", lambda: [type(descriptor)(**{**descriptor.__dict__, "timeout_seconds": 0.001})])

    result = registry.invoke(ToolCall(name="slow", arguments={}))

    assert result.status == "timeout"
    assert result.retry_allowed is True
