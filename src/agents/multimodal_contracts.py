"""Shared contracts for multimodal customer-support planning and evidence.

The adapters in this module intentionally accept legacy dictionaries so existing
skills can be migrated incrementally without losing trace compatibility.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


EVIDENCE_SOURCE_TYPES = {
    "visual",
    "ocr",
    "user_reported",
    "local_kb",
    "official_site",
    "official_community",
    "authoritative_standard",
    "ship_tool",
    "browser",
    "public_web",
}


@dataclass(frozen=True)
class EvidenceItem:
    source_type: str
    source_name: str = ""
    claim: str = ""
    snippet: str = ""
    url: str = ""
    authority: float = 0.0
    relevance: float = 0.0
    verified: bool = False
    supports: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def normalize_evidence_item(value: dict[str, Any] | EvidenceItem, *, claim: str = "") -> dict[str, Any]:
    """Return a complete EvidenceItem-compatible dictionary without inventing facts."""
    raw = value.to_dict() if isinstance(value, EvidenceItem) else dict(value or {})
    source_type = str(raw.get("source_type") or "public_web").strip().lower()
    if source_type not in EVIDENCE_SOURCE_TYPES:
        source_type = "public_web"
    snippet = str(raw.get("snippet") or raw.get("summary") or "").strip()
    resolved_claim = str(raw.get("claim") or claim or raw.get("purpose") or "").strip()
    verified = bool(raw.get("verified"))
    if not raw.get("verified"):
        # A search-result domain or snippet is not evidence verification. Only
        # tools that return primary data/body content can establish it by
        # default; official/local sources must set ``verified`` explicitly.
        verified = source_type in {"ship_tool", "browser"} and bool(snippet)
    return {
        **raw,
        "source_type": source_type,
        "source_name": str(raw.get("source_name") or "").strip(),
        "claim": resolved_claim,
        "snippet": snippet,
        "url": str(raw.get("url") or "").strip(),
        "authority": float(raw.get("authority") or 0.0),
        "relevance": float(raw.get("relevance") or 0.0),
        "verified": verified,
        "supports": [str(item) for item in list(raw.get("supports") or []) if str(item)],
        "conflicts": [str(item) for item in list(raw.get("conflicts") or []) if str(item)],
    }


def normalize_evidence_items(values: list[dict[str, Any] | EvidenceItem] | None, *, required_claims: list[str] | None = None) -> list[dict[str, Any]]:
    default_claim = "；".join(str(item) for item in list(required_claims or []) if str(item))
    return [normalize_evidence_item(item, claim=default_claim) for item in list(values or [])]


def evidence_coverage(items: list[dict[str, Any] | EvidenceItem] | None, required_claims: list[str] | None = None) -> dict[str, Any]:
    normalized = normalize_evidence_items(items, required_claims=required_claims)
    claims = [str(item) for item in list(required_claims or []) if str(item)]
    verified_claims = [item["claim"] for item in normalized if item["verified"] and item["claim"]]
    covered = [
        claim
        for claim in claims
        if any(
            claim in str(item.get("claim") or "")
            or claim in list(item.get("supports") or [])
            for item in normalized
            if item["verified"]
        )
    ]
    return {
        "required_claims": claims,
        "covered_claims": covered,
        "missing_claims": [claim for claim in claims if claim not in covered],
        "verified_item_count": sum(1 for item in normalized if item["verified"]),
        "item_count": len(normalized),
    }
