import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agents.customer_support_router import (
    Attachment,
    FILE_BUNDLE,
    KNOWLEDGE_BUNDLE,
    MULTIMODAL_BUNDLE,
    SHIP_QUERY_BUNDLE,
    SHIP_STATS_BUNDLE,
    SHIP_UPDATE_BUNDLE,
    SHIP_VOYAGE_BUNDLE,
    answer_conversation_memory,
    build_conversation_context,
    classify_message,
    classify_multimodal_message,
    execute_complex_ship_chain,
    execute_browser_verify_chain,
    execute_file_chain,
    execute_knowledge_chain,
    execute_multimodal_chain,
    execute_simple_ship_chain,
    execute_update_chain,
    extract_attachments,
    extract_entities,
    make_trace,
    refine_multimodal_route_with_perception,
    resolve_entities_with_context,
    should_use_ship_context,
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
    assert "优先匹配" not in output
    assert "回答指导" not in output


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


def test_update_chain_executes_when_fields_are_complete():
    upload = FakeTool("upload_ship_position", lambda args: f"更新成功 MMSI={args['mmsi']} lon={args['lon']} lat={args['lat']}")
    text = "请更新船位 MMSI 414726000 经度 121.4737 纬度 31.2304 更新时间 2026-06-15 10:20:30"
    entities = extract_entities(text)
    decision = classify_message(text, entities)
    trace = make_trace(decision, entities)

    output = execute_update_chain(text, entities, {"upload_ship_position": upload}, trace)

    assert decision.tool_bundle == SHIP_UPDATE_BUNDLE
    assert upload.calls == [
        {
            "mmsi": "414726000",
            "lon": "121.4737",
            "lat": "31.2304",
            "updatetime": "2026-06-15 10:20:30",
        }
    ]
    assert "更新成功" in output
    assert trace.check_result["write_result"] is True


def test_update_chain_asks_one_key_question_when_mmsi_missing():
    text = "请更新船位，经度 121.4737 纬度 31.2304"
    entities = extract_entities(text)
    decision = classify_message(text, entities)
    trace = make_trace(decision, entities)

    output = execute_update_chain(text, entities, {}, trace)

    assert "请提供 9 位 MMSI" in output
    assert trace.fallback_reason == "update_requires_mmsi"


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


def test_multimodal_chart_symbol_routes_to_chart_symbol():
    text = "图中这个海图符号是什么意思"
    base = classify_message(text, extract_entities(text))
    decision = classify_multimodal_message(text, [Attachment(type="image", url="https://example.com/a.png", filename="a.png")], base)

    assert decision.route == "chart_symbol"
    assert decision.task_type == "chart_symbol"
    assert decision.tool_bundle == MULTIMODAL_BUNDLE
    assert decision.search_depth == "deep"


def test_file_attachment_routes_to_file_task():
    text = "帮我分析这个 Excel 文件并生成报告"
    base = classify_message(text, extract_entities(text))
    decision = classify_multimodal_message(text, [Attachment(type="file", url="https://example.com/a.xlsx", filename="a.xlsx")], base)

    assert decision.route == "file_task"
    assert decision.tool_bundle == FILE_BUNDLE


def test_extract_attachments_from_human_multimodal_content():
    messages = [
        HumanMessage(
            content=[
                {"type": "image_url", "image_url": {"url": "https://example.com/chart.png"}},
                {"type": "text", "text": "这个符号是什么意思"},
            ]
        )
    ]

    attachments = extract_attachments(messages)

    assert len(attachments) == 1
    assert attachments[0].type == "image"
    assert attachments[0].filename == "chart.png"


def test_multimodal_chain_combines_perception_and_deep_search():
    smart_search = FakeTool("smart_search", lambda args: f"query={args['query']} depth={args['depth']}")
    inspect = FakeTool("inspect_media_attachment", lambda args: '{"category":"image"}')
    decision = classify_multimodal_message(
        "这个符号是什么意思",
        [Attachment(type="image", url="https://example.com/chart.png")],
        classify_message("这个符号是什么意思", extract_entities("这个符号是什么意思")),
    )
    trace = make_trace(decision, extract_entities("这个符号是什么意思"))

    output = execute_multimodal_chain(
        "这个符号是什么意思",
        [Attachment(type="image", url="https://example.com/chart.png")],
        {"confidence": "high", "summary": "红色圆圈中心黑点", "suspected_symbol": "安全水域浮标"},
        decision,
        {"smart_search": smart_search, "inspect_media_attachment": inspect},
        trace,
    )

    assert "安全水域浮标" in output
    assert "红色圆圈中心黑点" in smart_search.calls[0]["query"]
    assert smart_search.calls[0]["depth"] == "deep"
    assert trace.tool_call_sequence == ["inspect_media_attachment", "smart_search"]


def test_multimodal_error_screenshot_reroutes_to_platform_troubleshooting():
    text = "请分析这张图片，并结合用户问题作答。"
    attachments = [Attachment(type="image", url="https://example.com/error.png")]
    base = classify_multimodal_message(text, attachments, classify_message(text, extract_entities(text)))

    decision = refine_multimodal_route_with_perception(
        text,
        attachments,
        {
            "confidence": "high",
            "summary": "HiFleet 页面弹出 Error 弹窗",
            "visible_text": "Error 确定",
            "suspected_issue": "页面加载失败或服务异常",
        },
        base,
    )

    assert base.route == "multimodal_understanding"
    assert decision.route == "knowledge"
    assert decision.task_type == "platform_troubleshooting"
    assert decision.tool_bundle == KNOWLEDGE_BUNDLE


def test_multimodal_error_screenshot_answer_is_customer_friendly():
    smart_search = FakeTool("smart_search", lambda args: "【Hifleet官方站内搜索】\n🔗 https://www.hifleet.com/helpcenter/?i18n=zh")
    inspect = FakeTool("inspect_media_attachment", lambda args: '{"category":"image"}')
    decision = refine_multimodal_route_with_perception(
        "请分析这张图片，并结合用户问题作答。",
        [Attachment(type="image", url="https://example.com/error.png")],
        {
            "confidence": "high",
            "summary": "HiFleet 页面弹出 Error 弹窗",
            "visible_text": "Error 确定",
            "suspected_issue": "页面加载失败或服务异常",
        },
        classify_multimodal_message(
            "请分析这张图片，并结合用户问题作答。",
            [Attachment(type="image", url="https://example.com/error.png")],
            classify_message("请分析这张图片，并结合用户问题作答。", extract_entities("请分析这张图片，并结合用户问题作答。")),
        ),
    )
    trace = make_trace(decision, extract_entities("请分析这张图片，并结合用户问题作答。"))

    output = execute_knowledge_chain(
        "请分析这张图片，并结合用户问题作答。 HiFleet 页面弹出 Error 弹窗 页面加载失败或服务异常",
        decision,
        {"smart_search": smart_search, "inspect_media_attachment": inspect},
        trace,
    )

    assert "页面或网络加载异常" in output
    assert "可参考官方帮助中心" in output
    assert "【Hifleet官方站内搜索】" not in output


def test_file_chain_inspects_customer_file():
    inspect = FakeTool("inspect_customer_file", lambda args: '{"ok":true,"category":"document","text":"rows=10"}')
    decision = classify_multimodal_message(
        "分析文件",
        [Attachment(type="file", url="https://example.com/a.csv")],
        classify_message("分析文件", extract_entities("分析文件")),
    )
    trace = make_trace(decision, extract_entities("分析文件"))

    output = execute_file_chain("分析文件", [Attachment(type="file", url="https://example.com/a.csv")], decision, {"inspect_customer_file": inspect}, trace)

    assert "rows=10" in output
    assert inspect.calls == [{"file_url": "https://example.com/a.csv"}]
    assert trace.check_result["inspected"] is True


def test_browser_verify_chain_checks_public_url_and_searches():
    verify = FakeTool("verify_public_page", lambda args: '{"ok":true,"title":"HiFleet 官方社区"}')
    search = FakeTool("smart_search", lambda args: "【Hifleet官方站内搜索】\n来源：官方社区")
    text = "核验 https://www.hifleet.com/wp/communities 的官方信息"
    entities = extract_entities(text)
    decision = classify_message(text, entities)
    decision.route = "browser_verify"
    decision.task_type = "browser_verify"
    decision.tool_bundle = ["verify_public_page", "smart_search"]
    trace = make_trace(decision, entities)

    output = execute_browser_verify_chain(text, entities, decision, {"verify_public_page": verify, "smart_search": search}, trace)

    assert "HiFleet 官方社区" in output
    assert "官方社区" in output
    assert verify.calls == [{"url": "https://www.hifleet.com/wp/communities"}]
    assert trace.check_result["verified"] is True


def test_reference_02_route_upload_failure_returns_layered_troubleshooting():
    search = FakeTool("smart_search", lambda args: "计划航线支持手绘、上传航线文件、航程规划或邮箱登记方式建立。")
    text = "hifleet平台上传不了航线怎么办"
    entities = extract_entities(text)
    decision = classify_message(text, entities)
    trace = make_trace(decision, entities)

    output = execute_knowledge_chain(text, decision, {"smart_search": search}, trace)

    assert "优先排查文件格式和内容问题" in output
    assert "xls、csv、xml、rux、rx4、rtz" in output
    assert "浏览器" in output
    assert "报错截图" in output
    assert "参考检索结果" not in output


def test_general_knowledge_answer_does_not_expose_search_wrapper():
    search = FakeTool(
        "smart_search",
        lambda args: "【互联网搜索结果（增强版）】\n\n📋 **AI摘要**：当前您未提供待识别图标的对应图片，也没有补充该图标相关的外观特征。\n\n**HiFleet 帮助中心**  权威\n摘要: 官方平台使用与问题排查文档入口\nhttps://www.hifleet.com/helpcenter/?i18n=zh\n\n---\n【回答指导】\n- 综合多个来源回答，标注信息来源。",
    )
    text = "这是什么图标"
    entities = extract_entities(text)
    decision = classify_message(text, entities)
    trace = make_trace(decision, entities)

    output = execute_knowledge_chain(text, decision, {"smart_search": search}, trace)

    assert "请只补充一个关键信息：图标原图或更清晰的截图" in output
    assert "【互联网搜索结果（增强版）】" not in output
    assert "AI摘要" not in output
    assert "回答指导" not in output


def test_reference_04_methodology_summary_is_customer_safe():
    output = answer_conversation_memory(
        "基于上述对输入的思考与回复，总结是如何思索和检索资源并审查确定的，详细介绍逻辑",
        build_conversation_context([HumanMessage(content="这个在全球海图里是什么意思")]),
    )

    assert "先识别问题类型" in output
    assert "检索顺序" in output
    assert "不展示内部工具" in output


def test_non_ship_multimodal_route_does_not_reuse_previous_ship_context():
    messages = [
        HumanMessage(content="查询育明船位"),
        AIMessage(content="YU MING\nMMSI: 414726000 | IMO: 9613886"),
    ]
    context = build_conversation_context(messages)
    entities = resolve_entities_with_context(
        extract_entities("请分析这张图片，并结合用户问题作答。"),
        context,
        allow_ship_context=should_use_ship_context("multimodal_understanding"),
    )

    assert entities.mmsi == ""
    assert entities.imo == ""
    assert entities.ship_name == ""
