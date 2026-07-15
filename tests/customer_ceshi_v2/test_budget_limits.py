from agents.customer_ceshi_v2.contracts import ToolCall
from agents.customer_ceshi_v2.tools import CapabilityRegistry


def test_registry_does_not_expose_real_write_capabilities():
    registry = CapabilityRegistry(tools=[])

    assert registry.invoke(ToolCall(name="update_ship_static_info", arguments={})).status == "forbidden"
