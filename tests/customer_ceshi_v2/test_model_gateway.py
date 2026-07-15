from types import SimpleNamespace
import time

import pytest

from agents.customer_ceshi_v2.models import ModelGatewayError, MultimodalPerceptionClient, TextReasoningClient
from agents.customer_ceshi_v2.contracts import InspectMediaRequest, MediaAsset, Observation
from utils.llm_route_state import clear_current_llm_route, set_current_llm_route


class RepairingClient:
    def __init__(self):
        self.calls = 0

    def invoke(self, messages, **kwargs):
        self.calls += 1
        return SimpleNamespace(content="not-json" if self.calls == 1 else '{"action":"finish","answer_draft":"safe"}')


def test_text_gateway_repairs_one_invalid_structured_response():
    client = RepairingClient()
    gateway = TextReasoningClient({}, client=client)

    decision = gateway.decide(task_goal="hello", observations=[], assets=[], descriptors=[], step_count=0)

    assert client.calls == 2
    assert decision.action == "finish"
    assert decision.answer_draft == "safe"


def test_text_gateway_accepts_markdown_wrapped_json_response():
    class MarkdownClient:
        def invoke(self, messages, **kwargs):
            return SimpleNamespace(content='```json\n{"action":"finish","answer_draft":"safe"}\n```')

    decision = TextReasoningClient({}, client=MarkdownClient()).decide(
        task_goal="hello",
        observations=[],
        assets=[],
        descriptors=[],
        step_count=0,
    )

    assert decision.answer_draft == "safe"


def test_text_gateway_accepts_segmented_content_json_response():
    class SegmentedClient:
        def invoke(self, messages, **kwargs):
            return SimpleNamespace(content=[{"type": "text", "text": '{"action":"finish","answer_draft":"safe"}'}])

    decision = TextReasoningClient({}, client=SegmentedClient()).decide(
        task_goal="hello",
        observations=[],
        assets=[],
        descriptors=[],
        step_count=0,
    )

    assert decision.answer_draft == "safe"


def test_text_gateway_normalizes_empty_optional_decision_objects():
    class EmptyOptionalObjectClient:
        def invoke(self, messages, **kwargs):
            return SimpleNamespace(content='{"action":"finish","answer_draft":"safe","write_proposal":""}')

    decision = TextReasoningClient({}, client=EmptyOptionalObjectClient()).decide(
        task_goal="hello",
        observations=[],
        assets=[],
        descriptors=[],
        step_count=0,
    )

    assert decision.answer_draft == "safe"
    assert decision.write_proposal is None


def test_text_gateway_normalizes_nullable_optional_decision_fields_without_repair():
    class NullableFieldsClient:
        def __init__(self):
            self.calls = 0

        def invoke(self, messages, **kwargs):
            self.calls += 1
            return SimpleNamespace(
                content='{"action":"finish","answer_draft":null,"question":null,"perception_goal":null,"tool_calls":null,"asset_ids":null,"media_requests":null,"claims":null,"write_proposal":null}'
            )

    client = NullableFieldsClient()
    decision = TextReasoningClient({}, client=client).decide(
        task_goal="hello",
        observations=[],
        assets=[],
        descriptors=[],
        step_count=0,
    )

    assert client.calls == 1
    assert decision.answer_draft == ""
    assert decision.question == ""
    assert decision.tool_calls == []
    assert decision.media_requests == []


def test_v2_keeps_orchestrator_on_json_model_for_multimodal_external_route(monkeypatch):
    created = []

    class FakeChatOpenAI:
        def __init__(self, **kwargs):
            created.append(kwargs)

    monkeypatch.setenv("COZE_WORKLOAD_IDENTITY_API_KEY", "workload-key")
    monkeypatch.setenv("COZE_INTEGRATION_MODEL_BASE_URL", "https://gateway.example")
    monkeypatch.setattr("llm_gateway.ChatOpenAI", FakeChatOpenAI)
    set_current_llm_route({"model": "doubao-external", "modality": "multimodal"})
    try:
        config = {
            "customer_support_json_model": "customer-support-json",
            "multimodal_model": "customer-support-vision",
        }
        text = TextReasoningClient(config, ctx=SimpleNamespace())
        perception = MultimodalPerceptionClient(config, ctx=SimpleNamespace())
    finally:
        clear_current_llm_route()

    assert text.model == "customer-support-json"
    assert text.model_override_ignored is True
    assert perception.model == "doubao-external"
    assert perception.model_override_ignored is False
    assert [item["model"] for item in created] == ["customer-support-json", "doubao-external"]


