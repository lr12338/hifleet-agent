#!/usr/bin/env python3
"""Safely inspect a candidate hifleet-skills revision before updating the lock.

The production runtime never clones this repository. This script stages a candidate
in a temporary directory, validates the static contract, and only updates the lock
when every required check succeeds. A failed inspection leaves the previous lock
(and therefore its last-known-good revision) unchanged.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import textwrap

import yaml
import subprocess
import tempfile
from pathlib import Path
from typing import Any


REPOSITORY = "https://github.com/charleiWang/hifleet-skills.git"
TRUSTED_REPOSITORIES = {REPOSITORY.rstrip("/"), REPOSITORY.removesuffix(".git")}
REQUIRED_FILES = ("SKILL.md", "references/skills_index.md", "scripts/get_position.py")
ALLOWED_REQUIRED_ENV_PREFIXES = ("HIFLEET_",)
ALLOWED_API_HOSTS = {"api.hifleet.com", "skills.hifleet.com"}
APPROVED_READ_ONLY_SCRIPTS = {
    "get_archive",
    "get_area_traffic",
    "get_areas",
    "get_avoidredsea_traffic",
    "get_casualty",
    "get_maritime_penalty",
    "get_port",
    "get_position",
    "get_psc",
    "get_psc_anomalies",
    "get_psc_openclaw_stats",
    "get_sanction",
    "get_strait_traffic",
}
URL_HOST_PATTERN = re.compile(r"https?://([A-Za-z0-9.-]+)(?::\d+)?")
VERSION_PATTERN = re.compile(r"^version:\s*([^\s]+)", re.MULTILINE)
REQUIRED_ENV_PATTERN = re.compile(r"^\s*-\s*([A-Z][A-Z0-9_]+)\s*$", re.MULTILINE)


def _run(*args: str, cwd: Path | None = None) -> str:
    return subprocess.check_output(args, cwd=cwd, text=True, stderr=subprocess.STDOUT).strip()


def _normalize_repository(repository: str) -> str:
    return repository.rstrip("/").removesuffix(".git")


def _is_trusted_repository(repository: str) -> bool:
    normalized = _normalize_repository(repository)
    return normalized in {_normalize_repository(item) for item in TRUSTED_REPOSITORIES}


def _content_hash(candidate: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(path for path in candidate.rglob("*") if path.is_file() and ".git" not in path.parts):
        relative = path.relative_to(candidate).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(len(path.read_bytes()).to_bytes(8, "big"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _script_names(candidate: Path) -> list[str]:
    scripts_dir = candidate / "scripts"
    return sorted(path.stem for path in scripts_dir.glob("*.py") if path.name != "__init__.py")


def _python_contract_errors(candidate: Path) -> list[str]:
    errors: list[str] = []
    for path in sorted((candidate / "scripts").glob("*.py")):
        try:
            compile(path.read_text(encoding="utf-8"), str(path), "exec")
        except (OSError, SyntaxError, UnicodeDecodeError) as exc:
            errors.append(f"python_contract_invalid:{path.relative_to(candidate)}:{exc.__class__.__name__}")
    return errors


def _api_hosts(candidate: Path) -> list[str]:
    hosts: set[str] = set()
    for path in [candidate / "SKILL.md", *(candidate / "scripts").glob("*.py")]:
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for host in URL_HOST_PATTERN.findall(text):
            hosts.add(host.lower())
    return sorted(hosts)


def _is_allowed_api_host(host: str) -> bool:
    return host in ALLOWED_API_HOSTS or host.endswith(".hifleet.com")


def _validated_required_env(skill_text: str) -> list[str]:
    required_env = sorted(set(REQUIRED_ENV_PATTERN.findall(skill_text)))
    unexpected = [name for name in required_env if not name.startswith(ALLOWED_REQUIRED_ENV_PREFIXES)]
    if unexpected:
        raise RuntimeError(f"candidate_unapproved_required_env:{','.join(unexpected)}")
    return required_env


def inspect_checkout(candidate: Path, *, repository: str, commit: str) -> dict[str, Any]:
    """Validate one checked-out candidate without touching the active lock."""
    if not _is_trusted_repository(repository):
        raise RuntimeError("candidate_untrusted_repository")
    missing = [name for name in REQUIRED_FILES if not (candidate / name).is_file()]
    if missing:
        raise RuntimeError(f"candidate_missing_required_files:{','.join(missing)}")

    skill_text = (candidate / "SKILL.md").read_text(encoding="utf-8")
    version = VERSION_PATTERN.search(skill_text)
    if version is None:
        raise RuntimeError("candidate_missing_skill_version")
    required_env = _validated_required_env(skill_text)
    script_names = _script_names(candidate)
    contract_errors = _python_contract_errors(candidate)
    if contract_errors:
        raise RuntimeError(";".join(contract_errors))

    api_hosts = _api_hosts(candidate)
    unapproved_hosts = [host for host in api_hosts if not _is_allowed_api_host(host)]
    if unapproved_hosts:
        raise RuntimeError(f"candidate_unapproved_api_hosts:{','.join(unapproved_hosts)}")

    approved_capabilities = [name for name in script_names if name in APPROVED_READ_ONLY_SCRIPTS]
    review_required_capabilities = [name for name in script_names if name not in APPROVED_READ_ONLY_SCRIPTS]
    return {
        "repository": repository,
        "commit": commit,
        "version": version.group(1),
        "content_hash": _content_hash(candidate),
        "required_env": required_env,
        "api_hosts": api_hosts,
        "contract_checks": {"python_scripts_compile": "passed", "required_files": "passed"},
        "discovered_capabilities": script_names,
        "approved_read_only_capabilities": approved_capabilities,
        "review_required_capabilities": review_required_capabilities,
        "candidate_status": "validated",
    }


def inspect_candidate(repository: str = REPOSITORY, revision: str = "HEAD") -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="hifleet-skills-candidate-") as temporary:
        candidate = Path(temporary) / "source"
        _run("git", "clone", "--no-checkout", repository, str(candidate))
        _run("git", "checkout", "--detach", revision, cwd=candidate)
        commit = _run("git", "rev-parse", "HEAD", cwd=candidate)
        return inspect_checkout(candidate, repository=repository, commit=commit)


def last_known_good(lock_path: Path) -> dict[str, str]:
    payload = json.loads(lock_path.read_text(encoding="utf-8"))
    record = payload.get("skills", {}).get("hifleet-skills", {})
    commit = str(record.get("lastKnownGood") or record.get("commit") or "")
    if not commit:
        raise RuntimeError("lock_missing_last_known_good")
    return {
        "repository": str(record.get("source") or ""),
        "version": str(record.get("version") or ""),
        "commit": commit,
        "content_hash": str(record.get("contentHash") or ""),
    }


MANIFEST_PATH = Path("src/skills/hifleet_data/manifest.yaml")
SKILL_PROMPT_PATH = Path("src/skills/hifleet_data/SKILL.md")
UPSTREAM_REPOSITORY_URL = "https://github.com/charleiWang/hifleet-skills"


def _manifest_capabilities(manifest_path: Path) -> list[dict[str, Any]]:
    payload = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    return list(payload.get("capabilities") or [])


def update_manifest(manifest_path: Path, candidate: dict[str, Any]) -> None:
    """Keep the committed manifest snapshot in sync with the reviewed lock record.

    Only the upstream version and commit change; the project-controlled adapter
    capability list and mapping are never auto-extended from upstream.
    """
    if candidate.get("candidate_status") != "validated":
        raise RuntimeError("refusing_to_sync_unvalidated_manifest")
    text = manifest_path.read_text(encoding="utf-8")
    text = re.sub(r"(?m)^skill_version:.*$", f"skill_version: {candidate['version']}", text)
    if re.search(r"(?m)^upstream_commit:.*$", text):
        text = re.sub(r"(?m)^upstream_commit:.*$", f"upstream_commit: {candidate['commit']}", text)
    else:
        text = re.sub(r"(?m)^skill_version:.*$", rf"\g<0>\nupstream_commit: {candidate['commit']}", text)
    manifest_path.write_text(text, encoding="utf-8")


def render_hifleet_skill_prompt(candidate: dict[str, Any], capabilities: list[dict[str, Any]]) -> str:
    """Render the reviewed hifleet_data SKILL.md from the lock record + mapping."""
    approved = textwrap.fill(", ".join(candidate.get("approved_read_only_capabilities") or []), width=80)
    review_required = ", ".join(candidate.get("review_required_capabilities") or [])
    required_env = ", ".join(candidate.get("required_env") or [])
    rows = []
    for cap in capabilities:
        tool = str(cap.get("tool_name") or cap.get("id") or "")
        upstream = str(cap.get("upstream_capability") or "")
        label = upstream if upstream else "(project adapter)"
        rows.append(f"| {tool} | {label} | {cap.get('description', '')} |")
    mapping = "\n".join(rows)
    review_section = review_required or "(none)"
    return f"""# HiFleet Data V2

