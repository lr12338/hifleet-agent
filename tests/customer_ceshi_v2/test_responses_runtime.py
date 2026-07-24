from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.tools import tool

from agents.customer_ceshi_responses.builder import (
    CHECKPOINT_NAMESPACE,
    MEDIA_UPDATE_EVIDENCE_TOOL_NAME,
    MediaUpdateEvidence,
    MediaUpdateEvidenceLedger,
    NativeToolRuntime,
    SingleModelCustomerCeshiRuntime,
    _NamespacedRuntime,
    _wechat_plain_text,
    _wechat_position_result,
    runtime_config,
)
from agents.customer_ceshi_v2.contracts import Observation
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


def _media_candidate_arguments() -> dict:
    return {
        "operation_type": "position_update",
        "mmsi": "413790872",
        "ship_name": "GANG YANG 009",
        "imo": "",
        "lon": "116.1",
        "lat": "29.4",
        "updatetime": "2026-07-15 17:36:37",
        "speed": "9.8",
        "heading": "",
        "course": "",
        "destination": "HUKOU, CN",
        "eta": "",
        "draft": "6.8",
        "navstatus": "",
        "ship_type": "",
        "minotype": "",
        "length": "",
        "width": "",
        "dwt": "",
        "flag": "",
        "callsign": "",
        "built_year": "",
        "missing_fields": [],
        "validation_errors": [],
        "confidence": "high",
    }


class MediaCandidateResponses:
    class responses:
        pass

    def __init__(self):
        self.calls = []
        self.responses = SimpleNamespace(create=self.create)

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if len(self.calls) == 1:
            return SimpleNamespace(
                id="media-resp-1",
                output=[SimpleNamespace(type="function_call", name=MEDIA_UPDATE_EVIDENCE_TOOL_NAME, arguments=__import__("json").dumps(_media_candidate_arguments()), call_id="media-call-1")],
                output_text="",
                usage={},
            )
        return SimpleNamespace(id="media-resp-2", output=[], output_text="已识别到图片中的 AIS 船位信息。", usage={})


def test_responses_native_tool_loop_continues_two_calls_with_previous_response_id():
    client = ResponsesClient()
    runtime = _runtime(mode="responses", client=client, responses_client=client)

    result = _NamespacedRuntime(runtime).invoke({"messages": [HumanMessage(content="Check ETA and ETD")]}, {"configurable": {"thread_id": "session-a"}})

    assert result["generated_tool_calls"] == ["local_kb_search", "local_kb_search"]
    assert result["generated_answer"] == "Documentation confirms ETA and ETD."
    assert client.responses.calls[1]["previous_response_id"] == "resp-1"
    assert client.responses.calls[2]["previous_response_id"] == "resp-2"
    assert result["route_trace"]["checkpoint_namespace"] == CHECKPOINT_NAMESPACE


def test_v2_search_budget_removes_exhausted_search_schema_from_next_turn():
    client = ResponsesClient()
    runtime = NativeToolRuntime(
        client=client,
        registry=CapabilityRegistry(tools=[local_kb_search]),
        perception=PerceptionService(FakePerception()),
        config={"customer_ceshi_max_steps": 5},
        mode="responses",
        responses_client=client,
        skill_runtime_metadata={"mode": "v2"},
    )

    _NamespacedRuntime(runtime).invoke({"messages": [HumanMessage(content="Check ETA and ETD")]}, {"configurable": {"thread_id": "v2-search-budget"}})

    second_request_tools = [item["name"] for item in client.responses.calls[1]["tools"]]
    assert "local_kb_search" not in second_request_tools


def test_platform_operation_finalizes_after_one_internal_evidence_retrieval():
    class PlatformResponses:
        def __init__(self):
            self.calls = []

        def create(self, **kwargs):
            self.calls.append(kwargs)
            if len(self.calls) == 1:
                return SimpleNamespace(id="platform-1", output=[SimpleNamespace(type="function_call", name="local_kb_search", arguments='{"query":"航线上传"}', call_id="kb-1")], output_text="")
            return SimpleNamespace(id="platform-2", output=[], output_text="依据帮助文档，请在计划面板上传航线。")

    client = ResponsesClient()
    responses = PlatformResponses()
    client.responses = responses

    @tool("local_kb_search")
    def platform_kb(query: str) -> dict:
        """Find product instructions."""
        return {"status": "success", "items": [{"content": "在计划面板上传航线。"}], "facts": ["计划面板有上传入口。"]}

    runtime = NativeToolRuntime(
        client=client,
        registry=CapabilityRegistry(tools=[platform_kb]),
        perception=PerceptionService(FakePerception()),
        config={},
        mode="responses",
        responses_client=client,
        skill_runtime_metadata={"mode": "v2"},
    )

    result = _NamespacedRuntime(runtime).invoke({"messages": [HumanMessage(content="HiFleet 平台上传不了航线")]}, {"configurable": {"thread_id": "platform-one-kb"}})

    assert result["generated_tool_calls"] == ["local_kb_search"]
    assert responses.calls[1]["tools"] == []
    assert responses.calls[1]["tool_choice"] == "none"


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


def test_responses_tool_schema_exposes_only_read_only_allowlist():
    @tool
    def upload_ship_position(mmsi: str) -> dict:
        """Write a ship position."""
        return {"status": "success"}

    runtime = NativeToolRuntime(
        client=ChatToolClient(),
        registry=CapabilityRegistry(tools=[local_kb_search, upload_ship_position]),
        perception=PerceptionService(FakePerception()),
        config={},
        mode="chat_function_calling",
    )

    tool_names = [item["name"] for item in runtime._responses_tools()]

    assert "local_kb_search" in tool_names
    assert "inspect_media" in tool_names
    assert "upload_ship_position" not in tool_names


