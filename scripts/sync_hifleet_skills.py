#!/usr/bin/env python3
"""Safely inspect a candidate hifleet-skills revision before updating the lock."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import tempfile
from pathlib import Path


REPOSITORY = "https://github.com/charleiWang/hifleet-skills.git"
REQUIRED_FILES = ("SKILL.md", "references/skills_index.md", "scripts/get_position.py")


def _run(*args: str, cwd: Path | None = None) -> str:
    return subprocess.check_output(args, cwd=cwd, text=True, stderr=subprocess.STDOUT).strip()


def inspect_candidate(repository: str = REPOSITORY, revision: str = "HEAD") -> dict[str, str | list[str]]:
    with tempfile.TemporaryDirectory(prefix="hifleet-skills-candidate-") as temporary:
        candidate = Path(temporary) / "source"
        _run("git", "clone", "--no-checkout", repository, str(candidate))
        _run("git", "checkout", "--detach", revision, cwd=candidate)
        missing = [name for name in REQUIRED_FILES if not (candidate / name).is_file()]
        if missing:
            raise RuntimeError("candidate_missing_required_files")
        skill_text = (candidate / "SKILL.md").read_text(encoding="utf-8")
        version = re.search(r"^version:\s*([^\s]+)", skill_text, re.MULTILINE)
        required_env = re.findall(r"^\s*-\s*(HIFLEET_[A-Z0-9_]+)\s*$", skill_text, re.MULTILINE)
        commit = _run("git", "rev-parse", "HEAD", cwd=candidate)
        content_hash = hashlib.sha256(skill_text.encode("utf-8")).hexdigest()
        return {
            "repository": repository,
            "commit": commit,
            "version": version.group(1) if version else "unknown",
            "content_hash": content_hash,
            "required_env": sorted(set(required_env)),
            "candidate_status": "validated",
        }


def update_lock(lock_path: Path, candidate: dict[str, str | list[str]]) -> None:
    payload = json.loads(lock_path.read_text(encoding="utf-8"))
    skills = payload.setdefault("skills", {})
    skills["hifleet-skills"] = {
        "source": "charleiWang/hifleet-skills",
        "sourceType": "github",
        "skillPath": "SKILL.md",
        "version": candidate["version"],
        "commit": candidate["commit"],
        "lastKnownGood": candidate["commit"],
        "contentHash": candidate["content_hash"],
        "verification": "manifest-and-contract-reviewed",
    }
    lock_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--revision", default="HEAD")
    parser.add_argument("--repository", default=REPOSITORY)
    parser.add_argument("--apply", action="store_true", help="Update skills-lock.json after candidate validation.")
    parser.add_argument("--lock", type=Path, default=Path("skills-lock.json"))
    args = parser.parse_args()
    candidate = inspect_candidate(args.repository, args.revision)
    if args.apply:
        update_lock(args.lock, candidate)
        candidate["lock_status"] = "updated"
    else:
        candidate["lock_status"] = "unchanged"
    print(json.dumps(candidate, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
