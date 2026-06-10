#!/usr/bin/env python3
"""区域船舶查询：按 areaId/bbox/polygon 查询船舶数量

使用方式：
    python get_area_traffic.py --areaId 1
    python get_area_traffic.py --area-name "红海"
    python get_area_traffic.py --bbox 120,15,121,17

环境变量：
    HIFLEET_API_KEY - api.hifleet.com 通用 Token（必填）
    HIFLEET_API_BASE - API 基址（可选，默认 https://api.hifleet.com）

注意：
    - 返回JSON，格式: {"result":"ok","num":2189,"list":[{...},...]}
    - list 字段是数组
"""
import os
import sys
import json
import urllib.request
import urllib.parse

# 内置区域 ID 映射表
AREA_ID_MAP = {
    "红海": "1", "Red Sea": "1", "redsea": "1",
    "波斯湾": "2", "Persian Gulf": "2", "persiangulf": "2",
    "亚丁湾": "3", "Gulf of Aden": "3", "gulfofaden": "3",
    "地中海": "4", "Mediterranean": "4", "mediterranean": "4",
    "马六甲海峡": "5", "Malacca Strait": "5", "malaccastrait": "5",
    "南海": "6", "South China Sea": "6", "southchinasea": "6",
    "印度洋": "7", "Indian Ocean": "7", "indianocean": "7",
    "北太平洋": "8", "North Pacific": "8", "northpacific": "8",
    "好望角": "9", "Cape of Good Hope": "9", "capeofgoodhope": "9",
    "加勒比海": "10", "Caribbean Sea": "10", "caribbeansea": "10",
    "黑海": "11", "Black Sea": "11", "blacksea": "11",
    "阿拉伯海": "12", "Arabian Sea": "12", "arabiansea": "12",
    "东非沿海": "13", "East Africa": "13", "eastafrica": "13",
    "西非沿海": "14", "West Africa": "14", "westafrica": "14",
    "渤海": "15", "Bohai Sea": "15", "bohaisea": "15",
}


def match_area_id(area_name: str) -> str:
    """根据区域名称模糊匹配 areaId。

    Args:
        area_name: 区域名称（支持中英文、模糊匹配）

    Returns:
        匹配到的 areaId，未匹配到返回空字符串
    """
    # 精确匹配
    area_id = AREA_ID_MAP.get(area_name, "")
    if area_id:
        return area_id

    # 模糊匹配
    area_name_lower = area_name.lower().strip()
    for name, aid in AREA_ID_MAP.items():
        if area_name_lower in name.lower() or name.lower() in area_name_lower:
            return aid

    return ""


def get_area_traffic(area_id: str = "", bbox: str = "", polygon: str = "") -> dict:
    """按区域查询船舶数量。

    Args:
        area_id: 区域 ID（与 bbox/polygon 三选一）
        bbox: 矩形范围（左经,下纬,右经,上纬）
        polygon: 多边形范围（WKT 格式）

    Returns:
        API 原始响应字典
    """
    base = os.getenv("HIFLEET_API_BASE", "https://api.hifleet.com")
    key = os.getenv("HIFLEET_API_KEY", "")
    encoded_key = urllib.parse.quote(key, safe="")
    parts = [f"usertoken={encoded_key}"]
    if area_id:
        parts.append(f"areaId={urllib.parse.quote(area_id, safe='')}")
    if bbox:
        parts.append(f"bbox={urllib.parse.quote(bbox, safe='')}")
    if polygon:
        parts.append(f"polygon={urllib.parse.quote(polygon, safe='')}")
    url = f"{base}/position/gettraffic/token?{'&'.join(parts)}"
    with urllib.request.urlopen(url, timeout=10) as resp:
        return json.loads(resp.read().decode())


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="区域船舶查询")
    parser.add_argument("--areaId", default="", help="区域 ID")
    parser.add_argument("--area-name", default="", help="区域名称（中文/英文，模糊匹配）")
    parser.add_argument("--bbox", default="", help="矩形范围（左经,下纬,右经,上纬）")
    parser.add_argument("--polygon", default="", help="多边形范围（WKT 格式）")
    args = parser.parse_args()

    area_id = args.areaId
    if args.area_name and not area_id:
        area_id = match_area_id(args.area_name)
        if not area_id:
            print(json.dumps({"error": f"未找到区域: {args.area_name}", "available": list(AREA_ID_MAP.keys())}, ensure_ascii=False, indent=2))
            sys.exit(1)

    result = get_area_traffic(area_id=area_id, bbox=args.bbox, polygon=args.polygon)
    print(json.dumps(result, ensure_ascii=False, indent=2))
