from __future__ import annotations

import base64
import io
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from agents.customer_ceshi_v2.media_input import ImageInputPreparer, MediaPreparationError
from agents.customer_ceshi_v2.models import MultimodalPerceptionClient, _classify_gateway_error


FIXTURE = Path(__file__).resolve().parents[2] / "test" / "image" / "image01-这个在全球海图里是什么意思.png"


def _data_url() -> str:
    return "data:image/png;base64," + base64.b64encode(FIXTURE.read_bytes()).decode("ascii")


def _plain_image_data_url() -> str:
    image = Image.new("RGB", (40, 40), "blue")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def test_inline_icon_is_normalized_and_upscaled():
    prepared = ImageInputPreparer().prepare(_data_url())

    assert prepared.data_url.startswith("data:image/png;base64,")
    assert prepared.diagnostics["media_delivery"] == "inline_data_url"
    assert prepared.diagnostics["media_source_type"] == "inline"
    assert prepared.diagnostics["media_prepared_size"]["width"] >= 512
    assert prepared.diagnostics["media_prepared_size"]["height"] >= 512
    assert prepared.diagnostics["media_local_hint"]["suspected_symbol"] == "安全水域浮标（Safe Water Mark）"


def test_remote_private_address_is_rejected(monkeypatch):
    monkeypatch.setattr("agents.customer_ceshi_v2.media_input._is_public_http_url", lambda url: False)

    with pytest.raises(MediaPreparationError) as exc_info:
        ImageInputPreparer().prepare("http://127.0.0.1/private.png")

    assert exc_info.value.code == "media_url_blocked"


def test_provider_image_download_error_is_not_misclassified_as_authentication():
    error = _classify_gateway_error(Exception("HTTP 400 InvalidParameter: Error while downloading: signed image URL"))

    assert error.code == "media_download_failed"
    assert error.retryable is True


def test_multimodal_client_short_circuits_known_local_chart_icon():
    calls = []

    class FakeClient:
        def invoke(self, messages, **kwargs):
            calls.append(messages)
            return SimpleNamespace(
                content='{"asset_id":"asset-0-0","media_type":"image","model":"vision-test","requested_objective":"identify","visual_features":["red circular mark","black center dot"],"suspected_symbol":"安全水域浮标","factual_summary":"A red circular symbol with a black center dot is visible.","overall_confidence":"medium"}'
            )

    client = MultimodalPerceptionClient({}, client=FakeClient())
    from agents.customer_ceshi_v2.contracts import InspectMediaRequest, MediaAsset

    packet = client.inspect_packet(
        MediaAsset(asset_id="asset-0-0", kind="image", url=_data_url()),
        InspectMediaRequest(asset_id="asset-0-0", objective="identify", mode="visual_detail"),
    )

    assert packet.suspected_symbol == "安全水域浮标（Safe Water Mark）"
    assert packet.model == "local_visual_fallback"
    assert calls == []


def test_multimodal_client_receives_prepared_inline_image_for_unknown_picture():
    calls = []

    class FakeClient:
        def invoke(self, messages, **kwargs):
            calls.append(messages)
            return SimpleNamespace(
                content='{"asset_id":"asset-0-0","media_type":"image","model":"vision-test","requested_objective":"identify","visual_features":["blue square"],"factual_summary":"A blue square is visible.","overall_confidence":"medium"}'
            )

    client = MultimodalPerceptionClient({}, client=FakeClient())
    from agents.customer_ceshi_v2.contracts import InspectMediaRequest, MediaAsset

    packet = client.inspect_packet(
        MediaAsset(asset_id="asset-0-0", kind="image", url=_plain_image_data_url()),
        InspectMediaRequest(asset_id="asset-0-0", objective="identify", mode="visual_detail"),
    )

    assert packet.factual_summary == "A blue square is visible."
    content = calls[0][0].content
    image_part = next(part for part in content if part["type"] == "image_url")
    assert image_part["image_url"]["url"].startswith("data:image/png;base64,")
