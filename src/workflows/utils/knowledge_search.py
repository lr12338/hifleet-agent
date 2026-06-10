"""
知识库检索辅助函数
提供统一的知识库检索接口，支持多知识库配置
"""
import logging
from typing import List, Optional, Dict, Any

from coze_coding_dev_sdk import KnowledgeClient, Config
from coze_coding_utils.runtime_ctx.context import new_context

logger = logging.getLogger(__name__)


def search_knowledge(
    query: str,
    knowledge_base_ids: Optional[List[str]] = None,
    top_k: int = 3,
    min_score: float = 0.5
) -> str:
    """
    从知识库中检索相关信息
    
    Args:
        query: 查询文本
        knowledge_base_ids: 知识库ID列表，如果为None则搜索所有知识库
        top_k: 返回结果数量
        min_score: 最小相似度阈值
        
    Returns:
        检索结果文本
    """
    try:
        # 创建知识库客户端
        ctx = new_context(method="knowledge_search")
        config = Config()
        client = KnowledgeClient(config=config, ctx=ctx)
        
        # 执行搜索
        response = client.search(
            query=query,
            table_names=knowledge_base_ids,  # 如果为None或空，搜索所有知识库
            top_k=top_k,
            min_score=min_score
        )
        
        # 检查响应
        if response.code != 0:
            logger.error(f"Knowledge search failed: {response.msg}")
            return ""
        
        if not response.chunks or len(response.chunks) == 0:
            logger.warning(f"No knowledge found for query: {query}")
            return ""
        
        # 格式化结果
        result_parts = []
        for i, chunk in enumerate(response.chunks, 1):
            score_str = f"{chunk.score:.2f}" if chunk.score is not None else "N/A"
            result_parts.append(f"【相关度: {score_str}】\n{chunk.content}\n")
        
        result = "\n".join(result_parts)
        logger.info(f"Found {len(response.chunks)} knowledge chunks for query")
        
        return result
        
    except Exception as e:
        logger.error(f"Error in search_knowledge: {str(e)}", exc_info=True)
        return ""


def search_by_intent(
    query: str,
    intent: str,
    config: Optional[Dict[str, Any]] = None
) -> str:
    """
    根据意图类型检索知识库
    
    Args:
        query: 查询文本
        intent: 意图类型
        config: 配置信息（可选）
        
    Returns:
        检索结果文本
    """
    from workflows.config import load_config
    
    # 获取知识库配置
    if config is None:
        config = load_config("knowledge_config.json")
    
    # 获取意图对应的知识库配置
    intent_config = config.get("intent_knowledge_map", {}).get(intent, {})
    knowledge_bases = intent_config.get("knowledge_bases", [])
    search_params = intent_config.get("search_params", {})
    
    # 获取知识库ID列表
    all_knowledge_bases = config.get("knowledge_bases", {})
    knowledge_base_ids = []
    for kb_name in knowledge_bases:
        kb_id = all_knowledge_bases.get(kb_name, {}).get("id")
        if kb_id:
            knowledge_base_ids.append(kb_id)
    
    # 执行检索
    return search_knowledge(
        query=query,
        knowledge_base_ids=knowledge_base_ids if knowledge_base_ids else None,
        top_k=search_params.get("top_k", 3),
        min_score=search_params.get("min_score", 0.5)
    )