You are using a locked, read-only data adapter for verified HiFleet vessel and
traffic data. State only facts that are directly supported by the returned data.
A successful HTTP/tool response alone never establishes that a customer-facing
conclusion is semantically correct; always include the tool result's version
metadata in trace data.

Do not expose account, billing, registration, purchase, contact-unlock, console,
charter-enrichment, or any other upstream write/review-required capability. Only
the approved read-only capabilities listed below are available; everything else
the upstream repository may contain must remain hidden.

## Conservative data rules

- Return vessel identity (ship name, MMSI/IMO), the queried data item, and its
  data time. When there is no result, state the query condition or data latency;
  never fabricate a record.
- Trajectory queries must respect the configured day limit; narrow the range
  instead of repeating an identical over-span request.
- Distinguish observed data, data latency, and unsupported product claims. Use
  hedged language ("可能/通常/不一定") only when evidence supports it.
- Never infer fields that the tool did not return, and never let a weak or
  conflicting web result override authoritative HiFleet data.

## Upstream provenance (single source of truth: skills-lock.json)

- upstream_repository: {UPSTREAM_REPOSITORY_URL}
- version: {candidate['version']}
- commit: {candidate['commit']}
- contentHash: {candidate['content_hash']}
- requiredEnv: {required_env}
- verification: static-contract-reviewed

