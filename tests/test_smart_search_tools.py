import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from skills.knowledge_qa.browser_bridge import build_browser_bridge_payload
from skills.knowledge_qa import tools
from skills.knowledge_qa.web_search_runtime import rewrite_web_search_query
from skills.browser_verify import tools as browser_tools


def test_build_volc_web_search_payload_for_web_summary():
    payload = tools._build_volc_web_search_payload(
        "欧盟碳配额价格",
        search_type="web_summary",
        count=8,
        sites="hifleet.com|help.hifleet.com",
        need_summary=True,
        need_content=False,
        need_url=True,
        query_rewrite=True,
        auth_info_level=1,
        time_range="OneYear",
        content_format="markdown",
    )

    assert payload["Query"] == "欧盟碳配额价格"
    assert payload["SearchType"] == "web_summary"
    assert payload["Count"] == 8
    assert payload["NeedSummary"] is True
    assert payload["QueryControl"]["QueryRewrite"] is True
    assert payload["Filter"]["Sites"] == "hifleet.com|help.hifleet.com"
    assert payload["Filter"]["NeedUrl"] is True
    assert payload["Filter"]["NeedContent"] is False
    assert payload["Filter"]["AuthInfoLevel"] == 1
    assert payload["TimeRange"] == "OneYear"
    assert payload["ContentFormats"] == "markdown"


def test_normalize_web_search_result_prefers_structured_fields():
    raw = {
        "Result": {
            "WebResults": [
                {
                    "Title": "HiFleet 帮助中心",
                    "SiteName": "HiFleet",
                    "Url": "https://www.hifleet.com/helpcenter/?i18n=zh",
                    "Snippet": "短摘要",
                    "Summary": "长摘要",
                    "Content": "正文",
                    "PublishTime": "2026-06-16T10:00:00+08:00",
                    "AuthInfoLevel": 1,
                    "AuthInfoDes": "非常权威",
                    "RankScore": 0.98,
                    "ContentFormats": "markdown",
                }
            ],
            "Choices": [
                {
                    "Message": {
                        "Content": "这是总结版输出"
                    }
                }
            ],
            "SearchContext": {
                "OriginQuery": "HiFleet 帮助中心",
                "SearchType": "web_summary",
            },
            "TimeCost": 321,
            "LogId": "log-1",
        }
    }

    result = tools._normalize_web_search_result(raw)

    assert result["summary"] == "这是总结版输出"
    assert result["search_context"]["OriginQuery"] == "HiFleet 帮助中心"
    assert result["items"][0]["summary"] == "长摘要"
    assert result["items"][0]["authority_level"] == 1
    assert result["items"][0]["publish_time"] == "2026-06-16T10:00:00+08:00"


