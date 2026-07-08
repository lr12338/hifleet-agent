import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agents.customer_support_router import classify_message, execute_update_chain, extract_entities, make_trace


class FakeTool:
    def __init__(self, name, handler):
        self.name = name
        self.handler = handler
        self.calls = []

    def invoke(self, args):
        self.calls.append(args)
        return self.handler(args)


def test_llm_contract_extractor_position_json_drives_write_harness(monkeypatch):
    monkeypatch.setattr(
        "agents.customer_support_router._invoke_ship_update_contract_llm",
        lambda text, perception=None: {
            "operation_type": "position_update",
            "ship_identity": {"mmsi": "730285526", "identity_status": "resolved", "candidate_mmsi": []},
            "position_update_fields": {
                "name": "730285526",
                "mmsi": "730285526",
                "lon": 121.687167,
                "lat": 39.006833,
                "updatetime": "2026-07-04 14:43:00",
                "heading": 359,
                "course": 359,
                "status": "系泊",
            },
            "missing_required_fields": [],
            "invalid_fields": [],
            "conflict_fields": [],
            "action_recommendation": "execute_position_update",
            "source": "llm_contract_extractor",
        },
    )
    upload = FakeTool("upload_ship_position", lambda args: "船位更新成功！")
    text = "请更新船位：MMSI:730285526 更新时间：2026-07-04 14:43:00 经度：121.687167 纬度：39.006833 船首向：359 航迹向：359 状态：系泊"
    entities = extract_entities(text)
    trace = make_trace(classify_message(text, entities), entities)

    output = execute_update_chain(text, entities, {"upload_ship_position": upload}, trace)

    assert "成功" in output
    assert upload.calls == [
        {
            "mmsi": "730285526",
            "lon": "121.687167",
            "lat": "39.006833",
            "updatetime": "2026-07-04 14:43:00",
            "heading": "359",
            "course": "359",
            "navstatus": "系泊",
        }
    ]
    assert trace.check_result["write_result"] is True


def test_llm_contract_extractor_drops_placeholder_destination(monkeypatch):
    monkeypatch.setattr(
        "agents.customer_support_router._invoke_ship_update_contract_llm",
        lambda text, perception=None: {
            "operation_type": "position_update",
            "ship_identity": {"mmsi": "412510631", "identity_status": "resolved", "candidate_mmsi": []},
            "position_update_fields": {
                "mmsi": "412510631",
                "lon": 117.72995,
                "lat": 23.946816666666667,
                "updatetime": "2026-07-06 15:04:00",
                "speed": 1.2,
                "course": 91,
                "status": "机动船在航",
                "destination": "目的港/ETA: -- / --",
                "eta": "--",
            },
            "raw_mentions": {"destination": "目的港/ETA: -- / --", "eta": "--"},
            "missing_required_fields": [],
            "invalid_fields": [],
            "conflict_fields": [],
            "action_recommendation": "execute_position_update",
            "source": "llm_contract_extractor",
        },
    )
    upload = FakeTool("upload_ship_position", lambda args: "船位更新成功！")
    text = "更新船位，MMSI:412510631，位置：23°56.809' N 117°43.797' E，目的港/ETA: -- / --，更新时间：2026-07-06 15:04:00"
    entities = extract_entities(text)
    trace = make_trace(classify_message(text, entities), entities)

    output = execute_update_chain(text, entities, {"upload_ship_position": upload}, trace)

    assert "成功" in output
    assert "destination" not in upload.calls[0]
    assert "eta" not in upload.calls[0]
    assert "destination" not in trace.reasoning_trace["write_args"]
    extraction = trace.check_result["ship_update_extraction"]
    assert "destination" not in extraction["raw_fields"]
    assert extraction["normalized_fields"]["destination"] == ""


def test_llm_contract_extractor_static_json_drives_write_harness(monkeypatch):
    monkeypatch.setattr(
        "agents.customer_support_router._invoke_ship_update_contract_llm",
        lambda text, perception=None: {
            "operation_type": "static_update",
            "ship_identity": {"mmsi": "730285526", "identity_status": "resolved", "candidate_mmsi": []},
            "static_update_fields": {
                "mmsi": "730285526",
                "destination": "SINGAPORE",
                "eta": "2026-07-08 03:00:00",
                "draught": "12.6",
            },
            "missing_required_fields": [],
            "invalid_fields": [],
            "conflict_fields": [],
            "action_recommendation": "execute_static_update",
            "source": "llm_contract_extractor",
        },
    )
    static_update = FakeTool("update_ship_static_info", lambda args: "静态信息更新成功！")
    text = "更新船舶静态信息，MMSI:730285526，目的港：SINGAPORE，ETA：2026-07-08 03:00:00，吃水：12.6"
    entities = extract_entities(text)
    trace = make_trace(classify_message(text, entities), entities)

    output = execute_update_chain(text, entities, {"update_ship_static_info": static_update}, trace)

    assert "静态信息更新成功" in output
    assert static_update.calls == [
        {
            "mmsi": "730285526",
            "destination": "SINGAPORE",
            "eta": "2026-07-08 03:00:00",
            "draft": "12.6",
        }
    ]
    assert trace.check_result["write_result"] is True


def test_llm_contract_extractor_static_ship_type_syncs_minotype(monkeypatch):
    monkeypatch.setattr(
        "agents.customer_support_router._invoke_ship_update_contract_llm",
        lambda text, perception=None: {
            "operation_type": "static_update",
            "ship_identity": {"mmsi": "730285526", "identity_status": "resolved", "candidate_mmsi": []},
            "static_update_fields": {
                "mmsi": "730285526",
                "type": "散货船",
            },
            "missing_required_fields": [],
            "invalid_fields": [],
            "conflict_fields": [],
            "action_recommendation": "execute_static_update",
            "source": "llm_contract_extractor",
        },
    )
    static_update = FakeTool("update_ship_static_info", lambda args: "静态信息更新成功！")
    text = "更新船舶类型，MMSI 730285526，散货船"
    entities = extract_entities(text)
    trace = make_trace(classify_message(text, entities), entities)

    output = execute_update_chain(text, entities, {"update_ship_static_info": static_update}, trace)

    assert "静态信息更新成功" in output
    assert static_update.calls == [
        {
            "mmsi": "730285526",
            "ship_type": "散货船",
            "minotype": "散货船",
        }
    ]
