from nonebot.adapters.onebot.v11.event import MessageEvent

from ...database import DB as db
from ...utils import (
    get_room_id,
    get_short_url,
    get_type_id,
    logger,
    on_command,
    permission_check,
    to_me,
)
from ..pusher.live_pusher import status

live_now = on_command("已开播", rule=to_me(), priority=5)
live_now.__doc__ = """已开播"""

live_now.handle()(permission_check)


@live_now.handle()
async def _(event: MessageEvent):
    """返回已开播的直播间"""
    subs = await db.get_sub_list(event.message_type, await get_type_id(event))
    if now_live := [sub for sub in subs if status.get(str(sub.uid)) == 1]:
        message = f"共有{len(now_live)}个主播正在直播：\n\n"
        for sub in now_live:
            name = await db.get_name(sub.uid)
            if not name:
                continue
            room_id = await get_room_id(sub.uid)
            short_url = await get_short_url(room_id)
            msg = f"{name} ({short_url})\n"
            print(msg)
            message += msg
        await live_now.finish(message)
    await live_now.finish("当前没有正在直播的主播")
