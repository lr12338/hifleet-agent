#!/usr/bin/env python3
"""PSC 检查记录：按 IMO 查询港口国监督检查记录

使用方式：
    python get_psc.py --imo 9613886

环境变量：
    HIFLEET_API_KEY - api.hifleet.com 通用 Token（必填）
    HIFLEET_API_BASE - API 基址（可选，默认 https://api.hifleet.com）

注意：
    - 返回JSON，格式: {"status":"1","data":[{mou,port,authority,...,detail:[...]}]}
"""
import os
import json
import urllib.request
import urllib.parse

from auth import psc_api_key


def get_psc(imo: str) -> dict:
    """按 IMO 查询 PSC 检查记录。

    Args:
        imo: 船舶 IMO 编号

    Returns:
        API 原始响应字典
    """
    base = os.getenv("HIFLEET_API_BASE", "https://api.hifleet.com")
    key = psc_api_key()
    params = {"imo": imo}
    if key:
        params["api_key"] = key
        params["usertoken"] = key
    url = f"{base}/pscapi/get?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(url, timeout=10) as resp:
        return json.loads(resp.read().decode())


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="PSC 检查记录查询")
    parser.add_argument("--imo", required=True, help="船舶 IMO 编号")
    args = parser.parse_args()
    result = get_psc(args.imo)
    print(json.dumps(result, ensure_ascii=False, indent=2))
