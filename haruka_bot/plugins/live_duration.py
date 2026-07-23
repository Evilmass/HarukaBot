from nonebot.adapters.onebot.v11 import Message
from nonebot.adapters.onebot.v11.event import GroupMessageEvent
from nonebot.params import CommandArg

from ..database import DB as db
from ..utils import get_type_id, on_command, permission_check, to_me
from ..utils.live_duration import resolve_stat_date_query

# 要在 plugins/__init__.py 导入模块
live_duration = on_command(
    cmd="live_duration",
    aliases={"nbw", "耐播王"},
    rule=to_me(),
    priority=5,
)
live_duration.__doc__ = """耐播王 [今日|昨日|YYYY-MM-DD] -> nbw"""

live_duration.handle()(permission_check)


@live_duration.handle()
async def _(event: GroupMessageEvent, args: Message = CommandArg()):
    group_id = await get_type_id(event)
    current_stat_date = db.get_live_stat_date()
    query = args.extract_plain_text().strip()

    try:
        stat_date = resolve_stat_date_query(query, current_stat_date)
    except ValueError:
        await live_duration.finish(
            "日期格式错误，请使用：nbw、nbw 昨日或 nbw YYYY-MM-DD"
        )
        return

    if stat_date > current_stat_date:
        await live_duration.finish("不能查询未来的耐播王榜单")
    if stat_date == current_stat_date:
        await db.flush_live_duration()
        title = "今日耐播王"
    else:
        title = f"{stat_date.isoformat()} 耐播王"

    message_list = await db.get_live_duration(
        group_id=group_id,
        stat_date=stat_date,
        title=title,
    )
    if message_list:
        await live_duration.finish(message_list[0]["message"])
    await live_duration.finish(f"{stat_date.isoformat()} 暂无耐播王")
