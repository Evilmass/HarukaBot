from ..database import DB as db
from ..utils import calc_time_total, on_command, to_me

# 要在 plugins/__init__.py 导入模块
live_duration = on_command(
    cmd="live_duration",
    aliases={"nbw", "耐播王"},
    rule=to_me(),
    priority=5,
)


@live_duration.handle()
async def get_nbw():
    """
    统计前三个
    """
    message = "今日耐播王\n"
    res = await db.get_live_duration()
    max_len = max([len(x["user"]) for x in res])
    for r in res[:3]:
        message += f'{r["user"].ljust(max_len+1)}{calc_time_total(r["live_duration"])}\n'
    await live_duration.finish(message)
