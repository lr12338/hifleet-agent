"""
商务咨询处理节点
处理产品咨询、价格咨询和商务合作
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


def handle_business_inquiry_node(state: WorkflowState) -> WorkflowState:
    """
    商务咨询处理节点
    
    功能：
    1. 从多个知识库检索相关信息
    2. 使用商务专用Prompt生成回复
    3. 主动引导用户留资
    
    Args:
        state: 工作流状态
        
    Returns:
        更新后的工作流状态
    """
    logger.info("[BusinessInquiry] Processing business inquiry")
    
    try:
        user_input = state.get("user_input", "")
        rewritten_query = state.get("rewritten_query", user_input)
        
        # 1. 从知识库检索相关信息（使用更多知识库）
        search_result = search_by_intent(
            query=rewritten_query,
            intent="business_inquiry"
        )
        
        logger.info(f"[BusinessInquiry] Search result length: {len(search_result)}")
        
        # 2. 生成回复
        response = generate_business_response(user_input, search_result)
        
        # 3. 更新状态
        state["search_result"] = search_result
        state["response"] = response
        state["node_history"].append("handle_business_inquiry")
        
        logger.info("[BusinessInquiry] Business inquiry processing completed")
        
    except Exception as e:
        logger.error(f"[BusinessInquiry] Error: {str(e)}", exc_info=True)
        state["error"] = f"商务咨询处理失败：{str(e)}"
        state["error_node"] = "handle_business_inquiry"
        state["response"] = "抱歉，处理您的商务咨询时出现错误。请稍后重试或联系我们的商务团队。"
    
    return state


def generate_business_response(
    user_input: str,
    search_result: str
) -> str:
    """
    生成商务咨询回复
    
    Args:
        user_input: 用户原始输入
        search_result: 知识库检索结果
        
    Returns:
        生成的回复
    """
    try:
        # 获取配置
        model_config = get_model_config("reply")
        system_prompt = get_prompt("business_inquiry")
        
        # 创建LLM客户端
        ctx = new_context(method="generate_business_response")
        client = LLMClient(ctx=ctx)
        
        # 构建消息
        if search_result:
            content = f"""用户咨询：{user_input}

产品信息：
{search_result}

请为用户提供专业的商务咨询，并主动引导用户留下联系方式。"""
        else:
            content = f"""用户咨询：{user_input}

知识库中暂无相关信息。请友好地回复用户，并主动邀请用户留下联系方式，我们会安排专人跟进。

联系方式收集引导：
- 姓名
- 电话
- 公司
- 具体需求"""
        
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
        logger.error(f"[BusinessInquiry] Error generating response: {str(e)}", exc_info=True)
        return "抱歉，生成回复时出现错误。请直接联系我们的商务团队：400-xxx-xxxx"


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
