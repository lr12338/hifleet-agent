#!/usr/bin/env python3
"""船舶档案：按 MMSI 或 IMO 查询船舶详细参数

使用方式：
    python get_archive.py --mmsi 414726000
    python get_archive.py --imo 9613886

环境变量：
    HIFLEET_API_KEY - api.hifleet.com 通用 Token（必填）
    HIFLEET_API_BASE - API 基址（可选，默认 https://api.hifleet.com）

注意：
    - 返回JSON，格式: {"status":"1","data":[{key,labelZh,labelEn,value:[...]}]}
    - data 是分组数组，每组有 key/labelZh/labelEn/value
"""
import os
import json
import urllib.request
import urllib.parse


def get_archive(mmsi: str = "", imo: str = "") -> dict:
    """按 MMSI 或 IMO 查询船舶档案。

    Args:
        mmsi: 船舶 MMSI 编号（与 imo 二选一）
        imo: 船舶 IMO 编号（与 mmsi 二选一）

    Returns:
        API 原始响应字典
    """
    base = os.getenv("HIFLEET_API_BASE", "https://api.hifleet.com")
    key = os.getenv("HIFLEET_API_KEY", "")
    encoded_key = urllib.parse.quote(key, safe="")
    parts = [f"usertoken={encoded_key}"]
    if mmsi:
        parts.append(f"mmsi={urllib.parse.quote(mmsi, safe='')}")
    if imo:
        parts.append(f"imo={urllib.parse.quote(imo, safe='')}")
    url = f"{base}/shiparchive/getShipArchiveWithEnginAndCompany?{'&'.join(parts)}"
    with urllib.request.urlopen(url, timeout=10) as resp:
        return json.loads(resp.read().decode())


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="船舶档案查询")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--mmsi", help="船舶 MMSI 编号")
    group.add_argument("--imo", help="船舶 IMO 编号")
    args = parser.parse_args()
    result = get_archive(mmsi=args.mmsi or "", imo=args.imo or "")
    print(json.dumps(result, ensure_ascii=False, indent=2))
