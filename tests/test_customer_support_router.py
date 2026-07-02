import sys
import json
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
    build_customer_support_plan,
    classify_message,
    classify_multimodal_message,
    execute_complex_ship_chain,
    execute_browser_verify_chain,
    execute_file_chain,
    execute_knowledge_chain,
    execute_planned_knowledge_chain,
    execute_multimodal_chain,
    execute_simple_ship_chain,
    execute_update_chain,
    format_unverified_chart_symbol_answer,
    format_verified_chart_symbol_answer,
    extract_attachments,
    extract_entities,
    make_trace,
    refine_multimodal_route_with_perception,
    review_evidence_items,
    resolve_entities_with_context,
    should_use_ship_context,
    validate_links,
    _generate_knowledge_expansion_query,
    _rewrite_hifleet_knowledge_query,
)
from agents.customer_support_guard import sanitize_customer_output
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from skills.browser_verify.tools import (
    PREFERRED_HIFLEET_PAGES,
    AGENT_BROWSER_SESSION_PREFIX,
    AGENT_BROWSER_OPEN_TIMEOUT_SEC,
    _candidate_urls,
    _candidate_priority,
    _merge_agent_browser_args,
    _new_agent_browser_session,
    _run_agent_browser,
    _query_variants,
    _bing_search_candidates,
    _preferred_hifleet_candidates,
    _browser_capture_page_text,
    agent_browser_deep_search,
)


class FakeTool:
    def __init__(self, name, handler):
        self.name = name
        self.handler = handler
        self.calls = []

    def invoke(self, args):
        self.calls.append(args)
        return self.handler(args)


def test_knowledge_chain_escalates_to_web_search_agent_browser():
    call_sequence = []

    local_kb = FakeTool(
        "local_kb_search",
        lambda args: call_sequence.append("local_kb_search") or json.dumps(
            {"tool": "local_kb_search", "status": "ok", "can_answer": False, "should_continue": True, "items": [], "summary": "no hit"},
            ensure_ascii=False,
        ),
    )
    web_search = FakeTool(
        "web_search",
        lambda args: call_sequence.append("web_search") or json.dumps(
            {
                "tool": "web_search",
                "query": args["query"],
                "status": "ok",
                "can_answer": False,
                "should_continue": True,
                "continue_with": "agent_browser",
                "summary": "需要抓取正文",
                "items": [{"title": "HiFleet 轨迹", "url": "https://www.hifleet.com/wp/communities/fleet/export", "snippet": "摘要不足", "is_hifleet_official": True}],
                "best_urls": ["https://www.hifleet.com/wp/communities/fleet/export"],
                "trace": {"result_profile": {"result_count": 1, "official_count": 1}, "request_profile": {}, "used_ark_fallback": False},
            },
            ensure_ascii=False,
        ),
    )
    browser = FakeTool(
        "web_search_agent_browser",
        lambda args: call_sequence.append("web_search_agent_browser") or json.dumps(
            {
                "tool": "web_search_agent_browser",
                "status": "ok",
                "can_answer": True,
                "pages": [
                    {
                        "title": "HiFleet 轨迹导出",
                        "url": "https://www.hifleet.com/wp/communities/fleet/export",
                        "excerpt": "HiFleet 平台支持在船舶详情页查看并导出船舶轨迹。",
                        "official": True,
                    }
                ],
            },
            ensure_ascii=False,
        ),
    )
    
    text = "HiFleet 船舶轨迹怎么导出"
    entities = extract_entities(text)
    decision = classify_message(text, entities)
    trace = make_trace(decision, entities, session_id="s1")
    
    tool_map = {"local_kb_search": local_kb, "web_search": web_search, "web_search_agent_browser": browser}
    output = execute_knowledge_chain(text, decision, tool_map, trace)

    assert call_sequence == ["local_kb_search", "web_search", "web_search_agent_browser"]
    assert trace.reasoning_trace["retrieval_trace"]["t2_tool"] == "web_search_agent_browser"


def test_knowledge_chain_runs_multiple_understanding_queries_before_answering():
    local_kb = FakeTool(
        "local_kb_search",
        lambda args: json.dumps(
            {"tool": "local_kb_search", "status": "ok", "can_answer": False, "should_continue": True, "items": [], "summary": "no hit"},
            ensure_ascii=False,
        ),
    )

    def web_handler(args):
        if "我的标注" in args["query"]:
            return json.dumps(
                {
                    "tool": "web_search",
                    "query": args["query"],
                    "status": "ok",
                    "can_answer": True,
                    "should_continue": False,
                    "continue_with": "none",
                    "summary": "命中 HiFleet 官方具体页面且包含步骤/处置证据",
                    "items": [
                        {
                            "title": "HiFleet 区域标注操作说明",
                            "url": "https://www.hifleet.com/wp/communities/fleet/area-marker-steps",
                            "snippet": "主海图页面右上角点击标注，进入我的标注，选择区域标注后绘制并保存。",
                            "is_hifleet_official": True,
                        }
                    ],
                    "best_urls": [],
                    "trace": {
                        "question_class": "how_to_operate",
                        "request_profile": {"Filter": {"Sites": "hifleet.com"}},
                        "result_profile": {"result_count": 1, "official_count": 1},
                        "risk_flags": [],
                        "used_ark_fallback": False,
                    },
                },
                ensure_ascii=False,
            )
        return json.dumps(
            {
                "tool": "web_search",
                "query": args["query"],
                "status": "ok",
                "can_answer": False,
                "should_continue": True,
                "continue_with": "web_search_refine",
                "summary": "当前结果缺少可直接回答的步骤或处置证据",
                "items": [],
                "best_urls": [],
                "trace": {
                    "question_class": "how_to_operate",
                    "request_profile": {"Filter": {"Sites": "hifleet.com"}},
                    "result_profile": {"result_count": 0, "official_count": 0},
                    "risk_flags": ["insufficient_step_evidence"],
                    "used_ark_fallback": False,
                },
            },
            ensure_ascii=False,
        )

    web_search = FakeTool("web_search", web_handler)
    text = "怎么绘制区域标注"
    entities = extract_entities(text)
    decision = classify_message(text, entities)
    trace = make_trace(decision, entities, session_id="multi-query")
    trace.reasoning_trace = {
        "understanding_result": {
            "query_type": "hifleet_product",
            "rewritten_user_need": "用户想了解 HiFleet 如何绘制区域标注",
            "search_keywords": ["区域标注", "电子围栏", "我的标注"],
            "search_query_candidates": [
                "HiFleet 区域标注 绘制 步骤",
                "HiFleet 电子围栏 标注及电子围栏报警",
                "HiFleet 我的标注 区域标注 编辑 报警",
            ],
            "should_prefer_local_kb": True,
            "should_limit_to_hifleet_sites": True,
        }
    }

    output = execute_knowledge_chain(text, decision, {"local_kb_search": local_kb, "web_search": web_search}, trace)

    assert len(web_search.calls) == 3
    assert web_search.calls[-1]["query"] == "HiFleet 我的标注 区域标注 编辑 报警"
    assert trace.check_result["multi_query_synthesis"] is True
    assert trace.reasoning_trace["retrieval_trace"]["query_plan"][:3] == [
        "HiFleet 区域标注 绘制 步骤",
        "HiFleet 电子围栏 标注及电子围栏报警",
        "HiFleet 我的标注 区域标注 编辑 报警",
    ]
    assert "右上角点击标注" in output


