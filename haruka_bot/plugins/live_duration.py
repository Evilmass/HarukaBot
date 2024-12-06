from nonebot.adapters.onebot.v11.event import GroupMessageEvent

from ..database import DB as db
from ..utils import get_type_id, on_command, permission_check, to_me

# 要在 plugins/__init__.py 导入模块
live_duration = on_command(
    cmd="live_duration",
    aliases={"nbw", "耐播王"},
    rule=to_me(),
    priority=5,
)
live_duration.__doc__ = """耐播王 -> nbw"""

live_duration.handle()(permission_check)


@live_duration.handle()
async def _(event: GroupMessageEvent):
    if message_list := await db.get_live_duration():
        for ml in message_list:
            if ml["group_id"] == await get_type_id(event):
                await live_duration.finish(ml["message"])
                break
    else:
        print("no streaming")
        await live_duration.finish("今日暂无耐播王")
