"""
问题预处理节点
负责问题重写、实体抽取和关键词提取
"""
import re
import logging
from typing import Dict, Any, List
from datetime import datetime

from coze_coding_dev_sdk import LLMClient
from coze_coding_utils.runtime_ctx.context import new_context
from langchain_core.messages import SystemMessage, HumanMessage

from workflows.state import WorkflowState, EntityType
from workflows.config import get_model_config, get_prompt

logger = logging.getLogger(__name__)


def preprocess_node(state: WorkflowState) -> WorkflowState:
    """
    问题预处理节点
    
    功能：
    1. 问题重写：将口语化问题转为检索友好的Query
    2. 实体抽取：提取MMSI、电话、邮箱等实体
    3. 关键词提取：提取核心关键词
    
    Args:
        state: 工作流状态
        
    Returns:
        更新后的工作流状态
    """
    logger.info(f"[Preprocess] Processing user input: {state.get('user_input', '')}")
    
    try:
        # 1. 问题重写
        rewritten_query = rewrite_query(state["user_input"])
        logger.info(f"[Preprocess] Rewritten query: {rewritten_query}")
        
        # 2. 实体抽取
        entities = extract_entities(state["user_input"])
        logger.info(f"[Preprocess] Extracted entities: {entities}")
        
        # 3. 关键词提取
        keywords = extract_keywords(rewritten_query)
        logger.info(f"[Preprocess] Extracted keywords: {keywords}")
        
        # 4. 更新状态
        state["rewritten_query"] = rewritten_query
        state["entities"] = entities
        state["keywords"] = keywords
        state["timestamp"] = datetime.now().isoformat()
        state["node_history"].append("preprocess")
        
        logger.info(f"[Preprocess] Preprocess completed successfully")
        
    except Exception as e:
        logger.error(f"[Preprocess] Error in preprocess_node: {str(e)}", exc_info=True)
        state["error"] = f"问题预处理失败：{str(e)}"
        state["error_node"] = "preprocess"
    
    return state


def rewrite_query(user_input: str) -> str:
    """
    问题重写
    
    将口语化问题转化为适合知识库检索的Query
    
    Args:
        user_input: 用户原始输入
        
    Returns:
        重写后的查询语句
    """
    try:
        # 获取配置
        model_config = get_model_config("rewrite")
        system_prompt = get_prompt("rewrite")
        
        # 创建LLM客户端
        ctx = new_context(method="rewrite_query")
        client = LLMClient(ctx=ctx)
        
        # 调用LLM重写
        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_input)
        ]
        
        response = client.invoke(
            messages=messages,
            model=model_config.get("model", "deepseek-v3-2-251201"),
            temperature=model_config.get("temperature", 0.8),
            max_tokens=model_config.get("max_tokens", 1024)
        )
        
        # 提取文本内容
        rewritten = _extract_text_content(response.content)
        
        # 如果重写失败或结果太短，返回原始输入
        if not rewritten or len(rewritten.strip()) < 3:
            logger.warning(f"[Preprocess] Rewrite failed or too short, using original input")
            return user_input
        
        return rewritten.strip()
        
    except Exception as e:
        logger.error(f"[Preprocess] Error in rewrite_query: {str(e)}", exc_info=True)
        # 重写失败时返回原始输入
        return user_input


def extract_entities(text: str) -> List[Dict[str, str]]:
    """
    实体抽取
    
    提取MMSI、电话、邮箱等实体
    
    Args:
        text: 文本内容
        
    Returns:
        实体列表，格式：[{"type": "MMSI", "value": "123456789"}]
    """
    entities = []
    
    try:
        # 1. 提取MMSI (9位数字)
        mmsi_pattern = r'\b\d{9}\b'
        mmsi_matches = re.findall(mmsi_pattern, text)
        for mmsi in mmsi_matches:
            # 验证MMSI有效性（9位数字即可）
            entities.append({
                "type": EntityType.MMSI,
                "value": mmsi
            })
        
        # 2. 提取电话号码（中国大陆手机号）
        phone_pattern = r'\b1[3-9]\d{9}\b'
        phone_matches = re.findall(phone_pattern, text)
        for phone in phone_matches:
            entities.append({
                "type": EntityType.PHONE,
                "value": phone
            })
        
        # 3. 提取邮箱
        email_pattern = r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
        email_matches = re.findall(email_pattern, text)
        for email in email_matches:
            entities.append({
                "type": EntityType.EMAIL,
                "value": email
            })
        
        # 4. 提取可能的公司名称（简化版：以"公司"结尾的词组）
        company_pattern = r'([\u4e00-\u9fa5]+公司)'
        company_matches = re.findall(company_pattern, text)
        for company in company_matches:
            entities.append({
                "type": EntityType.COMPANY,
                "value": company
            })
        
        logger.info(f"[Preprocess] Extracted {len(entities)} entities from text")
        
    except Exception as e:
        logger.error(f"[Preprocess] Error in extract_entities: {str(e)}", exc_info=True)
    
    return entities


def extract_keywords(text: str) -> List[str]:
    """
    关键词提取
    
    从重写后的查询中提取关键词
    
    Args:
        text: 文本内容
        
    Returns:
        关键词列表
    """
    keywords = []
    
    try:
        # 简单实现：按空格分割，过滤停用词
        # 后续可以集成jieba等分词工具
        
        # 停用词列表
        stop_words = {'的', '了', '在', '是', '我', '有', '和', '就', '不', '人', '都', '一', '一个', '上', '也', '很', '到', '说', '要', '去', '你', '会', '着', '没有', '看', '好', '自己', '这'}
        
        # 分词（简单按空格和标点分割）
        words = re.split(r'[，。！？、；：\s]+', text)
        
        # 过滤
        keywords = [w for w in words if w and w not in stop_words and len(w) > 1]
        
        # 去重
        keywords = list(dict.fromkeys(keywords))
        
        # 限制数量
        keywords = keywords[:10]
        
        logger.info(f"[Preprocess] Extracted {len(keywords)} keywords")
        
    except Exception as e:
        logger.error(f"[Preprocess] Error in extract_keywords: {str(e)}", exc_info=True)
    
    return keywords


def _extract_text_content(content: Any) -> str:
    """
    从LLM响应中提取文本内容
    
    Args:
        content: LLM响应内容（可能是str或list）
        
    Returns:
        文本字符串
    """
    if isinstance(content, str):
        return content
    elif isinstance(content, list):
        if content and isinstance(content[0], str):
            return " ".join(content)
        else:
            # 多模态响应，提取文本部分
            text_parts = [
                item.get("text", "") 
                for item in content 
                if isinstance(item, dict) and item.get("type") == "text"
            ]
            return " ".join(text_parts)
    return str(content)