def test_area_marker_local_kb_faq_is_direct_answer():
    from skills.knowledge_qa.tools import local_kb_search

    payload = json.loads(local_kb_search.invoke({"query": "怎么绘制区域标注", "top_k": 3}))

    assert payload["can_answer"] is True
    assert payload["items"][0]["source_type"] == "faq"
    assert "保存" in payload["items"][0]["content"]
    assert "多边形" in payload["items"][0]["content"]
    

def test_knowledge_chain_stops_when_local_kb_can_answer():
    call_sequence = []

    local_kb = FakeTool(
        "local_kb_search",
        lambda args: call_sequence.append("local_kb_search") or json.dumps(
            {
                "tool": "local_kb_search",
                "status": "ok",
                "can_answer": True,
                "should_continue": False,
                "items": [{"title": "轨迹导出", "content": "导出轨迹：在船舶详情页点击导出轨迹按钮。", "source_type": "faq", "score": 0.95}],
                "summary": "hit",
            },
            ensure_ascii=False,
        ),
    )
    web_search = FakeTool("web_search", lambda args: call_sequence.append("web_search") or "{}")
    
    text = "HiFleet 船舶轨迹怎么导出"
    entities = extract_entities(text)
    decision = classify_message(text, entities)
    trace = make_trace(decision, entities, session_id="s1")
    
    tool_map = {"local_kb_search": local_kb, "web_search": web_search}
    output = execute_knowledge_chain(text, decision, tool_map, trace)

    assert call_sequence == ["local_kb_search"]
    assert "导出轨迹" in output


def test_planned_knowledge_chain_escalates_to_web_search_agent_browser():
    call_sequence = []

    local_kb = FakeTool(
        "local_kb_search",
        lambda args: call_sequence.append("local_kb_search") or json.dumps({"tool": "local_kb_search", "can_answer": False, "should_continue": True, "items": []}, ensure_ascii=False),
    )
    web_search = FakeTool(
        "web_search",
        lambda args: call_sequence.append("web_search") or json.dumps(
            {
                "tool": "web_search",
                "query": args["query"],
                "can_answer": False,
                "should_continue": True,
                "continue_with": "agent_browser",
                "items": [{"title": "API", "url": "https://www.hifleet.com/data/index.html", "snippet": "候选页面", "is_hifleet_official": True}],
                "best_urls": ["https://www.hifleet.com/data/index.html"],
                "trace": {"result_profile": {"result_count": 1, "official_count": 1}, "request_profile": {}, "used_ark_fallback": False},
            },
            ensure_ascii=False,
        ),
    )
    browser = FakeTool(
        "web_search_agent_browser",
        lambda args: call_sequence.append("web_search_agent_browser") or json.dumps(
            {
                "tool": "web_search_agent_browser",
                "can_answer": True,
                "pages": [{"title": "HiFleet 数据服务", "url": "https://www.hifleet.com/data/index.html", "excerpt": "公开页面包含 API 数据服务说明。", "official": True}],
            },
            ensure_ascii=False,
        ),
    )
    
    text = "HiFleet API 调用频率限制是多少"
    entities = extract_entities(text)
    decision = classify_message(text, entities)
    trace = make_trace(decision, entities, session_id="s1")
    
    tool_map = {"local_kb_search": local_kb, "web_search": web_search, "web_search_agent_browser": browser}
    search_plan = [{"query": "API 频率限制", "depth": "quick", "hypothesis_id": "H1", "purpose": "测试"}]
    
    output, evidence_items, evidence_summary = execute_planned_knowledge_chain(
        text, decision, search_plan, tool_map, trace
    )
    
    assert call_sequence == ["local_kb_search", "web_search", "web_search_agent_browser"]

    browser_evidence = [e for e in evidence_items if e.get("source_name") == "web_search_agent_browser"]
    assert len(browser_evidence) == 1
    assert browser_evidence[0]["source_type"] == "official_site"


def test_agent_browser_output_is_sanitized_for_customer():
    """Test that agent_browser_deep_search output does not expose internal details to customer."""
    def browser_search_handler(args):
        # Simulate output that might contain internal details
        return json.dumps({
            "ok": True,
            "query": "test",
            "skill_content_preview": "Internal CLI output...",
            "note": "This is a controlled deep search fallback."
        })
    
    agent_browser = FakeTool("agent_browser_deep_search", browser_search_handler)
    
    text = "HiFleet 新功能"
    entities = extract_entities(text)
    decision = classify_message(text, entities)
    trace = make_trace(decision, entities, session_id="s1")
    
    tool_map = {
        "smart_search": FakeTool("smart_search", lambda args: "未检索到足够可信的信息"),
        "agent_browser_deep_search": agent_browser,
    }
    output = execute_knowledge_chain(text, decision, tool_map, trace)
    
    # Sanitize and verify no internal paths/commands exposed
    sanitized = sanitize_customer_output(output)
    assert "subprocess" not in sanitized
    assert "/usr/bin" not in sanitized
    assert "agent-browser CLI" not in sanitized or "公开资料" in sanitized


def test_agent_browser_deep_search_formats_public_page_results(monkeypatch):
    monkeypatch.setattr("skills.browser_verify.tools._sandbox_hifleet_candidates", lambda query: [])
    monkeypatch.setattr(
        "skills.browser_verify.tools._candidate_urls",
        lambda query: [{"url": "https://www.hifleet.com/helpcenter/?i18n=zh", "title": "HiFleet 帮助中心", "summary": "官方说明"}],
    )
    monkeypatch.setattr(
        "skills.browser_verify.tools._browser_capture_page_text",
        lambda url, query="", session="": ("HiFleet 帮助中心", "这里是帮助中心正文摘要，包含轨迹导出功能说明。", {"image_count": 0, "screenshot_path": "", "used_snapshot": False}),
    )

    output = agent_browser_deep_search.invoke({"query": "HiFleet 船舶轨迹怎么导出"})
    payload = json.loads(output)

    assert payload["type"] == "hifleet_browser_evidence"
    assert payload["search_strategy"]["official_first"] is True
    assert payload["pages"][0]["title"] == "HiFleet 帮助中心"
    assert "轨迹导出功能说明" in payload["pages"][0]["excerpt"]
    assert payload["pages"][0]["url"] == "https://www.hifleet.com/helpcenter/?i18n=zh"
    assert payload["pages"][0]["used_snapshot"] is False


def test_agent_browser_deep_search_rejects_invalid_query_characters():
    output = agent_browser_deep_search.invoke({"query": "HiFleet; rm -rf /"})

    assert "未检索到足够可信的信息" in output


def test_agent_browser_deep_search_prefers_sandbox_candidates(monkeypatch):
    monkeypatch.setattr(
        "skills.browser_verify.tools._sandbox_hifleet_candidates",
        lambda query: [{"url": "https://www.hifleet.com/data/index.html", "title": "HiFleet 数据服务", "summary": "来自沙盒候选", "source": "sandbox_python", "query": query}],
    )
    monkeypatch.setattr("skills.browser_verify.tools._candidate_urls", lambda query: [])
    monkeypatch.setattr(
        "skills.browser_verify.tools._browser_capture_page_text",
        lambda url, query="", session="": ("HiFleet 数据服务", "这里是通过 agent-browser 抓取的 HiFleet 数据服务正文。", {"image_count": 0, "screenshot_path": "", "used_snapshot": False}),
    )

    output = agent_browser_deep_search.invoke({"query": "HiFleet 数据服务介绍"})
    payload = json.loads(output)

    assert payload["type"] == "hifleet_browser_evidence"
    assert payload["pages"][0]["title"] == "HiFleet 数据服务"
    assert "通过 agent-browser 抓取" in payload["pages"][0]["excerpt"]


