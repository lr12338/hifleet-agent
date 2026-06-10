"""
意图分类节点
负责识别用户意图类型，决定后续处理分支
"""
import logging
from typing import Dict, Any, Optional

from coze_coding_dev_sdk import LLMClient
from coze_coding_utils.runtime_ctx.context import new_context
from langchain_core.messages import SystemMessage, HumanMessage

from workflows.state import WorkflowState, IntentType, EntityType
from workflows.config import get_model_config, get_prompt

logger = logging.getLogger(__name__)


# 意图关键词配置
INTENT_KEYWORDS = {
    IntentType.PLATFORM_USAGE: {
        "keywords": ["怎么", "如何", "注册", "登录", "密码", "功能", "使用", "操作", "设置", "账号", "无法", "不能", "找不到", "看不到", "闪退", "登录不了", "注册不了"],
        "weight": 1.0
    },
    IntentType.SHIP_OPERATION: {
        "keywords": ["MMSI", "mmsi", "船位", "船舶", "位置", "查询", "更新", "船舶位置", "船只", "船名"],
        "weight": 1.2  # 船位操作关键词权重更高
    },
    IntentType.BUSINESS_INQUIRY: {
        "keywords": ["价格", "多少钱", "试用", "购买", "合作", "产品", "商务", "收费", "付费", "订阅", "套餐", "报价"],
        "weight": 1.0
    },
    IntentType.GENERAL_QUESTION: {
        "keywords": ["什么是", "解释", "介绍", "定义", "航运", "海事", "港口", "航线", "AIS", "ais"],
        "weight": 0.9
    }
}


def classify_intent_node(state: WorkflowState) -> WorkflowState:
    """
    意图分类节点
    
    功能：
    1. 规则匹配：基于关键词和实体快速分类
    2. LLM分类：复杂情况下使用LLM兜底
    3. 置信度计算：评估分类结果的可靠性
    
    Args:
        state: 工作流状态
        
    Returns:
        更新后的工作流状态
    """
    logger.info(f"[Classify] Classifying intent for: {state.get('user_input', '')}")
    
    try:
        user_input = state["user_input"]
        entities = state.get("entities", [])
        
        # 1. 规则匹配分类（快速）
        intent, confidence = rule_based_classify(user_input, entities)
        logger.info(f"[Classify] Rule-based result: intent={intent}, confidence={confidence}")
        
        # 2. 如果规则匹配置信度低，使用LLM分类
        if confidence < 0.7:
            llm_intent = llm_classify(user_input)
            if llm_intent:
                intent = llm_intent
                confidence = 0.75  # LLM分类给一个中等置信度
                logger.info(f"[Classify] LLM result: intent={intent}")
        
        # 3. 更新状态
        state["intent"] = intent
        state["intent_confidence"] = confidence
        state["node_history"].append("classify_intent")
        
        logger.info(f"[Classify] Classification completed: intent={intent}, confidence={confidence}")
        
    except Exception as e:
        logger.error(f"[Classify] Error in classify_intent_node: {str(e)}", exc_info=True)
        state["error"] = f"意图分类失败：{str(e)}"
        state["error_node"] = "classify_intent"
        # 分类失败时默认为通用问题
        state["intent"] = IntentType.GENERAL_QUESTION
        state["intent_confidence"] = 0.5
    
    return state


def rule_based_classify(user_input: str, entities: list) -> tuple:
    """
    基于规则的意图分类
    
    Args:
        user_input: 用户输入
        entities: 提取的实体列表
        
    Returns:
        (意图类型, 置信度)
    """
    # 1. 实体优先判断
    # 如果包含MMSI实体，很可能是船位操作
    has_mmsi = any(e["type"] == EntityType.MMSI for e in entities)
    if has_mmsi:
        logger.info(f"[Classify] Found MMSI entity, classify as ship_operation")
        return IntentType.SHIP_OPERATION, 0.95
    
    # 如果包含电话或邮箱实体，优先判断为商务咨询
    has_contact = any(e["type"] in [EntityType.PHONE, EntityType.EMAIL] for e in entities)
    if has_contact:
        # 进一步判断是否有购买意向
        purchase_keywords = ["想", "有意", "试用", "购买", "合作"]
        if any(kw in user_input for kw in purchase_keywords):
            logger.info(f"[Classify] Found contact info with purchase intent, classify as business_inquiry")
            return IntentType.BUSINESS_INQUIRY, 0.90
        # 也可能是商务咨询
        if any(kw in user_input for kw in ["价格", "多少钱", "产品"]):
            logger.info(f"[Classify] Found contact info with business keywords, classify as business_inquiry")
            return IntentType.BUSINESS_INQUIRY, 0.85
    
    # 2. 关键词匹配
    intent_scores = {}
    
    for intent_type, config in INTENT_KEYWORDS.items():
        keywords = config["keywords"]
        weight = config["weight"]
        
        # 计算匹配的关键词数量
        matched_count = sum(1 for kw in keywords if kw in user_input)
        
        if matched_count > 0:
            # 分数 = 匹配数量 * 权重
            intent_scores[intent_type] = matched_count * weight
    
    # 3. 选择得分最高的意图
    if intent_scores:
        best_intent = max(intent_scores, key=intent_scores.get)
        best_score = intent_scores[best_intent]
        
        # 计算置信度（基于得分归一化）
        max_possible_score = 5.0  # 假设最高可能得分
        confidence = min(best_score / max_possible_score, 0.9)
        
        logger.info(f"[Classify] Keyword match result: {intent_scores}, best={best_intent}")
        return best_intent, confidence
    
    # 4. 没有匹配到任何关键词，返回未知意图
    logger.info(f"[Classify] No keyword matched, returning unknown")
    return IntentType.UNKNOWN, 0.0


def llm_classify(user_input: str) -> Optional[str]:
    """
    使用LLM进行意图分类
    
    Args:
        user_input: 用户输入
        
    Returns:
        意图类型或None
    """
    try:
        # 获取配置
        model_config = get_model_config("classify")
        system_prompt = get_prompt("classify")
        
        # 创建LLM客户端
        ctx = new_context(method="llm_classify")
        client = LLMClient(ctx=ctx)
        
        # 调用LLM分类
        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_input)
        ]
        
        response = client.invoke(
            messages=messages,
            model=model_config.get("model", "deepseek-v3-2-251201"),
            temperature=model_config.get("temperature", 0.3),
            max_tokens=model_config.get("max_tokens", 100)
        )
        
        # 提取文本内容
        result = _extract_text_content(response.content).strip()
        
        # 验证结果是否为有效意图
        valid_intents = [
            IntentType.PLATFORM_USAGE,
            IntentType.SHIP_OPERATION,
            IntentType.BUSINESS_INQUIRY,
            IntentType.GENERAL_QUESTION
        ]
        
        if result in valid_intents:
            logger.info(f"[Classify] LLM classification successful: {result}")
            return result
        else:
            logger.warning(f"[Classify] LLM returned invalid intent: {result}")
            return None
            
    except Exception as e:
        logger.error(f"[Classify] Error in llm_classify: {str(e)}", exc_info=True)
        return None


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