def test_invalid_decision_reports_both_model_attempts():
    class InvalidDecisionClient:
        def __init__(self):
            self.calls = 0

        def invoke(self, messages, **kwargs):
            self.calls += 1
            return SimpleNamespace(content="not-json")

    client = InvalidDecisionClient()
    with pytest.raises(ModelGatewayError) as exc_info:
        TextReasoningClient({}, client=client).decide(
            task_goal="hello",
            observations=[],
            assets=[],
            descriptors=[],
            step_count=0,
        )

    assert client.calls == 2
    assert exc_info.value.code == "model_invalid_response"
    assert exc_info.value.model_calls == 2


def test_v2_uses_customer_support_gateway_credentials_and_json_role(monkeypatch):
    created = []

    class FakeChatOpenAI:
        def __init__(self, **kwargs):
            created.append(kwargs)

    monkeypatch.setenv("COZE_WORKLOAD_IDENTITY_API_KEY", "workload-key")
    monkeypatch.setenv("JSON_MODEL_BASE_URL", "https://json.example")
    monkeypatch.setattr("llm_gateway.ChatOpenAI", FakeChatOpenAI)
    config = {
        "text_model": "text-model",
        "customer_support_json_model": "customer-support-json-model",
        "json_model_base_url_env": "JSON_MODEL_BASE_URL",
        "customer_ceshi_v2_timeout_seconds": 17,
    }

    client = TextReasoningClient(config, ctx=SimpleNamespace())

    assert client.model == "customer-support-json-model"
    assert created[0]["model"] == "customer-support-json-model"
    assert created[0]["api_key"] == "workload-key"
    assert created[0]["base_url"] == "https://json.example"
    assert created[0]["timeout"] == 17.0


def test_perception_gateway_uses_low_latency_timeout_by_default(monkeypatch):
    created = []

    class FakeChatOpenAI:
        def __init__(self, **kwargs):
            created.append(kwargs)

    monkeypatch.setenv("COZE_WORKLOAD_IDENTITY_API_KEY", "workload-key")
    monkeypatch.setenv("COZE_INTEGRATION_MODEL_BASE_URL", "https://gateway.example")
    monkeypatch.setattr("llm_gateway.ChatOpenAI", FakeChatOpenAI)

    MultimodalPerceptionClient({"customer_ceshi_v2_timeout_seconds": 30}, ctx=SimpleNamespace())

    assert created[0]["timeout"] == 12.0


def test_perception_gateway_applies_hard_timeout_to_unresponsive_client():
    class SlowClient:
        def invoke(self, messages, **kwargs):
            time.sleep(0.2)
            return SimpleNamespace(content="{}")

    gateway = MultimodalPerceptionClient(
        {"customer_ceshi_v2_media_hard_timeout_seconds": 0.05},
        client=SlowClient(),
    )
    started = time.monotonic()
    result = gateway.inspect_packet(
        MediaAsset(asset_id="unknown", kind="image", url="data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="),
        InspectMediaRequest(asset_id="unknown", objective="inspect"),
    )

    assert time.monotonic() - started < 0.15
    assert isinstance(result, Observation)
    assert result.warnings == ["model_timeout"]


def test_transport_error_does_not_trigger_format_repair():
    class FailingClient:
        def __init__(self):
            self.calls = 0

        def invoke(self, messages, **kwargs):
            self.calls += 1
            raise TimeoutError("request timed out")

    client = FailingClient()
    gateway = TextReasoningClient({}, client=client)

    with pytest.raises(ModelGatewayError) as exc_info:
        gateway.decide(task_goal="hello", observations=[], assets=[], descriptors=[], step_count=0)

    assert exc_info.value.code == "model_timeout"
    assert exc_info.value.retryable is True
    assert client.calls == 1
