from agents.customer_ceshi_v2.contracts import InspectMediaRequest, MediaAsset, PerceptionPacket
from agents.customer_ceshi_v2.perception import PerceptionService
from agents.customer_ceshi_v2.builder import _media_metadata_observations


class VideoClient:
    model = "doubao-test"

    def inspect_packet(self, asset, request):
        return PerceptionPacket(asset_id=asset.asset_id, media_type="video", model=self.model, requested_objective=request.objective, events=[{"start_ms": 5000, "end_ms": 7000, "description": "Error dialog"}], factual_summary="An error dialog appears at 5-7 seconds.", overall_confidence="high")


def test_video_segment_request_is_part_of_cache_contract():
    service = PerceptionService(VideoClient())
    asset = MediaAsset(asset_id="video", kind="video", url="https://example.test/a.mp4")
    request = InspectMediaRequest(asset_id="video", objective="read error", mode="targeted_verify", time_range={"start_ms": 5000, "end_ms": 7000})

    observations, _ = service.inspect([asset], [request])

    assert observations[0].data["perception_packet"]["events"][0]["start_ms"] == 5000


def test_video_metadata_is_a_deterministic_pre_perception_observation():
    observations = _media_metadata_observations([MediaAsset(asset_id="video", kind="video", url="https://example.test/error.mp4").model_dump()])

    assert observations[0]["capability"] == "media_metadata"