def test_web_search_falls_back_to_ark_when_structured_search_fails(monkeypatch):
    monkeypatch.setattr(
        tools,
        "_volc_web_search",
        lambda query, **kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    monkeypatch.setattr(
        tools,
        "_ark_web_search",
        lambda query, site_hint="", count=5: {
            "summary": "fallback",
            "items": [{"title": "ark", "url": "https://example.com", "snippet": "fallback"}],
        },
    )

    result = tools._web_search("HiFleet 轨迹", count=3, sites="hifleet.com")

    assert result["summary"] == "fallback"
    assert result["items"][0]["title"] == "ark"


def test_rewrite_web_search_query_preserves_technical_qualifiers():
    rewritten = rewrite_web_search_query("HiFleet CCTV GB28181 接入价格异常怎么处理")

    assert "hifleet" in rewritten.lower()
    for phrase in ("CCTV", "GB28181", "接入", "价格", "异常"):
        assert phrase in rewritten


def test_verify_public_page_blocks_private_dns_result(monkeypatch):
    monkeypatch.setattr(browser_tools, "_resolve_public_host", lambda host: False)

    payload = json.loads(browser_tools.verify_public_page.invoke({"url": "https://public.example/path"}))

    assert payload == {"ok": False, "reason": "invalid_url"}


def test_verify_public_page_blocks_redirect_to_private_target(monkeypatch):
    class FakeResponse:
        status_code = 302
        headers = {"Location": "http://127.0.0.1/private"}
        text = ""

        @property
        def is_redirect(self):
            return True

    monkeypatch.setattr(browser_tools, "_resolve_public_host", lambda host: host == "public.example")
    monkeypatch.setattr(browser_tools.requests, "get", lambda *args, **kwargs: FakeResponse())

    payload = json.loads(browser_tools.verify_public_page.invoke({"url": "https://public.example/start"}))

    assert payload == {"ok": False, "reason": "internal_url_blocked"}


def test_browser_bridge_preserves_unavailable_status():
    payload = build_browser_bridge_payload(
        "HiFleet 轨迹",
        "",
        "HiFleet",
        {"type": "hifleet_browser_evidence", "status": "browser_unavailable", "pages": []},
    )

    assert payload["status"] == "browser_unavailable"
    assert payload["can_answer"] is False


def test_browser_bridge_rejects_generic_official_pages_as_evidence():
    payload = build_browser_bridge_payload(
        "HiFleet 筛选船队记忆功能",
        "",
        "HiFleet",
        {
            "type": "hifleet_browser_evidence",
            "pages": [
                {
                    "title": "HiFleet 官方社区",
                    "url": "https://www.hifleet.com/wp/communities",
                    "excerpt": "官方社区入口",
                    "official": True,
                }
            ],
        },
    )

    assert payload["status"] == "generic_or_irrelevant_page"
    assert payload["can_answer"] is False


def test_volc_web_search_accepts_existing_ark_websearch_env_name(monkeypatch):
    calls = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "ResponseMetadata": {},
                "Result": {
                    "WebResults": [],
                    "Choices": [],
                    "SearchContext": {"OriginQuery": "q", "SearchType": "web"},
                    "TimeCost": 10,
                    "LogId": "log-1",
                },
            }

    def fake_post(url, headers=None, json=None, timeout=None):
        calls["url"] = url
        calls["headers"] = headers or {}
        calls["json"] = json or {}
        calls["timeout"] = timeout
        return FakeResponse()

    monkeypatch.delenv("VOLC_WEB_SEARCH_API_KEY", raising=False)
    monkeypatch.delenv("WEB_SEARCH_API_KEY", raising=False)
    monkeypatch.delenv("TORCHLIGHT_API_KEY", raising=False)
    monkeypatch.delenv("ARK_WEBSEARCH_API_KEY", raising=False)
    monkeypatch.setenv("ark_websearch_api_key", "masked-key")
    monkeypatch.setattr(tools.requests, "post", fake_post)

    result = tools._volc_web_search("HiFleet 帮助中心", search_type="web", count=3)

    assert result["log_id"] == "log-1"
    assert calls["url"] == tools.VOLC_WEB_SEARCH_URL
    assert calls["headers"]["Authorization"] == "Bearer masked-key"
    assert calls["json"]["Query"] == "HiFleet 帮助中心"


def test_format_web_result_does_not_emit_query_or_answer_guidance():
    output = tools._format_web_result(
        {
            "summary": "这是综合摘要",
            "items": [
                {
                    "title": "标题A",
                    "authority_label": "🟢 权威",
                    "snippet": "摘要A",
                    "site_name": "站点A",
                    "publish_time": "2026-06-16T10:00:00+08:00",
                    "url": "https://example.com/a",
                }
            ],
        }
    )

    assert "综合摘要" in output
    assert "[Query1:" not in output
    assert "回答指导" not in output
    assert "AI摘要" not in output


def test_local_kb_search_returns_structured_faq_hit():
    payload = json.loads(tools.local_kb_search.invoke({"query": "DTU是什么？", "top_k": 3}))

    assert payload["tool"] == "local_kb_search"
    assert payload["status"] == "ok"
    assert payload["items"]
    assert payload["items"][0]["source_type"] in {"faq", "wiki", "product_doc"}
    assert payload["should_continue"] in {True, False}


