"""
URL检测和提取工具
用于识别消息中的链接
"""

import re
from typing import Optional, List
from urllib.parse import urlparse


# URL提取正则表达式
URL_PATTERN = re.compile(
    r'http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\\(\\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+'
)


def extract_url_from_text(text: str) -> Optional[str]:
    """
    从文本中提取第一个URL
    
    Args:
        text: 文本内容
    
    Returns:
        找到的第一个URL，如果没有则返回None
    """
    if not text:
        return None
    
    urls = URL_PATTERN.findall(text)
    return urls[0] if urls else None


def extract_all_urls_from_text(text: str) -> List[str]:
    """
    从文本中提取所有URL
    
    Args:
        text: 文本内容
    
    Returns:
        找到的所有URL列表
    """
    if not text:
        return []
    
    return URL_PATTERN.findall(text)


def is_valid_url(url: str) -> bool:
    """
    检查是否为有效的URL
    
    Args:
        url: URL字符串
    
    Returns:
        True: 有效URL
        False: 无效URL
    """
    if not url:
        return False
    
    try:
        result = urlparse(url)
        return all([result.scheme, result.netloc])
    except Exception:
        return False


def get_domain(url: str) -> Optional[str]:
    """
    从URL中提取域名
    
    Args:
        url: URL字符串
    
    Returns:
        域名（不含www.），失败返回None
    """
    try:
        parsed = urlparse(url)
        domain = parsed.netloc.lower()
        # 移除www.前缀
        domain = domain.replace('www.', '')
        return domain
    except Exception:
        return None

