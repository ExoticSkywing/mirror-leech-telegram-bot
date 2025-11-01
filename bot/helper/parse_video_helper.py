"""
Parse-Video API 调用模块
提供与Parse-Video服务交互的功能
"""

import aiohttp
import json
import re
from typing import Optional, Dict, Any
from bot import LOGGER
from bot.core.config_manager import Config
from bot.helper.ext_utils.url_utils import get_domain
from urllib.parse import urlparse, parse_qs


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


async def parse_video_v2_api(url: str, timeout: int = None) -> Optional[Dict[str, Any]]:
    api_base = getattr(Config, 'PARSE_VIDEO_V2_API', '') or 'https://parsev2.1yo.cc'
    enabled = getattr(Config, 'PARSE_VIDEO_V2_ENABLED', True)
    if not enabled:
        return None
    to = timeout or getattr(Config, 'PARSE_VIDEO_TIMEOUT', 30)

    # 选择端点：按平台精确匹配
    domain = (get_domain(url) or '').lower()
    url_lower = (url or '').lower()
    endpoints = []
    if domain in {"bilibili.com", "b23.tv"}:
        endpoints = ["bilibili.php"]
    elif domain.endswith("weibo.com") or domain.endswith("weibo.cn") or domain in {"video.weibo.com", "h5.video.weibo.com", "m.weibo.cn"}:
        endpoints = ["weibo.php", "weibo_v.php"]  # 按顺序回退
    elif domain in {"pipix.com", "h5.pipix.com"}:
        endpoints = ["ppxia.php"]
    elif domain == "qishui.douyin.com" or (domain.endswith("douyin.com") and "/music" in url_lower):
        endpoints = ["dymusic.php"]
    elif domain in {"music.163.com", "163cn.link"}:
        endpoints = ["NETEASE_163"]
    else:
        # 无映射的平台不走 v2
        endpoints = []

    # 若无端点映射，直接返回 None 交给上层回退
    if not endpoints:
        LOGGER.info(f"ParseV2 API: no endpoint mapping for domain {domain}, skip v2")
        return None

    async def fetch_and_normalize(session, endpoint: str) -> Optional[Dict[str, Any]]:
        # 特殊处理：网易云音乐通过 bugpk 接口解析
        if endpoint == "NETEASE_163":
            async def resolve_163_id(input_url: str) -> Optional[str]:
                try:
                    p = urlparse(input_url)
                    # 1) query 里找 id
                    q = parse_qs(p.query)
                    if 'id' in q and q['id']:
                        return q['id'][0]
                    # 2) fragment 里找 id（#/song?...&id=xxx）
                    if p.fragment:
                        frag = p.fragment
                        if '?' in frag:
                            frag_q = parse_qs(frag.split('?', 1)[1])
                            if 'id' in frag_q and frag_q['id']:
                                return frag_q['id'][0]
                    # 3) 163cn.link 短链，跟随重定向获取最终地址
                    if p.netloc.endswith('163cn.link'):
                        try:
                            async with session.get(input_url, allow_redirects=True, timeout=aiohttp.ClientTimeout(total=to)) as r:
                                final_url = str(r.url)
                                p2 = urlparse(final_url)
                                q2 = parse_qs(p2.query)
                                if 'id' in q2 and q2['id']:
                                    return q2['id'][0]
                                if p2.fragment and '?' in p2.fragment:
                                    frag_q2 = parse_qs(p2.fragment.split('?', 1)[1])
                                    if 'id' in frag_q2 and frag_q2['id']:
                                        return frag_q2['id'][0]
                        except Exception:
                            return None
                except Exception:
                    return None
                return None

            music_id = await resolve_163_id(url)
            if not music_id:
                LOGGER.warning("ParseV2 API NETEASE_163: failed to extract music id")
                return None

            try:
                api_url_163 = "https://v.iarc.top/"
                params = {
                    'type': 'song',
                    'id': music_id,
                }
                async with session.get(api_url_163, params=params, timeout=aiohttp.ClientTimeout(total=to)) as resp:
                    if resp.status != 200:
                        LOGGER.warning(f"ParseV2 API NETEASE_163 status {resp.status}")
                        return None
                    txt = await resp.text()
                    s = txt.strip()
                    # 该接口返回数组JSON
                    try:
                        rj = json.loads(s)
                    except Exception:
                        LOGGER.warning(f"ParseV2 API NETEASE_163 non-JSON body: {s[:200]}")
                        return None
                    if not isinstance(rj, list) or not rj:
                        return None
                    item = rj[0] if isinstance(rj[0], dict) else None
                    if not item:
                        return None
                    audio_url = item.get('url')
                    title = item.get('name') or ''
                    author = item.get('artist') or ''
                    cover = item.get('pic') or ''
                    if not audio_url:
                        return None
                    normalized = {
                        'video_url': audio_url,
                        'title': title,
                        'author': {'name': author},
                        'cover_url': cover,
                        'images': [],
                        'platform': 'NetEaseMusic',
                    }
                    LOGGER.info(f"ParseV2 API NETEASE_163 success: {title}")
                    return normalized
            except Exception as e:
                LOGGER.warning(f"ParseV2 API NETEASE_163 request failed: {e}")
                return None

        # 常规 parsev2.1yo.cc 端点处理
        api_url = f"{api_base.rstrip('/')}/{endpoint}"
        try:
            async with session.get(
                api_url,
                params={'url': url},
                timeout=aiohttp.ClientTimeout(total=to)
            ) as response:
                if response.status != 200:
                    LOGGER.warning(f"ParseV2 API {endpoint} status {response.status}")
                    return None
                try:
                    result = await response.json(content_type=None)
                except Exception:
                    # 某些接口可能在JSON前输出PHP告警，尝试从文本中提取JSON
                    text = await response.text()
                    s = text.strip()
                    start = s.find("{")
                    end = s.rfind("}")
                    if start != -1 and end != -1 and end > start:
                        try:
                            result = json.loads(s[start:end+1])
                        except Exception:
                            LOGGER.warning(f"ParseV2 API {endpoint} non-JSON body (failed to parse extracted JSON): {s[:200]}")
                            return None
                    else:
                        LOGGER.warning(f"ParseV2 API {endpoint} non-JSON body: {s[:200]}")
                        return None
                data = result.get('data') if isinstance(result, dict) else None
                if not data:
                    data = result if isinstance(result, dict) else None
                if not isinstance(data, dict):
                    return None

                def pick_url(d: Dict[str, Any]):
                    # 直接候选
                    direct_keys = ['video_url', 'url', 'play', 'video', 'mp4', 'play_url', 'download_url', 'down_url', 'src', 'videoUrl']
                    for k in direct_keys:
                        v = d.get(k)
                        if v:
                            if isinstance(v, dict):
                                v2 = v.get('url') or v.get('src') or v.get('play') or v.get('download')
                                if v2:
                                    return v2
                            if isinstance(v, list):
                                return v[0] if v else None
                            if isinstance(v, str):
                                return v
                    # 常见嵌套：video -> {play/url/src/url_list}
                    vid = d.get('video') or d.get('video_info') or {}
                    if isinstance(vid, dict):
                        for candidate in [vid.get('play'), vid.get('url'), vid.get('src')]:
                            if isinstance(candidate, str) and candidate:
                                return candidate
                            if isinstance(candidate, list) and candidate:
                                return candidate[0]
                            if isinstance(candidate, dict):
                                for kk in ['url', 'src']:
                                    if candidate.get(kk):
                                        val = candidate.get(kk)
                                        return val[0] if isinstance(val, list) and val else val
                        # url_list 常见
                        url_list = vid.get('url_list') or vid.get('play_addr') or {}
                        if isinstance(url_list, dict):
                            for kk in ['url_list', 'url', 'src']:
                                val = url_list.get(kk)
                                if isinstance(val, list) and val:
                                    return val[0]
                                if isinstance(val, str) and val:
                                    return val
                        if isinstance(url_list, list) and url_list:
                            return url_list[0]
                    # 其他容器 keys
                    for key in ['data', 'result', 'res']:
                        sub = d.get(key)
                        if isinstance(sub, dict):
                            val = pick_url(sub)
                            if val:
                                return val
                    return None

                def pick_images(d: Dict[str, Any]):
                    keys = ['images', 'imgs', 'image_list', 'pics', 'photos', 'image', 'images_list', 'imgurl']
                    for k in keys:
                        val = d.get(k)
                        if isinstance(val, list):
                            out = []
                            for it in val:
                                if isinstance(it, dict):
                                    # 常见：url / src / url_list
                                    u = it.get('url') or it.get('src')
                                    if not u:
                                        ul = it.get('url_list')
                                        if isinstance(ul, list) and ul:
                                            u = ul[0]
                                    if u:
                                        out.append(u)
                                else:
                                    out.append(it)
                            return [x for x in out if x]
                        if isinstance(val, dict):
                            # 兼容 image: {url: ...}
                            u = val.get('url') or val.get('src')
                            if u:
                                return [u]
                    return []

                def pick_author(d: Dict[str, Any]):
                    a = d.get('author') or d.get('user') or d.get('up') or d.get('owner')
                    if isinstance(a, dict):
                        name = a.get('name') or a.get('nickname') or a.get('uname') or ''
                        return {'name': name}
                    if isinstance(a, str):
                        return {'name': a}
                    return {'name': ''}

                # 从 endpoint 识别平台
                platform = 'unknown'
                if 'weibo' in endpoint:
                    platform = 'weibo'
                elif 'douyin' in endpoint or 'tiktok' in endpoint:
                    platform = 'douyin'
                elif 'bilibili' in endpoint:
                    platform = 'bilibili'
                elif 'xiaohongshu' in endpoint or 'xhs' in endpoint:
                    platform = 'xiaohongshu'
                
                normalized = {
                    'video_url': pick_url(data),
                    'title': data.get('title') or data.get('name') or data.get('desc') or '',
                    'author': pick_author(data),
                    'cover_url': data.get('cover_url') or data.get('cover') or data.get('pic') or '',
                    'images': pick_images(data),
                    'platform': platform,
                }
                if not normalized.get('video_url') and not normalized.get('images'):
                    return None
                LOGGER.info(f"ParseV2 API {endpoint} success: {normalized.get('title', 'Unknown')} [platform: {platform}]")
                return normalized
        except aiohttp.ClientError as e:
            LOGGER.warning(f"ParseV2 API {endpoint} request failed: {e}")
            return None

    try:
        async with aiohttp.ClientSession() as session:
            for ep in endpoints:
                res = await fetch_and_normalize(session, ep)
                if res:
                    return res
            return None
    except Exception as e:
        LOGGER.error(f"ParseV2 API unexpected error: {e}")
        return None


