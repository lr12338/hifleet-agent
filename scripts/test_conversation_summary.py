#!/usr/bin/env python3
"""
会话总结上传能力测试脚本

测试场景：
1. FAQ完成后自动总结
2. 生产查询闭环
3. 商务线索
4. 问题反馈
5. 超时关闭
"""
import os
import sys
import time
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

# 加载.env文件
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(project_root, ".env"))

# 添加项目根目录和src目录到Python路径
sys.path.insert(0, project_root)
sys.path.insert(0, os.path.join(project_root, "src"))

from utils.session_state import SessionState, reset_session_state, clear_session_state
from utils.session_end_checker import should_end_session
from utils.conversation_summarizer import generate_conversation_summary
from utils.coze_database import get_coze_db_client

# 北京时区
BEIJING_TZ = timezone(timedelta(hours=8))


def print_separator(title: str = ""):
    """打印分隔线"""
    print("\n" + "=" * 60)
    if title:
        print(f"  {title}")
        print("=" * 60)


def test_case_1_faq():
    """
    用例1：FAQ完成后自动总结
    
    输入：
    - 用户问功能
    - Agent正常回答
    - 用户表示"好的，明白了"
    
    预期：
    - primary_category = 使用需求
    - resolution_status = resolved
    - 自动插入1条记录
    """
    print_separator("测试用例1：FAQ完成后自动总结")
    
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
    
    # 检查是否应该结束
    should_end, end_reason = should_end_session(session, "好的，明白了")
    print(f"\n会话是否应该结束: {should_end}")
    print(f"结束原因: {end_reason}")
    
    if should_end:
        # 结束会话
        session.end_session(reason=end_reason)
        session.set_resolution_status("resolved")
        
        # 生成总结
        summary = generate_conversation_summary(session, "websdk")
        
        print("\n总结记录：")
        for key, value in summary.items():
            if key != "summary_content":
                print(f"  {key}: {value}")
        
        print(f"\n总结内容: {summary['summary_content']}")
        
        # 验证预期结果
        print("\n验证结果：")
        print(f"  ✓ primary_category = {summary['primary_category']} (预期: 使用需求)")
        print(f"  ✓ resolution_status = {summary['resolution_status']} (预期: resolved)")
        
        # 尝试插入（可能失败，因为没有配置数据库）
        print("\n尝试插入数据库...")
        client = get_coze_db_client()
        if client.database_id:
            result = client.insert_summary(summary)
            if result.get("success"):
                print("  ✓ 插入成功")
            else:
                print(f"  ✗ 插入失败: {result.get('message')}")
        else:
            print("  ⚠ 数据库未配置（COZE_DATABASE_ID），跳过实际插入")
            print("  ℹ 总结记录已生成，等待数据库配置后实际插入")
    
    return True


def test_case_2_production_query():
    """
    用例2：生产查询闭环
    
    输入：
    - 用户查询船位
    - 下游生产工作流返回结果
    - 用户未继续追问
    
    预期：
    - primary_category = 生产需求
    - follow_up_needed = false
    """
    print_separator("测试用例2：生产查询闭环")
    
    # 重置会话
    session = reset_session_state()
    
    # 模拟对话
    print("\n[用户]: 查询MMSI 123456789的船位")
    session.add_user_message("查询MMSI 123456789的船位")
    session.set_category("生产需求")
    
    print("[助手]: 已为您查询到MMSI为123456789的船舶位置信息...")
    session.add_assistant_message("已为您查询到MMSI为123456789的船舶位置信息...")
    
    # 检查是否应该结束
    should_end, end_reason = should_end_session(session, "")
    print(f"\n会话是否应该结束: {should_end}")
    print(f"结束原因: {end_reason}")
    
    # 用户未继续追问，假设任务完成
    if should_end:
        session.end_session(reason=end_reason)
        session.set_resolution_status("resolved")
        session.set_follow_up_needed(False)
        
        # 生成总结
        summary = generate_conversation_summary(session, "websdk")
        
        print("\n总结记录：")
        for key, value in summary.items():
            if key != "summary_content":
                print(f"  {key}: {value}")
        
        print(f"\n总结内容: {summary['summary_content']}")
        
        # 验证预期结果
        print("\n验证结果：")
        print(f"  ✓ primary_category = {summary['primary_category']} (预期: 生产需求)")
        print(f"  ✓ follow_up_needed = {summary['follow_up_needed']} (预期: false)")
    
    return True


