from nonebot.adapters.onebot.v11.event import MessageEvent

from ...database import DB as db
from ...plugins.live_duration import get_nbw
from ...utils import scheduler

status = {}
live_time = {}


@scheduler.scheduled_job("cron", hour=0, minute=0, seconds=0, id="notify_live_duration")
async def notify_live_duration(event: MessageEvent):
    """
    每天12点通知一次
    """
    await get_nbw(event)


@scheduler.scheduled_job("cron", hour=4, minute=0, seconds=0, id="reset_live_duration")
async def reset_live_duration():
    """
    每天凌晨4点重置当日直播时长
    """
    return await db.reset_live_duration()
