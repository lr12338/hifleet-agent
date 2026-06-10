"""
微信服务号消息解析器
解析 Hicargo 服务端预处理后的消息格式
"""
import re
import logging
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class WechatMessageContext:
    """微信消息上下文信息"""
    # 渠道信息
    channel: str = ""           # 渠道标识：wechat_mp
    application: str = ""       # 应用标识：hicargo_mp
    
    # 会话信息
    session_id: str = ""        # 会话ID：wx_mp_{openid}
    user_id: str = ""           # 用户ID：{openid}
    
    # 消息信息
    message_type: str = ""      # 消息类型：text/image/voice/location等
    language: str = "zh"        # 语言：zh/en
    
    # 多模态内容
    text_content: str = ""      # 文本内容
    image_url: str = ""         # 图片URL（如果有）
    location_info: Dict = None  # 位置信息（如果有）
    link_info: Dict = None      # 链接信息（如果有）
    
    # 原始内容
    raw_prefix: str = ""        # 原始前缀文本
    actual_message: str = ""    # 实际用户消息


class WechatMessageParser:
    """
    微信消息解析器
    
    处理 Hicargo 服务端预处理后的消息格式
    """
    
    # 上下文前缀解析正则
    PREFIX_PATTERN = re.compile(
        r'消息路径：([^\n]+)\n'
        r'渠道标识：([^\n]+)\n'
        r'应用标识：([^\n]+)\n'
        r'session_id：([^\n]+)\n'
        r'user_id：([^\n]+)\n'
        r'消息类型：([^\n]+)\n+'
        r'用户消息：\n(.*)',
        re.DOTALL
    )
    
    # 语言指令正则
    LANG_PATTERN_ZH = re.compile(r'请用中文回复[。，]?\s*$', re.MULTILINE)
    LANG_PATTERN_EN = re.compile(r'Please reply in English[.,]?\s*$', re.MULTILINE)
    
    @classmethod
    def parse_request(cls, request_body: Dict[str, Any]) -> WechatMessageContext:
        """
        解析微信服务号请求体
        
        请求体格式：
        {
          "content": {
            "query": {
              "prompt": [
                {"type": "text", "content": {"text": "..."}}
              ]
            }
          },
          "type": "query",
          "project_id": "...",
          "session_id": "wx_mp_{openid}",
          "user_id": "{openid}",
          "source_channel": "wechat_mp"
        }
        """
        ctx = WechatMessageContext()
        
        try:
            # 1. 提取顶层参数
            ctx.session_id = request_body.get("session_id", "")
            ctx.user_id = request_body.get("user_id", "")
            ctx.channel = request_body.get("source_channel", "wechat_mp")
            
            logger.info(f"[WechatParser] Parsing request: session_id={ctx.session_id}, user_id={ctx.user_id}")
            
            # 2. 提取 prompt 数组
            content = request_body.get("content", {})
            query = content.get("query", {})
            prompts: List[Dict] = query.get("prompt", [])
            
            if not prompts:
                logger.warning("[WechatParser] No prompt found in request")
                return ctx
            
            # 3. 解析 prompt 段落
            for prompt in prompts:
                prompt_type = prompt.get("type", "text")
                prompt_content = prompt.get("content", {})
                
                if prompt_type == "text":
                    text = prompt_content.get("text", "")
                    ctx.text_content += text
                    
                elif prompt_type == "image":
                    ctx.image_url = prompt_content.get("url", "")
                    logger.info(f"[WechatParser] Found image: {ctx.image_url[:50]}...")
                    
                elif prompt_type == "location":
                    ctx.location_info = {
                        "latitude": prompt_content.get("latitude"),
                        "longitude": prompt_content.get("longitude"),
                        "label": prompt_content.get("label", ""),
                        "address": prompt_content.get("address", "")
                    }
                    logger.info(f"[WechatParser] Found location: {ctx.location_info}")
                    
                elif prompt_type == "link":
                    ctx.link_info = {
                        "title": prompt_content.get("title", ""),
                        "description": prompt_content.get("description", ""),
                        "url": prompt_content.get("url", "")
                    }
                    logger.info(f"[WechatParser] Found link: {ctx.link_info}")
            
            # 4. 解析文本内容中的上下文前缀
            cls._parse_text_prefix(ctx)
            
            # 5. 检测语言指令
            cls._detect_language(ctx)
            
            logger.info(
                f"[WechatParser] Parsed context: "
                f"channel={ctx.channel}, app={ctx.application}, "
                f"type={ctx.message_type}, lang={ctx.language}"
            )
            
            return ctx
            
        except Exception as e:
            logger.error(f"[WechatParser] Parse error: {str(e)}", exc_info=True)
            return ctx
    
    @classmethod
    def _parse_text_prefix(cls, ctx: WechatMessageContext):
        """解析文本内容中的上下文前缀"""
        text = ctx.text_content
        
        if not text:
            return
        
        # 尝试匹配标准前缀格式
        match = cls.PREFIX_PATTERN.search(text)
        
        if match:
            # 提取前缀信息
            msg_path = match.group(1)
            ctx.channel = match.group(2) or ctx.channel
            ctx.application = match.group(3)
            # session_id 和 user_id 已从顶层提取，这里仅做验证
            parsed_session_id = match.group(4)
            parsed_user_id = match.group(5)
            ctx.message_type = match.group(6)
            ctx.actual_message = match.group(7).strip()
            ctx.raw_prefix = text[:match.start()]
            
            logger.info(f"[WechatParser] Extracted prefix: msg_path={msg_path}, type={ctx.message_type}")
            
        else:
            # 没有标准前缀，整段作为实际消息
            ctx.actual_message = text.strip()
            logger.warning("[WechatParser] No standard prefix found, using full text as message")
    
    @classmethod
    def _detect_language(cls, ctx: WechatMessageContext):
        """检测语言指令"""
        text = ctx.text_content
        
        if cls.LANG_PATTERN_EN.search(text):
            ctx.language = "en"
            logger.info("[WechatParser] Language detected: English")
        elif cls.LANG_PATTERN_ZH.search(text):
            ctx.language = "zh"
            logger.info("[WechatParser] Language detected: Chinese")
        else:
            # 默认中文
            ctx.language = "zh"
            logger.info("[WechatParser] Language detected: Chinese (default)")
    
    @classmethod
    def build_agent_input(cls, ctx: WechatMessageContext) -> Dict[str, Any]:
        """
        构建 Agent 输入
        
        根据消息类型和内容构建合适的输入结构
        """
        input_data = {
            "session_id": ctx.session_id,
            "user_id": ctx.user_id,
            "source_channel": ctx.channel,
            "message_type": ctx.message_type,
            "language": ctx.language,
        }
        
        # 根据消息类型构建内容
        if ctx.message_type == "image" and ctx.image_url:
            # 图片消息：返回多模态结构
            input_data["input"] = {
                "type": "multimodal",
                "text": ctx.actual_message,
                "image_url": ctx.image_url
            }
            
        elif ctx.message_type == "location" and ctx.location_info:
            # 位置消息
            location_text = f"用户位置：{ctx.location_info.get('label', '')} {ctx.location_info.get('address', '')}"
            input_data["input"] = f"{location_text}\n\n{ctx.actual_message}"
            
        elif ctx.message_type == "link" and ctx.link_info:
            # 链接消息
            link_text = f"链接：{ctx.link_info.get('title', '')}\n{ctx.link_info.get('description', '')}\n{ctx.link_info.get('url', '')}"
            input_data["input"] = f"{link_text}\n\n{ctx.actual_message}"
            
        elif ctx.message_type == "voice":
            # 语音消息（已转文本或说明）
            input_data["input"] = ctx.actual_message
            
        elif ctx.message_type == "event":
            # 微信事件
            input_data["input"] = ctx.actual_message
            
        else:
            # 文本消息
            input_data["input"] = ctx.actual_message
        
        return input_data


def parse_wechat_request(request_body: Dict[str, Any]) -> Tuple[Dict[str, Any], WechatMessageContext]:
    """
    便捷函数：解析微信请求并返回 Agent 输入和上下文
    
    Args:
        request_body: 微信服务端发送的请求体
        
    Returns:
        (agent_input, context): Agent 输入数据 和 消息上下文对象
    """
    ctx = WechatMessageParser.parse_request(request_body)
    agent_input = WechatMessageParser.build_agent_input(ctx)
    
    return agent_input, ctx
