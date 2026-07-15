from agents.customer_ceshi_v2.tracing import safe_trace


def test_trace_redacts_hidden_reasoning_credentials_and_paths():
    trace = safe_trace({"reasoning_content": "private", "api_key": "secret", "message": "failed at /home/ecs-user/private.py", "nested": [{"token": "abc"}]})

    assert trace["reasoning_content"] == "[redacted]"
    assert trace["api_key"] == "[redacted]"
    assert "ecs-user" not in trace["message"]
    assert trace["nested"][0]["token"] == "[redacted]"
