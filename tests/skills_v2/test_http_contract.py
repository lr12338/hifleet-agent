from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

import main


def test_run_preserves_external_protocol_and_returns_v2_trace_metadata(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    async def fake_run(payload: dict[str, Any], _ctx: Any) -> dict[str, Any]:
        captured["payload"] = payload
        return {
            "status": "success",
            "generated_answer": "已收到您的问题。",
            "messages": [{"type": "ai", "content": "已收到您的问题。"}],
            "metrics": {
                "runtime_mode": "responses",
                "skills_runtime": {
                    "mode": "v2",
                    "source_versions": {"hifleet_data": {"upstream_commit": "e4acf599"}},
                },
            },
            "route_trace": {"route": "knowledge", "tool_call_sequence": []},
        }

    monkeypatch.setattr(main.service, "run", fake_run)
    monkeypatch.setattr(main, "resolve_profile_id", lambda **_kwargs: "customer_ceshi")
    monkeypatch.setattr(main, "_resolve_request_llm_route", lambda _payload: {"model": "fixture-model"})
    monkeypatch.setattr(main, "_log_api_call_event", lambda **_kwargs: None)
    monkeypatch.setattr(main, "_log_agent_error_event", lambda **_kwargs: None)
    monkeypatch.setattr(main, "schedule_cozeloop_flush", lambda: None)

    response = TestClient(main.app).post(
        "/run",
        json={
            "user_id": "skills-v2-user",
            "session_id": "skills-v2-session",
            "source_channel": "websdk",
            "agent_profile": "customer_ceshi",
            "messages": [{"role": "user", "content": "你好"}],
            "llm_route": {"model": "fixture-model"},
        },
    )

    assert response.status_code == 200
    assert captured["payload"]["agent_profile"] == "customer_ceshi"
    assert captured["payload"]["llm_route"] == {"model": "fixture-model"}
    assert response.json()["metrics"]["skills_runtime"]["mode"] == "v2"
    assert response.json()["metrics"]["skills_runtime"]["source_versions"]["hifleet_data"]["upstream_commit"] == "e4acf599"
