#!/usr/bin/env python3
"""船位上传：上报船位数据（经纬度/航速/航向等）

使用方式：
    python upload_position.py --mmsi 414726000 --lon 116.875 --lat 22.125 --speed 15

环境变量：
    HIFLEET_TTSE_BASE - ttseapi 基址（可选，默认 http://ttseapi.hifleet.com）

注意：
    - 经纬度必须为十进制度格式（东经/北纬为正，西经/南纬为负）
    - 度分秒格式需先通过 coord_utils.py 转换
    - 仅上传用户明确提供的参数，不设置默认值（updatetime 除外）
    - API返回纯文本，非JSON
"""
import os
import sys
import json
import urllib.request
import urllib.parse
from datetime import datetime


def upload_position(data: dict, usertoken: str = "") -> str:
    """上传船位数据。

    Args:
        data: 船位数据字典，必须包含 name/mmsi 和至少一个动态参数
        usertoken: 可选的认证token（API文档标注无需认证，但某些群组需绑定账号）

    Returns:
        API 原始响应文本（纯文本格式，非JSON）
    """
    base = os.getenv("HIFLEET_TTSE_BASE", "http://ttseapi.hifleet.com")
    
    # 构造URL（可选带usertoken）
    if usertoken:
        url = f"{base}/position/updateShipAisInfo?usertoken={urllib.parse.quote(usertoken, safe='')}"
    else:
        url = f"{base}/position/updateShipAisInfo"
    
    body = json.dumps(data).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST",
                                headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return resp.read().decode()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="船位上传")
    parser.add_argument("--mmsi", required=True, help="船舶 MMSI 编号")
    parser.add_argument("--lon", type=float, default=None, help="经度（十进制度）")
    parser.add_argument("--lat", type=float, default=None, help="纬度（十进制度）")
    parser.add_argument("--speed", type=float, default=None, help="航速（节）")
    parser.add_argument("--heading", type=float, default=None, help="航首向（度）")
    parser.add_argument("--course", type=float, default=None, help="航迹向（度）")
    parser.add_argument("--destination", default=None, help="目的港")
    parser.add_argument("--eta", default=None, help="预抵时间（yyyy-MM-dd HH:mm:ss）")
    parser.add_argument("--draft", type=float, default=None, help="吃水（米）")
    parser.add_argument("--updatetime", default=None, help="更新时间（yyyy-MM-dd HH:mm:ss），若用户指定则使用指定时间")
    args = parser.parse_args()

    # 构造请求体 - 包含必要字段
    # 优先使用用户指定的更新时间，否则使用当前时间
    actual_update_time = args.updatetime if args.updatetime else datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    data = {
        "name": args.mmsi,
        "mmsi": args.mmsi,       # API 需要单独的 mmsi 字段
        "updatetime": actual_update_time,
        "checkFly": "0",         # 跳过飞行位置检查
        "bindCheck": "0"         # 跳过群组绑定检查
    }

    if args.lon is not None:
        data["lon"] = args.lon
    if args.lat is not None:
        data["lat"] = args.lat
    if args.speed is not None:
        data["speed"] = args.speed
    if args.heading is not None:
        data["heading"] = args.heading
    if args.course is not None:
        data["course"] = args.course
    if args.destination is not None:
        data["destination"] = args.destination
    if args.eta is not None:
        data["eta"] = args.eta
    if args.draft is not None:
        data["draught"] = args.draft  # API 字段名为 draught

    # 检查是否有实质性更新数据
    update_fields = [k for k in data if k not in ("name", "mmsi", "updatetime", "checkFly", "bindCheck")]
    if not update_fields:
        print(json.dumps({"error": "未提供任何可更新的数据，请至少提供经纬度/航速/航向等参数"}, ensure_ascii=False, indent=2))
        sys.exit(1)

    result = upload_position(data)
    print(result)
