#!/usr/bin/env python3
"""
数据库实际插入测试脚本
"""
import os
import sys

# 添加项目路径
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)
sys.path.insert(0, os.path.join(project_root, "src"))

# 加载.env文件
from dotenv import load_dotenv
load_dotenv(os.path.join(project_root, ".env"))

import json
from datetime import datetime, timezone, timedelta

# 导入必要模块
from utils.session_state import SessionState, reset_session_state
from utils.conversation_summarizer import generate_conversation_summary
from utils.coze_database import get_coze_db_client

# 北京时区
BEIJING_TZ = timezone(timedelta(hours=8))


def test_database_insert():
    """测试数据库实际插入"""
    
    print("\n" + "=" * 60)
    print("  数据库实际插入测试")
    print("  测试时间:", datetime.now(BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S"))
    print("=" * 60)
    
    # 检查环境变量
    database_id = os.getenv("COZE_DATABASE_ID")
    print(f"\n环境变量检查:")
    print(f"  COZE_DATABASE_ID: {database_id}")
    
    if not database_id:
        print("\n❌ 错误：COZE_DATABASE_ID 未配置")
        return False
    
    # 获取数据库客户端
    client = get_coze_db_client()
    print(f"  数据库ID（客户端）: {client.database_id}")
    
    # 测试用例1：FAQ场景
    print("\n" + "-" * 60)
    print("测试用例1：FAQ完成后自动总结")
    print("-" * 60)
    
    # 重置会话
    session = reset_session_state()
    
    # 模拟对话
    print("\n[用户]: Hifleet平台有哪些功能？")
    session.add_user_message("Hifleet平台有哪些功能？")
    session.set_category("使用需求")
    
    print("[助手]: Hifleet平台主要提供船位查询、船舶档案、PSC查询等功能...")
    session.add_assistant_message("Hifleet平台主要提供船位查询、船舶档案、PSC查询等功能...")
    
    print("[用户]: 好的，明白了")
    session.add_user_message("好的，明白了")
    
    # 结束会话
    session.end_session(reason="explicit")
    session.set_resolution_status("resolved")
    
    # 生成总结
    summary = generate_conversation_summary(session, "websdk")
    
    print("\n生成的总结记录:")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    
    # 实际插入数据库
    print("\n开始插入数据库...")
    result = client.insert_summary(summary)
    
    print("\n插入结果:")
    print(f"  成功: {result.get('success')}")
    print(f"  消息: {result.get('message')}")
    
    if result.get("success"):
        print(f"  数据: {result.get('data')}")
        print("\n✅ 插入成功！")
    else:
        print(f"  详细信息: {result.get('data')}")
        print("\n❌ 插入失败")
        return False
    
    # 测试用例2：商务线索场景
    print("\n" + "-" * 60)
    print("测试用例2：商务线索")
    print("-" * 60)
    
    # 重置会话
    session = reset_session_state()
    
    # 模拟对话
    print("\n[用户]: 我想试用一下你们的平台")
    session.add_user_message("我想试用一下你们的平台")
    session.set_category("商务需求")
    
    print("[助手]: 好的，请问您的姓名是？")
    session.add_assistant_message("好的，请问您的姓名是？")
    
    print("[用户]: 我叫张三")
    session.add_user_message("我叫张三")
    session.update_lead_info(name="张三")
    
    print("[助手]: 请问您的手机号是？")
    session.add_assistant_message("请问您的手机号是？")
    
    print("[用户]: 13800138000")
    session.add_user_message("13800138000")
    session.update_lead_info(phone="13800138000")
    
    print("[助手]: 好的，已记录您的信息，我们会尽快联系您")
    session.add_assistant_message("好的，已记录您的信息，我们会尽快联系您")
    session.set_resolution_status("lead_captured")
    session.set_follow_up_needed(True)
    
    # 结束会话
    session.end_session(reason="completed")
    
    # 生成总结
    summary = generate_conversation_summary(session, "websdk")
    
    print("\n生成的总结记录:")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    
    # 实际插入数据库
    print("\n开始插入数据库...")
    result = client.insert_summary(summary)
    
    print("\n插入结果:")
    print(f"  成功: {result.get('success')}")
    print(f"  消息: {result.get('message')}")
    
    if result.get("success"):
        print(f"  数据: {result.get('data')}")
        print("\n✅ 插入成功！")
    else:
        print(f"  详细信息: {result.get('data')}")
        print("\n❌ 插入失败")
        return False
    
    print("\n" + "=" * 60)
    print("  所有测试通过！")
    print("=" * 60)
    
    return True


if __name__ == "__main__":
    try:
        success = test_database_insert()
        sys.exit(0 if success else 1)
    except Exception as e:
        print(f"\n❌ 测试失败: {str(e)}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
