"""
HiFleet 船舶智能服务工具集
8个独立工具函数，直接调用 Hifleet API，替代原 ship_service_workflow_api。

工具清单：
  A. ship_search          — 船舶搜索（按关键字获取MMSI/IMO）→ TTSE API，返回纯文本
  B. get_ship_position    — 船位查询（实时位置/航速/航向）→ API JSON
  C. get_ship_archive     — 船舶档案（基本参数/尺寸/载重吨）→ API JSON
  D. get_psc_records      — PSC检查记录 → API JSON
  E. get_area_traffic     — 区域船舶数量 → API JSON
  F. get_strait_traffic   — 海峡通航统计 → API JSON
  G. upload_ship_position — 船位上传 → TTSE API，返回纯文本
  H. update_ship_static_info — 静态信息更新 → TTSE API，返回纯文本/JSON
"""
import os
import sys
import json
import logging
import re
import urllib.parse
import urllib.request
import time
from typing import Optional
from datetime import datetime

from langchain.tools import tool
from coze_coding_utils.log.write_log import request_context
from coze_coding_utils.runtime_ctx.context import new_context
from skills.common.tool_result import ToolResult, emit_tool_metric

logger = logging.getLogger(__name__)


def _emit_result(tool_name: str, ctx, result: ToolResult):
    run_id = getattr(ctx, "run_id", "")
    emit_tool_metric(
        tool_name,
        run_id,
        result,
        layer_trace={
            "method": getattr(ctx, "method", ""),
            "source_channel": getattr(ctx, "source_channel", ""),
        },
    )


def _wechat_ship_url(mmsi: str) -> str:
    return (
        "https://open.weixin.qq.com/connect/oauth2/authorize?"
        "appid=wx9d402b54c1d84ebf&"
        "redirect_uri=http://www.hifleet.com/wap-simple/index.html&"
        f"response_type=code&scope=snsapi_base&state={mmsi}#wechat_redirect"
    )


def _format_static_update_success(mmsi: str, data: dict) -> str:
    field_labels = [
        ("name", "船名", ""),
        ("imonumber", "IMO", ""),
        ("type", "船舶类型", ""),
        ("minotype", "船舶子类型", ""),
        ("flag", "船旗", ""),
        ("callsign", "呼号", ""),
        ("length", "船长", " 米"),
        ("width", "船宽", " 米"),
        ("dwt", "载重吨", ""),
        ("buildyear", "建造年份", ""),
        ("destination", "目的港", ""),
        ("eta", "ETA", ""),
        ("draught", "吃水", " 米"),
    ]
    lines = [
        "静态信息更新成功！",
        f"MMSI: {mmsi}",
        f"点击查看：{_wechat_ship_url(mmsi)}",
        "更新参数:",
    ]
    for key, label, suffix in field_labels:
        if key not in data:
            continue
        value = data.get(key)
        if value in (None, ""):
            continue
        lines.append(f"{label}: {value}{suffix}")
    lines.append("数据同步：预计 5 分钟内生效")
    return "\n".join(lines)


def _clean_static_ship_type(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _sync_static_ship_type_fields(ship_type: str, minotype: str) -> tuple[str, str, str]:
    ship_type_value = _clean_static_ship_type(ship_type)
    minotype_value = _clean_static_ship_type(minotype)
    if ship_type_value and minotype_value and ship_type_value != minotype_value:
        return (
            ship_type_value,
            minotype_value,
            f"船舶类型字段不一致：ship_type={ship_type_value}，minotype={minotype_value}。"
            "更新船舶类型时 type 和 minotype 必须一致，请确认后重试。",
        )
    unified = ship_type_value or minotype_value
    return unified, unified, ""

# 将 scripts/ 目录加入 sys.path，以便直接 import
_SCRIPTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scripts")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

# 延迟导入 scripts 模块
_search_ship = None
_get_position = None
_get_archive = None
_get_psc = None
_get_area_traffic_mod = None
_get_strait_traffic_mod = None
_upload_position = None
_update_static_info = None
_coord_utils = None


def _api_base() -> str:
    return (os.getenv("HIFLEET_API_BASE") or "https://api.hifleet.com").rstrip("/")


def _first_env(*names: str) -> str:
    for name in names:
        value = os.getenv(name)
        if value and value.strip():
            return value.strip()
    return ""


def _public_api_key() -> str:
    return _first_env("api_key", "HIFLEET_API_KEY", "hifleet_key1")


def _psc_api_key() -> str:
    return _first_env("hifleet_key1", "HIFLEET_API_KEY", "api_key")


def _ttse_key() -> str:
    return _first_env("hifleet_key2", "HIFLEET_TTSE_KEY", "HIFLEET_API_KEY")


def _http_json(method: str, path: str, params: dict, auth_scope: str = "public") -> dict:
    if auth_scope == "psc":
        key = _psc_api_key()
    elif auth_scope == "none":
        key = ""
    else:
        key = _public_api_key()
    if key:
        params = {**params, "api_key": key, "usertoken": key}
    url = _api_base() + path + "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v not in (None, "")})
    data = b"" if method.upper() == "POST" else None
    req = urllib.request.Request(url, method=method.upper(), data=data)
    if data is not None:
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode())


def _format_json_result(data: dict, title: str) -> str:
    if not data:
        return f"未找到{title}数据。"
    code = data.get("code")
    if code is not None:
        try:
            if int(code) >= 4000:
                message = str(data.get("message") or data.get("msg") or "接口返回错误")
                if "unauthor" in message.lower() or "token" in message.lower():
                    return f"{title}接口授权不足，当前 token 无法访问该能力。原始错误：{message}"
                return f"{title}接口返回错误（code={code}）：{message}"
        except (TypeError, ValueError):
            pass
    return json.dumps(data, ensure_ascii=False, indent=2)[:6000]


def _ensure_imports():
    """延迟导入所有 scripts 模块"""
    global _search_ship, _get_position, _get_archive, _get_psc
    global _get_area_traffic_mod, _get_strait_traffic_mod
    global _upload_position, _update_static_info, _coord_utils

    if _search_ship is not None:
        return

    import search_ship
    import get_position
    import get_archive
    import get_psc
    import get_area_traffic as area_traffic_mod
    import get_strait_traffic as strait_traffic_mod
    import upload_position
    import update_static_info
    import coord_utils

    _search_ship = search_ship
    _get_position = get_position
    _get_archive = get_archive
    _get_psc = get_psc
    _get_area_traffic_mod = area_traffic_mod
    _get_strait_traffic_mod = strait_traffic_mod
    _upload_position = upload_position
    _update_static_info = update_static_info
    _coord_utils = coord_utils


# ============================================================
# 格式化函数 — 适配真实 API 返回格式
# ============================================================

def _parse_lon_lat(lo_str: str, la_str: str):
    """
    解析Hifleet经纬度格式。
    
    API返回的经纬度是度分格式，如:
      lo="7354.2022705" 表示 73°54.2023'E → 73.903371度
      la="1824.2845917" 表示 18°24.2846'N → 18.404743度
    
    也可能是十进制度格式，如:
      lo="116.875" → 116.875度
    
    Returns:
        (经度, 纬度) 十进制度，或 (None, None)
    """
    def convert(val_str):
        if not val_str or val_str == "-":
            return None
        try:
            val = float(val_str)
            # 度分格式判断：整数值>180则为度分格式
            # 如 7354.20 → 度=73, 分=54.20
            # 如 116.875 → 十进制度（度<180）
            int_part = int(abs(val))
            if int_part >= 180:
                # 度分格式
                degrees = int_part // 100
                minutes = val - degrees * 100
                return degrees + minutes / 60
            else:
                # 十进制度格式
                return val
        except (ValueError, TypeError):
            return None

    return convert(lo_str), convert(la_str)


