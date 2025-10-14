"""
直接链接处理器
处理用户直接发送的视频分享链接（无命令）
"""

from bot import LOGGER
from bot.helper.ext_utils.bot_utils import new_task
from bot.helper.ext_utils.url_utils import extract_url_from_text
from bot.helper.telegram_helper.filters import CustomFilters
from bot.helper.telegram_helper.message_utils import send_message
from bot.modules.video_parser import handle_video_link


@new_task
async def handle_direct_message(client, message):
    """
    处理用户直接发送的消息（无命令）
    
    逻辑：
    1. 如果包含链接 → 走Parse-Video完整流程
    2. 如果不包含链接 → 提示需要发送链接或使用命令
    """
    
    # 获取消息文本
    text = message.text or message.caption or ""

    # 保护：如果是命令消息（以'/'开头或包含bot_command实体），直接忽略，交给原有命令处理器
    try:
        if text.strip().startswith("/"):
            return
        entities = (message.entities or []) + (message.caption_entities or [])
        for ent in entities:
            if getattr(ent, "type", None) == "bot_command" and getattr(ent, "offset", 0) == 0:
                return
    except Exception:
        # 安全兜底，任何异常都不影响后续逻辑
        pass
    
    # 提取URL
    url = extract_url_from_text(text)
    
    if url:
        # 有链接：走Parse-Video处理流程
        LOGGER.info(f"Direct link detected from user {message.from_user.id}: {url[:50]}...")
        await handle_video_link(client, message, url)
    
    else:
        # 无链接：提示用户
        await send_message(
            message,
            "💡 <b>使用说明</b>\n\n"
            "直接发送视频分享链接即可下载\n\n"
            "<b>支持平台：</b>\n"
            "• 抖音 (Douyin)\n"
            "• 快手 (Kuaishou)\n"
            "• 小红书 (Xiaohongshu)\n"
            "• 哔哩哔哩 (Bilibili)\n"
            "• 微博 (Weibo)\n"
            "• 以及其他20+平台...\n\n"
            "<b>其他功能请使用命令：</b>\n"
            "• /ytdlleech - YouTube等平台\n"
            "• /leech - 通用下载\n"
            "• /help - 查看所有命令"
        )