def test_responses_context_isolated_by_user_even_with_same_session_id():
    client = ResponsesClient()
    runtime = _runtime(mode="responses", client=client, responses_client=client)
    graph = _NamespacedRuntime(runtime)

    graph.invoke({"messages": [HumanMessage(content="Check ETA")], "session_id": "shared", "user_id": "user-a"}, {"configurable": {"thread_id": "shared"}})
    graph.invoke({"messages": [HumanMessage(content="Check ETD")], "session_id": "shared", "user_id": "user-b"}, {"configurable": {"thread_id": "shared"}})

    assert "previous_response_id" not in client.responses.calls[3]


def test_model_grounded_answer_is_not_blocked_by_keyword_matching():
    answer, result = NativeToolRuntime._guard(
        "船位更新较慢不一定是网站故障，可能与 AIS 信号、卫星链路或岸基覆盖有关。",
        [{"status": "success", "capability": "web_search", "data": {"can_answer": True, "confidence": "high"}, "facts": ["官方说明：远海、云层和岸基覆盖会影响 AIS 数据接收。"], "evidence_id": "e-2"}],
    )

    assert result == "model_grounded:e-2"
    assert "AIS 信号" in answer


def test_only_explicit_write_success_requires_write_observation():
    blocked_answer, blocked_result = NativeToolRuntime._guard("本次船位更新成功。", [])
    confirmed_answer, confirmed_result = NativeToolRuntime._guard(
        "本次船位更新成功。",
        [{"status": "success", "capability": "upload_ship_position", "data": {}, "facts": ["上传成功"], "evidence_id": "e-3"}],
    )

    assert blocked_result == "blocked_unconfirmed_write"
    assert "系统成功确认" in blocked_answer
    assert confirmed_result == "not_required"
    assert confirmed_answer == "本次船位更新成功。"


def test_compact_observation_uses_semantic_evidence_without_raw_search_json():
    runtime = _runtime(mode="chat_function_calling", client=ChatToolClient())
    raw = {
        "tool": "web_search",
        "query": "hifleet 船位更新 数据延迟 ais 原因",
        "summary": "命中 HiFleet 官方具体页面且包含明确事实",
        "can_answer": True,
        "confidence": "high",
        "items": [{"title": "常见船位问题", "snippet": "远海、云层和岸基覆盖会影响 AIS 数据接收。", "url": "https://www.hifleet.com/help"}],
        "trace": {"request_profile": {"secret": "must-not-reach-model"}},
    }
    packet = runtime._compact_observation(Observation(status="success", capability="web_search", facts=[__import__("json").dumps(raw, ensure_ascii=False)], data=raw))
    serialized = __import__("json").dumps(packet, ensure_ascii=False)

    assert packet["tool"] == "web_search"
    assert packet["data"]["can_answer"] is True
    assert "常见船位问题" in " ".join(packet["facts"])
    assert "trace" not in serialized
    assert "secret" not in serialized
    assert "items" not in serialized


def test_stream_mode_emits_langgraph_updates_tuple():
    graph = _NamespacedRuntime(_runtime(mode="chat_function_calling", client=ChatToolClient()))

    event = next(graph.stream({"messages": [HumanMessage(content="Check ETA")]}, stream_mode=["updates"]))

    assert event[0] == "updates"
    assert "customer_ceshi_responses" in event[1]


def test_model_requested_write_is_rejected_even_if_it_bypasses_schema():
    runtime = _runtime(mode="chat_function_calling", client=ChatToolClient())

    observation = runtime._execute("upload_ship_position", {"mmsi": "123"}, {})

    assert observation.status == "forbidden"
    assert observation.warnings == ["tool_not_in_customer_ceshi_read_only_allowlist"]


def test_astream_update_contract_is_async_compatible():
    import asyncio

    async def collect():
        graph = _NamespacedRuntime(_runtime(mode="chat_function_calling", client=ChatToolClient()))
        return [item async for item in graph.astream({"messages": [HumanMessage(content="Check ETA")]}, stream_mode=["updates"])]

    events = asyncio.run(collect())

    assert events[0][0] == "updates"
    assert events[0][1]["customer_ceshi_responses"]["generated_answer"] == "Documentation confirms ETA."


def test_runtime_exposes_compiled_graph_for_host_loop_tracer():
    graph = _NamespacedRuntime(_runtime(mode="chat_function_calling", client=ChatToolClient()))

    inspected = graph.get_graph()

    assert "customer_ceshi_responses" in inspected.nodes


def test_host_loop_trace_initialization_accepts_native_runtime_graph():
    from coze_coding_utils.log.loop_trace import init_run_config
    from coze_coding_utils.runtime_ctx.context import new_context

    graph = _NamespacedRuntime(_runtime(mode="chat_function_calling", client=ChatToolClient()))

    config = init_run_config(graph, new_context("responses-runtime-test"))

    assert config["callbacks"]


def test_chat_fallback_never_sends_raw_image_url_to_text_orchestrator():
    class RecordingClient(ChatToolClient):
        def __init__(self):
            super().__init__()
            self.first_messages = []

        def invoke(self, messages):
            self.first_messages = messages
            return super().invoke(messages)

    client = RecordingClient()
    graph = _NamespacedRuntime(_runtime(mode="chat_function_calling", client=client))
    graph.invoke({"messages": [HumanMessage(content=[{"type": "image_url", "image_url": {"url": "https://example.test/image.png"}}, {"type": "text", "text": "这是什么"}])]}, {"configurable": {"thread_id": "media"}})

    serialized = "\n".join(str(message.content) for message in client.first_messages)
    assert "https://example.test/image.png" not in serialized
    assert "asset-0:image" in serialized


