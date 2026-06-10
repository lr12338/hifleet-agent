"""
平台使用问题处理节点
处理平台功能使用、账号操作及技术支持问题
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


def handle_platform_usage_node(state: WorkflowState) -> WorkflowState:
    """
    平台使用问题处理节点
    
    功能：
    1. 从知识库检索相关信息
    2. 使用专业Prompt生成回复
    
    Args:
        state: 工作流状态
        
    Returns:
        更新后的工作流状态
    """
    logger.info("[PlatformUsage] Processing platform usage question")
    
    try:
        user_input = state.get("user_input", "")
        rewritten_query = state.get("rewritten_query", user_input)
        
        # 1. 从知识库检索相关信息
        search_result = search_by_intent(
            query=rewritten_query,
            intent="platform_usage"
        )
        
        logger.info(f"[PlatformUsage] Search result length: {len(search_result)}")
        
        # 2. 生成回复
        response = generate_platform_response(user_input, rewritten_query, search_result)
        
        # 3. 更新状态
        state["search_result"] = search_result
        state["response"] = response
        state["node_history"].append("handle_platform_usage")
        
        logger.info("[PlatformUsage] Platform usage processing completed")
        
    except Exception as e:
        logger.error(f"[PlatformUsage] Error: {str(e)}", exc_info=True)
        state["error"] = f"平台使用问题处理失败：{str(e)}"
        state["error_node"] = "handle_platform_usage"
        state["response"] = "抱歉，处理您的问题时出现错误。请稍后重试或联系人工客服。"
    
    return state


def generate_platform_response(
    user_input: str,
    rewritten_query: str,
    search_result: str
) -> str:
    """
    生成平台使用问题的回复
    
    Args:
        user_input: 用户原始输入
        rewritten_query: 重写后的查询
        search_result: 知识库检索结果
        
    Returns:
        生成的回复
    """
    try:
        # 获取配置
        model_config = get_model_config("reply")
        system_prompt = get_prompt("platform_usage")
        
        # 创建LLM客户端
        ctx = new_context(method="generate_platform_response")
        client = LLMClient(ctx=ctx)
        
        # 构建消息
        if search_result:
            content = f"""用户问题：{user_input}

知识库检索结果：
{search_result}

请根据知识库检索结果，为用户提供专业的解答。"""
        else:
            content = f"""用户问题：{user_input}

知识库中暂无相关信息。请礼貌地告知用户，并提供人工客服联系方式。

人工客服联系方式：
- 电话：400-xxx-xxxx
- 官方文档：https://www.hifleet.com/help"""
        
        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=content)
        ]
        
        # 调用LLM生成回复
        response = client.invoke(
            messages=messages,
            model=model_config.get("model", "doubao-seed-2-0-lite-260215"),
            temperature=model_config.get("temperature", 0.7),
            max_tokens=model_config.get("max_tokens", 2048)
        )
        
        # 提取文本内容
        reply = _extract_text_content(response.content)
        
        return reply
        
    except Exception as e:
        logger.error(f"[PlatformUsage] Error generating response: {str(e)}", exc_info=True)
        return "抱歉，生成回复时出现错误。请稍后重试或联系人工客服。"


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
            text_parts = [
                item.get("text", "") 
                for item in content 
                if isinstance(item, dict) and item.get("type") == "text"
            ]
            return " ".join(text_parts)
    return str(content)
