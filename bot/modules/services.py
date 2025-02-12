from time import time

from ..helper.ext_utils.bot_utils import new_task
from ..helper.telegram_helper.button_build import ButtonMaker
from ..helper.telegram_helper.message_utils import send_message, edit_message, send_file
from ..helper.telegram_helper.filters import CustomFilters
from ..helper.telegram_helper.bot_commands import BotCommands


@new_task
async def start(_, message):
    buttons = ButtonMaker()
    buttons.url_button(
        "访问官网", "https://lmyz.1yo.cc"
    )
    buttons.url_button("作者", "https://t.me/nebuluxe")
    reply_markup = buttons.build_menu(2)
    if await CustomFilters.authorized(_, message):
        start_string = f"""
【⚡️数据跃迁魔方——你的次元级云端操控台】

✨无需代码的次元穿梭术：
搭载「多维传输引擎」，可瞬间抓取磁链/暗网种子/NZB秘钥，在Telegram量子通道、GD星云与私有云堡垒间构建超流体隧道！

🌌黑科技亮点舱：
▫️「全息智库」一键解析12种数字暗语（.torrent/.nzb/.tgfiles…）
▫️支持将散落星尘重组为云端矩阵，Rclone联邦任你殖民
▫️跨维度镜像铸造厂，数据炼金术无视格式结界
▫️自载量子纠缠协议，百万级文件瞬时量子化

💾未来档案员模式：
点击「/星门指令」唤醒深度AI导航员，获取暗物质存储方案/虫洞加速协议/反熵压缩算法——你的每一次数据跃迁都在改写云端拓扑学！

🔮即刻激活：
发送 /{BotCommands.HelpCommand} 调出全息控制面板，体验用玩具级操作完成星际数据中心才能实现的跨维传输！
"""
        await send_message(message, start_string, reply_markup)
    else:
        # 量子态信息流拼接术（采用超弦格式化）
        auth_alert = "\n\n🚨 量子盾告警 🛸\n⚠️ 未授权用户！请联系作者拥有此机器人\n▸ 源码星门：https://t.me/nebuluxe"
        await send_message(
            message,
            f"{start_string}{auth_alert}",  # 用超维字符串焊接术
            reply_markup,
        )


@new_task
async def ping(_, message):
    start_time = int(round(time() * 1000))
    reply = await send_message(message, "Starting Ping")
    end_time = int(round(time() * 1000))
    await edit_message(reply, f"{end_time - start_time} ms")


@new_task
async def log(_, message):
    await send_file(message, "log.txt")