def test_responses_request_uses_flat_function_schema_and_preserves_system_language_instruction():
    client = ResponsesClient()
    runtime = _runtime(mode="responses", client=client, responses_client=client)
    graph = _NamespacedRuntime(runtime)

    graph.invoke(
        {"messages": [SystemMessage(content="请使用中文回答。"), HumanMessage(content="你是谁")], "session_id": "responses-shape"},
        {"configurable": {"thread_id": "responses-shape"}},
    )

    request = client.responses.calls[0]
    assert isinstance(request["input"], str)
    assert "请使用中文回答" in request["input"]
    assert request["tools"][0]["type"] == "function"
    assert "function" not in request["tools"][0]
    assert {"name", "description", "parameters"}.issubset(request["tools"][0])
    assert request["store"] is True
    assert request["max_output_tokens"] == 8192
    assert request["extra_body"] == {"thinking": {"type": "enabled"}}


def test_responses_fallback_records_safe_provider_status_and_code_only():
    chat = ChatToolClient()

    class ProviderFailure(Exception):
        status_code = 400
        body = {"error": {"code": "InvalidParameter", "param": "tools"}}

    class BrokenResponses:
        class responses:
            @staticmethod
            def create(**kwargs):
                raise ProviderFailure("https://secret.example/request-body-must-not-be-recorded")

    result = _runtime(mode="responses", client=chat, responses_client=BrokenResponses()).invoke(
        {"messages": [HumanMessage(content="Check ETA")]}, {"configurable": {"thread_id": "provider-error"}}
    )

    assert result["metrics"]["provider_error"] == "ProviderFailure;status=400;code=InvalidParameter;param=tools"
    assert "secret.example" not in result["metrics"]["fallback_reason"]


@pytest.mark.xfail(reason="obsolete: media must remain DeepSeek-led and no longer selects a Doubao business loop")
def test_single_model_router_uses_doubao_only_for_image_request():
    class MultimodalResponses:
        def __init__(self):
            self.calls = []

        class responses:
            pass

    multimodal = MultimodalResponses()
    multimodal.responses = SimpleNamespace(create=lambda **kwargs: (multimodal.calls.append(kwargs) or SimpleNamespace(output_text="图片中可见一个海图标记。", output=[], usage={})))

    text = ChatToolClient()
    runtime = SingleModelCustomerCeshiRuntime(text_runtime=_runtime(mode="chat_function_calling", client=text), multimodal_responses_client=multimodal)

    result = runtime.invoke({"messages": [HumanMessage(content=[{"type": "image_url", "image_url": {"url": "https://example.test/image.png"}}, {"type": "text", "text": "这是什么"}])]}, {"configurable": {"thread_id": "media-route"}})

    assert result["metrics"]["runtime_mode"] == "multimodal_responses"
    assert result["generated_answer"] == "图片中可见一个海图标记。"
    assert text.calls == 0
    assert len(multimodal.calls) == 1
    request = multimodal.calls[0]
    assert request["model"] == "doubao-seed-2-1-pro-260628"
    assert request["input"][0]["content"][1] == {"type": "input_image", "image_url": "https://example.test/image.png", "detail": "high"}
    assert request["input"][0]["content"][-1] == {"type": "input_text", "text": "这是什么"}


@pytest.mark.xfail(reason="obsolete: Doubao no longer owns business tools or previous_response_id loops")
def test_multimodal_responses_uses_doubao_read_only_tool_loop_and_previous_response_id():
    class MediaResponses:
        class responses:
            pass

        def __init__(self):
            self.calls = []
            self.responses = SimpleNamespace(create=self.create)

        def create(self, **kwargs):
            self.calls.append(kwargs)
            if len(self.calls) == 1:
                return SimpleNamespace(
                    id="media-1",
                    output=[SimpleNamespace(type="function_call", name="local_kb_search", arguments='{"query":"30天潮汐表 港口数量限制"}', call_id="kb-1")],
                    output_text="",
                    usage={},
                )
            return SimpleNamespace(id="media-2", output=[], output_text="30天潮汐表的限制已核验，请按页面提示选择港口。", usage={})

    media = MediaResponses()
    runtime = SingleModelCustomerCeshiRuntime(
        text_runtime=NativeToolRuntime(client=ChatToolClient(), registry=CapabilityRegistry(tools=[local_kb_search]), config={}, mode="chat_function_calling"),
        multimodal_responses_client=media,
    )

    result = runtime.invoke(
        {"messages": [HumanMessage(content=[{"type": "image_url", "image_url": {"url": "https://example.test/product.png"}}, {"type": "text", "text": "30天潮汐表是否有限制？"}])]},
        {"configurable": {"thread_id": "media-kb"}},
    )

    assert result["generated_answer"].startswith("30天潮汐表")
    assert result["generated_tool_calls"] == ["local_kb_search"]
    assert media.calls[0]["input"][0]["content"][1]["type"] == "input_image"
    assert media.calls[1]["previous_response_id"] == "media-1"
    assert media.calls[1]["input"][0]["type"] == "function_call_output"


