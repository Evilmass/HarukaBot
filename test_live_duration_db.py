import unittest
from datetime import date

DEPENDENCY_ERROR = ""
try:
    import nonebot
    from tortoise import Tortoise
    from tortoise.connection import connections
except ModuleNotFoundError as exc:
    DEPENDENCY_ERROR = f"数据库集成测试依赖未安装：{exc.name}"

if not DEPENDENCY_ERROR:
    nonebot.init()

    try:
        import haruka_bot.database.db as db_module
        from haruka_bot.database.db import DB
        from haruka_bot.database.models import LiveSession, Sub, User
    except ModuleNotFoundError as exc:
        DEPENDENCY_ERROR = f"HarukaBot 运行依赖未安装：{exc.name}"


@unittest.skipIf(bool(DEPENDENCY_ERROR), DEPENDENCY_ERROR)
class LiveDurationDatabaseTest(unittest.IsolatedAsyncioTestCase):
    async def test_resume_deduplication_cross_day_ranking_and_migration(self):
        config = {
            "connections": {"haruka_bot": "sqlite://:memory:"},
            "apps": {
                "haruka_bot_app": {
                    "models": ["haruka_bot.database.models"],
                    "default_connection": "haruka_bot",
                }
            },
        }
        await Tortoise.init(config)
        await Tortoise.generate_schemas()
        db_module.live_duration_lock = None
        old_day_start = db_module.plugin_config.haruka_live_duration_day_start_hour
        db_module.plugin_config.haruka_live_duration_day_start_hour = 0

        try:
            await User.create(uid=1, name="主播一", room_id=1)
            await Sub.create(
                type="group",
                type_id=100,
                uid=1,
                live=True,
                dynamic=False,
                at=False,
                bot_id=999,
                live_duration=0,
            )

            # Repeating the same observation must not add the same interval twice.
            started_at = 1784736000  # 2026-07-23 00:00:00 Asia/Shanghai
            await DB.observe_live_duration(1, started_at, started_at + 60)
            await DB.observe_live_duration(1, started_at, started_at + 60)
            await DB.observe_live_duration(1, started_at, started_at + 120)
            totals = await DB.get_live_duration_totals(date(2026, 7, 23))
            self.assertEqual(totals[1], 120)

            session = await LiveSession.get(uid=1).first()
            self.assertEqual(session.accounted_until, started_at + 120)
            self.assertTrue(session.active)

            ranking = await DB.get_live_duration(
                group_id=100,
                stat_date=date(2026, 7, 23),
            )
            self.assertEqual(len(ranking), 1)
            self.assertIn("主播一", ranking[0]["message"])

            # A session observed on both sides of midnight is split exactly.
            await User.create(uid=2, name="主播二", room_id=2)
            boundary_start = 1784735990
            await DB.observe_live_duration(2, boundary_start, boundary_start)
            await DB.observe_live_duration(2, boundary_start, boundary_start + 20)
            previous_totals = await DB.get_live_duration_totals(
                date(2026, 7, 22)
            )
            current_totals = await DB.get_live_duration_totals(
                date(2026, 7, 23)
            )
            self.assertEqual(previous_totals[2], 10)
            self.assertEqual(current_totals[2], 10)

            # Legacy per-subscription data is moved once and then cleared.
            await User.create(uid=3, name="主播三", room_id=3)
            legacy_sub = await Sub.create(
                type="group",
                type_id=100,
                uid=3,
                live=True,
                dynamic=False,
                at=False,
                bot_id=999,
                live_duration=90,
            )
            await DB.migrate_legacy_live_duration()
            await legacy_sub.refresh_from_db()
            self.assertEqual(legacy_sub.live_duration, 0)
            migrated_totals = await DB.get_live_duration_totals(
                DB.get_live_stat_date()
            )
            self.assertEqual(migrated_totals[3], 90)
        finally:
            db_module.plugin_config.haruka_live_duration_day_start_hour = (
                old_day_start
            )
            await connections.close_all()


if __name__ == "__main__":
    unittest.main()