def test_agent_browser_deep_search_keeps_image_evidence(monkeypatch):
    monkeypatch.setattr("skills.browser_verify.tools._sandbox_hifleet_candidates", lambda query: [])
    monkeypatch.setattr(
        "skills.browser_verify.tools._candidate_urls",
        lambda query: [{"url": "https://www.hifleet.com/wp/communities/fleet/haitutubiaoshuoming#post-305", "title": "海图图标说明", "summary": "图标说明页", "source": "bing", "query": query}],
    )
    monkeypatch.setattr(
        "skills.browser_verify.tools._browser_capture_page_text",
        lambda url, query="", session="": ("海图图标说明", "这里是海图图标说明正文。", {"image_count": 12, "screenshot_path": "/tmp/agent-browser-hifleet/map-icons.png", "used_snapshot": False}),
    )

    output = agent_browser_deep_search.invoke({"query": "HiFleet 海图图标说明 图片"})
    payload = json.loads(output)

    assert payload["pages"][0]["image_count"] == 12
    assert payload["pages"][0]["screenshot_path"] == "/tmp/agent-browser-hifleet/map-icons.png"


def test_agent_browser_deep_search_uses_bing_without_target_urls(monkeypatch):
    monkeypatch.setattr("skills.browser_verify.tools._sandbox_hifleet_candidates", lambda query: [])
    monkeypatch.setattr(
        "skills.browser_verify.tools._bing_search_candidates",
        lambda query: [
            {
                "url": "https://www.hifleet.com/wp/communities/fleet/zhuyiliulanqikaishijiyichuanduishaixuanle",
                "title": "注意！浏览器开始记忆船队“筛选”了",
                "summary": "船队筛选记忆功能",
                "source": "bing",
                "query": f"HiFleet {query}",
            }
        ],
    )
    monkeypatch.setattr("skills.browser_verify.tools._preferred_hifleet_candidates", lambda query: [])
    monkeypatch.setattr(
        "skills.browser_verify.tools._browser_capture_page_text",
        lambda url, query="", session="": ("注意！浏览器开始记忆船队“筛选”了", "在显示-船队-筛选上增加了浏览器记忆功能。", {"image_count": 0, "screenshot_path": "", "used_snapshot": False}),
    )

    output = agent_browser_deep_search.invoke({"query": "船队筛选记忆功能"})
    payload = json.loads(output)

    assert payload["type"] == "hifleet_browser_evidence"
    assert payload["pages"][0]["url"].startswith("https://www.hifleet.com/wp/communities/")
    assert payload["pages"][0]["source_query"] == "HiFleet 船队筛选记忆功能"


def test_agent_browser_deep_search_no_target_urls_no_candidates_returns_no_hit(monkeypatch):
    monkeypatch.setattr("skills.browser_verify.tools._candidate_urls", lambda query: [])
    monkeypatch.setattr("skills.browser_verify.tools._sandbox_hifleet_candidates", lambda query: [])

    output = agent_browser_deep_search.invoke({"query": "船队筛选记忆功能"})

    assert "未检索到足够可信的信息" in output


def test_agent_browser_deep_search_filters_local_target_urls(monkeypatch):
    monkeypatch.setattr("skills.browser_verify.tools._candidate_urls", lambda query: [])
    monkeypatch.setattr("skills.browser_verify.tools._sandbox_hifleet_candidates", lambda query: [])

    output = agent_browser_deep_search.invoke({"query": "普通问题", "target_urls": "http://localhost:8000/page"})

    assert "未检索到足够可信的信息" in output


def test_query_variants_add_hifleet_prefix():
    variants = _query_variants("租船AI 使用指南")

    assert variants[0] == "租船AI 使用指南"
    assert any(item.startswith("HiFleet ") for item in variants)


def test_merge_agent_browser_args_adds_no_sandbox():
    merged = _merge_agent_browser_args("--disable-gpu")

    assert "--disable-gpu" in merged
    assert "--no-sandbox" in merged


def test_new_agent_browser_session_is_unique():
    first = _new_agent_browser_session()
    second = _new_agent_browser_session()

    assert first.startswith(f"{AGENT_BROWSER_SESSION_PREFIX}-")
    assert second.startswith(f"{AGENT_BROWSER_SESSION_PREFIX}-")
    assert first != second


def test_run_agent_browser_uses_supplied_session(monkeypatch):
    captured = {}

    class FakeResult:
        returncode = 0
        stdout = "ok"
        stderr = ""

    def fake_run(cmd, capture_output, text, timeout, shell, env):
        captured["cmd"] = cmd
        captured["env"] = env
        return FakeResult()

    monkeypatch.setattr("skills.browser_verify.tools.subprocess.run", fake_run)

    output = _run_agent_browser("get", "title", session="hifleet-cs-test-session")

    assert output == "ok"
    assert captured["cmd"][:3] == ["agent-browser", "--session", "hifleet-cs-test-session"]
    assert captured["env"]["AGENT_BROWSER_SESSION"] == "hifleet-cs-test-session"


def test_browser_capture_page_text_reuses_one_session(monkeypatch):
    calls = []

    def fake_run_agent_browser(*args, timeout=25, session=""):
        calls.append({"args": args, "timeout": timeout, "session": session})
        if args == ("get", "title"):
            return "标题"
        if args == ("get", "text", "body"):
            return "正文"
        if args == ("get", "count", "img"):
            return "0"
        return ""

    monkeypatch.setattr("skills.browser_verify.tools._run_agent_browser", fake_run_agent_browser)

    title, body, meta = _browser_capture_page_text("https://www.hifleet.com/", "HiFleet", session="hifleet-cs-capture")

    assert title == "标题"
    assert body == "正文"
    assert meta["used_snapshot"] is False
    assert {call["session"] for call in calls} == {"hifleet-cs-capture"}
    assert calls[0]["args"] == ("open", "https://www.hifleet.com/")
    assert calls[0].get("timeout") == AGENT_BROWSER_OPEN_TIMEOUT_SEC


def test_browser_capture_page_text_retries_open_once(monkeypatch):
    calls = []

    def fake_run_agent_browser(*args, timeout=25, session=""):
        calls.append({"args": args, "timeout": timeout, "session": session})
        if args == ("open", "https://www.hifleet.com/") and len([call for call in calls if call["args"] == args]) == 1:
            raise RuntimeError("cold start timeout")
        if args == ("get", "title"):
            return "标题"
        if args == ("get", "text", "body"):
            return "正文"
        if args == ("get", "count", "img"):
            return "0"
        return ""

    monkeypatch.setattr("skills.browser_verify.tools._run_agent_browser", fake_run_agent_browser)

    title, body, meta = _browser_capture_page_text("https://www.hifleet.com/", "HiFleet", session="hifleet-cs-retry")

    open_calls = [call for call in calls if call["args"] == ("open", "https://www.hifleet.com/")]
    assert len(open_calls) == 2
    assert title == "标题"
    assert body == "正文"
    assert meta["used_snapshot"] is False


def test_start_system_service_defaults_to_single_worker():
    script = Path(__file__).resolve().parents[1] / "scripts/start_system_service.sh"
    text = script.read_text(encoding="utf-8")

    assert 'export COZE_HTTP_WORKERS="${COZE_HTTP_WORKERS:-1}"' in text


