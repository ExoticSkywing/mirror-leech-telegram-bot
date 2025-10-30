"""
视频链接解析和下载模块
支持Parse-Video解析的所有平台，包括视频和图集
"""

from asyncio import sleep
from aiofiles import open as aioopen
from aiofiles.os import path as aiopath, makedirs, remove as aioremove
from os import path as ospath
from pyrogram.types import InputMediaPhoto, InputMediaVideo
from time import time
from urllib.parse import urlparse

from bot import LOGGER, DOWNLOAD_DIR, bot_loop, user_data
from bot.core.config_manager import Config
from bot.helper.ext_utils.bot_utils import new_task, sync_to_async
from bot.helper.ext_utils.files_utils import clean_target
from bot.helper.ext_utils.url_utils import get_domain
from bot.helper.parse_video_helper import parse_video_api, parse_video_v2_api, format_video_info, parse_weixin_article
from bot.helper.listeners.task_listener import TaskListener
from bot.helper.mirror_leech_utils.download_utils.yt_dlp_download import YoutubeDLHelper
from bot.helper.ext_utils.membership_utils import check_membership
from bot.helper.telegram_helper.message_utils import (
    send_message,
    edit_message,
    delete_message,
)
from pyrogram.errors import FloodWait


class VideoLinkProcessor(TaskListener):
    """
    视频链接处理器
    完整流程：Parse-Video解析 → yt-dlp下载 → 上传到TG
    支持视频和图集（相册）
    """

    def __init__(self, client, message, url):
        self.message = message
        self.client = client
        self.url = url
        self.status_msg = None
        self.download_path = None
        super().__init__()
        # 确保关键属性不是None
        if self.name is None:
            self.name = ""
        if self.thumb is None:
            self.thumb = "none"  # 使用"none"而不是None，避免is_telegram_link检查失败
        # 设置same_dir为None（我们不使用多链接功能）
        self.same_dir = None
        self.is_leech = True  # 强制leech模式（上传到TG）
        self.is_ytdlp = True
        # 强制将上传目标指向当前对话，避免走全局 LEECH_DUMP_CHAT
        # 说明：TaskListener.before_start() 会优先使用 self.up_dest（若已设置）
        # 这里将其固定为当前消息所在的 chat，确保直发给用户/当前会话
        # 更改上传目的地：回归集中转存群，并优先 Hybrid（h: 前缀由上游解析）
        # 说明：sudo 用户（SUDO_USERS）走私有目的地，其余走公有目的地
        try:
            sudo_ids = set()
            if isinstance(Config.SUDO_USERS, str):
                sudo_ids = {int(x) for x in Config.SUDO_USERS.split() if x.strip().lstrip('-').isdigit()}
            elif isinstance(Config.SUDO_USERS, (list, tuple, set)):
                sudo_ids = {int(x) for x in Config.SUDO_USERS}
        except Exception:
            sudo_ids = set()

        try:
            uid = self.message.from_user.id if self.message.from_user else None
        except Exception:
            uid = None

        try:
            owner_match = bool(uid) and int(getattr(Config, 'OWNER_ID', 0) or 0) == int(uid)
        except Exception:
            owner_match = False

        try:
            runtime_sudo = bool(user_data.get(uid, {}).get('SUDO')) if uid else False
        except Exception:
            runtime_sudo = False

        is_sudo = bool(uid and (owner_match or uid in sudo_ids or runtime_sudo))

        dest_chat = None
        # 管理员/超级用户：投递到私有汇总群 LEECH_DUMP_CHAT
        if getattr(Config, 'ENABLE_SUDO_PRIVATE_DUMP', True) and is_sudo and getattr(Config, 'LEECH_DUMP_CHAT', ''):
            dest_chat = Config.LEECH_DUMP_CHAT
            self.private_dump = True
        else:
            # 普通用户：投递到公共汇总群 LEECH_PUBLIC_DUMP_CHAT（若未配置则回退到 LEECH_DUMP_CHAT）
            dest_chat = getattr(Config, 'LEECH_PUBLIC_DUMP_CHAT', '') or getattr(Config, 'LEECH_DUMP_CHAT', '')
            self.private_dump = False

        if dest_chat:
            self.up_dest = f"h:{dest_chat}"
        else:
            self.up_dest = None

    async def on_download_complete(self):
        """覆盖下载完成回调，添加日志"""
        LOGGER.info(f"VideoLinkProcessor: on_download_complete called for {self.name}")
        try:
            await super().on_download_complete()
            LOGGER.info(f"VideoLinkProcessor: upload completed for {self.name}")
        except Exception as e:
            LOGGER.error(f"VideoLinkProcessor: on_download_complete error: {e}")
            import traceback
            LOGGER.error(traceback.format_exc())
            raise
    
    async def execute(self):
        """执行完整处理流程"""

        # 发送初始状态消息
        self.status_msg = await send_message(
            self.message,
            f"🔍 检测到视频链接\n"
            f"📡 开始处理...\n"
            f"🔗 <code>{self.url[:60]}...</code>",
        )

        try:
            # 关键阶段直查 1：下载前校验（忽略缓存，确保未取关）
            try:
                from bot.core.config_manager import Config as CFG
                if CFG.PARSE_VIDEO_CHANNEL_CHECK_ENABLED and CFG.PARSE_VIDEO_CHECK_SCOPE in {"direct_only", "all"}:
                    # 豁免逻辑在 check_membership 内部已处理，这里直接调用直查
                    ok = await check_membership(self.client, self.message.from_user.id, use_cache=False)
                    if not ok:
                        await edit_message(self.status_msg, "❌ 已取消：请先关注指定频道再使用。")
                        await self.remove_from_same_dir()
                        return
            except Exception:
                pass
            # 策略1: 尝试解析（部分平台优先使用新接口）
            video_direct_url = None
            video_info = {}
            images_list = []

            await edit_message(
                self.status_msg,
                f"📡 正在解析链接...\n" f"🔗 <code>{self.url[:60]}...</code>",
            )

            # 平台判断：B站、微博、皮皮虾、汽水音乐优先走 v2
            domain = get_domain(self.url) or ""
            url_lower = (self.url or "").lower()
            prefer_v2_domains = {
                "bilibili.com", "b23.tv",
                "weibo.com", "weibo.cn", "m.weibo.cn", "video.weibo.com", "h5.video.weibo.com",
                "pipix.com", "h5.pipix.com",
                "music.douyin.com", "qishui.douyin.com",
                "music.163.com", "163cn.link",
            }
            prefer_v2 = (
                (domain in prefer_v2_domains)
                or domain.endswith("weibo.com")
                or domain.endswith("weibo.cn")
                or (domain.endswith("douyin.com") and "/music" in url_lower)
            )

            parse_result = None
            
            # 优先检查微信公众号（直接本地解析）
            if "mp.weixin.qq.com" in self.url:
                LOGGER.info("Detected Weixin article, using local parser")
                parse_result = await parse_weixin_article(self.url)
            
            # 如果不是微信或解析失败，尝试API
            if not parse_result:
            if prefer_v2:
                # 新接口优先
                parse_result = await parse_video_v2_api(self.url)
                if not parse_result:
                    parse_result = await parse_video_api(self.url)
            else:
                # 旧接口优先
                parse_result = await parse_video_api(self.url)
                if not parse_result:
                    parse_result = await parse_video_v2_api(self.url)

            if parse_result:
                # Parse-Video解析成功
                video_direct_url = parse_result.get("video_url")
                images_list = parse_result.get("images", [])

                video_info = {
                    "title": parse_result.get("title", ""),
                    "author": parse_result.get("author", {}).get("name", ""),
                    "cover_url": parse_result.get("cover_url", ""),
                    "platform": parse_result.get("platform", ""),
                    "url": parse_result.get("url", self.url),  # 确保有URL用于画廊ID生成
                }

                # 判断是视频还是图集
                if images_list:
                    # 图集处理
                    await edit_message(
                        self.status_msg,
                        f"✅ Parse-Video 解析成功！\n\n"
                        f"📸 <b>类型:</b> 图集\n"
                        f"📹 <b>标题:</b> {video_info['title']}\n"
                        f"👤 <b>作者:</b> {video_info['author']}\n"
                        f"🖼️ <b>图片数:</b> {len(images_list)} 张\n\n"
                        f"⬇️ 开始下载图集...",
                    )

                    LOGGER.info(
                        f"Parse-Video image gallery: {video_info['title']} ({len(images_list)} images)"
                    )

                    # 下载并上传图集
                    await self._handle_image_gallery(images_list, video_info)
                    return

                elif video_direct_url:
                    # 视频处理
                    await edit_message(
                        self.status_msg,
                        f"✅ Parse-Video 解析成功！\n\n"
                        f"{format_video_info(parse_result)}\n\n"
                        f"⬇️ 开始下载视频...",
                    )

                    LOGGER.info(f"Parse-Video success: {video_info['title']}")
                    # 针对音频直链（如网易云）：设置音频元数据供上传阶段使用
                    try:
                        lower_url = (video_direct_url or '').lower()
                        if video_info.get('platform') == 'NetEaseMusic' or lower_url.endswith('.mp3'):
                            self.audio_title = video_info.get('title') or ''
                            self.audio_performer = video_info.get('author') or ''
                            # 若文件名无扩展名，强制设为 .mp3
                            if self.name and not self.name.lower().endswith(('.mp3', '.m4a', '.flac', '.ogg', '.opus')):
                                self.name = f"{self._sanitize_filename(self.name)}.mp3"
                    except Exception:
                        pass
                else:
                    # 解析结果无效
                    raise ValueError("Parse-Video返回了空结果")

            else:
                # Parse-Video解析失败，继续尝试
                LOGGER.info("Parse-Video failed, will try yt-dlp directly")
                await edit_message(
                    self.status_msg,
                    f"⚠️ Parse-Video 未能解析\n"
                    f"🔄 尝试 yt-dlp 直接处理...\n"
                    f"🔗 <code>{self.url[:60]}...</code>",
                )

            # 策略2: 视频处理 - 先尝试URL直传，失败后下载
            download_url = video_direct_url if video_direct_url else self.url
            
            # 对于Parse-Video成功解析的直链，先尝试URL直传（仅小红书）
            if video_direct_url and video_info:
                try:
                    # 通过URL特征检测平台（Parse-Video返回的数据中没有platform字段）
                    # 仅对小红书启用视频URL直传
                    is_xiaohongshu = any([
                        'xhslink.com' in self.url.lower(),
                        'xiaohongshu.com' in self.url.lower(),
                        'xhscdn.com' in download_url.lower(),
                    ])
                    
                    if is_xiaohongshu:
                        LOGGER.info(f"Detected Xiaohongshu video, attempting URL direct upload")
                        # 添加平台信息到video_info
                        video_info['platform'] = 'Xiaohongshu'
                        await self._upload_video_by_url(download_url, video_info)
                        return  # 成功则直接返回
                    else:
                        LOGGER.info(f"Non-Xiaohongshu video, using download mode (URL: {self.url[:50]}...)")
                except Exception as url_err:
                    LOGGER.warning(f"Video URL direct upload failed: {url_err}, falling back to download mode")
            
            # 回退：使用yt-dlp下载视频
            await self._download_with_ytdlp(download_url, video_info)

        except Exception as e:
            # 所有策略都失败
            error_msg = str(e)
            LOGGER.error(f"All strategies failed: {error_msg}")

            await edit_message(
                self.status_msg,
                f"❌ <b>不支持该URL或下载失败</b>\n\n"
                f"📝 错误信息:\n<code>{error_msg}</code>\n\n"
                f"💡 可能原因:\n"
                f"• 平台不支持或链接已失效\n"
                f"• 需要登录或有地域限制\n"
                f"• 视频已被删除\n\n"
                f"🔗 原始链接:\n<code>{self.url}</code>",
            )

            await self.remove_from_same_dir()

    async def on_upload_start(self):
        """上传开始前的钩子。
        允许当前已通过校验且已启动的任务继续上传，即使期间用户取关；
        因此此处不再进行二次拦截，下一次新任务会在入口处重新校验。
        """
        return

    async def _download_with_ytdlp(self, url, video_info=None):
        """使用yt-dlp下载视频"""

        try:
            # 设置链接（YoutubeDLHelper需要从self.link获取）
            self.link = url
            
            # 初始化TaskListener
            await self.before_start()

            # 设置下载路径
            self.download_path = f"{DOWNLOAD_DIR}{self.mid}"

            # 设置视频信息
            if video_info:
                if video_info.get("title"):
                    # 基于解析标题设置自定义文件名
                    base = self._sanitize_filename(video_info["title"])
                    # 若为音频直链（如网易云/或URL以.mp3结尾），补充扩展名
                    try:
                        is_audio = False
                        if isinstance(url, str) and url.lower().endswith((".mp3", ".m4a", ".flac", ".ogg", ".opus")):
                            is_audio = True
                        elif str(video_info.get("platform", "")).lower() in {"neteasemusic".lower()}:
                            is_audio = True
                        self.name = f"{base}.mp3" if is_audio and not base.lower().endswith((".mp3", ".m4a", ".flac", ".ogg", ".opus")) else base
                        # 锁定自定义文件名，防止 yt-dlp 日志回调覆盖
                    except Exception:
                        self.name = base
                    self.lock_name = True
                if video_info.get("cover_url"):
                    self.thumb = video_info["cover_url"]

            # 更新状态
            await edit_message(
                self.status_msg,
                f"📥 正在下载视频...\n" f"📹 {self.name if self.name else '视频'}",
            )

            # 准备yt-dlp选项（统一优先合并为 MKV，避免比例问题）
            options = {
                "usenetrc": True,
                "cookiefile": "cookies.txt",
                "merge_output_format": "mkv",
            }

            # 先提取视频信息（测试链接是否有效）
            from bot.modules.ytdlp import extract_info

            test_options = options.copy()
            test_options["playlist_items"] = "0"

            # 测试提取信息
            try:
                result = await sync_to_async(extract_info, url, test_options)
            except Exception as e:
                raise Exception(f"无法提取视频信息: {str(e)}")

            if not result:
                raise Exception("视频信息提取失败，链接可能无效")

            # 删除状态消息，让yt-dlp的进度显示接管
            await delete_message(self.status_msg)
            self.status_msg = None

            # 使用YoutubeDLHelper下载
            ydl = YoutubeDLHelper(self)

            # 默认：按站点选择清晰度策略
            domain = get_domain(self.url)
            short_video_domains = {"douyin.com", "iesdouyin.com", "tiktok.com", "kuaishou.com", "v.kuaishou.com", "xiaohongshu.com", "xhslink.com", "ixigua.com"}
            large_video_domains = {"youtube.com", "youtu.be", "bilibili.com"}

            if domain in short_video_domains:
                preferred_qual = "bestvideo+bestaudio/best"
            elif domain in {"youtube.com", "youtu.be"}:
                # YouTube：严格匹配“1080p30.0-mp4”按钮对应的视频流 → format_id + ba[ext=m4a]
                def pick_exact_1080p30_mp4(formats_list):
                    candidates = []
                    for f in formats_list:
                        if (f.get("ext") == "mp4" and (f.get("height") == 1080)):
                            fps = f.get("fps")
                            # 精确 30fps（可能是 30 或 30.0）
                            try:
                                if fps is not None and float(fps) == 30.0:
                                    candidates.append(f)
                            except Exception:
                                continue
                    if not candidates:
                        return None
                    # 优先 avc1，再按 tbr 最大
                    candidates.sort(key=lambda x: (("avc1" in (x.get("vcodec") or "")), (x.get("tbr") or 0)), reverse=True)
                    return candidates[0].get("format_id")

                formats_list = result.get("formats") or []
                exact_fmt = pick_exact_1080p30_mp4(formats_list)
                if exact_fmt:
                    # 完全等效于按钮：format_id + ba[ext=m4a]，再到 +ba，再到同高回退（强制 fps<=30）
                    preferred_qual = (
                        f"{exact_fmt}+ba[ext=m4a]/{exact_fmt}+ba/"
                        "bv*[ext=mp4][height=1080][fps<=30]+ba[ext=m4a]/"
                        "bv*[ext=mp4][height<=1080][fps<=30]+ba/b[height<=1080]"
                    )
                else:
                    # 没有严格 1080p30-mp4：所有回退均限制 fps<=30，优先 avc1
                    preferred_qual = (
                        "bv*[ext=mp4][vcodec*=avc1][height=1080][fps<=30]+ba[ext=m4a]"
                        "/bv*[ext=mp4][vcodec*=avc1][height<=1080][fps<=30]+ba[ext=m4a]"
                        "/bv*[ext=mp4][height<=1080][fps<=30]+ba/"
                        "b[height<=1080]"
                    )
            elif domain in {"bilibili.com"}:
                # B站：默认中等清晰度（≤720p）以控制体积
                preferred_qual = "bestvideo[height<=720]+bestaudio/best[height<=720]/best[height<=720]"
            else:
                # 其他长视频站：保守中等清晰度
                preferred_qual = "bestvideo[height<=720]+bestaudio/best[height<=720]/best[height<=720]"

            # 针对直链/单一流（Generic 提取器）进行自适应：若只有单一可下载格式，则使用 'best'
            formats = result.get("formats") or []
            if not formats or len(formats) <= 1:
                qual = "best"
            else:
                # 检测是否存在仅视频或仅音频分离流
                has_video_only = any((f.get("vcodec") or "none") != "none" and (f.get("acodec") or "none") == "none" for f in formats)
                has_audio_only = any((f.get("acodec") or "none") != "none" and (f.get("vcodec") or "none") == "none" for f in formats)
                has_progressive = any((f.get("vcodec") or "none") != "none" and (f.get("acodec") or "none") != "none" for f in formats)
                if has_progressive and not (has_video_only and has_audio_only):
                    # 只有合流可选，用 best 最稳妥
                    qual = "best"
                else:
                    qual = preferred_qual
            playlist = "entries" in result

            await ydl.add_download(self.download_path, qual, playlist, options)

            LOGGER.info(f"Download started: {url}")

        except Exception as e:
            # 记录详细错误信息
            import traceback
            LOGGER.error(f"yt-dlp download error details: {traceback.format_exc()}")
            # 重新抛出异常，让上层处理
            raise Exception(f"yt-dlp下载失败: {str(e)}")

    async def _upload_gallery_by_url(self, images_list, video_info, gallery_url=None):
        """
        直接使用URL上传图集到Telegram（无需下载）
        
        Args:
            images_list: 图片URL列表
            gallery_url: 在线画廊URL（可选）
            video_info: 视频信息
            
        Raises:
            Exception: 如果任何一张图片上传失败
        """
        from pyrogram.types import InputMediaPhoto
        from asyncio import sleep
        from pyrogram.errors import FloodWait
        from time import time
        
        LOGGER.info("Attempting direct URL upload for gallery")
        start_ts = time()  # 记录开始时间
        
        # 提取图片 URLs
        image_urls = []
        for img in images_list:
            url = img.get("url") if isinstance(img, dict) else img
            if url:
                image_urls.append(url)
        
        if not image_urls:
            raise Exception("No valid image URLs found")
        
        # 目的地：统一沿用 self.up_dest 路由
        upload_dest = self.up_dest if hasattr(self, 'up_dest') and self.up_dest else self.message.chat.id
        if isinstance(upload_dest, str) and upload_dest.startswith('h:'):
            dest_str = upload_dest[2:]
            if dest_str.strip().lstrip('-').isdigit():
                upload_dest = int(dest_str)
            else:
                upload_dest = dest_str
        
        # 分批上传（串行，每批最多10张，固定延迟避免FloodWait）
        from ..helper.telegram_helper.button_build import ButtonMaker
        
        total_imgs = len(image_urls)
        total_batches = (len(image_urls) + 9) // 10
        
        # 直接更新现有状态消息为上传进度
        title = video_info.get('title', '图集')
        author = video_info.get('author', '未知作者')
        await edit_message(
            self.status_msg,
            f"✅ <b>解析成功！</b>共 {total_imgs} 张图片\n\n"
            f"📹 {title}\n"
            f"👤 {author}\n\n"
            f"📤 正在做预处理... 0/{total_imgs}"
        )
        upload_status_msg = self.status_msg  # 复用同一个消息
        
        base_caption = self._build_caption(video_info)
        total_sent = 0
        album_links = []
        batch_index = 0
        
        # 获取用户信息
        user_name = self.message.from_user.first_name or "未知用户"
        user_mention = f"<a href='https://t.me/nebuluxe_flash_bot'>{user_name}</a>"
        
        # 串行上传每个批次
        for start in range(0, len(image_urls), 10):
            batch_urls = image_urls[start:start + 10]
            media_group = []
            
            # 为每个相册添加标注
            album_number = batch_index + 1
            album_caption = f"{base_caption}\n\n📌 来自 {user_mention} 的相册 {album_number}/{total_batches}"
            
            # 构建媒体组
            for idx, img_url in enumerate(batch_urls):
                if idx == 0:  # 每个相册的第一张图片带 caption
                    media_group.append(InputMediaPhoto(media=img_url, caption=album_caption))
                else:
                    media_group.append(InputMediaPhoto(media=img_url))
            
            LOGGER.info(f"Uploading URL media group batch {batch_index + 1}/{total_batches} ({len(media_group)} images)")
            
            # 上传批次（带重试机制）
            attempt = 0
            max_flood_wait = 60
            while True:
                try:
                    msgs = await self.client.send_media_group(
                        chat_id=upload_dest, media=media_group, reply_to_message_id=None
                    )
                    break
                except FloodWait as f:
                    wait_s = int(f.value) + 1
                    if wait_s > max_flood_wait:
                        LOGGER.error(f"FloodWait too long ({wait_s}s), aborting URL upload")
                        raise
                    LOGGER.warning(f"⏳ FloodWait {wait_s}s for batch {batch_index + 1}/{total_batches}")
                    # 更新进度提示：显示等待状态
                    try:
                        # 构建当前的按钮（已上传的相册）
                        buttons = ButtonMaker()
                        for i, link in enumerate(album_links):
                            buttons.url_button(f"📸 相册 {i+1}", link)
                        
                        await edit_message(
                            upload_status_msg,
                            f"✅ <b>解析成功！</b>共 {total_imgs} 张图片\n\n"
                            f"📹 {title}\n"
                            f"👤 {author}\n\n"
                            f"⏳ 人数过多需排队，请耐心等待  {wait_s}秒… {total_sent}/{total_imgs}",
                            buttons.build_menu(3) if album_links else None
                        )
                    except Exception:
                        pass
                    await sleep(wait_s)
                    attempt += 1
                    if attempt >= 3:
                        LOGGER.error(f"Max FloodWait retries reached")
                        raise
                except Exception as e:
                    LOGGER.error(f"URL upload failed: {e}")
                    raise
            
            # 记录结果
            total_sent += len(msgs)
            if msgs and hasattr(msgs[0], "link"):
                album_links.append(msgs[0].link)
            batch_index += 1
            
            # 实时更新进度 + 已上传相册的按钮
            try:
                buttons = ButtonMaker()
                for i, link in enumerate(album_links):
                    buttons.url_button(f"📸 相册 {i+1}", link)
                
                await edit_message(
                    upload_status_msg,
                    f"✅ <b>解析成功！</b>共 {total_imgs} 张图片\n\n"
                    f"📹 {title}\n"
                    f"👤 {author}\n\n"
                    f"📤 上传中: {total_sent}/{total_imgs} ({batch_index}/{total_batches} 组) ⚡",
                    buttons.build_menu(3)
                )
            except Exception:
                pass
            
            # 批次间延迟（安全阈值，避免触发FloodWait）
            if batch_index < total_batches:  # 最后一批不需要延迟
                await sleep(1.5)
        
        # 最终汇总消息：更新原消息为完成状态 + 所有相册按钮
        from time import time
        buttons = ButtonMaker()
        for i, link in enumerate(album_links):
            buttons.url_button(f"📸 相册 {i+1}", link)
        
        # 计算耗时（从开始到现在）
        elapsed = int(time() - start_ts)
        
        # 构建完成消息
        completion_msg = (
                f"✅ <b>图集上传完成</b> 📸 {total_sent}/{total_imgs}\n\n"
                f"📹 {title}\n"
                f"👤 {author}\n\n"
                f"⏱️ 耗时: {elapsed}秒\n"
            f"⚡ 直传模式"
        )
        
        # 如果有画廊URL，添加到消息中
        if gallery_url:
            completion_msg += (
                f"\n\n"
                f"🌐 <b>在线画廊</b>：\n"
                f"<code>{gallery_url}</code>\n"
                f"💡 点击链接可在浏览器查看完整画廊"
            )
        
        try:
            await edit_message(
                upload_status_msg,
                completion_msg,
                buttons.build_menu(3)
            )
        except Exception as e:
            LOGGER.warning(f"Failed to update final message: {e}")
        
        LOGGER.info(f"URL direct upload successful: {total_sent}/{total_imgs} images")

    async def _upload_video_by_url(self, video_url, video_info):
        """
        直接使用URL上传视频到Telegram（无需下载）
        
        Args:
            video_url: 视频直链URL
            video_info: 视频信息字典
            
        Raises:
            Exception: 如果上传失败
        """
        from asyncio import sleep
        
        LOGGER.info(f"Attempting direct URL upload for video: {video_url[:100]}...")
        
        # 更新状态
        await edit_message(
            self.status_msg,
            f"⚡ 正在通过 URL 直传视频...\n"
            f"📹 {video_info.get('title', '视频')}\n"
            f"💡 <i>无需下载，直接上传</i>",
        )
        
        # 目的地：使用与其他视频相同的逻辑
        upload_dest = self.up_dest if hasattr(self, 'up_dest') and self.up_dest else self.message.chat.id
        
        # 如果 up_dest 是 h: 格式，提取实际的 chat_id
        if isinstance(upload_dest, str) and upload_dest.startswith('h:'):
            dest_str = upload_dest[2:]
            if dest_str.strip().lstrip("-").isdigit():
                upload_dest = int(dest_str)
            else:
                upload_dest = dest_str
        
        # 准备标题和缩略图
        caption = self._build_caption(video_info)
        thumb_url = video_info.get('cover_url') or video_info.get('cover')
        
        try:
            # 尝试直接用URL上传视频
            LOGGER.info(f"Sending video via URL to {upload_dest}")
            
            # 发送视频（使用URL）
            msg = await self.client.send_video(
                chat_id=upload_dest,
                video=video_url,
                caption=caption,
                thumb=thumb_url if thumb_url else None,
                supports_streaming=True,
                disable_notification=False
            )
            
            # 删除状态消息
            await delete_message(self.status_msg)
            self.status_msg = None
            
            # 发送成功消息
            platform = video_info.get('platform', '未知平台')
            text = (
                f"✅ <b>视频上传完成</b> ⚡ <i>URL直传模式</i>\n\n"
                f"📹 {video_info.get('title', '视频')}\n\n"
                f"👤 {video_info.get('author', '未知作者')}\n"
                f"🌐 平台: {platform}"
            )
            
            if msg and hasattr(msg, 'link'):
                text += f"\n🔗 <a href='{msg.link}'>查看视频</a>"
            
            await send_message(self.message, text)
            
            LOGGER.info(f"Video URL direct upload successful for {platform}")
            
        except Exception as e:
            LOGGER.error(f"Video URL upload failed: {e}")
            # 记录详细错误
            import traceback
            LOGGER.error(f"Video URL upload error traceback: {traceback.format_exc()}")
            # 抛出异常让外层回退到下载模式
            raise Exception(f"视频URL上传失败: {str(e)}")

    async def _handle_image_gallery(self, images_list, video_info):
        """
        处理图集下载和上传
        将图集作为媒体组（相册）上传到Telegram
        
        支持两种模式：
        1. Worker 画廊模式（推荐）：Catbox图床 + Cloudflare Worker，国内外均可访问
        2. Telegram 直接上传模式：下载后上传到群组

        Args:
            images_list: 图片URL列表 [{'url': 'https://...', 'live_photo_url': '...'}, ...]
            video_info: 视频信息字典
        """

        try:
            LOGGER.info(f"Starting image gallery processing: {len(images_list)} images")
            
            # 检查是否启用 Worker 画廊模式
            if Config.USE_TELEGRAPH_FOR_GALLERY:
                LOGGER.info("Using Worker Gallery mode (Catbox + Cloudflare)")
                await self._handle_gallery_telegraph_mode(images_list, video_info)
                return
            
            # 原有逻辑：Telegram 直接上传模式
            LOGGER.info("Using Telegram direct upload mode")
            await self._handle_gallery_telegram_mode(images_list, video_info)
            
        except Exception as e:
            LOGGER.error(f"Image gallery processing error: {e}")
            import traceback
            LOGGER.error(traceback.format_exc())
            await edit_message(
                self.status_msg,
                f"❌ 图集处理失败\n📝 错误: {str(e)}"
            )
            raise


    async def _handle_gallery_telegraph_mode(self, images_list, video_info):
        """Worker 画廊模式：先检查缓存 → 下载 → Catbox图床 → Worker画廊"""
        
        import aiohttp
        from bot.core.config_manager import Config
        from bot.helper.telegram_helper.button_build import ButtonMaker
        
        start_time = time()
        # 优先使用 video_info 中的 URL，如果没有则使用 self.url（原始输入URL）
        original_url = video_info.get('webpage_url') or video_info.get('url') or self.url
        
        if not original_url:
            LOGGER.warning("No URL found for gallery ID generation, using fallback")
            original_url = f"gallery_{int(time() * 1000)}"  # 时间戳作为最后的后备
        
        # ========== 第0步：生成确定性ID并检查是否已存在 ==========
        gallery_id = self._generate_gallery_id(original_url)
        LOGGER.info(f"Gallery ID: {gallery_id} for URL: {original_url}")
        
        worker_api = getattr(Config, 'WORKER_GALLERY_API', '')
        if not worker_api:
            raise Exception("WORKER_GALLERY_API 未配置")
        
        # 检查缓存开关
        use_cache = getattr(Config, 'USE_GALLERY_CACHE', True)
        
        if use_cache:
            # 先检查画廊是否已存在
        await edit_message(
            self.status_msg,
                f"🔍 检查画廊缓存...\n"
                f"📸 {len(images_list)} 张图片\n"
            f"📝 {video_info.get('title', '图集')[:50]}"
        )
        
        try:
                async with aiohttp.ClientSession() as session:
                    check_url = f"{worker_api.rstrip('/')}/api/check/{gallery_id}"
                    async with session.get(
                        check_url,
                        timeout=aiohttp.ClientTimeout(total=10)
                    ) as response:
                        if response.status == 200:
                            check_result = await response.json()
                        else:
                            check_result = {'exists': False}
            except Exception as e:
                LOGGER.warning(f"Failed to check gallery existence: {e}, assuming not exists")
                check_result = {'exists': False}
            
            # ========== 画廊已存在，直接返回 ==========
            if check_result.get('exists'):
                gallery_url = check_result.get('gallery_url', '')
                image_count = check_result.get('image_count', len(images_list))
                
                LOGGER.info(f"✅ Gallery cache hit! {gallery_id} -> {gallery_url}")
                
                # 检测是否包含GIF
                has_gif = self._contains_gif(images_list)
                
            buttons = ButtonMaker()
                buttons.url_button("🎨 在线画廊", gallery_url)
                
                # 如果包含GIF，不显示批量下载按钮
                if not has_gif:
                    buttons.data_button("📥 批量下载", f"manual_tg_upload_{self.status_msg.id}")
                
                # 构建消息
                msg_text = (
                    f"✅ <b>成功命中画廊</b>\n\n"
                    f"📸 共 {image_count} 张图片\n"
                    f"📹 {video_info.get('title', '图集')}\n\n"
                    f"💡 画廊有效期永久\n\n"
                    f"🌐 <b>在线画廊</b>：\n"
                    f"<code>{gallery_url}</code>\n"
                    f"💬 点击链接即可复制，分享给好友一起欣赏！"
                )
                
                # 如果包含GIF，添加提示
                if has_gif:
                    msg_text += (
                        f"\n\n"
                        f"⚠️ <b>包含GIF图片</b>\n"
                        f"💡 请使用在线画廊查看和下载"
            )
            
            await edit_message(
                self.status_msg,
                    msg_text,
                    buttons=buttons.build_menu(2) if not has_gif else buttons.build_menu(1)
                )
                
                # 保存状态供手动上传使用（包含画廊URL）
                await self._save_gallery_state_for_manual_upload(images_list, video_info, gallery_url)
                return
        else:
            LOGGER.info(f"Gallery cache is disabled, will create new gallery")
        
        # ========== 画廊不存在或缓存已禁用，走正常创建流程 ==========
        LOGGER.info(f"Creating new gallery: {gallery_id}")
        
        # ========== 检测是否为微信公众号来源 ==========
        platform = video_info.get('platform', '').lower()
        is_weixin = platform == 'weixin' or 'mp.weixin.qq.com' in original_url
        
        if is_weixin:
            # ========== 微信特殊处理：使用代理域名，不下载 ==========
            LOGGER.info("Detected WeChat source, using proxy domain (no download)")
            
            await edit_message(
                self.status_msg,
                f"🎨 正在处理微信图片...\n"
                f"📸 共 {len(images_list)} 张图片\n"
                f"💡 使用代理保持GIF动画\n"
                f"📝 {video_info.get('title', '图集')[:50]}"
            )
            
            # 提取并代理微信图片URL
            proxied_urls = self._proxy_weixin_images(images_list)
            
            if not proxied_urls:
                raise Exception("微信图片代理失败")
            
            LOGGER.info(f"Proxied {len(proxied_urls)} WeChat images")
            image_urls_for_worker = proxied_urls
            
        else:
            # ========== 非微信来源：走原流程（下载 → Catbox → Worker） ==========
            await edit_message(
                self.status_msg,
                f"📥 正在下载图集...\n"
                f"📸 共 {len(images_list)} 张图片\n"
                f"📝 {video_info.get('title', '图集')[:50]}"
            )
            
            try:
                # 第1步：下载图片到服务器
                downloaded_images = await self._download_images_for_gallery(images_list, video_info)
                
                if not downloaded_images:
                    raise Exception("未能下载任何图片")
                
                LOGGER.info(f"Downloaded {len(downloaded_images)} images successfully")
                
                # 第2步：上传到 Catbox 图床
                await edit_message(
                    self.status_msg,
                    f"📤 Catbox正在进食...\n"
                    f"📸 已下载 {len(downloaded_images)}/{len(images_list)} 张\n"
                    f"⏳ 请稍候..."
                )
                
                catbox_urls = await self._upload_to_catbox_image_host(downloaded_images)
                
                if not catbox_urls:
                    raise Exception("上传图床失败")
                
                LOGGER.info(f"Uploaded {len(catbox_urls)} images to Catbox")
                image_urls_for_worker = catbox_urls
                
            except Exception as e:
                LOGGER.error(f"Download/Upload error: {e}")
                raise
        
        # ========== 第3步：调用 Worker API 创建画廊（带确定性ID） ==========
        try:
            await edit_message(
                self.status_msg,
                f"🎨 正在创建画廊...\n"
                f"📸 共 {len(image_urls_for_worker)} 张图片\n"
                f"⚡ 即将完成..."
            )
            
            worker_response = await self._create_worker_gallery(gallery_id, image_urls_for_worker, video_info)
            
            if not worker_response.get('success'):
                error_msg = worker_response.get('message', '未知错误')
                
                # 如果是配额超限
                if worker_response.get('error') == 'QUOTA_EXCEEDED':
                    await edit_message(
                        self.status_msg,
                        f"⚠️ 今日画廊创建已达上限\n\n"
                        f"💡 请明天再试，或点击下方按钮手动上传到群组"
                    )
                    # 显示手动上传按钮
                    from bot.helper.telegram_helper.button_build import ButtonMaker
                    buttons = ButtonMaker()
                    buttons.data_button(
                "📥 批量下载", 
                        f"manual_tg_upload_{self.status_msg.id}"
                    )
                    await edit_message(self.status_msg, buttons=buttons.build_menu(1))
                    # 保存状态供手动上传使用
                    await self._save_gallery_state_for_manual_upload(images_list, video_info)
                    return
                
                raise Exception(f"Worker 画廊创建失败: {error_msg}")
            
            gallery_url = worker_response['gallery_url']
            elapsed = int(time() - start_time)
            
            LOGGER.info(f"Worker gallery created successfully in {elapsed}s: {gallery_url}")
            
            # 检测是否包含GIF
            has_gif = self._contains_gif(images_list)
            
            # 第4步：展示结果
            from bot.helper.telegram_helper.button_build import ButtonMaker
            buttons = ButtonMaker()
            buttons.url_button("🎨 在线画廊", gallery_url)
            
            # 如果包含GIF，不显示批量下载按钮
            if not has_gif:
                buttons.data_button(
                    "📥 批量下载", 
                    f"manual_tg_upload_{self.status_msg.id}"
                )
            
            summary_text = (
                f"✅ <b>图集已创建！</b>\n\n"
                f"📸 共 {len(image_urls_for_worker)} 张图片\n"
                f"📹 {video_info.get('title', '图集')}\n"
                f"👤 {video_info.get('author', '未知')}\n"
                f"⏱️ 耗时: {elapsed}秒\n\n"
                f"🌐 <b>在线画廊</b>：点击下方按钮查看\n"
                f"💡 国内外均可访问 · 有效期永久\n\n"
            )
            
            # 如果不包含GIF，添加批量下载提示
            if not has_gif:
                summary_text += f"📝 如需批量下载，点击右侧按钮\n\n"
            else:
                summary_text += (
                    f"⚠️ <b>包含GIF图片</b>\n"
                    f"💡 请使用在线画廊查看和下载\n\n"
                )
            
            summary_text += (
                f"🔗 <b>分享链接</b>：\n"
                f"<code>{gallery_url}</code>\n"
                f"💬 点击链接即可复制，分享给好友一起欣赏！"
            )
            
            await edit_message(
                self.status_msg,
                summary_text,
                buttons=buttons.build_menu(2) if not has_gif else buttons.build_menu(1)
            )
            
            # 保存状态供手动上传使用（包含画廊URL）
            await self._save_gallery_state_for_manual_upload(images_list, video_info, gallery_url)
            
            # 清理临时文件（仅非微信路径需要清理）
            if not is_weixin:
                await self._cleanup_downloaded_images(downloaded_images)
            
        except Exception as e:
            LOGGER.error(f"Gallery creation failed: {e}")
            import traceback
            LOGGER.error(traceback.format_exc())
            
            await edit_message(
                self.status_msg,
                f"❌ <b>画廊创建失败</b>\n\n"
                f"📝 错误: <code>{str(e)[:100]}</code>\n\n"
                f"💡 正在上传到群组..."
            )
            await sleep(1)
            
            # 降级：上传到 Telegram 群组
            await self._handle_gallery_telegram_mode(images_list, video_info)


    async def _handle_gallery_telegram_mode(self, images_list, video_info, gallery_url=None):
        """Telegram 直接上传模式（原有逻辑）
        
        Args:
            images_list: 图片URL列表
            video_info: 视频信息字典
            gallery_url: 在线画廊URL（可选，如果有的话会在完成消息中显示）
        """
        
        # 第一步：尝试直接用 URL 上传（零下载，极速）
        try:
            await self._upload_gallery_by_url(images_list, video_info, gallery_url)
            return  # 成功则直接返回
        except Exception as url_err:
            LOGGER.warning(f"URL direct upload failed: {url_err}, falling back to download mode")
            
            # 第二步：回退到下载模式
            # 创建临时下载目录
            temp_dir = f"{DOWNLOAD_DIR}{self.mid}_gallery"
            await makedirs(temp_dir, exist_ok=True)
            LOGGER.info(f"Created temp directory: {temp_dir}")

            downloaded_images = []
            import aiohttp
            import random
            
            # 准备下载任务（使用yt-dlp下载图片，更可靠）
            async def download_single_image(idx, image_data):
                """使用yt-dlp下载单张图片"""
                if self.is_cancelled:
                    return None

                image_url = image_data.get("url") if isinstance(image_data, dict) else image_data
                if not image_url:
                    LOGGER.warning(f"Image {idx + 1} has no URL, skipping")
                    return None
                    
                LOGGER.info(f"Processing image {idx + 1}/{len(images_list)}: {image_url[:60]}...")

                try:
                    final_path = ospath.join(temp_dir, f"image_{idx:03d}.jpg")
                    
                    # 使用yt-dlp下载图片
                    # yt-dlp命令行方式下载（已验证可以成功下载抖音图片）
                    import subprocess
                    
                    temp_output = ospath.join(temp_dir, f'temp_{idx:03d}')
                    
                    try:
                        # 使用yt-dlp命令行下载
                        cmd = [
                            'yt-dlp',
                            '--no-warnings',
                            '--quiet',
                            '-o', f'{temp_output}.%(ext)s',
                            image_url
                        ]
                        
                        LOGGER.info(f"Image {idx + 1}: Running yt-dlp download...")
                        result = await sync_to_async(
                            subprocess.run,
                            cmd,
                            capture_output=True,
                            text=True,
                            timeout=60  # 增加超时时间到60秒，配合并发限制
                        )
                        
                        if result.returncode != 0:
                            LOGGER.error(f"Image {idx + 1}: yt-dlp failed with code {result.returncode}: {result.stderr}")
                            return None
                        
                        # 查找下载的文件
                        import glob
                        downloaded_files = glob.glob(f'{temp_output}.*')
                        
                        if not downloaded_files:
                            LOGGER.error(f"Image {idx + 1}: yt-dlp download completed but no file found")
                            return None
                        
                        temp_file = downloaded_files[0]
                        LOGGER.info(f"Image {idx + 1} downloaded by yt-dlp: {ospath.basename(temp_file)}")
                        
                    except subprocess.TimeoutExpired:
                        LOGGER.error(f"Image {idx + 1}: yt-dlp download timeout")
                        return None
                    except Exception as yt_err:
                        LOGGER.error(f"Image {idx + 1}: yt-dlp download error: {yt_err}")
                        import traceback
                        LOGGER.error(f"yt-dlp error traceback: {traceback.format_exc()}")
                        return None
                        
                    # 转换图片为JPG
                    try:
                        def convert_image():
                            from PIL import Image
                            img = Image.open(temp_file)
                            if img.mode in ('RGBA', 'LA', 'P'):
                                background = Image.new('RGB', img.size, (255, 255, 255))
                                if img.mode == 'P':
                                    img = img.convert('RGBA')
                                background.paste(img, mask=img.split()[-1] if img.mode == 'RGBA' else None)
                                img = background
                            elif img.mode != 'RGB':
                                img = img.convert('RGB')
                            img.save(final_path, 'JPEG', quality=95)
                        
                        await sync_to_async(convert_image)
                        
                        try:
                            await aioremove(temp_file)
                        except:
                            pass
                        
                        LOGGER.info(f"Downloaded and converted image {idx + 1}/{len(images_list)}: {final_path}")
                        return (idx, final_path)
                        
                    except Exception as conv_error:
                        LOGGER.error(f"Image conversion error for {idx + 1}: {conv_error}")
                        try:
                            await aioremove(temp_file)
                        except:
                            pass
                        return None

                except Exception as e:
                    LOGGER.error(f"Error downloading image {idx + 1}: {e}")
                    import traceback
                    LOGGER.error(f"Image download traceback: {traceback.format_exc()}")
                    return None
            
            # 更新状态
            await edit_message(
                self.status_msg,
                f"📥 正在下载 {len(images_list)} 张图片...\n"
                f"📹 {video_info.get('title', '图集')}",
            )
            
            # 从第一张图片开始下载的时间点
            start_ts = time()

            # 使用信号量控制并发数，避免CDN限流
            import asyncio
            max_concurrent = 5  # 最多同时下载5张图片
            semaphore = asyncio.Semaphore(max_concurrent)
            
            async def download_with_semaphore(idx, img_data):
                """带信号量控制的下载函数"""
                async with semaphore:
                    return await download_single_image(idx, img_data)
            
            download_tasks = [download_with_semaphore(idx, img_data) for idx, img_data in enumerate(images_list)]
            results = await asyncio.gather(*download_tasks, return_exceptions=False)
            
            # 过滤成功的下载，并按索引排序
            successful_downloads = [r for r in results if r is not None]
            successful_downloads.sort(key=lambda x: x[0])  # 按原始索引排序
            downloaded_images = [path for _, path in successful_downloads]
            
            LOGGER.info(f"Successfully downloaded {len(downloaded_images)}/{len(images_list)} images")
            
            # 记录失败的图片索引和URL特征
            if len(downloaded_images) < len(images_list):
                successful_indices = {idx for idx, _ in successful_downloads}
                failed_indices = [i for i in range(len(images_list)) if i not in successful_indices]
                for fail_idx in failed_indices:
                    fail_url = images_list[fail_idx].get('url') if isinstance(images_list[fail_idx], dict) else images_list[fail_idx]
                    cdn_node = fail_url.split('/')[2] if fail_url and '/' in fail_url else 'unknown'
                    LOGGER.warning(f"Failed image {fail_idx + 1}: CDN={cdn_node}, URL_length={len(fail_url) if fail_url else 0}")
            
            if not downloaded_images:
                raise Exception("未能下载任何图片")

            # 删除进度消息
            await delete_message(self.status_msg)
            self.status_msg = None

            # 目的地：受配置控制
            if Config.GALLERY_UPLOAD_TO_DUMP and Config.LEECH_DUMP_CHAT:
                dest = Config.LEECH_DUMP_CHAT
                if isinstance(dest, str) and dest.strip().lstrip("-").isdigit():
                    upload_dest = int(dest)
                else:
                    upload_dest = dest
            else:
                upload_dest = self.message.chat.id

            # 分批上传（串行，每批最多10张，固定延迟避免FloodWait）
            total_imgs = len(images_list)
            total_batches = (len(downloaded_images) + 9) // 10
            # 上传期间的进度提示
            upload_status_msg = await send_message(
                self.message,
                f"⬆️ 正在上传图集… 0/{total_imgs} (0/{total_batches} 组) 📥 下载模式"
            )
            
            base_caption = self._build_caption(video_info)
            total_sent = 0
            album_links = []
            batch_index = 0
            
            # 获取用户信息
            user_name = self.message.from_user.first_name or "未知用户"
            user_mention = f"<a href='https://t.me/nebuluxe_flash_bot'>{user_name}</a>"
            
            # 串行上传每个批次
            for start in range(0, len(downloaded_images), 10):
                batch_paths = downloaded_images[start:start + 10]
                media_group = []
                
                # 为每个相册添加标注
                album_number = batch_index + 1
                album_caption = f"{base_caption}\n\n📌 来自 {user_mention} 的相册 {album_number}/{total_batches}"
                
                # 构建媒体组
                for idx, img_path in enumerate(batch_paths):
                    if idx == 0:  # 每个相册的第一张图片带 caption
                        media_group.append(InputMediaPhoto(media=img_path, caption=album_caption))
                    else:
                        media_group.append(InputMediaPhoto(media=img_path))
                
                LOGGER.info(f"Uploading media group batch {batch_index + 1}/{total_batches} ({len(media_group)} images)")
                
                # 上传批次（带重试机制）
                attempt = 0
                max_flood_wait = 60
                while True:
                    try:
                        msgs = await self.client.send_media_group(
                            chat_id=upload_dest, media=media_group, reply_to_message_id=None
                        )
                        break
                    except FloodWait as f:
                        wait_s = int(f.value) + 1
                        if wait_s > max_flood_wait:
                            LOGGER.error(f"FloodWait too long ({wait_s}s), aborting")
                            raise
                        LOGGER.warning(f"⏳ FloodWait {wait_s}s for batch {batch_index + 1}/{total_batches}")
                        # 更新进度提示：显示等待状态
                        try:
                            await edit_message(
                                upload_status_msg,
                                f"⏳ 人数过多需排队，请耐心等待 {wait_s}秒… {total_sent}/{total_imgs} ({batch_index}/{total_batches} 组)"
                            )
                        except Exception:
                            pass
                        await sleep(wait_s)
                        attempt += 1
                        if attempt >= 3:
                            LOGGER.error(f"Max FloodWait retries reached")
                            raise
                
                # 记录结果
                total_sent += len(msgs)
                if msgs and hasattr(msgs[0], "link"):
                    album_links.append(msgs[0].link)
                batch_index += 1
                
                # 更新进度
                try:
                    await edit_message(
                        upload_status_msg,
                        f"⬆️ 正在上传图集… {total_sent}/{total_imgs} ({batch_index}/{total_batches} 组) 📥"
                    )
                except Exception:
                    pass
                
                # 批次间延迟（安全阈值，避免触发FloodWait）
                if batch_index < total_batches:  # 最后一批不需要延迟
                    await sleep(2)

            LOGGER.info(f"Media gallery uploaded in {batch_index} batch(es), total sent: {total_sent}")

            # 单条完成提示：总成功数量 + 相册链接列表
            success_rate = f"{total_sent}/{len(images_list)}"
            # 计算从“开始下载第一张图片”到“所有相册上传完毕”的总耗时
            elapsed_seconds = int(time() - start_ts)
            def _format_duration(seconds):
                seconds = int(seconds)
                minutes, secs = divmod(seconds, 60)
                hours, mins = divmod(minutes, 60)
                if hours:
                    return f"{hours}小时{mins}分{secs}秒"
                if mins:
                    return f"{mins}分{secs}秒"
                return f"{secs}秒"
            text = (
                f"✅ <b>图集上传完成</b>  📸 {success_rate}\n\n"
                f"{video_info.get('title', '图集')}\n\n"
                f"👤 {video_info.get('author', '未知作者')}\n"
                f"⏱️ 耗时: {_format_duration(elapsed_seconds)}"
            )
            if total_sent < len(images_list):
                failed_count = len(images_list) - total_sent
                text += f"\n⚠️ <i>有 {failed_count} 张图片未成功下载</i>"
            if album_links:
                if len(album_links) == 1:
                    text += f"\n🔗 <a href='{album_links[0]}'>查看相册</a>"
                else:
                    links_str = "\n".join(
                        [f"🔗 <a href='{lnk}'>相册 {i+1}</a>" for i, lnk in enumerate(album_links)]
                    )
                    text += f"\n{links_str}"
            await send_message(self.message, text)

            # 清理临时文件
            await self._cleanup_temp_files(temp_dir)

            # 删除上传进度提示
            try:
                await delete_message(upload_status_msg)
            except Exception:
                pass

        except Exception as e:
            error_msg = str(e)
            LOGGER.error(f"Image gallery upload failed: {error_msg}")

            if self.status_msg:
                await edit_message(
                    self.status_msg,
                    f"❌ <b>图集上传失败</b>\n\n" f"📝 错误: <code>{error_msg}</code>",
                )
            else:
                await send_message(
                    self.message,
                    f"❌ <b>图集上传失败</b>\n\n" f"📝 错误: <code>{error_msg}</code>",
                )

            # 清理临时文件
            if await aiopath.exists(temp_dir):
                await self._cleanup_temp_files(temp_dir)

    def _build_caption(self, video_info):
        """构建图集caption"""
        lines = []

        # 添加前缀（如果配置了）
        if Config.LEECH_FILENAME_PREFIX:
            lines.append(Config.LEECH_FILENAME_PREFIX.strip())
            lines.append("")

        # 标题
        title = video_info.get("title", "").strip()
        if title:
            lines.append(f"📹 <b>{title}</b>")

        # 作者
        author = video_info.get("author", "").strip()
        if author:
            lines.append(f"👤 {author}")

        return "\n".join(lines) if lines else "图集"

    # ========================================================================
    # ⚠️ DEPRECATED: 以下为旧的 Telegraph 直接秒传实现，已被 Worker 画廊替代
    # ========================================================================
    # 旧方案问题：
    # 1. Telegraph 直接引用原始图片URL，会因为防盗链、Referer限制等原因导致图片裂开
    # 2. Telegraph 在中国大陆无法访问
    # 
    # 新方案：Worker 画廊 (Catbox图床 + Cloudflare Pages)
    # 1. 下载图片到服务器
    # 2. 上传到 Catbox.moe 免费图床（永久、无限制）
    # 3. 通过 Cloudflare Worker 创建画廊页面（国内外均可访问）
    # 
    # 以下代码保留仅供参考，实际运行中不会被调用
    # ========================================================================

    async def _create_telegraph_gallery(self, images_list, video_info):
        """
        [DEPRECATED] 创建 Telegraph 画廊（旧实现，已废弃）
        
        此函数已不再使用，请使用 _handle_gallery_telegraph_mode() 代替
        """
        from telegraph import Telegraph
        
        try:
            # 创建匿名账号
            telegraph = Telegraph()
            telegraph.create_account(short_name='GalleryBot')
            
            # 构建 HTML 内容
            html_content = self._build_gallery_html(images_list, video_info)
            
            # 创建页面
            title = self._sanitize_filename(video_info.get('title', '图集'))[:50]
            # 兼容 author 为字符串或字典的情况
            author_data = video_info.get('author', 'Unknown')
            if isinstance(author_data, dict):
                author_name = author_data.get('name', 'Unknown')
            else:
                author_name = str(author_data) if author_data else 'Unknown'
            
            response = await sync_to_async(
                telegraph.create_page,
                title=title,
                html_content=html_content,
                author_name=author_name
            )
            
            return response['url']
            
        except Exception as e:
            LOGGER.error(f"Telegraph gallery creation error: {e}")
            raise


    def _build_gallery_html(self, images_list, video_info):
        """
        [DEPRECATED] 构建 Telegraph 画廊 HTML（旧实现，已废弃）
        
        此函数已不再使用，新方案使用 Cloudflare Worker 渲染画廊页面
        """
        
        title = video_info.get('title', '图集')
        # 兼容 author 为字符串或字典的情况
        author_data = video_info.get('author', 'Unknown')
        if isinstance(author_data, dict):
            author = author_data.get('name', 'Unknown')
        else:
            author = str(author_data) if author_data else 'Unknown'
        
        # HTML 头部（Telegraph 不允许 div/table 等布局标签，仅单列展示）
        html = f'''
        <h3>{title}</h3>
        <p>👤 作者: {author}</p>
        <p>📸 共 {len(images_list)} 张图片</p>
        <hr>
        '''
        
        # 添加图片
        for idx, img_data in enumerate(images_list, 1):
            img_url = img_data.get('url') if isinstance(img_data, dict) else img_data
            
            html += f'''
            <figure>
                <img src="{img_url}" loading="lazy" decoding="async" referrerpolicy="no-referrer" />
                <figcaption>图片 {idx}/{len(images_list)}</figcaption>
            </figure>
            '''

        return html


    def _format_gallery_summary(self, images_list, video_info, elapsed, mode="telegraph"):
        """
        [DEPRECATED] 格式化图集汇总消息（旧实现，已废弃）
        
        此函数已不再使用，新方案直接在 _handle_gallery_telegraph_mode 中构造消息
        """
        
        title = video_info.get('title', '图集')
        # 兼容 author 为字符串或字典的情况
        author_data = video_info.get('author', '')
        if isinstance(author_data, dict):
            author = author_data.get('name', '')
        else:
            author = str(author_data) if author_data else ''
        
        if mode == "telegraph":
            return (
                f"✅ 图集已秒传完成！\n\n"
                f"📸 共 {len(images_list)} 张图片\n"
                f"📝 {title}\n"
                f"👤 {author}\n"
                f"⏱️ 耗时: {elapsed}秒\n\n"
                f"━━━━━━━━━━━━━━━━"
            )
        else:
            # 原有 Telegram 模式格式
            return (
                f"✅ 图集上传完成\n\n"
                f"📸 {len(images_list)} 张图片\n"
                f"📝 {title}\n"
                f"👤 {author}\n"
                f"⏱️ 耗时: {elapsed}秒"
            )


    # 内存缓存用于临时存储画廊状态
    _gallery_cache = {}

    async def _save_gallery_state(self, images_list, video_info, gallery_url, freeze_until=None):
        """保存图集状态供批量下载使用"""
        msg_id = self.status_msg.id
        VideoLinkProcessor._gallery_cache[msg_id] = {
            'images_list': images_list,
            'video_info': video_info,
            'gallery_url': gallery_url,
            'timestamp': time(),
            'freeze_until': freeze_until,
            'user_id': self.message.from_user.id,
            'chat_id': self.message.chat.id
        }
        LOGGER.info(f"Saved gallery state for message {msg_id}")


    @classmethod
    async def load_gallery_state(cls, msg_id):
        """加载图集状态"""
        return cls._gallery_cache.get(msg_id)


    @classmethod
    async def delete_gallery_state(cls, msg_id):
        """删除图集状态"""
        if msg_id in cls._gallery_cache:
            del cls._gallery_cache[msg_id]
            LOGGER.info(f"Deleted gallery state for message {msg_id}")


    def _sanitize_filename(self, filename):
        """清理文件名，移除非法字符并智能截取"""
        import re

        if not filename:
            return "video"
        
        original = str(filename)
        
        # 移除或替换非法字符
        filename = re.sub(r'[<>:"/\\|?*]', "", original)
        
        # 如果原始文本包含换行符，说明是多行文案
        if '\n' in original or len(filename) > 80:
            lines = original.split('\n')
            lines = [line.strip() for line in lines if line.strip()]
            
            if len(lines) > 3:
                # 多行文案：保留前两行 + 最后一行（通常是标签）
                parts = []
                # 前两行
                for i in range(min(2, len(lines))):
                    parts.append(lines[i][:30])  # 每行最多30字符
                # 最后一行（标签）
                if len(lines) > 2:
                    last_line = lines[-1]
                    # 如果最后一行是标签（包含#），保留
                    if '#' in last_line:
                        parts.append(last_line[:40])
                
                filename = ' '.join(parts)
            else:
                # 少于3行，直接合并
                filename = ' '.join(lines)
        
        # 移除换行符和多余空白
        filename = filename.replace('\n', ' ').replace('\r', ' ')
        # 合并多个空格为一个
        filename = re.sub(r'\s+', ' ', filename)
        # 再次移除非法字符
        filename = re.sub(r'[<>:"/\\|?*]', "", filename)
        
        # 最终长度限制（50字符，约150字节）
        # Linux 文件名最大 255 字节，UTF-8 中文占 3 字节
        max_length = 50
        if len(filename) > max_length:
            filename = filename[:max_length].rstrip()
        
        result = filename.strip()
        return result if result else "video"

    async def _download_images_for_gallery(self, images_list, video_info):
        """下载图片到服务器（复用原有逻辑）"""
        temp_dir = f"{DOWNLOAD_DIR}{self.mid}_gallery"
        await makedirs(temp_dir, exist_ok=True)
        LOGGER.info(f"Created temp directory: {temp_dir}")

        downloaded_images = []
        import subprocess
        
        # 使用信号量控制并发数
        import asyncio
        max_concurrent = 5
        semaphore = asyncio.Semaphore(max_concurrent)
        
        async def download_single_image(idx, image_data):
            """使用yt-dlp下载单张图片"""
            if self.is_cancelled:
                return None

            image_url = image_data.get("url") if isinstance(image_data, dict) else image_data
            if not image_url:
                return None
                
            async with semaphore:
                try:
                    temp_output = ospath.join(temp_dir, f'temp_{idx:03d}')
                    
                    cmd = [
                        'yt-dlp',
                        '--no-warnings',
                        '--quiet',
                        '-o', f'{temp_output}.%(ext)s',
                        image_url
                    ]
                    
                    result = await sync_to_async(
                        subprocess.run,
                        cmd,
                        capture_output=True,
                        text=True,
                        timeout=60
                    )
                    
                    if result.returncode != 0:
                        LOGGER.error(f"Image {idx + 1}: yt-dlp failed")
                        return None
                    
                    import glob
                    downloaded_files = glob.glob(f'{temp_output}.*')
                    
                    if not downloaded_files:
                        return None
                    
                    temp_file = downloaded_files[0]
                    
                    # 检测文件扩展名
                    file_ext = ospath.splitext(temp_file)[1].lower()
                    
                    # 如果是GIF，保持GIF格式不转换
                    if file_ext == '.gif':
                        final_path = ospath.join(temp_dir, f"image_{idx:03d}.gif")
                        # 直接移动文件，不转换
                        def keep_gif():
                            import shutil
                            shutil.move(temp_file, final_path)
                        
                        await sync_to_async(keep_gif)
                        LOGGER.info(f"Image {idx + 1}: Kept as GIF (animated)")
                    else:
                        # 非GIF图片转换为JPG
                        final_path = ospath.join(temp_dir, f"image_{idx:03d}.jpg")
                        
                        def convert_image():
                            from PIL import Image
                            img = Image.open(temp_file)
                            if img.mode in ('RGBA', 'LA', 'P'):
                                background = Image.new('RGB', img.size, (255, 255, 255))
                                if img.mode == 'P':
                                    img = img.convert('RGBA')
                                background.paste(img, mask=img.split()[-1] if img.mode == 'RGBA' else None)
                                img = background
                            elif img.mode != 'RGB':
                                img = img.convert('RGB')
                            img.save(final_path, 'JPEG', quality=95)
                        
                        await sync_to_async(convert_image)
                    
                    try:
                        await aioremove(temp_file)
                    except:
                        pass
                    
                    LOGGER.info(f"Downloaded image {idx + 1}/{len(images_list)}")
                    return final_path
                    
                except Exception as e:
                    LOGGER.error(f"Error downloading image {idx + 1}: {e}")
                    return None
        
        download_tasks = [download_single_image(idx, img_data) for idx, img_data in enumerate(images_list)]
        results = await asyncio.gather(*download_tasks, return_exceptions=False)
        
        downloaded_images = [r for r in results if r is not None]
        
        LOGGER.info(f"Successfully downloaded {len(downloaded_images)}/{len(images_list)} images")
        
        return downloaded_images

    async def _upload_to_catbox_image_host(self, image_paths):
        """上传图片到 Catbox 图床（免费、永久、无限制）"""
        import aiohttp
        
        catbox_urls = []
        
        for idx, img_path in enumerate(image_paths):
            try:
                # 检测文件扩展名和类型
                file_ext = ospath.splitext(img_path)[1].lower()
                
                # 根据扩展名设置文件名和MIME类型
                if file_ext == '.gif':
                    filename = 'image.gif'
                    content_type = 'image/gif'
                else:
                    filename = 'image.jpg'
                    content_type = 'image/jpeg'
                
                # 读取图片数据
                async with aioopen(img_path, 'rb') as f:
                    img_data = await f.read()
                
                # Catbox.moe 上传 API
                async with aiohttp.ClientSession() as session:
                    form_data = aiohttp.FormData()
                    form_data.add_field('reqtype', 'fileupload')
                    form_data.add_field('userhash', '')  # 匿名上传
                    form_data.add_field(
                        'fileToUpload',
                        img_data,
                        filename=filename,
                        content_type=content_type
                    )
                    
                    try:
                        async with session.post(
                            'https://catbox.moe/user/api.php',
                            data=form_data,
                            timeout=aiohttp.ClientTimeout(total=60)
                        ) as resp:
                            if resp.status == 200:
                                catbox_url = await resp.text()
                                catbox_url = catbox_url.strip()
                                
                                # 验证返回的是有效URL
                                if catbox_url.startswith('https://files.catbox.moe/'):
                                    catbox_urls.append(catbox_url)
                                    LOGGER.info(f"Uploaded image {idx + 1}/{len(image_paths)} to Catbox: {catbox_url}")
                                else:
                                    LOGGER.error(f"Catbox returned invalid URL for image {idx + 1}: {catbox_url[:100]}")
                            else:
                                resp_text = await resp.text()
                                LOGGER.error(f"Catbox upload failed for image {idx + 1}: HTTP {resp.status}, Response: {resp_text[:200]}")
                    except Exception as upload_error:
                        LOGGER.error(f"Catbox upload request error for image {idx + 1}: {upload_error}")
                
                # 避免限流，延迟一下
                await sleep(0.5)
                
            except Exception as e:
                LOGGER.error(f"Error processing image {idx + 1} for Catbox: {e}")
                import traceback
                LOGGER.error(traceback.format_exc())
                continue
        
        LOGGER.info(f"Successfully uploaded {len(catbox_urls)}/{len(image_paths)} images to Catbox")
        
        return catbox_urls

    def _generate_gallery_id(self, original_url: str) -> str:
        """从URL生成确定性的画廊ID（12位MD5哈希）"""
        import hashlib
        return hashlib.md5(original_url.encode()).hexdigest()[:12]
    
    def _proxy_weixin_images(self, images_list: list) -> list:
        """
        微信图片URL代理处理
        
        将 mmbiz.qpic.cn 替换为 mmbiz.qpic.cn.in（公共代理）
        保持GIF动态效果和透明背景
        
        Args:
            images_list: 图片URL列表
            
        Returns:
            list: 代理后的URL列表
        """
        proxied_urls = []
        
        for img_data in images_list:
            # 提取URL
            if isinstance(img_data, dict):
                img_url = img_data.get('url', '')
            else:
                img_url = str(img_data)
            
            if not img_url:
                continue
            
            # 替换微信CDN域名为代理域名
            if 'mmbiz.qpic.cn' in img_url:
                proxied_url = img_url.replace('mmbiz.qpic.cn', 'mmbiz.qpic.cn.in')
                proxied_urls.append(proxied_url)
                LOGGER.info(f"Proxied WeChat image: {img_url[:60]}... -> ...{proxied_url[-40:]}")
            else:
                # 非微信CDN图片，保持原样
                proxied_urls.append(img_url)
                LOGGER.warning(f"Non-WeChat CDN image in WeChat article: {img_url[:60]}")
        
        return proxied_urls
    
    def _contains_gif(self, images_list: list) -> bool:
        """
        检测图片列表中是否包含GIF
        
        支持多种GIF格式检测：
        - 标准扩展名：.gif
        - URL路径：/gif/
        - 微信公众号：mmbiz_gif, wx_fmt=gif
        - 其他平台：fmt=gif, type=gif
        
        Args:
            images_list: 图片URL列表
            
        Returns:
            True: 包含GIF
            False: 不包含GIF
        """
        if not images_list:
            return False
        
        gif_count = 0
        for img_data in images_list:
            # 提取URL
            if isinstance(img_data, dict):
                img_url = img_data.get('url', '')
            else:
                img_url = str(img_data)
            
            img_url_lower = img_url.lower()
            
            # 检测GIF的多种模式
            is_gif = False
            
            # 1. 标准扩展名
            if img_url_lower.endswith('.gif') or '.gif?' in img_url_lower:
                is_gif = True
            
            # 2. URL路径包含gif
            elif '/gif/' in img_url_lower or '_gif/' in img_url_lower or '/gifs/' in img_url_lower:
                is_gif = True
            
            # 3. 微信公众号特殊格式
            elif 'mmbiz_gif' in img_url_lower:  # 微信：mmbiz.qpic.cn/mmbiz_gif/...
                is_gif = True
            elif 'wx_fmt=gif' in img_url_lower:  # 微信：?wx_fmt=gif
                is_gif = True
            
            # 4. 其他平台的参数格式
            elif 'fmt=gif' in img_url_lower or 'format=gif' in img_url_lower:
                is_gif = True
            elif 'type=gif' in img_url_lower or 'filetype=gif' in img_url_lower:
                is_gif = True
            
            if is_gif:
                gif_count += 1
                LOGGER.info(f"Detected GIF in gallery: {img_url[:100]}")
        
        if gif_count > 0:
            LOGGER.info(f"Gallery contains {gif_count} GIF(s), will disable batch download")
            return True
        
        return False

    async def _create_worker_gallery(self, gallery_id: str, image_urls, video_info):
        """调用 Worker API 创建画廊（带确定性ID）"""
        import aiohttp
        from bot.core.config_manager import Config
        
        worker_api = getattr(Config, 'WORKER_GALLERY_API', '')
        
        if not worker_api:
            raise Exception("WORKER_GALLERY_API 未配置")
        
        api_url = f"{worker_api.rstrip('/')}/api/create-gallery"
        
        payload = {
            'gallery_id': gallery_id,  # 指定画廊ID
            'title': video_info.get('title', '图集'),
            'author': video_info.get('author', '未知'),
            'images': image_urls
        }
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    api_url,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=30)
                ) as response:
                    result = await response.json()
                    return result
        except Exception as e:
            LOGGER.error(f"Worker API call failed: {e}")
            return {
                'success': False,
                'error': 'API_ERROR',
                'message': str(e)
            }

    # 状态缓存（用于手动上传）
    _manual_upload_cache = {}

    async def _save_gallery_state_for_manual_upload(self, images_list, video_info, gallery_url=None):
        """保存图集状态供手动上传使用"""
        msg_id = self.status_msg.id
        VideoLinkProcessor._manual_upload_cache[msg_id] = {
            'images_list': images_list,
            'video_info': video_info,
            'gallery_url': gallery_url,  # 保存画廊URL
            'timestamp': time(),
            'user_id': self.message.from_user.id,
            'chat_id': self.message.chat.id
        }
        LOGGER.info(f"Saved manual upload state for message {msg_id} (gallery_url: {gallery_url})")

    @classmethod
    async def load_manual_upload_state(cls, msg_id):
        """加载手动上传状态"""
        return cls._manual_upload_cache.get(msg_id)

    @classmethod
    async def delete_manual_upload_state(cls, msg_id):
        """删除手动上传状态"""
        if msg_id in cls._manual_upload_cache:
            del cls._manual_upload_cache[msg_id]
            LOGGER.info(f"Deleted manual upload state for message {msg_id}")

    async def _cleanup_downloaded_images(self, image_paths):
        """清理下载的图片文件"""
        if not image_paths:
            return
        
        try:
            # 获取目录路径
            first_path = image_paths[0]
            temp_dir = ospath.dirname(first_path)
            
            if await aiopath.exists(temp_dir):
                await clean_target(temp_dir)
                LOGGER.info(f"Cleaned up temp directory: {temp_dir}")
        except Exception as e:
            LOGGER.error(f"Error cleaning up downloaded images: {e}")

    async def _cleanup_temp_files(self, directory):
        """清理临时文件"""
        try:
            await clean_target(directory)
            LOGGER.info(f"Cleaned up temp directory: {directory}")
        except Exception as e:
            LOGGER.error(f"Error cleaning up temp directory: {e}")