def _format_position_result(data: dict, mmsi: str) -> str:
    """
    格式化船位查询结果。
    
    API返回格式: {"result":"ok","num":1,"list":{单船对象}}
    list字段是单个对象（dict），不是数组
    """
    if not data:
        return f"未找到 MMSI {mmsi} 的船位数据。"

    result_code = data.get("result", "")
    if result_code not in ("ok", "0", 0, 1):
        return f"查询失败（result: {result_code}），请检查 MMSI 或稍后重试。"

    ship = data.get("list", data.get("list", {}))
    # list可能是dict（单船）或list（多船）
    if isinstance(ship, list):
        if not ship:
            return f"未找到 MMSI {mmsi} 的实时船位数据。"
        ship = ship[0]
    elif not ship:
        return f"未找到 MMSI {mmsi} 的实时船位数据。"

    name = ship.get("n", ship.get("name", ""))
    mmsi_val = ship.get("m", ship.get("mmsi", mmsi))
    imo = ship.get("imonumber", ship.get("imo", ""))
    shiptype = ship.get("type", ship.get("shiptype", ship.get("minotype", "")))
    flag = ship.get("fn", ship.get("dn", ship.get("flag", "")))
    length = ship.get("l", ship.get("length", ""))
    width = ship.get("w", ship.get("width", ""))
    lo_raw = str(ship.get("lo", ""))
    la_raw = str(ship.get("la", ""))
    speed = ship.get("sp", ship.get("speed", ""))
    heading = ship.get("h", ship.get("heading", ""))
    course = ship.get("co", ship.get("course", ""))
    nav_status = ship.get("status", "")
    draft = ship.get("draught", ship.get("draft", ""))
    dest = ship.get("destination", "")
    eta = ship.get("eta", "")
    updatetime = ship.get("ti", ship.get("updatetime", ""))
    callsign = ship.get("callsign", "")

    # 经纬度转换
    lon, lat = _parse_lon_lat(lo_raw, la_raw)

    # 构造微信可点击链接
    wechat_url = (
        f"https://open.weixin.qq.com/connect/oauth2/authorize?"
        f"appid=wx9d402b54c1d84ebf&"
        f"redirect_uri=http://www.hifleet.com/wap-simple/index.html&"
        f"response_type=code&scope=snsapi_base&state={mmsi_val}#wechat_redirect"
    )

    lines = []
    if name:
        lines.append(name)
    lines.append(f"MMSI: {mmsi_val}" + (f" | IMO: {imo}" if imo else ""))
    if flag:
        lines.append(f"船旗: {flag}" + (f" | 船型: {shiptype}" if shiptype else ""))
    if length and width:
        lines.append(f"船舶尺寸: {length} 米 / {width} 米")
    if lon is not None and lat is not None:
        lines.append(f"实时坐标：{lon:.6f},{lat:.6f}")
    lines.append(f'<a href="{wechat_url}">点击查看</a>')
    if updatetime:
        lines.append(f"更新于: {updatetime} UTC+8")
    if nav_status:
        lines.append(f"航行状态：{nav_status}" + (f" | 吃水: {draft} 米" if draft else ""))
    lines.append(f"航速: {speed} 节" + (f" | 航首向: {heading}" if heading else ""))
    if dest:
        lines.append(f"目的港: {dest}" + (f" | ETA: {eta}" if eta else ""))
    lines.append("数据来源于 HIFLEET 全球 AIS 网络，定位可能存在延迟，仅供参考航行决策。")

    return "\n".join(lines)


def _format_archive_result(data: dict) -> str:
    """
    格式化船舶档案结果。
    
    API返回格式: {"status":"1","data":[{key,labelZh,value:[{key,value,labelZh,valueZh}]}]}
    """
    if not data:
        return "未找到船舶档案数据。"

    status = str(data.get("status", "-1"))
    if status not in ("0", "1"):
        return f"查询失败（状态码: {status}），请检查参数或稍后重试。"

    sections = data.get("data", [])
    if not sections:
        return "未找到船舶档案数据。"

    lines = []
    for section in sections:
        section_label = section.get("labelZh", section.get("labelEn", ""))
        values = section.get("value", [])
        if not values:
            continue

        if section_label:
            lines.append(f"\n【{section_label}】")

        for item in values:
            label = item.get("labelZh", item.get("labelEn", ""))
            val = item.get("valueZh", item.get("valueEn", item.get("value", "")))
            if val and label:
                lines.append(f"  {label}: {val}")

    if not lines:
        return json.dumps(data, ensure_ascii=False, indent=2)

    return "\n".join(lines)


def _format_psc_result(data: dict) -> str:
    """
    格式化PSC检查记录。
    
    API返回格式: {"status":"1","data":[{mou,port,authority,...,detail:[{code,description,ground}]}]}
    """
    if not data:
        return "未找到PSC检查记录。"

    code = data.get("code")
    if code is not None:
        try:
            if int(code) >= 4000:
                message = str(data.get("message") or data.get("msg") or "接口返回错误")
                if "unauthor" in message.lower() or "token" in message.lower():
                    return f"PSC接口授权不足，当前 token 无法访问该能力。原始错误：{message}"
                return f"PSC接口返回错误（code={code}）：{message}"
        except (TypeError, ValueError):
            pass

    status = str(data.get("status", "-1"))
    if status not in ("0", "1"):
        return f"查询失败（状态码: {status}），请检查IMO或稍后重试。"

    records = data.get("data", [])
    if not records:
        return "未找到PSC检查记录。"

    lines = [f"共 {len(records)} 条PSC检查记录：\n"]
    for i, rec in enumerate(records[:10], 1):
        lines.append(f"--- 记录 {i} ---")
        for key, label in [
            ("mou", "MOU"), ("port", "检查港口"), ("authority", "检查机构"),
            ("type_ins", "检查类型"), ("num", "缺陷数"), ("detained", "是否滞留"),
            ("date_ins", "检查日期"),
        ]:
            val = rec.get(key, "")
            if val:
                lines.append(f"  {label}: {val}")

        # 缺陷详情
        details = rec.get("detail", [])
        if details:
            lines.append(f"  缺陷详情:")
            for d in details:
                code = d.get("code", "")
                desc = d.get("description", "")
                ground = d.get("ground", "")
                line = f"    - [{code}] {desc}"
                if ground and ground != "No":
                    line += f" (ground: {ground})"
                lines.append(line)

    return "\n".join(lines)


def _format_traffic_result(data: dict) -> str:
    """
    格式化区域船舶数量结果。
    
    API返回格式: {"result":"ok","num":2189,"list":[{name,lon,lat,mmsi,...},...]}
    """
    if not data:
        return "未找到区域船舶数据。"

    result_code = str(data.get("result", ""))
    if result_code not in ("ok", "0", "1"):
        return f"查询失败（result: {result_code}），请稍后重试。"

    num = data.get("num", 0)
    ship_list = data.get("list", [])

    lines = [f"该区域当前共有 {num} 艘船舶。"]
    if ship_list:
        # 过滤有名字的船舶
        named_ships = [s for s in ship_list if s.get("name") or s.get("n")][:10]
        if named_ships:
            lines.append(f"\n显示前 {len(named_ships)} 艘：")
            for i, ship in enumerate(named_ships, 1):
                name = ship.get("name", ship.get("n", ""))
                mmsi = ship.get("mmsi", ship.get("m", ""))
                shiptype = ship.get("type", ship.get("minotype", ""))
                flag = ship.get("dn", ship.get("fn", ""))
                speed = ship.get("speed", ship.get("sp", ""))
                lines.append(f"  {i}. {name} | MMSI: {mmsi} | {shiptype} | {flag} | {speed}节")

    return "\n".join(lines)


