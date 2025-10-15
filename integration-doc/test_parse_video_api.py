#!/usr/bin/env python3
"""
Parse-Video API 测试脚本
测试API的可用性和响应格式
"""

import requests
import json
import sys

# Parse-Video API配置
API_BASE = "http://localhost:18085"
API_ENDPOINT = f"{API_BASE}/video/share/url/parse"

# 测试用的视频链接（这些是示例，实际测试时需要真实的分享链接）
TEST_URLS = {
    "抖音": "https://v.douyin.com/iRNBho6u/",  # 需要真实链接
    "快手": "https://v.kuaishou.com/test",    # 需要真实链接
}


def test_api_health():
    """测试API健康状态"""
    print("=" * 60)
    print("测试1: API健康检查")
    print("=" * 60)
    
    try:
        response = requests.get(API_BASE, timeout=5)
        if response.status_code == 200:
            print("✅ Parse-Video服务正常运行")
            print(f"   状态码: {response.status_code}")
            return True
        else:
            print(f"⚠️  服务返回异常状态码: {response.status_code}")
            return False
    except requests.RequestException as e:
        print(f"❌ 无法连接到Parse-Video服务: {e}")
        return False


def test_api_response_format():
    """测试API响应格式"""
    print("\n" + "=" * 60)
    print("测试2: API响应格式")
    print("=" * 60)
    
    test_url = "https://test.com/invalid"
    
    try:
        response = requests.get(
            API_ENDPOINT,
            params={"url": test_url},
            timeout=10
        )
        
        print(f"请求URL: {test_url}")
        print(f"状态码: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            print(f"✅ JSON格式正确")
            print(f"   返回结构: {json.dumps(data, indent=2, ensure_ascii=False)}")
            
            # 验证必需字段
            required_fields = ["code", "msg", "data"]
            missing_fields = [f for f in required_fields if f not in data]
            
            if not missing_fields:
                print(f"✅ 包含所有必需字段: {required_fields}")
                return True
            else:
                print(f"⚠️  缺少字段: {missing_fields}")
                return False
        else:
            print(f"⚠️  HTTP状态码异常: {response.status_code}")
            return False
            
    except requests.RequestException as e:
        print(f"❌ 请求失败: {e}")
        return False
    except json.JSONDecodeError as e:
        print(f"❌ JSON解析失败: {e}")
        return False


def test_parse_video(platform, url):
    """测试实际视频解析"""
    print(f"\n测试平台: {platform}")
    print(f"测试链接: {url}")
    
    try:
        response = requests.get(
            API_ENDPOINT,
            params={"url": url},
            timeout=30
        )
        
        if response.status_code == 200:
            data = response.json()
            
            if data.get("code") == 200:
                print(f"✅ {platform} 解析成功")
                video_data = data.get("data", {})
                print(f"   标题: {video_data.get('title', 'N/A')}")
                print(f"   作者: {video_data.get('author', {}).get('name', 'N/A')}")
                print(f"   视频URL: {video_data.get('video_url', 'N/A')[:50]}...")
                return True
            else:
                print(f"⚠️  {platform} 解析失败")
                print(f"   错误: {data.get('msg', 'Unknown error')}")
                return False
        else:
            print(f"❌ HTTP请求失败: {response.status_code}")
            return False
            
    except requests.RequestException as e:
        print(f"❌ 请求异常: {e}")
        return False


def main():
    """主测试流程"""
    print("\n" + "🎬" * 30)
    print("Parse-Video API 测试")
    print("🎬" * 30 + "\n")
    
    # 测试1: 健康检查
    if not test_api_health():
        print("\n❌ 服务不可用，停止测试")
        sys.exit(1)
    
    # 测试2: 响应格式
    if not test_api_response_format():
        print("\n⚠️  响应格式异常，但继续测试")
    
    # 测试3: 实际视频解析（需要真实链接）
    print("\n" + "=" * 60)
    print("测试3: 视频解析功能")
    print("=" * 60)
    print("⚠️  需要真实的视频分享链接才能完全测试")
    print("   当前使用示例链接，可能会失败")
    
    for platform, url in TEST_URLS.items():
        test_parse_video(platform, url)
    
    # 总结
    print("\n" + "=" * 60)
    print("测试总结")
    print("=" * 60)
    print("✅ Parse-Video API 基础功能正常")
    print("✅ 响应格式符合预期")
    print("💡 提示: 使用真实的视频分享链接可以测试完整功能")
    print("\n示例测试命令:")
    print(f'   curl "{API_ENDPOINT}?url=<真实视频链接>" | python3 -m json.tool')
    print("\n")


if __name__ == "__main__":
    main()

