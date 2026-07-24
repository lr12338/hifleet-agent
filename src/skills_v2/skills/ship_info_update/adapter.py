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
from datetime import datetime, timedelta

from langchain.tools import tool
from coze_coding_utils.log.write_log import request_context
from coze_coding_utils.runtime_ctx.context import new_context
from skills_v2.common.tool_result import ToolResult, emit_tool_metric

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


def _normalize_optional_eta_value(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    cleaned = re.sub(r"\([^)]*\)", "", raw).strip()
    cleaned = re.sub(r"（[^）]*）", "", cleaned).strip()
    cleaned = cleaned.replace("/", "-").replace("T", " ").replace("：", ":")
    cleaned = re.sub(r"\s+", " ", cleaned)
    match = re.search(r"(20\d{2})-(\d{1,2})-(\d{1,2})(?:\s+(\d{1,2})(?::?(\d{2})(?::(\d{1,2}))?)?)?", cleaned)
    if not match:
        return ""
    hour = int(match.group(4) or 0)
    minute = int(match.group(5) or 0)
    second = int(match.group(6) or 0)
    return f"{int(match.group(1)):04d}-{int(match.group(2)):02d}-{int(match.group(3)):02d} {hour:02d}:{minute:02d}:{second:02d}"


_SCRIPTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scripts")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)


_upload_position = None
_update_static_info = None
_coord_utils = None


def _ensure_imports():
    """Lazily import the write scripts bundled with this ship_info_update Skill."""
    global _upload_position, _update_static_info, _coord_utils

    if _upload_position is not None:
        return

    import upload_position
    import update_static_info
    import coord_utils

    _upload_position = upload_position
    _update_static_info = update_static_info
    _coord_utils = coord_utils


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
            normalized_eta = _normalize_optional_eta_value(eta)
            if normalized_eta:
                data["eta"] = normalized_eta
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
            "eta": _normalize_optional_eta_value(eta),
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





def get_ship_update_tools():
    """Return the write tools owned by this V2 ship_info_update Skill."""
    return [upload_ship_position, update_ship_static_info]


__all__ = ["upload_ship_position", "update_ship_static_info", "get_ship_update_tools"]
