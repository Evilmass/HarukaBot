import nonebot

from ...config import plugin_config
from ...database import DB as db
from ...utils import scheduler

bots = nonebot.get_bots()


# id 和 函数名要一致
@scheduler.scheduled_job("cron", hour=0, minute=0, second=0, timezone="Asia/Shanghai", id="notify_live_duration")
async def notify_live_duration():
    message_list = await db.get_live_duration()
    for ml in message_list:
        if ml["group_id"] in plugin_config.ignore_group:
            continue
        bot = bots.get(str(ml["bot_id"]))
        await bot.call_api("send_group_msg", **{"group_id": ml["group_id"], "message": ml["message"]})


@scheduler.scheduled_job("cron", hour=4, minute=0, second=0, timezone="Asia/Shanghai", id="reset_live_duration")
async def reset_live_duration():
    await db.reset_live_duration()


@scheduler.scheduled_job("cron", second="*/5", timezone="Asia/Shanghai", id="test")
async def test():
    # await db.reset_live_duration()
    # await notify_live_duration()
    pass
