#!/usr/bin/env python3
"""Run a safe OSS-backed customer_ceshi image E2E without exposing credentials or signed URLs."""
from __future__ import annotations

import configparser
import json
import mimetypes
import sys
import time
import uuid
import argparse
from pathlib import Path

import oss2
import requests

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.probe_customer_ceshi_responses import record_media_capability


WEBCHAT_CONFIG = Path("/home/ecs-user/webchat-agent/config/config.ini")
DEFAULT_FIXTURE = ROOT / "test/image/image01-这个在全球海图里是什么意思.png"


def _media_type(fixture: Path) -> str:
    suffix = fixture.suffix.lower()
    if suffix in {".wav", ".mp3", ".m4a", ".aac", ".ogg"}:
        return "audio_url"
    if suffix in {".mp4", ".mov", ".avi", ".webm"}:
        return "video_url"
    return "image_url"


def _prompt(media_type: str) -> str:
    return {
        "audio_url": "请简要说明这段音频是否可识别；不要臆测听不清的内容。",
        "video_url": "请简要说明这个视频是否可识别；不要臆测不可见内容。",
    }.get(media_type, "这个在全球海图里是什么意思？")


def main() -> int:
    arguments = argparse.ArgumentParser()
    arguments.add_argument("--wechat", action="store_true", help="use the legacy WeChat content.query.prompt envelope")
    arguments.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE, help="local image fixture to upload")
    args = arguments.parse_args()
    fixture = args.fixture.resolve()
    if not fixture.is_file():
        print(json.dumps({"status": "FAILED", "error": "fixture_not_found"}, ensure_ascii=False))
        return 1
    parser = configparser.ConfigParser()
    parser.read(WEBCHAT_CONFIG, encoding="utf-8")
    oss = parser["oss"]
    required = ("access_key_id", "access_key_secret", "bucket_name", "endpoint")
    if any(not oss.get(name, "").strip() for name in required):
        print(json.dumps({"status": "SKIPPED", "reason": "secure_oss_configuration_incomplete"}, ensure_ascii=False))
        return 0
    object_key = f"chatwoot/customer_ceshi_e2e/{int(time.time())}-{uuid.uuid4().hex[:8]}/{fixture.name}"
    auth = oss2.Auth(oss.get("access_key_id"), oss.get("access_key_secret"))
    bucket = oss2.Bucket(auth, oss.get("endpoint"), oss.get("bucket_name"))
    media_type = _media_type(fixture)
    content_type = mimetypes.guess_type(fixture.name)[0] or "application/octet-stream"
    try:
        bucket.put_object_from_file(object_key, str(fixture), headers={"Content-Type": content_type})
        signed_url = bucket.sign_url("GET", object_key, int(oss.get("signed_url_expire_seconds", "600")))
        media_part = {"type": media_type, media_type: {"url": signed_url}}
        if media_type == "audio_url":
            media_part = {"type": "input_audio", "input_audio": {"url": signed_url, "format": fixture.suffix.lstrip(".").lower()}}
        payload = {
            "messages": [{"role": "user", "content": [media_part, {"type": "text", "text": _prompt(media_type)}]}],
            "session_id": f"customer_ceshi:e2e:{media_type}:oss",
            "user_id": "customer_ceshi_e2e_image",
            "source_channel": "customer_api",
            "agent_profile": "customer_ceshi",
            "response_mode": "compact",
        }
        if args.wechat and media_type == "image_url":
            payload = {
                "content": {"query": {"prompt": [{"type": "image", "content": {"url": signed_url}}, {"type": "text", "content": {"text": "这个在全球海图里是什么意思？"}}]}},
                "session_id": "wechat_kf:e2e:image:oss",
                "user_id": "customer_ceshi_e2e_image",
                "source_channel": "wechat_kf",
                "agent_profile": "customer_ceshi",
                "response_mode": "compact",
            }
        response = requests.post(
            "http://127.0.0.1:10123/run",
            json=payload,
            timeout=120,
        )
        result = response.json()
        metrics = dict(result.get("metrics") or {})
        safe = {
            "status": result.get("status"),
            "http_status": response.status_code,
            "answer_length": len(str(result.get("answer") or "")),
            "session_id": result.get("session_id"),
            "agent_profile": result.get("agent_profile"),
            "orchestrator_model": metrics.get("orchestrator_model"),
            "perception_model": metrics.get("perception_model"),
            "media_calls": metrics.get("media_calls"),
            "tool_calls": metrics.get("tool_calls"),
            "object_key": object_key,
            "fixture": fixture.name,
            "wechat_compat": args.wechat,
            "media_type": media_type,
        }
        print(json.dumps(safe, ensure_ascii=False))
        passed = response.ok and result.get("status") == "success" and int(metrics.get("media_calls") or 0) >= 1
        record_media_capability(media_type, passed=passed)
        return 0 if passed else 1
    except Exception as exc:
        record_media_capability(media_type, passed=False)
        print(json.dumps({"status": "FAILED", "error": type(exc).__name__, "object_key": object_key}, ensure_ascii=False))
        return 1
    finally:
        try:
            bucket.delete_object(object_key)
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
