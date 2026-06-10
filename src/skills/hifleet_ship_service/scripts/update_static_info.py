#!/usr/bin/env python3
"""静态信息更新：更新船舶档案数据

使用方式：
    python update_static_info.py --mmsi 636025653 --destination LUOYUAN --eta "2026-05-09 20:00:00"

环境变量：
    HIFLEET_TTSE_BASE - ttseapi 基址（可选，默认 http://ttseapi.hifleet.com）

注意：
    - 仅上传用户明确提供的参数，不设置默认值
    - 至少需要提供 mmsi + 一个可更新字段
    - API可能返回纯文本或JSON

字段名映射（CLI参数 → API字段名）：
    ship_name  → name        (船名)
    imo        → imonumber   (IMO编号)
    ship_type  → type        (船型)
    built_year → buildyear   (建造年份)
    draft      → draught     (吃水)
    其余字段名与API一致，无需映射
"""
import os
import sys
import json
import urllib.request
import urllib.parse


def update_static_info(data: dict, usertoken: str = "") -> str:
    """更新船舶静态信息。

    Args:
        data: 更新数据字典，必须包含 mmsi 和至少一个可更新字段
        usertoken: 可选的认证token（API文档标注无需认证，但某些群组需绑定账号）

    Returns:
        API 原始响应文本（可能是纯文本或JSON字符串）
    """
    base = os.getenv("HIFLEET_TTSE_BASE", "http://ttseapi.hifleet.com")

    # 构造URL（可选带usertoken）
    if usertoken:
        url = f"{base}/position/updateShipAisStaticInfo?usertoken={urllib.parse.quote(usertoken, safe='')}"
    else:
        url = f"{base}/position/updateShipAisStaticInfo"

    body = json.dumps(data).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST",
                                headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.read().decode()
    except urllib.error.HTTPError as e:
        error_body = e.read().decode() if e.fp else ""
        return json.dumps({"error": f"HTTP {e.code}", "detail": error_body}, ensure_ascii=False)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="静态信息更新")
    parser.add_argument("--mmsi", required=True, help="船舶 MMSI 编号")
    parser.add_argument("--ship-name", default=None, help="船名")
    parser.add_argument("--imo", default=None, help="IMO 编号")
    parser.add_argument("--ship-type", default=None, help="船舶类型")
    parser.add_argument("--minotype", default=None, help="MINO 船型代码")
    parser.add_argument("--length", type=float, default=None, help="船长（米）")
    parser.add_argument("--width", type=float, default=None, help="船宽（米）")
    parser.add_argument("--dwt", type=int, default=None, help="载重吨")
    parser.add_argument("--flag", default=None, help="船旗国")
    parser.add_argument("--callsign", default=None, help="呼号")
    parser.add_argument("--built-year", type=int, default=None, help="建造年份")
    parser.add_argument("--destination", default=None, help="目的港")
    parser.add_argument("--eta", default=None, help="预抵时间（yyyy-MM-dd HH:mm:ss）")
    parser.add_argument("--draft", type=float, default=None, help="吃水（米）")
    parser.add_argument("--wechatgroup", default=None, help="微信群组绑定")
    args = parser.parse_args()

    # 构造请求体 - 静态信息更新只需要 mmsi + bindCheck
    data = {
        "mmsi": args.mmsi,
        "bindCheck": "0"          # 跳过群组绑定检查
    }
    # 注意：不传 "name": args.mmsi（name是船名，不应填MMSI）
    # 注意：不传 "checkFly"（这是船位上传接口的字段，静态更新不需要）

    # 字段名映射：CLI参数名 → API实际字段名
    if args.ship_name is not None:
        data["name"] = args.ship_name          # ship_name → name
    if args.imo is not None:
        data["imonumber"] = args.imo           # imo → imonumber
    if args.ship_type is not None:
        data["type"] = args.ship_type          # ship_type → type
    if args.minotype is not None:
        data["minotype"] = args.minotype       # minotype 与API一致
    if args.built_year is not None:
        data["buildyear"] = args.built_year    # built_year → buildyear
    if args.length is not None:
        data["length"] = args.length
    if args.width is not None:
        data["width"] = args.width
    if args.dwt is not None:
        data["dwt"] = args.dwt
    if args.flag is not None:
        data["flag"] = args.flag
    if args.callsign is not None:
        data["callsign"] = args.callsign
    if args.destination is not None:
        data["destination"] = args.destination
    if args.eta is not None:
        data["eta"] = args.eta
    if args.draft is not None:
        data["draught"] = args.draft           # draft → draught
    if args.wechatgroup is not None:
        data["wechatgroup"] = args.wechatgroup # wechatgroup 与API一致

    # 检查是否有实质性更新数据
    update_fields = [k for k in data if k not in ("mmsi", "bindCheck")]
    if not update_fields:
        print(json.dumps({"error": "未提供任何可更新的数据，请至少提供一个更新字段"}, ensure_ascii=False, indent=2))
        sys.exit(1)

    result = update_static_info(data)
    print(result)
