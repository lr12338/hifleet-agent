#!/usr/bin/env python3
"""经纬度格式转换工具

支持度分秒、度分、十进制度等多种格式的经纬度转换。
"""
import re
from typing import Optional, Tuple


def dms_to_decimal(dms_str: str) -> Optional[float]:
    """将度分秒/度分格式的经纬度字符串转换为十进制度。

    支持格式：
    - 度分秒：116°52′30″E, 22°7'30"N
    - 度分：116°52.5′E, 22°7.5'N
    - 十进制度：116.875°E, 22.125°N
    - 纯数字：116.875, 22.125

    Args:
        dms_str: 经纬度字符串

    Returns:
        十进制度数值，东经/北纬为正，西经/南纬为负。解析失败返回 None。
    """
    if not dms_str or not isinstance(dms_str, str):
        return None

    dms_str = dms_str.strip()

    # 尝试度分秒格式：116°52′30″E（秒符号可选）
    pattern_dms = r"(\d+)[°]\s*(\d+)[′']\s*(\d+(?:\.\d+)?)[″\"]?\s*([EWNSewns])"
    match = re.match(pattern_dms, dms_str)
    if match:
        degrees = float(match.group(1))
        minutes = float(match.group(2))
        seconds = float(match.group(3))
        direction = match.group(4).upper()
        decimal = degrees + minutes / 60 + seconds / 3600
        if direction in ("W", "S"):
            decimal = -decimal
        return decimal

    # 尝试度分格式（无分符号）：26°34.502N
    # 注意：必须放在带分符号的度分格式之前，否则 26°34.502′N 会被误匹配
    # 但带分符号的更精确，所以先尝试带分符号的
    pattern_dm = r"(\d+)[°]\s*(\d+(?:\.\d+)?)[′']\s*([EWNSewns])"
    match = re.match(pattern_dm, dms_str)
    if match:
        degrees = float(match.group(1))
        minutes = float(match.group(2))
        direction = match.group(3).upper()
        decimal = degrees + minutes / 60
        if direction in ("W", "S"):
            decimal = -decimal
        return decimal

    # 尝试度分格式（无分符号）：26°34.502N, 056°06.714E
    pattern_dm_nosep = r"(\d+)[°]\s*(\d+(?:\.\d+)?)\s*([EWNSewns])"
    match = re.match(pattern_dm_nosep, dms_str)
    if match:
        degrees = float(match.group(1))
        minutes = float(match.group(2))
        direction = match.group(3).upper()
        decimal = degrees + minutes / 60
        if direction in ("W", "S"):
            decimal = -decimal
        return decimal

    # 尝试中横线分隔度分格式：26-34.502N, 056-06.714E
    pattern_dash = r"(\d+)\s*[-–—]\s*(\d+(?:\.\d+)?)\s*([EWNSewns])"
    match = re.match(pattern_dash, dms_str)
    if match:
        degrees = float(match.group(1))
        minutes = float(match.group(2))
        direction = match.group(3).upper()
        decimal = degrees + minutes / 60
        if direction in ("W", "S"):
            decimal = -decimal
        return decimal

    # 尝试带方向的十进制度：116.875°E
    pattern_dd = r"(\d+(?:\.\d+)?)[°]?\s*([EWNSewns])"
    match = re.match(pattern_dd, dms_str)
    if match:
        decimal = float(match.group(1))
        direction = match.group(2).upper()
        if direction in ("W", "S"):
            decimal = -decimal
        return decimal

    # 尝试纯数字
    try:
        return float(dms_str)
    except (ValueError, TypeError):
        return None


def parse_coordinates(lon_str: str, lat_str: str) -> Tuple[Optional[float], Optional[float]]:
    """解析经纬度字符串对，返回十进制度。

    Args:
        lon_str: 经度字符串
        lat_str: 纬度字符串

    Returns:
        (经度, 纬度) 元组，解析失败对应位置为 None
    """
    lon = dms_to_decimal(lon_str)
    lat = dms_to_decimal(lat_str)
    return lon, lat


if __name__ == "__main__":
    # 测试
    test_cases = [
        ("116°52.5′E", "22°7.5′N"),
        ("116°52′30″E", "22°7′30″N"),
        ("72°3.763′W", "19°53.793′N"),
        ("116.875", "22.125"),
    ]
    for lon_s, lat_s in test_cases:
        lon, lat = parse_coordinates(lon_s, lat_s)
        print(f"{lon_s}, {lat_s} → ({lon}, {lat})")