async def parse_weixin_article(url: str, timeout: int = 30) -> Optional[Dict[str, Any]]:
    """
    解析微信公众号文章
    
    Args:
        url: 微信公众号文章链接 (mp.weixin.qq.com)
        timeout: 超时时间(秒)
    
    Returns:
        {
            'title': '文章标题',
            'author': {'name': '公众号名称'},
            'images': ['https://...', ...],  # 图片列表
            'desc': '文章文本内容',
            'platform': 'weixin'
        }
    """
    try:
        # 检查是否是微信公众号链接
        if not re.match(r'^(http(s)?://)mp\.weixin\.qq\.com/s/.*', url):
            LOGGER.warning(f"Not a valid Weixin article URL: {url}")
            return None
        
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url,
                timeout=aiohttp.ClientTimeout(total=timeout),
                headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
            ) as response:
                if response.status != 200:
                    LOGGER.error(f"Weixin article fetch failed with status {response.status}")
                    return None
                
                html = await response.text()
                
                # 使用 BeautifulSoup 解析 HTML
                try:
                    from bs4 import BeautifulSoup
                except ImportError:
                    LOGGER.error("BeautifulSoup4 not installed. Please run: pip install beautifulsoup4 lxml")
                    return None
                
                soup = BeautifulSoup(html, 'lxml')
                
                # 提取标题
                title_tag = soup.find('h1', {'class': 'rich_media_title'})
                title = title_tag.text.strip() if title_tag else '微信文章'
                
                # 提取图片
                images = []
                
                # 方式1：rich_media_content 内的图片（常见）
                if rich_media_content := soup.find('div', {'class': 'rich_media_content'}):
                    img_tags = rich_media_content.find_all('img', {'class': 'rich_pages'})
                    for img in img_tags:
                        # 图片链接在 data-src 属性中
                        img_url = img.get('data-src') or img.get('src')
                        if img_url:
                            images.append(img_url)
                
                # 方式2：share_content_page 内的图片（图集模式）
                elif share_content_page := soup.find('div', {'class': 'share_content_page'}):
                    swiper_items = share_content_page.find_all('div', {'class': 'swiper_item'})
                    for item in swiper_items:
                        img = item.find('img')
                        if img:
                            img_url = img.get('data-src') or img.get('src')
                            if img_url:
                                images.append(img_url)
                
                # 如果没有找到图片，尝试所有img标签
                if not images:
                    all_imgs = soup.find_all('img')
                    for img in all_imgs:
                        img_url = img.get('data-src') or img.get('src')
                        if img_url and 'mmbiz.qpic.cn' in img_url:
                            images.append(img_url)
                
                # 提取作者/公众号名称
                author_tag = soup.find('a', {'id': 'js_name'}) or soup.find('strong', {'class': 'profile_nickname'})
                author_name = author_tag.text.strip() if author_tag else '微信公众号'
                
                # 提取文本内容（用于描述）
                desc = ''
                if rich_media_content := soup.find('div', {'class': 'rich_media_content'}):
                    # 提取纯文本
                    text_parts = rich_media_content.stripped_strings
                    desc = ' '.join(list(text_parts)[:200])  # 限制长度
                
                result = {
                    'title': title,
                    'author': {'name': author_name},
                    'images': images,
                    'desc': desc[:500] if desc else '',  # 限制描述长度
                    'platform': 'weixin',
                    'url': url,  # 添加原始URL，用于生成画廊ID
                }
                
                LOGGER.info(f"Weixin article parsed: {title}, {len(images)} images")
                return result
                
    except Exception as e:
        LOGGER.error(f"Weixin article parse error: {e}")
        import traceback
        LOGGER.error(traceback.format_exc())
        return None