def test_web_search_returns_structured_analysis(monkeypatch):
    monkeypatch.setattr(
        tools,
        "_web_search",
        lambda query, **kwargs: {
            "query": query,
            "summary": "命中具体官方页面且包含明确事实",
            "items": [
                {
                    "title": "注意！浏览器开始记忆船队“筛选”了",
                    "url": "https://www.hifleet.com/wp/communities/fleet/zhuyiliulanqikaishijiyichuanduishaixuanle",
                    "site_name": "HiFleet",
                    "summary": "在显示-船队-筛选上增加了浏览器记忆功能。",
                    "snippet": "增加了浏览器记忆功能。",
                    "publish_time": "2025-04-11T10:00:00+08:00",
                    "authority_level": 1,
                    "authority_desc": "非常权威",
                    "rank_score": 0.99,
                }
            ],
            "payload_meta": {
                "Query": query,
                "SearchType": "web",
                "Count": 5,
                "NeedSummary": True,
                "ContentFormats": "text",
                "Filter": {"NeedContent": False, "NeedUrl": True, "AuthInfoLevel": 0, "Sites": tools.HIFLEET_SITES, "BlockHosts": ""},
            },
            "used_ark_fallback": False,
        },
    )

    payload = json.loads(tools.web_search.invoke({"query": "Hifleet筛选船队有记忆功能吗"}))

    assert payload["tool"] == "web_search"
    assert payload["query"] == "hifleet 筛选船队 记忆功能"
    assert payload["can_answer"] is True
    assert payload["trace"]["request_profile"]["Filter"]["Sites"] == tools.HIFLEET_SITES
    assert payload["items"][0]["is_hifleet_official"] is True
    assert payload["items"][0]["has_specific_fact"] is True


def test_web_search_how_to_rejects_directory_or_video_only(monkeypatch):
    monkeypatch.setattr(
        tools,
        "_web_search",
        lambda query, **kwargs: {
            "query": query,
            "summary": "命中帮助中心和演示视频标题",
            "items": [
                {
                    "title": "HiFleet 帮助中心",
                    "url": "https://www.hifleet.com/helpcenter/?i18n=zh",
                    "site_name": "HiFleet",
                    "summary": "官方平台使用与问题排查文档入口。",
                    "snippet": "官方平台使用与问题排查文档入口。",
                    "authority_level": 1,
                },
                {
                    "title": "在HiFleet平台上添加区域(电子围栏)报警 - HIFLEET演示视频",
                    "url": "https://www.hifleet.com/wp/communities/fleet/video-area",
                    "site_name": "HiFleet",
                    "summary": "演示视频标题页，介绍可添加区域报警。",
                    "snippet": "演示视频标题页，介绍可添加区域报警。",
                    "authority_level": 1,
                },
            ],
            "payload_meta": {"Filter": {"Sites": tools.HIFLEET_SITES}},
            "used_ark_fallback": False,
        },
    )

    payload = json.loads(tools.web_search.invoke({"query": "怎么绘制区域标注"}))

    assert payload["can_answer"] is False
    assert payload["trace"]["question_class"] == "how_to_operate"
    assert "tutorial_generic_pages_only" in payload["trace"]["risk_flags"]


def test_web_search_how_to_accepts_official_step_evidence(monkeypatch):
    monkeypatch.setattr(
        tools,
        "_web_search",
        lambda query, **kwargs: {
            "query": query,
            "summary": "命中官方正文步骤",
            "items": [
                {
                    "title": "HiFleet 区域标注操作说明",
                    "url": "https://www.hifleet.com/wp/communities/fleet/area-marker-steps",
                    "site_name": "HiFleet",
                    "summary": "在主海图页面右上角点击标注，选择区域标注，绘制多边形后点击保存完成。",
                    "snippet": "在主海图页面右上角点击标注，选择区域标注，绘制多边形后点击保存完成。",
                    "authority_level": 1,
                }
            ],
            "payload_meta": {"Filter": {"Sites": tools.HIFLEET_SITES}},
            "used_ark_fallback": False,
        },
    )

    payload = json.loads(tools.web_search.invoke({"query": "怎么绘制区域标注"}))

    assert payload["can_answer"] is True
    assert payload["trace"]["question_class"] == "how_to_operate"
    assert payload["items"][0]["has_operation_step_signal"] is True


