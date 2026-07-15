from agents.customer_ceshi_v2.evidence import review_candidate
from agents.customer_ceshi_v2.evidence import EvidenceReviewer
from agents.customer_ceshi_v2.contracts import EvidenceReview


def test_reviewer_rejects_claim_not_supported_by_a_source():
    review = review_candidate("HiFleet calculates the warning line.", ["HiFleet calculates the warning line."], [{"status": "success", "facts": ["The page shows a warning line."], "sources": ["https://example.test/release"]}])

    assert review.ready is False
    assert review.unsupported_claims == ["HiFleet calculates the warning line."]
    assert review.recommended_action == "qualify_claim"


def test_reviewer_accepts_observed_claim_only_when_present_in_evidence():
    review = review_candidate("ETA is 10:00.", ["ETA is 10:00."], [{"status": "success", "facts": ["ETA is 10:00."], "sources": ["https://example.test/ship"]}])

    assert review.ready is True


def test_model_reviewer_is_preferred_when_available():
    class FakeClient:
        def review_evidence(self, **kwargs):
            return EvidenceReview(ready=True, supported_claims=["verified"], repaired_answer="verified")

    review = EvidenceReviewer(FakeClient()).review(goal="goal", answer="draft", claims=["verified"], observations=[])

    assert review.ready is True
    assert review.repaired_answer == "verified"


def test_reviewer_skips_model_when_there_are_no_claims():
    class FailingClient:
        def review_evidence(self, **kwargs):
            raise AssertionError("no-claim response should use local review")

    review = EvidenceReviewer(FailingClient()).review(
        goal="goal",
        answer="tool result",
        claims=[],
        observations=[{"status": "success", "facts": ["tool result"]}],
    )

    assert review.ready is True


def test_reviewer_accepts_claims_backed_by_successful_tool_result_without_model_call():
    class FailingClient:
        def review_evidence(self, **kwargs):
            raise AssertionError("tool-backed response should not require a model review")

    review = EvidenceReviewer(FailingClient()).review(
        goal="position",
        answer="船位已查询。",
        claims=["船位已查询。"],
        observations=[{"status": "success", "capability": "get_ship_position", "facts": ["MMSI 308068077 current position"], "data": {"mmsi": "308068077"}}],
    )

    assert review.ready is True
    assert review.supported_claims == ["船位已查询。"]
