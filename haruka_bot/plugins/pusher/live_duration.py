from typing import Optional

from nonebot.adapters.onebot.v11.event import MessageEvent

from ...database import DB as db
from ...utils import scheduler
from ..live_duration import get_live_duration


@scheduler.scheduled_job("cron", hour=0, minute=0, second=0, timezone="Asia/Shanghai", id="get_live_duration")
async def notify_live_duration():
    await get_live_duration(cron=True)


@scheduler.scheduled_job("cron", hour=4, minute=0, second=0, timezone="Asia/Shanghai", id="reset_live_duration")
async def reset_live_duration():
    await db.reset_live_duration()


@scheduler.scheduled_job("cron", second="*/5", timezone="Asia/Shanghai", id="test")
async def test():
    # await db.reset_live_duration()
    # message = await get_live_duration(cron=True)
    # print(message)
    pass
