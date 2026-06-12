import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agents.customer_support_router import (
    KNOWLEDGE_BUNDLE,
    SHIP_QUERY_BUNDLE,
    SHIP_STATS_BUNDLE,
    SHIP_VOYAGE_BUNDLE,
    answer_conversation_memory,
    build_conversation_context,
    classify_message,
    execute_complex_ship_chain,
    execute_knowledge_chain,
    execute_simple_ship_chain,
    extract_entities,
    make_trace,
    resolve_entities_with_context,
    validate_links,
)
from langchain_core.messages import AIMessage, HumanMessage


class FakeTool:
    def __init__(self, name, handler):
        self.name = name
        self.handler = handler
        self.calls = []

    def invoke(self, args):
        self.calls.append(args)
        return self.handler(args)


def test_platform_question_kb_first_then_search_fallback():
    smart_search = FakeTool(
        "smart_search",
        lambda args: "未检索到足够可信的信息" if args["depth"] == "quick" else "【Hifleet官方站内搜索】\n🔗 https://www.hifleet.com/helpcenter/?i18n=zh",
    )
    text = "HiFleet 轨迹加载失败怎么办"
    entities = extract_entities(text)
    decision = classify_message(text, entities)
    trace = make_trace(decision, entities, session_id="s1")

    output = execute_knowledge_chain(text, decision, {"smart_search": smart_search}, trace)

    assert [c["depth"] for c in smart_search.calls] == ["normal"]
    assert "helpcenter" in output
    assert trace.tool_bundle == KNOWLEDGE_BUNDLE
    assert trace.check_result["links_ok"] is True


def test_knowledge_quick_kb_falls_back_to_normal_when_weak():
    smart_search = FakeTool(
        "smart_search",
        lambda args: "未找到精确的FAQ匹配" if args["depth"] == "quick" else "【优先匹配 - FAQ/标准回复】\n标准答案",
    )
    text = "HiFleet 绿点是什么意思"
    entities = extract_entities(text)
    decision = classify_message(text, entities)
    trace = make_trace(decision, entities)

    output = execute_knowledge_chain(text, decision, {"smart_search": smart_search}, trace)

    assert [c["depth"] for c in smart_search.calls] == ["quick", "normal"]
    assert "标准答案" in output
    assert trace.fallback_reason == "quick_kb_weak_hit"


def test_link_validation_removes_invalid_links():
    ok, invalid = validate_links(
        "参考 https://invalid.example/not-found 和 https://www.hifleet.com/helpcenter/?i18n=zh",
        checker=lambda url: "hifleet.com" in url,
    )

    assert ok is False
    assert invalid == ["https://invalid.example/not-found"]


def test_single_ship_position_uses_shrunk_ship_query_bundle():
    position = FakeTool("get_ship_position", lambda args: f"MMSI: {args['mmsi']}\n实时坐标：1,2")
    text = "查询 MMSI 414726000 船位"
    entities = extract_entities(text)
    decision = classify_message(text, entities)
    trace = make_trace(decision, entities)

    output = execute_simple_ship_chain(text, decision, entities, {"get_ship_position": position}, trace)

    assert decision.tool_bundle == SHIP_QUERY_BUNDLE
    assert position.calls == [{"mmsi": "414726000"}]
    assert "实时坐标" in output
    assert trace.tool_call_sequence == ["get_ship_position"]


def test_single_ship_position_resolves_bare_ship_name():
    search = FakeTool("ship_search", lambda args: "YU MING MMSI: 414726000 IMO: 9613886")
    position = FakeTool("get_ship_position", lambda args: f"MMSI: {args['mmsi']}\n实时坐标：1,2")
    text = "查询 yuming 船位"
    entities = extract_entities(text)
    decision = classify_message(text, entities)
    trace = make_trace(decision, entities)

    output = execute_simple_ship_chain(text, decision, entities, {"ship_search": search, "get_ship_position": position}, trace)

    assert entities.ship_name == "yuming"
    assert search.calls == [{"keyword": "yuming"}]
    assert position.calls == [{"mmsi": "414726000"}]
    assert "实时坐标" in output


def test_single_ship_position_resolves_chinese_ship_name():
    search = FakeTool("ship_search", lambda args: "育明 YU MING MMSI: 414726000 IMO: 9613886")
    position = FakeTool("get_ship_position", lambda args: f"MMSI: {args['mmsi']}\n实时坐标：1,2")
    text = "查询育明船位"
    entities = extract_entities(text)
    decision = classify_message(text, entities)
    trace = make_trace(decision, entities)

    output = execute_simple_ship_chain(text, decision, entities, {"ship_search": search, "get_ship_position": position}, trace)

    assert entities.ship_name == "育明"
    assert decision.route == "ship_single"
    assert search.calls == [{"keyword": "育明"}]
    assert position.calls == [{"mmsi": "414726000"}]
    assert "实时坐标" in output


