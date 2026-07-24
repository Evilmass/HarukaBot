import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import nonebot

nonebot.init()
nonebot.load_plugin("nonebot_plugin_guild_patch")

from bilireq.grpc.protos.bilibili.app.dynamic.v2.dynamic_pb2 import DynamicType

from haruka_bot.plugins.pusher import dynamic_pusher


def make_dynamic(dynamic_id: int, card_type=DynamicType.word):
    return SimpleNamespace(
        extend=SimpleNamespace(dyn_id_str=str(dynamic_id)),
        card_type=card_type,
        modules=[
            SimpleNamespace(
                module_author=SimpleNamespace(
                    author=SimpleNamespace(name="测试 UP"),
                ),
            ),
        ],
    )


class DynamicPusherTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.old_offset = dict(dynamic_pusher.offset)
        dynamic_pusher.offset.clear()
        self.uid = 123456

    def tearDown(self):
        dynamic_pusher.offset.clear()
        dynamic_pusher.offset.update(self.old_offset)

    def scheduler_patches(self, dynamics):
        return (
            patch.object(
                dynamic_pusher.db,
                "next_uid",
                new=AsyncMock(return_value=self.uid),
            ),
            patch.object(
                dynamic_pusher.db,
                "get_user",
                new=AsyncMock(return_value=SimpleNamespace(name="旧昵称")),
            ),
            patch.object(
                dynamic_pusher.db,
                "update_user",
                new=AsyncMock(),
            ),
            patch.object(
                dynamic_pusher._bili_dynamic,
                "grpc_get_user_dynamics",
                new=AsyncMock(return_value=SimpleNamespace(list=dynamics)),
            ),
        )

    async def test_first_poll_uses_latest_id_without_sending(self):
        dynamic_pusher.offset[self.uid] = -1
        dynamics = [make_dynamic(105), make_dynamic(101), make_dynamic(104)]
        screenshot = AsyncMock()
        patches = self.scheduler_patches(dynamics)

        with patches[0], patches[1], patches[2], patches[3], patch.object(
            dynamic_pusher,
            "get_dynamic_screenshot",
            new=screenshot,
        ):
            await dynamic_pusher.dy_sched()

        self.assertEqual(dynamic_pusher.offset[self.uid], 105)
        screenshot.assert_not_awaited()

    async def test_default_limit_only_processes_latest_dynamic(self):
        dynamic_pusher.offset[self.uid] = 100
        dynamics = [make_dynamic(101), make_dynamic(103), make_dynamic(102)]
        screenshot = AsyncMock(return_value=(None, None))
        patches = self.scheduler_patches(dynamics)

        with patches[0], patches[1], patches[2], patches[3], patch.object(
            dynamic_pusher,
            "get_dynamic_screenshot",
            new=screenshot,
        ), patch.object(
            dynamic_pusher.plugin_config,
            "haruka_dynamic_max_push_per_poll",
            1,
        ):
            await dynamic_pusher.dy_sched()

        screenshot.assert_awaited_once_with(103)
        self.assertEqual(dynamic_pusher.offset[self.uid], 103)

    async def test_bounded_backfill_processes_recent_items_oldest_first(self):
        dynamic_pusher.offset[self.uid] = 100
        dynamics = [
            make_dynamic(105),
            make_dynamic(101),
            make_dynamic(104),
            make_dynamic(103),
            make_dynamic(102),
        ]
        screenshot = AsyncMock(return_value=(None, None))
        patches = self.scheduler_patches(dynamics)

        with patches[0], patches[1], patches[2], patches[3], patch.object(
            dynamic_pusher,
            "get_dynamic_screenshot",
            new=screenshot,
        ), patch.object(
            dynamic_pusher.plugin_config,
            "haruka_dynamic_max_push_per_poll",
            3,
        ):
            await dynamic_pusher.dy_sched()

        self.assertEqual(
            [call.args[0] for call in screenshot.await_args_list],
            [103, 104, 105],
        )
        self.assertEqual(dynamic_pusher.offset[self.uid], 105)

    async def test_ignored_dynamic_advances_offset(self):
        dynamic_pusher.offset[self.uid] = 100
        dynamics = [make_dynamic(101, DynamicType.live)]
        screenshot = AsyncMock(return_value=(b"image", None))
        patches = self.scheduler_patches(dynamics)

        with patches[0], patches[1], patches[2], patches[3], patch.object(
            dynamic_pusher,
            "get_dynamic_screenshot",
            new=screenshot,
        ):
            await dynamic_pusher.dy_sched()

        self.assertEqual(dynamic_pusher.offset[self.uid], 101)

    async def test_empty_first_poll_sets_zero_offset(self):
        dynamic_pusher.offset[self.uid] = -1
        patches = self.scheduler_patches([])

        with patches[0], patches[1], patches[2], patches[3]:
            await dynamic_pusher.dy_sched()

        self.assertEqual(dynamic_pusher.offset[self.uid], 0)

    async def test_none_response_keeps_current_offset(self):
        dynamic_pusher.offset[self.uid] = 100

        with patch.object(
            dynamic_pusher.db,
            "next_uid",
            new=AsyncMock(return_value=self.uid),
        ), patch.object(
            dynamic_pusher.db,
            "get_user",
            new=AsyncMock(return_value=SimpleNamespace(name="旧昵称")),
        ), patch.object(
            dynamic_pusher._bili_dynamic,
            "grpc_get_user_dynamics",
            new=AsyncMock(return_value=None),
        ):
            await dynamic_pusher.dy_sched()

        self.assertEqual(dynamic_pusher.offset[self.uid], 100)


if __name__ == "__main__":
    unittest.main()