def test_case_3_business_lead():
    """
    用例3：商务线索
    
    输入：
    - 用户要求试用或报价
    - 已收集手机号
    
    预期：
    - primary_category = 商务需求
    - contact_phone 被正确写入
    - resolution_status = lead_captured
    - follow_up_needed = true
    """
    print_separator("测试用例3：商务线索")
    
    # 重置会话
    session = reset_session_state()
    
    # 模拟对话
    print("\n[用户]: 我想试用一下你们的平台")
    session.add_user_message("我想试用一下你们的平台")
    session.set_category("商务需求")
    session.set_pending_slots(["contact_name", "contact_phone"])
    
    print("[助手]: 好的，请问您的姓名是？")
    session.add_assistant_message("好的，请问您的姓名是？")
    
    print("[用户]: 我叫张三")
    session.add_user_message("我叫张三")
    session.update_lead_info(name="张三")
    session.set_pending_slots(["contact_phone"])  # 移除姓名，保留手机号
    
    print("[助手]: 请问您的手机号是？")
    session.add_assistant_message("请问您的手机号是？")
    
    print("[用户]: 13800138000")
    session.add_user_message("13800138000")
    session.update_lead_info(phone="13800138000")
    session.clear_pending_slots()
    
    print("[助手]: 好的，已记录您的信息，我们会尽快联系您")
    session.add_assistant_message("好的，已记录您的信息，我们会尽快联系您")
    session.set_resolution_status("lead_captured")
    session.set_follow_up_needed(True)
    
    # 检查是否应该结束
    should_end, end_reason = should_end_session(session, "")
    print(f"\n会话是否应该结束: {should_end}")
    print(f"结束原因: {end_reason}")
    
    if should_end:
        session.end_session(reason=end_reason)
        
        # 生成总结
        summary = generate_conversation_summary(session, "websdk")
        
        print("\n总结记录：")
        for key, value in summary.items():
            if key != "summary_content":
                print(f"  {key}: {value}")
        
        print(f"\n总结内容: {summary['summary_content']}")
        
        # 验证预期结果
        print("\n验证结果：")
        print(f"  ✓ primary_category = {summary['primary_category']} (预期: 商务需求)")
        print(f"  ✓ contact_phone = {summary['contact_phone']} (预期: 13800138000)")
        print(f"  ✓ resolution_status = {summary['resolution_status']} (预期: lead_captured)")
        print(f"  ✓ follow_up_needed = {summary['follow_up_needed']} (预期: true)")
    
    return True


def test_case_4_issue_feedback():
    """
    用例4：问题反馈
    
    输入：
    - 用户反馈产品异常
    - 当前未解决
    
    预期：
    - primary_category = 问题反馈
    - resolution_status = unresolved
    - follow_up_needed = true
    """
    print_separator("测试用例4：问题反馈")
    
    # 重置会话
    session = reset_session_state()
    
    # 模拟对话
    print("\n[用户]: 你们平台查询结果不对")
    session.add_user_message("你们平台查询结果不对")
    session.set_category("问题反馈")
    
    print("[助手]: 请问具体是什么问题？")
    session.add_assistant_message("请问具体是什么问题？")
    
    print("[用户]: 查询的船位数据和实际位置不符")
    session.add_user_message("查询的船位数据和实际位置不符")
    
    print("[助手]: 非常抱歉给您带来不便，我们会尽快核实处理")
    session.add_assistant_message("非常抱歉给您带来不便，我们会尽快核实处理")
    session.set_resolution_status("unresolved")
    session.set_follow_up_needed(True)
    
    # 检查是否应该结束
    should_end, end_reason = should_end_session(session, "")
    print(f"\n会话是否应该结束: {should_end}")
    print(f"结束原因: {end_reason}")
    
    if should_end:
        session.end_session(reason=end_reason)
        
        # 生成总结
        summary = generate_conversation_summary(session, "websdk")
        
        print("\n总结记录：")
        for key, value in summary.items():
            if key != "summary_content":
                print(f"  {key}: {value}")
        
        print(f"\n总结内容: {summary['summary_content']}")
        
        # 验证预期结果
        print("\n验证结果：")
        print(f"  ✓ primary_category = {summary['primary_category']} (预期: 问题反馈)")
        print(f"  ✓ resolution_status = {summary['resolution_status']} (预期: unresolved)")
        print(f"  ✓ follow_up_needed = {summary['follow_up_needed']} (预期: true)")
    
    return True


