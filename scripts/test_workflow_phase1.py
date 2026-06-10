"""
阶段一测试脚本
测试工作流基础框架、问题预处理和意图分类节点
"""
import sys
import os
import logging

# 添加项目根目录到Python路径
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(project_root, "src"))

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def test_workflow():
    """测试工作流"""
    from workflows.graph import run_workflow, create_initial_state, build_workflow
    from workflows.state import IntentType
    
    print("=" * 80)
    print("Hifleet智能客服工作流 - 阶段一测试")
    print("=" * 80)
    
    # 测试用例
    test_cases = [
        {
            "name": "平台使用问题 - 注册",
            "input": "我怎么注册账号？",
            "expected_intent": IntentType.PLATFORM_USAGE
        },
        {
            "name": "平台使用问题 - 功能",
            "input": "历史轨迹看不了了怎么办？",
            "expected_intent": IntentType.PLATFORM_USAGE
        },
        {
            "name": "船位操作 - 查询",
            "input": "查询MMSI 123456789的船位",
            "expected_intent": IntentType.SHIP_OPERATION
        },
        {
            "name": "商务咨询 - 价格",
            "input": "你们的产品多少钱？",
            "expected_intent": IntentType.BUSINESS_INQUIRY
        },
        {
            "name": "线索收集 - 试用",
            "input": "我想试用，我叫张三，电话13800138000",
            "expected_intent": IntentType.LEAD_COLLECTION
        },
        {
            "name": "通用问答 - AIS",
            "input": "什么是AIS？",
            "expected_intent": IntentType.GENERAL_QUESTION
        }
    ]
    
    # 构建工作流
    print("\n📦 构建工作流...")
    try:
        workflow = build_workflow()
        print("✅ 工作流构建成功！")
    except Exception as e:
        print(f"❌ 工作流构建失败: {e}")
        return
    
    # 执行测试用例
    passed = 0
    failed = 0
    
    for i, test_case in enumerate(test_cases, 1):
        print(f"\n{'='*80}")
        print(f"测试用例 {i}: {test_case['name']}")
        print(f"{'='*80}")
        print(f"输入: {test_case['input']}")
        
        try:
            # 运行工作流
            result = run_workflow(test_case['input'])
            
            # 验证结果
            print(f"\n📋 执行结果:")
            print(f"  - 重写查询: {result.get('rewritten_query', 'N/A')}")
            print(f"  - 实体提取: {result.get('entities', [])}")
            print(f"  - 关键词: {result.get('keywords', [])}")
            print(f"  - 意图分类: {result.get('intent', 'N/A')}")
            print(f"  - 置信度: {result.get('intent_confidence', 0.0):.2f}")
            print(f"  - 节点路径: {' -> '.join(result.get('node_history', []))}")
            print(f"  - 回复: {result.get('response', 'N/A')[:100]}...")
            
            # 检查意图是否正确
            actual_intent = result.get('intent')
            expected_intent = test_case['expected_intent']
            
            if actual_intent == expected_intent:
                print(f"\n✅ 测试通过！意图分类正确。")
                passed += 1
            else:
                print(f"\n❌ 测试失败！")
                print(f"  期望意图: {expected_intent}")
                print(f"  实际意图: {actual_intent}")
                failed += 1
            
            # 检查是否有错误
            if result.get('error'):
                print(f"\n⚠️ 执行过程中有错误: {result.get('error')}")
                
        except Exception as e:
            print(f"\n❌ 测试执行失败: {e}")
            import traceback
            traceback.print_exc()
            failed += 1
    
    # 输出总结
    print(f"\n{'='*80}")
    print("测试总结")
    print(f"{'='*80}")
    print(f"总计: {len(test_cases)} 个测试用例")
    print(f"✅ 通过: {passed}")
    print(f"❌ 失败: {failed}")
    print(f"通过率: {passed / len(test_cases) * 100:.1f}%")
    print(f"{'='*80}\n")
    
    return passed == len(test_cases)


def test_preprocess_node():
    """单独测试问题预处理节点"""
    from workflows.nodes.preprocess import preprocess_node
    from workflows.state import WorkflowState
    
    print("\n" + "=" * 80)
    print("测试问题预处理节点")
    print("=" * 80)
    
    test_input = "我怎么看不了历史轨迹了？MMSI 123456789"
    
    # 创建初始状态
    state = {
        "user_input": test_input,
        "rewritten_query": "",
        "entities": [],
        "keywords": [],
        "intent": "unknown",
        "intent_confidence": 0.0,
        "search_result": "",
        "search_score": 0.0,
        "response": "",
        "messages": [],
        "session_id": "test_session",
        "timestamp": "",
        "node_history": [],
        "error": None,
        "error_node": None
    }
    
    print(f"输入: {test_input}")
    
    # 执行预处理
    result = preprocess_node(state)
    
    print(f"\n结果:")
    print(f"  - 重写查询: {result.get('rewritten_query', 'N/A')}")
    print(f"  - 实体: {result.get('entities', [])}")
    print(f"  - 关键词: {result.get('keywords', [])}")
    
    # 验证实体提取
    entities = result.get('entities', [])
    has_mmsi = any(e['type'] == 'MMSI' for e in entities)
    
    if has_mmsi:
        print("\n✅ MMSI实体提取成功！")
    else:
        print("\n❌ MMSI实体提取失败！")


def test_classify_node():
    """单独测试意图分类节点"""
    from workflows.nodes.classify import classify_intent_node, rule_based_classify
    from workflows.state import WorkflowState
    
    print("\n" + "=" * 80)
    print("测试意图分类节点")
    print("=" * 80)
    
    test_cases = [
        ("我怎么注册账号？", []),
        ("查询MMSI 123456789的船位", [{"type": "MMSI", "value": "123456789"}]),
        ("你们的产品多少钱？", []),
    ]
    
    for user_input, entities in test_cases:
        print(f"\n输入: {user_input}")
        
        # 创建状态
        state = {
            "user_input": user_input,
            "entities": entities,
            "rewritten_query": user_input,
            "keywords": [],
            "intent": "unknown",
            "intent_confidence": 0.0,
            "search_result": "",
            "search_score": 0.0,
            "response": "",
            "messages": [],
            "session_id": "test_session",
            "timestamp": "",
            "node_history": [],
            "error": None,
            "error_node": None
        }
        
        # 执行分类
        result = classify_intent_node(state)
        
        print(f"  - 意图: {result.get('intent')}")
        print(f"  - 置信度: {result.get('intent_confidence', 0.0):.2f}")


if __name__ == "__main__":
    print("\n" + "=" * 80)
    print("Hifleet智能客服工作流 - 阶段一测试套件")
    print("=" * 80)
    
    # 测试单个节点
    print("\n" + "=" * 80)
    print("第一部分：单元测试")
    print("=" * 80)
    
    test_preprocess_node()
    test_classify_node()
    
    # 测试完整工作流
    print("\n" + "=" * 80)
    print("第二部分：集成测试")
    print("=" * 80)
    
    success = test_workflow()
    
    sys.exit(0 if success else 1)