## Approved read-only upstream capabilities

{approved}

## Review-required / rejected upstream capabilities (never auto-exposed)

{review_section}

## Capability to adapter tool mapping

| adapter tool | upstream capability | description |
| --- | --- | --- |
{mapping}

"(project adapter)" marks HiFleet-API-backed tools that this project reviews and
exposes directly; they are not auto-derived from a new upstream script and any
new upstream capability remains review-required until explicitly mapped here.
"""


def update_skill_prompt(skill_md_path: Path, manifest_path: Path, candidate: dict[str, Any]) -> None:
    """Regenerate the reviewed SKILL.md so prompt, manifest and lock share one record."""
    if candidate.get("candidate_status") != "validated":
        raise RuntimeError("refusing_to_sync_unvalidated_prompt")
    capabilities = _manifest_capabilities(manifest_path)
    skill_md_path.write_text(render_hifleet_skill_prompt(candidate, capabilities), encoding="utf-8")


def update_lock(lock_path: Path, candidate: dict[str, Any]) -> None:
    if candidate.get("candidate_status") != "validated":
        raise RuntimeError("refusing_to_lock_unvalidated_candidate")
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
        "requiredEnv": candidate["required_env"],
        "approvedReadOnlyCapabilities": candidate["approved_read_only_capabilities"],
        "reviewRequiredCapabilities": candidate["review_required_capabilities"],
        "verification": "static-contract-reviewed",
    }
    lock_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--revision", default="HEAD")
    parser.add_argument("--repository", default=REPOSITORY)
    parser.add_argument("--apply", action="store_true", help="Update skills-lock.json after candidate validation.")
    parser.add_argument("--lock", type=Path, default=Path("skills-lock.json"))
    parser.add_argument("--manifest", type=Path, default=MANIFEST_PATH)
    parser.add_argument("--skill-prompt", type=Path, default=SKILL_PROMPT_PATH)
    args = parser.parse_args()
    try:
        candidate = inspect_candidate(args.repository, args.revision)
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        result: dict[str, Any] = {
            "candidate_status": "validation_failed",
            "error": str(exc),
            "lock_status": "unchanged",
        }
        if args.lock.is_file():
            result["fallback"] = last_known_good(args.lock)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 2
    if args.apply:
        update_lock(args.lock, candidate)
        update_manifest(args.manifest, candidate)
        update_skill_prompt(args.skill_prompt, args.manifest, candidate)
        candidate["lock_status"] = "updated"
        candidate["manifest_status"] = "updated"
        candidate["skill_prompt_status"] = "updated"
    else:
        candidate["lock_status"] = "unchanged"
    print(json.dumps(candidate, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