def _format_strait_result(data: dict, is_hormuz: bool = False, strait_name: str = "") -> str:
    """
    格式化海峡通航统计结果。
    
    API返回格式:
    {"oid":24480,"zonename":"曼德海峡","startdate":"...","enddate":"...",
     "passdata":[{"passdate":"2026-06-01","passdirection":[
       {"direction":"东南","shiptypecount":[{"shiptype":"杂货船","count":1},...],
        "total":16,"ships":[...]}]}]}
    
    注意：API返回的zonename可能不准确（如OID 24471返回"霍尔姆兹海峡"），
    优先使用本地strait_name参数。
    """
    if not data:
        return "未找到海峡通航数据。"

    # 优先使用本地映射的海峡名称，API返回的zonename可能不准确
    zonename = strait_name or data.get("zonename", "")
    startdate = data.get("startdate", "")
    enddate = data.get("enddate", "")
    passdata = data.get("passdata", [])

    if not passdata:
        return "未找到海峡通航数据，请检查日期范围。"

    lines = []
    if zonename:
        lines.append(f"海峡: {zonename}")
    if startdate and enddate:
        lines.append(f"统计日期: {startdate} ~ {enddate}")
    lines.append("")

    for day_entry in passdata:
        date = day_entry.get("passdate", "")
        directions = day_entry.get("passdirection", [])

        for dir_entry in directions:
            direction = dir_entry.get("direction", "")
            total = dir_entry.get("total", 0)
            type_counts = dir_entry.get("shiptypecount", [])

            # 霍尔木兹海峡方向说明
            dir_display = direction
            if is_hormuz:
                if "东" in direction or "east" in direction.lower():
                    dir_display = f"{direction}（出湾：波斯湾→阿曼湾）"
                elif "西" in direction or "west" in direction.lower():
                    dir_display = f"{direction}（进湾：阿曼湾→波斯湾）"

            lines.append(f"日期: {date} | 方向: {dir_display} | 合计: {total} 艘")

            # 各船型数据
            for tc in type_counts:
                typename = tc.get("shiptype", "")
                count = tc.get("count", 0)
                lines.append(f"  {typename}: {count} 艘")

            lines.append("")

    return "\n".join(lines).strip()


# ============================================================
# 8个工具函数
# ============================================================

@tool
def ship_search(keyword: str) -> str:
    """按关键字搜索船舶，返回匹配船舶的MMSI、IMO、船名、船型、船旗。

    适用场景：用户给出船名、MMSI或关键字，需要获取船舶标识信息。
    此工具常作为其他查询的前置步骤（获取MMSI/IMO后再查船位/档案/PSC）。

    Args:
        keyword: 搜索关键字（船名、MMSI等），如 "YU MING"、"414726000"

    Returns:
        匹配船舶列表
    """
    t0 = time.time()
    try:
        ctx = request_context.get() or new_context(method="ship_search")
        _ensure_imports()

        logger.info(f"[ShipSearch] keyword={keyword}")
        result = _search_ship.search_ship(keyword)

        # 搜索API返回纯文本，直接透传
        if result and isinstance(result, str):
            output = result
        else:
            output = "未找到匹配的船舶。"
        _emit_result(
            "ship_search",
            ctx,
            ToolResult(status="ok", code="SHIP_SEARCH_OK", message=output, latency_ms=int((time.time() - t0) * 1000), source="hifleet_ttse"),
        )
        return output

    except Exception as e:
        logger.error(f"[ShipSearch] Error: {e}", exc_info=True)
        output = f"船舶搜索失败: {str(e)}，请稍后重试。"
        _emit_result(
            "ship_search",
            ctx if "ctx" in locals() else None,
            ToolResult(status="error", code="SHIP_SEARCH_ERROR", message=output, retriable=True, latency_ms=int((time.time() - t0) * 1000), source="hifleet_ttse"),
        )
        return output


@tool
def get_ship_position(mmsi: str) -> str:
    """查询船舶实时位置、航速、航向、航行状态等动态数据。

    适用场景：用户要查某艘船的当前位置、航速、航向等。
    需要MMSI编号，若用户仅提供船名，需先调用ship_search获取MMSI。

    Args:
        mmsi: 船舶MMSI编号，如 "414726000"

    Returns:
        船舶实时位置信息（含微信可点击链接）
    """
    t0 = time.time()
    try:
        ctx = request_context.get() or new_context(method="get_ship_position")
        _ensure_imports()

        logger.info(f"[GetPosition] mmsi={mmsi}")
        result = _get_position.get_position(mmsi)
        output = _format_position_result(result, mmsi)
        _emit_result(
            "get_ship_position",
            ctx,
            ToolResult(status="ok", code="SHIP_POSITION_OK", message=output, latency_ms=int((time.time() - t0) * 1000), source="hifleet_api"),
        )
        return output

    except Exception as e:
        logger.error(f"[GetPosition] Error: {e}", exc_info=True)
        output = f"船位查询失败: {str(e)}，请稍后重试。"
        _emit_result(
            "get_ship_position",
            ctx if "ctx" in locals() else None,
            ToolResult(status="error", code="SHIP_POSITION_ERROR", message=output, retriable=True, latency_ms=int((time.time() - t0) * 1000), source="hifleet_api"),
        )
        return output


@tool
def get_ship_archive(mmsi: str = "", imo: str = "") -> str:
    """查询船舶档案信息，包括基本参数、尺寸、载重吨、建造年份、轮机、公司等。

    适用场景：用户要查看船舶的详细参数信息。
    需要MMSI或IMO编号（二选一），若仅有船名需先调用ship_search。

    Args:
        mmsi: 船舶MMSI编号（与imo二选一），如 "414726000"
        imo: 船舶IMO编号（与mmsi二选一），如 "9613886"

    Returns:
        船舶档案详细信息
    """
    t0 = time.time()
    try:
        ctx = request_context.get() or new_context(method="get_ship_archive")
        _ensure_imports()

        if not mmsi and not imo:
            output = "请提供MMSI或IMO编号以查询船舶档案。"
            _emit_result(
                "get_ship_archive",
                ctx,
                ToolResult(status="error", code="SHIP_ARCHIVE_BAD_INPUT", message=output, retriable=False, latency_ms=int((time.time() - t0) * 1000), source="validation"),
            )
            return output

        logger.info(f"[GetArchive] mmsi={mmsi}, imo={imo}")
        result = _get_archive.get_archive(mmsi=mmsi, imo=imo)
        output = _format_archive_result(result)
        _emit_result(
            "get_ship_archive",
            ctx,
            ToolResult(status="ok", code="SHIP_ARCHIVE_OK", message=output, latency_ms=int((time.time() - t0) * 1000), source="hifleet_api"),
        )
        return output

    except Exception as e:
        logger.error(f"[GetArchive] Error: {e}", exc_info=True)
        output = f"船舶档案查询失败: {str(e)}，请稍后重试。"
        _emit_result(
            "get_ship_archive",
            ctx if "ctx" in locals() else None,
            ToolResult(status="error", code="SHIP_ARCHIVE_ERROR", message=output, retriable=True, latency_ms=int((time.time() - t0) * 1000), source="hifleet_api"),
        )
        return output


