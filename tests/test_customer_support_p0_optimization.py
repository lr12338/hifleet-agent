import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from langchain_core.messages import AIMessage, HumanMessage

from agents.agent import _build_lightweight_customer_support_agent
from agents.customer_support_evidence_guard import apply_high_risk_evidence_guard
from agents.profiles import AgentProfile
from agents.ship_update_extractor import extract_and_normalize_ship_update
from skills.hifleet_ship_service.tools import upload_ship_position


class FakeTool:
    def __init__(self, name, handler):
        self.name = name
        self.handler = handler
        self.calls = []

    def invoke(self, args):
        self.calls.append(args)
        return self.handler(args)


def _graph(monkeypatch, *, tools=None, final_answer=""):
    class FakeStandardAgent:
        def invoke(self, payload, context=None, config=None):
            return {"messages": list(payload["messages"]) + [AIMessage(content=final_answer)]}

    monkeypatch.setattr("agents.agent._build_standard_agent", lambda *args, **kwargs: FakeStandardAgent())
    monkeypatch.setattr("agents.agent.SkillLoader.get_tools_by_names", lambda names: tools or [])
    return _build_lightweight_customer_support_agent(
        ctx=SimpleNamespace(run_id="r-p0"),
        cfg={"config": {}},
        workspace_path=str(Path(__file__).resolve().parents[1]),
        profile=AgentProfile(profile_id="customer_support", skills=["knowledge_qa", "hifleet_ship_service"]),
    )


def test_destination_eta_frontend_capability_guard_blocks_fake_tutorial(monkeypatch):
    graph = _graph(monkeypatch, final_answer="可以在网页端船舶详情页点击编辑按钮修改目的港和 ETA，提交后立即生效。")
    result = graph.invoke(
        {
            "messages": [HumanMessage(content="怎么在 HiFleet 平台手动更新船舶目的港和 ETA？")],
            "session_id": "s-dest-eta-front",
            "agent_profile": "customer_support",
        },
        config={"configurable": {"thread_id": "s-dest-eta-front"}},
    )
    answer = result["messages"][-1].content
    assert "编辑按钮" not in answer
    assert "立即生效" not in answer
    assert "自助编辑船舶目的港/ETA" in answer
    assert result["generated_tool_calls"] == []
    assert result["route_trace"]["ship_update_gate"]["should_run_harness"] is False
    assert result["route_trace"]["evidence_guard"]["triggered"] is True


def test_reports_email_eta_guard_blocks_auto_parse_claim(monkeypatch):
    graph = _graph(monkeypatch, final_answer="可以发邮件到 reports@hifleet.com，系统会自动解析更新 ETA。")
    result = graph.invoke(
        {
            "messages": [HumanMessage(content="能不能发邮件到 reports@hifleet.com 更新 ETA？")],
            "session_id": "s-reports-eta",
            "agent_profile": "customer_support",
        },
        config={"configurable": {"thread_id": "s-reports-eta"}},
    )
    answer = result["messages"][-1].content
    assert "自动解析更新 ETA" not in answer
    assert "文本邮件自动更新目的港/ETA" in answer
    assert "MMSI" in answer
    assert result["generated_tool_calls"] == []
    assert result["route_trace"]["evidence_guard"]["triggered"] is True


def test_final_answer_evidence_guard_rewrites_unsupported_claim():
    result = apply_high_risk_evidence_guard(
        "可以在网页端船舶详情页点击编辑按钮修改目的港和 ETA，提交后立即生效。",
        route_trace={},
    )
    assert result.triggered is True
    assert "编辑按钮" not in result.text
    assert "立即生效" not in result.text
    assert "没有查到 HiFleet 前台" in result.text


def test_final_answer_evidence_guard_allows_negated_safe_statement():
    text = "目前没有查到普通用户可在前台自助编辑目的港/ETA 的明确入口。"
    result = apply_high_risk_evidence_guard(text, route_trace={})
    assert result.triggered is False
    assert result.text == text


def test_ship_update_extractor_handles_spaced_dmm_and_suspicious_time():
    extraction, normalized = extract_and_normalize_ship_update(
        "更新船位，mmsi：730285526，更新时间：22026-07-04 15:36，经度：038°48.771′ E，纬度：19°40.094′ N ，航速：10.9，船艏向：166，航迹向：166，吃水：11.2"
    )
    assert extraction.mmsi == "730285526"
    assert normalized.longitude_decimal == 38.81285
    assert normalized.latitude_decimal == 19.668233
    assert extraction.speed == 10.9
    assert extraction.heading == 166
    assert extraction.course == 166
    assert extraction.draft == 11.2
    assert extraction.updatetime_valid is False
    assert extraction.updatetime_suggestion == "2026-07-04 15:36"
    assert "有效更新时间" in extraction.missing_required_fields
    assert extraction.can_write is False
    assert extraction.need_user_confirmation is True


