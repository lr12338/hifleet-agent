import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import skills.hifleet_ship_service.tools as ship_tools


class FakeCoordUtils:
    @staticmethod
    def dms_to_decimal(value):
        return float(value)


class FakeUploadPosition:
    def __init__(self):
        self.calls = []

    def upload_position(self, data, usertoken=""):
        self.calls.append((data, usertoken))
        return "更新成功！"


def test_upload_ship_position_requires_explicit_updatetime(monkeypatch):
    fake_upload = FakeUploadPosition()
    monkeypatch.setattr(ship_tools, "_ensure_imports", lambda: None)
    monkeypatch.setattr(ship_tools, "_coord_utils", FakeCoordUtils)
    monkeypatch.setattr(ship_tools, "_upload_position", fake_upload)

    output = ship_tools.upload_ship_position.invoke(
        {
            "mmsi": "414726000",
            "lon": "121.4737",
            "lat": "31.2304",
            "speed": "5",
            "heading": "120",
            "draft": "9.6",
            "navstatus": "在航",
        }
    )

    assert "更新时间" in output
    assert "不会复用历史船舶或自动生成更新时间" in output
    assert fake_upload.calls == []


def test_upload_ship_position_lists_missing_required_position_fields(monkeypatch):
    fake_upload = FakeUploadPosition()
    monkeypatch.setattr(ship_tools, "_ensure_imports", lambda: None)
    monkeypatch.setattr(ship_tools, "_coord_utils", FakeCoordUtils)
    monkeypatch.setattr(ship_tools, "_upload_position", fake_upload)

    output = ship_tools.upload_ship_position.invoke(
        {
            "mmsi": "414726000",
            "lon": "121.4737",
            "updatetime": "2026-06-15 10:20:30",
        }
    )

    assert "纬度" in output
    assert fake_upload.calls == []


def test_upload_ship_position_allows_missing_optional_fields(monkeypatch):
    fake_upload = FakeUploadPosition()
    monkeypatch.setattr(ship_tools, "_ensure_imports", lambda: None)
    monkeypatch.setattr(ship_tools, "_coord_utils", FakeCoordUtils)
    monkeypatch.setattr(ship_tools, "_upload_position", fake_upload)
    monkeypatch.setattr(ship_tools, "_ttse_key", lambda: "")

    output = ship_tools.upload_ship_position.invoke(
        {
            "mmsi": "414726000",
            "lon": "121.4737",
            "lat": "31.2304",
            "updatetime": "2026-06-15 10:20:30",
        }
    )

    assert "船位更新成功" in output
    assert "本次未更新航速、船首向、吃水、航行状态等字段" in output
    assert fake_upload.calls[0][0]["mmsi"] == "414726000"
    assert fake_upload.calls[0][0]["checkFly"] == "0"
    assert fake_upload.calls[0][0]["bindCheck"] == "0"
    assert "speed" not in fake_upload.calls[0][0]
    assert "heading" not in fake_upload.calls[0][0]
    assert "draught" not in fake_upload.calls[0][0]
    assert "status" not in fake_upload.calls[0][0]


def test_upload_ship_position_success_uses_only_submitted_time_and_fields(monkeypatch):
    fake_upload = FakeUploadPosition()
    monkeypatch.setattr(ship_tools, "_ensure_imports", lambda: None)
    monkeypatch.setattr(ship_tools, "_coord_utils", FakeCoordUtils)
    monkeypatch.setattr(ship_tools, "_upload_position", fake_upload)
    monkeypatch.setattr(ship_tools, "_ttse_key", lambda: "")

    output = ship_tools.upload_ship_position.invoke(
        {
            "mmsi": "414726000",
            "lon": "121.4737",
            "lat": "31.2304",
            "speed": "5",
            "heading": "120",
            "draft": "9.6",
            "navstatus": "航行中",
            "updatetime": "2026-06-15 10:20:30",
        }
    )

    assert "船位更新成功" in output
    assert "更新时间：2026-06-15 10:20:30 (UTC+8)" in output
    assert "航行状态: 在航" in output
    assert "修正" not in output
    assert "之前错误" not in output
    assert fake_upload.calls[0][0]["updatetime"] == "2026-06-15 10:20:30"
    assert fake_upload.calls[0][0]["status"] == "在航"