@tool
def get_psc_records(imo: str) -> str:
    """查询船舶PSC（港口国监督）检查记录和滞留情况。

    适用场景：用户要查看某艘船的PSC检查历史。
    需要IMO编号，若仅有MMSI或船名，需先通过ship_search或get_ship_archive获取IMO。

    Args:
        imo: 船舶IMO编号，如 "9613886"

    Returns:
        PSC检查记录列表
    """
    t0 = time.time()
    try:
        ctx = request_context.get() or new_context(method="get_psc_records")
        _ensure_imports()

        logger.info(f"[GetPSC] imo={imo}")
        result = _get_psc.get_psc(imo)
        output = _format_psc_result(result)
        _emit_result(
            "get_psc_records",
            ctx,
            ToolResult(status="ok", code="SHIP_PSC_OK", message=output, latency_ms=int((time.time() - t0) * 1000), source="hifleet_api"),
        )
        return output

    except Exception as e:
        logger.error(f"[GetPSC] Error: {e}", exc_info=True)
        output = f"PSC记录查询失败: {str(e)}，请稍后重试。"
        _emit_result(
            "get_psc_records",
            ctx if "ctx" in locals() else None,
            ToolResult(status="error", code="SHIP_PSC_ERROR", message=output, retriable=True, latency_ms=int((time.time() - t0) * 1000), source="hifleet_api"),
        )
        return output


@tool
def get_area_traffic(area_name: str = "", area_id: str = "", bbox: str = "") -> str:
    """查询指定区域的船舶数量和列表。

    适用场景：用户想了解某区域当前有多少艘船。
    支持三种方式：区域名称（中文/英文自动匹配）、区域ID、坐标范围(bbox)。

    Args:
        area_name: 区域名称，如 "红海"、"波斯湾"、"南海"，自动匹配内置ID
        area_id: 区域ID（与area_name/bbox三选一），如 "1"
        bbox: 矩形范围 "左经,下纬,右经,上纬"，如 "120,15,121,17"

    Returns:
        区域船舶数量和部分船舶列表
    """
    t0 = time.time()
    try:
        ctx = request_context.get() or new_context(method="get_area_traffic")
        _ensure_imports()

        # 区域名称 → areaId
        resolved_id = area_id
        if area_name and not area_id:
            resolved_id = _get_area_traffic_mod.match_area_id(area_name)
            if not resolved_id:
                available = list(_get_area_traffic_mod.AREA_ID_MAP.keys())
                cn_areas = [k for k in available if any('\u4e00' <= c <= '\u9fff' for c in k)]
                output = f"未找到区域: {area_name}\n支持的区域: {', '.join(cn_areas)}"
                _emit_result(
                    "get_area_traffic",
                    ctx,
                    ToolResult(status="error", code="AREA_TRAFFIC_BAD_INPUT", message=output, retriable=False, latency_ms=int((time.time() - t0) * 1000), source="validation"),
                )
                return output

        if not resolved_id and not bbox:
            output = "请提供区域名称、区域ID或坐标范围(bbox)。"
            _emit_result(
                "get_area_traffic",
                ctx,
                ToolResult(status="error", code="AREA_TRAFFIC_BAD_INPUT", message=output, retriable=False, latency_ms=int((time.time() - t0) * 1000), source="validation"),
            )
            return output

        logger.info(f"[GetAreaTraffic] area_id={resolved_id}, bbox={bbox}")
        result = _get_area_traffic_mod.get_area_traffic(area_id=resolved_id, bbox=bbox)
        output = _format_traffic_result(result)
        _emit_result(
            "get_area_traffic",
            ctx,
            ToolResult(status="ok", code="AREA_TRAFFIC_OK", message=output, latency_ms=int((time.time() - t0) * 1000), source="hifleet_api"),
        )
        return output

    except Exception as e:
        logger.error(f"[GetAreaTraffic] Error: {e}", exc_info=True)
        output = f"区域船舶查询失败: {str(e)}，请稍后重试。"
        _emit_result(
            "get_area_traffic",
            ctx if "ctx" in locals() else None,
            ToolResult(status="error", code="AREA_TRAFFIC_ERROR", message=output, retriable=True, latency_ms=int((time.time() - t0) * 1000), source="hifleet_api"),
        )
        return output


@tool
def get_strait_traffic(strait_name: str = "", oid: str = "",
                       startdate: str = "", enddate: str = "") -> str:
    """查询海峡通航统计数据，按方向和船型分类。

    适用场景：用户想了解某海峡的通航情况（如霍尔木兹海峡、曼德海峡等）。
    支持海峡名称自动匹配OID，需指定日期范围。

    Args:
        strait_name: 海峡名称，如 "霍尔木兹海峡"、"曼德海峡"、"苏伊士运河"，自动匹配OID
        oid: 海峡OID（与strait_name二选一），如 "24480"
        startdate: 开始日期(yyyy-MM-dd)，如 "2026-05-07"，默认今天
        enddate: 结束日期(yyyy-MM-dd)，如 "2026-05-07"，默认今天

    Returns:
        海峡通航统计数据
    """
    t0 = time.time()
    try:
        ctx = request_context.get() or new_context(method="get_strait_traffic")
        _ensure_imports()

        # 海峡名称 → OID
        resolved_oid = oid
        if strait_name and not oid:
            resolved_oid = _get_strait_traffic_mod.match_strait_oid(strait_name)
            if not resolved_oid:
                available = list(_get_strait_traffic_mod.STRAIT_OID_MAP.keys())
                cn_straits = [k for k in available if any('\u4e00' <= c <= '\u9fff' for c in k)]
                output = f"未找到海峡: {strait_name}\n支持的海峡: {', '.join(cn_straits)}"
                _emit_result(
                    "get_strait_traffic",
                    ctx,
                    ToolResult(status="error", code="STRAIT_TRAFFIC_BAD_INPUT", message=output, retriable=False, latency_ms=int((time.time() - t0) * 1000), source="validation"),
                )
                return output

        if not resolved_oid:
            output = "请提供海峡名称或OID。"
            _emit_result(
                "get_strait_traffic",
                ctx,
                ToolResult(status="error", code="STRAIT_TRAFFIC_BAD_INPUT", message=output, retriable=False, latency_ms=int((time.time() - t0) * 1000), source="validation"),
            )
            return output

        # 日期默认值：今天
        if not startdate or not enddate:
            today = datetime.now().strftime("%Y-%m-%d")
            startdate = startdate or today
            enddate = enddate or today

        # 判断是否为霍尔木兹海峡
        is_hormuz = _get_strait_traffic_mod.is_hormuz(resolved_oid)

        # 获取本地映射的海峡中文名（API返回的zonename可能不准确）
        local_strait_name = strait_name
        if not local_strait_name:
            for cn_name, oid_val in _get_strait_traffic_mod.STRAIT_OID_MAP.items():
                if oid_val == resolved_oid and any('\u4e00' <= c <= '\u9fff' for c in cn_name):
                    local_strait_name = cn_name
                    break

        logger.info(f"[GetStraitTraffic] oid={resolved_oid}, date={startdate}~{enddate}, hormuz={is_hormuz}")
        result = _get_strait_traffic_mod.get_strait_traffic(resolved_oid, startdate, enddate)
        output = _format_strait_result(result, is_hormuz=is_hormuz, strait_name=local_strait_name)
        _emit_result(
            "get_strait_traffic",
            ctx,
            ToolResult(status="ok", code="STRAIT_TRAFFIC_OK", message=output, latency_ms=int((time.time() - t0) * 1000), source="hifleet_api"),
        )
        return output

    except Exception as e:
        logger.error(f"[GetStraitTraffic] Error: {e}", exc_info=True)
        output = f"海峡通航查询失败: {str(e)}，请稍后重试。"
        _emit_result(
            "get_strait_traffic",
            ctx if "ctx" in locals() else None,
            ToolResult(status="error", code="STRAIT_TRAFFIC_ERROR", message=output, retriable=True, latency_ms=int((time.time() - t0) * 1000), source="hifleet_api"),
        )
        return output


