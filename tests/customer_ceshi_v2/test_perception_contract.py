from agents.customer_ceshi_v2.contracts import InspectMediaRequest, MediaAsset, PerceptionPacket
from agents.customer_ceshi_v2.perception import PerceptionService


class FakePerceptionClient:
    model = "doubao-test"

    def __init__(self):
        self.calls = []

    def inspect_packet(self, asset, request):
        self.calls.append((asset.asset_id, request.mode))
        return PerceptionPacket(asset_id=asset.asset_id, media_type=asset.kind, model=self.model, requested_objective=request.objective, fields=[{"name": "mmsi", "value": "123456789", "raw_text": "123456789", "status": "observed", "confidence": "high", "source_ref": "ocr:1", "asset_id": asset.asset_id}], factual_summary=f"Observed {asset.asset_id}", overall_confidence="high")


def test_perception_packets_keep_assets_separate_and_cache_by_request():
    client = FakePerceptionClient()
    service = PerceptionService(client)
    assets = [MediaAsset(asset_id="image-a", kind="image", url="https://example.test/a.png"), MediaAsset(asset_id="image-b", kind="image", url="https://example.test/b.png")]
    requests = [InspectMediaRequest(asset_id="image-a", objective="extract fields", mode="field_extract"), InspectMediaRequest(asset_id="image-b", objective="extract fields", mode="field_extract")]

    observations, cache_hits = service.inspect(assets, requests)
    cached, cached_hits = service.inspect(assets, requests)

    assert [item.data["perception_packet"]["asset_id"] for item in observations] == ["image-a", "image-b"]
    assert client.calls == [("image-a", "field_extract"), ("image-b", "field_extract")]
    assert cache_hits == 0
    assert cached_hits == 2
    assert all(item.data["cached"] is True for item in cached)


def test_empty_image_packet_runs_one_detail_pass_before_success():
    class DetailClient:
        model = "doubao-test"

        def __init__(self):
            self.calls = []

        def inspect_packet(self, asset, request):
            self.calls.append(bool(asset.metadata.get("perception_detail")))
            if not asset.metadata.get("perception_detail"):
                return PerceptionPacket(asset_id=asset.asset_id, media_type="image", model=self.model, requested_objective=request.objective, overall_confidence="low")
            return PerceptionPacket(
                asset_id=asset.asset_id,
                media_type="image",
                model=self.model,
                requested_objective=request.objective,
                visual_features=["red circular mark", "black center dot"],
                suspected_symbol="安全水域浮标",
                factual_summary="A red circular mark with a black center dot is visible.",
                overall_confidence="medium",
            )

    client = DetailClient()
    observations, cache_hits = PerceptionService(client).inspect(
        [MediaAsset(asset_id="image", kind="image", url="data:image/png;base64,AA==")],
        [InspectMediaRequest(asset_id="image", objective="identify", mode="visual_detail")],
    )

    assert cache_hits == 0
    assert client.calls == [False, True]
    assert observations[0].status == "success"
    assert observations[0].data["media_perception_passes"] == 2
    assert observations[0].data["perception_packet"]["suspected_symbol"] == "安全水域浮标"


def test_empty_image_packet_is_not_recorded_as_success():
    class EmptyClient:
        model = "doubao-test"

        def __init__(self):
            self.calls = 0

        def inspect_packet(self, asset, request):
            self.calls += 1
            return PerceptionPacket(asset_id=asset.asset_id, media_type="image", model=self.model, requested_objective=request.objective, overall_confidence="low")

    client = EmptyClient()
    observations, _ = PerceptionService(client).inspect(
        [MediaAsset(asset_id="image", kind="image", url="data:image/png;base64,AA==")],
        [InspectMediaRequest(asset_id="image", objective="identify", mode="visual_detail")],
    )

    assert client.calls == 2
    assert observations[0].status == "not_found"
    assert observations[0].warnings == ["media_no_visual_evidence"]
    assert observations[0].data["media_perception_passes"] == 2


def test_empty_model_packet_uses_bounded_local_visual_hint():
    class EmptyClient:
        model = "doubao-test"

        def inspect_packet(self, asset, request):
            return PerceptionPacket(asset_id=asset.asset_id, media_type="image", model=self.model, requested_objective=request.objective, overall_confidence="low")

    asset = MediaAsset(
        asset_id="image",
        kind="image",
        url="data:image/png;base64,AA==",
        metadata={
            "media_input": {
                "media_local_hint": {
                    "confidence": "medium",
                    "visual_features": ["红色圆形标记", "中心黑色圆点"],
                    "suspected_symbol": "安全水域浮标（Safe Water Mark）",
                    "summary": "图中可见红色圆形标记，中心有黑色圆点。",
                }
            }
        },
    )

    observations, _ = PerceptionService(EmptyClient()).inspect(
        [asset],
        [InspectMediaRequest(asset_id="image", objective="identify", mode="visual_detail")],
    )

    assert observations[0].status == "success"
    assert observations[0].data["perception_packet"]["model"] == "local_visual_fallback"
    assert observations[0].data["perception_packet"]["suspected_symbol"] == "安全水域浮标（Safe Water Mark）"