def test_stats_route_uses_stats_bundle():
    entities = extract_entities("查询曼德海峡 2026-06-01 到 2026-06-02 通航统计")
    decision = classify_message("查询曼德海峡 2026-06-01 到 2026-06-02 通航统计", entities)

    assert decision.route == "ship_stats"
    assert decision.tool_bundle == SHIP_STATS_BUNDLE


def test_complex_ship_analysis_plan_act_check():
    tools = {
        "get_ship_archive": FakeTool("get_ship_archive", lambda args: "【基本信息】\n类型: 散货船\nIMO: 9613886"),
        "get_ship_position": FakeTool("get_ship_position", lambda args: "MMSI: 414726000\n船型: 训练船\n目的港: SHANGHAI"),
        "get_ship_call_ports": FakeTool("get_ship_call_ports", lambda args: "上一挂靠港: NINGBO"),
        "get_last_departure": FakeTool("get_last_departure", lambda args: "NINGBO 2026-06-01"),
        "get_ship_voyages": FakeTool("get_ship_voyages", lambda args: "NINGBO -> SHANGHAI"),
    }
    text = "查询 MMSI 414726000 目的港是什么，最近挂靠港是否与航次一致"
    entities = extract_entities(text)
    decision = classify_message(text, entities)
    trace = make_trace(decision, entities)

    output = execute_complex_ship_chain(text, entities, tools, trace)

    assert decision.tool_bundle == SHIP_VOYAGE_BUNDLE
    assert trace.check_result["position_ok"] is True
    assert trace.check_result["consistency_ok"] is False
    assert trace.loop_count == 0
    assert trace.tool_call_sequence == [
        "get_ship_archive",
        "get_ship_position",
        "get_ship_call_ports",
        "get_last_departure",
        "get_ship_voyages",
    ]
    assert "航次/目的港校验" in output
    assert "船型字段不一致" in output


def test_complex_ship_fallback_when_identifier_missing():
    text = "查询某船近期轨迹，上一次停靠在哪个港口"
    entities = extract_entities(text)
    decision = classify_message(text, entities)
    trace = make_trace(decision, entities)

    output = execute_complex_ship_chain(text, entities, {}, trace)

    assert "需要先确定唯一 MMSI" in output
    assert trace.fallback_reason == "complex_ship_missing_mmsi"


def test_context_memory_summary_does_not_route_to_search():
    messages = [
        HumanMessage(content="查询育明船位"),
        AIMessage(content="YU MING\nMMSI: 414726000 | IMO: 9613886"),
        HumanMessage(content="为什么更新这么慢"),
        AIMessage(content="船位更新慢通常和 AIS 上报频率、岸基接收、卫星覆盖有关。"),
        HumanMessage(content="上面我问了哪些问题，总结"),
    ]
    context = build_conversation_context(messages)
    text = "上面我问了哪些问题，总结"
    entities = resolve_entities_with_context(extract_entities(text), context)
    decision = classify_message(text, entities, context)
    output = answer_conversation_memory(text, context)

    assert decision.route == "conversation"
    assert "查询育明船位" in output
    assert "为什么更新这么慢" in output


def test_context_followup_reuses_last_ship_identity():
    messages = [
        HumanMessage(content="查询育明船位"),
        AIMessage(content="YU MING\nMMSI: 414726000 | IMO: 9613886"),
        HumanMessage(content="这艘船历史轨迹有哪些"),
    ]
    context = build_conversation_context(messages)
    text = "这艘船历史轨迹有哪些"
    entities = resolve_entities_with_context(extract_entities(text), context)
    decision = classify_message(text, entities, context)

    assert entities.mmsi == "414726000"
    assert entities.imo == "9613886"
    assert decision.route in {"ship_context", "ship_complex"}


def test_platform_troubleshooting_followup_beats_ship_update():
    messages = [
        HumanMessage(content="hifleet船位更新很慢"),
        HumanMessage(content="为什么更新这么慢"),
    ]
    context = build_conversation_context(messages)
    text = "为什么更新这么慢"
    entities = resolve_entities_with_context(extract_entities(text), context)
    decision = classify_message(text, entities, context)

    assert decision.route == "knowledge"
    assert decision.task_type == "platform_troubleshooting"


def test_ai_troubleshooting_reply_does_not_pollute_ship_context():
    messages = [
        HumanMessage(content="hifleet船位更新很慢"),
        AIMessage(content="AIS 数据依赖岸基和卫星接收，远海会有延迟。"),
        HumanMessage(content="为什么更新这么慢"),
    ]
    context = build_conversation_context(messages)
    text = "为什么更新这么慢"
    entities = resolve_entities_with_context(extract_entities(text), context)
    decision = classify_message(text, entities, context)

    assert context.last_ship_name == ""
    assert entities.ship_name == ""
    assert decision.task_type == "platform_troubleshooting"


def test_platform_troubleshooting_phrase_is_not_misclassified_as_write():
    text = "hifleet船位更新很慢"
    entities = extract_entities(text)
    decision = classify_message(text, entities)

    assert decision.route == "knowledge"
    assert decision.task_type == "platform_troubleshooting"
