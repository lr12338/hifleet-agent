import json

from scripts.probe_customer_ceshi_responses import _CAPABILITIES, _write_artifacts


def test_capability_artifacts_never_promote_unexecuted_features(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_artifacts({"status": "SKIPPED", "reason": "credentials unavailable"})
    payload = json.loads((tmp_path / "artifacts" / "customer_ceshi_capabilities.json").read_text(encoding="utf-8"))
    assert set(payload["statuses"]) == set(_CAPABILITIES)
    assert all(value != "PASSED" for value in payload["statuses"].values())
    assert "credentials unavailable" in payload["probe_summary"]["reason"]
