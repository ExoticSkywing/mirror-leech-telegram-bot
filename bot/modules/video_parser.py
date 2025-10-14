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

from bot import LOGGER, DOWNLOAD_DIR, bot_loop
from bot.core.config_manager import Config
from bot.helper.ext_utils.bot_utils import new_task, sync_to_async
from bot.helper.ext_utils.files_utils import clean_target
from bot.helper.ext_utils.url_utils import get_domain
from bot.helper.parse_video_helper import parse_video_api, format_video_info
from bot.helper.listeners.task_listener import TaskListener
from bot.helper.mirror_leech_utils.download_utils.yt_dlp_download import YoutubeDLHelper
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
        # 说明：如果用户会话在该群具备所需权限则走用户会话，否则回退到 bot
        if Config.LEECH_DUMP_CHAT:
            self.up_dest = f"h:{Config.LEECH_DUMP_CHAT}"
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
            # 策略1: 尝试Parse-Video解析
            video_direct_url = None
            video_info = {}
            images_list = []

            await edit_message(
                self.status_msg,
                f"📡 正在通过 Parse-Video 解析...\n" f"🔗 <code>{self.url[:60]}...</code>",
            )

            parse_result = await parse_video_api(self.url)

            if parse_result:
                # Parse-Video解析成功
                video_direct_url = parse_result.get("video_url")
                images_list = parse_result.get("images", [])

                video_info = {
                    "title": parse_result.get("title", ""),
                    "author": parse_result.get("author", {}).get("name", ""),
                    "cover_url": parse_result.get("cover_url", ""),
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

            # 策略2: 使用yt-dlp下载视频
            # 如果有直链就下载直链，否则下载原链接
            download_url = video_direct_url if video_direct_url else self.url
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
                    self.name = self._sanitize_filename(video_info["title"])
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

    async def _handle_image_gallery(self, images_list, video_info):
        """
        处理图集下载和上传
        将图集作为媒体组（相册）上传到Telegram

        Args:
            images_list: 图片URL列表 [{'url': 'https://...', 'live_photo_url': '...'}, ...]
            video_info: 视频信息字典
        """

        try:
            LOGGER.info(f"Starting image gallery processing: {len(images_list)} images")
            
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
                            timeout=30
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
                f"📥 正在并发下载 {len(images_list)} 张图片...\n"
                f"📹 {video_info.get('title', '图集')}",
            )
            
            # 并发下载所有图片
            import asyncio
            download_tasks = [download_single_image(idx, img_data) for idx, img_data in enumerate(images_list)]
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

            # 分批上传（每批最多10张）
            total_imgs = len(images_list)
            total_batches = (len(downloaded_images) + 9) // 10
            # 上传期间的进度提示
            upload_status_msg = await send_message(
                self.message,
                f"⬆️ 正在上传图集… 0/{total_imgs} (0/{total_batches} 组)"
            )
            total_sent = 0
            album_links = []
            caption = self._build_caption(video_info)
            batch_index = 0
            for start in range(0, len(downloaded_images), 10):
                batch_paths = downloaded_images[start:start + 10]
                media_group = []
                for idx, img_path in enumerate(batch_paths):
                    if batch_index == 0 and idx == 0:
                        media_group.append(InputMediaPhoto(media=img_path, caption=caption))
                    else:
                        media_group.append(InputMediaPhoto(media=img_path))

                LOGGER.info(f"Uploading media group batch {batch_index + 1} with {len(media_group)} images")
                attempt = 0
                while True:
                    try:
                        msgs = await self.client.send_media_group(
                            chat_id=upload_dest, media=media_group, reply_to_message_id=None
                        )
                        break
                    except FloodWait as f:
                        wait_s = int(f.value) + 1
                        LOGGER.warning(f"FloodWait while sending album batch {batch_index + 1}: wait {wait_s}s")
                        await sleep(wait_s)
                        attempt += 1
                        if attempt >= 3:
                            raise
                total_sent += len(msgs)
                if msgs and hasattr(msgs[0], "link"):
                    album_links.append(msgs[0].link)
                batch_index += 1
                # 更新上传进度提示
                try:
                    await edit_message(
                        upload_status_msg,
                        f"⬆️ 正在上传图集… {total_sent}/{total_imgs} ({batch_index}/{total_batches} 组) 请耐心等待☺"
                    )
                except Exception:
                    pass
                await sleep(1)

            LOGGER.info(f"Media gallery uploaded in {batch_index} batch(es), total sent: {total_sent}")

            # 单条完成提示：总成功数量 + 相册链接列表
            success_rate = f"{total_sent}/{len(images_list)}"
            text = (
                f"✅ <b>图集上传完成</b>  📸 {success_rate}\n\n"
                f"{video_info.get('title', '图集')}\n\n"
                f"👤 {video_info.get('author', '未知作者')}"
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

    def _sanitize_filename(self, filename):
        """清理文件名，移除非法字符"""
        import re

        if not filename:
            return "video"
        
        # 移除或替换非法字符
        filename = re.sub(r'[<>:"/\\|?*]', "", str(filename))
        # 限制长度
        if len(filename) > 200:
            filename = filename[:200]
        result = filename.strip()
        return result if result else "video"

    async def _cleanup_temp_files(self, directory):
        """清理临时文件"""
        try:
            await clean_target(directory)
            LOGGER.info(f"Cleaned up temp directory: {directory}")
        except Exception as e:
            LOGGER.error(f"Error cleaning up temp directory: {e}")


@new_task
async def handle_video_link(client, message, url):
    """
    处理视频链接的入口函数
    会尝试所有可能的方式下载，失败则返回错误
    """
    processor = VideoLinkProcessor(client, message, url)
    await processor.execute()