@tool
def get_ship_trajectory(mmsi: str, starttime: str = "", endtime: str = "", zoom: str = "7") -> str:
    """查询单船历史轨迹点。

    Args:
        mmsi: 船舶MMSI编号
        starttime: 开始时间，yyyy-MM-dd 或 yyyy-MM-dd HH:mm:ss
        endtime: 结束时间，yyyy-MM-dd 或 yyyy-MM-dd HH:mm:ss
        zoom: 轨迹压缩级别，默认7
    """
    t0 = time.time()
    ctx = request_context.get() or new_context(method="get_ship_trajectory")
    try:
        if not mmsi:
            output = "请提供MMSI以查询历史轨迹。"
            _emit_result("get_ship_trajectory", ctx, ToolResult(status="error", code="TRAJECTORY_BAD_INPUT", message=output, retriable=False, latency_ms=int((time.time() - t0) * 1000), source="validation"))
            return output
        if starttime and len(starttime) == 10:
            starttime += " 00:00:00"
        if endtime and len(endtime) == 10:
            endtime += " 23:59:59"
        data = _http_json("GET", "/position/trajectory/token", {"mmsi": mmsi, "starttime": starttime, "endtime": endtime, "zoom": zoom or "7"})
        output = _format_json_result(data, "历史轨迹")
        _emit_result("get_ship_trajectory", ctx, ToolResult(status="ok", code="TRAJECTORY_OK", message=output, latency_ms=int((time.time() - t0) * 1000), source="hifleet_api"))
        return output
    except Exception as e:
        output = f"历史轨迹查询失败: {str(e)}，请稍后重试。"
        _emit_result("get_ship_trajectory", ctx, ToolResult(status="error", code="TRAJECTORY_ERROR", message=output, retriable=True, latency_ms=int((time.time() - t0) * 1000), source="hifleet_api"))
        return output


@tool
def get_ship_call_ports(mmsi: str, starttime: str = "", endtime: str = "", accuracyval: str = "6") -> str:
    """查询单船历史挂靠记录。"""
    t0 = time.time()
    ctx = request_context.get() or new_context(method="get_ship_call_ports")
    try:
        if not mmsi:
            output = "请提供MMSI以查询历史挂靠。"
            _emit_result("get_ship_call_ports", ctx, ToolResult(status="error", code="CALL_PORTS_BAD_INPUT", message=output, retriable=False, latency_ms=int((time.time() - t0) * 1000), source="validation"))
            return output
        if starttime and len(starttime) == 10:
            starttime += " 00:00:00"
        if endtime and len(endtime) == 10:
            endtime += " 23:59:59"
        data = _http_json("GET", "/position/getcallport/token", {"mmsi": mmsi, "starttime": starttime, "endtime": endtime, "accuracyval": accuracyval or "6"})
        output = _format_json_result(data, "历史挂靠")
        _emit_result("get_ship_call_ports", ctx, ToolResult(status="ok", code="CALL_PORTS_OK", message=output, latency_ms=int((time.time() - t0) * 1000), source="hifleet_api"))
        return output
    except Exception as e:
        output = f"历史挂靠查询失败: {str(e)}，请稍后重试。"
        _emit_result("get_ship_call_ports", ctx, ToolResult(status="error", code="CALL_PORTS_ERROR", message=output, retriable=True, latency_ms=int((time.time() - t0) * 1000), source="hifleet_api"))
        return output


@tool
def get_ship_voyages(mmsi: str, starttime: str = "", endtime: str = "") -> str:
    """查询单船历史航次。未提供时间范围时使用简版航次接口。"""
    t0 = time.time()
    ctx = request_context.get() or new_context(method="get_ship_voyages")
    try:
        if not mmsi:
            output = "请提供MMSI以查询历史航次。"
            _emit_result("get_ship_voyages", ctx, ToolResult(status="error", code="VOYAGES_BAD_INPUT", message=output, retriable=False, latency_ms=int((time.time() - t0) * 1000), source="validation"))
            return output
        if starttime and endtime:
            if len(starttime) == 10:
                starttime += " 00:00:00"
            if len(endtime) == 10:
                endtime += " 23:59:59"
            data = _http_json("GET", "/portofcall/getvoyages", {"mmsi": mmsi, "starttime": starttime, "endtime": endtime})
        else:
            data = _http_json("GET", "/position/getvoyagelist/token", {"mmsi": mmsi})
        output = _format_json_result(data, "历史航次")
        _emit_result("get_ship_voyages", ctx, ToolResult(status="ok", code="VOYAGES_OK", message=output, latency_ms=int((time.time() - t0) * 1000), source="hifleet_api"))
        return output
    except Exception as e:
        output = f"历史航次查询失败: {str(e)}，请稍后重试。"
        _emit_result("get_ship_voyages", ctx, ToolResult(status="error", code="VOYAGES_ERROR", message=output, retriable=True, latency_ms=int((time.time() - t0) * 1000), source="hifleet_api"))
        return output


@tool
def get_last_departure(mmsi: str) -> str:
    """查询船舶最近一次离港港口与离港时间。"""
    t0 = time.time()
    ctx = request_context.get() or new_context(method="get_last_departure")
    try:
        if not mmsi:
            output = "请提供MMSI以查询上一离港。"
            _emit_result("get_last_departure", ctx, ToolResult(status="error", code="LAST_DEPARTURE_BAD_INPUT", message=output, retriable=False, latency_ms=int((time.time() - t0) * 1000), source="validation"))
            return output
        data = _http_json("GET", "/position/lastdeparture/token", {"mmsi": mmsi})
        output = _format_json_result(data, "上一离港")
        _emit_result("get_last_departure", ctx, ToolResult(status="ok", code="LAST_DEPARTURE_OK", message=output, latency_ms=int((time.time() - t0) * 1000), source="hifleet_api"))
        return output
    except Exception as e:
        output = f"上一离港查询失败: {str(e)}，请稍后重试。"
        _emit_result("get_last_departure", ctx, ToolResult(status="error", code="LAST_DEPARTURE_ERROR", message=output, retriable=True, latency_ms=int((time.time() - t0) * 1000), source="hifleet_api"))
        return output