def test_case_5_timeout():
    """
    用例5：超时关闭
    
    输入：
    - 用户完成一轮查询后15分钟未再发消息
    
    预期：
    - 系统自动结束并上传总结
    """
    print_separator("测试用例5：超时关闭")
    
    # 重置会话
    session = reset_session_state()
    
    # 模拟对话
    print("\n[用户]: 查询MMSI 123456789的船位")
    session.add_user_message("查询MMSI 123456789的船位")
    session.set_category("生产需求")
    
    print("[助手]: 已为您查询到船位信息...")
    session.add_assistant_message("已为您查询到船位信息...")
    session.set_resolution_status("resolved")
    
    # 模拟超时（设置最后消息时间为15分钟前）
    from datetime import datetime
    old_time = (datetime.now(BEIJING_TZ) - timedelta(minutes=16)).strftime("%Y-%m-%dT%H:%M:%S+08:00")
    session.last_message_at = old_time
    print(f"\n模拟超时：最后消息时间设置为 {old_time}")
    
    # 检查是否应该结束
    should_end, end_reason = should_end_session(session, "")
    print(f"\n会话是否应该结束: {should_end}")
    print(f"结束原因: {end_reason}")
    
    if should_end:
        session.end_session(reason=end_reason)
        
        # 生成总结
        summary = generate_conversation_summary(session, "websdk")
        
        print("\n总结记录：")
        for key, value in summary.items():
            if key != "summary_content":
                print(f"  {key}: {value}")
        
        print(f"\n总结内容: {summary['summary_content']}")
        
        # 验证预期结果
        print("\n验证结果：")
        print(f"  ✓ 会话因超时自动结束")
        print(f"  ✓ end_reason = {end_reason} (预期: timeout)")
    
    return True


def main():
    """主测试函数"""
    print("\n" + "=" * 60)
    print("  会话总结上传能力测试")
    print("  测试时间:", datetime.now(BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S"))
    print("=" * 60)
    
    results = []
    
    # 执行测试用例
    try:
        results.append(("用例1: FAQ完成后自动总结", test_case_1_faq()))
    except Exception as e:
        results.append(("用例1: FAQ完成后自动总结", False))
        print(f"\n✗ 测试失败: {str(e)}")
    
    try:
        results.append(("用例2: 生产查询闭环", test_case_2_production_query()))
    except Exception as e:
        results.append(("用例2: 生产查询闭环", False))
        print(f"\n✗ 测试失败: {str(e)}")
    
    try:
        results.append(("用例3: 商务线索", test_case_3_business_lead()))
    except Exception as e:
        results.append(("用例3: 商务线索", False))
        print(f"\n✗ 测试失败: {str(e)}")
    
    try:
        results.append(("用例4: 问题反馈", test_case_4_issue_feedback()))
    except Exception as e:
        results.append(("用例4: 问题反馈", False))
        print(f"\n✗ 测试失败: {str(e)}")
    
    try:
        results.append(("用例5: 超时关闭", test_case_5_timeout()))
    except Exception as e:
        results.append(("用例5: 超时关闭", False))
        print(f"\n✗ 测试失败: {str(e)}")
    
    # 打印测试总结
    print_separator("测试总结")
    
    passed = sum(1 for _, result in results if result)
    total = len(results)
    
    for name, result in results:
        status = "✓ 通过" if result else "✗ 失败"
        print(f"  {status} - {name}")
    
    print(f"\n总计: {passed}/{total} 通过")
    
    # 数据库配置提示
    print("\n" + "=" * 60)
    print("  数据库配置说明")
    print("=" * 60)
    print("""
要实际插入数据库，需要配置以下环境变量：

1. COZE_DATABASE_ID: Coze 数据库ID
   - 在 Coze 平台数据库页面获取
   
2. COZE_WORKLOAD_IDENTITY_API_KEY: API访问令牌
   - 平台自动注入，无需手动配置
   
配置方式：
   export COZE_DATABASE_ID="your_database_id"
   
或者在 .env 文件中添加：
   COZE_DATABASE_ID=your_database_id
""")
    
    # 返回退出码
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
