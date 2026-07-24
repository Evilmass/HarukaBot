import time

from bilireq.live import get_rooms_info_by_uids
from nonebot.adapters.onebot.v11.message import MessageSegment
from nonebot.log import logger

from ...config import plugin_config
from ...database import DB as db
from ...utils import PROXIES, calc_time_total, safe_send, scheduler

status = {}
live_time = {}
live_snapshot = {}


@scheduler.scheduled_job("interval", seconds=plugin_config.haruka_live_interval, id="live_sched")
async def live_sched():
    # sourcery skip: use-fstring-for-concatenation
    """直播推送"""
    uids = await db.get_all_subscription_uids()

    if not uids:  # 订阅为空
        status.clear()
        live_time.clear()
        live_snapshot.clear()
        return
    active_uids = {str(uid) for uid in uids}
    for uid in list(live_snapshot):
        if str(uid) not in active_uids:
            live_snapshot.pop(uid, None)
            status.pop(uid, None)
            live_time.pop(uid, None)
    logger.debug(f"爬取直播列表，目前开播{sum(status.values())}人，总共{len(uids)}人")
    res = await get_rooms_info_by_uids(uids, reqtype="web", proxies=PROXIES)
    if not res:
        return

    for uid, info in res.items():
        observed_at = int(time.time())
        new_status = 0 if info["live_status"] == 2 else info["live_status"]
        started_at = int(info.get("live_time") or observed_at) if new_status else 0
        area = str(info.get("area_v2_name") or "")
        area_parent = str(info.get("area_v2_parent_name") or "")
        live_snapshot[uid] = {
            "status": new_status,
            "checked_at": observed_at,
            "live_started_at": started_at,
            "title": str(info.get("title") or ""),
            "area": " / ".join(part for part in (area_parent, area) if part),
        }
        if new_status:
            live_time[uid] = started_at
            await db.observe_live_duration(
                uid=int(uid),
                live_started_at=started_at,
                observed_at=observed_at,
            )

        if uid not in status:
            status[uid] = new_status
            if not new_status:
                # A persisted active cursor may remain after the bot was stopped.
                # Do not count the unknown offline gap during startup recovery.
                await db.close_live_session(
                    uid=int(uid),
                    observed_at=observed_at,
                    account_until_observation=False,
                )
            continue
        old_status = status[uid]
        if new_status == old_status:  # 直播间状态无变化
            continue
        status[uid] = new_status

        name = info["uname"]
        if new_status:  # 开播
            room_id = info["short_id"] or info["room_id"]
            url = f"https://live.bilibili.com/{room_id}"
            title = info["title"]
            cover = info["cover_from_user"] or info["keyframe"]
            area = info["area_v2_name"]
            area_parent = info["area_v2_parent_name"]
            room_area = f"{area_parent} / {area}"
            logger.info(f"检测到开播：{name}（{uid}）")
            live_msg = f"{name} 开播啦！\n分区：{room_area}\n标题：{title}\n" + MessageSegment.image(cover) + f"\n{url}"
        else:  # 下播
            logger.info(f"检测到下播：{name}（{uid}）")
            await db.close_live_session(
                uid=int(uid),
                observed_at=observed_at,
            )
            started_at = live_time.pop(uid, None)
            if not plugin_config.haruka_live_off_notify:  # 没开下播推送
                await db.update_user(int(uid), name)
                continue
            current_duration = observed_at - started_at if started_at else 0
            live_time_msg = (
                f"\n本次直播时长 {calc_time_total(current_duration)}。"
                if current_duration > 0
                else "。"
            )
            live_msg = f"{name} 下播了{live_time_msg}"

        # 推送
        push_list = await db.get_push_list(uid, "live")
        for sets in push_list:
            await safe_send(
                bot_id=sets.bot_id,
                send_type=sets.type,
                type_id=sets.type_id,
                message=live_msg,
                at=bool(sets.at) if new_status else False,  # 下播不@全体
                subscription_id=sets.id,
                event_type="live_start" if new_status else "live_end",
            )
        await db.update_user(int(uid), name)
