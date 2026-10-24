import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import httpx
import nonebot
from nonebot.plugin import get_plugin

nonebot.init()
if get_plugin("nonebot_plugin_guild_patch") is None:
    nonebot.load_plugin("nonebot_plugin_guild_patch")

from bilireq.exceptions import ResponseCodeError

from haruka_bot.plugins.pusher import live_pusher


def make_room(**kwargs):
    room = {
        "live_status": 0,
        "uname": "测试主播",
        "title": "",
        "area_v2_name": "",
        "area_v2_parent_name": "",
        "cover_from_user": "",
        "keyframe": "",
        "short_id": 0,
        "room_id": 1,
    }
    room.update(kwargs)
    return room


class LivePusherTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        live_pusher.status.clear()
        live_pusher.live_time.clear()
        live_pusher.live_snapshot.clear()
        self.logger = Mock()
        self.logger_patch = patch.object(live_pusher, "logger", self.logger)
        self.logger_patch.start()
        self.addCleanup(self.logger_patch.stop)

        self.uids_patch = patch.object(
            live_pusher.db,
            "get_all_subscription_uids",
            new=AsyncMock(return_value=[123]),
        )
        self.uids_patch.start()
        self.addCleanup(self.uids_patch.stop)

        self.close_session_patch = patch.object(
            live_pusher.db,
            "close_live_session",
            new=AsyncMock(),
        )
        self.close_session_patch.start()
        self.addCleanup(self.close_session_patch.stop)

    async def test_connect_timeout_is_retried_next_poll(self):
        request = httpx.Request("GET", "https://data.bilibili.com/v/")
        error = httpx.ConnectTimeout("", request=request)
        with patch.object(
            live_pusher,
            "get_rooms_info_by_uids",
            new=AsyncMock(side_effect=error),
        ):
            await live_pusher.live_sched()

        self.assertEqual(live_pusher.status, {})
        self.assertEqual(live_pusher.live_snapshot, {})
        warning = self.logger.warning.call_args[0][0]
        self.assertIn("ConnectTimeout", warning)
        self.assertIn("https://data.bilibili.com/v/", warning)

    async def test_response_code_error_is_retried_next_poll(self):
        error = ResponseCodeError(code=-352, msg="风控校验失败", data=None)
        with patch.object(
            live_pusher,
            "get_rooms_info_by_uids",
            new=AsyncMock(side_effect=error),
        ):
            await live_pusher.live_sched()

        self.assertEqual(live_pusher.status, {})
        self.assertIn("-352", self.logger.warning.call_args[0][0])

    async def test_offline_room_only_closes_persisted_session(self):
        with patch.object(
            live_pusher,
            "get_rooms_info_by_uids",
            new=AsyncMock(return_value={"123": make_room()}),
        ):
            await live_pusher.live_sched()

        self.assertEqual(live_pusher.status["123"], 0)
        self.assertEqual(live_pusher.live_snapshot["123"]["status"], 0)
        live_pusher.db.close_live_session.assert_awaited_once()

    async def test_live_start_pushes_to_every_subscription(self):
        room = make_room(
            live_status=1,
            title="直播标题",
            area_v2_name="虚拟主播",
            area_v2_parent_name="虚拟",
            cover_from_user="https://i0.hdslb.com/cover.jpg",
            room_id=456,
        )
        subscription = SimpleNamespace(
            id=7,
            bot_id=1000,
            type="group",
            type_id=2000,
            at=True,
        )
        safe_send = AsyncMock()
        with patch.object(
            live_pusher,
            "get_rooms_info_by_uids",
            new=AsyncMock(
                side_effect=[
                    {"123": make_room()},
                    {"123": room},
                ]
            ),
        ), patch.object(
            live_pusher.db,
            "observe_live_duration",
            new=AsyncMock(),
        ), patch.object(
            live_pusher.db,
            "get_push_list",
            new=AsyncMock(return_value=[subscription]),
        ), patch.object(
            live_pusher.db,
            "update_user",
            new=AsyncMock(),
        ), patch.object(
            live_pusher,
            "safe_send",
            new=safe_send,
        ):
            await live_pusher.live_sched()  # 首次观察只记录状态
            self.assertFalse(safe_send.await_count)
            await live_pusher.live_sched()  # 状态变化后推送

        self.assertEqual(live_pusher.status["123"], 1)
        safe_send.assert_awaited_once()
        kwargs = safe_send.await_args.kwargs
        self.assertEqual(kwargs["subscription_id"], 7)
        self.assertEqual(kwargs["event_type"], "live_start")
        self.assertIn(
            "https://live.bilibili.com/456",
            kwargs["message"].extract_plain_text(),
        )
