import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import nonebot

nonebot.init()
nonebot.load_plugin("nonebot_plugin_guild_patch")

from haruka_bot.config import Config
from haruka_bot.plugins.bili_video import (
    BiliVideoDownloader,
    VideoInfo,
    extract_message_urls,
    parse_video_url,
    resolve_video_references,
    send_forward_video,
    select_dash_streams,
)


class BiliVideoConfigTests(unittest.TestCase):
    def test_groups_accept_json_and_delimited_values(self):
        self.assertEqual(
            Config(haruka_bili_video_groups="[123, 456]").haruka_bili_video_groups,
            [123, 456],
        )
        self.assertEqual(
            Config(haruka_bili_video_groups="123, 456 789").haruka_bili_video_groups,
            [123, 456, 789],
        )

    def test_groups_accept_delimited_environment_variable(self):
        with patch.dict(
            os.environ,
            {"HARUKA_BILI_VIDEO_GROUPS": "123,456 789"},
        ):
            groups = Config().haruka_bili_video_groups
        self.assertEqual(groups, [123, 456, 789])


class BiliVideoUrlTests(unittest.IsolatedAsyncioTestCase):
    def test_extract_and_parse_video_links(self):
        text = (
            "第一个 https://www.bilibili.com/video/BV1xx411c7mD?p=2，"
            "短链 https://b23.tv/abc123。"
        )
        self.assertEqual(
            extract_message_urls(text),
            [
                "https://www.bilibili.com/video/BV1xx411c7mD?p=2",
                "https://b23.tv/abc123",
            ],
        )
        reference = parse_video_url(extract_message_urls(text)[0])
        self.assertEqual(reference.bvid, "BV1xx411c7mD")
        self.assertEqual(reference.page, 2)

    def test_parse_av_link(self):
        reference = parse_video_url(
            "https://m.bilibili.com/video/av170001?p=invalid"
        )
        self.assertEqual(reference.aid, 170001)
        self.assertEqual(reference.page, 1)

    async def test_resolve_short_link_and_deduplicate(self):
        request = httpx.Request("GET", "https://b23.tv/abc123")
        response = httpx.Response(
            200,
            request=request,
        )
        response._url = httpx.URL(
            "https://www.bilibili.com/video/BV1xx411c7mD?p=2"
        )
        client = AsyncMock(spec=httpx.AsyncClient)
        client.get.return_value = response

        result = await resolve_video_references(
            "https://b23.tv/abc123 "
            "https://www.bilibili.com/video/BV1xx411c7mD?p=2",
            client,
        )

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].key, ("BV1xx411c7mD", 2))


class BiliVideoStreamTests(unittest.TestCase):
    def test_selects_highest_allowed_avc_and_best_audio(self):
        video_hevc = {
            "id": 80,
            "codecs": "hev1.1.6.L120.90",
            "bandwidth": 4000,
            "baseUrl": "hevc",
        }
        video_avc = {
            "id": 80,
            "codecs": "avc1.640032",
            "bandwidth": 3000,
            "baseUrl": "avc",
        }
        video_4k = {
            "id": 120,
            "codecs": "avc1.640033",
            "bandwidth": 8000,
            "baseUrl": "4k",
        }
        audio_low = {"id": 30216, "bandwidth": 64000, "baseUrl": "low"}
        audio_high = {"id": 30280, "bandwidth": 192000, "baseUrl": "high"}

        video, audio = select_dash_streams(
            {
                "dash": {
                    "video": [video_hevc, video_avc, video_4k],
                    "audio": [audio_low, audio_high],
                }
            },
            80,
        )

        self.assertIs(video, video_avc)
        self.assertIs(audio, audio_high)

    def test_video_info_builds_multi_page_url(self):
        info = VideoInfo(
            bvid="BV1xx411c7mD",
            title="title",
            owner="owner",
            page_name="part",
            page_number=2,
            page_count=3,
            cid=1,
            duration=10,
        )
        self.assertEqual(
            info.canonical_url,
            "https://www.bilibili.com/video/BV1xx411c7mD?p=2",
        )


class BiliVideoDownloadTests(unittest.IsolatedAsyncioTestCase):
    async def test_media_download_uses_range_header(self):
        async def handler(request):
            self.assertEqual(request.headers["range"], "bytes=0-")
            return httpx.Response(
                206,
                headers={
                    "content-length": "5",
                    "content-range": "bytes 0-4/5",
                },
                content=b"video",
            )

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            downloader = BiliVideoDownloader(client)
            with tempfile.TemporaryDirectory() as directory:
                target = Path(directory, "video.m4s")
                size = await downloader._download_stream(
                    {"baseUrl": "https://cdn.example/video.m4s"},
                    target,
                )
                self.assertEqual(size, 5)
                self.assertEqual(target.read_bytes(), b"video")


class BiliVideoForwardTests(unittest.IsolatedAsyncioTestCase):
    async def test_sends_video_in_forward_node(self):
        bot = AsyncMock()
        bot.self_id = "10000"
        event = SimpleNamespace(group_id=123456)
        info = VideoInfo(
            bvid="BV1xx411c7mD",
            title="title",
            owner="owner",
            page_name="part",
            page_number=1,
            page_count=1,
            cid=1,
            duration=65,
        )
        with tempfile.TemporaryDirectory() as directory:
            video_path = Path(directory, "video.mp4")
            video_path.touch()
            await send_forward_video(bot, event, info, video_path)

        call = bot.send_group_forward_msg.await_args
        self.assertEqual(call.kwargs["group_id"], 123456)
        messages = call.kwargs["messages"]
        self.assertEqual(len(messages), 2)
        video = messages[1]["data"]["content"][0]
        self.assertEqual(video.type, "video")
        self.assertTrue(video.data["file"].startswith("file:///"))


if __name__ == "__main__":
    unittest.main()
