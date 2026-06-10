"""
工作流状态定义
定义Hifleet智能客服工作流的所有状态字段
"""
from typing import TypedDict, Annotated, List, Dict, Any, Optional
from langgraph.graph.message import add_messages


class WorkflowState(TypedDict):
    """
    Hifleet智能客服工作流状态
    
    包含从用户输入到最终回复的所有中间状态
    """
    # ========== 用户输入 ==========
    user_input: str
    """用户原始输入"""
    
    # ========== 预处理结果 ==========
    rewritten_query: str
    """重写后的查询语句，适合知识库检索"""
    
    entities: List[Dict[str, str]]
    """提取的实体列表，格式：[{"type": "MMSI", "value": "123456789"}]"""
    
    keywords: List[str]
    """提取的关键词列表"""
    
    # ========== 意图分类 ==========
    intent: str
    """识别的意图类型：platform_usage/ship_operation/business_inquiry/general_question"""
    
    intent_confidence: float
    """意图分类置信度，0.0-1.0"""
    
    # ========== 知识库检索 ==========
    search_result: str
    """知识库检索结果"""
    
    search_score: float
    """检索相似度分数"""
    
    # ========== 回复生成 ==========
    response: str
    """最终生成的回复"""
    
    # ========== 对话历史 ==========
    messages: Annotated[list, add_messages]
    """对话历史，支持自动累加"""
    
    # ========== 元数据 ==========
    session_id: str
    """会话ID，用于记忆管理"""
    
    timestamp: str
    """时间戳"""
    
    node_history: List[str]
    """节点执行历史，用于调试和监控"""
    
    # ========== 错误处理 ==========
    error: Optional[str]
    """错误信息，如果有的话"""
    
    error_node: Optional[str]
    """发生错误的节点名称"""


# 意图类型常量
class IntentType:
    """意图类型常量定义"""
    PLATFORM_USAGE = "platform_usage"
    SHIP_OPERATION = "ship_operation"
    BUSINESS_INQUIRY = "business_inquiry"
    GENERAL_QUESTION = "general_question"
    UNKNOWN = "unknown"


# 实体类型常量
class EntityType:
    """实体类型常量定义"""
    MMSI = "MMSI"
    PHONE = "phone"
    EMAIL = "email"
    NAME = "name"
    COMPANY = "company"
    LOCATION = "location"
