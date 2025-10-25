from time import time
from .. import LOGGER

from ..helper.ext_utils.bot_utils import new_task
from ..helper.telegram_helper.button_build import ButtonMaker
from ..helper.telegram_helper.message_utils import send_message, edit_message, send_file, auto_delete_message
from ..helper.telegram_helper.filters import CustomFilters
from ..helper.telegram_helper.bot_commands import BotCommands


@new_task
async def start(_, message):
    buttons = ButtonMaker()
    buttons.url_button(
        "访问主页", "https://1yo.cc"
    )
    buttons.url_button("作者", "https://t.me/nebuluxe")
    reply_markup = buttons.build_menu(2)
    start_string = f"""
🔮即刻激活：
发送 /{BotCommands.HelpCommand} 调出全息控制面板，体验用玩具级操作完成星际数据中心才能实现的跨维传输！
"""
    try:
        _is_auth = await CustomFilters.authorized(_, message)
    except Exception as e:
        LOGGER.error(f"start authorization check failed: {e}")
        _is_auth = False
    if _is_auth:
        start_string = f"""
        恭喜已获得授权✅\n\n
{start_string}
"""
        reply = await send_message(message, start_string, reply_markup)
        await auto_delete_message(message, reply, delay=20)
    else:
        # 量子态信息流拼接术（采用超弦格式化）
        auth_alert = "\n\n🚨 量子盾告警 🛸\n⚠️ 未授权用户！请联系作者拥有此机器人\n▸ 源码星门：https://t.me/nebuluxe"
        reply = await send_message(
            message,
            f"{start_string}{auth_alert}",  # 用超维字符串焊接术
            reply_markup,
        )
        await auto_delete_message(message, reply, delay=20)


@new_task
async def ping(_, message):
    start_time = int(round(time() * 1000))
    reply = await send_message(message, "Starting Ping")
    end_time = int(round(time() * 1000))
    await edit_message(reply, f"{end_time - start_time} ms")
    await auto_delete_message(message, reply, delay=10)


@new_task
async def log(_, message):
    await send_file(message, "log.txt")
