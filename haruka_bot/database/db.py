import asyncio
import json
import time
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional

from nonebot import get_driver
from nonebot.log import logger
from packaging.version import Version as version_parser
from tortoise import Tortoise
from tortoise.connection import connections
from tortoise.expressions import F
from tortoise.transactions import in_transaction

from ..config import plugin_config
from ..utils import calc_time_total, get_path
from ..utils.live_duration import (
    split_duration_by_stat_date,
    stat_date_for_timestamp,
    stat_period_start_timestamp,
)
from ..version import VERSION as HBVERSION
from .models import (
    Group,
    Guild,
    LiveDurationDaily,
    LiveSession,
    Sub,
    User,
    Version,
)

uid_list = {"live": {"list": [], "index": 0}, "dynamic": {"list": [], "index": 0}}
dynamic_offset = {}
live_duration_lock: Optional[asyncio.Lock] = None


def get_live_duration_lock() -> asyncio.Lock:
    """Create the lock lazily so Python 3.8 binds it to the running loop."""
    global live_duration_lock
    if live_duration_lock is None:
        live_duration_lock = asyncio.Lock()
    return live_duration_lock


class DB:
    """数据库交互类，与增删改查无关的部分不应该在这里面实现"""

    @classmethod
    async def init(cls):
        """初始化数据库"""
        config = {
            "connections": {
                # "haruka_bot": {
                #     "engine": "tortoise.backends.sqlite",
                #     "credentials": {"file_path": get_path("data.sqlite3")},
                # },
                "haruka_bot": f"sqlite://{get_path('data.sqlite3')}"
            },
            "apps": {
                "haruka_bot_app": {
                    "models": ["haruka_bot.database.models"],
                    "default_connection": "haruka_bot",
                }
            },
        }

        await Tortoise.init(config)

        await Tortoise.generate_schemas()
        await cls.migrate()
        await cls.migrate_legacy_live_duration()
        await cls.update_uid_list()

    @classmethod
    async def close(cls):
        await connections.close_all()

    @classmethod
    async def get_user(cls, **kwargs):
        """获取 UP 主信息"""
        return await User.get(**kwargs).first()

    @classmethod
    async def get_users(cls, **kwargs):
        """获取所以 UP 主信息"""
        return await User.get(**kwargs).all()

    @classmethod
    async def get_name(cls, uid) -> Optional[str]:
        """获取 UP 主昵称"""
        user = await cls.get_user(uid=uid)
        if user:
            return user.name
        return None

    @classmethod
    async def add_user(cls, **kwargs):
        """添加 UP 主信息"""
        return await User.add(**kwargs)

    @classmethod
    async def delete_user(cls, uid) -> bool:
        """删除 UP 主信息"""
        if await cls.get_sub(uid=uid):
            # 还存在该 UP 主订阅，不能删除
            return False
        await User.delete(uid=uid)
        return True

    @classmethod
    async def update_user(cls, uid: int, name: str) -> bool:
        """更新 UP 主信息"""
        if await cls.get_user(uid=uid):
            await User.update({"uid": uid}, name=name)
            return True
        return False

    @classmethod
    async def update_user_info(cls, uid: int, data: dict) -> bool:
        """更新 UP 主信息"""
        if await cls.get_user(uid=uid):
            await User.update({"uid": uid}, **data)
            return True
        return False

    @classmethod
    async def get_group(cls, **kwargs):
        """获取群设置"""
        return await Group.get(**kwargs).first()

    @classmethod
    async def get_group_admin(cls, group_id) -> bool:
        """获取指定群权限状态"""
        group = await cls.get_group(id=group_id)
        if not group:
            # TODO 自定义默认状态
            return True
        return bool(group.admin)

    @classmethod
    async def get_guild_admin(cls, guild_id, channel_id) -> bool:
        """获取指定频道权限状态"""
        guild = await cls.get_guild(guild_id=guild_id, channel_id=channel_id)
        if not guild:
            # TODO 自定义默认状态
            return True
        return bool(guild.admin)

    @classmethod
    async def add_group(cls, **kwargs):
        """创建群设置"""
        return await Group.add(**kwargs)

    @classmethod
    async def add_guild(cls, **kwargs):
        """创建频道设置"""
        return await Guild.add(**kwargs)

    @classmethod
    async def delete_guild(cls, id) -> bool:
        """删除子频道设置"""
        if await cls.get_sub(type="guild", type_id=id):
            # 当前频道还有订阅，不能删除
            return False
        await Guild.delete(id=id)
        return True

    @classmethod
    async def delete_group(cls, id) -> bool:
        """删除群设置"""
        if await cls.get_sub(type="group", type_id=id):
            # 当前群还有订阅，不能删除
            return False
        await Group.delete(id=id)
        return True

    @classmethod
    async def set_permission(cls, id, switch):
        """设置指定群组权限"""
        if not await cls.add_group(id=id, admin=switch):
            await Group.update({"id": id}, admin=switch)

    @classmethod
    async def set_guild_permission(cls, guild_id, channel_id, switch):
        """设置指定频道权限"""
        if not await cls.add_guild(guild_id=guild_id, channel_id=channel_id, admin=switch):
            await Guild.update({"guild_id": guild_id, "channel_id": channel_id}, admin=switch)

    @classmethod
    async def get_guild(cls, **kwargs):
        """获取频道设置"""
        return await Guild.get(**kwargs).first()

    @classmethod
    async def get_guild_type_id(cls, guild_id, channel_id) -> Optional[int]:
        """获取频道订阅 ID"""
        guild = await Guild.get(guild_id=guild_id, channel_id=channel_id).first()
        return guild.id if guild else None

    @classmethod
    async def get_sub(cls, **kwargs):
        """获取指定位置的订阅信息"""
        return await Sub.get(**kwargs).first()

    @classmethod
    async def get_subs(cls, **kwargs):
        return await Sub.get(**kwargs)

    @classmethod
    async def get_push_list(cls, uid, func) -> List[Sub]:
        """根据类型和 UID 获取需要推送的 QQ 列表"""
        return await cls.get_subs(uid=uid, **{func: True})

    @classmethod
    async def get_sub_list(cls, type, type_id) -> List[Sub]:
        """获取指定位置的推送列表"""
        return await cls.get_subs(type=type, type_id=type_id)

    @classmethod
    async def add_sub(cls, *, name, room_id=0, short_url=None, **kwargs) -> bool:
        """添加订阅"""
        if await cls.get_sub(
            uid=kwargs["uid"],
            type=kwargs["type"],
            type_id=kwargs["type_id"],
        ):
            return False
        await Sub.create(live_duration=0, **kwargs)
        user = await cls.get_user(uid=kwargs["uid"])
        user_data = {"name": name}
        if room_id:
            user_data["room_id"] = room_id
        if short_url is not None:
            user_data["short_url"] = short_url
        if user:
            await User.update({"uid": kwargs["uid"]}, **user_data)
        else:
            await User.create(
                uid=kwargs["uid"],
                name=name,
                room_id=room_id,
                short_url=short_url,
            )
        if kwargs["type"] == "group":
            await cls.add_group(id=kwargs["type_id"], admin=True)
        await cls.update_uid_list()
        return True

    @classmethod
    async def get_sub_by_id(cls, sub_id: int):
        """按数据库 ID 获取订阅。"""
        return await Sub.get(id=sub_id).first()

    @classmethod
    async def update_sub_by_id(cls, sub_id: int, **kwargs):
        """更新订阅；不存在返回 None，目标重复返回 False。"""
        sub = await cls.get_sub_by_id(sub_id)
        if not sub:
            return None

        new_type_id = kwargs.get("type_id", sub.type_id)
        duplicate = await Sub.get(
            uid=sub.uid,
            type=sub.type,
            type_id=new_type_id,
        ).exclude(id=sub_id).exists()
        if duplicate:
            return False

        allowed = {"type_id", "bot_id", "live", "dynamic", "at"}
        updates = {key: value for key, value in kwargs.items() if key in allowed}
        if updates:
            await Sub.update({"id": sub_id}, **updates)
        if sub.type == "group":
            await cls.add_group(id=new_type_id, admin=True)
        await cls.update_uid_list()
        return True

    @classmethod
    async def delete_sub_by_id(cls, sub_id: int) -> bool:
        """按数据库 ID 删除订阅并清理孤立主播。"""
        sub = await cls.get_sub_by_id(sub_id)
        if not sub:
            return False
        uid = sub.uid
        await Sub.get(id=sub_id).delete()
        await cls.delete_user(uid=uid)
        await cls.update_uid_list()
        return True

    @classmethod
    async def delete_sub(cls, uid, type, type_id) -> bool:
        """删除指定订阅"""
        if await Sub.delete(uid=uid, type=type, type_id=type_id):
            await cls.delete_user(uid=uid)
            await cls.update_uid_list()
            return True
        # 订阅不存在
        return False

    @classmethod
    async def delete_sub_list(cls, type, type_id):
        """删除指定位置的推送列表"""
        async for sub in Sub.get(type=type, type_id=type_id):
            await cls.delete_sub(uid=sub.uid, type=sub.type, type_id=sub.type_id)
        await cls.update_uid_list()

    @classmethod
    async def set_sub(cls, conf, switch, **kwargs):
        """开关订阅设置"""
        return await Sub.update(kwargs, **{conf: switch})

    @classmethod
    async def get_version(cls):
        """获取数据库版本"""
        version = await Version.first()
        return version_parser(version.version) if version else None

    @classmethod
    async def migrate(cls):
        """迁移数据库"""
        DBVERSION = await cls.get_version()
        # 新数据库
        if not DBVERSION:
            # 检查是否有旧的 json 数据库需要迁移
            await cls.migrate_from_json()
            await Version.add(version=str(HBVERSION))
            return
        if DBVERSION != HBVERSION:
            # await cls._migrate()
            await Version.update({}, version=HBVERSION)
            return

    @classmethod
    async def migrate_from_json(cls):
        """从 TinyDB 的 config.json 迁移数据"""
        json_path = Path(get_path("config.json"))
        if not json_path.exists():
            return

        logger.info("正在从 config.json 迁移数据库")
        with json_path.open("r", encoding="utf-8") as f:
            old_db = json.loads(f.read())
        subs: Dict[int, Dict] = old_db["_default"]
        groups: Dict[int, Dict] = old_db["groups"]
        for sub in subs.values():
            await cls.add_sub(
                uid=sub["uid"],
                type=sub["type"],
                type_id=sub["type_id"],
                bot_id=sub["bot_id"],
                name=sub["name"],
                live=sub["live"],
                dynamic=sub["dynamic"],
                at=sub["at"],
            )
        for group in groups.values():
            await cls.set_permission(group["group_id"], group["admin"])

        json_path.rename(get_path("config.json.bak"))
        logger.info("数据库迁移完成")

    @classmethod
    async def get_uid_list(cls, func) -> List:
        """根据类型获取需要爬取的 UID 列表"""
        return uid_list[func]["list"]

    @classmethod
    async def next_uid(cls, func):
        """获取下一个要爬取的 UID"""
        func = uid_list[func]
        if func["list"] == []:
            return None

        if func["index"] >= len(func["list"]):
            func["index"] = 1
            return func["list"][0]
        else:
            index = func["index"]
            func["index"] += 1
            return func["list"][index]

    @classmethod
    async def update_uid_list(cls):
        """更新需要推送的 UP 主列表"""
        subs = Sub.all()
        uid_list["live"]["list"] = list(set([sub.uid async for sub in subs if sub.live]))
        uid_list["dynamic"]["list"] = list(set([sub.uid async for sub in subs if sub.dynamic]))

        # 清除没有订阅的 offset
        dynamic_offset_keys = set(dynamic_offset)
        dynamic_uids = set(uid_list["dynamic"]["list"])
        for uid in dynamic_offset_keys - dynamic_uids:
            del dynamic_offset[uid]
        for uid in dynamic_uids - dynamic_offset_keys:
            dynamic_offset[uid] = -1

        live_uids = uid_list["live"]["list"]
        stale_sessions = LiveSession.filter(active=True)
        if live_uids:
            stale_sessions = stale_sessions.exclude(uid__in=live_uids)
        await stale_sessions.update(active=False)

    async def backup(self):
        """备份数据库"""
        pass

    @classmethod
    async def get_login(cls):
        """获取登录信息"""
        pass

    @classmethod
    async def update_login(cls, tokens):
        """更新登录信息"""
        pass

    @classmethod
    def get_live_stat_date(cls, timestamp: Optional[int] = None) -> date:
        timestamp = int(time.time()) if timestamp is None else timestamp
        return stat_date_for_timestamp(
            timestamp,
            plugin_config.haruka_live_duration_day_start_hour,
        )

    @classmethod
    async def migrate_legacy_live_duration(cls):
        """Move old per-subscription counters into the new daily table once."""
        async with in_transaction():
            legacy_rows = await Sub.filter(live_duration__gt=0).values(
                "uid",
                "live_duration",
            )
            if not legacy_rows:
                return

            legacy_by_uid: Dict[int, int] = {}
            for row in legacy_rows:
                uid = row["uid"]
                legacy_by_uid[uid] = max(
                    legacy_by_uid.get(uid, 0),
                    row["live_duration"],
                )

            stat_date = cls.get_live_stat_date().isoformat()
            for uid, duration in legacy_by_uid.items():
                daily = await LiveDurationDaily.get(
                    uid=uid,
                    stat_date=stat_date,
                ).first()
                if daily:
                    if daily.duration < duration:
                        await LiveDurationDaily.update(
                            {"id": daily.id},
                            duration=duration,
                        )
                else:
                    await LiveDurationDaily.create(
                        uid=uid,
                        stat_date=stat_date,
                        duration=duration,
                    )

            await Sub.filter(live_duration__gt=0).update(live_duration=0)
        logger.info("已迁移旧版耐播王直播时长数据")

    @classmethod
    async def _increment_live_duration(cls, uid: int, stat_date: str, duration: int):
        if duration <= 0:
            return
        _, created = await LiveDurationDaily.get_or_create(
            uid=uid,
            stat_date=stat_date,
            defaults={"duration": duration},
        )
        if not created:
            await LiveDurationDaily.filter(
                uid=uid,
                stat_date=stat_date,
            ).update(duration=F("duration") + duration)

    @classmethod
    async def _add_live_interval(cls, uid: int, started_at: int, ended_at: int):
        durations = split_duration_by_stat_date(
            started_at,
            ended_at,
            plugin_config.haruka_live_duration_day_start_hour,
        )
        for stat_date, duration in durations.items():
            await cls._increment_live_duration(uid, stat_date, duration)

    @classmethod
    async def observe_live_duration(
        cls,
        uid: int,
        live_started_at: int,
        observed_at: Optional[int] = None,
    ) -> bool:
        """Account an online observation and persist its resume cursor."""
        async with get_live_duration_lock():
            async with in_transaction():
                return await cls._observe_live_duration(
                    uid,
                    live_started_at,
                    observed_at,
                )

    @classmethod
    async def _observe_live_duration(
        cls,
        uid: int,
        live_started_at: int,
        observed_at: Optional[int] = None,
    ) -> bool:
        observed_at = int(time.time()) if observed_at is None else observed_at
        if live_started_at <= 0 or live_started_at > observed_at:
            live_started_at = observed_at

        session = await LiveSession.get(uid=uid).first()
        if session and session.live_started_at == live_started_at:
            account_from = max(session.accounted_until, live_started_at)
        else:
            # On first observation, recover the current statistics period without
            # retroactively changing an already reported older period.
            account_from = max(
                live_started_at,
                stat_period_start_timestamp(
                    observed_at,
                    plugin_config.haruka_live_duration_day_start_hour,
                ),
            )

        if account_from < observed_at:
            await cls._add_live_interval(uid, account_from, observed_at)

        if session:
            await LiveSession.update(
                {"uid": uid},
                live_started_at=live_started_at,
                accounted_until=max(account_from, observed_at),
                active=True,
            )
        else:
            await LiveSession.create(
                uid=uid,
                live_started_at=live_started_at,
                accounted_until=max(account_from, observed_at),
                active=True,
            )
        return True

    @classmethod
    async def close_live_session(
        cls,
        uid: int,
        observed_at: Optional[int] = None,
        account_until_observation: bool = True,
    ):
        """Close a persisted session, optionally accounting up to this poll."""
        async with get_live_duration_lock():
            async with in_transaction():
                await cls._close_live_session(
                    uid,
                    observed_at,
                    account_until_observation,
                )

    @classmethod
    async def _close_live_session(
        cls,
        uid: int,
        observed_at: Optional[int] = None,
        account_until_observation: bool = True,
    ):
        session = await LiveSession.get(uid=uid).first()
        if not session:
            return

        observed_at = int(time.time()) if observed_at is None else observed_at
        accounted_until = session.accounted_until
        if (
            session.active
            and account_until_observation
            and accounted_until < observed_at
        ):
            polling_grace = max(plugin_config.haruka_live_interval * 2, 60)
            if observed_at - accounted_until <= polling_grace:
                await cls._add_live_interval(uid, accounted_until, observed_at)
                accounted_until = observed_at

        await LiveSession.update(
            {"uid": uid},
            accounted_until=accounted_until,
            active=False,
        )

    @classmethod
    async def flush_live_duration(cls, observed_at: Optional[int] = None):
        """Account active sessions through a report boundary."""
        observed_at = int(time.time()) if observed_at is None else observed_at
        async with get_live_duration_lock():
            async with in_transaction():
                sessions = await LiveSession.get(active=True)
                for session in sessions:
                    if session.accounted_until >= observed_at:
                        continue
                    # Do not assume a streamer stayed online through a long
                    # polling outage merely because its last state was active.
                    if observed_at - session.accounted_until > max(
                        plugin_config.haruka_live_interval * 2,
                        60,
                    ):
                        continue
                    await cls._add_live_interval(
                        session.uid,
                        session.accounted_until,
                        observed_at,
                    )
                    await LiveSession.update(
                        {"uid": session.uid},
                        accounted_until=observed_at,
                    )

    @classmethod
    async def get_live_duration_totals(
        cls,
        stat_date: Optional[date] = None,
    ) -> Dict[int, int]:
        stat_date = stat_date or cls.get_live_stat_date()
        rows = await LiveDurationDaily.get(stat_date=stat_date.isoformat())
        return {row.uid: row.duration for row in rows}

    @classmethod
    async def get_live_duration(
        cls,
        group_id: Optional[int] = None,
        stat_date: Optional[date] = None,
        title: str = "今日耐播王",
    ) -> List[Dict[str, object]]:
        """Build per-group live-duration ranking messages."""
        totals = await cls.get_live_duration_totals(stat_date)
        if not totals:
            return []

        subs_query = Sub.get(
            type="group",
            live=True,
            uid__in=list(totals),
        )
        if group_id is not None:
            subs_query = subs_query.filter(type_id=group_id)
        subs = await subs_query
        if not subs:
            return []

        users = await User.get(uid__in=list(totals))
        names = {user.uid: user.name for user in users}
        grouped_data: Dict[int, List[Dict[str, object]]] = {}
        for sub in subs:
            duration = totals.get(sub.uid, 0)
            if duration <= 0:
                continue
            grouped_data.setdefault(sub.type_id, []).append(
                {
                    "uid": sub.uid,
                    "bot_id": sub.bot_id,
                    "user": names.get(sub.uid, str(sub.uid)),
                    "live_duration": duration,
                }
            )

        message_list: List[Dict[str, object]] = []
        for current_group_id, items in grouped_data.items():
            top_items = sorted(
                items,
                key=lambda item: (
                    -int(item["live_duration"]),
                    str(item["user"]),
                    int(item["uid"]),
                ),
            )[: plugin_config.haruka_live_duration_top_n]
            lines = [title]
            for rank, item in enumerate(top_items, 1):
                lines.append(
                    f'{rank}. {item["user"]} — '
                    f'{calc_time_total(item["live_duration"]).strip()}'
                )
            message_list.append(
                {
                    "group_id": current_group_id,
                    "bot_id": top_items[0]["bot_id"],
                    "message": "\n".join(lines),
                }
            )

        return message_list

    @classmethod
    async def update_live_duration(
        cls,
        uid: int,
        live_duration: int = 0,
        stop_live: bool = False,
    ) -> bool:
        """Compatibility helper for callers that still submit duration deltas."""
        if not await User.get(uid=uid).exists():
            return False
        async with get_live_duration_lock():
            async with in_transaction():
                if stop_live:
                    daily, _ = await LiveDurationDaily.get_or_create(
                        uid=uid,
                        stat_date=cls.get_live_stat_date().isoformat(),
                        defaults={"duration": live_duration},
                    )
                    if daily.duration != live_duration:
                        await LiveDurationDaily.update(
                            {"id": daily.id},
                            duration=live_duration,
                        )
                else:
                    await cls._increment_live_duration(
                        uid,
                        cls.get_live_stat_date().isoformat(),
                        live_duration,
                    )
        return True

    @classmethod
    async def reset_live_duration(cls):
        """Compatibility helper that clears only the current statistics day."""
        await LiveDurationDaily.filter(
            stat_date=cls.get_live_stat_date().isoformat()
        ).delete()


get_driver().on_startup(DB.init)
get_driver().on_shutdown(DB.close)
