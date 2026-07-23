"""Shared Skills V2 definitions and runtime helpers.

This package is intentionally separate from the legacy ``SkillLoader`` so a
profile can be migrated or rolled back without mutating the legacy runtime.
"""

from .contracts import SkillManifest, ToolDescriptor
from .policy import customer_support_shadow_enabled, resolve_skill_runtime
from .registry import SharedSkillRegistry

__all__ = ["SharedSkillRegistry", "SkillManifest", "ToolDescriptor", "customer_support_shadow_enabled", "resolve_skill_runtime"]
