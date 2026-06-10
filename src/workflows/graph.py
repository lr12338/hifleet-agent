"""
工作流图构建模块
使用LangGraph构建多节点工作流
"""
import logging
from typing import TypedDict
from datetime import datetime

from langgraph.graph import StateGraph, END

from workflows.state import WorkflowState, IntentType
from workflows.nodes.preprocess import preprocess_node
from workflows.nodes.classify import classify_intent_node
from workflows.nodes.routers import route_by_intent
from workflows.nodes.handlers import (
    handle_platform_usage_node,
    handle_ship_operation_node,
    handle_business_inquiry_node,
    handle_general_question_node
)
from storage.memory.memory_saver import get_memory_saver

logger = logging.getLogger(__name__)


def build_workflow():
    """
    构建Hifleet智能客服工作流
    
    工作流结构：
    开始 -> 预处理 -> 意图分类 -> 分支处理 -> 结束
    
    Returns:
        编译后的工作流应用
    """
    logger.info("[Workflow] Building Hifleet workflow...")
    
    # 创建工作流图
    workflow = StateGraph(WorkflowState)
    
    # ========== 添加节点 ==========
    logger.info("[Workflow] Adding nodes...")
    
    # 1. 预处理节点
    workflow.add_node("preprocess", preprocess_node)
    
    # 2. 意图分类节点
    workflow.add_node("classify_intent", classify_intent_node)
    
    # 3. 处理节点
    workflow.add_node("handle_platform_usage", handle_platform_usage_node)
    workflow.add_node("handle_ship_operation", handle_ship_operation_node)
    workflow.add_node("handle_business_inquiry", handle_business_inquiry_node)
    workflow.add_node("handle_general_question", handle_general_question_node)
    
    # ========== 设置入口 ==========
    logger.info("[Workflow] Setting entry point...")
    workflow.set_entry_point("preprocess")
    
    # ========== 添加边 ==========
    logger.info("[Workflow] Adding edges...")
    
    # 预处理 -> 意图分类
    workflow.add_edge("preprocess", "classify_intent")
    
    # 意图分类 -> 条件分支
    workflow.add_conditional_edges(
        "classify_intent",
        route_by_intent,
        {
            "handle_platform_usage": "handle_platform_usage",
            "handle_ship_operation": "handle_ship_operation",
            "handle_business_inquiry": "handle_business_inquiry",
            "handle_general_question": "handle_general_question"
        }
    )
    
    # 所有处理节点 -> 结束
    for node in [
        "handle_platform_usage",
        "handle_ship_operation",
        "handle_business_inquiry",
        "handle_general_question"
    ]:
        workflow.add_edge(node, END)
    
    # ========== 编译工作流 ==========
    logger.info("[Workflow] Compiling workflow with memory...")
    
    # 使用记忆保存器
    checkpointer = get_memory_saver()
    
    # 编译
    app = workflow.compile(checkpointer=checkpointer)
    
    logger.info("[Workflow] Workflow built successfully!")
    
    return app


def create_initial_state(user_input: str, session_id: str = None) -> WorkflowState:
    """
    创建初始工作流状态
    
    Args:
        user_input: 用户输入
        session_id: 会话ID（可选）
        
    Returns:
        初始状态字典
    """
    if not session_id:
        session_id = f"session_{datetime.now().strftime('%Y%m%d%H%M%S')}"
    
    return {
        "user_input": user_input,
        "rewritten_query": "",
        "entities": [],
        "keywords": [],
        "intent": IntentType.UNKNOWN,
        "intent_confidence": 0.0,
        "search_result": "",
        "search_score": 0.0,
        "response": "",
        "messages": [],
        "session_id": session_id,
        "timestamp": datetime.now().isoformat(),
        "node_history": [],
        "error": None,
        "error_node": None
    }


def run_workflow(user_input: str, session_id: str = None) -> dict:
    """
    运行工作流（便捷函数）
    
    Args:
        user_input: 用户输入
        session_id: 会话ID（可选）
        
    Returns:
        工作流执行结果
    """
    # 构建工作流
    app = build_workflow()
    
    # 创建初始状态
    initial_state = create_initial_state(user_input, session_id)
    
    # 执行工作流
    config = {
        "configurable": {
            "thread_id": session_id or initial_state["session_id"]
        }
    }
    
    result = app.invoke(initial_state, config)
    
    return result
