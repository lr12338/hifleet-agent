from agents.customer_ceshi_v2.contracts import InspectMediaRequest, MediaAsset, PerceptionPacket
from agents.customer_ceshi_v2.perception import PerceptionService


class ImageClient:
    model = "doubao-test"

    def inspect_packet(self, asset, request):
        return PerceptionPacket(asset_id=asset.asset_id, media_type="image", model=self.model, requested_objective=request.objective, ocr_blocks=[{"id": "ocr:1", "text": "Average speed"}], factual_summary="The screenshot visibly shows Average speed.", overall_confidence="high")


def test_image_packet_exposes_observed_ocr_without_product_rule_inference():
    observations, _ = PerceptionService(ImageClient()).inspect([MediaAsset(asset_id="image", kind="image", url="https://example.test/image.png")], [InspectMediaRequest(asset_id="image", objective="extract visible labels", mode="ocr")])

    assert observations[0].data["perception_packet"]["ocr_blocks"][0]["text"] == "Average speed"
