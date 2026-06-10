"""
Coze 数据库插入工具
用于将会话总结插入到 Coze 数据库
"""
import os
import json
import logging
from typing import Dict, Any, Optional
import requests
from datetime import datetime

from utils.coze_token_manager import get_token_manager

logger = logging.getLogger(__name__)

# Coze 数据库ID（cs_conv_summary 表）
DATABASE_ID = "7624446992558604342"


class CozeDatabaseClient:
    """
    Coze 数据库客户端
    
    使用 Coze 官方数据库 API 进行数据插入和更新
    自动管理 Token，避免过期问题
    """
    
    def __init__(self):
        """初始化客户端"""
        self.api_base = os.getenv("COZE_API_BASE", "https://api.coze.cn")
        
        # 使用硬编码的数据库ID
        self.database_id = DATABASE_ID

    def _get_access_token(self) -> str:
        """获取有效的access token（使用JWT自动刷新）"""
        return get_token_manager().get_access_token()
    
    def insert_summary(self, record: Dict[str, Any]) -> Dict[str, Any]:
        """
        插入会话总结记录
        
        Args:
            record: 符合数据库字段结构的记录
            
        Returns:
            {
                "success": bool,
                "message": str,
                "data": Optional[Dict]  # API返回的数据
            }
        """
        try:
            access_token = self._get_access_token()
        except Exception as e:
            logger.error(f"[CozeDatabase] Cannot insert: {e}")
            return {
                "success": False,
                "message": f"Access token error: {e}",
                "data": None
            }
        
        url = f"{self.api_base}/v1/databases/{self.database_id}/records"
        
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json"
        }
        
        # 构建插入数据
        payload = {
            "insert_rows": [record],
            "is_async": False  # 同步插入
        }
        
        logger.info(f"[CozeDatabase] Inserting summary: conversation_round_id={record.get('conversation_round_id')}")
        
        try:
            response = requests.post(url, headers=headers, json=payload, timeout=30)
            
            logger.info(f"[CozeDatabase] Response status: {response.status_code}")
            logger.debug(f"[CozeDatabase] Response body: {response.text}")
            
            if response.status_code == 200:
                result = response.json()
                
                # 检查返回码
                if result.get("code") == 0:
                    logger.info(f"[CozeDatabase] Insert successful: {result.get('data')}")
                    return {
                        "success": True,
                        "message": "Insert successful",
                        "data": result.get("data")
                    }
                else:
                    error_msg = result.get("msg", "Unknown error")
                    logger.error(f"[CozeDatabase] Insert failed: {error_msg}")
                    return {
                        "success": False,
                        "message": error_msg,
                        "data": result
                    }
            else:
                logger.error(f"[CozeDatabase] HTTP error: {response.status_code} - {response.text}")
                return {
                    "success": False,
                    "message": f"HTTP error: {response.status_code}",
                    "data": {"status_code": response.status_code, "body": response.text}
                }
                
        except requests.exceptions.Timeout:
            logger.error("[CozeDatabase] Request timeout")
            return {
                "success": False,
                "message": "Request timeout",
                "data": None
            }
        except requests.exceptions.RequestException as e:
            logger.error(f"[CozeDatabase] Request error: {str(e)}")
            return {
                "success": False,
                "message": f"Request error: {str(e)}",
                "data": None
            }
        except Exception as e:
            logger.error(f"[CozeDatabase] Unexpected error: {str(e)}", exc_info=True)
            return {
                "success": False,
                "message": f"Unexpected error: {str(e)}",
                "data": None
            }
    
    def update_summary(
        self,
        conversation_round_id: str,
        update_fields: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        更新会话总结记录
        
        Args:
            conversation_round_id: 单次对话ID
            update_fields: 要更新的字段
            
        Returns:
            {
                "success": bool,
                "message": str,
                "data": Optional[Dict]
            }
        """
        try:
            access_token = self._get_access_token()
        except Exception as e:
            logger.error(f"[CozeDatabase] Cannot update: {e}")
            return {
                "success": False,
                "message": f"Access token error: {e}",
                "data": None
            }
        
        url = f"{self.api_base}/v1/databases/{self.database_id}/records"
        
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json"
        }
        
        # 构建 update_fields 格式
        fields_list = [
            {"field_name": key, "value": str(value)}
            for key, value in update_fields.items()
        ]
        
        # 构建更新数据
        payload = {
            "update_fields": fields_list,
            "filter": {
                "logic": "and",
                "conditions": [
                    {
                        "left": "conversation_round_id",
                        "operation": "equal",
                        "right": conversation_round_id
                    }
                ]
            },
            "is_async": False  # 同步更新
        }
        
        logger.info(f"[CozeDatabase] Updating summary: conversation_round_id={conversation_round_id}")
        
        try:
            response = requests.put(url, headers=headers, json=payload, timeout=30)
            
            logger.info(f"[CozeDatabase] Response status: {response.status_code}")
            logger.debug(f"[CozeDatabase] Response body: {response.text}")
            
            if response.status_code == 200:
                result = response.json()
                
                # 检查返回码
                if result.get("code") == 0:
                    logger.info(f"[CozeDatabase] Update successful: {result.get('data')}")
                    return {
                        "success": True,
                        "message": "Update successful",
                        "data": result.get("data")
                    }
                else:
                    error_msg = result.get("msg", "Unknown error")
                    logger.error(f"[CozeDatabase] Update failed: {error_msg}")
                    return {
                        "success": False,
                        "message": error_msg,
                        "data": result
                    }
            else:
                logger.error(f"[CozeDatabase] HTTP error: {response.status_code} - {response.text}")
                return {
                    "success": False,
                    "message": f"HTTP error: {response.status_code}",
                    "data": {"status_code": response.status_code, "body": response.text}
                }
                
        except requests.exceptions.Timeout:
            logger.error("[CozeDatabase] Request timeout")
            return {
                "success": False,
                "message": "Request timeout",
                "data": None
            }
        except requests.exceptions.RequestException as e:
            logger.error(f"[CozeDatabase] Request error: {str(e)}")
            return {
                "success": False,
                "message": f"Request error: {str(e)}",
                "data": None
            }
        except Exception as e:
            logger.error(f"[CozeDatabase] Unexpected error: {str(e)}", exc_info=True)
            return {
                "success": False,
                "message": f"Unexpected error: {str(e)}",
                "data": None
            }


# 全局单例
_coze_db_client = None


def get_coze_db_client() -> CozeDatabaseClient:
    """获取 Coze 数据库客户端实例"""
    global _coze_db_client
    if _coze_db_client is None:
        _coze_db_client = CozeDatabaseClient()
    return _coze_db_client


def insert_conversation_summary(record: Dict[str, Any]) -> Dict[str, Any]:
    """
    插入会话总结的便捷函数
    
    Args:
        record: 符合数据库字段结构的记录
        
    Returns:
        {
            "success": bool,
            "message": str,
            "data": Optional[Dict]
        }
    """
    client = get_coze_db_client()
    return client.insert_summary(record)


def update_conversation_summary(
    conversation_round_id: str,
    update_fields: Dict[str, Any]
) -> Dict[str, Any]:
    """
    更新会话总结的便捷函数
    
    Args:
        conversation_round_id: 单次对话ID
        update_fields: 要更新的字段
        
    Returns:
        {
            "success": bool,
            "message": str,
            "data": Optional[Dict]
        }
    """
    client = get_coze_db_client()
    return client.update_summary(conversation_round_id, update_fields)
