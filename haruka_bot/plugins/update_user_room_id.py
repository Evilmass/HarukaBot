from nonebot.adapters.onebot.v11.event import GroupMessageEvent

from ..database import DB as db
from ..utils import get_room_id, logger, on_command, to_me

update_user_live_room_id = on_command(
    cmd="update_user_live_room_id",
    aliases={"更新所有用户直播间信息"},
    rule=to_me(),
    priority=5,
)
update_user_live_room_id.__doc__ = """更新所有用户直播间信息"""


@update_user_live_room_id.handle()
async def _(event: GroupMessageEvent):
    message = "已更新用户直播间信息\n"
    users = await db.get_users()
    for user in users:
        if user.room_id != 0:
            logger.info(f"ignore {user.name}")
            continue
        else:
            room_id = await get_room_id(user.uid)
            await db.update_user_info(user.uid, data={"room_id": room_id})
            msg = f"{user.name}\t{room_id}\n"
            logger.info(msg)
            message += msg
    await update_user_live_room_id.finish(message)