def test_ship_update_extractor_allows_complete_spaced_dmm():
    extraction, normalized = extract_and_normalize_ship_update(
        "更新船位，mmsi：730285526，更新时间：2026-07-04 15:36，经度：038°48.771′ E，纬度：19°40.094′ N，航速：10.9，船艏向：166，航迹向：166，吃水：11.2"
    )
    assert normalized.longitude_decimal == 38.81285
    assert normalized.latitude_decimal == 19.668233
    assert extraction.heading == 166
    assert extraction.can_write is True


def test_ship_update_extractor_blocks_dmm_without_direction():
    extraction, _normalized = extract_and_normalize_ship_update(
        "更新船位，MMSI 730285526，经度：038°48.771′，纬度：19°40.094′，时间：2026-07-04 15:36"
    )
    assert extraction.can_write is False
    assert extraction.need_user_confirmation is True
    assert "有效经度" in extraction.missing_required_fields
    assert "有效纬度" in extraction.missing_required_fields
    assert "方向字母" in (extraction.user_confirmation_message or "")


def test_ship_update_extractor_handles_decimal_with_direction_aliases():
    extraction, normalized = extract_and_normalize_ship_update(
        "更新船位，MMSI 730285526，经度 38.81285E，纬度 19.668233N，时间 2026/07/04 15:36，SOG 10.9，HDG 166，COG 166"
    )
    assert normalized.longitude_decimal == 38.81285
    assert normalized.latitude_decimal == 19.668233
    assert extraction.speed == 10.9
    assert extraction.heading == 166
    assert extraction.course == 166
    assert extraction.can_write is True


def test_ship_update_extractor_handles_hyphen_dmm_pair():
    extraction, normalized = extract_and_normalize_ship_update(
        "更新船位 MMSI:375066971 位置：25-15.61n 056-29.1e 更新时间：2026-07-03 1010 (UTC+8)"
    )

    assert extraction.mmsi == "375066971"
    assert extraction.raw_updatetime == "2026-07-03 1010"
    assert extraction.normalized_updatetime == "2026-07-03 10:10:00"
    assert normalized.latitude_decimal == 25.260167
    assert normalized.longitude_decimal == 56.485
    assert extraction.can_write is True


def test_ship_update_extractor_handles_quoted_dmm_pair():
    extraction, normalized = extract_and_normalize_ship_update(
        "请更新船位 MMSI:730285526 POSN 02°27.805'N 119°34.947'E 更新时间 2026-07-03 1010 (UTC+8)"
    )

    assert extraction.mmsi == "730285526"
    assert normalized.latitude_decimal == 2.463417
    assert normalized.longitude_decimal == 119.58245
    assert extraction.normalized_updatetime == "2026-07-03 10:10:00"
    assert extraction.can_write is True


def test_lightweight_ship_update_invalid_time_precise_feedback(monkeypatch):
    position = FakeTool("upload_ship_position", lambda args: "不应调用")
    graph = _graph(monkeypatch, tools=[position])
    result = graph.invoke(
        {
            "messages": [
                HumanMessage(
                    content="更新船位，mmsi：730285526，更新时间：22026-07-04 15:36，经度：038°48.771′ E，纬度：19°40.094′ N，航速：10.9，船艏向：166，航迹向：166，吃水：11.2"
                )
            ],
            "session_id": "s-invalid-time",
            "agent_profile": "customer_support",
        },
        config={"configurable": {"thread_id": "s-invalid-time"}},
    )
    answer = result["messages"][-1].content
    assert position.calls == []
    assert "疑似年份多输入了一个 2" in answer
    assert "2026-07-04 15:36" in answer
    assert result["route_trace"]["reasoning_trace"]["ship_update_extraction"]["can_write"] is False


def test_lightweight_ship_update_complete_fields_calls_upload(monkeypatch):
    position = FakeTool("upload_ship_position", lambda args: "船位更新成功！")
    graph = _graph(monkeypatch, tools=[position])
    result = graph.invoke(
        {
            "messages": [
                HumanMessage(
                    content="更新船位，mmsi：730285526，更新时间：2026-07-04 15:36，经度：038°48.771′ E，纬度：19°40.094′ N，航速：10.9，船艏向：166，航迹向：166，吃水：11.2"
                )
            ],
            "session_id": "s-valid-position",
            "agent_profile": "customer_support",
        },
        config={"configurable": {"thread_id": "s-valid-position"}},
    )
    assert result["generated_tool_calls"] == ["upload_ship_position"]
    assert position.calls[0]["mmsi"] == "730285526"
    assert position.calls[0]["lon"] == "038°48.771′ E"
    assert position.calls[0]["lat"] == "19°40.094′ N"
    assert position.calls[0]["heading"] == "166"
    assert position.calls[0]["updatetime"] == "2026-07-04 15:36:00"


