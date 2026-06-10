#!/usr/bin/env python3
"""
Coze API Token 管理器
自动获取和刷新 JWT OAuth Token，避免 Token 过期问题

配置方式：
1. JWT密钥配置（仅从环境变量读取）
2. 备用静态Token: COZE_BACKUP_PAT_TOKEN / COZE_BEARER_TOKEN / COZE_API_TOKEN
"""
from __future__ import annotations

import json
import logging
import os
import time
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import jwt
import requests
from cryptography.hazmat.primitives import serialization

logger = logging.getLogger(__name__)


def _normalize_private_key(private_key: str) -> str:
    """将环境变量中的私钥标准化为 PEM 文本。"""
    key = (private_key or "").strip()
    if not key:
        return ""
    key = key.replace("\\n", "\n")
    if "BEGIN PRIVATE KEY" in key:
        return key
    if "BEGIN RSA PRIVATE KEY" in key:
        return key
    return f"-----BEGIN PRIVATE KEY-----\n{key}\n-----END PRIVATE KEY-----"


class CozeTokenManager:
    """Coze OAuth Token 管理器"""
    
    COZE_API_BASE = "api.coze.cn"
    DEFAULT_JWT_OAUTH_CLIENT_ID = "1170095929405"

    def __init__(self) -> None:
        self.jwt_oauth_client_id = os.getenv(
            "COZE_JWT_OAUTH_CLIENT_ID",
            self.DEFAULT_JWT_OAUTH_CLIENT_ID,
        )
        self.key_configs = self._load_key_configs()
        self.backup_pat_token = _first_non_empty(
            os.getenv("COZE_BACKUP_PAT_TOKEN"),
            os.getenv("COZE_BEARER_TOKEN"),
            os.getenv("COZE_API_TOKEN"),
            os.getenv("COZE_API_KEY"),
            os.getenv("COZE_ACCESS_TOKEN"),
            os.getenv("COZE_WORKLOAD_IDENTITY_API_KEY"),
        )
        self.current_access_token: Optional[str] = None
        self.token_expires_at: Optional[datetime] = None
        self.last_successful_key_index = 0

    def _load_key_configs(self) -> List[Dict[str, str]]:
        """加载JWT密钥配置（仅支持环境变量/外部文件）。"""
        # 1) JSON 字符串 / JSON 文件
        raw_json = _first_non_empty(os.getenv("COZE_KEY_CONFIGS_JSON"))
        config_file = _first_non_empty(os.getenv("COZE_KEY_CONFIGS_FILE"))
        
        if not raw_json and config_file and os.path.exists(config_file):
            with open(config_file, "r", encoding="utf-8") as file_handle:
                raw_json = file_handle.read()

        normalized: List[Dict[str, str]] = []
        if raw_json:
            try:
                parsed = json.loads(raw_json)
            except json.JSONDecodeError as exc:
                logger.error(f"[CozeTokenManager] Invalid COZE_KEY_CONFIGS_JSON: {exc}")
                parsed = []

            if isinstance(parsed, list):
                for item in parsed:
                    if not isinstance(item, dict):
                        continue
                    public_key_id = str(item.get("public_key_id") or "").strip()
                    private_key = _normalize_private_key(str(item.get("private_key") or ""))
                    if not public_key_id or not private_key:
                        continue
                    normalized.append({
                        "name": str(item.get("name") or f"key_{len(normalized) + 1}"),
                        "public_key_id": public_key_id,
                        "private_key": private_key,
                    })
            else:
                logger.error("[CozeTokenManager] COZE_KEY_CONFIGS_JSON must be a JSON array")

        # 2) 分项环境变量：COZE_KEY_1_*, COZE_KEY_2_* ...
        for index in range(1, 11):
            public_key_id = _first_non_empty(os.getenv(f"COZE_KEY_{index}_PUBLIC_KEY_ID"))
            private_key = _first_non_empty(os.getenv(f"COZE_KEY_{index}_PRIVATE_KEY"))
            if not public_key_id or not private_key:
                continue
            normalized.append({
                "name": _first_non_empty(os.getenv(f"COZE_KEY_{index}_NAME")) or f"key_{index}",
                "public_key_id": public_key_id,
                "private_key": _normalize_private_key(private_key),
            })

        # 3) 单组环境变量：COZE_JWT_PUBLIC_KEY_ID + COZE_JWT_PRIVATE_KEY
        single_public_key_id = _first_non_empty(os.getenv("COZE_JWT_PUBLIC_KEY_ID"))
        single_private_key = _first_non_empty(os.getenv("COZE_JWT_PRIVATE_KEY"))
        if single_public_key_id and single_private_key:
            normalized.append({
                "name": _first_non_empty(os.getenv("COZE_JWT_KEY_NAME")) or "jwt_key_1",
                "public_key_id": single_public_key_id,
                "private_key": _normalize_private_key(single_private_key),
            })

        # 去重：按 public_key_id 保留第一条
        deduped: List[Dict[str, str]] = []
        seen_kid = set()
        for item in normalized:
            kid = item["public_key_id"]
            if kid in seen_kid:
                continue
            seen_kid.add(kid)
            deduped.append(item)

        if not deduped:
            logger.info("[CozeTokenManager] No JWT key configs found in environment, will use fallback token")
            return []

        normalized = deduped
        logger.info(f"[CozeTokenManager] Loaded {len(normalized)} JWT key configs")
        return normalized

    def get_access_token(self) -> str:
        """
        获取有效的access token（自动刷新）
        
        策略：
        1. 检查缓存的token是否有效
        2. 如果无效，尝试JWT OAuth获取新token
        3. 如果JWT失败，使用备用静态token作为fallback
        """
        if self._is_token_valid():
            return self.current_access_token or ""
        
        try:
            if not self.key_configs:
                # 没有JWT配置，使用备用静态token
                if self.backup_pat_token:
                    logger.info("[CozeTokenManager] Using fallback static token")
                    return self.backup_pat_token
                raise RuntimeError("No Coze JWT key configs or backup token configured")
            
            return self._fetch_new_access_token()
        except Exception as exc:
            logger.error(f"[CozeTokenManager] Failed to fetch OAuth token: {exc}")
            if self.backup_pat_token:
                logger.warning("[CozeTokenManager] Falling back to static token")
                return self.backup_pat_token
            raise

    def _is_token_valid(self) -> bool:
        """检查当前token是否有效（提前5分钟刷新）"""
        if not self.current_access_token or not self.token_expires_at:
            return False
        return datetime.now() < (self.token_expires_at - timedelta(minutes=5))

    def _fetch_new_access_token(self) -> str:
        """使用JWT获取新的access token"""
        ordered = self.key_configs[self.last_successful_key_index:] + self.key_configs[:self.last_successful_key_index]
        
        for offset, key_config in enumerate(ordered):
            try:
                jwt_token = self._generate_jwt_token(key_config)
                token_response = self._request_access_token(jwt_token)
                access_token = token_response.get("access_token")
                
                if access_token:
                    expires_in = int(token_response.get("expires_in", 86399))
                    self.current_access_token = access_token
                    self.token_expires_at = datetime.now() + timedelta(seconds=expires_in)
                    self.last_successful_key_index = (self.last_successful_key_index + offset) % len(self.key_configs)
                    
                    logger.info(f"[CozeTokenManager] Token refreshed successfully, expires in {expires_in}s")
                    return access_token
            except Exception as exc:
                logger.warning(f"[CozeTokenManager] OAuth attempt with key '{key_config.get('name')}' failed: {exc}")
                continue
        
        raise RuntimeError("All configured Coze OAuth keys failed")

    def _generate_jwt_token(self, key_config: Dict[str, str]) -> str:
        """生成JWT token"""
        private_key = serialization.load_pem_private_key(
            key_config["private_key"].encode("utf-8"),
            password=None,
        )
        now = int(time.time())
        payload = {
            "iss": self.jwt_oauth_client_id,
            "aud": self.COZE_API_BASE,
            "iat": now,
            "exp": now + 10 * 365 * 24 * 60 * 60,  # 10年有效期
            "jti": str(uuid.uuid4()),
        }
        headers = {
            "alg": "RS256",
            "typ": "JWT",
            "kid": key_config["public_key_id"],
        }
        return jwt.encode(payload=payload, key=private_key, algorithm="RS256", headers=headers)

    def _request_access_token(self, jwt_token: str) -> Dict[str, Any]:
        """请求access token"""
        response = requests.post(
            url=f"https://{self.COZE_API_BASE}/api/permission/oauth2/token",
            headers={
                "Content-Type": "application/json; charset=UTF-8",
                "Authorization": f"Bearer {jwt_token}",
            },
            json={
                "duration_seconds": 86399,  # 接近24小时
                "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            },
            timeout=10,
        )
        response.raise_for_status()
        return response.json()

    def invalidate_token(self) -> None:
        """使当前token失效（强制刷新）"""
        self.current_access_token = None
        self.token_expires_at = None
        logger.info("[CozeTokenManager] Token invalidated")

    def get_token_info(self) -> Dict[str, Any]:
        """获取token状态信息"""
        return {
            "has_cached_token": bool(self.current_access_token),
            "token_expires_at": self.token_expires_at.isoformat() if self.token_expires_at else None,
            "is_token_valid": self._is_token_valid(),
            "last_successful_key_index": self.last_successful_key_index,
            "available_key_count": len(self.key_configs),
            "has_static_fallback": bool(self.backup_pat_token),
        }


def _first_non_empty(*values: Optional[str]) -> Optional[str]:
    """返回第一个非空值"""
    for value in values:
        if value is None:
            continue
        stripped = value.strip()
        if stripped:
            return stripped
    return None


# 全局单例
_token_manager: Optional[CozeTokenManager] = None


def get_token_manager() -> CozeTokenManager:
    """获取全局Token管理器实例"""
    global _token_manager
    if _token_manager is None:
        _token_manager = CozeTokenManager()
    return _token_manager


def get_coze_access_token() -> str:
    """获取Coze access token（便捷函数）"""
    return get_token_manager().get_access_token()


def invalidate_coze_token() -> None:
    """使token失效（便捷函数）"""
    get_token_manager().invalidate_token()