# ============ 批量下载回调处理 ============

@new_task
async def handle_batch_download_callback(client, query):
    """处理批量下载按钮回调"""
    
    try:
        # 解析回调数据: batch_dl_{msg_id}_{img_count}
        callback_data = query.data
        parts = callback_data.split('_')
        
        if len(parts) < 3:
            await query.answer("❌ 无效的回调数据", show_alert=True)
            return
        
        msg_id = int(parts[2])
        img_count = int(parts[3]) if len(parts) > 3 else 0
        
        # 加载图集状态
        state = await VideoLinkProcessor.load_gallery_state(msg_id)
        
        if not state:
            await query.answer("❌ 图集状态已过期，请重新解析链接", show_alert=True)
            return
        
        # 轻量提示（不弹窗），并在原消息内展示确认按钮
        await query.answer("请确认是否继续批量下载")

        # 创建确认按钮（编辑原消息，不再新发一条消息，避免混乱）
        from bot.helper.telegram_helper.button_build import ButtonMaker
        buttons = ButtonMaker()
        buttons.data_button("✅ 确定下载", f"confirm_batch_{msg_id}")
        buttons.data_button("❌ 取消", f"cancel_batch_{msg_id}")
        
        # 编辑原状态消息，展示确认按钮
        from bot.helper.telegram_helper.message_utils import edit_message
        await edit_message(
            query.message,
            (
                f"⚠️ 批量下载提示\n\n"
                f"批量下载 {img_count} 张图片到群组\n"
                f"大约需要 1-2 分钟\n\n"
                f"是否继续？"
            ),
            buttons=buttons.build_menu(2)
        )
        
    except Exception as e:
        LOGGER.error(f"Batch download callback error: {e}")
        import traceback
        LOGGER.error(traceback.format_exc())
        await query.answer("❌ 处理失败，请重试", show_alert=True)


