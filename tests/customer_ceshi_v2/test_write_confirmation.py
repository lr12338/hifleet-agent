from agents.customer_ceshi_v2.actions import ShipUpdateGate
from agents.customer_ceshi_v2.contracts import Observation, WriteProposal


def test_write_gate_rejects_commit_when_disabled():
    gate = ShipUpdateGate(enabled=False)
    proposal = WriteProposal(operation="ship_static_info", fields={"mmsi": "123456789", "ship_name": "TEST"})

    prepared = gate.prepare(proposal, user_id="u", session_id="s", profile_id="customer_ceshi")

    assert prepared.status == "forbidden"


def test_write_gate_binds_token_to_profile_session_and_is_idempotent():
    calls = []
    gate = ShipUpdateGate(enabled=True, secret="test", executor=lambda proposal: calls.append(proposal) or Observation(status="success", capability="commit_ship_update", facts=["updated"]))
    proposal = WriteProposal(operation="ship_static_info", fields={"mmsi": "123456789", "ship_name": "TEST"})
    prepared = gate.prepare(proposal, user_id="u", session_id="s", profile_id="customer_ceshi")
    token = prepared.data["confirmation_token"]

    assert gate.commit(token, user_id="u", session_id="other", profile_id="customer_ceshi").status == "forbidden"
    assert gate.commit(token, user_id="u", session_id="s", profile_id="customer_ceshi").status == "success"
    assert gate.commit(token, user_id="u", session_id="s", profile_id="customer_ceshi").status == "success"
    assert len(calls) == 1