def test_contract_extractor_handles_compact_time_and_navstatus_from_log():
    text = """请更新船位：MMSI:730285526
更新时间：2026-07-04 1443
 (UTC+8)
AIS船名
QING FENG LING
系泊
经度：121°41.23′ E
纬度：39°00.41′ N
IMO
9663702
船艏向
359° 航向 359°"""

    extraction, normalized = extract_and_normalize_ship_update(text)

    assert extraction.operation_type == "position_update"
    assert extraction.source == "fallback_contract_parser"
    assert extraction.mmsi == "730285526"
    assert extraction.imo == "9663702"
    assert extraction.raw_updatetime == "2026-07-04 1443"
    assert extraction.normalized_updatetime == "2026-07-04 14:43:00"
    assert normalized.longitude_decimal == 121.687167
    assert normalized.latitude_decimal == 39.006833
    assert extraction.heading == 359
    assert extraction.course == 359
    assert extraction.nav_status == "系泊"
    assert extraction.can_write is True
    assert "按 HHMM 理解" in " ".join(extraction.ambiguities)


def test_contract_extractor_supports_chinese_colon_and_slash_compact_time():
    first, _ = extract_and_normalize_ship_update(
        "更新船位 MMSI 730285526 经度 121.5E 纬度 39.1N 更新时间 2026-07-04 14：43"
    )
    second, _ = extract_and_normalize_ship_update(
        "更新船位 MMSI 730285526 经度 121.5E 纬度 39.1N 更新时间 2026/07/04 0930"
    )

    assert first.normalized_updatetime == "2026-07-04 14:43:00"
    assert second.normalized_updatetime == "2026-07-04 09:30:00"
    assert first.can_write is True
    assert second.can_write is True


def test_contract_extractor_blocks_date_only_time():
    extraction, _normalized = extract_and_normalize_ship_update(
        "更新船位 MMSI 730285526 经度 121.5E 纬度 39.1N 更新时间 2026-07-04"
    )

    assert extraction.can_write is False
    assert "具体时分" in extraction.missing_required_fields
    assert "缺少具体时分" in (extraction.user_confirmation_message or "")


def test_lightweight_ship_update_compact_time_and_navstatus_calls_upload(monkeypatch):
    monkeypatch.setattr(
        "agents.customer_support_router._invoke_ship_update_contract_llm",
        lambda text, perception=None: {
            "operation_type": "position_update",
            "fields": {
                "mmsi": "730285526",
                "imo": "9663702",
                "updatetime": "2026-07-04 1443",
                "lon": "121°41.23′ E",
                "lat": "39°00.41′ N",
                "heading": "359",
                "course": "359",
                "navstatus": "系泊",
            },
            "raw_mentions": {"updatetime": "2026-07-04 1443", "navstatus": "系泊"},
            "confidence": {},
            "ambiguities": ["时间字段未使用冒号，按 HHMM 理解。"],
            "missing_fields": [],
            "invalid_fields": [],
            "unsupported_fields": [],
            "action_allowed": True,
            "source": "llm_contract_extractor",
        },
    )
    position = FakeTool("upload_ship_position", lambda args: "船位更新成功！")
    graph = _graph(monkeypatch, tools=[position])
    result = graph.invoke(
        {
            "messages": [
                HumanMessage(
                    content="""请更新船位：MMSI:730285526
更新时间：2026-07-04 1443
 (UTC+8)
AIS船名
QING FENG LING
系泊
经度：121°41.23′ E
纬度：39°00.41′ N
IMO
9663702
船艏向
359° 航向 359°"""
                )
            ],
            "session_id": "s-compact-time-status",
            "agent_profile": "customer_support",
        },
        config={"configurable": {"thread_id": "s-compact-time-status"}},
    )

    assert result["generated_tool_calls"] == ["upload_ship_position"]
    assert position.calls[0]["updatetime"] == "2026-07-04 14:43:00"
    assert position.calls[0]["navstatus"] == "系泊"
    assert position.calls[0]["heading"] == "359"
    assert position.calls[0]["course"] == "359"
    extraction = result["route_trace"]["reasoning_trace"]["ship_update_extraction"]
    assert extraction["source"] == "llm_contract_extractor"
    assert extraction["operation_type"] == "position_update"


def test_upload_feedback_does_not_mark_provided_navstatus_or_draft_missing(monkeypatch):
    monkeypatch.setattr("skills.hifleet_ship_service.tools._ensure_imports", lambda: None)
    monkeypatch.setattr("skills.hifleet_ship_service.tools._coord_utils", SimpleNamespace(dms_to_decimal=lambda value: float(value.rstrip("EN"))))
    monkeypatch.setattr("skills.hifleet_ship_service.tools._upload_position", SimpleNamespace(upload_position=lambda data, usertoken=None: "更新成功！"))

    output = upload_ship_position.invoke(
        {
            "mmsi": "730285526",
            "lon": "121.5",
            "lat": "39.1",
            "updatetime": "2026-07-04 14:43:00",
            "navstatus": "系泊",
            "draft": "11.2",
        }
    )

    assert "航行状态: 系泊" in output
    assert "吃水: 11.2 米" in output
    assert "未更新航行状态" not in output
    assert "未更新吃水" not in output
