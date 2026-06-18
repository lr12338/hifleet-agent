import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from skills.knowledge_qa import tools


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
