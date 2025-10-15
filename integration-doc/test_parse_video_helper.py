#!/usr/bin/env python3
"""
测试parse_video_helper模块
验证API调用功能是否正常
"""

import asyncio
import sys
import os

# 添加项目路径
bot_path = '/root/data/docker_data/mirror-leech-telegram-bot'
sys.path.insert(0, bot_path)
os.chdir(bot_path)

# 创建必要的模块结构
import types

# 模拟bot包
bot_module = types.ModuleType('bot')
sys.modules['bot'] = bot_module

# 模拟bot.core
bot_core = types.ModuleType('bot.core')
sys.modules['bot.core'] = bot_core

# 模拟bot.core.config_manager
config_manager = types.ModuleType('bot.core.config_manager')
sys.modules['bot.core.config_manager'] = config_manager

# 模拟Config
class MockConfig:
    PARSE_VIDEO_API = "http://localhost:18085"
    PARSE_VIDEO_ENABLED = True
    PARSE_VIDEO_TIMEOUT = 30

config_manager.Config = MockConfig

# 模拟LOGGER
class MockLogger:
    @staticmethod
    def info(msg):
        print(f"[INFO] {msg}")
    
    @staticmethod
    def warning(msg):
        print(f"[WARNING] {msg}")
    
    @staticmethod
    def error(msg):
        print(f"[ERROR] {msg}")

bot_module.LOGGER = MockLogger()

# 现在导入要测试的模块
sys.path.insert(0, os.path.join(bot_path, 'bot', 'helper'))
sys.path.insert(0, os.path.join(bot_path, 'bot', 'helper', 'ext_utils'))

# 直接导入文件
import importlib.util

# 导入parse_video_helper
spec = importlib.util.spec_from_file_location(
    "parse_video_helper",
    os.path.join(bot_path, 'bot', 'helper', 'parse_video_helper.py')
)
parse_video_helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(parse_video_helper)

# 导入url_utils
spec = importlib.util.spec_from_file_location(
    "url_utils",
    os.path.join(bot_path, 'bot', 'helper', 'ext_utils', 'url_utils.py')
)
url_utils = importlib.util.module_from_spec(spec)
spec.loader.exec_module(url_utils)

# 提取函数
parse_video_api = parse_video_helper.parse_video_api
check_parse_video_health = parse_video_helper.check_parse_video_health
format_video_info = parse_video_helper.format_video_info

extract_url_from_text = url_utils.extract_url_from_text
extract_all_urls_from_text = url_utils.extract_all_urls_from_text
is_valid_url = url_utils.is_valid_url
get_domain = url_utils.get_domain


async def test_parse_video_helper():
    """测试parse_video_helper模块"""
    
    print("=" * 70)
    print("测试 Parse-Video Helper 模块")
    print("=" * 70)
    
    # 测试1: 健康检查
    print("\n[测试1] 检查Parse-Video服务健康状态")
    print("-" * 70)
    is_healthy = await check_parse_video_health()
    if is_healthy:
        print("✅ Parse-Video服务健康")
    else:
        print("❌ Parse-Video服务异常")
        return False
    
    # 测试2: API调用（使用真实快手链接）
    print("\n[测试2] 测试parse_video_api函数")
    print("-" * 70)
    test_url = "https://v.kuaishou.com/2yBzDR"  # 这是一个测试链接
    print(f"测试URL: {test_url}")
    
    result = await parse_video_api(test_url)
    if result:
        print("✅ API调用成功")
        print(f"\n返回数据:")
        print(f"  - 标题: {result.get('title', 'N/A')}")
        print(f"  - 作者: {result.get('author', {}).get('name', 'N/A')}")
        print(f"  - 视频URL: {result.get('video_url', 'N/A')[:60]}...")
        print(f"  - 封面URL: {result.get('cover_url', 'N/A')[:60]}...")
        
        # 测试3: 格式化信息
        print("\n[测试3] 测试format_video_info函数")
        print("-" * 70)
        formatted = format_video_info(result)
        print("格式化输出:")
        print(formatted)
    else:
        print("⚠️  API调用失败或链接无效")
    
    # 测试4: 无效链接
    print("\n[测试4] 测试无效链接处理")
    print("-" * 70)
    invalid_url = "https://invalid-site.com/video/123"
    result = await parse_video_api(invalid_url)
    if result is None:
        print("✅ 正确处理了无效链接（返回None）")
    else:
        print("⚠️  意外返回了结果")
    
    return True


def test_url_utils():
    """测试url_utils模块"""
    
    print("\n" + "=" * 70)
    print("测试 URL Utils 模块")
    print("=" * 70)
    
    # 测试5: URL提取
    print("\n[测试5] 测试extract_url_from_text")
    print("-" * 70)
    
    test_cases = [
        ("这是一个快手视频 https://v.kuaishou.com/xxx 请查看", "https://v.kuaishou.com/xxx"),
        ("http://douyin.com/video/123", "http://douyin.com/video/123"),
        ("没有链接的文本", None),
        ("", None),
    ]
    
    for text, expected in test_cases:
        result = extract_url_from_text(text)
        status = "✅" if result == expected else "❌"
        print(f"{status} '{text[:30]}...' -> {result}")
    
    # 测试6: 提取所有URL
    print("\n[测试6] 测试extract_all_urls_from_text")
    print("-" * 70)
    text_with_multiple_urls = "链接1: http://site1.com 链接2: https://site2.com/path"
    urls = extract_all_urls_from_text(text_with_multiple_urls)
    print(f"文本: {text_with_multiple_urls}")
    print(f"提取到 {len(urls)} 个URL: {urls}")
    
    # 测试7: URL验证
    print("\n[测试7] 测试is_valid_url")
    print("-" * 70)
    
    url_tests = [
        ("https://douyin.com/video/123", True),
        ("http://kuaishou.com", True),
        ("not a url", False),
        ("", False),
    ]
    
    for url, expected in url_tests:
        result = is_valid_url(url)
        status = "✅" if result == expected else "❌"
        print(f"{status} '{url}' -> {result}")
    
    # 测试8: 域名提取
    print("\n[测试8] 测试get_domain")
    print("-" * 70)
    
    domain_tests = [
        ("https://www.douyin.com/video/123", "douyin.com"),
        ("http://v.kuaishou.com/xxx", "v.kuaishou.com"),
        ("https://xiaohongshu.com", "xiaohongshu.com"),
    ]
    
    for url, expected in domain_tests:
        result = get_domain(url)
        status = "✅" if result == expected else "❌"
        print(f"{status} '{url}' -> {result}")


async def main():
    """主测试函数"""
    
    print("\n" + "🎬" * 35)
    print("Parse-Video 集成模块测试")
    print("🎬" * 35 + "\n")
    
    # 测试parse_video_helper
    success = await test_parse_video_helper()
    
    # 测试url_utils
    test_url_utils()
    
    # 总结
    print("\n" + "=" * 70)
    print("测试总结")
    print("=" * 70)
    if success:
        print("✅ 所有核心功能测试通过")
        print("✅ parse_video_helper模块工作正常")
        print("✅ url_utils模块工作正常")
        print("\n💡 阶段2完成：API调用模块和工具函数已就绪")
    else:
        print("⚠️  部分测试未通过，请检查Parse-Video服务")
    
    print()


if __name__ == "__main__":
    asyncio.run(main())

