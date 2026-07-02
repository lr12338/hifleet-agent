import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import skills.hifleet_ship_service.tools as ship_tools


class FakeUpdateStatic:
    def __init__(self):
        self.calls = []

    def update_static_info(self, data, usertoken=""):
        self.calls.append((data, usertoken))
        return "更新成功！"


def test_update_ship_static_info_syncs_ship_type_and_minotype(monkeypatch):
    fake_update = FakeUpdateStatic()
    monkeypatch.setattr(ship_tools, "_ensure_imports", lambda: None)
    monkeypatch.setattr(ship_tools, "_update_static_info", fake_update)
    monkeypatch.setattr(ship_tools, "_ttse_key", lambda: "")

    output = ship_tools.update_ship_static_info.invoke(
        {
            "mmsi": "414726000",
            "ship_type": "散货船",
        }
    )

    assert "静态信息更新成功" in output
    payload = fake_update.calls[0][0]
    assert payload["type"] == "散货船"
    assert payload["minotype"] == "散货船"


def test_update_ship_static_info_rejects_invalid_ship_type_without_api_call(monkeypatch):
    fake_update = FakeUpdateStatic()
    monkeypatch.setattr(ship_tools, "_ensure_imports", lambda: None)
    monkeypatch.setattr(ship_tools, "_update_static_info", fake_update)

    output = ship_tools.update_ship_static_info.invoke(
        {
            "mmsi": "414726000",
            "ship_type": "化学品运输船",
        }
    )

    assert "不在支持目录内" in output
    assert "请从以下标准船型中确认后重试" in output
    assert fake_update.calls == []


def test_update_ship_static_info_skips_invalid_ship_type_but_updates_other_fields(monkeypatch):
    fake_update = FakeUpdateStatic()
    monkeypatch.setattr(ship_tools, "_ensure_imports", lambda: None)
    monkeypatch.setattr(ship_tools, "_update_static_info", fake_update)
    monkeypatch.setattr(ship_tools, "_ttse_key", lambda: "")

    output = ship_tools.update_ship_static_info.invoke(
        {
            "mmsi": "414726000",
            "ship_type": "化学品运输船",
            "destination": "SHANGHAI",
        }
    )

    assert "静态信息更新成功" in output
    assert "本次未更新船舶类型" in output
    payload = fake_update.calls[0][0]
    assert payload["destination"] == "SHANGHAI"
    assert "type" not in payload
    assert "minotype" not in payload


def test_update_ship_static_info_blocks_conflicting_ship_type_fields_only(monkeypatch):
    fake_update = FakeUpdateStatic()
    monkeypatch.setattr(ship_tools, "_ensure_imports", lambda: None)
    monkeypatch.setattr(ship_tools, "_update_static_info", fake_update)
    monkeypatch.setattr(ship_tools, "_ttse_key", lambda: "")

    output = ship_tools.update_ship_static_info.invoke(
        {
            "mmsi": "414726000",
            "ship_type": "散货船",
            "minotype": "油船",
            "destination": "NINGBO",
        }
    )

    assert "船舶类型字段存在冲突" in output
    payload = fake_update.calls[0][0]
    assert payload["destination"] == "NINGBO"
    assert "type" not in payload
    assert "minotype" not in payload
