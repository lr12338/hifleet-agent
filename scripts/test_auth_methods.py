#!/usr/bin/env python3
"""
测试不同的认证方式
"""
import os
import sys
import requests
import json
from dotenv import load_dotenv

# 加载环境变量
load_dotenv("/workspace/projects/.env")

database_id = os.getenv("COZE_DATABASE_ID")
token = os.getenv("COZE_WORKLOAD_IDENTITY_API_KEY")

print("=" * 60)
print("  测试不同的认证方式")
print("=" * 60)

print(f"\n数据库ID: {database_id}")
print(f"Token前20字符: {token[:20] if token else 'None'}...")

# 测试数据
test_record = {
    "conversation_round_id": "test_001",
    "session_id": "test_session",
    "source_channel": "websdk",
    "started_at": "2026-04-03T17:00:00+08:00",
    "ended_at": "2026-04-03T17:01:00+08:00",
    "turn_count": "1",
    "primary_category": "测试",
    "summary_content": "这是一条测试记录",
    "contact_name": "",
    "contact_phone": "",
    "contact_email": "",
    "resolution_status": "resolved",
    "follow_up_needed": "false",
    "uploaded_at": "2026-04-03T17:01:01+08:00"
}

url = f"https://api.coze.cn/v1/databases/{database_id}/records"
payload = {"insert_rows": [test_record], "is_async": False}

# 方式1: Bearer token
print("\n方式1: Authorization: Bearer {token}")
headers1 = {
    "Authorization": f"Bearer {token}",
    "Content-Type": "application/json"
}
response = requests.post(url, headers=headers1, json=payload, timeout=30)
print(f"  状态码: {response.status_code}")
print(f"  响应: {response.text[:200]}")

# 方式2: 直接使用token（无Bearer前缀）
print("\n方式2: Authorization: {token}")
headers2 = {
    "Authorization": token,
    "Content-Type": "application/json"
}
response = requests.post(url, headers=headers2, json=payload, timeout=30)
print(f"  状态码: {response.status_code}")
print(f"  响应: {response.text[:200]}")

# 方式3: 使用x-api-key
print("\n方式3: x-api-key: {token}")
headers3 = {
    "x-api-key": token,
    "Content-Type": "application/json"
}
response = requests.post(url, headers=headers3, json=payload, timeout=30)
print(f"  状态码: {response.status_code}")
print(f"  响应: {response.text[:200]}")

# 方式4: 使用access_token
print("\n方式4: access_token: {token}")
headers4 = {
    "access_token": token,
    "Content-Type": "application/json"
}
response = requests.post(url, headers=headers4, json=payload, timeout=30)
print(f"  状态码: {response.status_code}")
print(f"  响应: {response.text[:200]}")

print("\n" + "=" * 60)