async def parse_weibo_ajax(url: str) -> Optional[Dict[str, Any]]:
    """
    使用微博 Ajax API 解析微博内容（视频/图集）
    基于 ParseHub 项目的实现
    
    支持:
    - 视频 (video)
    - 图片 (photo)
    - LivePhoto
    - GIF
    - 混合媒体
    
    Args:
        url: 微博链接
        
    Returns:
        {
            'video_url': str,  # 视频URL（如果是视频）
            'images': list,    # 图片列表（如果是图集）
            'title': str,      # 标题/描述
            'author': {'name': str},
            'cover_url': str,  # 封面
            'platform': 'weibo'
        }
    """
    try:
        # 提取微博ID (bid) - 支持多种URL格式
        from urllib.parse import urlparse, parse_qs
        import re
        
        parsed = urlparse(url)
        bid = None
        
        LOGGER.info(f"Parsing Weibo URL: {url}")
        
        # 格式1: https://weibo.com/用户ID/微博ID
        # 格式2: https://m.weibo.cn/status/微博ID
        if '/status/' in url or re.search(r'/\d+/\w+', url):
            bid = url.split("/")[-1].split("?")[0]
            LOGGER.info(f"Format 1/2 detected, extracted bid: {bid}")
        
        # 格式3: https://video.weibo.com/show?fid=1034:xxx
        elif 'video.weibo.com/show' in url or 'h5.video.weibo.com' in url:
            LOGGER.info(f"Format 3 detected (video.weibo.com/show)")
            query_params = parse_qs(parsed.query)
            LOGGER.info(f"Query params: {query_params}")
            if 'fid' in query_params:
                fid = query_params['fid'][0]
                LOGGER.info(f"Found fid parameter: {fid}")
                # fid格式: 1034:4xxxxx，提取后面的数字部分
                if ':' in fid:
                    bid = fid.split(':')[1]
                    LOGGER.info(f"Extracted bid from fid: {bid}")
            else:
                # 如果没有fid，尝试访问页面提取真实ID
                LOGGER.info(f"Weibo video URL without fid, trying to extract from page: {url}")
                try:
                    async with aiohttp.ClientSession() as session:
                        async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                            if resp.status == 200:
                                html = await resp.text()
                                # 从HTML中提取微博ID (通常在 $render_data 变量中)
                                match = re.search(r'"id"[:\s]+"?(\d+)"?', html)
                                if match:
                                    bid = match.group(1)
                                    LOGGER.info(f"Extracted bid from page: {bid}")
                except Exception as e:
                    LOGGER.warning(f"Failed to extract bid from page: {e}")
        
        # 验证 bid
        LOGGER.info(f"Final extracted bid: {bid}, type: {type(bid)}, is_digit: {bid.isdigit() if bid else 'N/A'}, len: {len(bid) if bid else 0}")
        if not bid:
            LOGGER.warning(f"Empty Weibo bid from URL: {url}")
            return None
        # bid应该是纯数字（长bid如16位）或长度为9的短bid
        if not bid.isdigit():
            LOGGER.warning(f"Invalid Weibo bid (not digits): {bid} from URL: {url}")
            return None
        
        # 微博 Ajax API
        api_url = f"https://weibo.com/ajax/statuses/show?id={bid}"
        LOGGER.info(f"Calling Weibo Ajax API: {api_url}")
        headers = {
            "referer": "https://weibo.com",
            "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        cookies = {
            "SUB": "_2AkMR47Mlf8NxqwFRmfocxG_lbox2wg7EieKnv0L-JRMxHRl-yT9yqhFdtRB6OmOdyoia9pKPkqoHRRmSBA_WNPaHuybH",
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.get(
                api_url,
                headers=headers,
                cookies=cookies,
                timeout=aiohttp.ClientTimeout(total=30)
            ) as resp:
                if resp.status != 200:
                    LOGGER.warning(f"Weibo Ajax API status {resp.status}")
                    return None
                
                data = await resp.json()
                LOGGER.info(f"Weibo Ajax API full response keys: {list(data.keys())}")
                
                # 检查是否为错误响应
                if data.get('ok') == 0 or 'error_code' in data:
                    error_msg = data.get('message', 'Unknown error')
                    error_code = data.get('error_code', 'N/A')
                    LOGGER.warning(f"Weibo Ajax API error: code={error_code}, message={error_msg}")
                    LOGGER.info(f"Trying to convert bid format or access page HTML...")
                    
                    # 尝试访问原始URL的页面来提取真实ID
                    try:
                        async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as page_resp:
                            if page_resp.status == 200:
                                # 读取原始字节，尝试多种编码
                                raw_html = await page_resp.read()
                                html = None
                                for encoding in ['utf-8', 'gb2312', 'gbk', 'gb18030']:
                                    try:
                                        html = raw_html.decode(encoding)
                                        LOGGER.info(f"HTML decoded successfully with {encoding}")
                                        break
                                    except (UnicodeDecodeError, LookupError):
                                        continue
                                
                                if html:
                                    # 从HTML中提取微博ID
                                    import re
                                    # 查找 $render_data 中的 status id
                                    match = re.search(r'"(?:status|id)"[^}]*?"id"[:\s]+"?(\d+)"?', html)
                                    if match:
                                        new_bid = match.group(1)
                                        LOGGER.info(f"Extracted new bid from HTML: {new_bid}")
                                        if new_bid != bid:
                                            # 用新的 bid 重新请求
                                            api_url = f"https://weibo.com/ajax/statuses/show?id={new_bid}"
                                            async with session.get(api_url, headers=headers, cookies=cookies, timeout=aiohttp.ClientTimeout(total=30)) as retry_resp:
                                                if retry_resp.status == 200:
                                                    data = await retry_resp.json()
                                                    LOGGER.info(f"Retry with new bid success: {new_bid}")
                    except Exception as e:
                        LOGGER.warning(f"Failed to extract from HTML: {e}")
                
                LOGGER.info(f"Weibo Ajax API response: id={data.get('id')}, has_page_info={bool(data.get('page_info'))}, has_pic_infos={bool(data.get('pic_infos'))}, has_mix_media={bool(data.get('mix_media_info'))}")
        
        # 解析返回数据
        if not data or data.get('ok') == 0:
            return None
        
        # 提取基本信息
        text_raw = data.get('text_raw', '')
        text = data.get('text', '')
        author_name = data.get('user', {}).get('screen_name', '未知')
        
        # 清理文本
        page_info = data.get('page_info')
        if page_info and page_info.get('short_url'):
            text_raw = text_raw.replace(page_info['short_url'], '').strip()
        
        result = {
            'title': text_raw or '微博视频',
            'author': {'name': author_name},
            'platform': 'weibo'
        }
        
        # 检查是否为视频类型
        pic_infos = data.get('pic_infos')
        mix_media_info = data.get('mix_media_info')
        
        # 情况1: 单个视频 (page_info.object_type == 'video')
        if page_info and page_info.get('object_type') == 'video':
            media_info = page_info.get('media_info', {})
            video_url = media_info.get('mp4_hd_url') or media_info.get('mp4_sd_url')
            cover_url = page_info.get('page_pic')
            
            if video_url:
                result['video_url'] = video_url
                result['cover_url'] = cover_url
                LOGGER.info(f"Weibo Ajax: video parsed - {text_raw[:50]}")
                return result
        
        # 情况2: 图集或混合媒体
        media_items = []
        
        # 优先处理 mix_media_info (混合媒体)
        if mix_media_info and mix_media_info.get('items'):
            for item in mix_media_info['items']:
                item_type = item.get('type')
                item_data = item.get('data', {})
                
                if item_type == 'video':
                    # 视频
                    media_info = item_data.get('media_info', {})
                    video_url = media_info.get('mp4_hd_url') or media_info.get('mp4_sd_url')
                    if video_url:
                        media_items.append({'type': 'video', 'url': video_url})
                elif item_type == 'pic':
                    # 图片
                    largest = item_data.get('largest', {})
                    pic_url = largest.get('url')
                    if pic_url:
                        media_items.append({'type': 'image', 'url': pic_url})
        
        # 处理 pic_infos (纯图集)
        elif pic_infos:
            for pic_id, pic_data in pic_infos.items():
                pic_type = pic_data.get('type')
                
                if pic_type == 'pic':
                    # 普通图片
                    largest = pic_data.get('largest', {})
                    pic_url = largest.get('url')
                    if pic_url:
                        media_items.append({'type': 'image', 'url': pic_url})
                elif pic_type == 'livephoto':
                    # LivePhoto - 只取静态图部分，统一作为图片处理
                    largest = pic_data.get('largest', {})
                    pic_url = largest.get('url')
                    if pic_url:
                        media_items.append({'type': 'image', 'url': pic_url})
                        LOGGER.info(f"LivePhoto detected, using static image: {pic_url}")
                elif pic_type == 'gif':
                    # GIF - 取视频URL
                    video_url = pic_data.get('video')
                    if video_url:
                        media_items.append({'type': 'gif', 'url': video_url})
        
        # 检查转发微博
        if not media_items:
            retweeted_status = data.get('retweeted_status')
            if retweeted_status and retweeted_status.get('pic_infos'):
                for pic_id, pic_data in retweeted_status['pic_infos'].items():
                    largest = pic_data.get('largest', {})
                    pic_url = largest.get('url')
                    if pic_url:
                        media_items.append({'type': 'image', 'url': pic_url})
        
        # 分类处理媒体
        if media_items:
            # 检查是否全是图片
            all_images = all(item['type'] == 'image' for item in media_items)
            
            if all_images:
                # 纯图集
                result['images'] = [item['url'] for item in media_items]
                LOGGER.info(f"Weibo Ajax: {len(result['images'])} images parsed")
            else:
                # 混合或视频
                videos = [item['url'] for item in media_items if item['type'] in ('video', 'gif')]
                images = [item['url'] for item in media_items if item['type'] == 'image']
                
                if videos:
                    result['video_url'] = videos[0]  # 取第一个视频
                if images:
                    result['images'] = images
                
                LOGGER.info(f"Weibo Ajax: mixed media - {len(videos)} videos, {len(images)} images")
            
            return result
        
        LOGGER.warning("Weibo Ajax: no media found")
        return None
        
    except Exception as e:
        LOGGER.error(f"Weibo Ajax parse error: {e}")
        import traceback
        LOGGER.error(traceback.format_exc())
        return None

