from nonebot.adapters.onebot.v11.event import MessageEvent

from ...database import DB as db
from ...utils import get_type_id, on_command, permission_check, to_me
from ..pusher.live_pusher import status

live_now = on_command("已开播", rule=to_me(), priority=5)
live_now.__doc__ = """已开播"""

live_now.handle()(permission_check)


@live_now.handle()
async def _(event: MessageEvent):
    """返回已开播的直播间"""
    # 一次性获取所有订阅信息和直播间状态
    subs = await db.get_sub_list(event.message_type, await get_type_id(event))
    live_subs = [sub for sub in subs if status.get(str(sub.uid)) == 1]
    if not live_subs:
        await live_now.finish("当前没有正在直播的主播")
        return

    message = f"共有{len(live_subs)}个主播正在直播：\n\n"
    users = [await db.get_user(uid=sub.uid) for sub in live_subs]
    for user in users:
        message += f"{user.name} ({user.short_url})\n"

    await live_now.finish(message)