def test_preferred_hifleet_candidates_prioritize_helpcenter_for_howto(monkeypatch):
    monkeypatch.setattr("skills.browser_verify.tools._is_public_http_url", lambda url: True)
    monkeypatch.setattr("skills.knowledge_qa.tools._is_url_accessible", lambda url: True)

    candidates = _preferred_hifleet_candidates("HiFleet 帮助中心怎么登录账号")

    assert candidates
    assert candidates[0]["url"] == "https://www.hifleet.com/helpcenter/?i18n=en"
    assert any(item["url"] == "https://www.hifleet.com/account/index.html?type=account" for item in candidates)


def test_candidate_urls_merge_preferred_hifleet_pages_before_bing_for_hifleet_scope(monkeypatch):
    preferred = [
        {"url": "https://www.hifleet.com/helpcenter/?i18n=en", "title": "HiFleet Help Center EN", "summary": "", "source": "preferred_hifleet", "query": "q"}
    ]
    bing = [
        {"url": "https://www.hifleet.com/data/index.html", "title": "HiFleet 数据服务", "summary": "来自 Bing", "source": "bing", "query": "q"}
    ]
    monkeypatch.setattr("skills.browser_verify.tools._preferred_hifleet_candidates", lambda query: preferred)
    monkeypatch.setattr("skills.browser_verify.tools._bing_search_candidates", lambda query: bing)

    candidates = _candidate_urls("HiFleet 帮助中心")

    assert candidates[0]["source"] == "preferred_hifleet"
    assert candidates[0]["url"] == "https://www.hifleet.com/helpcenter/?i18n=en"
    assert candidates[1]["source"] == "bing"


def test_candidate_urls_prioritize_official_bing_candidates(monkeypatch):
    bing = [
        {"url": "https://example.com/hifleet-guide", "title": "第三方 HiFleet 指南", "summary": "船队筛选记忆", "source": "bing", "query": "q"},
        {"url": "https://www.hifleet.com/wp/communities/fleet/zhuyiliulanqikaishijiyichuanduishaixuanle", "title": "HiFleet 官方社区", "summary": "船队筛选记忆", "source": "bing", "query": "q"},
    ]
    monkeypatch.setattr("skills.browser_verify.tools._bing_search_candidates", lambda query: bing)
    monkeypatch.setattr("skills.browser_verify.tools._preferred_hifleet_candidates", lambda query: [])

    candidates = _candidate_urls("船队筛选记忆功能")

    assert candidates[0]["url"].startswith("https://www.hifleet.com/")
    assert _candidate_priority(candidates[0], "船队筛选记忆功能") > _candidate_priority(candidates[1], "船队筛选记忆功能")


def test_candidate_urls_prioritize_specific_official_pages_over_homepage(monkeypatch):
    preferred = [
        {"url": "https://www.hifleet.com/", "title": "HiFleet 官网首页", "summary": "", "source": "preferred_hifleet", "query": "q"}
    ]
    bing = [
        {"url": "https://www.hifleet.com/wp/communities/fleet/zhuyiliulanqikaishijiyichuanduishaixuanle", "title": "船队筛选记忆功能", "summary": "船队筛选记忆", "source": "bing", "query": "q"}
    ]
    monkeypatch.setattr("skills.browser_verify.tools._preferred_hifleet_candidates", lambda query: preferred)
    monkeypatch.setattr("skills.browser_verify.tools._bing_search_candidates", lambda query: bing)

    candidates = _candidate_urls("HiFleet 船队筛选记忆功能")

    assert candidates[0]["url"].startswith("https://www.hifleet.com/wp/communities/")


def test_bing_search_candidates_records_actual_source_query(monkeypatch):
    html = """
    <li class="b_algo"><h2><a href="https://www.hifleet.com/wp/communities/fleet/example">HiFleet 官方社区</a></h2><p>船队筛选记忆</p></li>
    """

    class FakeResponse:
        text = html

        def raise_for_status(self):
            return None

    requested_urls = []
    monkeypatch.setattr("skills.browser_verify.tools.requests.get", lambda url, timeout, headers: requested_urls.append(url) or FakeResponse())
    monkeypatch.setattr("skills.knowledge_qa.tools._is_url_accessible", lambda url: True)

    candidates = _bing_search_candidates("船队筛选记忆功能")

    assert candidates[0]["url"] == "https://www.hifleet.com/wp/communities/fleet/example"
    assert candidates[0]["query"].startswith('site:hifleet.com "HiFleet"')
    assert requested_urls


def test_preferred_hifleet_pages_cover_requested_urls():
    urls = {item["url"] for item in PREFERRED_HIFLEET_PAGES}

    assert "https://www.hifleet.com/" in urls
    assert "https://www.hifleet.com/data/index.html" in urls
    assert "https://www.hifleet.com/helpcenter/?i18n=en" in urls
    assert "https://www.hifleet.com/account/index.html?type=account" in urls
    assert "https://www.hifleet.com/wp/communities" in urls or "https://www.hifleet.com/wp/community/" in urls


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


def test_knowledge_chain_compat_smart_search_does_not_drive_main_sequence():
    smart_search = FakeTool(
        "smart_search",
        lambda args: "未找到精确的FAQ匹配",
    )
    text = "HiFleet 绿点是什么意思"
    entities = extract_entities(text)
    decision = classify_message(text, entities)
    trace = make_trace(decision, entities)

    output = execute_knowledge_chain(text, decision, {"smart_search": smart_search}, trace)

    assert [c["depth"] for c in smart_search.calls] == ["quick"]
    assert trace.reasoning_trace["retrieval_trace"]["t1_eval_decision"] == "compat_smart_search"
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
    text = "请更新船位 MMSI 414726000 经度 121.4737 纬度 31.2304 航速 5 船首向 120 吃水 9.6 航行状态 在航 更新时间 2026-06-15 10:20:30"
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
            "speed": "5",
            "heading": "120",
            "draft": "9.6",
            "navstatus": "在航",
            "updatetime": "2026-06-15 10:20:30",
        }
    ]
    assert "更新成功" in output
    assert trace.check_result["write_result"] is True


def test_update_chain_executes_with_minimum_position_fields():
    upload = FakeTool("upload_ship_position", lambda args: f"更新成功 MMSI={args['mmsi']} lon={args['lon']} lat={args['lat']}")
    text = "请更新船位 MMSI 414726000 经度 121.4737 纬度 31.2304 更新时间 2026-06-15 10:20:30"
    entities = extract_entities(text)
    decision = classify_message(text, entities)
    trace = make_trace(decision, entities)

    output = execute_update_chain(text, entities, {"upload_ship_position": upload}, trace)

    assert upload.calls == [
        {
            "mmsi": "414726000",
            "lon": "121.4737",
            "lat": "31.2304",
            "updatetime": "2026-06-15 10:20:30",
        }
    ]
    assert "更新成功" in output


def test_update_chain_uses_understanding_position_params_for_dash_coordinates():
    upload = FakeTool("upload_ship_position", lambda args: f"更新成功 MMSI={args['mmsi']} lon={args['lon']} lat={args['lat']}")
    text = "更新船位，船名：友好3，MMSI：413341920，更新时间 2026-07-01 21:27，位置：17-56.73N 115-47.69E，航速8.9节，航向120度"
    entities = extract_entities(text)
    trace = make_trace(classify_message(text, entities), entities)
    understanding = {
        "position_update_params": {
            "mmsi": "413341920",
            "ship_name": "友好3",
            "lat": "17-56.73N",
            "lon": "115-47.69E",
            "updatetime": "2026-07-01 21:27",
            "speed": "8.9",
            "heading": "120",
        }
    }

    output = execute_update_chain(text, entities, {"upload_ship_position": upload}, trace, understanding_result=understanding)

    assert upload.calls == [
        {
            "mmsi": "413341920",
            "ship_name": "友好3",
            "lon": "115-47.69E",
            "lat": "17-56.73N",
            "speed": "8.9",
            "heading": "120",
            "updatetime": "2026-07-01 21:27",
        }
    ]
    assert "更新成功" in output


