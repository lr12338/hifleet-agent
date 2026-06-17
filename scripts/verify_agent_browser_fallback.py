#!/usr/bin/env python
"""
验证 agent_browser_deep_search 兜底逻辑的集成测试脚本

运行方式：
    cd /Users/raymondlu/LocalProject/AIPM/智能客服/客服开发/本地agent/hifleet-agent
    PYTHONPATH=src python scripts/verify_agent_browser_fallback.py
"""

import sys
from pathlib import Path

# 添加 src 到路径
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agents.customer_support_router import (
    extract_entities,
    classify_message,
    execute_knowledge_chain,
    execute_planned_knowledge_chain,
    make_trace,
)


class MockTool:
    """模拟工具，用于测试"""
    def __init__(self, name, responses):
        self.name = name
        self.responses = responses  # dict: depth or key -> response
        self.calls = []

    def invoke(self, args):
        self.calls.append(args)
        # 根据 depth 或其他参数返回不同的响应
        depth = args.get("depth", "default")
        if depth in self.responses:
            return self.responses[depth]
        return self.responses.get("default", "未检索到足够可信的信息")


def test_knowledge_chain_fallback():
    """测试 execute_knowledge_chain 的 agent_browser 兜底逻辑"""
    print("=" * 80)
    print("测试 1: execute_knowledge_chain 兜底逻辑")
    print("=" * 80)

    # 创建 mock 工具
    smart_search = MockTool("smart_search", {
        "quick": "未检索到足够可信的信息",
        "normal": "未检索到足够可信的信息",
        "deep": "未检索到足够可信的信息",
    })
    
    agent_browser = MockTool("agent_browser_deep_search", {
        "default": "【互联网搜索结果】\n根据公开资料，HiFleet 平台支持船舶轨迹导出功能..."
    })

    # 构建 tool_map
    tool_map = {
        "smart_search": smart_search,
        "agent_browser_deep_search": agent_browser,
    }

    # 测试问题
    text = "HiFleet 船舶轨迹怎么导出"
    entities = extract_entities(text)
    decision = classify_message(text, entities)
    trace = make_trace(decision, entities, session_id="test_001")

    print(f"\n问题: {text}")
    print(f"路由: {decision.route}")
    print(f"搜索深度: {decision.search_depth}")

    # 执行知识链
    output = execute_knowledge_chain(text, decision, tool_map, trace)

    print(f"\n工具调用序列:")
    print(f"  smart_search 调用次数: {len(smart_search.calls)}")
    for i, call in enumerate(smart_search.calls):
        print(f"    [{i+1}] depth={call.get('depth')}")
    
    print(f"  agent_browser_deep_search 调用次数: {len(agent_browser.calls)}")
    
    print(f"\n兜底原因: {trace.fallback_reason}")
    print(f"输出包含浏览器内容: {'互联网搜索' in output or '公开资料' in output}")
    print(f"\n输出预览 (前200字符):")
    print(f"  {output[:200]}")

    # 验证
    assert len(agent_browser.calls) > 0, "agent_browser_deep_search 应该被调用"
    assert trace.fallback_reason == "smart_search_empty_agent_browser_fallback"
    assert "互联网搜索" in output or "公开资料" in output
    
    print("\n✅ 测试通过!")
    return True


