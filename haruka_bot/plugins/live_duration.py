from typing import Optional

from ..database import DB as db
from ..utils import calc_time_total, on_command, safe_send, to_me

# 要在 plugins/__init__.py 导入模块
live_duration = on_command(
    cmd="live_duration",
    aliases={"nbw", "耐播王"},
    rule=to_me(),
    priority=5,
)


@live_duration.handle()
async def get_live_duration(cron: Optional[bool] = False):
    message = "今日耐播王\n"
    res = await db.get_live_duration()
    for r in res[:3]:
        message += f'{r["user"].ljust(10)}{calc_time_total(r["live_duration"])}\n'
    if not cron:
        await live_duration.finish(message)
    else:
        sub = await db.get_sub()
        await safe_send(
            bot_id=sub.bot_id,
            send_type=sub.type,
            type_id=sub.type_id,
            message=message,
            at=bool(sub.at),
        )
    return message
