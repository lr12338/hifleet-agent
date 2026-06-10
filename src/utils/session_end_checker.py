"""
会话结束判定器
判断当前单次对话是否结束
"""
import re
from typing import Tuple, Optional
import logging
from datetime import datetime, timezone, timedelta

from utils.session_state import SessionState

logger = logging.getLogger(__name__)

# 北京时区
BEIJING_TZ = timezone(timedelta(hours=8))

# 超时阈值（秒）
TIMEOUT_THRESHOLD = 15 * 60  # 15分钟

# 显式结束关键词
EXPLICIT_END_KEYWORDS = [
    "谢谢，没问题了",
    "谢谢没问题了",
    "好的，没问题了",
    "好的没问题了",
    "好的，明白了",
    "好的明白了",
    "好的，知道了",
    "好的知道了",
    "先这样",
    "不用了",
    "没有了",
    "暂时没有了",
    "暂时没有其他问题了",
    "没有其他问题了",
    "就这些",
    "那就这样",
    "那先这样",
]


class SessionEndChecker:
    """
    会话结束判定器
    
    根据多种条件判断会话是否应该结束
    """
    
    @staticmethod
    def check_should_end(session: SessionState, latest_user_message: str = "") -> Tuple[bool, str]:
        """
        检查会话是否应该结束
        
        Args:
            session: 当前会话状态
            latest_user_message: 最新的用户消息（可选）
            
        Returns:
            (should_end: bool, reason: str)
            - should_end: 是否应该结束
            - reason: 结束原因（如果不结束则为空字符串）
        """
        # 1. 检查是否有待处理项（优先级最高）
        if session.pending_slots:
            logger.info(f"[SessionEndChecker] Cannot end: pending slots exist - {session.pending_slots}")
            return False, ""
        
        if session.pending_confirmation:
            logger.info("[SessionEndChecker] Cannot end: pending confirmation exists")
            return False, ""
        
        # 2. 检查用户显式结束
        if latest_user_message:
            is_explicit, reason = SessionEndChecker._check_explicit_end(latest_user_message)
            if is_explicit:
                logger.info(f"[SessionEndChecker] User explicit end: {reason}")
                return True, "explicit"
        
        # 3. 检查任务完成闭环
        is_completed, reason = SessionEndChecker._check_task_completed(session)
        if is_completed:
            logger.info(f"[SessionEndChecker] Task completed: {reason}")
            return True, "completed"
        
        # 4. 检查超时
        is_timeout = SessionEndChecker._check_timeout(session)
        if is_timeout:
            logger.info("[SessionEndChecker] Session timeout")
            return True, "timeout"
        
        # 5. 默认不结束
        return False, ""
    
    @staticmethod
    def _check_explicit_end(message: str) -> Tuple[bool, str]:
        """
        检查用户是否显式结束
        
        Args:
            message: 用户消息
            
        Returns:
            (is_explicit: bool, matched_keyword: str)
        """
        message_lower = message.lower().strip()
        
        # 移除多余空格
        message_clean = re.sub(r'\s+', '', message_lower)
        
        for keyword in EXPLICIT_END_KEYWORDS:
            keyword_clean = re.sub(r'\s+', '', keyword.lower())
            if keyword_clean in message_clean:
                return True, keyword
        
        # 检查单独的"好的"、"谢谢"等
        simple_end_words = ["好的", "谢谢", "感谢", "明白了", "知道了"]
        if message_clean in simple_end_words:
            return True, message_clean
        
        return False, ""
    
    @staticmethod
    def _check_task_completed(session: SessionState) -> Tuple[bool, str]:
        """
        检查任务是否完成闭环
        
        Args:
            session: 会话状态
            
        Returns:
            (is_completed: bool, reason: str)
        """
        # 检查resolution_status（优先级最高）
        if session.resolution_status:
            # 已解决或已留资的情况
            if session.resolution_status in ["resolved", "lead_captured"]:
                return True, f"resolution_status={session.resolution_status}"
            
            # 需要人工的情况（转人工）
            if session.resolution_status == "handoff_required":
                return True, "handoff"
            
            # 未解决的情况也需要结束（后续会跟进）
            if session.resolution_status == "unresolved":
                return True, "unresolved_issue"
        
        # 如果没有任何对话，不判断为完成
        if session.turn_count == 0:
            return False, ""
        
        return False, ""
    
    @staticmethod
    def _check_timeout(session: SessionState) -> bool:
        """
        检查会话是否超时
        
        Args:
            session: 会话状态
            
        Returns:
            is_timeout: bool
        """
        if not session.last_message_at:
            return False
        
        try:
            # 解析最后消息时间
            last_time = datetime.fromisoformat(session.last_message_at.replace('+08:00', '+08:00'))
            now = datetime.now(BEIJING_TZ)
            
            # 计算时间差
            elapsed = (now - last_time).total_seconds()
            
            if elapsed > TIMEOUT_THRESHOLD:
                logger.info(f"[SessionEndChecker] Timeout: {elapsed:.0f}s > {TIMEOUT_THRESHOLD}s")
                return True
            
        except Exception as e:
            logger.error(f"[SessionEndChecker] Error parsing time: {e}")
        
        return False
    
    @staticmethod
    def check_for_handoff(session: SessionState) -> bool:
        """
        检查是否需要转人工
        
        Args:
            session: 会话状态
            
        Returns:
            needs_handoff: bool
        """
        # 如果resolution_status设置为handoff_required
        if session.resolution_status == "handoff_required":
            return True
        
        # 可以添加其他转人工的判断逻辑
        # 例如：连续失败次数、用户明确要求等
        
        return False
    
    @staticmethod
    def check_for_error(session: SessionState) -> bool:
        """
        检查是否存在异常中止
        
        Args:
            session: 会话状态
            
        Returns:
            has_error: bool
        """
        # 如果resolution_status设置为unresolved且follow_up_needed为True
        if session.resolution_status == "unresolved" and session.follow_up_needed:
            return True
        
        return False


def should_end_session(session: SessionState, latest_user_message: str = "") -> Tuple[bool, str]:
    """
    判断会话是否应该结束的便捷函数
    
    Args:
        session: 当前会话状态
        latest_user_message: 最新的用户消息（可选）
        
    Returns:
        (should_end: bool, reason: str)
    """
    return SessionEndChecker.check_should_end(session, latest_user_message)
