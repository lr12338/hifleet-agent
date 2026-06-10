"""
会话状态管理器
用于维护当前会话的状态信息，包括开始时间、轮数、待处理项等
支持多用户会话隔离
"""
import time
import uuid
import threading
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field, asdict
import json
import logging

logger = logging.getLogger(__name__)

# 北京时区
BEIJING_TZ = timezone(timedelta(hours=8))

# 线程本地存储（用于多线程环境下的会话ID隔离）
_thread_local = threading.local()


@dataclass
class LeadInfo:
    """线索信息"""
    contact_name: str = ""
    contact_phone: str = ""
    contact_email: str = ""


@dataclass
class SessionState:
    """
    会话状态
    
    用于追踪单次对话的完整状态
    """
    # 会话标识
    session_id: str = ""
    conversation_round_id: str = ""
    
    # 用户信息
    user_id: str = ""
    source_channel: str = "websdk"
    
    # 时间信息
    started_at: str = ""  # ISO 8601格式
    last_message_at: str = ""  # ISO 8601格式
    ended_at: str = ""  # ISO 8601格式
    
    # 活跃时间戳（用于超时清理）
    last_activity: float = 0.0
    
    # 对话统计
    turn_count: int = 0
    
    # 用户消息历史（用于生成总结）
    user_messages: List[str] = field(default_factory=list)
    assistant_messages: List[str] = field(default_factory=list)
    
    # 待处理状态
    pending_slots: List[str] = field(default_factory=list)  # 待补全的字段
    pending_confirmation: bool = False  # 是否有待确认的操作
    
    # 线索信息
    lead_info: LeadInfo = field(default_factory=LeadInfo)
    
    # 分类与结果
    primary_category: str = ""  # 主类别
    resolution_status: str = ""  # 处理结果
    follow_up_needed: bool = False  # 是否需要跟进
    
    # 上传状态
    uploaded: bool = False
    
    # 结束原因
    end_reason: str = ""  # explicit / completed / timeout / handoff / error
    
    def __post_init__(self):
        """初始化时自动生成ID和时间戳"""
        if not self.session_id:
            self.session_id = f"sess_{uuid.uuid4().hex[:12]}"
        
        if not self.conversation_round_id:
            timestamp = datetime.now(BEIJING_TZ).strftime("%Y%m%d%H%M%S")
            self.conversation_round_id = f"{self.session_id}_{timestamp}"
        
        if not self.started_at:
            self.started_at = self._get_iso_time()
        
        if not self.last_message_at:
            self.last_message_at = self.started_at
        
        if self.last_activity == 0.0:
            self.last_activity = time.time()
    
    @staticmethod
    def _get_iso_time() -> str:
        """获取ISO 8601格式的时间字符串"""
        return datetime.now(BEIJING_TZ).strftime("%Y-%m-%dT%H:%M:%S+08:00")
    
    def add_user_message(self, message: str):
        """记录用户消息"""
        self.user_messages.append(message)
        self.turn_count += 1
        self.last_message_at = self._get_iso_time()
        self.last_activity = time.time()
        logger.info(f"[SessionState] User message #{len(self.user_messages)}: {message[:50]}...")
    
    def add_assistant_message(self, message: str):
        """记录助手消息"""
        self.assistant_messages.append(message)
        self.last_activity = time.time()
        logger.info(f"[SessionState] Assistant message #{len(self.assistant_messages)}: {message[:50]}...")
    
    def set_pending_slots(self, slots: List[str]):
        """设置待补全字段"""
        self.pending_slots = slots
        logger.info(f"[SessionState] Pending slots: {slots}")
    
    def clear_pending_slots(self):
        """清除待补全字段"""
        self.pending_slots = []
        logger.info(f"[SessionState] Pending slots cleared")
    
    def set_pending_confirmation(self, pending: bool):
        """设置待确认状态"""
        self.pending_confirmation = pending
        logger.info(f"[SessionState] Pending confirmation: {pending}")
    
    def update_lead_info(self, name: str = "", phone: str = "", email: str = ""):
        """更新线索信息"""
        if name:
            self.lead_info.contact_name = name
        if phone:
            self.lead_info.contact_phone = phone
        if email:
            self.lead_info.contact_email = email
        logger.info(f"[SessionState] Lead info updated: name={name}, phone={phone}, email={email}")
    
    def set_category(self, category: str):
        """设置主类别"""
        self.primary_category = category
        logger.info(f"[SessionState] Primary category: {category}")
    
    def set_resolution_status(self, status: str):
        """设置处理结果"""
        self.resolution_status = status
        logger.info(f"[SessionState] Resolution status: {status}")
    
    def set_follow_up_needed(self, needed: bool):
        """设置是否需要跟进"""
        self.follow_up_needed = needed
        logger.info(f"[SessionState] Follow up needed: {needed}")
    
    def end_session(self, reason: str = "completed"):
        """结束会话"""
        self.ended_at = self._get_iso_time()
        self.end_reason = reason
        logger.info(f"[SessionState] Session ended: reason={reason}, turn_count={self.turn_count}")
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        result = asdict(self)
        result['lead_info'] = asdict(self.lead_info)
        return result
    
    def to_json(self) -> str:
        """转换为JSON字符串"""
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)


