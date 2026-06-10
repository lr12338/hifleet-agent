"""Deprecated compatibility wrapper for the unified knowledge QA search tool.

The active implementation lives in ``skills.knowledge_qa.tools`` and is loaded
through the profile-aware SkillLoader. Keep this module only for legacy imports.
"""

from skills.knowledge_qa.tools import smart_search

__all__ = ["smart_search"]
