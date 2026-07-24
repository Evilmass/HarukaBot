import asyncio
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import nonebot
from bilireq.exceptions import ResponseCodeError

nonebot.init()

from fastapi.testclient import TestClient

from haruka_bot.config import plugin_config
from haruka_bot.database import DB as db
from haruka_bot.utils import SendResult, safe_send
from haruka_bot.web.app import (
    CSRF_COOKIE,
    LOGIN_MAX_TRACKED_IPS,
    ROOM_RESOLVE_TIMEOUT_SECONDS,
    RoomResolveError,
    _avatar_cache,
    _bot_cache,
    _create_session,
    _get_avatar_data,
    _login_failures,
    _normalize_avatar_url,
    _prune_login_failures,
    _read_session,
    _resolve_room,
    _resolve_uid,
    setup_web,
)


class WebSecurityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.old_password = plugin_config.haruka_web_password
        cls.old_dir = plugin_config.haruka_dir
        cls.temp_dir = tempfile.TemporaryDirectory()
        plugin_config.haruka_web_password = "correct-horse-battery-staple"
        plugin_config.haruka_dir = cls.temp_dir.name
        setup_web()

    @classmethod
    def tearDownClass(cls):
        plugin_config.haruka_web_password = cls.old_password
        plugin_config.haruka_dir = cls.old_dir
        cls.temp_dir.cleanup()

    def setUp(self):
        _login_failures.clear()

    def test_signed_session_rejects_tampering(self):
        session = _create_session()
        self.assertEqual(_read_session(session["token"])["csrf"], session["csrf"])
        tampered = session["token"][:-1] + (
            "A" if session["token"][-1] != "A" else "B"
        )
        self.assertIsNone(_read_session(tampered))
        future = time.time() + plugin_config.haruka_web_session_ttl + 1
        with patch("haruka_bot.web.app.time.time", return_value=future):
            self.assertIsNone(_read_session(session["token"]))

    def test_login_and_csrf_protection(self):
        with TestClient(nonebot.get_app()) as client:
            redirect = client.get("/admin", follow_redirects=False)
            self.assertEqual(redirect.status_code, 307)
            self.assertEqual(redirect.headers["location"], "/admin/")

            index = client.get("/admin/")
            self.assertEqual(index.status_code, 200)
            self.assertIn("default-src 'self'", index.headers["Content-Security-Policy"])
            self.assertEqual(index.headers["X-Frame-Options"], "DENY")
            self.assertEqual(index.headers["Cache-Control"], "no-cache")

            static = client.get("/admin/static/app.js")
            self.assertEqual(static.status_code, 200)
            self.assertEqual(static.headers["X-Content-Type-Options"], "nosniff")
            self.assertIn("AUTO_REFRESH_INTERVAL_MS", static.text)
            self.assertIn("live_status", static.text)
            self.assertIn("target_type", static.text)

            page = index.text
            self.assertNotIn('id="status-filter"', page)
            self.assertIn('id="source-type-input"', page)
            self.assertIn('<option value="uid">用户 UID</option>', page)
            self.assertIn('<option value="room">直播间号</option>', page)
            self.assertIn('id="source-value-input"', page)
            self.assertNotIn('class="source-tabs"', page)
            self.assertIn('id="page-size-input"', page)
            self.assertIn('id="bulk-toolbar"', page)
            self.assertNotIn('id="audit-drawer"', page)
            self.assertNotIn('data-view="streamers"', page)
            self.assertIn('data-summary-filter="all"', page)
            self.assertIn('data-summary-filter="live"', page)
            self.assertIn('data-summary-filter="enabled"', page)
            self.assertIn('data-summary-filter="online-bots"', page)
            self.assertIn('value="100"', page)
            self.assertIn('value="private"', page)
            self.assertIn('value="guild"', page)

            self.assertEqual(client.get("/haruka/").status_code, 404)
            self.assertEqual(
                client.get("/haruka/api/auth/session").status_code,
                404,
            )

            password = plugin_config.haruka_web_password
            plugin_config.haruka_web_password = None
            try:
                disabled = client.get("/admin/api/auth/session")
                self.assertEqual(disabled.status_code, 503)
                self.assertEqual(disabled.headers["Cache-Control"], "no-store")
            finally:
                plugin_config.haruka_web_password = password

            wrong = client.post(
                "/admin/api/auth/login",
                json={"password": "wrong"},
            )
            self.assertEqual(wrong.status_code, 401)
            self.assertEqual(wrong.headers["Cache-Control"], "no-store")

            login = client.post(
                "/admin/api/auth/login",
                json={"password": "correct-horse-battery-staple"},
            )
            self.assertEqual(login.status_code, 200)
            self.assertTrue(login.json()["authenticated"])
            set_cookie_headers = login.headers.get_list("set-cookie")
            self.assertEqual(len(set_cookie_headers), 2)
            self.assertTrue(
                all("Path=/admin" in header for header in set_cookie_headers)
            )

            session = client.get("/admin/api/auth/session")
            self.assertEqual(session.status_code, 200)
            self.assertTrue(session.json()["authenticated"])

            no_csrf = client.delete("/admin/api/subscriptions/999999")
            self.assertEqual(no_csrf.status_code, 403)

            csrf = client.cookies.get(CSRF_COOKIE)
            headers = {"X-CSRF-Token": csrf}
            room = {
                "uid": 1001,
                "name": "网页,测试主播",
                "room_id": 4001,
                "short_id": 0,
            }
            with patch(
                "haruka_bot.web.app._resolve_room",
                new=AsyncMock(return_value=room),
            ), patch(
                "haruka_bot.web.app._resolve_uid",
                new=AsyncMock(return_value=room),
            ):
                created = client.post(
                    "/admin/api/subscriptions",
                    headers=headers,
                    json={
                        "room_id": 4001,
                        "target_id": 2001,
                        "bot_id": 3001,
                        "live": True,
                        "dynamic": False,
                        "at": False,
                    },
                )
                self.assertEqual(created.status_code, 201)
                sub_id = created.json()["id"]

                duplicate = client.post(
                    "/admin/api/subscriptions",
                    headers=headers,
                    json={
                        "room_id": 4001,
                        "target_id": 2001,
                        "bot_id": 9999,
                    },
                )
                self.assertEqual(duplicate.status_code, 409)

                private_created = client.post(
                    "/admin/api/subscriptions",
                    headers=headers,
                    json={
                        "uid": 1001,
                        "target_type": "private",
                        "target_id": 5001,
                        "bot_id": 3001,
                        "at": True,
                    },
                )
                self.assertEqual(private_created.status_code, 201)
                self.assertEqual(private_created.json()["target_type"], "private")
                self.assertEqual(private_created.json()["target_id"], 5001)
                self.assertFalse(private_created.json()["at"])
                private_sub_id = private_created.json()["id"]

                guild_created = client.post(
                    "/admin/api/subscriptions",
                    headers=headers,
                    json={
                        "room_id": 4001,
                        "target_type": "guild",
                        "guild_id": "guild-a",
                        "channel_id": "channel-b",
                        "bot_id": 3001,
                    },
                )
                self.assertEqual(guild_created.status_code, 201)
                self.assertEqual(guild_created.json()["target_type"], "guild")
                self.assertEqual(guild_created.json()["guild_id"], "guild-a")
                self.assertEqual(guild_created.json()["channel_id"], "channel-b")
                guild_sub_id = guild_created.json()["id"]

                ambiguous_source = client.post(
                    "/admin/api/subscriptions",
                    headers=headers,
                    json={
                        "uid": 1001,
                        "room_id": 4001,
                        "target_id": 2003,
                        "bot_id": 3001,
                    },
                )
                self.assertEqual(ambiguous_source.status_code, 422)

            updated = client.patch(
                f"/admin/api/subscriptions/{sub_id}",
                headers=headers,
                json={
                    "target_id": 2002,
                    "bot_id": 3002,
                    "live": False,
                    "dynamic": True,
                    "at": True,
                },
            )
            self.assertEqual(updated.status_code, 200)
            self.assertEqual(updated.json()["target_id"], 2002)
            self.assertFalse(updated.json()["live"])
            self.assertTrue(updated.json()["dynamic"])

            with patch.dict(
                "haruka_bot.plugins.pusher.live_pusher.status",
                {"1001": 1},
                clear=True,
            ), patch.dict(
                "haruka_bot.plugins.pusher.live_pusher.live_snapshot",
                {
                    "1001": {
                        "status": 1,
                        "checked_at": 123456,
                        "live_started_at": 123400,
                        "title": "测试直播标题",
                        "area": "游戏 / 单机",
                    }
                },
                clear=True,
            ):
                listing = client.get(
                    "/admin/api/subscriptions",
                    params={
                        "q": "网页",
                        "target_type": "group",
                        "live_enabled": "false",
                        "live_status": "live",
                    },
                )
            self.assertEqual(listing.status_code, 200)
            self.assertEqual(listing.json()["total"], 1)
            self.assertEqual(listing.json()["page"], 1)
            self.assertEqual(listing.json()["page_size"], 10)
            self.assertEqual(listing.json()["live_total"], 1)
            self.assertEqual(listing.json()["enabled_total"], 0)
            self.assertEqual(listing.json()["items"][0]["live_status"], "live")
            self.assertEqual(listing.json()["items"][0]["checked_at"], 123456)
            self.assertEqual(listing.json()["items"][0]["live_title"], "测试直播标题")
            self.assertEqual(
                listing.json()["items"][0]["avatar_url"],
                "/admin/api/users/1001/avatar",
            )

            with patch(
                "haruka_bot.web.app._get_avatar_data",
                new=AsyncMock(return_value=(b"\x89PNG\r\n", "image/png")),
            ):
                avatar = client.get("/admin/api/users/1001/avatar")
            self.assertEqual(avatar.status_code, 200)
            self.assertEqual(avatar.headers["content-type"], "image/png")
            self.assertIn("max-age=3600", avatar.headers["cache-control"])

            bulk = client.post(
                "/admin/api/subscriptions/bulk",
                headers=headers,
                json={
                    "ids": [private_sub_id, 999999],
                    "operation": "update",
                    "live": False,
                    "at": True,
                },
            )
            self.assertEqual(bulk.status_code, 200)
            self.assertEqual(bulk.json()["processed_ids"], [private_sub_id])
            self.assertEqual(bulk.json()["missing_ids"], [999999])
            private_after_bulk = client.get(
                "/admin/api/subscriptions",
                params={"target_type": "private", "target_id": 5001},
            ).json()["items"][0]
            self.assertFalse(private_after_bulk["live"])
            self.assertFalse(private_after_bulk["at"])

            push_result = SendResult(
                success=False,
                code="bot_offline",
                message="配置机器人未连接",
                bot_id=3001,
            )
            with patch(
                "haruka_bot.web.app.safe_send",
                new=AsyncMock(return_value=push_result),
            ) as send:
                test_push = client.post(
                    f"/admin/api/subscriptions/{private_sub_id}/test-push",
                    headers=headers,
                )
            self.assertEqual(test_push.status_code, 200)
            self.assertFalse(test_push.json()["success"])
            self.assertEqual(test_push.json()["code"], "bot_offline")
            self.assertFalse(send.await_args.kwargs["allow_fallback"])
            self.assertFalse(send.await_args.kwargs["cleanup_invalid_target"])

            audit = client.get("/admin/api/audit")
            self.assertEqual(audit.status_code, 200)
            self.assertGreaterEqual(audit.json()["total"], 1)
            self.assertIn(
                "test_push",
                {item["action"] for item in audit.json()["items"]},
            )

            options = client.get("/admin/api/options")
            self.assertEqual(options.status_code, 200)
            self.assertIn(3002, [bot["id"] for bot in options.json()["bots"]])

            failing_bot = SimpleNamespace(
                self_id="4004",
                call_api=AsyncMock(side_effect=RuntimeError("OneBot unavailable")),
            )
            with patch(
                "haruka_bot.web.app.nonebot.get_bots",
                return_value={"4004": failing_bot},
            ):
                _bot_cache["expires"] = 0
                degraded = client.get("/admin/api/options")
            self.assertEqual(degraded.status_code, 200)
            degraded_bot = next(
                bot for bot in degraded.json()["bots"] if bot["id"] == 4004
            )
            self.assertTrue(degraded_bot["online"])
            self.assertEqual(degraded_bot["groups"], [])

            group_row = updated.json()
            paginated_rows = [
                {
                    **group_row,
                    "id": group_row["id"] + index,
                    "uid": group_row["uid"] + index // 2,
                    "checked_at": 1000 + index,
                    "bot_online": index % 2 == 0,
                }
                for index in range(25)
            ]
            with patch(
                "haruka_bot.web.app._subscription_rows",
                new=AsyncMock(return_value=paginated_rows),
            ):
                second_page = client.get(
                    "/admin/api/subscriptions",
                    params={"page": 2, "page_size": 10},
                )
                last_page = client.get(
                    "/admin/api/subscriptions",
                    params={"page": 99, "page_size": 10},
                )
                invalid_page_size = client.get(
                    "/admin/api/subscriptions",
                    params={"page_size": 25},
                )
                exact_uid = client.get(
                    "/admin/api/subscriptions",
                    params={"uid": group_row["uid"] + 3},
                )
                sorted_rows = client.get(
                    "/admin/api/subscriptions",
                    params={"sort_by": "uid", "sort_order": "desc"},
                )
                online_bot_rows = client.get(
                    "/admin/api/subscriptions",
                    params={"bot_online": "true"},
                )
                streamer_page = client.get(
                    "/admin/api/streamers",
                    params={"page": 1, "page_size": 10},
                )
            self.assertEqual(second_page.status_code, 200, second_page.text)
            self.assertEqual(second_page.json()["total"], 25)
            self.assertEqual(second_page.json()["page"], 2)
            self.assertEqual(len(second_page.json()["items"]), 10)
            self.assertEqual(last_page.json()["page"], 3)
            self.assertEqual(len(last_page.json()["items"]), 5)
            self.assertEqual(invalid_page_size.status_code, 422)
            self.assertEqual(exact_uid.json()["total"], 2)
            self.assertEqual(
                sorted_rows.json()["items"][0]["uid"],
                group_row["uid"] + 12,
            )
            self.assertEqual(online_bot_rows.json()["total"], 13)
            self.assertEqual(online_bot_rows.json()["summary_total"], 25)
            self.assertEqual(streamer_page.status_code, 200)
            self.assertEqual(streamer_page.json()["total"], 13)
            self.assertEqual(
                streamer_page.json()["items"][0]["subscription_count"],
                2,
            )

            private_row = {
                **group_row,
                "id": group_row["id"] + 1,
                "target_type": "private",
                "target_id": 5001,
                "target_name": "",
            }
            guild_row = {
                **group_row,
                "id": group_row["id"] + 2,
                "target_type": "guild",
                "target_id": 6001,
                "target_name": "guild-a / channel-b",
                "guild_id": "guild-a",
                "channel_id": "channel-b",
            }
            export_rows = [group_row, private_row, guild_row]
            with patch(
                "haruka_bot.web.app._subscription_rows",
                new=AsyncMock(return_value=export_rows),
            ):
                json_export = client.get("/admin/api/export.json")
                self.assertEqual(json_export.status_code, 200)
                self.assertEqual(len(json_export.json()["subscriptions"]), 3)
                self.assertEqual(
                    {row["target_type"] for row in json_export.json()["subscriptions"]},
                    {"group", "private", "guild"},
                )

                csv_export = client.get("/admin/api/export.csv")
                self.assertEqual(csv_export.status_code, 200)
                self.assertTrue(csv_export.content.startswith(b"\xef\xbb\xbf"))
                self.assertIn('"网页,测试主播"'.encode("utf-8"), csv_export.content)
                self.assertIn("私聊".encode("utf-8"), csv_export.content)
                self.assertIn("频道".encode("utf-8"), csv_export.content)

            deleted = client.delete(
                f"/admin/api/subscriptions/{sub_id}",
                headers=headers,
            )
            self.assertEqual(deleted.status_code, 200)
            self.assertEqual(
                client.delete(
                    f"/admin/api/subscriptions/{private_sub_id}",
                    headers=headers,
                ).status_code,
                200,
            )
            self.assertEqual(
                client.delete(
                    f"/admin/api/subscriptions/{guild_sub_id}",
                    headers=headers,
                ).status_code,
                200,
            )

            missing = client.delete(
                f"/admin/api/subscriptions/{sub_id}",
                headers=headers,
            )
            self.assertEqual(missing.status_code, 404)

    def test_login_rate_limit(self):
        client = TestClient(nonebot.get_app())
        try:
            for _ in range(5):
                response = client.post(
                    "/admin/api/auth/login",
                    json={"password": "wrong"},
                )
                self.assertEqual(response.status_code, 401)
            limited = client.post(
                "/admin/api/auth/login",
                json={"password": "wrong"},
            )
            self.assertEqual(limited.status_code, 429)
            self.assertIn("Retry-After", limited.headers)
        finally:
            client.close()

    def test_login_failure_cache_is_bounded(self):
        now = time.monotonic()
        _login_failures.update(
            {f"192.0.2.{index}": [now] for index in range(LOGIN_MAX_TRACKED_IPS)}
        )
        _prune_login_failures("198.51.100.1")
        _login_failures["198.51.100.1"] = [now]
        self.assertLessEqual(len(_login_failures), LOGIN_MAX_TRACKED_IPS)


class RoomResolverTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        _avatar_cache.clear()

    def test_avatar_url_validation(self):
        self.assertEqual(
            _normalize_avatar_url("//i0.hdslb.com/bfs/face/avatar.jpg"),
            "https://i0.hdslb.com/bfs/face/avatar.jpg",
        )
        self.assertEqual(
            _normalize_avatar_url("http://i1.hdslb.com/bfs/face/avatar.jpg"),
            "https://i1.hdslb.com/bfs/face/avatar.jpg",
        )
        self.assertEqual(_normalize_avatar_url("https://example.com/avatar.jpg"), "")

    async def test_avatar_data_is_cached(self):
        avatar = (b"image-data", "image/jpeg")
        with patch(
            "haruka_bot.web.app._load_avatar",
            new=AsyncMock(return_value=avatar),
        ) as load_avatar:
            self.assertEqual(await _get_avatar_data(1001), avatar)
            self.assertEqual(await _get_avatar_data(1001), avatar)
        load_avatar.assert_awaited_once_with(1001)

    async def test_resolve_room_success(self):
        with patch(
            "haruka_bot.web.app.get",
            new=AsyncMock(
                return_value={
                    "uid": 1001,
                    "room_id": 3001,
                    "short_id": 42,
                }
            ),
        ), patch(
            "haruka_bot.web.app.get_user_info",
            new=AsyncMock(return_value={"card": {"name": "测试主播"}}),
        ):
            room = await _resolve_room(42)
        self.assertEqual(
            room,
            {
                "uid": 1001,
                "name": "测试主播",
                "room_id": 3001,
                "short_id": 42,
            },
        )

    async def test_resolve_uid_success(self):
        with patch(
            "haruka_bot.web.app.get_user_info",
            new=AsyncMock(
                return_value={
                    "card": {
                        "name": "UID测试主播",
                        "live_room": {"roomid": 3002},
                    }
                }
            ),
        ):
            room = await _resolve_uid(1002)
        self.assertEqual(
            room,
            {
                "uid": 1002,
                "name": "UID测试主播",
                "room_id": 3002,
                "short_id": 0,
            },
        )

    async def test_resolve_room_error_mapping(self):
        with patch(
            "haruka_bot.web.app.get",
            new=AsyncMock(return_value={}),
        ):
            with self.assertRaises(RoomResolveError) as missing:
                await _resolve_room(100)
        self.assertEqual(missing.exception.status_code, 422)

        for code, expected in ((-404, 422), (-412, 429), (-500, 502)):
            with self.subTest(code=code), patch(
                "haruka_bot.web.app.get",
                new=AsyncMock(
                    side_effect=ResponseCodeError(code, "test", {}),
                ),
            ):
                with self.assertRaises(RoomResolveError) as mapped:
                    await _resolve_room(100)
                self.assertEqual(mapped.exception.status_code, expected)

        with patch(
            "haruka_bot.web.app.get",
            new=AsyncMock(side_effect=RuntimeError("network unavailable")),
        ):
            with self.assertRaises(RoomResolveError) as upstream:
                await _resolve_room(100)
        self.assertEqual(upstream.exception.status_code, 502)

    async def test_resolve_room_timeout(self):
        async def slow_resolver(_room_id):
            await asyncio.sleep(ROOM_RESOLVE_TIMEOUT_SECONDS)

        with patch(
            "haruka_bot.web.app.ROOM_RESOLVE_TIMEOUT_SECONDS",
            0.001,
        ), patch(
            "haruka_bot.web.app._resolve_room_details",
            new=slow_resolver,
        ):
            with self.assertRaises(RoomResolveError) as timeout:
                await _resolve_room(100)
        self.assertEqual(timeout.exception.status_code, 502)
        self.assertIn("超时", timeout.exception.detail)


