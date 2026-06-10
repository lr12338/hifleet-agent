#!/usr/bin/env python3
"""船舶搜索：按关键字搜索船舶，返回 MMSI/IMO/船名/船型/船旗

使用方式：
    python search_ship.py --keyword "YU MING"

环境变量：
    HIFLEET_TTSE_KEY - ttseapi 搜索服务 Token（必填）
    HIFLEET_TTSE_BASE - ttseapi 基址（可选，默认 http://ttseapi.hifleet.com）

注意：
    - 搜索API返回纯文本，非JSON
    - 本函数返回原始文本，由调用方解析
"""
import os
import json
import urllib.request
import urllib.parse


def search_ship(keyword: str) -> str:
    """按关键字搜索船舶。

    Args:
        keyword: 搜索关键字（船名、MMSI 等）

    Returns:
        API 原始响应文本（纯文本格式，非JSON）
    """
    base = os.getenv("HIFLEET_TTSE_BASE", "http://ttseapi.hifleet.com")
    key = os.getenv("HIFLEET_TTSE_KEY", "")
    # 注意: usertoken含特殊字符(/+等)，不能用urlencode，需手动quote
    encoded_keyword = urllib.parse.quote(keyword, safe="")
    encoded_key = urllib.parse.quote(key, safe="")
    url = f"{base}/position/shipSearchText?keyword={encoded_keyword}&usertoken={encoded_key}"
    with urllib.request.urlopen(url, timeout=10) as resp:
        return resp.read().decode()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="船舶搜索")
    parser.add_argument("--keyword", required=True, help="搜索关键字（船名、MMSI 等）")
    args = parser.parse_args()
    result = search_ship(args.keyword)
    print(result)
