from ...database import DB as db
from ...utils import get_short_url, logger, scheduler


@scheduler.scheduled_job("cron", hour=4, minute=0, second=0, timezone="Asia/Shanghai", id="update_short_url")
async def update_short_url():
    message = "已更新用户直播间短链接\n"
    users = await db.get_users()
    for user in users:
        short_url = await get_short_url(user.room_id)
        await db.update_user_info(int(user.uid), {"short_url": short_url})
        msg = f"{user.name} {short_url}\n"
        logger.info(msg)
        message += msg
    return message