@tool
def get_current_stop(mmsi: str) -> str:
    """查询船舶当前停船/到港位置与停船时长。"""
    t0 = time.time()
    ctx = request_context.get() or new_context(method="get_current_stop")
    try:
        if not mmsi:
            output = "请提供MMSI以查询当前停船。"
            _emit_result("get_current_stop", ctx, ToolResult(status="error", code="CURRENT_STOP_BAD_INPUT", message=output, retriable=False, latency_ms=int((time.time() - t0) * 1000), source="validation"))
            return output
        data = _http_json("GET", "/position/getstop/token", {"mmsi": mmsi})
        output = _format_json_result(data, "当前停船")
        _emit_result("get_current_stop", ctx, ToolResult(status="ok", code="CURRENT_STOP_OK", message=output, latency_ms=int((time.time() - t0) * 1000), source="hifleet_api"))
        return output
    except Exception as e:
        output = f"当前停船查询失败: {str(e)}，请稍后重试。"
        _emit_result("get_current_stop", ctx, ToolResult(status="error", code="CURRENT_STOP_ERROR", message=output, retriable=True, latency_ms=int((time.time() - t0) * 1000), source="hifleet_api"))
        return output


@tool
def get_avoid_redsea_traffic(startdate: str = "", enddate: str = "", i18n: str = "zh") -> str:
    """查询红海绕航每日统计。"""
    t0 = time.time()
    ctx = request_context.get() or new_context(method="get_avoid_redsea_traffic")
    try:
        today = datetime.now().strftime("%Y-%m-%d")
        data = _http_json("POST", "/routerisk/getAvoidRedSeaDetail/token", {"starttime": startdate or today, "endtime": enddate or startdate or today, "i18n": i18n or "zh"})
        output = _format_json_result(data, "红海绕航")
        _emit_result("get_avoid_redsea_traffic", ctx, ToolResult(status="ok", code="AVOID_REDSEA_OK", message=output, latency_ms=int((time.time() - t0) * 1000), source="hifleet_api"))
        return output
    except Exception as e:
        output = f"红海绕航统计查询失败: {str(e)}，请稍后重试。"
        _emit_result("get_avoid_redsea_traffic", ctx, ToolResult(status="error", code="AVOID_REDSEA_ERROR", message=output, retriable=True, latency_ms=int((time.time() - t0) * 1000), source="hifleet_api"))
        return output


@tool
def search_ports(port_name: str = "", port_code: str = "") -> str:
    """检索港口列表。详情查询需使用返回项中的 piuid 作为 port_id。"""
    t0 = time.time()
    ctx = request_context.get() or new_context(method="search_ports")
    try:
        data = _http_json("GET", "/portguide/getPort/token", {"portName": port_name, "portCode": port_code})
        output = _format_json_result(data, "港口列表")
        _emit_result("search_ports", ctx, ToolResult(status="ok", code="PORT_SEARCH_OK", message=output, latency_ms=int((time.time() - t0) * 1000), source="hifleet_api"))
        return output
    except Exception as e:
        output = f"港口检索失败: {str(e)}。该接口可能缺少 portguide 授权。"
        _emit_result("search_ports", ctx, ToolResult(status="error", code="PORT_SEARCH_ERROR", message=output, retriable=False, latency_ms=int((time.time() - t0) * 1000), source="hifleet_api"))
        return output


@tool
def get_port_detail(port_id: str) -> str:
    """查询单港详情，port_id 应来自 search_ports 返回项 piuid。"""
    t0 = time.time()
    ctx = request_context.get() or new_context(method="get_port_detail")
    try:
        if not str(port_id).strip().isdigit():
            output = "请提供 search_ports 返回的 piuid 作为 port_id。"
            _emit_result("get_port_detail", ctx, ToolResult(status="error", code="PORT_DETAIL_BAD_INPUT", message=output, retriable=False, latency_ms=int((time.time() - t0) * 1000), source="validation"))
            return output
        data = _http_json("GET", "/portguide/getPortDetail/token", {"portId": str(port_id).strip()})
        output = _format_json_result(data, "港口详情")
        _emit_result("get_port_detail", ctx, ToolResult(status="ok", code="PORT_DETAIL_OK", message=output, latency_ms=int((time.time() - t0) * 1000), source="hifleet_api"))
        return output
    except Exception as e:
        output = f"港口详情查询失败: {str(e)}。该接口可能缺少 portguide 授权。"
        _emit_result("get_port_detail", ctx, ToolResult(status="error", code="PORT_DETAIL_ERROR", message=output, retriable=False, latency_ms=int((time.time() - t0) * 1000), source="hifleet_api"))
        return output


# AIS 航行状态 — API直接接收中文文本
# 有效值：在航 失控 帆船在航 搁浅 操纵能力受限 机动船在航 系泊 锚泊 停泊 未知 未定义 正在捕鱼作业 限于吃水 高速船留用 地效翼船留用 待定义
# 以下是中文别名的映射（用户可能使用的说法 → API标准值）
NAV_STATUS_ALIASES = {
    "航行中": "在航", "航行": "在航",
    "抛锚": "锚泊", "锚": "锚泊",
    "未在指挥": "失控",
    "受限操纵": "操纵能力受限", "操纵受限": "操纵能力受限", "操作受限": "操纵能力受限",
    "吃水受限": "限于吃水", "吃水受限制": "限于吃水",
    "靠泊": "系泊", "停泊": "系泊",
    "从事捕鱼": "正在捕鱼作业", "渔船作业": "正在捕鱼作业", "捕鱼": "正在捕鱼作业",
    "从事航行": "在航",
    "机动船在航": "机动船在航",
}


