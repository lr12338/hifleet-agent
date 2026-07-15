from types import SimpleNamespace

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import tool

from agents.customer_ceshi_responses.builder import (
    CHECKPOINT_NAMESPACE,
    NativeToolRuntime,
    _NamespacedRuntime,
)
from agents.customer_ceshi_v2.perception import PerceptionService
from agents.customer_ceshi_v2.tools import CapabilityRegistry


@tool
def local_kb_search(query: str) -> dict:
    """Find product documentation."""
    return {"status": "success", "facts": [f"Documentation confirms {query}."], "sources": ["https://example.test/docs"]}


class FakePerception:
    model = "doubao-test"

    def inspect_packet(self, *args, **kwargs):
        raise AssertionError("media perception should not run")


class ChatToolClient:
    model = "deepseek-test"

    def __init__(self):
        self.calls = 0

    def bind_tools(self, tools):
        return self

    def invoke(self, messages):
        self.calls += 1
        if self.calls == 1:
            return AIMessage(content="", tool_calls=[{"name": "local_kb_search", "args": {"query": "ETA"}, "id": "call-1"}])
        return AIMessage(content="Documentation confirms ETA.")


class FakeResponses:
    def __init__(self):
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if len(self.calls) == 1:
            return SimpleNamespace(id="resp-1", output=[SimpleNamespace(type="function_call", name="local_kb_search", arguments='{"query":"ETA"}', call_id="fc-1")], output_text="")
        if len(self.calls) == 2:
            return SimpleNamespace(id="resp-2", output=[SimpleNamespace(type="function_call", name="local_kb_search", arguments='{"query":"ETD"}', call_id="fc-2")], output_text="")
        return SimpleNamespace(id="resp-3", output=[], output_text="Documentation confirms ETA and ETD.")


class ResponsesClient:
    model = "deepseek-test"

    def __init__(self):
        self.responses = FakeResponses()

    def bind_tools(self, tools):
        raise AssertionError("Responses path must not use Chat tool binding")


def _runtime(*, mode, client, responses_client=None):
    return NativeToolRuntime(
        client=client,
        registry=CapabilityRegistry(tools=[local_kb_search]),
        perception=PerceptionService(FakePerception()),
        config={"customer_ceshi_max_steps": 5, "customer_ceshi_chat_fallback_enabled": True},
        mode=mode,
        responses_client=responses_client,
    )


def test_responses_native_tool_loop_continues_two_calls_with_previous_response_id():
    client = ResponsesClient()
    runtime = _runtime(mode="responses", client=client, responses_client=client)

    result = _NamespacedRuntime(runtime).invoke({"messages": [HumanMessage(content="Check ETA and ETD")]}, {"configurable": {"thread_id": "session-a"}})

    assert result["generated_tool_calls"] == ["local_kb_search", "local_kb_search"]
    assert result["generated_answer"] == "Documentation confirms ETA and ETD."
    assert client.responses.calls[1]["previous_response_id"] == "resp-1"
    assert client.responses.calls[2]["previous_response_id"] == "resp-2"
    assert result["route_trace"]["checkpoint_namespace"] == CHECKPOINT_NAMESPACE


def test_responses_failure_uses_native_chat_function_calling_fallback():
    chat = ChatToolClient()

    class BrokenResponses:
        class responses:
            @staticmethod
            def create(**kwargs):
                raise RuntimeError("unsupported")

    result = _runtime(mode="responses", client=chat, responses_client=BrokenResponses()).invoke({"messages": [HumanMessage(content="Check ETA")]}, {"configurable": {"thread_id": "session-b"}})

    assert result["generated_tool_calls"] == ["local_kb_search"]
    assert result["generated_answer"] == "Documentation confirms ETA."
    assert result["metrics"]["finish_reason"] == "stop"
