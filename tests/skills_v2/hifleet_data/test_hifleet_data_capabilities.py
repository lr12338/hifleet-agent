"""Contract tests for every approved hifleet_data V2 capability.

Each approved capability in the capability map must resolve to a real adapter
tool with a matching input schema. Review-required capabilities must not resolve
to any local tool.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from skills_v2.skills.hifleet_data import adapter as hd_adapter
from skills_v2.core.loader import get_tool


ROOT = Path(__file__).resolve().parents[3]
CAPABILITY_MAP = ROOT / "src" / "skills_v2" / "upstream" / "hifleet_skills" / "capability_map.yaml"


def _capabilities() -> list[dict]:
    payload = yaml.safe_load(CAPABILITY_MAP.read_text(encoding="utf-8")) or {}
    return list(payload.get("capabilities") or [])


@pytest.fixture(scope="module")
def tool_index() -> dict:
    return {tool.name: tool for tool in hd_adapter.get_hifleet_data_tools()}


@pytest.mark.parametrize("cap", [c for c in _capabilities() if c.get("status") == "approved"])
def test_approved_capability_has_adapter_tool_and_schema(cap, tool_index) -> None:
    tool = tool_index.get(cap["local_tool"])
    assert tool is not None, f"approved capability {cap['upstream_id']} has no adapter tool {cap['local_tool']}"
    schema = tool.args_schema.model_json_schema()
    schema.pop("title", None)
    schema_file = ROOT / "src" / "skills_v2" / "upstream" / "hifleet_skills" / cap["input_schema"]
    assert schema_file.is_file(), f"input schema file missing for {cap['local_tool']}"
    expected = __import__("json").loads(schema_file.read_text(encoding="utf-8"))
    expected.pop("title", None)
    assert set(schema.get("properties", {})) == set(expected.get("properties", {})), (
        f"schema properties mismatch for {cap['local_tool']}"
    )
    assert tool.name == cap["local_tool"]


@pytest.mark.parametrize("cap", [c for c in _capabilities() if c.get("status") == "review_required"])
def test_review_required_capability_is_not_exposed(cap) -> None:
    assert cap["local_tool"] == ""
    assert get_tool(cap["upstream_id"]) is None


def test_hifleet_data_exposes_no_write_or_browser_tools() -> None:
    names = {tool.name for tool in hd_adapter.get_hifleet_data_tools()}
    forbidden = {"upload_ship_position", "update_ship_static_info", "verify_public_page", "agent_browser_deep_search", "web_search_agent_browser"}
    assert names.isdisjoint(forbidden)
    assert all(getattr(tool, "name", "") for tool in hd_adapter.get_hifleet_data_tools())
