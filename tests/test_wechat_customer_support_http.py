from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

import main


def _post_wechat_case(monkeypatch, reply: str, *, tool_calls: list[str]) -> tuple[dict[str, Any], dict[str, Any]]:
    captured: dict[str, Any] = {}

    async def fake_run(payload: dict[str, Any], _ctx: Any) -> dict[str, Any]:
        captured["payload"] = payload
        return {
            "status": "success",
            "generated_answer": reply,
            "messages": [
                {"type": "human", "content": payload["messages"][-1]["content"]},
                {"type": "ai", "content": reply},
            ],
            "route_trace": {
                "route": "ship_update",
                "task_type": "ship_update",
                "tool_call_sequence": tool_calls,
            },
        }

    monkeypatch.setattr(main.service, "run", fake_run)
    monkeypatch.setattr(main, "resolve_profile_id", lambda **_kwargs: "customer_support")
    monkeypatch.setattr(main, "_resolve_request_llm_route", lambda _payload: {})
    monkeypatch.setattr(main, "_log_api_call_event", lambda **_kwargs: None)
    monkeypatch.setattr(main, "_log_agent_error_event", lambda **_kwargs: None)
    monkeypatch.setattr(main, "schedule_cozeloop_flush", lambda: None)

    response = TestClient(main.app).post(
        "/run",
        json={
            "session_id": "wechat-case-session",
            "user_id": "wechat-case-user",
            "source_channel": "wechat_kf",
            "response_mode": "compact",
            "content": {
                "query": {
                    "prompt": [
                        {"type": "text", "content": {"text": "更新船位，MMSI：414718000"}},
                    ]
                }
            },
        },
    )

    assert response.status_code == 200
    return response.json(), captured


def test_wechat_customer_service_http_missing_fields_reply(monkeypatch) -> None:
    response, captured = _post_wechat_case(
        monkeypatch,
        "请补充经度、纬度和更新时间；当前未执行更新。",
        tool_calls=[],
    )

    assert captured["payload"]["messages"] == [{"role": "user", "content": "更新船位，MMSI：414718000"}]
    assert response["source_channel"] == "wechat_kf"
    assert response["agent_profile"] == "customer_support"
    assert "请补充经度、纬度和更新时间" in response["answer"]
    assert "更新成功" not in response["answer"]
    assert "context" not in response
    assert "route_trace" not in response


def test_wechat_customer_service_http_tool_failure_never_claims_success(monkeypatch) -> None:
    response, captured = _post_wechat_case(
        monkeypatch,
        "船位更新未成功，服务暂时不可用，请稍后重试。",
        tool_calls=["upload_ship_position"],
    )

    assert captured["payload"]["source_channel"] == "wechat_kf"
    assert response["answer"] == "船位更新未成功，服务暂时不可用，请稍后重试。"
    assert "更新成功" not in response["answer"]
    assert "已为您更新" not in response["answer"]
    assert "context" not in response
    assert "route_trace" not in response