def test_web_search_authoritative_public_query_does_not_add_sites(monkeypatch):
    monkeypatch.setattr(
        tools,
        "_web_search",
        lambda query, **kwargs: {
            "query": query,
            "summary": "命中权威公共页面且包含明确事实",
            "items": [
                {
                    "title": "2026年6月18日8时长江水位",
                    "url": "https://www.mot.gov.cn/fuwu/yujingtishi/cjshuiweichaowei/202606/t20260618_4207751.html",
                    "site_name": "中华人民共和国交通运输部",
                    "summary": "2026-06-18 长江水位 12.3 米",
                    "snippet": "2026-06-18 长江水位 12.3 米",
                    "publish_time": "2026-06-18T09:06:00+08:00",
                    "authority_level": 1,
                    "authority_desc": "非常权威",
                    "rank_score": 0.94,
                }
            ],
            "payload_meta": {
                "Query": query,
                "SearchType": "web",
                "Count": 5,
                "NeedSummary": True,
                "ContentFormats": "text",
                "Filter": {"NeedContent": False, "NeedUrl": True, "AuthInfoLevel": 0, "Sites": "", "BlockHosts": ""},
            },
            "used_ark_fallback": False,
        },
    )

    payload = json.loads(tools.web_search.invoke({"query": "今日长江水位"}))

    assert payload["query"] == "今日长江水位 长江海事局 交通运输部"
    assert payload["trace"]["request_profile"]["Filter"]["Sites"] == ""
    assert payload["can_answer"] is True


def test_web_search_agent_browser_wraps_pages(monkeypatch):
    output = tools._format_browser_response(
        {
            "pages": [
                {
                    "title": "注意！浏览器开始记忆船队“筛选”了",
                    "url": "https://www.hifleet.com/wp/communities/fleet/zhuyiliulanqikaishijiyichuanduishaixuanle",
                    "excerpt": "在显示-船队-筛选上增加了浏览器记忆功能。",
                    "official": True,
                }
            ]
        }
    )

    assert "浏览器开始记忆船队" in output
    assert "https://www.hifleet.com/wp/communities/fleet/zhuyiliulanqikaishijiyichuanduishaixuanle" in output


def test_web_search_agent_browser_trace_keeps_raw_no_hit(monkeypatch):
    payload = build_browser_bridge_payload(
        "获取该海图图标说明页面的完整内容",
        "https://www.hifleet.com/wp/communities/fleet/haitutubiaoshuoming#post-305",
        "",
        {},
        "未检索到足够可信的信息",
        "JSONDecodeError",
    )

    assert payload["status"] == "no_hit"
    assert payload["trace"]["target_urls_present"] is True
    assert payload["trace"]["raw_status"] == "parse_error"
    assert payload["trace"]["raw_summary"] == "未检索到足够可信的信息"
    assert payload["trace"]["parse_error"] == "JSONDecodeError"


def test_web_search_agent_browser_trace_supports_keyword_bing_success():
    parsed = {
        "type": "hifleet_browser_evidence",
        "query": "船队筛选记忆功能",
        "pages": [
            {
                "title": "注意！浏览器开始记忆船队“筛选”了",
                "url": "https://www.hifleet.com/wp/communities/fleet/zhuyiliulanqikaishijiyichuanduishaixuanle",
                "excerpt": "在显示-船队-筛选上增加了浏览器记忆功能。",
                "official": True,
                "source_query": "HiFleet 船队筛选记忆功能",
            }
        ],
    }

    payload = build_browser_bridge_payload(
        "船队筛选记忆功能",
        "",
        "",
        parsed,
        json.dumps(parsed, ensure_ascii=False),
        "",
    )

    assert payload["status"] == "ok"
    assert payload["trace"]["target_urls_present"] is False
    assert payload["trace"]["raw_status"] == "hifleet_browser_evidence"
    assert payload["pages"][0]["source_query"] == "HiFleet 船队筛选记忆功能"


