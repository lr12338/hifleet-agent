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


class FakeUpdateStaticInfo:
    def __init__(self, result='{"status":"1"}'):
        self.calls = []
        self.result = result

    def update_static_info(self, data, usertoken=""):
        self.calls.append((data, usertoken))
        return self.result


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


def test_upload_ship_position_normalizes_optional_eta(monkeypatch):
    fake_upload = FakeUploadPosition()
    monkeypatch.setattr(ship_tools, "_ensure_imports", lambda: None)
    monkeypatch.setattr(ship_tools, "_coord_utils", FakeCoordUtils)
    monkeypatch.setattr(ship_tools, "_upload_position", fake_upload)
    monkeypatch.setattr(ship_tools, "_ttse_key", lambda: "")

    output = ship_tools.upload_ship_position.invoke(
        {
            "mmsi": "413994561",
            "lon": "116.3291",
            "lat": "29.816783",
            "updatetime": "2026-07-08 16:18:00",
            "destination": "HUKOU",
            "eta": "2026-07-06 18:30 (UTC)",
        }
    )

    assert "船位更新成功" in output
    assert fake_upload.calls[0][0]["eta"] == "2026-07-06 18:30:00"


def test_upload_ship_position_drops_invalid_optional_eta(monkeypatch):
    fake_upload = FakeUploadPosition()
    monkeypatch.setattr(ship_tools, "_ensure_imports", lambda: None)
    monkeypatch.setattr(ship_tools, "_coord_utils", FakeCoordUtils)
    monkeypatch.setattr(ship_tools, "_upload_position", fake_upload)
    monkeypatch.setattr(ship_tools, "_ttse_key", lambda: "")

    ship_tools.upload_ship_position.invoke(
        {
            "mmsi": "413994561",
            "lon": "116.3291",
            "lat": "29.816783",
            "updatetime": "2026-07-08 16:18:00",
            "destination": "HUKOU",
            "eta": "2026-",
        }
    )

    assert "eta" not in fake_upload.calls[0][0]


def test_update_ship_static_info_success_returns_verification_link_and_fields(monkeypatch):
    fake_static = FakeUpdateStaticInfo()
    monkeypatch.setattr(ship_tools, "_ensure_imports", lambda: None)
    monkeypatch.setattr(ship_tools, "_update_static_info", fake_static)
    monkeypatch.setattr(ship_tools, "_ttse_key", lambda: "")

    output = ship_tools.update_ship_static_info.invoke(
        {
            "mmsi": "414402130",
            "destination": "HUI ZHOU",
            "eta": "2026-07-11 20:00",
            "draft": "7.9",
            "ship_name": "TEST SHIP",
            "imo": "9876543",
        }
    )

    assert "静态信息更新成功！" in output
    assert "MMSI: 414402130" in output
    assert "点击查看：https://open.weixin.qq.com/connect/oauth2/authorize?" in output
    assert "state=414402130#wechat_redirect" in output
    assert "更新参数:" in output
    assert "船名: TEST SHIP" in output
    assert "IMO: 9876543" in output
    assert "目的港: HUI ZHOU" in output
    assert "ETA: 2026-07-11 20:00:00" in output
    assert "吃水: 7.9 米" in output
    assert "数据同步：预计 5 分钟内生效" in output
    assert "更新字段:" not in output
    assert fake_static.calls[0][0]["mmsi"] == "414402130"
    assert fake_static.calls[0][0]["destination"] == "HUI ZHOU"
    assert fake_static.calls[0][0]["eta"] == "2026-07-11 20:00:00"
    assert fake_static.calls[0][0]["draught"] == 7.9


def test_update_ship_static_info_syncs_ship_type_to_minotype(monkeypatch):
    fake_static = FakeUpdateStaticInfo()
    monkeypatch.setattr(ship_tools, "_ensure_imports", lambda: None)
    monkeypatch.setattr(ship_tools, "_update_static_info", fake_static)
    monkeypatch.setattr(ship_tools, "_ttse_key", lambda: "")

    output = ship_tools.update_ship_static_info.invoke(
        {
            "mmsi": "730285526",
            "ship_type": "散货船",
        }
    )

    assert "静态信息更新成功！" in output
    assert "船舶类型: 散货船" in output
    assert "船舶子类型: 散货船" in output
    assert fake_static.calls[0][0]["type"] == "散货船"
    assert fake_static.calls[0][0]["minotype"] == "散货船"


def test_update_ship_static_info_rejects_mismatched_ship_type_fields(monkeypatch):
    fake_static = FakeUpdateStaticInfo()
    monkeypatch.setattr(ship_tools, "_ensure_imports", lambda: None)
    monkeypatch.setattr(ship_tools, "_update_static_info", fake_static)
    monkeypatch.setattr(ship_tools, "_ttse_key", lambda: "")

    output = ship_tools.update_ship_static_info.invoke(
        {
            "mmsi": "730285526",
            "ship_type": "散货船",
            "minotype": "油船",
        }
    )

    assert "船舶类型字段不一致" in output
    assert fake_static.calls == []