@new_task
async def handle_confirm_batch_download(client, query):
    """确认批量下载"""
    
    try:
        # 解析回调数据: confirm_batch_{msg_id}
        callback_data = query.data
        parts = callback_data.split('_')
        
        if len(parts) < 3:
            await query.answer("❌ 无效的回调数据", show_alert=True)
            return
        
        msg_id = int(parts[2])
        
        # 加载图集状态
        state = await VideoLinkProcessor.load_gallery_state(msg_id)
        
        if not state:
            await query.answer("❌ 图集状态已过期", show_alert=True)
            return

        # 先尽快回应一次，避免 QueryIdInvalid（后续不再调用 answer）
        try:
            await query.answer("已开始批量下载…", show_alert=False)
        except Exception:
            pass
        
        # 更新原消息：提示已开始下载，禁用再次下载
        images_list = state['images_list']
        video_info = state['video_info']
        gallery_url = state['gallery_url']

        from bot.helper.telegram_helper.button_build import ButtonMaker
        from bot.helper.telegram_helper.message_utils import edit_message
        buttons_info = ButtonMaker()
        # 若仍在冻结期，继续显示灰色“立即欣赏(剩余s)”；否则启用
        now_ts = time()
        freeze_until = state.get('freeze_until') or 0
        if now_ts < freeze_until:
            remaining = int(max(0, freeze_until - now_ts))
            buttons_info.data_button(f"⏳ 立即欣赏({remaining}s)", "noop")
            tip_text = "\n\n💡 提示：为确保更完美呈现，请等待 30 秒后再点击“立即欣赏”。"
        else:
            buttons_info.url_button("🎨 立即欣赏", gallery_url)
            tip_text = ""
        buttons_info.data_button("⏳ 下载中…", "noop")
        await edit_message(
            query.message,
            (
                "📤 已开始批量下载到群组…\n\n"
                f"📸 共 {len(images_list)} 张，稍候在下方查看进度" + tip_text
            ),
            buttons=buttons_info.build_menu(2)
        )

        # 创建新的进度消息
        progress_msg = await query.message.reply(
            f"📤 正在批量下载到群组...\n\n"
            f"📸 进度: 0/{len(images_list)}"
        )
        
        # 创建临时处理器执行下载
        temp_processor = VideoLinkProcessor(
            client, 
            query.message.reply_to_message,
            ""  # 不需要 URL
        )
        temp_processor.status_msg = progress_msg
        
        # 执行 Telegram 上传模式
        await temp_processor._handle_gallery_telegram_mode(images_list, video_info)
        
        # 修改原消息：标记已下载，防止重复
        buttons_done = ButtonMaker()
        buttons_done.url_button("🎨 立即欣赏", gallery_url)
        buttons_done.data_button("✅ 已下载", "noop")
        await edit_message(
            query.message,
            (
                "✅ 批量下载完成！\n\n"
                f"📸 共上传 {len(images_list)} 张"
            ),
            buttons=buttons_done.build_menu(2)
        )
        
        # 清理状态
        await VideoLinkProcessor.delete_gallery_state(msg_id)
        
    except Exception as e:
        LOGGER.error(f"Confirm batch download error: {e}")
        import traceback
        LOGGER.error(traceback.format_exc())
        # 失败通过消息提示，不再调用 answer，避免 QueryIdInvalid
        try:
            from bot.helper.telegram_helper.message_utils import edit_message
            await edit_message(query.message, "❌ 下载失败，请稍后重试")
        except Exception:
            pass


