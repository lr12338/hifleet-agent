import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from admin_api.schemas import ChatDebugSessionSaveRequest
from admin_api import service


def test_schema_accepts_explicit_contract_fields():
    req = ChatDebugSessionSaveRequest(
        session_key="k1",
        title="t",
        status="ended",
        meta_session_id="s1",
        user_id="u",
        source_channel="admin_panel",
        model="m",
        payload={"meta": {"agent_profile": "customer_ceshi"}},
        agent_profile="customer_ceshi",
        endpoint="/stream_run",
        response_mode="compact",
    )
    assert req.agent_profile == "customer_ceshi"
    assert req.endpoint == "/stream_run"
    assert req.response_mode == "compact"


def test_save_enriches_payload_with_contract_fields():
    captured = {}

    def fake_upsert(**kwargs):
        captured.update(kwargs)

    with patch.object(service.repository, "upsert_chat_debug_session", side_effect=fake_upsert):
        req = ChatDebugSessionSaveRequest(
            session_key="k1",
            title="t",
            status="ended",
            meta_session_id="s1",
            user_id="u",
            source_channel="admin_panel",
            model="m",
            payload={"meta": {"session_id": "s1"}},
            agent_profile="customer_ceshi",
            endpoint="/run",
            response_mode="full",
        )
        result = service.save_chat_debug_session(req)
    assert result["ok"] is True
    payload = captured["payload"]
    assert payload["meta"]["agent_profile"] == "customer_ceshi"
    assert payload["meta"]["endpoint"] == "/run"
    assert payload["meta"]["response_mode"] == "full"
    assert payload["_contract"] == {"agent_profile": "customer_ceshi", "endpoint": "/run", "response_mode": "full"}


def test_save_without_optional_fields_still_works():
    captured = {}

    def fake_upsert(**kwargs):
        captured.update(kwargs)

    with patch.object(service.repository, "upsert_chat_debug_session", side_effect=fake_upsert):
        req = ChatDebugSessionSaveRequest(
            session_key="k2",
            title="t",
            status="running",
            meta_session_id="s2",
            user_id="u",
            source_channel="admin_panel",
            model="m",
            payload={"meta": {}},
        )
        service.save_chat_debug_session(req)
    assert captured["payload"]["_contract"] == {}
