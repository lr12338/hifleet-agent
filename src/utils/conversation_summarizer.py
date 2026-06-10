"""
会话总结生成器
将本次对话转成数据库所需的结构化记录
"""
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone, timedelta
import logging
import re

from utils.session_state import SessionState

logger = logging.getLogger(__name__)

# 北京时区
BEIJING_TZ = timezone(timedelta(hours=8))


class ConversationSummarizer:
    """
    会话总结生成器
    
    根据会话状态生成符合数据库结构的总结记录
    """
    
    # 主类别关键词映射
    CATEGORY_KEYWORDS = {
        "使用需求": [
            "功能", "怎么用", "如何使用", "怎么注册", "怎么登录", 
            "会员", "价格", "收费", "账号", "密码", "权限",
            "平台", "介绍", "什么", "FAQ", "帮助", "教程"
        ],
        "生产需求": [
            "船位", "查询", "位置", "MMSI", "IMO", "船名",
            "PSC", "档案", "历史", "航程", "港口", "目的港",
            "更新", "修改", "区域", "海峡", "统计", "船舶"
        ],
        "商务需求": [
            "试用", "报价", "合作", "联系销售", "商务",
            "采购", "购买", "付费", "合作意向", "试用一下"
        ],
        "问题反馈": [
            "问题", "异常", "错误", "失败", "投诉", "不满",
            "故障", "不能用", "不对", "不正确", "不符合"
        ],
    }
    
    @staticmethod
    def generate_summary(session: SessionState, source_channel: str = "websdk") -> Dict[str, Any]:
        """
        生成会话总结记录
        
        Args:
            session: 当前会话状态
            source_channel: 来源渠道
            
        Returns:
            符合数据库字段结构的总结记录（所有值均为字符串）
        """
        # 1. 确定主类别
        primary_category = ConversationSummarizer._determine_category(session)
        
        # 2. 确定处理结果
        resolution_status = ConversationSummarizer._determine_resolution_status(session)
        
        # 3. 确定是否需要跟进
        follow_up_needed = ConversationSummarizer._determine_follow_up(session)
        
        # 4. 生成总结内容
        summary_content = ConversationSummarizer._generate_summary_content(session)
        
        # 5. 构建记录
        now = datetime.now(BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S")
        
        # 转换时间格式：ISO 8601 -> YYYY-MM-DD HH:MM:SS
        started_at = session.started_at.replace("T", " ").split("+")[0] if session.started_at else now
        ended_at = (session.ended_at.replace("T", " ").split("+")[0] if session.ended_at else now)
        
        record = {
            "conversation_round_id": session.conversation_round_id,
            "session_id": session.session_id,
            "source_channel": source_channel,
            "started_at": started_at,
            "ended_at": ended_at,
            "turn_count": str(session.turn_count),
            "primary_category": primary_category,
            "summary_content": summary_content,
            "contact_name": session.lead_info.contact_name,
            "contact_phone": session.lead_info.contact_phone,
            "contact_email": session.lead_info.contact_email,
            "resolution_status": resolution_status,
            "follow_up_needed": "true" if follow_up_needed else "false",
            "uploaded_at": now,
        }
        
        logger.info(f"[ConversationSummarizer] Generated summary: category={primary_category}, status={resolution_status}")
        
        return record
    
    @staticmethod
    def _determine_category(session: SessionState) -> str:
        """
        确定主类别
        
        Args:
            session: 会话状态
            
        Returns:
            主类别字符串
        """
        # 如果已经设置了类别，直接使用
        if session.primary_category:
            return session.primary_category
        
        # 否则根据用户消息判断
        all_messages = " ".join(session.user_messages).lower()
        
        category_scores = {}
        for category, keywords in ConversationSummarizer.CATEGORY_KEYWORDS.items():
            score = sum(1 for kw in keywords if kw.lower() in all_messages)
            category_scores[category] = score
        
        # 找出得分最高的类别
        max_score = max(category_scores.values()) if category_scores else 0
        
        if max_score == 0:
            return "其他"
        
        # 检查是否有多个类别得分相同且都大于0
        top_categories = [cat for cat, score in category_scores.items() if score == max_score]
        
        if len(top_categories) > 1:
            return "混合需求"
        
        return top_categories[0]
    
    @staticmethod
    def _determine_resolution_status(session: SessionState) -> str:
        """
        确定处理结果
        
        Args:
            session: 会话状态
            
        Returns:
            处理结果字符串
        """
        # 如果已经设置了处理结果，直接使用
        if session.resolution_status:
            return session.resolution_status
        
        # 否则根据状态判断
        # 如果有联系方式且是商务需求，则为已留资
        has_contact = (
            session.lead_info.contact_phone or 
            session.lead_info.contact_email or 
            session.lead_info.contact_name
        )
        
        if has_contact:
            return "lead_captured"
        
        # 如果有助手回复且没有待处理项，假设已解决
        if session.assistant_messages and not session.pending_slots and not session.pending_confirmation:
            return "resolved"
        
        # 默认部分解决
        return "partially_resolved"
    
    @staticmethod
    def _determine_follow_up(session: SessionState) -> bool:
        """
        确定是否需要跟进
        
        Args:
            session: 会话状态
            
        Returns:
            是否需要跟进
        """
        # 如果已经设置了，直接使用
        if session.follow_up_needed:
            return True
        
        # 如果有联系方式（商务线索）
        has_contact = (
            session.lead_info.contact_phone or 
            session.lead_info.contact_email
        )
        
        if has_contact:
            return True
        
        # 如果处理结果为未解决或需要人工
        if session.resolution_status in ["unresolved", "handoff_required"]:
            return True
        
        return False
    
    @staticmethod
    def _generate_summary_content(session: SessionState) -> str:
        """
        生成总结内容
        
        Args:
            session: 会话状态
            
        Returns:
            总结内容字符串（1段自然语言）
        """
        parts = []
        
        # 1. 用户想做什么
        if session.user_messages:
            user_intent = session.user_messages[0] if session.user_messages else ""
            if len(user_intent) > 100:
                user_intent = user_intent[:100] + "..."
            parts.append(f"用户咨询：{user_intent}")
        else:
            parts.append("用户发起对话")
        
        # 2. 系统做了什么
        if session.assistant_messages:
            if len(session.assistant_messages) > 0:
                system_action = "系统已回复用户问题"
                if session.turn_count > 1:
                    system_action += f"，共{session.turn_count}轮对话"
                parts.append(system_action)
        
        # 3. 最终结果如何
        if session.resolution_status == "resolved":
            parts.append("问题已解决")
        elif session.resolution_status == "lead_captured":
            parts.append("已收集用户联系方式")
        elif session.resolution_status == "handoff_required":
            parts.append("需要人工介入")
        elif session.resolution_status == "unresolved":
            parts.append("问题未解决")
        else:
            parts.append("对话已结束")
        
        # 4. 是否留下联系方式
        if session.lead_info.contact_phone or session.lead_info.contact_email:
            contact_parts = []
            if session.lead_info.contact_name:
                contact_parts.append(f"姓名：{session.lead_info.contact_name}")
            if session.lead_info.contact_phone:
                contact_parts.append(f"手机：{session.lead_info.contact_phone}")
            if session.lead_info.contact_email:
                contact_parts.append(f"邮箱：{session.lead_info.contact_email}")
            
            parts.append(f"用户联系方式：{', '.join(contact_parts)}")
        
        # 组合总结
        summary = "。".join(parts) + "。"
        
        return summary


def generate_conversation_summary(session: SessionState, source_channel: str = "websdk") -> Dict[str, Any]:
    """
    生成会话总结的便捷函数
    
    Args:
        session: 当前会话状态
        source_channel: 来源渠道
        
    Returns:
        符合数据库字段结构的总结记录
    """
    return ConversationSummarizer.generate_summary(session, source_channel)