@tool
def upload_ship_position(mmsi: str, lon: str = "", lat: str = "",
                         speed: str = "", heading: str = "",
                         course: str = "", destination: str = "",
                         eta: str = "", draft: str = "", updatetime: str = "",
                         navstatus: str = "", ship_name: str = "",
                         wechatgroup: str = "") -> str:
    """上传/更新船舶动态位置数据（经纬度、航速、航向等）。

    适用场景：用户要更新某艘船的位置、航速等动态信息。
    仅当本轮用户输入或附件明确提供目标船舶、经纬度、更新时间时执行。经纬度支持度分秒格式（如30°08′51N），工具内部自动转换。
    若仅有船名需先调用ship_search获取MMSI；禁止复用历史船舶标识直接写入。

    Args:
        mmsi: 船舶MMSI编号
        lon: 经度（十进制度或度分秒格式，如 "123.227" 或 "123°13′38E"）
        lat: 纬度（十进制度或度分秒格式，如 "30.147" 或 "30°08′51N"）
        speed: 航速（节），如 "5"
        heading: 航首向（度），如 "351"
        course: 航迹向（度），如 "180"
        destination: 目的港，如 "SHANGHAI"
        eta: 预抵时间，如 "2026-05-10 20:00:00"
        draft: 吃水（米），如 "9.6"
        updatetime: 更新时间（用户指定的船位时间），如 "2026-06-05 15:30:00"。必须由用户或附件明确提供，工具不自动生成
        navstatus: 航行状态（中文），如 "机动船在航"、"锚泊"、"系泊"、"搁浅"、"捕鱼"等，API直接接收中文文本
        ship_name: 船名（英文），如 "YU MING"。若用户提供了船名则传入
        wechatgroup: 微信群组ID，用于绑定船队

    Returns:
        上传结果
    """
    t0 = time.time()
    try:
        ctx = request_context.get() or new_context(method="upload_ship_position")
        _ensure_imports()

        def _validation_error(output: str) -> str:
            _emit_result(
                "upload_ship_position",
                ctx,
                ToolResult(status="error", code="UPLOAD_POSITION_BAD_INPUT", message=output, retriable=False, latency_ms=int((time.time() - t0) * 1000), source="validation"),
            )
            return output

        missing_required = []
        if not mmsi or not mmsi.strip():
            missing_required.append("船舶标识（MMSI）")
        if not lon or not str(lon).strip():
            missing_required.append("经度")
        if not lat or not str(lat).strip():
            missing_required.append("纬度")
        if not updatetime or not str(updatetime).strip():
            missing_required.append("更新时间")
        if missing_required:
            return _validation_error(
                "船位更新缺少必填参数：" + "、".join(missing_required) + "。请补充后再更新；我不会复用历史船舶或自动生成更新时间。"
            )

        # 构造请求体 - 包含必要字段
        data = {
            "name": ship_name or mmsi,  # API必选：优先用船名，缺省用MMSI
            "mmsi": mmsi,           # API 需要单独的 mmsi 字段
            "updatetime": updatetime.strip(),
            "checkFly": "0",       # 跳过飞行位置检查
            "bindCheck": "0"       # 跳过群组绑定检查
        }

        # 经纬度转换（度分秒 → 十进制度）
        if lon:
            converted_lon = _coord_utils.dms_to_decimal(lon)
            if converted_lon is not None:
                data["lon"] = converted_lon
            else:
                output = f"经度格式无法识别: {lon}，请使用十进制度（如123.227）或度分秒（如123°13′38E）格式。"
                return _validation_error(output)

        if lat:
            converted_lat = _coord_utils.dms_to_decimal(lat)
            if converted_lat is not None:
                data["lat"] = converted_lat
            else:
                output = f"纬度格式无法识别: {lat}，请使用十进制度（如30.147）或度分秒（如30°08′51N）格式。"
                return _validation_error(output)

        if speed:
            try:
                data["speed"] = float(speed)
            except ValueError:
                output = f"航速格式错误: {speed}，请提供数字。"
                return _validation_error(output)
        if heading:
            try:
                data["heading"] = float(heading)
            except ValueError:
                return _validation_error(f"船首向格式错误: {heading}，请提供数字。")
        if course:
            try:
                data["course"] = float(course)
            except ValueError:
                pass
        if destination:
            data["destination"] = destination
        if eta:
            data["eta"] = eta
        if draft:
            try:
                data["draught"] = float(draft)  # API文档字段名为 draught
            except ValueError:
                return _validation_error(f"吃水格式错误: {draft}，请提供数字。")
        # 航行状态：API直接接收中文文本（如"机动船在航"、"锚泊"等）
        if navstatus:
            navstatus_stripped = navstatus.strip()
            # 先检查是否是API标准值
            api_valid_values = ["在航", "失控", "帆船在航", "搁浅", "操纵能力受限", "机动船在航",
                                "系泊", "锚泊", "停泊", "未知", "未定义", "正在捕鱼作业",
                                "限于吃水", "高速船留用", "地效翼船留用", "待定义"]
            if navstatus_stripped in api_valid_values:
                data["status"] = navstatus_stripped
            elif navstatus_stripped in NAV_STATUS_ALIASES:
                data["status"] = NAV_STATUS_ALIASES[navstatus_stripped]
            else:
                # 非标准值也直接传入，让API自行校验
                data["status"] = navstatus_stripped
        # 船名（若有）
        if ship_name:
            data["name"] = ship_name
        # 微信群组
        if wechatgroup:
            data["wechatgroup"] = wechatgroup

        update_fields = [k for k in data if k not in ("name", "mmsi", "updatetime", "checkFly", "bindCheck")]

        logger.info(f"[UploadPosition] mmsi={mmsi}, fields={update_fields}")

        # 尝试带usertoken上传（API文档标注无需认证，但实际需要账号绑定）
        # 优先使用API_KEY，若失败再尝试TTSE_KEY
        result = _upload_position.upload_position(data)

        # 如果返回"群组未绑定账号"，尝试带token重试
        if isinstance(result, str) and "群组未绑定账号" in result:
            logger.info("[UploadPosition] Retrying with usertoken...")
            ttse_key = _ttse_key()
            for key_name, key_val in [("TTSE_KEY", ttse_key)]:
                if not key_val:
                    continue
                try:
                    result = _upload_position.upload_position(data, usertoken=key_val)
                    if isinstance(result, str) and "群组未绑定账号" not in result:
                        logger.info(f"[UploadPosition] Success with {key_name}")
                        break
                except Exception:
                    continue

        # 上传API返回纯文本
        if isinstance(result, str):
            # 判断是否成功
            if "成功" in result or "更新成功" in result:
                wechat_url = _wechat_ship_url(mmsi)
                lines = [
                    f"船位更新成功！",
                    f"MMSI: {mmsi}",
                    f'<a href="{wechat_url}">点击查看</a>',
                    f"更新参数:",
                ]
                if "lon" in data:
                    lines.append(f"经度: {data['lon']}")
                if "lat" in data:
                    lines.append(f"纬度: {data['lat']}")
                if "speed" in data:
                    lines.append(f"航速: {data['speed']} 节")
                if "heading" in data:
                    lines.append(f"航首向: {data['heading']}")
                if "destination" in data:
                    lines.append(f"目的港: {data['destination']}")
                if "eta" in data:
                    lines.append(f"ETA: {data['eta']}")
                if "draught" in data:
                    lines.append(f"吃水: {data['draught']} 米")
                if "status" in data:
                    # status已经是中文文本，直接显示
                    lines.append(f"航行状态: {data['status']}")
                lines.append(f"更新时间：{data['updatetime']} (UTC+8)")
                optional_missing = []
                if "speed" not in data:
                    optional_missing.append("航速")
                if "heading" not in data:
                    optional_missing.append("船首向")
                if "draught" not in data:
                    optional_missing.append("吃水")
                if "status" not in data:
                    optional_missing.append("航行状态")
                if optional_missing:
                    lines.append("本次未更新" + "、".join(optional_missing) + "等字段，如需同步可继续补充。")
                lines.append("数据同步：预计 5 分钟内生效")
                output = "\n".join(lines)
                _emit_result(
                    "upload_ship_position",
                    ctx,
                    ToolResult(status="ok", code="UPLOAD_POSITION_OK", message=output, latency_ms=int((time.time() - t0) * 1000), source="hifleet_ttse"),
                )
                return output
            else:
                # 返回原始错误信息
                output = result
                _emit_result(
                    "upload_ship_position",
                    ctx,
                    ToolResult(status="error", code="UPLOAD_POSITION_FAILED", message=output, retriable=True, latency_ms=int((time.time() - t0) * 1000), source="hifleet_ttse"),
                )
                return output

        output = str(result)
        _emit_result(
            "upload_ship_position",
            ctx,
            ToolResult(status="partial", code="UPLOAD_POSITION_UNKNOWN", message=output, retriable=False, latency_ms=int((time.time() - t0) * 1000), source="hifleet_ttse"),
        )
        return output

    except Exception as e:
        logger.error(f"[UploadPosition] Error: {e}", exc_info=True)
        output = f"船位上传失败: {str(e)}，请稍后重试。"
        _emit_result(
            "upload_ship_position",
            ctx if "ctx" in locals() else None,
            ToolResult(status="error", code="UPLOAD_POSITION_ERROR", message=output, retriable=True, latency_ms=int((time.time() - t0) * 1000), source="hifleet_ttse"),
        )
        return output


