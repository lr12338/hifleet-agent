"""
Hifleet智能客服主Agent — Skills + 动态工具加载架构

架构设计：
- 路由Agent（本文件）：薄调度器，只负责意图识别和工具路由
- Skills（src/skills/）：领域知识+工具的封装单元
  - hifleet_ship_service: 船舶查询/更新/PSC
  - knowledge_qa: 知识库检索+增强搜索+深度搜索
- System Prompt: Base Prompt + 各Skill的SKILL.md动态拼接

设计原则：
1. 路由Agent是调度器，不承载业务逻辑
2. 每个Skill是独立的能力单元，包含SKILL.md（知识）+ tools.py（工具）
3. Prompt按需组装，避免22KB全量加载
4. 工具按Skill注册，LLM只需4个工具中选择
5. 写操作直接执行，无需确认
6. 失败不得伪造成功
"""
import os
import json
import logging
import re
from typing import Annotated
from langchain.agents import create_agent
from langchain_openai import ChatOpenAI
from langgraph.graph import MessagesState
from langgraph.graph.message import add_messages
from langchain_core.messages import AnyMessage, AIMessage, HumanMessage
from coze_coding_utils.runtime_ctx.context import default_headers
from storage.memory.memory_saver import get_memory_saver
from skills import SkillLoader
from agents.profiles import (
    AgentProfile,
    PROFILE_HEADER,
    get_current_agent_profile_id,
    get_profile,
    read_profile_prompt,
)

# 配置文件路径
LLM_CONFIG = "config/agent_llm_config.json"
SYSTEM_PROMPT_BASE = "config/system_prompt_base.md"

# 默认保留最近20轮对话（40条消息）
MAX_MESSAGES = 40
DEFAULT_SKILLS = {"hifleet_ship_service", "knowledge_qa"}

logger = logging.getLogger(__name__)


def _windowed_messages(old, new):
    """
    滑动窗口：只保留最近MAX_MESSAGES条消息
    
    去重策略：当AI消息同时包含content和tool_calls时，清除content。
    原因：模型在调用工具时会生成一段文本(content)，工具执行完毕后
    模型会再次生成相同或相似的文本，导致消息历史中出现重复回复。
    清除带tool_calls消息的content，确保最终回复只在工具执行后出现一次。
    """
    merged = add_messages(old, new)
    
    # 去重：AI消息同时有content和tool_calls时，清除content防止重复
    cleaned = []
    for msg in merged:
        if isinstance(msg, AIMessage) and getattr(msg, 'tool_calls', None) and msg.content and isinstance(msg.content, str) and msg.content.strip():
            cleaned.append(AIMessage(
                content="",
                tool_calls=msg.tool_calls,
                id=msg.id,
                name=msg.name if hasattr(msg, 'name') else None,
                additional_kwargs=msg.additional_kwargs,
            ))
        else:
            cleaned.append(msg)

    # 多模态历史清洗：
    # - 仅保留“最近一条用户消息”的原始多模态内容
    # - 其余历史用户消息中，移除 input_audio/image_url/video_url，避免过期URL导致后续纯文本轮次失败
    latest_user_idx = -1
    for i in range(len(cleaned) - 1, -1, -1):
        if isinstance(cleaned[i], HumanMessage):
            latest_user_idx = i
            break

    def _sanitize_historical_content(content):
        if not isinstance(content, list):
            return content
        kept = []
        for seg in content:
            if not isinstance(seg, dict):
                continue
            seg_type = str(seg.get("type", "")).strip().lower()
            if seg_type in ("input_audio", "image_url", "video_url"):
                continue
            kept.append(seg)
        if not kept:
            return "（历史多媒体内容已省略，仅保留上下文结论）"
        if len(kept) == 1 and kept[0].get("type") == "text":
            return str(kept[0].get("text", "")).strip()
        return kept

    sanitized = []
    for idx, msg in enumerate(cleaned):
        if isinstance(msg, HumanMessage) and idx != latest_user_idx:
            new_content = _sanitize_historical_content(msg.content)
            if new_content != msg.content:
                try:
                    sanitized.append(msg.model_copy(update={"content": new_content}))
                except Exception:
                    msg.content = new_content
                    sanitized.append(msg)
            else:
                sanitized.append(msg)
        else:
            sanitized.append(msg)

    return sanitized[-MAX_MESSAGES:]  # type: ignore


class AgentState(MessagesState):
    """Agent状态，包含消息历史和滑动窗口"""
    messages: Annotated[list[AnyMessage], _windowed_messages]


def _resolve_intent_hint(ctx=None, explicit_intent: str = "") -> str:
    if explicit_intent:
        return explicit_intent.strip().lower()
    if ctx is None:
        return ""
    headers = getattr(ctx, "headers", {}) or {}
    if isinstance(headers, dict):
        return str(headers.get("x-intent-hint", "")).strip().lower()
    return ""


def _resolve_agent_profile(ctx=None) -> AgentProfile:
    headers = getattr(ctx, "headers", {}) if ctx is not None else {}
    profile_id = ""
    if isinstance(headers, dict):
        profile_id = str(headers.get(PROFILE_HEADER, "")).strip()
    if not profile_id:
        profile_id = get_current_agent_profile_id()
    return get_profile(profile_id)


