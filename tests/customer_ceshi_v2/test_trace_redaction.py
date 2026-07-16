from agents.customer_ceshi_v2.tracing import safe_trace


def test_trace_redacts_hidden_reasoning_credentials_and_paths():
    trace = safe_trace({"reasoning_content": "private", "api_key": "secret", "message": "failed at /home/ecs-user/private.py", "nested": [{"token": "abc"}]})

    assert trace["reasoning_content"] == "[redacted]"
    assert trace["api_key"] == "[redacted]"
    assert "ecs-user" not in trace["message"]
    assert trace["nested"][0]["token"] == "[redacted]"


def test_trace_preserves_non_secret_token_metrics():
    trace = safe_trace({"context_tokens": 12, "output_tokens": 8, "reasoning_tokens": 4, "confirmation_token": "secret"})

    assert trace["context_tokens"] == 12
    assert trace["output_tokens"] == 8
    assert trace["reasoning_tokens"] == 4
    assert trace["confirmation_token"] == "[redacted]"
