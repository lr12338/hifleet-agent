"""
船位操作处理节点
处理船位查询和更新操作
"""
import logging
import re
from typing import Optional, Tuple

from workflows.state import WorkflowState, EntityType
from tools.ship_position_tool import query_ship_position, update_ship_position

logger = logging.getLogger(__name__)


def handle_ship_operation_node(state: WorkflowState) -> WorkflowState:
    """
    船位操作处理节点
    
    功能：
    1. 提取MMSI号码
    2. 判断操作类型（查询/更新）
    3. 调用对应的API
    
    Args:
        state: 工作流状态
        
    Returns:
        更新后的工作流状态
    """
    logger.info("[ShipOperation] Processing ship operation request")
    
    try:
        user_input = state.get("user_input", "")
        entities = state.get("entities", [])
        
        # 1. 提取MMSI
        mmsi = extract_mmsi(user_input, entities)
        
        if not mmsi:
            response = "请提供正确的MMSI号码（9位数字）。例如：查询MMSI 123456789的船位"
            state["response"] = response
            state["node_history"].append("handle_ship_operation")
            logger.warning("[ShipOperation] No valid MMSI found")
            return state
        
        # 2. 判断操作类型
        operation_type = determine_operation_type(user_input)
        
        logger.info(f"[ShipOperation] MMSI: {mmsi}, Operation: {operation_type}")
        
        # 3. 执行操作
        if operation_type == "update":
            # 提取位置信息
            lat, lon = extract_position(user_input)
            if lat and lon:
                response = update_ship_position(mmsi, lat, lon)
            else:
                response = f"请提供完整的位置信息（经度和纬度）。\n\n例如：更新MMSI {mmsi}的位置到 东经121.5，北纬31.2"
        else:
            # 默认查询
            response = query_ship_position(mmsi)
        
        # 4. 更新状态
        state["response"] = response
        state["node_history"].append("handle_ship_operation")
        
        logger.info("[ShipOperation] Ship operation processing completed")
        
    except Exception as e:
        logger.error(f"[ShipOperation] Error: {str(e)}", exc_info=True)
        state["error"] = f"船位操作处理失败：{str(e)}"
        state["error_node"] = "handle_ship_operation"
        state["response"] = "抱歉，处理船位操作时出现错误。请稍后重试。"
    
    return state


def extract_mmsi(text: str, entities: list) -> Optional[str]:
    """
    提取MMSI号码
    
    Args:
        text: 文本内容
        entities: 实体列表
        
    Returns:
        MMSI号码或None
    """
    # 1. 从实体中提取
    for entity in entities:
        if entity.get("type") == EntityType.MMSI:
            return entity.get("value")
    
    # 2. 正则匹配（9位数字）
    pattern = r'\b\d{9}\b'
    matches = re.findall(pattern, text)
    if matches:
        return matches[0]
    
    return None


def determine_operation_type(text: str) -> str:
    """
    判断操作类型
    
    Args:
        text: 用户输入文本
        
    Returns:
        操作类型：query/update
    """
    update_keywords = ["更新", "修改", "更改", "设置"]
    if any(kw in text for kw in update_keywords):
        return "update"
    return "query"


def extract_position(text: str) -> Tuple[Optional[float], Optional[float]]:
    """
    从文本中提取经纬度信息
    
    Args:
        text: 用户输入文本
        
    Returns:
        (纬度, 经度) 或 (None, None)
    """
    # 尝试提取数字对（简单实现）
    # 格式可能为：东经121.5 北纬31.2 或 121.5, 31.2
    
    # 尝试匹配 "东经/经度 xxx, 北纬/纬度 xxx"
    patterns = [
        r'东经\s*(\d+\.?\d*).*?北纬\s*(\d+\.?\d*)',
        r'经度\s*(\d+\.?\d*).*?纬度\s*(\d+\.?\d*)',
        r'(\d+\.?\d*)[,\s]+(\d+\.?\d*)'
    ]
    
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            try:
                lon = float(match.group(1))
                lat = float(match.group(2))
                return lat, lon
            except ValueError:
                continue
    
    return None, None
