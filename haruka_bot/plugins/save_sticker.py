from nonebot import logger
from nonebot.adapters.onebot.v11 import Bot, Message, MessageEvent, MessageSegment
from nonebot.plugin import PluginMetadata

from ..utils import on_command

__plugin_meta__ = PluginMetadata(
    name="表情包保存器",
    description="一款很简单的，用于保存已经不提供保存选项的 QQ 表情包的 Nonebot 插件",
    usage="以 save 命令回复表情包即可.\n机器人回复的静态表情可以直接保存，动态表情可以通过短链接保存。",
    type="application",
    homepage="https://github.com/colasama/nonebot-plugin-sticker-saver",
    supported_adapters={"~onebot.v11"},
)

face_extractor = on_command(
    "save",
    aliases={"保存图片", "保存表情", "保存"},
    priority=10,
    block=True,
)

# TARGET_REDIRECT_URL = "https://pic.colanns.me"
# TARGET_REDIRECT_URL_NT = "https://ntpic.colanns.me"


@face_extractor.handle()
async def handle_face_extraction(bot: Bot, event: MessageEvent):
    if event.reply:
        # 获取被回复的消息内容
        original_message = event.reply.message
        # 提取表情包并发送回去，静态表情包可以直接被保存
        for seg in original_message:
            logger.debug(f"seg: {seg} type: {seg.type}")
            if seg.type == "image":
                image_source = seg.data.get("url") or seg.data.get("file")
                if not image_source:
                    continue
                image_url = str(image_source)
                url = image_url
                # 用于 .gif 格式的表情包保存，加上一层跳转防止可能的检测
                # url = (
                #     image_url
                #     .replace("https://gchat.qpic.cn", TARGET_REDIRECT_URL)
                #     .replace("https://multimedia.nt.qq.com.cn", TARGET_REDIRECT_URL_NT)
                # )
                content = Message(
                    [
                        MessageSegment.text("表情："),
                        MessageSegment.image(image_source, type_=0),
                        MessageSegment.text(f"原始链接：{url}"),
                    ]
                )
                await bot.send(event, content)
                return
        await bot.send(event, "未在回复内容中检测到表情...")
    # 如果没有回复消息
    else:
        await bot.send(event, "只有回复表情才可以用捏")
