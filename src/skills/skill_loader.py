"""
Skill加载器 — 动态加载技能包，按需组装Prompt和工具

核心职责：
  1. 根据意图类别加载对应的Skill
  2. 读取SKILL.md组装技能专属Prompt
  3. 绑定技能专属工具
  4. 缓存已加载的Skill，避免重复IO
"""

import os
import json
import logging
from pathlib import Path
from typing import List, Optional
from dataclasses import dataclass, field

from langchain.tools import BaseTool

logger = logging.getLogger(__name__)

# Skills根目录
SKILLS_DIR = Path(__file__).parent

# 基础Prompt（所有Skill共享）
BASE_PROMPT_FILE = Path(__file__).parent.parent.parent / "config" / "system_prompt_base.md"


@dataclass
class Skill:
    """技能包定义"""
    name: str
    prompt: str                    # SKILL.md内容
    tools: List[BaseTool] = field(default_factory=list)
    description: str = ""


class SkillLoader:
    """
    技能加载器 — 按需加载，缓存复用

    用法：
        skill = SkillLoader.load("ship")
        # skill.prompt → 技能专属Prompt
        # skill.tools → 技能专属工具列表
    """

    # 意图 → Skill目录名映射
    INTENT_MAP = {
        "knowledge": "knowledge_qa",
        "ship": "hifleet_ship_service",
    }

    # 缓存
    _cache: dict = {}

    @classmethod
    def load(cls, intent: str) -> Skill:
        """
        根据意图加载技能包

        Args:
            intent: 意图类别 (knowledge/ship/lead/summary/human)

        Returns:
            Skill对象（prompt + tools）
        """
        if intent in cls._cache:
            return cls._cache[intent]

        skill_name = cls.INTENT_MAP.get(intent, "knowledge_qa")
        skill_dir = SKILLS_DIR / skill_name

        # 读取SKILL.md
        skill_md = skill_dir / "SKILL.md"
        if skill_md.exists():
            prompt = skill_md.read_text(encoding="utf-8")
        else:
            logger.warning(f"SKILL.md not found for {skill_name}, using fallback")
            prompt = f"你是Hifleet智能客服助手，负责处理{intent}类问题。"

        # 加载工具
        tools = cls._load_tools(skill_name)

        skill = Skill(
            name=skill_name,
            prompt=prompt,
            tools=tools,
            description=prompt.split("\n")[0] if prompt else "",
        )

        cls._cache[intent] = skill
        logger.info(f"Loaded skill: {skill_name} (intent={intent}, tools={len(tools)})")
        return skill

    @classmethod
    def _load_tools(cls, skill_name: str) -> List[BaseTool]:
        """加载技能专属工具 — 使用importlib动态导入"""
        import importlib

        # 目录名 → 导入的工具列表
        TOOL_MAP = {
            "hifleet_ship_service": [
                "ship_search",
                "get_ship_position",
                "get_ship_archive",
                "get_psc_records",
                "get_area_traffic",
                "get_strait_traffic",
                "upload_ship_position",
                "update_ship_static_info",
            ],
            "knowledge_qa": ["smart_search"],
        }

        if skill_name not in TOOL_MAP:
            return []

        tool_names = TOOL_MAP[skill_name]
        if not tool_names:
            return []

        try:
            module = importlib.import_module(f"skills.{skill_name}.tools")
            tools = []
            for tool_name in tool_names:
                tool = getattr(module, tool_name, None)
                if tool is not None:
                    tools.append(tool)
                else:
                    logger.warning(f"Tool '{tool_name}' not found in skills.{skill_name}.tools")
            return tools

        except ImportError as e:
            logger.error(f"Failed to load tools for {skill_name}: {e}")
            return []

    @classmethod
    def load_base_prompt(cls) -> str:
        """加载基础Prompt（所有Skill共享的通用规则）"""
        if BASE_PROMPT_FILE.exists():
            return BASE_PROMPT_FILE.read_text(encoding="utf-8")
        return ""

    @classmethod
    def build_full_prompt(cls, intent: str = None) -> str:
        """
        构建完整Prompt = 基础Prompt + 技能Prompt

        Args:
            intent: 意图类别。None时加载所有Skill的Prompt（默认）

        Returns:
            组装后的完整Prompt
        """
        base = cls.load_base_prompt()

        if intent:
            # 按意图加载单个Skill
            skill = cls.load(intent)
            return f"{base}\n\n---\n\n{skill.prompt}"
        else:
            # 加载所有Skill的Prompt（默认行为，用于单Agent模式）
            parts = [base]
            seen = set()
            for skill_name in cls.INTENT_MAP.values():
                if skill_name in seen:
                    continue
                seen.add(skill_name)
                skill_md = SKILLS_DIR / skill_name / "SKILL.md"
                if skill_md.exists():
                    skill_prompt = skill_md.read_text(encoding="utf-8")
                    if skill_prompt.strip():
                        parts.append(skill_prompt)
            return "\n\n---\n\n".join(parts)

    @classmethod
    def get_all_tools(cls) -> List[BaseTool]:
        """获取所有技能的工具列表（用于ToolNode）"""
        all_tools = []
        seen = set()
        for intent in cls.INTENT_MAP:
            skill = cls.load(intent)
            for tool in skill.tools:
                if tool.name not in seen:
                    all_tools.append(tool)
                    seen.add(tool.name)
        consistency = cls.validate_registry_consistency()
        if consistency["missing_in_loader"] or consistency["extra_in_loader"]:
            logger.warning(f"Tool registry mismatch: {consistency}")
        return all_tools

    @classmethod
    def get_tools_by_intent(cls, intent: str) -> List[BaseTool]:
        """
        按意图获取工具集合。
        仅支持 knowledge / ship，两者以外默认回退 knowledge。
        """
        normalized_intent = (intent or "").strip().lower()
        if normalized_intent == "ship":
            skill = cls.load("ship")
            return skill.tools
        if normalized_intent == "knowledge":
            skill = cls.load("knowledge")
            return skill.tools

        logger.warning(f"Unknown intent '{intent}', fallback to knowledge tools")
        return cls.load("knowledge").tools

    @classmethod
    def validate_registry_consistency(cls) -> dict:
        """
        校验 config/agent_llm_config.json 与 SkillLoader 实际加载工具的一致性。
        """
        workspace_path = os.getenv("COZE_WORKSPACE_PATH")
        if not workspace_path:
            workspace_path = str(Path(__file__).parent.parent.parent)
        config_path = Path(workspace_path) / "config" / "agent_llm_config.json"

        configured_tools = set()
        if config_path.exists():
            try:
                cfg = json.loads(config_path.read_text(encoding="utf-8"))
                configured_tools = set(cfg.get("tools", []))
            except Exception as e:
                logger.warning(f"Failed to parse tool config {config_path}: {e}")

        loaded_tools = set()
        for intent in cls.INTENT_MAP:
            skill = cls.load(intent)
            for tool in skill.tools:
                loaded_tools.add(tool.name)

        missing_in_loader = sorted(list(configured_tools - loaded_tools))
        extra_in_loader = sorted(list(loaded_tools - configured_tools))
        return {
            "configured_count": len(configured_tools),
            "loaded_count": len(loaded_tools),
            "missing_in_loader": missing_in_loader,
            "extra_in_loader": extra_in_loader,
        }

    @classmethod
    def clear_cache(cls):
        """清除缓存"""
        cls._cache.clear()
