"""
Parse-Video API 调用模块
提供与Parse-Video服务交互的功能
"""

import aiohttp
from typing import Optional, Dict, Any
from bot import LOGGER
from bot.core.config_manager import Config


async def parse_video_api(url: str, timeout: int = 30) -> Optional[Dict[str, Any]]:
    """
    调用Parse-Video API解析视频链接
    
    Args:
        url: 视频分享链接
        timeout: 超时时间(秒)
    
    Returns:
        成功返回解析结果dict，失败返回None
        {
            'video_url': 'https://...',      # 视频直链
            'title': '标题',
            'author': {
                'name': '作者',
                'uid': 'xxx',
                'avatar': 'https://...'
            },
            'cover_url': 'https://...',      # 封面图
            'music_url': 'https://...',      # 背景音乐
            'images': [],                     # 图集
            'image_live_photos': []          # 图集LivePhoto
        }
    """
    
    # 从配置获取Parse-Video服务地址
    api_base = getattr(Config, 'PARSE_VIDEO_API', 'http://localhost:18085')
    
    # 检查功能是否启用
    if not getattr(Config, 'PARSE_VIDEO_ENABLED', True):
        LOGGER.warning("Parse-Video feature is disabled")
        return None
    
    api_url = f"{api_base}/video/share/url/parse"
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                api_url,
                params={'url': url},
                timeout=aiohttp.ClientTimeout(total=timeout)
            ) as response:
                
                if response.status != 200:
                    LOGGER.error(f"Parse-Video API returned status {response.status}")
                    return None
                
                result = await response.json()
                
                # 检查返回码
                code = result.get('code')
                if code != 200:
                    msg = result.get('msg', 'Unknown error')
                    LOGGER.warning(f"Parse-Video API error (code={code}): {msg}")
                    return None
                
                # 返回数据部分
                data = result.get('data')
                if not data:
                    LOGGER.error("Parse-Video API returned empty data")
                    return None
                
                # 验证必须包含video_url或images
                if not data.get('video_url') and not data.get('images'):
                    LOGGER.error("Parse-Video API: no video_url or images in response")
                    return None
                
                LOGGER.info(f"Parse-Video API success: {data.get('title', 'Unknown')}")
                return data
    
    except aiohttp.ClientError as e:
        LOGGER.error(f"Parse-Video API request failed: {e}")
        return None
    
    except Exception as e:
        LOGGER.error(f"Parse-Video API unexpected error: {e}")
        return None


async def check_parse_video_health() -> bool:
    """
    检查Parse-Video服务健康状态
    
    Returns:
        True: 服务正常
        False: 服务异常
    """
    api_base = getattr(Config, 'PARSE_VIDEO_API', 'http://localhost:18085')
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{api_base}/",
                timeout=aiohttp.ClientTimeout(total=5)
            ) as response:
                is_healthy = response.status == 200
                if is_healthy:
                    LOGGER.info("Parse-Video service is healthy")
                else:
                    LOGGER.warning(f"Parse-Video service returned status {response.status}")
                return is_healthy
    
    except Exception as e:
        LOGGER.error(f"Parse-Video health check failed: {e}")
        return False


def format_video_info(data: Dict[str, Any]) -> str:
    """
    格式化视频信息用于显示
    
    Args:
        data: parse_video_api返回的数据
    
    Returns:
        格式化的文本信息
    """
    lines = []
    
    # 标题
    title = data.get('title', '').strip()
    if title:
        lines.append(f"📹 <b>标题:</b> {title}")
    
    # 作者
    author = data.get('author', {})
    if isinstance(author, dict):
        author_name = author.get('name', '').strip()
        if author_name:
            lines.append(f"👤 <b>作者:</b> {author_name}")
    
    # 视频URL
    video_url = data.get('video_url', '').strip()
    if video_url:
        lines.append(f"🔗 <b>获取到视频直链</b>")
    
    # 图集
    images = data.get('images', [])
    if images:
        lines.append(f"📸 <b>图集:</b> {len(images)} 张图片")
    
    return '\n'.join(lines) if lines else "未获取到详细信息"

