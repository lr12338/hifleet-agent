"""
通用问答处理节点
处理非产品相关的通用问题，使用深度思考模式
"""
import logging
from typing import Any

from coze_coding_dev_sdk import LLMClient
from coze_coding_utils.runtime_ctx.context import new_context
from langchain_core.messages import SystemMessage, HumanMessage

from workflows.state import WorkflowState
from workflows.config import get_model_config, get_prompt
from workflows.utils.knowledge_search import search_by_intent

logger = logging.getLogger(__name__)


def handle_general_question_node(state: WorkflowState) -> WorkflowState:
    """
    通用问答处理节点
    
    功能：
    1. 从全量知识库检索相关信息
    2. 使用深度思考模式生成回复
    
    Args:
        state: 工作流状态
        
    Returns:
        更新后的工作流状态
    """
    logger.info("[GeneralQuestion] Processing general question")
    
    try:
        user_input = state.get("user_input", "")
        rewritten_query = state.get("rewritten_query", user_input)
        
        # 1. 从知识库检索相关信息
        search_result = search_by_intent(
            query=rewritten_query,
            intent="general_question"
        )
        
        logger.info(f"[GeneralQuestion] Search result length: {len(search_result)}")
        
        # 2. 生成回复（使用深度思考模式）
        response = generate_general_response(user_input, search_result)
        
        # 3. 更新状态
        state["search_result"] = search_result
        state["response"] = response
        state["node_history"].append("handle_general_question")
        
        logger.info("[GeneralQuestion] General question processing completed")
        
    except Exception as e:
        logger.error(f"[GeneralQuestion] Error: {str(e)}", exc_info=True)
        state["error"] = f"通用问答处理失败：{str(e)}"
        state["error_node"] = "handle_general_question"
        state["response"] = "抱歉，处理您的问题时出现错误。请稍后重试或联系人工客服。"
    
    return state


def generate_general_response(
    user_input: str,
    search_result: str
) -> str:
    """
    生成通用问答回复（启用深度思考模式）
    
    Args:
        user_input: 用户原始输入
        search_result: 知识库检索结果
        
    Returns:
        生成的回复
    """
    try:
        # 获取配置
        model_config = get_model_config("deep_thinking")
        system_prompt = get_prompt("general_question")
        
        # 创建LLM客户端
        ctx = new_context(method="generate_general_response")
        client = LLMClient(ctx=ctx)
        
        # 构建消息
        if search_result:
            content = f"""用户问题：{user_input}

参考资料：
{search_result}

请基于参考资料，为用户提供专业、全面的解答。"""
        else:
            content = f"""用户问题：{user_input}

知识库中暂无相关信息。请根据自己的知识库，为用户提供有帮助的回答。如果问题超出您的知识范围，请诚实告知。"""
        
        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=content)
        ]
        
        # 调用LLM生成回复（启用深度思考）
        response = client.invoke(
            messages=messages,
            model=model_config.get("model", "doubao-seed-2-0-lite-260215"),
            temperature=model_config.get("temperature", 0.5),
            max_tokens=model_config.get("max_tokens", 4096),
            thinking=model_config.get("thinking", "enabled")
        )
        
        # 提取文本内容
        reply = _extract_text_content(response.content)
        
        return reply
        
    except Exception as e:
        logger.error(f"[GeneralQuestion] Error generating response: {str(e)}", exc_info=True)
        return "抱歉，生成回复时出现错误。请稍后重试或联系人工客服。"


def _extract_text_content(content: Any) -> str:
    """
    从LLM响应中提取文本内容
    """
    if isinstance(content, str):
        return content
    elif isinstance(content, list):
        if content and isinstance(content[0], str):
            return " ".join(content)
        else:
            text_parts = [
                item.get("text", "") 
                for item in content 
                if isinstance(item, dict) and item.get("type") == "text"
            ]
            return " ".join(text_parts)
    return str(content)