@new_task
async def handle_cancel_batch_download(client, query):
    """取消批量下载"""
    
    try:
        # 先尽快回应一次，避免 QueryIdInvalid
        try:
            await query.answer("已取消", show_alert=False)
        except Exception:
            pass

        # 解析回调数据: cancel_batch_{msg_id}
        data = query.data
        parts = data.split('_')
        if len(parts) < 3:
            # 若无效，仅提示在消息里
            from bot.helper.telegram_helper.message_utils import edit_message
            await edit_message(query.message, "❌ 无效的回调数据")
            return

        msg_id = int(parts[2])

        # 取回图集状态
        state = await VideoLinkProcessor.load_gallery_state(msg_id)

        # 默认提示
        tip_text = "已取消批量下载"

        from bot.helper.telegram_helper.button_build import ButtonMaker
        from bot.helper.telegram_helper.message_utils import edit_message

        if state:
            images_list = state.get('images_list', [])
            video_info = state.get('video_info', {})
            gallery_url = state.get('gallery_url')

            # 判断是否仍在冻结期内
            now_ts = time()
            freeze_until = state.get('freeze_until') or 0
            still_freezing = now_ts < freeze_until

            # 恢复按钮：冻结期内继续灰按钮；过期后启用
            buttons = ButtonMaker()
            if gallery_url:
                if still_freezing:
                    remaining = int(max(0, freeze_until - now_ts))
                    buttons.data_button(f"⏳ 立即欣赏({remaining}s)", "noop")
                else:
                    buttons.url_button("🎨 立即欣赏", gallery_url)
            buttons.data_button(
                "📥 批量下载",
                f"batch_dl_{msg_id}_{len(images_list) if images_list else 0}"
            )

            # 恢复摘要文本并附加已取消提示（并在冻结期内保留提示）
            title = (video_info.get('title')
                     if isinstance(video_info, dict) else str(video_info) if video_info else '图集')
            # 兼容 author
            author_data = video_info.get('author', '') if isinstance(video_info, dict) else ''
            if isinstance(author_data, dict):
                author = author_data.get('name', '')
            else:
                author = str(author_data) if author_data else ''

            base_summary = (
                f"✅ 图集已秒传完成！\n\n"
                f"📸 共 {len(images_list) if images_list else 0} 张图片\n"
                f"📝 {title}\n"
                f"👤 {author}"
            )
            if still_freezing:
                tip = "\n\n💡 提示：为确保更完美呈现，请等待 30 秒后再点击“立即欣赏”。"
                summary = base_summary + tip + f"\n\n━━━━━━━━━━━━━━━━\n⚠️ {tip_text}"
            else:
                summary = base_summary + f"\n\n━━━━━━━━━━━━━━━━\n⚠️ {tip_text}"

            await edit_message(query.message, summary, buttons=buttons.build_menu(2))
        else:
            # 状态丢失则仅提示
            await edit_message(query.message, f"⚠️ {tip_text}")

        # 已在开头答复，这里不再调用 answer
        
    except Exception as e:
        LOGGER.error(f"Cancel batch download error: {e}")
        try:
            from bot.helper.telegram_helper.message_utils import edit_message
            await edit_message(query.message, "❌ 操作失败")
        except Exception:
            pass


