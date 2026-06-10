"""
工作流路由模块
根据意图类型决定下一个处理节点
"""
import logging
from workflows.state import WorkflowState, IntentType

logger = logging.getLogger(__name__)


def route_by_intent(state: WorkflowState) -> str:
    """
    根据意图类型路由到对应的处理节点
    
    Args:
        state: 工作流状态
        
    Returns:
        下一个节点的名称
    """
    intent = state.get("intent", IntentType.UNKNOWN)
    confidence = state.get("intent_confidence", 0.0)
    
    logger.info(f"[Router] Routing based on intent: {intent} (confidence: {confidence})")
    
    # 路由映射
    route_map = {
        IntentType.PLATFORM_USAGE: "handle_platform_usage",
        IntentType.SHIP_OPERATION: "handle_ship_operation",
        IntentType.BUSINESS_INQUIRY: "handle_business_inquiry",
        IntentType.GENERAL_QUESTION: "handle_general_question",
        IntentType.UNKNOWN: "handle_general_question"  # 未知意图默认走通用问答
    }
    
    next_node = route_map.get(intent, "handle_general_question")
    
    logger.info(f"[Router] Next node: {next_node}")
    
    return next_node