def test_planned_knowledge_chain_fallback():
    """测试 execute_planned_knowledge_chain 的 agent_browser 兜底逻辑"""
    print("\n" + "=" * 80)
    print("测试 2: execute_planned_knowledge_chain 兜底逻辑")
    print("=" * 80)

    smart_search = MockTool("smart_search", {
        "quick": "未检索到足够可信的信息",
        "normal": "未检索到足够可信的信息",
        "deep": "未检索到足够可信的信息",
    })
    
    agent_browser = MockTool("agent_browser_deep_search", {
        "default": "【互联网搜索结果】\n根据公开技术资料，HiFleet API 调用频率限制为每分钟100次..."
    })

    tool_map = {
        "smart_search": smart_search,
        "agent_browser_deep_search": agent_browser,
    }

    text = "HiFleet API 调用频率限制是多少"
    entities = extract_entities(text)
    decision = classify_message(text, entities)
    trace = make_trace(decision, entities, session_id="test_002")

    search_plan = [
        {"query": "API 频率限制", "depth": "quick", "hypothesis_id": "H1", "purpose": "测试"}
    ]

    print(f"\n问题: {text}")
    print(f"搜索计划: {search_plan}")

    output, evidence_items, evidence_summary = execute_planned_knowledge_chain(
        text, decision, search_plan, tool_map, trace
    )

    print(f"\n工具调用序列:")
    print(f"  smart_search 调用次数: {len(smart_search.calls)}")
    print(f"  agent_browser_deep_search 调用次数: {len(agent_browser.calls)}")
    
    print(f"\n兜底原因: {trace.fallback_reason}")
    print(f"证据项数量: {len(evidence_items)}")
    
    # 查找 browser 证据项
    browser_evidence = [e for e in evidence_items if e.get("source_name") == "agent_browser_deep_search"]
    print(f"browser 证据项: {len(browser_evidence)}")
    if browser_evidence:
        print(f"  source_type: {browser_evidence[0].get('source_type')}")
        print(f"  authority: {browser_evidence[0].get('authority')}")

    print(f"\n输出预览 (前200字符):")
    print(f"  {output[:200]}")

    # 验证
    assert len(agent_browser.calls) > 0, "agent_browser_deep_search 应该被调用"
    assert trace.fallback_reason == "smart_search_empty_agent_browser_fallback"
    assert len(browser_evidence) == 1
    assert browser_evidence[0]["source_type"] == "public_web"
    assert browser_evidence[0]["authority"] == 0.6
    
    print("\n✅ 测试通过!")
    return True


def test_no_fallback_when_kb_hit():
    """测试当 KB 命中时不触发 browser 兜底"""
    print("\n" + "=" * 80)
    print("测试 3: KB 命中时不触发 browser 兜底")
    print("=" * 80)

    smart_search = MockTool("smart_search", {
        "quick": "【优先匹配 - FAQ/标准回复】\n导出轨迹：在船舶详情页点击'导出轨迹'按钮...",
        "normal": "【优先匹配 - FAQ/标准回复】\n导出轨迹：在船舶详情页点击'导出轨迹'按钮...",
        "deep": "【优先匹配 - FAQ/标准回复】\n导出轨迹：在船舶详情页点击'导出轨迹'按钮...",
        "default": "【优先匹配 - FAQ/标准回复】\n导出轨迹：在船舶详情页点击'导出轨迹'按钮...",
    })
    
    agent_browser = MockTool("agent_browser_deep_search", {
        "default": "【互联网搜索结果】\n这个不应该被调用"
    })

    tool_map = {
        "smart_search": smart_search,
        "agent_browser_deep_search": agent_browser,
    }

    text = "HiFleet 船舶轨迹怎么导出"
    entities = extract_entities(text)
    decision = classify_message(text, entities)
    trace = make_trace(decision, entities, session_id="test_003")

    print(f"\n问题: {text}")

    output = execute_knowledge_chain(text, decision, tool_map, trace)

    print(f"\n工具调用序列:")
    print(f"  smart_search 调用次数: {len(smart_search.calls)}")
    print(f"  agent_browser_deep_search 调用次数: {len(agent_browser.calls)}")
    
    print(f"\n兜底原因: {trace.fallback_reason}")
    print(f"输出包含 FAQ 内容: {'导出轨迹' in output}")

    # 验证 browser 未被调用
    assert len(agent_browser.calls) == 0, "agent_browser_deep_search 不应该被调用"
    assert "导出轨迹" in output
    
    print("\n✅ 测试通过!")
    return True


def main():
    print("\n" + "🔍" * 40)
    print("Agent Browser 兜底逻辑验证测试")
    print("🔍" * 40 + "\n")

    all_passed = True
    
    try:
        all_passed &= test_knowledge_chain_fallback()
    except Exception as e:
        print(f"\n❌ 测试 1 失败: {e}")
        import traceback
        traceback.print_exc()
        all_passed = False

    try:
        all_passed &= test_planned_knowledge_chain_fallback()
    except Exception as e:
        print(f"\n❌ 测试 2 失败: {e}")
        import traceback
        traceback.print_exc()
        all_passed = False

    try:
        all_passed &= test_no_fallback_when_kb_hit()
    except Exception as e:
        print(f"\n❌ 测试 3 失败: {e}")
        import traceback
        traceback.print_exc()
        all_passed = False

    print("\n" + "=" * 80)
    if all_passed:
        print("✅ 所有测试通过!")
    else:
        print("❌ 部分测试失败")
    print("=" * 80)

    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
