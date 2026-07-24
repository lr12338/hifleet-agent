"""V2 safe-constrained fallback used when the V2 loader/registry fails.

This never loads legacy ``skills.*``. When the V2 registry cannot assemble a
bundle (for example a missing or corrupted lock/manifest), the customer_ceshi
link degrades to a no-tool, conservative-prompt bundle instead of falling back
to the legacy skill system.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from skills_v2.core.descriptors import SkillRuntimeBundle


SAFE_CONSTRAINED_PROMPT = """# Shared Skills V2 - Safe Constrained Mode

The V2 Skill registry could not be loaded safely. Until the V2 lock and manifests
are restored, operate under these constraints:

- Do not call any tool; do not claim a write or data lookup succeeded.
- Answer conservatively from already-established facts only.
- If a question cannot be answered safely, ask the user for the key detail or
  suggest retrying shortly.
- Never fabricate vessel data, positions, or update results.
"""


def build_safe_constrained_bundle(workspace_path: str | Path | None = None) -> SkillRuntimeBundle:
    """Return a no-tool, conservative V2 bundle for degraded customer_ceshi runs."""
    return SkillRuntimeBundle(
        profile_id="customer_ceshi",
        mode="safe_constrained",
        tools=tuple(),
        descriptors=tuple(),
        prompt=SAFE_CONSTRAINED_PROMPT,
        source_versions={},
    )


def is_safe_constrained(bundle: Any) -> bool:
    return getattr(bundle, "mode", "") == "safe_constrained"