def classify_intent_fast(user_text: str, has_media: bool = False) -> str:
    text = (user_text or "").lower()
    if not text and has_media:
        return "knowledge"
    knowledge_priority_patterns = [
        "更新慢", "延迟", "异常", "报警", "告警", "为什么", "怎么", "怎么办", "无法",
        "失败", "收不到", "看不到", "不显示", "不刷新", "不准确", "功能", "教程",
        "使用", "说明", "帮助", "规则", "配置", "服务异常", "系统异常",
    ]
    if any(k in text for k in knowledge_priority_patterns):
        return "knowledge"

    ship_strong_patterns = [
        r"\bmmsi\b", r"\bimo\b", r"\b\d{9}\b", "查询船位", "更新船位", "上传船位",
        r"查.*船位", r"船位.*查", r"查.*位置", r"位置.*查",
        "船舶档案", "psc记录", "区域船舶", "海峡通航", "更新静态信息",
    ]
    for p in ship_strong_patterns:
        if re.search(p, text):
            return "ship"
    return "knowledge"


def _build_system_prompt(workspace_path: str, profile: AgentProfile, intent_hint: str = "") -> str:
    """Build system prompt from base prompt, active profile prompt, and allowed skill docs."""
    base_path = os.path.join(workspace_path, SYSTEM_PROMPT_BASE)
    with open(base_path, 'r', encoding='utf-8') as f:
        parts = [f.read()]

    profile_prompt = read_profile_prompt(profile)
    if profile_prompt.strip():
        parts.append(f"\n\n---\n\n# Active Agent Profile: {profile.profile_id}\n\n{profile_prompt}")

    selected_skills = set(profile.skills or DEFAULT_SKILLS)
    skills_dir = os.path.join(workspace_path, "src/skills")
    if os.path.isdir(skills_dir):
        for skill_name in sorted(os.listdir(skills_dir)):
            if skill_name not in selected_skills:
                continue
            skill_path = os.path.join(skills_dir, skill_name)
            skill_md = os.path.join(skill_path, "SKILL.md")
            if os.path.isdir(skill_path) and os.path.exists(skill_md):
                with open(skill_md, 'r', encoding='utf-8') as f:
                    skill_doc = f.read()
                parts.append(f"\n\n---\n\n# Skill: {skill_name}\n\n{skill_doc}")
                logger.info(f"[MainAgent] Loaded skill prompt: {skill_name} ({len(skill_doc)} chars)")

    full_prompt = "".join(parts)
    logger.info(
        f"[MainAgent] Total system prompt: {len(full_prompt)} chars, "
        f"profile={profile.profile_id}, intent_hint={intent_hint or 'none'}"
    )
    return full_prompt

def _load_all_tools(profile: AgentProfile) -> list:
    """Load tools allowed by the active profile."""
    all_tools = SkillLoader.get_tools_by_skill_names(list(profile.skills or DEFAULT_SKILLS))
    disabled = set(profile.disabled_tools or [])
    if disabled:
        all_tools = [tool for tool in all_tools if tool.name not in disabled]
    logger.info(
        f"[MainAgent] Tools for profile={profile.profile_id}: "
        f"{[t.name for t in all_tools]}"
    )
    return all_tools

def build_agent(ctx=None, intent: str = ""):
    """
    构建并返回Hifleet智能客服主Agent
    
    架构说明：
    - Skills动态加载：工具和Prompt从Skill目录自动加载
    - Base Prompt + Skill SKILL.md：按需拼接，避免全量加载
    - 工具从Skill注册：每个Skill定义自己的工具
    
    Args:
        ctx: 请求上下文，用于链路追踪
        
    Returns:
        Agent实例
    """
    logger.info("[MainAgent] Building Hifleet customer service agent (Skills architecture)...")
    
    # 1. 读取配置
    workspace_path = os.getenv("COZE_WORKSPACE_PATH")
    if not workspace_path:
        workspace_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
    config_path = os.path.join(workspace_path, LLM_CONFIG)
    
    with open(config_path, 'r', encoding='utf-8') as f:
        cfg = json.load(f)
    
    intent_hint = _resolve_intent_hint(ctx, explicit_intent=intent)
    profile = _resolve_agent_profile(ctx)
    # 2. 动态组装System Prompt
    system_prompt = _build_system_prompt(workspace_path, profile=profile, intent_hint=intent_hint)
    
    # 3. 初始化LLM
    api_key = os.getenv("COZE_WORKLOAD_IDENTITY_API_KEY")
    base_url = os.getenv("COZE_INTEGRATION_MODEL_BASE_URL")
    
    llm = ChatOpenAI(
        model=cfg['config'].get("model", "doubao-seed-2-0-lite-260215"),
        api_key=api_key,
        base_url=base_url,
        temperature=cfg['config'].get('temperature', 0.7),
        streaming=True,
        timeout=cfg['config'].get('timeout', 600),
        extra_body={
            "thinking": {
                "type": cfg['config'].get('thinking_type', 'disabled')
            }
        },
        default_headers=default_headers(ctx) if ctx else {}
    )
    
    logger.info(f"[MainAgent] LLM initialized: {cfg['config'].get('model')}")
    
    # 4. 从Profile允许的Skills动态加载工具
    tools = _load_all_tools(profile)
    
    # 5. 创建Agent
    agent = create_agent(
        model=llm,
        system_prompt=system_prompt,
        tools=tools,
        checkpointer=get_memory_saver(),
        state_schema=AgentState,
    )
    
    logger.info(
        f"[MainAgent] Agent built successfully (Profile architecture), "
        f"profile={profile.profile_id}, intent_hint={intent_hint or 'none'}"
    )
    
    return agent
