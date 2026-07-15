from agents.customer_ceshi_v2.contracts import InspectMediaRequest, MediaAsset, PerceptionPacket
from agents.customer_ceshi_v2.perception import PerceptionService


class AudioClient:
    model = "doubao-test"

    def inspect_packet(self, asset, request):
        return PerceptionPacket(asset_id=asset.asset_id, media_type="audio", model=self.model, requested_objective=request.objective, transcript=[{"start_ms": 0, "end_ms": 1000, "text": "MMSI 123", "confidence": "low"}], factual_summary="A low-confidence MMSI was transcribed.", limitations=["Confirm critical identifiers."], overall_confidence="low")


def test_audio_packet_preserves_transcript_confidence():
    observations, _ = PerceptionService(AudioClient()).inspect([MediaAsset(asset_id="audio", kind="audio", url="https://example.test/a.wav")], [InspectMediaRequest(asset_id="audio", objective="transcribe", mode="transcription")])

    packet = observations[0].data["perception_packet"]
    assert packet["transcript"][0]["confidence"] == "low"
