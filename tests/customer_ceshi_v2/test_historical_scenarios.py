import json
from pathlib import Path

import pytest

from agents.customer_ceshi_v2.evidence import review_candidate


@pytest.mark.parametrize("scenario", json.loads((Path(__file__).parent / "fixtures" / "scenarios.json").read_text(encoding="utf-8")), ids=lambda item: item["name"])
def test_historical_claims_are_not_completed_by_partial_evidence(scenario):
    review = review_candidate("draft", scenario["claims"], [{"status": "success", "facts": scenario["facts"], "sources": ["https://example.test"]}])

    assert review.ready is False
    assert review.unsupported_claims == scenario["expected_unsupported"]
