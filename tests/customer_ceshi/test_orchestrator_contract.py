from types import SimpleNamespace

from langchain_core.messages import HumanMessage

from agents.customer_ceshi_responses.builder import NativeToolRuntime, ResponsesMediaPerception, SingleModelCustomerCeshiRuntime
from agents.customer_ceshi_responses.scenarios import classify
from agents.customer_ceshi_v2.contracts import InspectMediaRequest, MediaAsset, Observation, PerceptionPacket
from agents.customer_ceshi_v2.tools import CapabilityRegistry


class TextClient:
    def __init__(self):
        self.calls = 0

    def bind_tools(self, _tools):
        return self

    def invoke(self, _messages):
        self.calls += 1
        return SimpleNamespace(content="附件已收到；我会先核验后再说明。", tool_calls=[])


def test_media_keeps_text_runtime_as_orchestrator():
    text = TextClient()
    runtime = SingleModelCustomerCeshiRuntime(
        text_runtime=NativeToolRuntime(client=text, registry=CapabilityRegistry(tools=[]), config={}, mode="chat_function_calling")
    )
    result = runtime.invoke(
        {"messages": [HumanMessage(content=[{"type": "image_url", "image_url": {"url": "https://example.test/chart.png"}}, {"type": "text", "text": "这是什么"}])]},
        {"configurable": {"thread_id": "media"}},
    )
    assert result["status"] == "success"
    assert text.calls == 1


def test_tool_metadata_cannot_force_a_final_response():
    runtime = NativeToolRuntime(client=TextClient(), registry=CapabilityRegistry(tools=[]), config={}, mode="chat_function_calling")
    observation = Observation(status="success", capability="local_kb_search", data={"can_answer": True, "recommended_next_action": "直接回答"})
    assert runtime._can_answer_from(observation) is True
    # The Responses loop deliberately no longer consults this compatibility helper.
    assert "force_final = force_final or self._can_answer_from(observation)" not in open("src/agents/customer_ceshi_responses/builder.py", encoding="utf-8").read()


def test_perception_adapter_never_exposes_business_tools():
    adapter = ResponsesMediaPerception(None, {})
    assert adapter.client is None


def test_perception_adapter_uses_gateway_compatible_thinking_options(monkeypatch):
    class Responses:
        def __init__(self):
            self.request = None

        def create(self, **kwargs):
            self.request = kwargs
            return SimpleNamespace(output_text=(
                '{"factual_summary":"红色航标","ocr_blocks":[],"visual_objects":[],"entities":[],'
                '"fields":[],"visual_features":[],"overall_confidence":"high","limitations":[]}'
            ))

    class Files:
        def __init__(self):
            self.created = None
            self.deleted = []

        def create(self, **kwargs):
            self.created = kwargs
            return SimpleNamespace(id="file-1", status="completed")

        def retrieve(self, _file_id):
            return SimpleNamespace(id="file-1", status="completed")

        def delete(self, file_id):
            self.deleted.append(file_id)

    responses = Responses()
    files = Files()
    class Source:
        headers = SimpleNamespace(get=lambda _name, _default=None: None, get_content_type=lambda: "image/png")
        def read(self, _limit):
            return b"png-bytes"
        def __enter__(self):
            return self
        def __exit__(self, *_args):
            return None
    monkeypatch.setattr("agents.customer_ceshi_responses.builder.urlopen", lambda *_args, **_kwargs: Source())
    adapter = ResponsesMediaPerception(SimpleNamespace(responses=responses, files=files), {"customer_ceshi_runtime": {"responses": {"doubao": {"model": "doubao-test"}}}})
    packet = adapter.inspect(
        MediaAsset(asset_id="asset-1", kind="image", url="https://example.test/chart.png"),
        InspectMediaRequest(asset_id="asset-1", objective="识别航标"),
    )
    assert isinstance(packet, PerceptionPacket)
    assert responses.request["extra_body"] == {"thinking": {"type": "disabled"}}
    assert "thinking" not in responses.request
    assert files.created["purpose"] == "user_data"
    assert files.deleted == ["file-1"]
    assert responses.request["input"][0]["content"][1] == {"type": "input_image", "file_id": "file-1", "detail": "high"}


def test_perception_adapter_normalizes_common_provider_json_shapes():
    payload = ResponsesMediaPerception._normalize_packet_payload(
        {"factual_summary": {"颜色": "红色"}, "fields": {"颜色": "红色"}, "overall_confidence": 0.92, "visual_features": [{"shape": "circle"}]},
        MediaAsset(asset_id="asset-1", kind="image", url="https://example.test/chart.png"),
        InspectMediaRequest(asset_id="asset-1", objective="识别航标"),
    )
    packet = PerceptionPacket.model_validate({**payload, "model": "doubao-test"})
    assert packet.overall_confidence == "high"
    assert packet.fields[0].name == "颜色"
    assert packet.fields[0].asset_id == "asset-1"
    assert packet.factual_summary == '{"颜色":"红色"}'
    assert packet.visual_features == ['{"shape":"circle"}']


