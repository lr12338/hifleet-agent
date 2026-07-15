from __future__ import annotations

import hashlib
import hmac
import os
import time
from typing import Any

from .contracts import Observation, WriteProposal


class ShipUpdateGate:
    """Two-phase write gate. It intentionally keeps commit closed by default."""

    def __init__(self, *, enabled: bool = False, secret: str | None = None, ttl_seconds: int = 600, executor: Any | None = None):
        self.enabled = enabled
        self.secret = (secret or os.getenv("CUSTOMER_CESHI_V2_CONFIRMATION_SECRET") or "customer-ceshi-v2-disabled").encode()
        self.ttl_seconds = ttl_seconds
        self.executor = executor
        self._issued: dict[str, dict[str, Any]] = {}
        self._committed: dict[str, Observation] = {}

    def prepare(self, proposal: WriteProposal, *, user_id: str, session_id: str, profile_id: str) -> Observation:
        if not self.enabled:
            return Observation(status="forbidden", capability="prepare_ship_update", warnings=["Ship writes are disabled for customer_ceshi_v2."], retry_allowed=False)
        required = ["mmsi"]
        missing = [field for field in required if not str(proposal.fields.get(field, "")).strip()]
        unsafe = [name for name, source in proposal.field_sources.items() if source.startswith("media:") and str(proposal.fields.get(name, "")) and name in {"mmsi", "lon", "lat", "updatetime"}]
        if missing or unsafe:
            return Observation(status="invalid_input", capability="prepare_ship_update", warnings=[f"Missing required fields: {', '.join(missing)}" if missing else "Low-confidence media fields require user confirmation."], retry_allowed=False)
        expiry = int(time.time()) + self.ttl_seconds
        payload = f"{user_id}|{session_id}|{profile_id}|{proposal.operation}|{sorted(proposal.fields.items())}|{expiry}"
        token = hmac.new(self.secret, payload.encode(), hashlib.sha256).hexdigest()
        self._issued[token] = {"user_id": user_id, "session_id": session_id, "profile_id": profile_id, "proposal": proposal, "expiry": expiry}
        return Observation(status="success", capability="prepare_ship_update", facts=["A ship-update proposal is ready for explicit confirmation."], data={"confirmation_token": token, "expires_at": expiry, "summary": {"operation": proposal.operation, "fields": proposal.fields}}, retry_allowed=False)

    def commit(self, token: str, *, user_id: str, session_id: str, profile_id: str) -> Observation:
        if token in self._committed:
            return self._committed[token]
        issued = self._issued.get(token)
        if not issued or issued["expiry"] < time.time() or (issued["user_id"], issued["session_id"], issued["profile_id"]) != (user_id, session_id, profile_id):
            return Observation(status="forbidden", capability="commit_ship_update", warnings=["Invalid, expired, or cross-session confirmation token."], retry_allowed=False)
        if not self.enabled or self.executor is None:
            return Observation(status="forbidden", capability="commit_ship_update", warnings=["Ship writes are disabled for customer_ceshi_v2."], retry_allowed=False)
        result = self.executor(issued["proposal"])
        if not isinstance(result, Observation) or result.status != "success":
            return Observation(status="upstream_error", capability="commit_ship_update", warnings=["The write service did not explicitly confirm success."], retry_allowed=False)
        self._committed[token] = result
        return result
