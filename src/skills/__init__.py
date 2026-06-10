"""
Skills模块 — 领域技能包

每个Skill包含：
  - SKILL.md: 技能定义（角色、规则、工具用法、输出格式）
  - tool.py: 技能工具（可选，部分Skill复用tools/下的工具）
  - __init__.py: 模块导出

技能清单：
  - hifleet_ship_service: 船舶服务（查询/更新船位、档案、PSC）
  - knowledge_qa: 知识问答（FAQ、Wiki、官网、网络搜索）
"""

from skills.skill_loader import SkillLoader, Skill

__all__ = ["SkillLoader", "Skill"]
