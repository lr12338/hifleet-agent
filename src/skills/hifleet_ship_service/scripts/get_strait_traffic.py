#!/usr/bin/env python3
"""海峡通航统计：查询四大咽喉航道通航数据

使用方式：
    python get_strait_traffic.py --oid 24480 --startdate 2026-05-07 --enddate 2026-05-07
    python get_strait_traffic.py --strait-name "霍尔木兹海峡" --startdate 2026-05-07 --enddate 2026-05-07

环境变量：
    HIFLEET_API_KEY - api.hifleet.com 通用 Token（可选，无 token 仅查近 1 周）
    HIFLEET_API_BASE - API 基址（可选，默认 https://api.hifleet.com）

注意：
    - 返回JSON，格式:
      {"oid":24480,"zonename":"曼德海峡","startdate":"...","enddate":"...",
       "passdata":[{"passdate":"2026-06-01","passdirection":[
         {"direction":"东南","shiptypecount":[{"shiptype":"杂货船","count":1},...],
          "total":16,"ships":[...]}]}]}
"""
import os
import sys
import json
import urllib.request
import urllib.parse

# 海峡 OID 映射表
STRAIT_OID_MAP = {
    "霍尔木兹海峡": "24480", "Hormuz": "24480", "hormuz": "24480",
    "曼德海峡": "24471", "Bab el-Mandeb": "24471", "bab el-mandeb": "24471",
    "苏伊士运河": "24474", "Suez Canal": "24474", "suez canal": "24474",
    "好望角": "24476", "Cape of Good Hope": "24476", "cape of good hope": "24476",
    "龙目海峡": "24482", "Lombok Strait": "24482", "lombok strait": "24482",
}

# 霍尔木兹海峡标识
HORMUZ_OID = "24480"


def match_strait_oid(strait_name: str) -> str:
    """根据海峡名称匹配 OID。

    Args:
        strait_name: 海峡名称（支持中英文、模糊匹配）

    Returns:
        匹配到的 OID，未匹配到返回空字符串
    """
    # 精确匹配
    oid = STRAIT_OID_MAP.get(strait_name, "")
    if oid:
        return oid

    # 模糊匹配
    name_lower = strait_name.lower().strip()
    for name, oid_val in STRAIT_OID_MAP.items():
        if name_lower in name.lower() or name.lower() in name_lower:
            return oid_val

    return ""


def get_strait_traffic(oid: str, startdate: str, enddate: str, i18n: str = "zh") -> dict:
    """查询海峡通航统计。

    Args:
        oid: 海峡 OID
        startdate: 开始日期（yyyy-MM-dd）
        enddate: 结束日期（yyyy-MM-dd）
        i18n: 语言（zh/en）

    Returns:
        API 原始响应字典
    """
    base = os.getenv("HIFLEET_API_BASE", "https://api.hifleet.com")
    key = os.getenv("HIFLEET_API_KEY", "")
    parts = [
        f"oid={urllib.parse.quote(oid, safe='')}",
        f"startdate={urllib.parse.quote(startdate, safe='')}",
        f"enddate={urllib.parse.quote(enddate, safe='')}",
        f"i18n={urllib.parse.quote(i18n, safe='')}",
    ]
    if key:
        encoded_key = urllib.parse.quote(key, safe="")
        parts.append(f"usertoken={encoded_key}")
    url = f"{base}/position/statisticzonetraffic?{'&'.join(parts)}"
    req = urllib.request.Request(url, method="POST", data=b"")
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode())


def is_hormuz(oid: str) -> bool:
    """判断是否为霍尔木兹海峡。"""
    return oid == HORMUZ_OID


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="海峡通航统计")
    parser.add_argument("--oid", default="", help="海峡 OID")
    parser.add_argument("--strait-name", default="", help="海峡名称（中文/英文）")
    parser.add_argument("--startdate", required=True, help="开始日期（yyyy-MM-dd）")
    parser.add_argument("--enddate", required=True, help="结束日期（yyyy-MM-dd）")
    parser.add_argument("--i18n", default="zh", help="语言（zh/en）")
    args = parser.parse_args()

    oid = args.oid
    if args.strait_name and not oid:
        oid = match_strait_oid(args.strait_name)
        if not oid:
            print(json.dumps({"error": f"未找到海峡: {args.strait_name}", "available": list(STRAIT_OID_MAP.keys())}, ensure_ascii=False, indent=2))
            sys.exit(1)

    if not oid:
        print(json.dumps({"error": "请提供 --oid 或 --strait-name"}, ensure_ascii=False, indent=2))
        sys.exit(1)

    result = get_strait_traffic(oid, args.startdate, args.enddate, args.i18n)
    result["is_hormuz"] = is_hormuz(oid)
    print(json.dumps(result, ensure_ascii=False, indent=2))