def test_update_chain_fallback_extracts_position_pair_when_understanding_missing():
    upload = FakeTool("upload_ship_position", lambda args: f"更新成功 MMSI={args['mmsi']} lon={args['lon']} lat={args['lat']}")
    text = "更新船位，MMSI：413341920，更新时间 2026-07-01 21:27，位置：17-56.73N 115-47.69E，航速8.9节，航向120度"
    entities = extract_entities(text)
    trace = make_trace(classify_message(text, entities), entities)

    output = execute_update_chain(text, entities, {"upload_ship_position": upload}, trace)

    assert upload.calls[0]["lon"] == "115-47.69E"
    assert upload.calls[0]["lat"] == "17-56.73N"
    assert "更新成功" in output


def test_update_chain_uses_static_understanding_params():
    update_static = FakeTool("update_ship_static_info", lambda args: f"静态更新成功 MMSI={args['mmsi']}")
    text = "更新船舶静态信息，MMSI：613003594，目的港：RUPRI，ETA：2026-06-24 09:00:00，吃水：8.2"
    entities = extract_entities(text)
    trace = make_trace(classify_message(text, entities), entities)
    understanding = {
        "static_update_params": {
            "mmsi": "613003594",
            "destination": "RUPRI",
            "eta": "2026-06-24 09:00:00",
            "draft": "8.2",
        }
    }

    output = execute_update_chain(text, entities, {"update_ship_static_info": update_static, "upload_ship_position": FakeTool("upload_ship_position", lambda args: "不应调用")}, trace, understanding_result=understanding)

    assert update_static.calls == [
        {"mmsi": "613003594", "destination": "RUPRI", "eta": "2026-06-24 09:00:00", "draft": "8.2"}
    ]
    assert "静态更新成功" in output


def test_update_chain_static_ship_type_minotype_conflict_not_submitted():
    update_static = FakeTool("update_ship_static_info", lambda args: f"静态更新成功 fields={','.join(sorted(args))}")
    text = "更新静态信息 MMSI 414726000 ship_type=散货船 minotype=油船 目的港 NINGBO"
    entities = extract_entities(text)
    trace = make_trace(classify_message(text, entities), entities)
    understanding = {
        "static_update_params": {
            "mmsi": "414726000",
            "ship_type": "散货船",
            "minotype": "油船",
            "destination": "NINGBO",
        }
    }

    output = execute_update_chain(text, entities, {"update_ship_static_info": update_static}, trace, understanding_result=understanding)

    assert update_static.calls == [{"mmsi": "414726000", "destination": "NINGBO"}]
    assert "静态更新成功" in output
    assert trace.reasoning_trace["update_params"]["low_confidence_fields"]


def test_update_chain_ship_name_unique_match_requires_confirmation():
    search = FakeTool("ship_search", lambda args: "YU MING\nMMSI: 414726000 | IMO: 9613886")
    upload = FakeTool("upload_ship_position", lambda args: f"更新成功 MMSI={args['mmsi']}")
    text = "请更新船位 船名 YU MING 经度 121.4737 纬度 31.2304 更新时间 2026-06-15 10:20:30"
    entities = extract_entities(text)
    trace = make_trace(classify_message(text, entities), entities)

    output = execute_update_chain(text, entities, {"ship_search": search, "upload_ship_position": upload}, trace)

    assert search.calls == [{"keyword": "YU MING"}]
    assert upload.calls == []
    assert "请确认是否更新该 MMSI" in output
    assert "414726000" in output


def test_update_chain_ship_name_multiple_matches_asks_for_mmsi():
    search = FakeTool("ship_search", lambda args: "A MMSI: 111111111\nB MMSI: 222222222")
    upload = FakeTool("upload_ship_position", lambda args: "不应调用")
    text = "请更新船位 船名 YU MING 经度 121.4737 纬度 31.2304 更新时间 2026-06-15 10:20:30"
    entities = extract_entities(text)
    trace = make_trace(classify_message(text, entities), entities)

    output = execute_update_chain(text, entities, {"ship_search": search, "upload_ship_position": upload}, trace)

    assert upload.calls == []
    assert "未能唯一确认目标船舶" in output
    assert "111111111" in output
    assert "222222222" in output


def test_update_chain_ship_name_no_match_asks_for_identifier():
    search = FakeTool("ship_search", lambda args: "未找到匹配的船舶。")
    upload = FakeTool("upload_ship_position", lambda args: "不应调用")
    text = "请更新船位 船名 中文简称 经度 121.4737 纬度 31.2304 更新时间 2026-06-15 10:20:30"
    entities = extract_entities(text)
    trace = make_trace(classify_message(text, entities), entities)

    output = execute_update_chain(text, entities, {"ship_search": search, "upload_ship_position": upload}, trace)

    assert upload.calls == []
    assert "请补充 9 位 MMSI、IMO 或更准确的标准船名" in output


def test_update_chain_imo_unique_match_updates_with_mmsi():
    search = FakeTool("ship_search", lambda args: "IMO: 9613886\nMMSI: 414726000")
    upload = FakeTool("upload_ship_position", lambda args: f"更新成功 MMSI={args['mmsi']}")
    text = "请更新船位 IMO 9613886 经度 121.4737 纬度 31.2304 更新时间 2026-06-15 10:20:30"
    entities = extract_entities(text)
    trace = make_trace(classify_message(text, entities), entities)

    output = execute_update_chain(text, entities, {"ship_search": search, "upload_ship_position": upload}, trace)

    assert search.calls == [{"keyword": "9613886"}]
    assert upload.calls[0]["mmsi"] == "414726000"
    assert "更新成功" in output


def test_update_chain_does_not_reuse_history_for_bare_update():
    messages = [
        HumanMessage(content="查询 MMSI 352002867 船位"),
        AIMessage(content="MMSI: 352002867\n船位查询完成"),
    ]
    context = build_conversation_context(messages)
    text = "更新一下"
    entities = resolve_entities_with_context(
        extract_entities(text),
        context,
        allow_ship_context=should_use_ship_context("ship_update", text),
    )
    upload = FakeTool("upload_ship_position", lambda args: "不应调用")
    trace = make_trace(classify_message(text, entities, context), entities)

    output = execute_update_chain(text, entities, {"upload_ship_position": upload}, trace)

    assert entities.mmsi == ""
    assert upload.calls == []
    assert "需要明确船舶身份标识" in output