def test_multimodal_response_text_handles_nested_output_text_shape():
    response = SimpleNamespace(output_text="", output=[SimpleNamespace(content=[{"type": "output_text", "output_text": {"value": "图片中的页面功能已识别。"}}])])

    assert NativeToolRuntime._responses_text(response) == "图片中的页面功能已识别。"


def test_single_model_router_uses_deepseek_runtime_for_text_only_request():
    class MultimodalClient:
        def invoke(self, messages):
            raise AssertionError("multimodal model must not handle text-only request")

    text = ChatToolClient()
    runtime = SingleModelCustomerCeshiRuntime(text_runtime=_runtime(mode="chat_function_calling", client=text), multimodal_client=MultimodalClient())

    result = runtime.invoke({"messages": [HumanMessage(content="Check ETA")]}, {"configurable": {"thread_id": "text-route"}})

    assert result["metrics"]["orchestrator_model"] == "deepseek-test"
    assert text.calls == 2


def test_cross_turn_text_context_does_not_reuse_previous_response_id_or_raw_tool_logs():
    class TextResponses:
        def __init__(self):
            self.calls = []

        class responses:
            pass

    responses = TextResponses()
    responses.responses = SimpleNamespace(create=lambda **kwargs: (responses.calls.append(kwargs) or SimpleNamespace(id=f"resp-{len(responses.calls)}", output=[], output_text="已回答。", usage={})))
    client = ResponsesClient()
    runtime = SingleModelCustomerCeshiRuntime(text_runtime=_runtime(mode="responses", client=client, responses_client=responses), config={"customer_ceshi_runtime": {"context": {"max_rounds": 10, "recent_full_rounds": 3}}})

    runtime.invoke({"messages": [HumanMessage(content="第一问")], "session_id": "history", "user_id": "u"}, {"configurable": {"thread_id": "history"}})
    runtime.invoke({"messages": [HumanMessage(content="第二问")], "session_id": "history", "user_id": "u"}, {"configurable": {"thread_id": "history"}})

    assert "previous_response_id" not in responses.calls[1]
    assert "第一问" in responses.calls[1]["input"]
    assert "function_call_output" not in responses.calls[1]["input"]


def test_memory_keeps_ten_rounds_and_compacts_older_turns():
    class TextResponses:
        def __init__(self):
            self.calls = []

        class responses:
            pass

    responses = TextResponses()
    responses.responses = SimpleNamespace(create=lambda **kwargs: (responses.calls.append(kwargs) or SimpleNamespace(id=f"resp-{len(responses.calls)}", output=[], output_text="回复", usage={})))
    runtime = SingleModelCustomerCeshiRuntime(text_runtime=_runtime(mode="responses", client=ResponsesClient(), responses_client=responses), config={"customer_ceshi_runtime": {"context": {"max_rounds": 10, "recent_full_rounds": 3}}})

    for index in range(11):
        runtime.invoke({"messages": [HumanMessage(content=f"问题-{index}")], "session_id": "ten", "user_id": "u"}, {"configurable": {"thread_id": "ten"}})

    context, rounds, compacted = runtime.memory.render("default:u:ten")
    assert rounds == 10
    assert compacted is True
    assert "问题-0" not in context
    assert "问题-1" in context
    assert "问题-10" in context


@pytest.mark.xfail(reason="obsolete: mixed media is orchestrated by DeepSeek through inspect_media")
def test_multimodal_responses_supports_video_audio_and_mixed_content():
    class MediaResponses:
        class responses:
            pass

        def __init__(self):
            self.calls = []
            self.responses = SimpleNamespace(create=lambda **kwargs: (self.calls.append(kwargs) or SimpleNamespace(output_text="已分析", output=[], usage={})))

    media = MediaResponses()
    runtime = SingleModelCustomerCeshiRuntime(text_runtime=_runtime(mode="chat_function_calling", client=ChatToolClient()), multimodal_responses_client=media)
    result = runtime.invoke(
        {"messages": [HumanMessage(content=[
            {"type": "image_url", "image_url": {"url": "https://example.test/1.png"}},
            {"type": "video_url", "video_url": {"url": "https://example.test/1.mp4"}},
            {"type": "audio_url", "audio_url": {"url": "https://example.test/1.mp3"}},
            {"type": "text", "text": "综合分析"},
        ])]},
        {"configurable": {"thread_id": "mixed"}},
    )

    assert result["status"] == "success"
    content = media.calls[0]["input"][0]["content"]
    assert [item["type"] for item in content[1:-1]] == ["input_image", "input_video", "input_audio"]
    assert content[2]["fps"] == 1.0
    assert media.calls[0]["model"] == "doubao-seed-2-1-pro-260628"


