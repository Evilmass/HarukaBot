import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

import nonebot
from nonebot.adapters.onebot.v11 import Message, MessageSegment
from nonebot.plugin import get_plugin

nonebot.init()
if get_plugin("nonebot_plugin_guild_patch") is None:
    nonebot.load_plugin("nonebot_plugin_guild_patch")

from haruka_bot.plugins.save_sticker import handle_face_extraction


class SaveStickerTests(unittest.IsolatedAsyncioTestCase):
    async def test_reply_image_is_sent_back(self):
        event = SimpleNamespace(
            reply=SimpleNamespace(
                message=Message(
                    MessageSegment.image(
                        "https://gchat.qpic.cn/example/sticker.gif"
                    )
                )
            )
        )
        bot = SimpleNamespace(send=AsyncMock())

        await handle_face_extraction(bot, event)

        bot.send.assert_awaited_once()
        sent_message = bot.send.await_args.args[1]
        self.assertEqual(
            [segment.type for segment in sent_message],
            ["text", "image", "text"],
        )
        self.assertIn("https://pic.colanns.me/example/sticker.gif", str(sent_message))

    async def test_image_file_is_used_when_url_is_missing(self):
        event = SimpleNamespace(
            reply=SimpleNamespace(
                message=Message(
                    [
                        MessageSegment(
                            type="image",
                            data={"file": "sticker-file-id"},
                        )
                    ]
                )
            )
        )
        bot = SimpleNamespace(send=AsyncMock())

        await handle_face_extraction(bot, event)

        sent_message = bot.send.await_args.args[1]
        self.assertEqual(sent_message[1].data["file"], "sticker-file-id")


if __name__ == "__main__":
    unittest.main()
