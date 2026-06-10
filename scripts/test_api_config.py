"""
API配置测试脚本
用于验证环境变量和API连接是否正常
"""
import os
import sys
import json
import requests
from typing import Optional, Dict, Any

def print_header(title: str):
    """打印标题"""
    print("\n" + "=" * 60)
    print(f"  {title}")
    print("=" * 60)

def print_success(message: str):
    """打印成功信息"""
    print(f"✅ {message}")

def print_error(message: str):
    """打印错误信息"""
    print(f"❌ {message}")

def print_warning(message: str):
    """打印警告信息"""
    print(f"⚠️  {message}")

def print_info(message: str):
    """打印信息"""
    print(f"ℹ️  {message}")

def check_environment_variables() -> bool:
    """检查环境变量"""
    print_header("环境变量检查")
    
    required_vars = {
        "COZE_WORKLOAD_IDENTITY_API_KEY": "大模型API密钥",
        "COZE_INTEGRATION_MODEL_BASE_URL": "大模型API地址",
        "SHIP_SERVICE_API_URL": "船舶服务API地址",
        "SHIP_SERVICE_API_TOKEN": "船舶服务API Token"
    }
    
    all_set = True
    
    for var_name, description in required_vars.items():
        value = os.getenv(var_name)
        if value:
            # 对敏感信息进行脱敏
            if "TOKEN" in var_name or "KEY" in var_name:
                display_value = value[:20] + "..." if len(value) > 20 else value
            else:
                display_value = value
            print_success(f"{description}: {display_value}")
        else:
            print_error(f"{description}: 未设置")
            all_set = False
    
    return all_set

def test_ship_service_api() -> bool:
    """测试船舶服务API"""
    print_header("船舶服务API测试")
    
    api_url = os.getenv("SHIP_SERVICE_API_URL")
    api_token = os.getenv("SHIP_SERVICE_API_TOKEN")
    
    if not api_url or not api_token:
        print_error("API配置缺失，跳过测试")
        return False
    
    # 构建请求
    headers = {
        "Authorization": f"Bearer {api_token}",
        "Content-Type": "application/json"
    }
    
    # 测试用例
    test_cases = [
        {
            "name": "船位查询测试",
            "user_input": "查询MMSI 123456789的船位"
        },
        {
            "name": "档案查询测试",
            "user_input": "查询船舶档案"
        }
    ]
    
    success_count = 0
    
    for test_case in test_cases:
        print_info(f"执行: {test_case['name']}")
        print_info(f"输入: {test_case['user_input']}")
        
        payload = {
            "user_input": test_case['user_input']
        }
        
        try:
            response = requests.post(
                api_url,
                headers=headers,
                json=payload,
                timeout=30
            )
            
            print_info(f"状态码: {response.status_code}")
            
            if response.status_code == 200:
                result = response.json()
                print_info(f"响应: {json.dumps(result, ensure_ascii=False, indent=2)}")
                
                if result.get("isSuccess"):
                    print_success(f"{test_case['name']}成功")
                    success_count += 1
                else:
                    print_warning(f"{test_case['name']}返回失败")
            else:
                print_error(f"{test_case['name']}失败: HTTP {response.status_code}")
                print_error(f"响应: {response.text[:200]}")
                
        except requests.Timeout:
            print_error(f"{test_case['name']}超时")
        except requests.RequestException as e:
            print_error(f"{test_case['name']}网络错误: {e}")
        except json.JSONDecodeError as e:
            print_error(f"{test_case['name']}JSON解析错误: {e}")
        
        print()
    
    return success_count == len(test_cases)

def test_knowledge_search_api() -> bool:
    """测试知识库API"""
    print_header("知识库API测试")
    
    # 检查是否有SDK
    try:
        from coze_coding_dev_sdk import CozeCodingSDK
        print_success("CozeCodingSDK已安装")
        
        # 测试SDK调用
        sdk = CozeCodingSDK()
        print_info("测试知识库检索...")
        
        result = sdk.knowledge.search(
            query="Hifleet平台有哪些功能",
            top_k=1
        )
        
        if result:
            print_success("知识库检索成功")
            return True
        else:
            print_warning("知识库检索返回空结果")
            return False
            
    except ImportError:
        print_warning("CozeCodingSDK未安装，跳过知识库测试")
        print_info("SDK安装: uv add coze-coding-dev-sdk")
        return False
    except Exception as e:
        print_error(f"知识库测试失败: {e}")
        return False

def run_all_tests():
    """运行所有测试"""
    print_header("Hifleet智能客服API配置测试")
    print()
    
    # 1. 检查环境变量
    env_ok = check_environment_variables()
    
    if not env_ok:
        print_error("\n环境变量配置不完整，请先配置环境变量")
        print_info("参考文档: docs/ENVIRONMENT_CONFIG.md")
        return False
    
    print()
    
    # 2. 测试船舶服务API
    ship_api_ok = test_ship_service_api()
    
    # 3. 测试知识库API
    knowledge_ok = test_knowledge_search_api()
    
    # 总结
    print_header("测试总结")
    
    results = {
        "环境变量配置": env_ok,
        "船舶服务API": ship_api_ok,
        "知识库API": knowledge_ok
    }
    
    for name, status in results.items():
        if status:
            print_success(f"{name}: 正常")
        else:
            print_error(f"{name}: 异常")
    
    all_ok = all(results.values())
    
    if all_ok:
        print("\n🎉 所有测试通过！可以开始使用。")
    else:
        print("\n⚠️  部分测试失败，请检查配置后重试。")
    
    return all_ok

if __name__ == "__main__":
    # 尝试从.env文件加载环境变量
    try:
        from dotenv import load_dotenv
        load_dotenv()
        print("✅ 已加载.env文件")
    except ImportError:
        print("ℹ️  未安装python-dotenv，使用系统环境变量")
    
    # 运行测试
    success = run_all_tests()
    sys.exit(0 if success else 1)