@pytest.mark.xfail(reason="obsolete: HTTP media messages no longer invoke a standalone Doubao runtime")
def test_multimodal_responses_accepts_http_dict_messages():
    class MediaResponses:
        class responses:
            pass

        def __init__(self):
            self.calls = []
            self.responses = SimpleNamespace(create=lambda **kwargs: (self.calls.append(kwargs) or SimpleNamespace(output_text="海图", output=[], usage={})))

    media = MediaResponses()
    runtime = SingleModelCustomerCeshiRuntime(text_runtime=_runtime(mode="chat_function_calling", client=ChatToolClient()), multimodal_responses_client=media)

    result = runtime.invoke(
        {"messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "https://example.test/chart.png"}}, {"type": "text", "text": "这是什么"}]}]},
        {"configurable": {"thread_id": "http-media"}},
    )

    assert result["status"] == "success"
    assert media.calls[0]["input"][0]["content"][1]["type"] == "input_image"


def test_runtime_config_exposes_customer_ceshi_responses_profiles():
    value = runtime_config({"config": {"customer_ceshi_runtime": {"text_model": {"model": "deepseek-x"}, "responses": {"deepseek": {"model": "deepseek-x"}}, "context": {"max_rounds": 10}}}})

    assert value["text_model"]["model"] == "deepseek-x"
    assert value["responses"]["deepseek"]["model"] == "deepseek-x"
    assert value["context"]["max_rounds"] == 10


def test_responses_failure_does_not_use_chat_when_runtime_flag_disables_it():
    chat = ChatToolClient()

    class BrokenResponses:
        class responses:
            @staticmethod
            def create(**kwargs):
                raise RuntimeError("unsupported")

    runtime = NativeToolRuntime(
        client=chat,
        registry=CapabilityRegistry(tools=[local_kb_search]),
        perception=PerceptionService(FakePerception()),
        config={"customer_ceshi_runtime": {"fallback_mode": "disabled", "chat_fallback_enabled": False}},
        mode="responses",
        responses_client=BrokenResponses(),
    )

    result = runtime.invoke({"messages": [HumanMessage(content="Check ETA")]}, {"configurable": {"thread_id": "no-chat"}})

    assert chat.calls == 0
    assert result["status"] == "degraded"
    assert result["metrics"]["finish_reason"] == "responses_unavailable_no_chat_fallback"


@pytest.mark.xfail(reason="obsolete: can_answer metadata cannot force model completion")
def test_responses_stops_tools_after_answerable_search_result():
    class SearchResponses:
        class responses:
            pass

        def __init__(self):
            self.calls = []
            self.responses = SimpleNamespace(create=self.create)

        def create(self, **kwargs):
            self.calls.append(kwargs)
            if len(self.calls) == 1:
                return SimpleNamespace(id="resp-search-1", output=[SimpleNamespace(type="function_call", name="local_kb_search", arguments='{"query":"HiFleet 区域历史数据"}', call_id="fc-search")], output_text="", usage={})
            return SimpleNamespace(id="resp-search-2", output=[], output_text="可在区域分析中查看历史数据。", usage={})

    @tool("local_kb_search")
    def answerable_kb(query: str) -> dict:
        """Find HiFleet instructions."""
        return {
            "status": "success",
            "facts": ["区域分析页面支持查看历史数据。"],
            "query": query,
            "can_answer": True,
            "recommended_next_action": "直接基于当前检索结果回答用户",
        }

    responses = SearchResponses()
    runtime = NativeToolRuntime(
        client=ResponsesClient(),
        registry=CapabilityRegistry(tools=[answerable_kb]),
        config={"customer_ceshi_runtime": {"search": {"max_local_kb_calls": 1, "max_web_calls": 1}}},
        mode="responses",
        responses_client=responses,
    )

    result = runtime.invoke({"messages": [HumanMessage(content="如何查询区域历史数据？")]}, {"configurable": {"thread_id": "answerable-search"}})

    assert result["generated_answer"] == "可在区域分析中查看历史数据。"
    assert result["generated_tool_calls"] == ["local_kb_search"]
    assert responses.calls[1]["tool_choice"] == "none"
    function_output = responses.calls[1]["input"][0]["output"]
    assert "区域分析页面支持查看历史数据" in function_output
    assert "items" not in function_output


def test_direct_update_candidate_requires_explicit_current_turn_identity_and_fields(monkeypatch):
    from skills_v2.skills.ship_info_update import adapter as ship_tools

    runtime = NativeToolRuntime(
        client=ChatToolClient(),
        registry=CapabilityRegistry(tools=[]),
        config={"customer_ceshi_runtime": {"direct_updates": {"enabled": True}}},
        mode="chat_function_calling",
    )
    invalid = runtime._execute_update_candidate(
        {"operation_type": "position_update", "mmsi": "123456789", "lon": "120.1", "lat": "30.2", "updatetime": "2026-07-15 10:00:00"},
        "更新船位，经度120.1 纬度30.2 时间2026-07-15 10:00:00",
    )
    assert invalid.status == "invalid_input"
    assert invalid.warnings == ["current_turn_mmsi_required"]

    monkeypatch.setattr(ship_tools, "upload_ship_position", SimpleNamespace(invoke=lambda args: {"status": "success", "updated_fields": ["lon", "lat", "updatetime"]}))
    valid_text = "请更新MMSI 123456789 的船位，经度120.1 纬度30.2，更新时间2026-07-15 10:00:00"
    valid = runtime._execute_update_candidate(
        {"operation_type": "position_update", "mmsi": "123456789", "lon": "120.1", "lat": "30.2", "updatetime": "2026-07-15 10:00:00"},
        valid_text,
    )
    assert valid.status == "success"
    assert valid.capability == "upload_ship_position"


@pytest.mark.xfail(reason="obsolete: media evidence cannot bypass prepare/confirm/commit")
def test_media_ais_evidence_allows_one_follow_up_direct_position_update(monkeypatch):
    from skills_v2.skills.ship_info_update import adapter as ship_tools

    calls = []
    monkeypatch.setattr(ship_tools, "upload_ship_position", SimpleNamespace(invoke=lambda args: (calls.append(args) or "上传成功")))
    media = MediaCandidateResponses()
    runtime = SingleModelCustomerCeshiRuntime(
        text_runtime=NativeToolRuntime(client=ChatToolClient(), registry=CapabilityRegistry(tools=[]), config={"customer_ceshi_runtime": {"direct_updates": {"enabled": True}}}, mode="chat_function_calling"),
        multimodal_responses_client=media,
        config={"customer_ceshi_runtime": {"direct_updates": {"enabled": True}}},
    )
    request = {"session_id": "ais-update", "user_id": "u", "messages": [HumanMessage(content=[{"type": "image_url", "image_url": {"url": "https://example.test/ais.png"}}, {"type": "text", "text": "这是什么"}])]}

    first = runtime.invoke(request, {"configurable": {"thread_id": "ais-update"}})
    second = runtime.invoke({"session_id": "ais-update", "user_id": "u", "messages": [HumanMessage(content="根据上述图片中的 AIS 数据更新船位")]}, {"configurable": {"thread_id": "ais-update"}})

    assert first["status"] == "success"
    assert "text" not in media.calls[0]
    assert any(tool["name"] == MEDIA_UPDATE_EVIDENCE_TOOL_NAME for tool in media.calls[0]["tools"])
    assert media.calls[1]["previous_response_id"] == "media-resp-1"
    assert second["status"] == "success"
    assert second["metrics"]["media_update_evidence"] is True
    assert len(calls) == 1
    assert calls[0]["mmsi"] == "413790872"
    assert calls[0]["lon"] == "116.1"
    assert "更新成功" in second["generated_answer"]


@pytest.mark.xfail(reason="obsolete: direct media writes are intentionally removed")
def test_media_ais_evidence_accepts_explicit_commands_and_confirm_only_once(monkeypatch):
    from skills_v2.skills.ship_info_update import adapter as ship_tools

    calls = []
    monkeypatch.setattr(ship_tools, "upload_ship_position", SimpleNamespace(invoke=lambda args: (calls.append(args) or "上传成功")))
    for command in ("更新船位", "请执行更新", "确认"):
        runtime = SingleModelCustomerCeshiRuntime(
            text_runtime=NativeToolRuntime(client=ChatToolClient(), registry=CapabilityRegistry(tools=[]), config={"customer_ceshi_runtime": {"direct_updates": {"enabled": True}}}, mode="chat_function_calling"),
            multimodal_responses_client=MediaCandidateResponses(),
            config={"customer_ceshi_runtime": {"direct_updates": {"enabled": True}}},
        )
        runtime.invoke({"session_id": command, "user_id": "u", "messages": [HumanMessage(content=[{"type": "image_url", "image_url": {"url": "https://example.test/ais.png"}}, {"type": "text", "text": "识别图片"}])]}, {"configurable": {"thread_id": command}})
        result = runtime.invoke({"session_id": command, "user_id": "u", "messages": [HumanMessage(content=command)]}, {"configurable": {"thread_id": command}})
        assert result["status"] == "success"
    assert len(calls) == 3


def test_media_evidence_never_crosses_sessions_or_enters_text_context():
    class TextResponses:
        class responses:
            pass

        def __init__(self):
            self.calls = []
            self.responses = SimpleNamespace(create=lambda **kwargs: (self.calls.append(kwargs) or SimpleNamespace(id="text", output=[], output_text="普通回复", usage={})))

    text = TextResponses()
    runtime = SingleModelCustomerCeshiRuntime(
        text_runtime=NativeToolRuntime(client=ResponsesClient(), registry=CapabilityRegistry(tools=[]), config={"customer_ceshi_runtime": {"direct_updates": {"enabled": True}}}, mode="responses", responses_client=text),
        multimodal_responses_client=MediaCandidateResponses(),
        config={"customer_ceshi_runtime": {"direct_updates": {"enabled": True}}},
    )
    runtime.invoke({"session_id": "one", "user_id": "u", "messages": [HumanMessage(content=[{"type": "image_url", "image_url": {"url": "https://secret.example/ais.png?signature=secret"}}, {"type": "text", "text": "识别"}])]}, {"configurable": {"thread_id": "one"}})
    result = runtime.invoke({"session_id": "two", "user_id": "u", "messages": [HumanMessage(content="确认")]}, {"configurable": {"thread_id": "two"}})

    assert result["generated_answer"] == "普通回复"
    assert "secret.example" not in text.calls[0]["input"]
    assert "413790872" not in text.calls[0]["input"]


def test_media_evidence_ledger_keeps_only_latest_unexpired_candidate():
    ledger = MediaUpdateEvidenceLedger({"ttl_seconds": 600, "minimum_confidence": "high"})
    ledger.put("u:s", MediaUpdateEvidence(fields={"mmsi": "111111111"}, media_types=("image",), source_turn_id="first"))
    ledger.put("u:s", MediaUpdateEvidence(fields={"mmsi": "222222222"}, media_types=("image",), source_turn_id="second"))

    evidence = ledger.get("u:s")

    assert evidence is not None
    assert evidence.fields["mmsi"] == "222222222"


def test_wechat_plain_text_strips_markdown_and_preserves_full_wechat_url():
    url = "https://open.weixin.qq.com/connect/oauth2/authorize?appid=wx9&redirect_uri=http://www.hifleet.com/wap-simple/index.html&state=413790872#wechat_redirect"
    answer = _wechat_plain_text(f"# 船位查询\n**GANG YANG 009**\n- 实时坐标：116.1,29.4\n[点击查看]({url})\n<a href=\"{url}\">备用链接</a>")

    assert not answer.startswith("#")
    assert "**" not in answer
    assert "\n- " not in answer
    assert "点击查看：" in answer
    assert "备用链接：" in answer
    assert "redirect_uri=" in answer
    assert _wechat_plain_text("重复\n内容\n重复") == "重复\n内容"


def test_media_envelope_is_not_exposed_when_wrapped_in_prose_or_code_fence():
    payload = '{"answer":"这是 AIS 船位截图。","media_update_candidate":{"operation_type":"position_update","mmsi":"413790872"}}'
    response = SimpleNamespace(output_text=f"识别结果如下：\n```json\n{payload}\n```", output=[])
    rendered = _wechat_plain_text(response.output_text, media_fallback="请补充问题。")

    assert rendered == "这是 AIS 船位截图。"
    assert "media_update_candidate" not in rendered
    assert "operation_type" not in rendered


def test_malformed_media_candidate_never_reaches_customer_output():
    rendered = _wechat_plain_text('识别结果：{"media_update_candidate":{"mmsi":"413790872"}}', media_fallback="请补充问题。")

    assert rendered == "请补充问题。"
    assert "413790872" not in rendered


def test_malformed_media_candidate_preserves_embedded_customer_answer():
    rendered = _wechat_plain_text('{"answer":"页面中的时间范围需要进一步核验。","media_update_candidate":{bad}', media_fallback="请补充问题。")

    assert rendered == "页面中的时间范围需要进一步核验。"
    assert "media_update_candidate" not in rendered


def test_position_query_prefers_verified_wechat_card_and_full_url():
    url = "https://open.weixin.qq.com/connect/oauth2/authorize?appid=wx9&redirect_uri=http://www.hifleet.com/wap-simple/index.html&state=413790872#wechat_redirect"
    tool_card = "GANG YANG 009\nMMSI: 413790872 | IMO: 1400704\n实时坐标：70.850130,18.730425\n<a href=\"%s\">点击查看</a>\n更新于: 2026-07-16 09:00:26 UTC+8\n航速: 6.5 节" % url
    answer = _wechat_position_result("413790872 现在到哪了", [{"status": "success", "capability": "get_ship_position", "facts": [tool_card]}])

    assert answer.startswith("GANG YANG 009")
    assert "点击查看：" in answer
    assert url in answer
    assert "<a " not in answer
    assert _wechat_position_result("查询船舶档案", [{"status": "success", "capability": "get_ship_position", "facts": [tool_card]}]) == ""


def test_position_query_formats_ship_search_current_dynamics_as_wechat_card():
    raw = "船舶GAN YANG 009的当前动态如下：\n船舶名称：GAN YANG 009\nMMSI：413790872\nIMO：1400704\n船型：干货船\n船旗：中国\n船长/船宽：117米/20米\n航行状态：系泊\n经度/纬度：117°31.008′E/30°43.826′N\n更新时间：2026-07-16 09:00:26 UTC+8\n航速：6.5节\n船首向/航迹向：61.8°/61.8°\n当前吃水：6.3米\n船报目的港：CLASS B\n更多信息可以查询HiFleet : https://www.hifleet.com/?_mmsi=413790872"

    answer = _wechat_position_result("413790872 现在到哪了", [{"status": "success", "capability": "ship_search", "facts": [raw]}])

    assert answer.startswith("GAN YANG 009")
    assert "实时坐标：117°31.008′E/30°43.826′N" in answer
    assert "点击查看：https://open.weixin.qq.com/connect/oauth2/authorize?" in answer
    assert "www.hifleet.com/?_mmsi" not in answer


def test_direct_update_feedback_is_plain_text_only():
    evidence = MediaUpdateEvidence(fields={"ship_name": "GANG YANG 009", "mmsi": "413790872", "lon": "116.1", "lat": "29.4", "updatetime": "2026-07-16 09:00:26"}, media_types=("image",), source_turn_id="test")
    feedback = SingleModelCustomerCeshiRuntime._update_feedback(evidence, SimpleNamespace(status="success", data={"updated_fields": ["lon", "lat"]}, facts=[]))

    assert "\n- " not in feedback
    assert "MMSI 413790872" in feedback


def test_customer_ceshi_profile_prompt_is_injected_into_native_runtime():
    runtime = NativeToolRuntime(
        client=ChatToolClient(),
        registry=CapabilityRegistry(tools=[]),
        config={},
        mode="chat_function_calling",
        profile_prompt="你是 HiFleet 企业客服。",
    )

    assert "HiFleet 企业客服" in runtime._system([]).content


def test_ship_lookup_scenario_keeps_knowledge_tools_available():
    from agents.customer_ceshi_responses.scenarios import classify

    runtime = NativeToolRuntime(
        client=ResponsesClient(),
        registry=CapabilityRegistry(tools=[local_kb_search]),
        perception=PerceptionService(FakePerception()),
        config={},
        mode="responses",
        responses_client=ResponsesClient(),
    )
    runtime._active_scenario = classify("咱们的船位跟踪是北京时间，还是GMT时间哈")
    assert runtime._active_scenario.name == "ship_lookup"
    tool_names = {tool["name"] for tool in runtime._responses_tools()}
    assert "local_kb_search" in tool_names
    assert "prepare_ship_update" in tool_names


def test_platform_operation_scenario_still_restricts_to_knowledge_tools():
    from agents.customer_ceshi_responses.scenarios import classify

    runtime = NativeToolRuntime(
        client=ResponsesClient(),
        registry=CapabilityRegistry(tools=[local_kb_search]),
        perception=PerceptionService(FakePerception()),
        config={},
        mode="responses",
        responses_client=ResponsesClient(),
    )
    runtime._active_scenario = classify("HiFleet 平台上传不了航线")
    assert runtime._active_scenario.name == "platform_operation"
    tool_names = {tool["name"] for tool in runtime._responses_tools()}
    assert "local_kb_search" in tool_names
    assert "prepare_ship_update" not in tool_names


def test_trajectory_span_exceeds_limit_returns_actionable_error():
    runtime = NativeToolRuntime(client=object(), registry=CapabilityRegistry(tools=[]), config={}, mode="chat_function_calling")
    observation = runtime._trajectory_span_check({"starttime": "2026-01-01", "endtime": "2026-07-23"})
    assert observation is not None
    assert observation.status == "upstream_error"
    assert "trajectory_span_exceeds_limit" in observation.warnings
    assert observation.retry_allowed is True
    assert "缩小" in observation.suggested_fix
    assert runtime._trajectory_span_check({"starttime": "2026-07-20", "endtime": "2026-07-23"}) is None


def test_trajectory_default_range_injected_when_missing():
    runtime = NativeToolRuntime(client=object(), registry=CapabilityRegistry(tools=[]), config={}, mode="chat_function_calling")
    defaulted = runtime._trajectory_default_range({})
    assert defaulted["starttime"]
    assert defaulted["endtime"]
    # Single-side ranges derive the missing bound within the configured day limit.
    only_start = runtime._trajectory_default_range({"starttime": "2025-01-01"})
    assert only_start["starttime"] == "2025-01-01"
    assert only_start["endtime"] == "2025-01-31"
    only_end = runtime._trajectory_default_range({"endtime": "2025-01-31"})
    assert only_end["endtime"] == "2025-01-31"
    assert only_end["starttime"] == "2025-01-01"


def test_trajectory_reverse_and_boundary_ranges():
    runtime = NativeToolRuntime(client=object(), registry=CapabilityRegistry(tools=[]), config={}, mode="chat_function_calling")
    # Reverse range is rejected with an actionable order-check message.
    reverse = runtime._trajectory_span_check({"starttime": "2026-07-23", "endtime": "2026-06-01"})
    assert reverse is not None
    assert reverse.status == "upstream_error"
    assert "trajectory_reverse_range" in reverse.warnings
    # A 30-day span is within the limit; a 31-day span is rejected.
    assert runtime._trajectory_span_check({"starttime": "2026-06-23", "endtime": "2026-07-23"}) is None
    exceeded = runtime._trajectory_span_check({"starttime": "2026-06-22", "endtime": "2026-07-23"})
    assert exceeded is not None
    assert "trajectory_span_exceeds_limit" in exceeded.warnings


def test_trajectory_dedup_and_budget_prevent_budget_burn():
    runtime = NativeToolRuntime(client=object(), registry=CapabilityRegistry(tools=[]), config={"customer_ceshi_trajectory_max_calls": 2}, mode="chat_function_calling")
    executed = []

    def fake_execute(name, arguments, assets):
        executed.append(dict(arguments))
        return Observation(status="success", capability=name, facts=["trajectory point"])

    runtime._execute = fake_execute
    fingerprints = set()
    counts = {"local_kb_search": 0, "web_search": 0}
    args = {"mmsi": "414726000", "starttime": "2026-07-20", "endtime": "2026-07-23"}
    assert runtime._resolve_bounded_observation("get_ship_trajectory", args, {}, fingerprints, counts, False).status == "success"
    assert runtime._resolve_bounded_observation("get_ship_trajectory", {**args, "endtime": "2026-07-22"}, {}, fingerprints, counts, False).status == "success"
    dup = runtime._resolve_bounded_observation("get_ship_trajectory", args, {}, fingerprints, counts, False)
    assert dup.status == "forbidden" and "duplicate_trajectory_query" in dup.warnings
    over = runtime._resolve_bounded_observation("get_ship_trajectory", {**args, "endtime": "2026-07-21"}, {}, fingerprints, counts, False)
    assert over.status == "forbidden" and "trajectory_budget_exhausted" in over.warnings
    assert len(executed) == 2


def test_text_position_update_flows_to_llm_without_preflight_short_circuit():
    class UpdateResponses:
        def __init__(self):
            self.calls = []

        def create(self, **kwargs):
            self.calls.append(kwargs)
            if len(self.calls) == 1:
                payload = {"operation_type": "position_update", "mmsi": "730285526", "longitude": "121°42′55″ E", "latitude": "39°01′55″ N", "updatetime": "2026-07-06 14:13:00 UTC+8"}
                return SimpleNamespace(id="upd-1", output=[SimpleNamespace(type="function_call", name="prepare_ship_update", arguments=__import__("json").dumps(payload), call_id="upd-call-1")], output_text="", usage={})
            return SimpleNamespace(id="upd-2", output=[], output_text="已生成更新草稿，请确认。", usage={})

    client = ResponsesClient()
    client.responses = UpdateResponses()
    text_runtime = NativeToolRuntime(client=client, registry=CapabilityRegistry(tools=[]), perception=PerceptionService(FakePerception()), config={"customer_ceshi_max_steps": 5}, mode="responses", responses_client=client)
    runtime = SingleModelCustomerCeshiRuntime(text_runtime=text_runtime)
    result = runtime.invoke({"messages": [HumanMessage(content="更新船位，MMSI 730285526，更新于2026-07-06 14:13:00 UTC+8，纬度 39°01′55″ N，经度 121°42′55″ E")]}, {"configurable": {"thread_id": "text-update"}})
    assert result["generated_tool_calls"] == ["prepare_ship_update"]
    assert result["metrics"]["model_calls"] >= 1
    prepared = [o for o in result["observations"] if o.get("capability") == "prepare_ship_update"]
    assert prepared and prepared[0]["status"] == "success"