class SessionStateManager:
    """
    会话状态管理器（支持多用户）
    
    用于管理多个活跃的会话状态，实现用户隔离
    """
    _instance = None
    _sessions: Dict[str, SessionState] = {}  # session_id -> SessionState
    
    # 配置
    _max_sessions = 1000  # 最大会话数
    _session_timeout = 1800  # 会话超时时间（秒），30分钟
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def get_session(self, session_id: str = None, user_id: str = "", source_channel: str = "websdk") -> SessionState:
        """
        获取或创建指定会话
        
        Args:
            session_id: 会话ID，如果为None则自动生成
            user_id: 用户ID
            source_channel: 来源渠道
            
        Returns:
            SessionState: 会话状态对象
        """
        if session_id is None:
            session_id = f"sess_{uuid.uuid4().hex[:12]}"
            logger.info(f"[SessionManager] Generated new session_id: {session_id}")
        
        # 清理过期会话
        self._cleanup_expired_sessions()
        
        if session_id not in self._sessions:
            # 创建新会话
            self._sessions[session_id] = SessionState(
                session_id=session_id,
                user_id=user_id,
                source_channel=source_channel
            )
            logger.info(
                f"[SessionManager] Created new session: "
                f"session_id={session_id}, user_id={user_id}, channel={source_channel}"
            )
        else:
            # 检查会话是否已上传总结
            existing_session = self._sessions[session_id]
            if existing_session.uploaded:
                # 会话已上传，重置为新会话（新的对话轮次）
                logger.info(
                    f"[SessionManager] Session {session_id} already uploaded, "
                    f"resetting for new conversation round"
                )
                self._sessions[session_id] = SessionState(
                    session_id=session_id,
                    user_id=user_id,
                    source_channel=source_channel
                )
            else:
                # 更新活跃时间
                self._sessions[session_id].last_activity = time.time()
                logger.info(f"[SessionManager] Retrieved existing session: {session_id}")
        
        return self._sessions[session_id]
    
    def reset_session(self, session_id: str) -> SessionState:
        """重置指定会话（开始新的对话轮次）"""
        old_session = self._sessions.get(session_id)
        user_id = old_session.user_id if old_session else ""
        source_channel = old_session.source_channel if old_session else "websdk"
        
        self._sessions[session_id] = SessionState(
            session_id=session_id,
            user_id=user_id,
            source_channel=source_channel
        )
        logger.info(f"[SessionManager] Reset session: {session_id}")
        return self._sessions[session_id]
    
    def clear_session(self, session_id: str):
        """清除指定会话"""
        if session_id in self._sessions:
            logger.info(f"[SessionManager] Cleared session: {session_id}")
            del self._sessions[session_id]
    
    def _cleanup_expired_sessions(self):
        """清理过期会话"""
        current_time = time.time()
        expired = [
            sid for sid, session in self._sessions.items()
            if current_time - session.last_activity > self._session_timeout
        ]
        
        for sid in expired:
            del self._sessions[sid]
            logger.info(f"[SessionManager] Cleaned up expired session: {sid}")
        
        if expired:
            logger.info(f"[SessionManager] Cleaned up {len(expired)} expired sessions")
    
    def get_active_session_count(self) -> int:
        """获取活跃会话数"""
        return len(self._sessions)
    
    def get_all_session_ids(self) -> List[str]:
        """获取所有会话ID"""
        return list(self._sessions.keys())


# 全局单例实例
session_manager = SessionStateManager()


def set_current_session_id(session_id: str):
    """设置当前请求的会话ID（线程安全）"""
    _thread_local.session_id = session_id
    logger.info(f"[SessionState] Set current session_id: {session_id}")


def get_current_session_id() -> Optional[str]:
    """获取当前请求的会话ID（线程安全）"""
    return getattr(_thread_local, 'session_id', None)


def get_session_state(session_id: str = None) -> SessionState:
    """
    获取会话状态的便捷函数
    
    Args:
        session_id: 会话ID，如果为None则使用当前请求的会话ID
        
    Returns:
        SessionState: 会话状态对象
    """
    # 优先使用传入的 session_id，否则使用当前请求的会话ID
    actual_session_id = session_id or get_current_session_id()
    
    if actual_session_id:
        return session_manager.get_session(actual_session_id)
    else:
        # 如果都没有，创建新会话
        logger.warning("[SessionState] No session_id available, creating new session")
        return session_manager.get_session()