class SendResultTests(unittest.IsolatedAsyncioTestCase):
    async def test_strict_send_does_not_fallback(self):
        fallback_bot = SimpleNamespace(call_api=AsyncMock(return_value={"message_id": 1}))
        with patch(
            "haruka_bot.utils.nonebot.get_bots",
            return_value={"3002": fallback_bot},
        ):
            result = await safe_send(
                bot_id=3001,
                send_type="private",
                type_id=5001,
                message="test",
                allow_fallback=False,
            )
        self.assertFalse(result.success)
        self.assertEqual(result.code, "bot_offline")
        fallback_bot.call_api.assert_not_awaited()


class SubscriptionDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.old_dir = plugin_config.haruka_dir
        plugin_config.haruka_dir = self.temp_dir.name
        await db.init()

    async def asyncTearDown(self):
        await db.close()
        plugin_config.haruka_dir = self.old_dir
        self.temp_dir.cleanup()

    async def test_crud_duplicate_and_orphan_cleanup(self):
        concurrent = await asyncio.gather(
            *(
                db.add_sub(
                    uid=9001,
                    type="group",
                    type_id=9002,
                    bot_id=bot_id,
                    name="并发测试主播",
                    room_id=9003,
                    live=False,
                    dynamic=False,
                    at=False,
                )
                for bot_id in range(9100, 9108)
            )
        )
        self.assertEqual(concurrent.count(True), 1)
        self.assertEqual(concurrent.count(False), 7)

        first = await db.add_sub(
            uid=1001,
            type="group",
            type_id=2001,
            bot_id=3001,
            name="测试主播",
            room_id=4001,
            live=True,
            dynamic=False,
            at=False,
        )
        self.assertTrue(first)
        self.assertEqual(await db.get_uid_list("live"), [1001])

        duplicate = await db.add_sub(
            uid=1001,
            type="group",
            type_id=2001,
            bot_id=9999,
            name="测试主播",
            room_id=4001,
            live=True,
            dynamic=False,
            at=False,
        )
        self.assertFalse(duplicate)

        second = await db.add_sub(
            uid=1001,
            type="group",
            type_id=2002,
            bot_id=3001,
            name="测试主播",
            room_id=4001,
            live=True,
            dynamic=False,
            at=False,
        )
        self.assertTrue(second)

        sub = await db.get_sub(uid=1001, type="group", type_id=2001)
        await db.record_push_delivery(
            subscription_id=sub.id,
            attempted_at=123456,
            success=False,
            event_type="test",
            bot_id=3001,
            error_code="bot_offline",
            error_message="配置机器人未连接",
        )
        delivery = (await db.get_push_delivery_states())[sub.id]
        self.assertFalse(delivery.success)
        self.assertEqual(delivery.error_code, "bot_offline")
        await db.add_web_audit(
            action="test",
            target_ids=[sub.id],
            success=True,
            summary="数据库测试",
        )
        audits, audit_total = await db.get_web_audits(offset=0, limit=20)
        self.assertEqual(audit_total, 1)
        self.assertEqual(audits[0].action, "test")

        updated = await db.update_sub_by_id(
            sub.id,
            bot_id=3002,
            live=False,
            dynamic=True,
            at=True,
        )
        self.assertTrue(updated)
        changed = await db.get_sub_by_id(sub.id)
        self.assertEqual(changed.bot_id, 3002)
        self.assertFalse(changed.live)
        self.assertTrue(changed.dynamic)
        self.assertTrue(changed.at)

        await db.delete_sub_by_id(sub.id)
        self.assertNotIn(sub.id, await db.get_push_delivery_states())
        self.assertIsNotNone(await db.get_user(uid=1001))

        remaining = await db.get_sub(uid=1001, type="group", type_id=2002)
        await db.delete_sub_by_id(remaining.id)
        self.assertIsNone(await db.get_user(uid=1001))
        self.assertEqual(await db.get_uid_list("live"), [])


if __name__ == "__main__":
    unittest.main()
