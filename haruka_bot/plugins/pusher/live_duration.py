from datetime import timedelta

from ...config import plugin_config
from ...database import DB as db
from ...utils import safe_send, scheduler


# 在统计日切换时发送刚结束的完整榜单。
@scheduler.scheduled_job(
    "cron",
    hour=plugin_config.haruka_live_duration_day_start_hour,
    minute=0,
    second=0,
    timezone="Asia/Shanghai",
    id="notify_live_duration",
)
async def notify_live_duration():
    await db.flush_live_duration()
    stat_date = db.get_live_stat_date() - timedelta(days=1)
    message_list = await db.get_live_duration(
        stat_date=stat_date,
        title=f"{stat_date.isoformat()} 耐播王",
    )
    for ml in message_list:
        if ml["group_id"] in (plugin_config.ignore_group or []):
            continue
        await safe_send(
            bot_id=ml["bot_id"],
            send_type="group",
            type_id=ml["group_id"],
            message=ml["message"],
        )