def test_update_chain_requires_confirmation_for_context_ship_reference():
    messages = [
        HumanMessage(content="查询 MMSI 352002867 船位"),
        AIMessage(content="MMSI: 352002867\n船位查询完成"),
    ]
    context = build_conversation_context(messages)
    text = "这艘船更新一下，经度 121.4737 纬度 31.2304 航速 5 船首向 120 吃水 9.6 航行状态 在航 更新时间 2026-06-15 10:20:30"
    entities = resolve_entities_with_context(
        extract_entities(text),
        context,
        allow_ship_context=should_use_ship_context("ship_update", text),
    )
    upload = FakeTool("upload_ship_position", lambda args: "不应调用")
    trace = make_trace(classify_message(text, entities, context), entities)

    output = execute_update_chain(text, entities, {"upload_ship_position": upload}, trace)

    assert entities.mmsi == "352002867"
    assert upload.calls == []
    assert "请确认本次要更新的目标船舶标识" in output
    assert trace.fallback_reason == "update_requires_explicit_ship_identifier"


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


def test_business_question_about_track_retention_routes_to_knowledge_not_ship():
    text = "基础版的历史轨迹可查多久前的"
    entities = extract_entities(text)
    decision = classify_message(text, entities)

    assert decision.route == "knowledge"
    assert decision.task_type == "platform_knowledge"


def test_area_history_howto_routes_to_knowledge_not_stats():
    text = "如何查询区域过往历史数据？"
    entities = extract_entities(text)
    decision = classify_message(text, entities)

    assert decision.route == "knowledge"


def test_context_clear_routes_to_conversation():
    text = "清理上下文"
    entities = extract_entities(text)
    decision = classify_message(text, entities)

    assert decision.route == "conversation"


def test_hifleet_business_answers_prefer_direct_domain_answer():
    trace = make_trace(classify_message("专业版账号有几天的气象预报？", extract_entities("专业版账号有几天的气象预报？")), extract_entities("专业版账号有几天的气象预报？"))

    output = execute_knowledge_chain(
        "专业版账号有几天的气象预报？",
        classify_message("专业版账号有几天的气象预报？", extract_entities("专业版账号有几天的气象预报？")),
        {},
        trace,
    )

    assert "15 天" in output or "15天" in output
    assert "彩云天气" not in output
    assert trace.check_result["direct_business_answer"] is True


def test_authoritative_data_query_does_not_rewrite_into_hifleet_product_query():
    assert _rewrite_hifleet_knowledge_query("今日长江水位") == "今日长江水位"
    assert _generate_knowledge_expansion_query("今日长江水位", classify_message("今日长江水位", extract_entities("今日长江水位"))) == "今日长江水位 长江海事局 交通运输部"


def test_authoritative_data_short_circuit_skips_browser_and_keeps_public_query():
    local_kb = FakeTool(
        "local_kb_search",
        lambda args: json.dumps({"tool": "local_kb_search", "can_answer": False, "should_continue": True, "items": []}, ensure_ascii=False),
    )
    web_search = FakeTool(
        "web_search",
        lambda args: json.dumps(
            {
                "tool": "web_search",
                "query": args["query"],
                "can_answer": True,
                "should_continue": False,
                "continue_with": "none",
                "summary": "命中权威公共页面且包含明确事实",
                "items": [
                    {
                        "title": "长江水位日报",
                        "url": "https://cj.msa.gov.cn/water/2026-06-18.html",
                        "summary": "2026-06-18 长江水位 12.3 米",
                        "snippet": "2026-06-18 长江水位 12.3 米",
                        "authority_level": 1,
                        "is_authoritative": True,
                    }
                ],
                "best_urls": ["https://cj.msa.gov.cn/water/2026-06-18.html"],
                "trace": {
                    "request_profile": {"Filter": {"NeedContent": False, "NeedUrl": True, "AuthInfoLevel": 0}},
                    "result_profile": {"result_count": 1, "authoritative_count": 1},
                    "used_ark_fallback": False,
                },
            },
            ensure_ascii=False,
        ),
    )
    browser = FakeTool("web_search_agent_browser", lambda args: "Should not be called")
    text = "今日长江水位"
    entities = extract_entities(text)
    decision = classify_message(text, entities)
    trace = make_trace(decision, entities, session_id="s1")

    output = execute_knowledge_chain(text, decision, {"local_kb_search": local_kb, "web_search": web_search, "web_search_agent_browser": browser}, trace)

    assert local_kb.calls == [{"query": "今日长江水位"}]
    assert web_search.calls[0]["query"] == "今日长江水位"
    assert browser.calls == []
    assert trace.reasoning_trace["retrieval_trace"]["t1_eval_decision"] == "short_circuit"
    assert trace.reasoning_trace["retrieval_trace"]["t2_triggered"] is False
    assert trace.reasoning_trace["retrieval_trace"]["t1_payload_meta"]["Filter"].get("Sites") in {None, ""}
    assert "长江水位" in output


def test_specific_satellite_ais_query_does_not_answer_from_weak_local_kb():
    local_kb = FakeTool(
        "local_kb_search",
        lambda args: json.dumps(
            {
                "tool": "local_kb_search",
                "can_answer": False,
                "should_continue": True,
                "items": [
                    {
                        "title": "航运数据与API服务",
                        "content": "HiFleet可提供船位实时AIS、历史AIS、历史航次、靠离港、船舶档案、港口、海图、气象等数据，并支持API、SDK、分析报告和行业解决方案。",
                        "source_type": "wiki",
                        "score": 0.58,
                    },
                    {
                        "title": "账号注册与登录",
                        "content": "点击页面右上角‘登录’后可免费注册，支持用户名注册、手机验证码注册和微信扫码注册。",
                        "source_type": "faq",
                        "score": 0.52,
                    },
                ],
            },
            ensure_ascii=False,
        ),
    )
    web_search = FakeTool(
        "web_search",
        lambda args: json.dumps(
            {
                "tool": "web_search",
                "query": args["query"],
                "can_answer": False,
                "should_continue": True,
                "continue_with": "none",
                "summary": "未命中足够具体的资料",
                "items": [],
                "best_urls": [],
                "trace": {"request_profile": {"Filter": {"Sites": "hifleet.com"}}, "result_profile": {"result_count": 0}, "used_ark_fallback": False},
            },
            ensure_ascii=False,
        ),
    )
    text = "我想详细了解一下，Hifleet卫星AIS数据情况，有多少颗在轨AIS卫星？每日接收数据是多少？"
    entities = extract_entities(text)
    decision = classify_message(text, entities)
    trace = make_trace(decision, entities)

    output = execute_knowledge_chain(text, decision, {"local_kb_search": local_kb, "web_search": web_search}, trace)

    assert "免费注册" not in output
    assert "下载APP" not in output
    assert "HiFleet可提供船位实时AIS、历史AIS" not in output
    assert trace.check_result["evidence_count"] == 0
    assert trace.reasoning_trace["retrieval_trace"]["t1_source_count"] == 0


