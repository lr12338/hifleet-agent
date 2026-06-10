#!/usr/bin/env python3
"""
船舶服务API直接测试脚本
绕过Agent，直接测试API调用
"""
import os
import sys
import json
import requests

def test_ship_service_api():
    """直接测试船舶服务API"""
    print("=" * 60)
    print("  船舶服务API直接测试")
    print("=" * 60)
    print()
    
    # API配置
    api_url = "https://y3rz9srmmb.coze.site/run"
    api_token = "eyJhbGciOiJSUzI1NiIsImtpZCI6ImNiYjIxYjQxLWZkMTktNDc0ZS1hNjU5LTc2NGQxZGI4YjA0OSJ9.eyJpc3MiOiJodHRwczovL2FwaS5jb3plLmNuIiwiYXVkIjpbIlAyeWtCdFB2MmJ4c1pmT3VkQms2VjdzZG1XdmlhMXk5Il0sImV4cCI6ODIxMDI2Njg3Njc5OSwiaWF0IjoxNzcwMDg4OTY3LCJzdWIiOiJzcGlmZmU6Ly9hcGkuY296ZS5jbi93b3JrbG9hZF9pZGVudGl0eS9pZDo3NjAyNDcwMjQ1MTU1OTk1NjkxIiwic3JjIjoiaW5ib3VuZF9hdXRoX2FjY2Vzc190b2tlbl9pZDo3NjAyNDc0MjI3OTAzNDk2MjQ0In0.eEic1jIwn8Fia3RBVrrotCDTR0xuG3n66gLVU4M7eepDDzBx5mjyWAlGkSRdzQeWKt5FS91-k7HznNuxSfr_S8-srwUV5HEcgqgSBitT9jc3gKDPogqd0FrR-Gf09tqOOMlVJVj1x6jEvcN3541iOMPFPNHrdaDxPCvwsIwfvJY0NVgbasmuGphY8AVOgyW8l6fRN83MAE6RB3w-PnoTOUj-fXYs95toplne80AyEtUwSqSnqXlA1i3yZd-qu8acFDqRSisCcuthWw3XQuupyUhQQ8NHjsQBzFt-OUbycvraOAsN1wMa_1sDu6LuxpCUOhxMmaVO2PezrckJZ6P1Ww"
    
    # 请求头
    headers = {
        "Authorization": f"Bearer {api_token}",
        "Content-Type": "application/json"
    }
    
    # 测试用例
    test_cases = [
        {
            "name": "船位查询",
            "user_input": "查询MMSI 123456789的船位"
        },
        {
            "name": "档案查询",
            "user_input": "查询船舶档案信息"
        }
    ]
    
    for test_case in test_cases:
        print(f"测试: {test_case['name']}")
        print(f"输入: {test_case['user_input']}")
        
        # 构建请求体
        payload = {
            "user_input": test_case['user_input']
        }
        
        try:
            # 发送请求
            print(f"调用API: {api_url}")
            response = requests.post(
                api_url,
                headers=headers,
                json=payload,
                timeout=30
            )
            
            print(f"状态码: {response.status_code}")
            
            if response.status_code == 200:
                result = response.json()
                print(f"响应:")
                print(json.dumps(result, ensure_ascii=False, indent=2))
                
                if result.get("isSuccess"):
                    print("✅ 测试成功")
                else:
                    print("⚠️  API返回失败")
            else:
                print(f"❌ HTTP错误: {response.status_code}")
                print(f"响应: {response.text[:200]}")
                
        except requests.Timeout:
            print("❌ 请求超时")
        except requests.RequestException as e:
            print(f"❌ 网络错误: {e}")
        except json.JSONDecodeError as e:
            print(f"❌ JSON解析错误: {e}")
        
        print()
        print("-" * 60)
        print()
    
    print("测试完成！")

if __name__ == "__main__":
    test_ship_service_api()
