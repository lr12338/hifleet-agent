#!/usr/bin/env python3
"""Opt-in real gateway smoke test for customer_ceshi media handling."""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
from pathlib import Path

import requests


DEFAULT_IMAGES = (
    "image01-这个在全球海图里是什么意思.png",
    "image02-图上紫色的波浪线是指的什么.png",
    "image03-图中的小圈圈是什么意思？.png",
)


def _data_url(path: Path) -> str:
    return "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode("ascii")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--endpoint", default="http://127.0.0.1:10123/run")
    parser.add_argument("--image-dir", type=Path, default=Path(__file__).resolve().parents[1] / "test" / "image")
    parser.add_argument("--timeout", type=float, default=90.0)
    parser.add_argument("--strict-reference", action="store_true", help="Require image01 to mention 安全水域浮标.")
    parser.add_argument("--fixture", action="append", choices=DEFAULT_IMAGES, help="Run only the selected fixture; may be repeated.")
    args = parser.parse_args()

    if os.getenv("RUN_CUSTOMER_CESHI_MEDIA_E2E") != "1":
        print("Set RUN_CUSTOMER_CESHI_MEDIA_E2E=1 to make real model requests.", file=sys.stderr)
        return 2

    prompts = {
        DEFAULT_IMAGES[0]: "请结合用户上一条发送的媒体内容，回答以下补充说明或问题：这个在全球海图里是什么意思",
        DEFAULT_IMAGES[1]: "请结合用户上一条发送的媒体内容，回答以下补充说明或问题：图上紫色的波浪线是指的什么",
        DEFAULT_IMAGES[2]: "请结合用户上一条发送的媒体内容，回答以下补充说明或问题：图中的小圈圈是什么意思？",
    }
    failed = False
    for name in args.fixture or DEFAULT_IMAGES:
        path = args.image_dir / name
        if not path.is_file():
            print(f"Missing fixture: {path}", file=sys.stderr)
            return 2
        payload = {
            "user_id": "customer-ceshi-media-smoke",
            "session_id": f"customer-ceshi-media-smoke:{path.stem}",
            "source_channel": "wechat_cs",
            "agent_profile": "customer_ceshi",
            "response_mode": "compact",
            "llm_route": {
                "model": "doubao-seed-2-0-lite-260428",
                "modality": "multimodal",
                "thinking_type": "enabled",
                "reasoning_effort": "high",
                "deep_thinking_enabled": True,
            },
            "messages": [
                {"role": "system", "content": "用户当前更适合使用中文沟通。除非用户明确要求英文，否则请使用简洁、自然的中文回复。"},
                {"role": "user", "content": [{"type": "image_url", "image_url": {"url": _data_url(path)}}, {"type": "text", "text": prompts[name]}]},
            ],
        }
        response = requests.post(args.endpoint, json=payload, timeout=args.timeout)
        result = response.json()
        print(json.dumps({"fixture": name, "http_status": response.status_code, "run_id": result.get("run_id"), "status": result.get("status"), "answer": result.get("answer"), "metrics": result.get("metrics"), "context": result.get("context"), "error": result.get("error")}, ensure_ascii=False))
        metrics = dict(result.get("metrics") or {})
        context = dict(result.get("context") or {})
        if not result.get("answer") or int(metrics.get("media_calls", 0)) < 1 or context.get("media_delivery") != "inline_data_url":
            failed = True
        if args.strict_reference and name == DEFAULT_IMAGES[0] and "安全水域浮标" not in str(result.get("answer", "")):
            failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
