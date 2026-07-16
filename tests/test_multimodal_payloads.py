import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from main import _build_stream_prompt_from_messages, normalize_request_payload


def test_wechat_prompt_multimodal_files_normalize_to_message_segments():
    payload = {
        "source_channel": "wechat_mp",
        "content": {
            "query": {
                "prompt": [
                    {"type": "image", "content": {"url": "https://example.com/a.png"}},
                    {"type": "voice", "content": {"url": "https://example.com/a.amr", "format": "amr"}},
                    {"type": "video", "content": {"url": "https://example.com/a.mp4"}},
                    {"type": "text", "content": {"text": "请识别附件"}},
                ]
            }
        },
    }

    normalized = normalize_request_payload(payload)
    content = normalized["messages"][0]["content"]

    assert content[0] == {"type": "image_url", "image_url": {"url": "https://example.com/a.png"}}
    assert content[1] == {"type": "input_audio", "input_audio": {"url": "https://example.com/a.amr", "format": "amr"}}
    assert content[2] == {"type": "video_url", "video_url": {"url": "https://example.com/a.mp4"}}
    assert content[3] == {"type": "text", "text": "请识别附件"}


def test_input_multimodal_preserves_audio_format():
    payload = {
        "input": {
            "type": "multimodal",
            "image_url": "https://example.com/a.png",
            "audio_url": "https://example.com/a.amr",
            "audio_format": "amr",
            "video_url": "https://example.com/a.mp4",
            "text": "请识别附件",
        }
    }

    normalized = normalize_request_payload(payload)
    content = normalized["messages"][0]["content"]

    assert {"type": "input_audio", "input_audio": {"url": "https://example.com/a.amr", "format": "amr"}} in content


def test_stream_prompt_preserves_audio_semantics_and_format():
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "input_audio", "input_audio": {"url": "https://example.com/a.amr", "format": "amr"}},
                {"type": "image_url", "image_url": {"url": "https://example.com/a.png"}},
                {"type": "video_url", "video_url": {"url": "https://example.com/a.mp4"}},
                {"type": "text", "text": "请识别附件"},
            ],
        }
    ]

    prompt = _build_stream_prompt_from_messages(messages)

    assert {"type": "voice", "content": {"url": "https://example.com/a.amr", "format": "amr"}} in prompt
    assert {"type": "image", "content": {"url": "https://example.com/a.png"}} in prompt
    assert {"type": "video", "content": {"url": "https://example.com/a.mp4"}} in prompt
    assert {"type": "text", "content": {"text": "请识别附件"}} in prompt


def test_http_customer_support_final_sanitizer_removes_html_app_promotion():
    from main import _sanitize_customer_support_run_result

    cleaned = _sanitize_customer_support_run_result(
        {
            "generated_answer": '排障建议。\n<a href="https://www.hifleet.com/download/qr.html">下载APP</a>,手机查船更方便,服务电话:400-963-6899,微信:hifleetkhzs',
            "messages": [{"type": "ai", "content": '排障建议。\n<a href="https://www.hifleet.com/download/qr.html">下载APP</a>,手机查船更方便'}],
        },
        "customer_support",
    )

    assert "下载APP" not in cleaned["generated_answer"]
    assert "手机查船更方便" not in cleaned["messages"][0]["content"]