@tool
def update_ship_static_info(mmsi: str, ship_name: str = "", imo: str = "",
                            ship_type: str = "", minotype: str = "",
                            length: str = "", width: str = "", dwt: str = "",
                            flag: str = "", callsign: str = "",
                            built_year: str = "", destination: str = "",
                            eta: str = "", draft: str = "", wechatgroup: str = "") -> str:
    """更新船舶静态信息（船名、船型、尺寸、载重吨等）。

    适用场景：用户要更新船舶的基本信息。
    直接执行无需确认。仅更新用户提供的参数，不设置默认值。
    若仅有船名需先调用ship_search获取MMSI。

    Args:
        mmsi: 船舶MMSI编号
        ship_name: 船名
        imo: IMO编号
        ship_type: 船舶类型
        minotype: MINO船型代码
        length: 船长（米）
        width: 船宽（米）
        dwt: 载重吨
        flag: 船旗国
        callsign: 呼号
        built_year: 建造年份
        destination: 目的港
        eta: 预抵时间(yyyy-MM-dd HH:mm:ss)
        draft: 吃水（米）
        wechatgroup: 微信群组绑定

    Returns:
        更新结果
    """
    t0 = time.time()
    try:
        ctx = request_context.get() or new_context(method="update_ship_static_info")
        _ensure_imports()

        synced_ship_type, synced_minotype, ship_type_error = _sync_static_ship_type_fields(ship_type, minotype)
        if ship_type_error:
            _emit_result(
                "update_ship_static_info",
                ctx,
                ToolResult(status="error", code="UPDATE_STATIC_BAD_INPUT", message=ship_type_error, retriable=False, latency_ms=int((time.time() - t0) * 1000), source="hifleet_ttse"),
            )
            return ship_type_error

        # 构造请求体 - 静态信息更新只需要 mmsi + bindCheck
        data = {
            "mmsi": mmsi,
            "bindCheck": "0"       # 跳过群组绑定检查
        }
        # 注意：不传 "name": mmsi（name是船名，不应填MMSI）
        # 注意：不传 "checkFly"（这是船位上传接口的字段，静态更新不需要）

        # 字段名映射：工具参数名 → API实际字段名
        str_field_map = {
            "name": ship_name,       # ship_name → name
            "imonumber": imo,        # imo → imonumber
            "type": synced_ship_type,       # ship_type → type
            "minotype": synced_minotype,    # minotype 与API一致；船型更新时与type同步
            "flag": flag,
            "callsign": callsign,
            "destination": destination,
            "eta": eta,
            "wechatgroup": wechatgroup,  # wechatgroup 与API一致
        }
        for api_key, val in str_field_map.items():
            if val:
                data[api_key] = val

        # 数值字段（部分需要映射API字段名）
        num_field_map = {
            "length": length, "width": width, "dwt": dwt,
            "buildyear": built_year,   # built_year → buildyear
            "draught": draft,         # draft → draught
        }
        for api_key, val in num_field_map.items():
            if val:
                try:
                    if api_key in ("length", "width", "draught"):
                        data[api_key] = float(val)
                    else:
                        data[api_key] = int(val)
                except ValueError:
                    pass  # 跳过无效数值

        # 检查是否有实质性更新数据
        update_fields = [k for k in data if k not in ("mmsi", "bindCheck")]
        if not update_fields:
            output = "未提供任何可更新的数据，请至少提供一个更新字段。"
            _emit_result(
                "update_ship_static_info",
                ctx,
                ToolResult(status="error", code="UPDATE_STATIC_BAD_INPUT", message=output, retriable=False, latency_ms=int((time.time() - t0) * 1000), source="validation"),
            )
            return output

        logger.info(f"[UpdateStatic] mmsi={mmsi}, fields={update_fields}")
        result = _update_static_info.update_static_info(data)

        # 如果返回"群组未绑定账号"，尝试带token重试
        if isinstance(result, str) and "群组未绑定账号" in result:
            logger.info("[UpdateStatic] Retrying with usertoken...")
            ttse_key = _ttse_key()
            for key_name, key_val in [("TTSE_KEY", ttse_key)]:
                if not key_val:
                    continue
                try:
                    result = _update_static_info.update_static_info(data, usertoken=key_val)
                    if isinstance(result, str) and "群组未绑定账号" not in result:
                        logger.info(f"[UpdateStatic] Success with {key_name}")
                        break
                except Exception:
                    continue

        # 处理返回结果（可能是纯文本或JSON）
        if isinstance(result, str):
            # 尝试解析JSON
            try:
                parsed = json.loads(result)
                if "error" in parsed:
                    output = f"静态信息更新失败: {parsed.get('detail', parsed.get('error', ''))}"
                    _emit_result(
                        "update_ship_static_info",
                        ctx,
                        ToolResult(status="error", code="UPDATE_STATIC_FAILED", message=output, retriable=True, latency_ms=int((time.time() - t0) * 1000), source="hifleet_ttse"),
                    )
                    return output
                status = str(parsed.get("status", ""))
                if status in ("0", "1"):
                    output = _format_static_update_success(mmsi, data)
                    _emit_result(
                        "update_ship_static_info",
                        ctx,
                        ToolResult(status="ok", code="UPDATE_STATIC_OK", message=output, latency_ms=int((time.time() - t0) * 1000), source="hifleet_ttse"),
                    )
                    return output
                output = f"静态信息更新结果: {result}"
                _emit_result(
                    "update_ship_static_info",
                    ctx,
                    ToolResult(status="partial", code="UPDATE_STATIC_UNKNOWN", message=output, retriable=False, latency_ms=int((time.time() - t0) * 1000), source="hifleet_ttse"),
                )
                return output
            except json.JSONDecodeError:
                # 纯文本响应
                if "成功" in result:
                    output = _format_static_update_success(mmsi, data)
                    _emit_result(
                        "update_ship_static_info",
                        ctx,
                        ToolResult(status="ok", code="UPDATE_STATIC_OK", message=output, latency_ms=int((time.time() - t0) * 1000), source="hifleet_ttse"),
                    )
                    return output
                output = result
                _emit_result(
                    "update_ship_static_info",
                    ctx,
                    ToolResult(status="error", code="UPDATE_STATIC_FAILED", message=output, retriable=True, latency_ms=int((time.time() - t0) * 1000), source="hifleet_ttse"),
                )
                return output

        output = str(result)
        _emit_result(
            "update_ship_static_info",
            ctx,
            ToolResult(status="partial", code="UPDATE_STATIC_UNKNOWN", message=output, retriable=False, latency_ms=int((time.time() - t0) * 1000), source="hifleet_ttse"),
        )
        return output

    except Exception as e:
        logger.error(f"[UpdateStatic] Error: {e}", exc_info=True)
        output = f"静态信息更新失败: {str(e)}，请稍后重试。"
        _emit_result(
            "update_ship_static_info",
            ctx if "ctx" in locals() else None,
            ToolResult(status="error", code="UPDATE_STATIC_ERROR", message=output, retriable=True, latency_ms=int((time.time() - t0) * 1000), source="hifleet_ttse"),
        )
        return output


def get_ship_service_tools():
    """返回船舶服务技能的工具列表"""
    return [
        ship_search,
        get_ship_position,
        get_ship_archive,
        get_psc_records,
        get_area_traffic,
        get_strait_traffic,
        upload_ship_position,
        update_ship_static_info,
    ]