def test_execute_knowledge_chain_prefers_understanding_primary_query(monkeypatch):
    smart_search = FakeTool("smart_search", lambda args: "这是检索结果")
    agent_browser = FakeTool("agent_browser_deep_search", lambda args: "Should not be called")
    text = "Hifleet筛选船队有记忆功能吗"
    entities = extract_entities(text)
    decision = classify_message(text, entities)
    trace = make_trace(decision, entities, session_id="s2")
    trace.reasoning_trace = {
        "understanding_result": {
            "query_type": "hifleet_product",
            "rewritten_user_need": "用户想确认 HiFleet 平台中筛选船队后，筛选条件是否会被记住并在下次继续生效",
            "search_keywords": ["hifleet", "筛选船队", "记忆功能"],
            "search_query_candidates": ["hifleet 筛选船队 记忆功能", "HiFleet 船队筛选 条件记忆"],
            "should_prefer_local_kb": True,
            "should_limit_to_hifleet_sites": True,
        }
    }

    monkeypatch.setattr(
        "agents.customer_support_router._read_structured_search_trace",
        lambda query, depth: {
            "t0_kb_hit": False,
            "t1_query": query,
            "t1_payload_meta": {
                "Query": query,
                "SearchType": "web",
                "Count": 5,
                "NeedSummary": True,
                "ContentFormats": "text",
                "Filter": {"NeedContent": False, "NeedUrl": True, "AuthInfoLevel": 0, "Sites": "hifleet.com"},
            },
            "t1_source_count": 1,
            "t1_official_source_count": 0,
            "t1_used_ark_fallback": False,
            "items": [],
            "summary": "",
        },
    )
    monkeypatch.setattr("agents.customer_support_router._evaluate_t1_results", lambda *args, **kwargs: {"decision": "short_circuit", "reason": "enough", "best_urls": [], "fallback_reason": "t1_short_circuit_default"})

    execute_knowledge_chain(
        text,
        decision,
        {"smart_search": smart_search, "agent_browser_deep_search": agent_browser},
        trace,
    )

    assert smart_search.calls[0]["query"] == "hifleet 筛选船队 记忆功能"
    assert trace.reasoning_trace["retrieval_trace"]["understanding_primary_query"] == "hifleet 筛选船队 记忆功能"
    assert trace.reasoning_trace["understanding_summary"]["query_type"] == "hifleet_product"


def test_history_track_permission_answer_is_concise_and_domain_correct():
    question = "基础版的历史轨迹可查多久前的"
    entities = extract_entities(question)
    decision = classify_message(question, entities)
    trace = make_trace(decision, entities)

    output = execute_knowledge_chain(question, decision, {}, trace)

    assert "基础版：可查看近 12 个月" in output
    assert "专业版：可查看近 36 个月" in output
    assert "MMSI" not in output


def test_context_clear_answer_does_not_false_claim_total_deletion():
    question = "清理上下文"
    context = build_conversation_context([HumanMessage(content="查询育明船位"), HumanMessage(content=question)])

    output = answer_conversation_memory(question, context)

    assert "重新理解" in output
    assert "彻底清空历史记忆" in output
    assert "未留存任何" not in output


def test_sanitize_customer_output_strips_query_footer_and_app_promo():
    raw = (
        "我先根据目前检索到的官方资料给您结论：\n"
        "[Query1:欧盟碳配额价格及每吨重油碳配额水平]\n"
        "### 相关信息说明\n"
        "如需更多帮助，请继续补充船名、MMSI、IMO、呼号或直接提问。\n"
        "<a href=\"https://www.hifleet.com/download/qr.html\">下载APP</a>,手机查船更方便"
    )

    cleaned = sanitize_customer_output(raw)

    assert "[Query1:" not in cleaned
    assert "下载APP" not in cleaned
    assert "手机查船更方便" not in cleaned


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


def test_conversation_context_reads_ship_entity_from_compressed_summary():
    messages = [
        SystemMessage(content="请用中文回复。"),
        SystemMessage(
            content=(
                "历史上下文摘要：\n"
                "用户此前咨询过：查询 MMSI 123456789 船位\n"
                "最近确认的船舶实体：MMSI 123456789\n"
                "除非用户明确引用历史，否则优先按当前最新问题独立处理。"
            )
        ),
        HumanMessage(content="这艘船历史轨迹呢"),
    ]

    context = build_conversation_context(messages)
    entities = resolve_entities_with_context(extract_entities(messages[-1].content), context)

    assert context.last_ship_mmsi == "123456789"
    assert entities.mmsi == "123456789"


def test_conversation_context_compresses_and_filters_irrelevant_history():
    messages = [
        HumanMessage(content="帮我看看租船AI入口在哪里，顺便介绍一下数据服务页面"),
        HumanMessage(content="我还想知道帮助中心英文版入口"),
        HumanMessage(content="另外上周问过船位问题先不用管"),
        HumanMessage(content="账号登录入口在哪"),
    ]
    context = build_conversation_context(messages)

    assert context.previous_user_text in {
        "帮我看看租船AI入口在哪里，顺便介绍一下数据服务页面",
        "我还想知道帮助中心英文版入口",
    }
    assert context.relevant_recent_user_questions
    assert "另外上周问过船位问题先不用管" not in context.relevant_recent_user_questions
    assert all("船位问题" not in item for item in context.relevant_recent_user_questions)
    assert "最近相关问题" in context.context_summary


def test_irrelevant_old_context_does_not_force_platform_troubleshooting_route():
    messages = [
        HumanMessage(content="hifleet平台上传不了航线怎么办"),
        HumanMessage(content="今天上海天气怎么样"),
    ]
    context = build_conversation_context(messages)
    text = "今天上海天气怎么样"
    entities = extract_entities(text)
    decision = classify_message(text, entities, context)

    assert decision.route == "knowledge"
    assert decision.task_type == "platform_knowledge"


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


def test_customer_support_plan_uses_harness_for_ship_update():
    text = "请更新船位 MMSI 414726000 经度 121.4737 纬度 31.2304"
    entities = extract_entities(text)
    decision = classify_message(text, entities)
    context = build_conversation_context([HumanMessage(content=text)])

    plan = build_customer_support_plan(text, decision, entities, context, [], {})

    assert plan["problem_frame"]["question_type"] == "ship_update"
    assert plan["decision_rationale"]["response_mode"] == "use_harness"
    assert plan["decision_rationale"]["need_harness"] is True


def test_customer_support_plan_creates_multi_query_troubleshooting_search_plan():
    text = "hifleet平台上传不了航线怎么办"
    entities = extract_entities(text)
    decision = classify_message(text, entities)
    context = build_conversation_context([HumanMessage(content=text)])

    plan = build_customer_support_plan(text, decision, entities, context, [], {})

    assert plan["problem_frame"]["question_type"] == "troubleshooting"
    assert len(plan["search_plan"]) >= 2
    assert any("上传航线" in item["query"] for item in plan["search_plan"])


def test_execute_planned_knowledge_chain_records_evidence_review():
    smart_search = FakeTool(
        "smart_search",
        lambda args: "【优先匹配 - FAQ/标准回复】\n标准答案\nhttps://www.hifleet.com/helpcenter/?i18n=zh",
    )
    text = "HiFleet 绿点是什么意思"
    entities = extract_entities(text)
    decision = classify_message(text, entities)
    trace = make_trace(decision, entities)
    plan = build_customer_support_plan(text, decision, entities, build_conversation_context([HumanMessage(content=text)]), [], {})

    output, evidence_items, evidence_summary = execute_planned_knowledge_chain(text, decision, plan["search_plan"], {"smart_search": smart_search}, trace)

    assert "官方资料" in output or "标准答案" in output
    assert evidence_items
    assert evidence_summary["support_count"] >= 1
    assert trace.check_result["evidence_count"] >= 1


def test_review_evidence_items_prefers_official_sources():
    summary = review_evidence_items(
        [
            {"source_type": "official_site", "supports": ["H1"]},
            {"source_type": "public_web", "supports": ["H1"]},
        ]
    )

    assert summary["official_support_count"] == 1
    assert summary["confidence"] == "high"


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
    smart_search = FakeTool(
        "smart_search",
        lambda args: (
            "海图图标说明：红色圆形中心黑点为安全水域浮标\n"
            "https://www.hifleet.com/wp/communities/fleet/haitutubiaoshuoming"
        ),
    )
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
        {"confidence": "high", "visible_features": "红色圆形、中心黑点", "summary": "红色圆形、中心黑点"},
        decision,
        {"smart_search": smart_search, "inspect_media_attachment": inspect},
        trace,
    )

    assert "安全水域浮标" in output
    assert "https://www.hifleet.com/wp/communities/fleet/haitutubiaoshuoming" in output
    assert "红色圆形、中心黑点" in smart_search.calls[0]["query"]
    assert smart_search.calls[0]["depth"] == "deep"
    assert trace.tool_call_sequence == ["inspect_media_attachment", "smart_search"]


