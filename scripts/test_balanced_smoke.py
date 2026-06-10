#!/usr/bin/env python3
"""
Balanced 方案最小冒烟回归：
1) /health
2) /run 文本
3) /run websdk 图片
4) /run wechat 图片适配
5) /stream_run
"""
import json
import requests

BASE_URL = "http://127.0.0.1:10123"


def _ok(name: str, passed: bool, detail: str = ""):
    icon = "PASS" if passed else "FAIL"
    print(f"[{icon}] {name} {detail}")


def test_health() -> bool:
    r = requests.get(f"{BASE_URL}/health", timeout=10)
    passed = r.status_code == 200 and r.json().get("status") == "ok"
    _ok("health", passed, f"status={r.status_code}")
    return passed


def test_run_text() -> bool:
    payload = {
        "messages": [{"role": "user", "content": "你好"}],
        "session_id": "balanced_smoke_text",
        "user_id": "u1",
        "source_channel": "websdk",
    }
    r = requests.post(f"{BASE_URL}/run", json=payload, timeout=30)
    body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    passed = r.status_code == 200 and body.get("run_id")
    _ok("run_text", bool(passed), f"status={r.status_code}")
    return bool(passed)


def test_run_websdk_image() -> bool:
    payload = {
        "messages": [{
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": "https://ark-project.tos-cn-beijing.volces.com/doc_image/ark_demo_img_1.png"}},
                {"type": "text", "text": "请识别图片"}
            ]
        }],
        "session_id": "balanced_smoke_mm_websdk",
        "user_id": "u1",
        "source_channel": "websdk",
    }
    r = requests.post(f"{BASE_URL}/run", json=payload, timeout=45)
    passed = r.status_code == 200
    _ok("run_websdk_image", passed, f"status={r.status_code}")
    return passed


def test_run_wechat_image() -> bool:
    payload = {
        "content": {
            "query": {
                "prompt": [
                    {"type": "image", "content": {"url": "https://ark-project.tos-cn-beijing.volces.com/doc_image/ark_demo_img_1.png"}},
                    {"type": "text", "content": {"text": "请识别图片内容"}}
                ]
            }
        },
        "session_id": "wx_mp_balanced_smoke_1",
        "user_id": "u1",
        "source_channel": "wechat_mp",
    }
    r = requests.post(f"{BASE_URL}/run", json=payload, timeout=45)
    body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    ai_text = ""
    for m in body.get("messages", []):
        if m.get("type") == "ai" and m.get("content"):
            ai_text = m.get("content", "")
    # 最小判定：非欢迎语的图片识别结果通常会包含“图/图片/表格”等描述词
    passed = r.status_code == 200 and bool(ai_text)
    _ok("run_wechat_image", passed, f"status={r.status_code}")
    if not passed:
        print(f"  response_text={ai_text[:120]}")
    return passed


def test_stream_run() -> bool:
    payload = {
        "messages": [{"role": "user", "content": "请简短自我介绍"}],
        "session_id": "balanced_smoke_stream",
        "user_id": "u1",
        "source_channel": "websdk",
    }
    r = requests.post(f"{BASE_URL}/stream_run", json=payload, timeout=40, stream=True)
    first_chunks = []
    for line in r.iter_lines(decode_unicode=True):
        if line:
            first_chunks.append(line)
        if len(first_chunks) >= 3:
            break
    passed = r.status_code == 200 and any("event: message" in c for c in first_chunks)
    _ok("stream_run", passed, f"status={r.status_code}")
    return passed


if __name__ == "__main__":
    tests = [
        test_health,
        test_run_text,
        test_run_websdk_image,
        test_run_wechat_image,
        test_stream_run,
    ]
    passed = 0
    for t in tests:
        try:
            if t():
                passed += 1
        except Exception as e:
            _ok(t.__name__, False, f"exception={e}")

    total = len(tests)
    print(f"\nSummary: {passed}/{total} passed")
    raise SystemExit(0 if passed == total else 1)

