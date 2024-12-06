from nonebot import on_notice
from nonebot.adapters.onebot.v11 import Bot
from nonebot.adapters.onebot.v11.event import GroupMessageEvent, GroupRecallNoticeEvent
from nonebot.log import logger

from ..utils import to_me  # 要用这个 to_me 才能调用

anti_pic_recall = on_notice(rule=to_me(), priority=1)


@anti_pic_recall.handle()
async def _(bot: Bot, event: GroupRecallNoticeEvent | GroupMessageEvent):
    logger.warning(event.user_id)
    logger.info(event.group_id)
    logger.success(event.message_id)

    logger.info(event.message)

    await bot.delete_msg(message_id=event.message_id)
