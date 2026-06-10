"""
配置文件加载模块
"""
import os
import json
from typing import Dict, Any, Optional


def load_config(file_name: str) -> Dict[str, Any]:
    """
    加载配置文件
    
    Args:
        file_name: 配置文件名（不含路径）
    
    Returns:
        配置字典
    """
    workspace_path = os.getenv("COZE_WORKSPACE_PATH", "/workspace/projects")
    config_path = os.path.join(workspace_path, "src/workflows/config", file_name)
    
    with open(config_path, 'r', encoding='utf-8') as f:
        return json.load(f)


def get_model_config(model_name: str) -> Dict[str, Any]:
    """
    获取指定模型的配置
    
    Args:
        model_name: 模型名称（rewrite/classify/reply/deep_thinking）
    
    Returns:
        模型配置字典
    """
    models_config = load_config("models.json")
    return models_config.get("models", {}).get(model_name, {})


def get_prompt(prompt_name: str) -> str:
    """
    获取指定Prompt
    
    Args:
        prompt_name: Prompt名称
    
    Returns:
        Prompt字符串
    """
    prompts_config = load_config("prompts.json")
    prompt_config = prompts_config.get(prompt_name, {})
    return prompt_config.get("system_prompt", "")


def get_knowledge_config(intent: str) -> Dict[str, Any]:
    """
    获取指定意图的知识库配置
    
    Args:
        intent: 意图类型
    
    Returns:
        知识库配置字典
    """
    knowledge_config = load_config("knowledge_config.json")
    return knowledge_config.get("intent_knowledge_map", {}).get(intent, {})


def get_knowledge_base_id(kb_name: str) -> Optional[str]:
    """
    获取知识库ID
    
    Args:
        kb_name: 知识库名称
    
    Returns:
        知识库ID
    """
    knowledge_config = load_config("knowledge_config.json")
    kb_config = knowledge_config.get("knowledge_bases", {}).get(kb_name, {})
    return kb_config.get("id")
