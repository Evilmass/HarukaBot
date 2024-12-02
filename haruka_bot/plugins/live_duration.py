from nonebot.adapters.onebot.v11.event import GroupMessageEvent

from ..database import DB as db
from ..utils import get_type_id, on_command, to_me

# 要在 plugins/__init__.py 导入模块
live_duration = on_command(
    cmd="live_duration",
    aliases={"nbw", "耐播王"},
    rule=to_me(),
    priority=5,
)


@live_duration.handle()
async def get_live_duration(event: GroupMessageEvent):
    message_list = await db.get_live_duration()
    for ml in message_list:
        if ml["group_id"] == await get_type_id(event):
            await live_duration.finish(ml["message"])
            break