@new_task
async def noop_callback(_, query):
    """吞掉无操作回调，立即消除"加载中…"提示"""
    try:
        await query.answer()
    except Exception:
        pass


@new_task
async def handle_manual_tg_upload(client, query):
    """处理手动上传到TG群组按钮"""
    try:
        # 解析回调数据: manual_tg_upload_{msg_id}
        callback_data = query.data
        parts = callback_data.split('_')
        
        if len(parts) < 4:
            await query.answer("❌ 无效的回调数据", show_alert=True)
            return
        
        msg_id = int(parts[3])
        
        # 加载图集状态
        state = await VideoLinkProcessor.load_manual_upload_state(msg_id)
        
        if not state:
            await query.answer("❌ 图集状态已过期，请重新解析链接", show_alert=True)
            return
        
        # 立即回应
        await query.answer("开始上传到群组...", show_alert=False)
        
        # 更新消息状态
        from bot.helper.telegram_helper.message_utils import edit_message
        await edit_message(
            query.message,
            f"📤 正在上传到群组...\n\n"
            f"📸 共 {len(state['images_list'])} 张图片\n"
            f"⏳ 请稍候..."
        )
        
        # 创建临时处理器执行上传
        temp_processor = VideoLinkProcessor(
            client,
            query.message.reply_to_message or query.message,
            ""  # 不需要URL
        )
        temp_processor.status_msg = query.message
        temp_processor.message = query.message.reply_to_message or query.message
        
        # 执行 Telegram 上传模式（传入画廊URL，以便在完成消息中显示）
        await temp_processor._handle_gallery_telegram_mode(
            state['images_list'],
            state['video_info'],
            state.get('gallery_url')  # 传递画廊URL
        )
        
        # 清理状态
        await VideoLinkProcessor.delete_manual_upload_state(msg_id)
        
    except Exception as e:
        LOGGER.error(f"Manual TG upload error: {e}")
        import traceback
        LOGGER.error(traceback.format_exc())
        try:
            from bot.helper.telegram_helper.message_utils import edit_message
            await edit_message(query.message, f"❌ 上传失败：{str(e)[:100]}")
    except Exception:
        pass


@new_task
async def handle_video_link(client, message, url):
    """
    处理视频链接的入口函数
    会尝试所有可能的方式下载，失败则返回错误
    """
    processor = VideoLinkProcessor(client, message, url)
    await processor.execute()

