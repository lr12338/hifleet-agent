from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.sync_hifleet_skills import REPOSITORY, inspect_checkout, last_known_good, update_lock


def _candidate_checkout(tmp_path: Path, *, extra_skill_text: str = "") -> Path:
    candidate = tmp_path / "candidate"
    (candidate / "references").mkdir(parents=True)
    (candidate / "scripts").mkdir()
    (candidate / "SKILL.md").write_text(
        "---\nversion: 9.9.9\nrequiredEnv:\n  - HIFLEET_API_KEY\nsource: https://api.hifleet.com\n---\n"
        + extra_skill_text,
        encoding="utf-8",
    )
    (candidate / "references" / "skills_index.md").write_text("# Index\n", encoding="utf-8")
    (candidate / "scripts" / "get_position.py").write_text("def fetch():\n    return {}\n", encoding="utf-8")
    return candidate


def test_candidate_discovery_only_approves_reviewed_read_only_scripts(tmp_path: Path) -> None:
    candidate = _candidate_checkout(tmp_path)
    (candidate / "scripts" / "open_console.py").write_text("def open_console():\n    return None\n", encoding="utf-8")

    inspection = inspect_checkout(candidate, repository=REPOSITORY, commit="a" * 40)

    assert inspection["candidate_status"] == "validated"
    assert inspection["approved_read_only_capabilities"] == ["get_position"]
    assert inspection["review_required_capabilities"] == ["open_console"]
    assert inspection["contract_checks"]["python_scripts_compile"] == "passed"


def test_candidate_rejects_unapproved_script_api_host(tmp_path: Path) -> None:
    candidate = _candidate_checkout(tmp_path, extra_skill_text="See https://evil.example/api for more.\n")

    with pytest.raises(RuntimeError, match="candidate_unapproved_api_hosts:evil.example"):
        inspect_checkout(candidate, repository=REPOSITORY, commit="b" * 40)


def test_failed_candidate_inspection_preserves_last_known_good_lock(tmp_path: Path) -> None:
    lock_path = tmp_path / "skills-lock.json"
    lock_payload = {
        "version": 1,
        "skills": {
            "hifleet-skills": {
                "source": "charleiWang/hifleet-skills",
                "version": "0.3.21",
                "commit": "c" * 40,
                "lastKnownGood": "c" * 40,
                "contentHash": "old-hash",
            }
        },
    }
    lock_path.write_text(json.dumps(lock_payload), encoding="utf-8")
    original = lock_path.read_text(encoding="utf-8")
    candidate = _candidate_checkout(tmp_path, extra_skill_text="https://untrusted.example\n")

    with pytest.raises(RuntimeError, match="candidate_unapproved_api_hosts:untrusted.example"):
        inspect_checkout(candidate, repository=REPOSITORY, commit="d" * 40)

    assert lock_path.read_text(encoding="utf-8") == original
    assert last_known_good(lock_path)["commit"] == "c" * 40


def test_lock_updates_only_with_validated_candidate_metadata(tmp_path: Path) -> None:
    lock_path = tmp_path / "skills-lock.json"
    lock_path.write_text(json.dumps({"version": 1, "skills": {}}), encoding="utf-8")
    candidate = _candidate_checkout(tmp_path)
    inspection = inspect_checkout(candidate, repository=REPOSITORY, commit="e" * 40)

    update_lock(lock_path, inspection)

    record = json.loads(lock_path.read_text(encoding="utf-8"))["skills"]["hifleet-skills"]
    assert record["lastKnownGood"] == "e" * 40
    assert record["approvedReadOnlyCapabilities"] == ["get_position"]
    assert record["reviewRequiredCapabilities"] == []


def test_lock_rejects_unvalidated_candidate_metadata(tmp_path: Path) -> None:
    lock_path = tmp_path / "skills-lock.json"
    lock_path.write_text(json.dumps({"version": 1, "skills": {}}), encoding="utf-8")

    with pytest.raises(RuntimeError, match="refusing_to_lock_unvalidated_candidate"):
        update_lock(lock_path, {"candidate_status": "validation_failed"})