def test_agent_browser_keyword_fallback_prompt_rules_are_documented():
    repo_root = Path(__file__).resolve().parents[1]
    skill_text = (repo_root / "src/skills/knowledge_qa/SKILL.md").read_text(encoding="utf-8")
    profile_text = (repo_root / "config/profiles/customer_support.md").read_text(encoding="utf-8")

    assert "web_search" in skill_text and "Bing" in skill_text and "官方候选" in skill_text
    assert "无有效命中" in profile_text
    assert "短关键词" in profile_text
    assert "3–5 组短关键词" in profile_text


def test_web_search_passes_and_enforces_block_hosts(monkeypatch):
    captured = {}

    def fake_web_search(query, **kwargs):
        captured.update(kwargs)
        return {
            "query": query,
            "summary": "results",
            "items": [
                {"title": "blocked", "url": "https://cars.example.com/fleet", "snippet": "汽车车队", "authority_level": 1},
                {"title": "official", "url": "https://www.hifleet.com/wp/communities/fleet/article", "snippet": "HiFleet 功能", "authority_level": 1},
            ],
            "payload_meta": {"Filter": {"Sites": tools.HIFLEET_SITES, "NeedUrl": True}},
            "used_ark_fallback": False,
        }

    monkeypatch.setattr(tools, "_web_search", fake_web_search)
    payload = json.loads(tools.web_search.invoke({"query": "HiFleet 船队功能", "block_hosts": "cars.example.com"}))

    assert captured["block_hosts"] == "cars.example.com"
    assert payload["trace"]["request_profile"]["Filter"]["BlockHosts"] == "cars.example.com"
    assert payload["trace"]["block_hosts_applied_locally"] is True
    assert payload["trace"]["blocked_result_count"] == 1
    assert [item["title"] for item in payload["items"]] == ["official"]


def test_web_search_excludes_automotive_fleet_noise_from_citable_items(monkeypatch):
    monkeypatch.setattr(
        tools,
        "_web_search",
        lambda query, **kwargs: {
            "query": query,
            "summary": "mixed results",
            "items": [
                {"title": "汽车车队司机管理平台", "url": "https://example.com/fleet", "summary": "车辆管理与司机调度", "authority_level": 1},
                {"title": "HIFLEET 船舶 CCTV 接入平台指南", "url": "https://www.hifleet.com/wp/communities/fleet/cctv", "summary": "船舶 CCTV 接入支持 GB28181", "authority_level": 1},
            ],
            "payload_meta": {"Filter": {"Sites": tools.HIFLEET_SITES}},
            "used_ark_fallback": False,
        },
    )

    payload = json.loads(tools.web_search.invoke({"query": "HiFleet 船舶 CCTV 接入"}))

    assert [item["title"] for item in payload["items"]] == ["HIFLEET 船舶 CCTV 接入平台指南"]
    assert payload["items"][0]["authority"] == 1.0


def test_web_search_reranks_and_filters_unrelated_official_product_pages(monkeypatch):
    monkeypatch.setattr(
        tools,
        "_web_search",
        lambda query, **kwargs: {
            "query": query,
            "summary": "mixed official results",
            "items": [
                {"title": "HIFLEET 上线船舶进入目的港 PSC 检查窗口期智能提醒", "url": "https://www.hifleet.com/wp/communities/fleet/psc", "summary": "PSC 检查提醒", "authority_level": 1, "rank_score": 0.99},
                {"title": "船队筛选记忆功能", "url": "https://www.hifleet.com/wp/communities/fleet/filter-memory", "summary": "浏览器记忆船队筛选功能", "authority_level": 1, "rank_score": 0.8},
                {"title": "HiFleet 官方社区", "url": "https://www.hifleet.com/wp/communities/recent", "summary": "最近文章", "authority_level": 1, "rank_score": 1.0},
            ],
            "payload_meta": {"Filter": {"Sites": tools.HIFLEET_SITES}},
            "used_ark_fallback": False,
        },
    )

    payload = json.loads(tools.web_search.invoke({"query": "HiFleet 筛选船队记忆功能"}))

    assert [item["title"] for item in payload["items"]] == ["船队筛选记忆功能"]
    assert payload["best_urls"] == ["https://www.hifleet.com/wp/communities/fleet/filter-memory"]
