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
from haruka_bot.web.app import (
    CSRF_COOKIE,
    LOGIN_MAX_TRACKED_IPS,
    ROOM_RESOLVE_TIMEOUT_SECONDS,
    RoomResolveError,
    _avatar_cache,
    _bot_cache,
    _create_session,
    _extract_room_id,
    _get_avatar_data,
    _login_failures,
    _normalize_avatar_url,
    _prune_login_failures,
    _read_session,
    _resolve_room,
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

    def test_room_id_parser(self):
        self.assertEqual(_extract_room_id("12345"), 12345)
        self.assertEqual(
            _extract_room_id("https://live.bilibili.com/67890?broadcast_type=0"),
            67890,
        )
        self.assertEqual(_extract_room_id("live.bilibili.com/blanc/24680"), 24680)
        with self.assertRaises(RoomResolveError):
            _extract_room_id("https://www.bilibili.com/video/BV1invalid")

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
            ):
                created = client.post(
                    "/admin/api/subscriptions",
                    headers=headers,
                    json={
                        "room": "4001",
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
                        "room": "4001",
                        "target_id": 2001,
                        "bot_id": 9999,
                    },
                )
                self.assertEqual(duplicate.status_code, 409)

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

            listing = client.get(
                "/admin/api/subscriptions",
                params={
                    "q": "网页",
                    "target_type": "group",
                    "live_enabled": "false",
                },
            )
            self.assertEqual(listing.status_code, 200)
            self.assertEqual(listing.json()["total"], 1)
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
            room = await _resolve_room("https://live.bilibili.com/42")
        self.assertEqual(
            room,
            {
                "uid": 1001,
                "name": "测试主播",
                "room_id": 3001,
                "short_id": 42,
            },
        )

    async def test_resolve_room_error_mapping(self):
        with patch(
            "haruka_bot.web.app.get",
            new=AsyncMock(return_value={}),
        ):
            with self.assertRaises(RoomResolveError) as missing:
                await _resolve_room("100")
        self.assertEqual(missing.exception.status_code, 422)

        for code, expected in ((-404, 422), (-412, 429), (-500, 502)):
            with self.subTest(code=code), patch(
                "haruka_bot.web.app.get",
                new=AsyncMock(
                    side_effect=ResponseCodeError(code, "test", {}),
                ),
            ):
                with self.assertRaises(RoomResolveError) as mapped:
                    await _resolve_room("100")
                self.assertEqual(mapped.exception.status_code, expected)

        with patch(
            "haruka_bot.web.app.get",
            new=AsyncMock(side_effect=RuntimeError("network unavailable")),
        ):
            with self.assertRaises(RoomResolveError) as upstream:
                await _resolve_room("100")
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
                await _resolve_room("100")
        self.assertEqual(timeout.exception.status_code, 502)
        self.assertIn("超时", timeout.exception.detail)


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
        self.assertIsNotNone(await db.get_user(uid=1001))

        remaining = await db.get_sub(uid=1001, type="group", type_id=2002)
        await db.delete_sub_by_id(remaining.id)
        self.assertIsNone(await db.get_user(uid=1001))
        self.assertEqual(await db.get_uid_list("live"), [])


if __name__ == "__main__":
    unittest.main()
