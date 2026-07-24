"""Shared Skills V2 physical-decoupling boundary tests.

These enforce that V2 and legacy skills never cross-import, that customer_ceshi
uses only V2, that customer_support stays legacy, and that the upstream sync
failure modes leave the active snapshot untouched.
"""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

import pytest

from skills_v2.adapters.customer_ceshi import build_customer_ceshi_bundle
from skills_v2.core.loader import available_tool_names
from skills_v2.core.lock_store import lock_path as v2_lock_path

ROOT = Path(__file__).resolve().parents[2]
V2_ROOT = ROOT / "src" / "skills_v2"
CESHI_AGENTS = [ROOT / "src" / "agents" / "customer_ceshi_responses", ROOT / "src" / "agents" / "customer_ceshi_v2"]


def _py_files(d: Path):
    yield from d.rglob("*.py")


def _imports_legacy_skills(path: Path) -> list[str]:
    """Return legacy ``skills.*`` import statements (excluding skills_v2)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "skills" or alias.name.startswith("skills."):
                    violations.append(f"{path.name}: import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if mod == "skills" or mod.startswith("skills."):
                violations.append(f"{path.name}: from {mod} import ...")
    return violations


def test_v2_tree_never_imports_legacy_skills() -> None:
    violations: list[str] = []
    for path in _py_files(V2_ROOT):
        violations.extend(_imports_legacy_skills(path))
    assert violations == [], "V2 imports legacy skills: " + "; ".join(violations)


def test_customer_ceshi_only_imports_skills_v2() -> None:
    violations: list[str] = []
    for d in CESHI_AGENTS:
        for path in _py_files(d):
            violations.extend(_imports_legacy_skills(path))
    assert violations == [], "customer_ceshi imports legacy skills: " + "; ".join(violations)


def test_customer_support_never_imports_skills_v2() -> None:
    violations: list[str] = []
    for path in (ROOT / "src" / "agents").glob("customer_support*.py"):
        text = path.read_text(encoding="utf-8")
        if "skills_v2" in text:
            violations.append(path.name)
    assert violations == [], "customer_support imports skills_v2: " + ", ".join(violations)


def test_tests_skills_v2_do_not_import_legacy_skills() -> None:
    violations: list[str] = []
    for path in (ROOT / "tests" / "skills_v2").rglob("*.py"):
        violations.extend(_imports_legacy_skills(path))
    assert violations == [], "tests/skills_v2 imports legacy skills: " + "; ".join(violations)


def test_v2_loader_failure_uses_safe_constrained_not_legacy() -> None:
    # The safe fallback is a no-tool, conservative V2 bundle that never loads legacy skills.
    from skills_v2.fallback.safe_constrained import build_safe_constrained_bundle, SAFE_CONSTRAINED_PROMPT

    bundle = build_safe_constrained_bundle()
    assert bundle.tools == ()
    assert bundle.descriptors == ()
    assert bundle.mode == "safe_constrained"
    assert "保守" in SAFE_CONSTRAINED_PROMPT or "conservative" in SAFE_CONSTRAINED_PROMPT.lower()
    # The fallback module itself must not import legacy skills.
    assert _imports_legacy_skills(V2_ROOT / "fallback" / "safe_constrained.py") == []


def test_v2_core_passes_without_legacy_skill_loader_in_import_cache() -> None:
    # In a fresh process that imports only V2, the legacy skill_loader must never be loaded.
    import subprocess
    code = (
        "import sys; "
        "from skills_v2.adapters.customer_ceshi import build_customer_ceshi_bundle; "
        "b = build_customer_ceshi_bundle(); "
        "assert b.descriptors, 'no descriptors'; "
        "assert 'skills.skill_loader' not in sys.modules, 'legacy skill_loader leaked'; "
        "assert 'skills.core' not in sys.modules, 'legacy skills.core leaked'; "
        "print('OK')"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env={**__import__("os").environ, "PYTHONPATH": str(ROOT / "src")})
    assert result.returncode == 0, f"V2 core needed legacy skills: {result.stderr}"


def test_v2_lock_is_separate_from_legacy_root_lock() -> None:
    assert v2_lock_path(ROOT) == ROOT / "src" / "skills_v2" / "upstream" / "hifleet_skills" / "lock.json"
    assert v2_lock_path(ROOT) != ROOT / "skills-lock.json"
    assert v2_lock_path(ROOT).is_file()


def test_upstream_apply_failure_leaves_current_and_lkg_unchanged(tmp_path: Path) -> None:
    import scripts.skills_v2.sync_hifleet_skills as S

    lock = tmp_path / "lock.json"
    lock.write_text(json.dumps({"version": 1, "skills": {"hifleet-skills": {"version": "0.3.21", "commit": "oldcommit", "lastKnownGood": "oldcommit", "contentHash": "h"}}}), encoding="utf-8")
    S.LOCK_PATH = lock
    S.MANIFEST_PATH = tmp_path / "manifest.yaml"
    S.MANIFEST_PATH.write_text("skill_version: 0.3.21\nupstream_commit: oldcommit\n", encoding="utf-8")
    S.SKILL_PROMPT_PATH = tmp_path / "SKILL.md"
    S.SKILL_PROMPT_PATH.write_text("oldcommit\n", encoding="utf-8")
    S.CURRENT_DIR = tmp_path / "current"
    S.LAST_KNOWN_GOOD_DIR = tmp_path / "lkg"
    S.CANDIDATES_DIR = tmp_path / "candidates"
    S.REPORT_PATH = tmp_path / "report.md"
    # a non-validated candidate staged for apply
    bad_dir = S.CANDIDATES_DIR / "badcommit"
    bad_dir.mkdir(parents=True)
    (bad_dir / "candidate.json").write_text(json.dumps({"candidate_status": "validation_failed"}), encoding="utf-8")

    class A:
        pass
    a = A()
    a.lock = lock
    a.manifest = S.MANIFEST_PATH
    a.skill_prompt = S.SKILL_PROMPT_PATH
    a.repository = S.REPOSITORY
    a.revision = "badcommit"
    rc = S.cmd_apply(a)
    assert rc == 2
    record = json.loads(lock.read_text(encoding="utf-8"))["skills"]["hifleet-skills"]
    assert record["commit"] == "oldcommit"
    assert record["lastKnownGood"] == "oldcommit"
    assert not S.CURRENT_DIR.exists() or not (S.CURRENT_DIR / "lock.json").is_file()


def test_new_upstream_capability_is_not_exposed_by_default() -> None:
    bundle = build_customer_ceshi_bundle(str(ROOT))
    names = {d.name for d in bundle.descriptors}
    tool_names = set(available_tool_names())
    # a brand-new upstream script that was never mapped stays invisible
    for hidden in ("brand_new_data_feed", "open_console", "charter_contact_dedup"):
        assert hidden not in names
        assert hidden not in tool_names


def test_web_search_skill_exposes_only_web_search() -> None:
    import yaml
    from skills_v2.skills.web_search import adapter as ws_adapter

    manifest = yaml.safe_load((V2_ROOT / "skills" / "web_search" / "manifest.yaml").read_text(encoding="utf-8"))
    caps = [str(c.get("tool_name") or c.get("id")) for c in manifest["capabilities"]]
    assert caps == ["web_search"]
    exported = [name for name in getattr(ws_adapter, "__all__", [])]
    assert exported == ["web_search"]
    forbidden = {"verify_public_page", "agent_browser_deep_search", "web_search_agent_browser"}
    assert forbidden.isdisjoint(set(available_tool_names()))