def test_chart_symbol_answer_without_verified_link_uses_unverified_template():
    perception = {"visible_features": "红色圆形、中心黑点", "confidence": "high"}

    output = format_verified_chart_symbol_answer(perception, "未检索到足够可信的信息")

    assert "初步识别为：红色圆形、中心黑点" in output
    assert "未检索到准确官方内容" in output
    assert "安全水域浮标" not in output


def test_chart_symbol_answer_requires_verified_link():
    perception = {"visible_features": "红色圆形、中心黑点", "confidence": "high"}

    output = format_verified_chart_symbol_answer(
        perception,
        "海图图标说明：红色圆形中心黑点对应安全水域浮标。"
        "https://www.hifleet.com/wp/communities/fleet/haitutubiaoshuoming",
    )

    assert "安全水域浮标" in output
    assert "验证链接：https://www.hifleet.com/wp/communities/fleet/haitutubiaoshuoming" in output


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


def test_browser_verify_chain_prefers_browser_verified_evidence():
    verify = FakeTool("verify_public_page", lambda args: '{"ok":true,"title":"HiFleet 官方社区"}')
    search = FakeTool("smart_search", lambda args: "【Hifleet官方站内搜索】\n来源：官方社区")
    browser = FakeTool(
        "agent_browser_deep_search",
        lambda args: json.dumps(
            {
                "type": "hifleet_browser_evidence",
                "query": args["query"],
                "pages": [
                    {
                        "title": "HiFleet 官方社区",
                        "url": "https://www.hifleet.com/wp/communities",
                        "excerpt": "这里是社区正文摘要。",
                        "official": True,
                        "source_query": "HiFleet 官方社区 核验",
                        "image_count": 5,
                        "screenshot_path": "/tmp/agent-browser-hifleet/community.png",
                    }
                ],
            },
            ensure_ascii=False,
        ),
    )
    text = "核验 https://www.hifleet.com/wp/communities 的官方信息"
    entities = extract_entities(text)
    decision = classify_message(text, entities)
    decision.route = "browser_verify"
    decision.task_type = "browser_verify"
    decision.tool_bundle = ["verify_public_page", "smart_search", "agent_browser_deep_search"]
    trace = make_trace(decision, entities)

    output = execute_browser_verify_chain(
        text,
        entities,
        decision,
        {"verify_public_page": verify, "smart_search": search, "agent_browser_deep_search": browser},
        trace,
    )

    assert "HiFleet 官方社区" in output
    assert "页面包含较多图片" in output
    assert trace.check_result["browser_verified"] is True
    assert trace.check_result["browser_image_evidence"] is True
    assert trace.reasoning_trace["tool_summary"]["official_source_count"] >= 1


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


def test_free_user_latest_position_routes_to_knowledge_not_random_ship_query():
    text = "我是免费用户，为什么在网站上看不到最新的船位？"
    entities = extract_entities(text)
    decision = classify_message(text, entities)
    trace = make_trace(decision, entities)

    output = execute_knowledge_chain(text, decision, {}, trace)

    assert decision.route == "knowledge"
    assert decision.task_type == "platform_knowledge"
    assert "免费账号" in output
    assert "MMSI:" not in output
    assert "随机" not in output


def test_generic_device_complaint_asks_light_hifleet_context_question():
    text = "你们这网速太卡了，我电脑都死机了"
    entities = extract_entities(text)
    decision = classify_message(text, entities)
    trace = make_trace(decision, entities)

    output = execute_knowledge_chain(
        text,
        decision,
        {"smart_search": FakeTool("smart_search", lambda args: "未检索到足够可信的信息")},
        trace,
    )

    assert "卡顿发生在哪个 HiFleet 页面" in output
    assert "清理缓存" not in output


def test_official_article_verification_uses_web_search_agent_browser_for_specific_page():
    calls = []
    local_kb = FakeTool("local_kb_search", lambda args: calls.append("local_kb_search") or json.dumps({"tool": "local_kb_search", "can_answer": False, "should_continue": True, "items": []}, ensure_ascii=False))
    web_search = FakeTool(
        "web_search",
        lambda args: calls.append("web_search") or json.dumps(
            {
                "tool": "web_search",
                "query": args["query"],
                "can_answer": False,
                "should_continue": True,
                "continue_with": "agent_browser",
                "items": [{"title": "注意！浏览器开始记忆船队“筛选”了", "url": "https://www.hifleet.com/wp/communities/example-filter-memory/", "snippet": "摘要不足", "is_hifleet_official": True}],
                "best_urls": ["https://www.hifleet.com/wp/communities/example-filter-memory/"],
                "trace": {"request_profile": {}, "result_profile": {"result_count": 1, "official_count": 1}, "used_ark_fallback": False},
            },
            ensure_ascii=False,
        ),
    )
    browser = FakeTool(
        "web_search_agent_browser",
        lambda args: calls.append("web_search_agent_browser") or json.dumps(
            {
                "tool": "web_search_agent_browser",
                "query": args["query"],
                "can_answer": True,
                "pages": [
                    {
                        "title": "注意！浏览器开始记忆船队“筛选”了",
                        "url": "https://www.hifleet.com/wp/communities/example-filter-memory/",
                        "excerpt": "HiFleet 网页版新增浏览器记忆船队筛选功能，用户再次打开页面时可保留上一次筛选条件。",
                        "official": True,
                    }
                ],
            },
            ensure_ascii=False,
        ),
    )
    text = "验证 注意！浏览器开始记忆船队“筛选”了 的详细内容"
    entities = extract_entities(text)
    decision = classify_message(text, entities)
    trace = make_trace(decision, entities)

    output, evidence_items, evidence_summary = execute_planned_knowledge_chain(
        text,
        decision,
        [{"query": text, "depth": "normal", "hypothesis_id": "H1", "purpose": "核验社区文章"}],
        {"local_kb_search": local_kb, "web_search": web_search, "web_search_agent_browser": browser},
        trace,
    )

    assert calls == ["local_kb_search", "web_search", "web_search_agent_browser"]
    assert "浏览器记忆船队筛选功能" in output
    assert "example-filter-memory" in output
    assert evidence_summary["official_support_count"] >= 1
    assert trace.reasoning_trace["tool_summary"]["official_source_count"] >= 1

def test_sanitize_customer_output_strips_more_search_noise_and_html_placeholders():
    raw = (
        "综合摘要：\n"
        "查询1（验证文章）：这是正文。\n"
        "[HTMLLINK_0],手机查船更方便\n"
        "如需更多帮助，请继续补充船名、MMSI、IMO、呼号或直接提问。\n"
        "下载APP,手机查船更方便,服务电话:400-963-6899,微信:hifleetkhzs"
    )

    cleaned = sanitize_customer_output(raw)

    assert "综合摘要" not in cleaned
    assert "查询1" not in cleaned
    assert "HTMLLINK" not in cleaned
    assert "下载APP" not in cleaned
    assert "继续补充船名" not in cleaned