def test_perception_adapter_discards_invalid_structured_list_items():
    class Responses:
        def create(self, **_kwargs):
            return SimpleNamespace(output_text='{"factual_summary": "x", "overall_confidence": "high", "ocr_blocks": ["not-an-object"]}')

    class Files:
        def create(self, **_kwargs):
            return SimpleNamespace(id="file-1", status="completed")

        def delete(self, _file_id):
            return None

    adapter = ResponsesMediaPerception(SimpleNamespace(responses=Responses(), files=Files()), {"customer_ceshi_runtime": {"responses": {"doubao": {"model": "doubao-test"}}}})
    adapter._upload_for_perception = lambda *_args: "file-1"
    result = adapter.inspect(MediaAsset(asset_id="asset-1", kind="image", url="https://example.test/chart.png"), InspectMediaRequest(asset_id="asset-1", objective="识别"))
    assert isinstance(result, PerceptionPacket)
    assert result.ocr_blocks == []


def test_position_update_only_advertises_transaction_and_ship_tools():
    runtime = NativeToolRuntime(client=TextClient(), registry=CapabilityRegistry(tools=[]), config={}, mode="responses")
    runtime._active_scenario = classify("更新船位，MMSI 123456789，经度 120E，纬度 30N")
    assert {tool["name"] for tool in runtime._responses_tools()} == {"prepare_ship_update", "commit_ship_update", "cancel_ship_update"}


def test_audio_input_is_exposed_to_the_multimodal_adapter():
    human = HumanMessage(content=[{"type": "input_audio", "input_audio": {"url": "https://example.test/audio.wav"}}, {"type": "text", "text": "请转写"}])
    content, kinds = SingleModelCustomerCeshiRuntime._media_content(human, "", "", {"video_fps": 1})
    assert kinds == ["audio"]
    assert any(item["type"] == "input_audio" for item in content)


def test_asset_first_turn_only_advertises_inspect_media():
    runtime = NativeToolRuntime(client=TextClient(), registry=CapabilityRegistry(tools=[]), perception=object(), config={}, mode="responses")
    assert [tool["name"] for tool in runtime._responses_tools(inspect_only=True)] == ["inspect_media"]
    assert "inspect_media" not in {tool["name"] for tool in runtime._responses_tools(exclude_media=True)}


def test_chart_symbol_prompt_requires_evidence_after_perception():
    runtime = NativeToolRuntime(client=TextClient(), registry=CapabilityRegistry(tools=[]), config={}, mode="responses")
    runtime._active_scenario = classify("这个海图符号是什么意思", has_media=True)
    prompt = runtime._system([MediaAsset(asset_id="asset-1", kind="image", url="https://example.test/chart.png")]).content
    assert "before naming the symbol" in prompt
    assert "Visible color or shape alone is never enough" in prompt
    assert "at most one inspect_media call" in prompt


def test_chart_symbol_scenario_has_single_media_call_budget():
    runtime = NativeToolRuntime(client=TextClient(), registry=CapabilityRegistry(tools=[]), config={}, mode="responses")
    runtime._active_scenario = classify("这个海图符号是什么意思", has_media=True)
    assert runtime._active_scenario.name == "multimodal_symbol"


def test_chart_symbol_tool_budget_uses_model_finalization_turn():
    source = open("src/agents/customer_ceshi_responses/builder.py", encoding="utf-8").read()
    assert "tool_call_limit = 3 if is_chart_symbol" in source
    assert "工具调用预算已到上限" in source
    assert '"tool_choice": "none"' in source


def test_media_recovery_path_is_present_for_provider_missing_tool_call():
    source = open("src/agents/customer_ceshi_responses/builder.py", encoding="utf-8").read()
    assert "if assets and self.perception is not None and not media_recovery_used" in source
    assert "附件观察结果（仅作为事实证据）" in source


def test_result_metrics_expose_effective_runtime_and_sanitized_response_suffix():
    runtime = NativeToolRuntime(client=TextClient(), registry=CapabilityRegistry(tools=[]), config={}, mode="responses")
    result = runtime._result("您好", [Observation(status="upstream_error", capability="inspect_media", warnings=["media_perception_error:BadRequestError"]).model_dump()], [], 1, 0, 1, "stop", "not_required", 0.0, "resp_abcdefghijklmnop", "", "")
    assert result["metrics"]["effective_runtime"] == "responses"
    assert result["metrics"]["response_id_suffix"] == "efghijklmnop"
    assert result["metrics"]["media_statuses"] == ["upstream_error"]
    assert result["metrics"]["media_error_codes"] == ["media_perception_error:BadRequestError"]
    assert result["metrics"]["tool_names"] == []


def test_media_observation_retains_bounded_visual_and_ocr_facts():
    class Perception:
        def inspect(self, *_args):
            return PerceptionPacket(
                asset_id="asset-1", media_type="image", model="doubao-test", requested_objective="识别",
                factual_summary="红色圆形标记", visual_features=["中心有黑点"],
                ocr_blocks=[{"text": "图例"}], visual_objects=[{"label": "圆形航标"}],
            )

    runtime = NativeToolRuntime(client=TextClient(), registry=CapabilityRegistry(tools=[]), perception=Perception(), config={}, mode="responses")
    observation = runtime._execute("inspect_media", {"asset_id": "asset-1", "objective": "识别"}, {"asset-1": MediaAsset(asset_id="asset-1", kind="image", url="https://example.test/chart.png")})
    assert "红色圆形标记" in observation.facts
    assert "中心有黑点" in observation.facts
    assert "OCR：图例" in observation.facts


def test_perception_prompt_prioritizes_annotated_regions():
    source = open("src/agents/customer_ceshi_responses/builder.py", encoding="utf-8").read()
    assert "箭头、圆圈、方框、荧光笔" in source
    assert "不得因截图整体复杂而忽略标注区域" in source
