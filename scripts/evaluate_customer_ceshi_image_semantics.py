#!/usr/bin/env python3
"""Run OSS-backed customer_ceshi image rubrics without retaining answers or URLs."""
from __future__ import annotations

import configparser
import json
import time
import uuid
from pathlib import Path
from typing import Any

import oss2
import requests


ROOT = Path(__file__).resolve().parents[1]
WEBCHAT_CONFIG = Path("/home/ecs-user/webchat-agent/config/config.ini")
IMAGE_DIR = ROOT / "test/image"
OUTPUT = ROOT / "reports/customer_ceshi_eval/image_semantic_rubric.json"

_RUBRICS: dict[str, dict[str, Any]] = {
    "image01": {"any_groups": [("安全水域", "safe water"), ("航标", "浮标")]},
    "image02": {"any_groups": [("可能", "需核实", "无法仅凭"), ("图层", "图例", "区域")]},
    "image03": {"any_groups": [("锚地", "锚泊"), ("图例", "规定", "公告")]},
    "image-04": {"any_groups": [("停航", "靠泊", "锚泊"), ("移动", "航行", "航段")]},
    "Image05": {"any_groups": [("mmsi",), ("开始时间", "异常时间", "何时开始")]},
}


def _fixtures() -> list[Path]:
    return [
        path for path in sorted(IMAGE_DIR.glob("*"))
        if path.suffix.lower() in {".png", ".jpg", ".jpeg"} and "参考回复" not in path.name
    ]


def _rubric_for(path: Path) -> dict[str, Any]:
    for prefix, rubric in _RUBRICS.items():
        if path.name.startswith(prefix):
            return rubric
    raise ValueError(f"missing rubric for {path.name}")


def _matches(answer: str, rubric: dict[str, Any]) -> bool:
    normalized = answer.lower()
    return all(any(term.lower() in normalized for term in group) for group in rubric["any_groups"])


def main() -> int:
    parser = configparser.ConfigParser()
    parser.read(WEBCHAT_CONFIG, encoding="utf-8")
    oss = parser["oss"]
    required = ("access_key_id", "access_key_secret", "bucket_name", "endpoint")
    if any(not oss.get(name, "").strip() for name in required):
        print(json.dumps({"status": "SKIPPED", "reason": "secure_oss_configuration_incomplete"}, ensure_ascii=False))
        return 0
    auth = oss2.Auth(oss.get("access_key_id"), oss.get("access_key_secret"))
    bucket = oss2.Bucket(auth, oss.get("endpoint"), oss.get("bucket_name"))
    results: list[dict[str, Any]] = []
    for fixture in _fixtures():
        object_key = f"chatwoot/customer_ceshi_image_rubric/{int(time.time())}-{uuid.uuid4().hex[:8]}/{fixture.name}"
        started = time.perf_counter()
        try:
            bucket.put_object_from_file(object_key, str(fixture), headers={"Content-Type": "image/png"})
            url = bucket.sign_url("GET", object_key, int(oss.get("signed_url_expire_seconds", "600")))
            response = requests.post(
                "http://127.0.0.1:10123/run",
                json={
                    "messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": url}}, {"type": "text", "text": fixture.stem.split("-", 1)[-1]}]}],
                    "session_id": f"customer_ceshi:image-rubric:{fixture.stem}",
                    "user_id": "customer_ceshi_image_rubric",
                    "source_channel": "customer_api",
                    "agent_profile": "customer_ceshi",
                    "response_mode": "compact",
                },
                timeout=240,
            )
            body = response.json()
            metrics = dict(body.get("metrics") or {})
            answer = str(body.get("answer") or "")
            passed = response.ok and body.get("status") == "success" and int(metrics.get("media_calls") or 0) >= 1 and _matches(answer, _rubric_for(fixture))
            results.append({
                "fixture": fixture.name,
                "passed": passed,
                "http_status": response.status_code,
                "answer_length": len(answer),
                "media_calls": int(metrics.get("media_calls") or 0),
                "orchestrator_model": metrics.get("orchestrator_model"),
                "perception_model": metrics.get("perception_model"),
                "elapsed_ms": int((time.perf_counter() - started) * 1000),
            })
        except Exception as exc:
            results.append({"fixture": fixture.name, "passed": False, "error": type(exc).__name__, "elapsed_ms": int((time.perf_counter() - started) * 1000)})
        finally:
            try:
                bucket.delete_object(object_key)
            except Exception:
                pass
    summary = {"kind": "conservative_semantic_rubric_no_answers_saved", "fixture_count": len(results), "passed": sum(item["passed"] for item in results), "failed": sum(not item["passed"] for item in results)}
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps({"summary": summary, "results": results}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
