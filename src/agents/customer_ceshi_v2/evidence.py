from __future__ import annotations

from typing import Any

from .contracts import EvidenceReview


def review_candidate(answer: str, claims: list[str], observations: list[dict]) -> EvidenceReview:
    if not answer:
        return EvidenceReview(ready=False, missing_required_facts=["candidate_answer"], recommended_action="ask_user")
    evidence_text = "\n".join(" ".join(item.get("facts", [])) for item in observations if item.get("status") in {"success", "partial"}).lower()
    supported = [claim for claim in claims if claim and claim.lower() in evidence_text]
    unsupported = [claim for claim in claims if claim and claim not in supported]
    tool_backed = any(
        item.get("status") in {"success", "partial"}
        and str(item.get("capability", "")).startswith(("get_", "ship_", "local_kb_", "web_search"))
        and bool(item.get("facts"))
        for item in observations
    )
    if tool_backed:
        return EvidenceReview(ready=True, supported_claims=claims, repaired_answer=answer)
    if unsupported:
        repaired = answer
        for claim in unsupported:
            repaired = repaired.replace(claim, "该信息尚未获得可靠证据")
        return EvidenceReview(ready=False, supported_claims=supported, unsupported_claims=unsupported, recommended_action="qualify_claim", repaired_answer=repaired)
    return EvidenceReview(ready=True, supported_claims=supported, repaired_answer=answer)


class EvidenceReviewer:
    """Delegates semantic claim review to DeepSeek when available, with safe local fallback."""

    def __init__(self, client: Any | None = None):
        self.client = client

    def review(self, *, goal: str, answer: str, claims: list[str], observations: list[dict]) -> EvidenceReview:
        local_result = review_candidate(answer, claims, observations)
        if local_result.ready:
            return local_result
        if self.client is not None and hasattr(self.client, "review_evidence"):
            try:
                result = self.client.review_evidence(goal=goal, answer=answer, claims=claims, observations=observations)
                return result if isinstance(result, EvidenceReview) else EvidenceReview.model_validate(result)
            except Exception:
                pass
        return local_result
