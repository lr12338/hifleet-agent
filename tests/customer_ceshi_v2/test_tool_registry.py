from agents.customer_ceshi_v2.contracts import ToolCall
from agents.customer_ceshi_v2.tools import CapabilityRegistry


def test_registry_hides_real_write_tools():
    registry = CapabilityRegistry(tools=[])

    result = registry.invoke(ToolCall(name="upload_ship_position", arguments={"mmsi": "123"}))

    assert result.status == "forbidden"
    assert not registry.has("upload_ship_position")


def test_registry_filters_mutating_tools_from_profile_discovery():
    class Tool:
        def __init__(self, name):
            self.name = name

    registry = CapabilityRegistry(tools=[Tool("local_kb_search"), Tool("upsert_local_kb_entry")])

    assert registry.has("local_kb_search")
    assert not registry.has("upsert_local_kb_entry")
