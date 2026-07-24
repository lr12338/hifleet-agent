from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ScenarioContract:
    name: str
    allowed_tools: frozenset[str]
    forbidden_claims: tuple[str, ...] = ()


CONTRACTS = {
    "ship_lookup": ScenarioContract("ship_lookup", frozenset({"ship_search", "get_ship_position", "get_ship_archive", "get_ship_call_ports", "search_ports"})),
    "position_update": ScenarioContract("position_update", frozenset({"prepare_ship_update", "commit_ship_update", "cancel_ship_update", "ship_search"})),
    "static_update": ScenarioContract("static_update", frozenset({"prepare_ship_update", "commit_ship_update", "cancel_ship_update", "ship_search", "get_ship_archive"}), ("不得断言用户前台有编辑入口", "不得承诺立即生效")),
    "platform_operation": ScenarioContract("platform_operation", frozenset({"local_kb_search", "web_search"}), ("不得根据内部工具证明前台功能",)),
    "membership_permissions": ScenarioContract("membership_permissions", frozenset({"local_kb_search", "web_search"}), ("不得编造价格、额度或套餐",)),
    "multimodal_symbol": ScenarioContract("multimodal_symbol", frozenset({"inspect_media", "local_kb_search", "web_search"}), ("不得只凭颜色命名符号",)),
}


def classify(text: str, *, has_media: bool = False) -> ScenarioContract | None:
    value = (text or "").lower()
    if any(token in value for token in ("目的港", "eta", "船名", "船型", "类型", "船旗", "呼号", "建造年", "吃水", "静态信息")) and any(token in value for token in ("更新", "上传", "修改", "更正", "录入", "手动")):
        return CONTRACTS["static_update"]
    if any(token in value for token in ("更新船位", "更新位置", "船位", "经度", "纬度", "航速", "航向")) and any(token in value for token in ("更新", "上传", "修改", "确认")):
        return CONTRACTS["position_update"]
    if any(token in value for token in ("会员", "权限", "套餐", "价格", "额度")):
        return CONTRACTS["membership_permissions"]
    if has_media and any(token in value for token in ("符号", "海图", "截图", "这是什么", "图上", "图中", "圈圈", "波浪线", "锯齿线", "标记", "图案")):
        return CONTRACTS["multimodal_symbol"]
    if any(token in value for token in ("怎么", "入口", "功能", "上传不了", "平台")):
        return CONTRACTS["platform_operation"]
    if any(token in value for token in ("mmsi", "imo", "船位", "船名")):
        return CONTRACTS["ship_lookup"]
    return None
