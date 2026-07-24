#!/usr/bin/env python3
"""船位查询：按 MMSI 查询实时位置、航速、航向

使用方式：
    python get_position.py --mmsi 414726000

环境变量：
    HIFLEET_API_KEY - api.hifleet.com 通用 Token（必填）
    HIFLEET_API_BASE - API 基址（可选，默认 https://api.hifleet.com）

注意：
    - 返回JSON，格式: {"result":"ok","num":1,"list":{...}}
    - list 字段是单个对象（dict），不是数组
    - 经纬度为度分格式（如 la="1824.2845" 表示 18°24.2845'N）
"""
import os
import json
import urllib.request
import urllib.parse

from auth import public_api_key


def get_position(mmsi: str) -> dict:
    """按 MMSI 查询船舶实时位置。

    Args:
        mmsi: 船舶 MMSI 编号

    Returns:
        API 原始响应字典
    """
    base = os.getenv("HIFLEET_API_BASE", "https://api.hifleet.com")
    key = public_api_key()
    params = {"mmsi": mmsi}
    if key:
        params["api_key"] = key
        params["usertoken"] = key
    url = f"{base}/position/position/get/token?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(url, timeout=10) as resp:
        return json.loads(resp.read().decode())


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="船位查询")
    parser.add_argument("--mmsi", required=True, help="船舶 MMSI 编号")
    args = parser.parse_args()
    result = get_position(args.mmsi)
    print(json.dumps(result, ensure_ascii=False, indent=2))
