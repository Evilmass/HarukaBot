import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from urllib.parse import urlparse

import httpx
import nonebot
from fastapi import HTTPException

nonebot.init()
nonebot.load_plugin("nonebot_plugin_guild_patch")

from haruka_bot.config import Config, plugin_config
from haruka_bot.plugins.bili_video import (
    VIDEO_DOWNLOAD_ROUTE,
    BiliVideoError,
    BiliVideoDownloader,
    VideoInfo,
    _http_client_options,
    _serve_temporary_video,
    _temporary_video_files,
    _temporary_video_url,
    extract_message_urls,
    get_dash_stream_candidates,
    parse_video_url,
    resolve_video_references,
    send_forward_video,
    select_dash_streams,
)

TEST_OUTPUT_DIR = (
    Path(__file__).resolve().parents[1] / "test" / "bili_video"
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

    def test_public_base_url_is_optional(self):
        self.assertIsNone(Config().haruka_bili_video_public_base_url)
        self.assertEqual(
            Config(
                haruka_bili_video_public_base_url="http://192.168.31.131:7070"
            ).haruka_bili_video_public_base_url,
            "http://192.168.31.131:7070",
        )


class BiliVideoTemporaryHttpTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        TEST_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        self.video_path = TEST_OUTPUT_DIR / "temporary-http-video.mp4"
        self.video_path.write_bytes(b"temporary-video-content")
        _temporary_video_files.clear()

    def tearDown(self):
        _temporary_video_files.clear()

    async def test_temporary_url_serves_video_then_expires(self):
        with patch.object(
            plugin_config,
            "haruka_bili_video_public_base_url",
            "http://192.168.31.131:7070",
        ):
            with _temporary_video_url(self.video_path) as video_url:
                request_path = urlparse(video_url).path
                token = request_path.split("/")[-2]
                response = await _serve_temporary_video(token)
                sent_messages = []

                async def receive():
                    return {"type": "http.request", "body": b"", "more_body": False}

                async def send(message):
                    sent_messages.append(message)

                await response(
                    {
                        "type": "http",
                        "method": "GET",
                        "path": request_path,
                        "headers": [],
                    },
                    receive,
                    send,
                )
                body = b"".join(
                    message.get("body", b"") for message in sent_messages
                )
                self.assertEqual(body, b"temporary-video-content")
                self.assertEqual(response.media_type, "video/mp4")
                self.assertEqual(response.headers["cache-control"], "no-store")
            with self.assertRaises(HTTPException) as expired:
                await _serve_temporary_video(token)
            self.assertEqual(expired.exception.status_code, 404)

    async def test_unknown_token_returns_not_found(self):
        with self.assertRaises(HTTPException) as unknown:
            await _serve_temporary_video("unknown-token")
        self.assertEqual(unknown.exception.status_code, 404)
        self.assertIn(
            VIDEO_DOWNLOAD_ROUTE,
            {route.path for route in nonebot.get_app().routes},
        )


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

        videos, _ = get_dash_stream_candidates(
            {
                "dash": {
                    "video": [video_hevc, video_avc, video_4k],
                    "audio": [audio_low, audio_high],
                }
            },
            120,
        )
        self.assertEqual([item["id"] for item in videos], [120, 80])

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
            self.assertEqual(
                request.headers["referer"],
                "https://www.bilibili.com/video/BV1xx411c7mD",
            )
            self.assertNotIn("cookie", request.headers)
            self.assertNotIn("Mobile", request.headers["user-agent"])
            return httpx.Response(
                206,
                headers={
                    "content-length": "5",
                    "content-range": "bytes 0-4/5",
                },
                content=b"video",
            )

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(
            transport=transport,
            headers={
                "Cookie": "SESSDATA=secret",
                "User-Agent": "Mobile test client",
            },
        ) as client:
            downloader = BiliVideoDownloader(client)
            TEST_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
            target = TEST_OUTPUT_DIR / "mock-video.m4s"
            size = await downloader._download_stream(
                {"baseUrl": "https://cdn.example/video.m4s"},
                target,
                "https://www.bilibili.com/video/BV1xx411c7mD",
            )
            self.assertEqual(size, 5)
            self.assertEqual(target.read_bytes(), b"video")

    async def test_size_probe_uses_single_byte_range(self):
        async def handler(request):
            self.assertEqual(request.headers["range"], "bytes=0-0")
            self.assertEqual(
                request.headers["referer"],
                "https://www.bilibili.com/video/BV1xx411c7mD?p=2",
            )
            return httpx.Response(
                206,
                headers={
                    "content-length": "1",
                    "content-range": "bytes 0-0/12345",
                },
                content=b"v",
            )

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            downloader = BiliVideoDownloader(client)
            size = await downloader._probe_stream_size(
                {"baseUrl": "https://cdn.example/video.m4s"},
                "https://www.bilibili.com/video/BV1xx411c7mD?p=2",
            )
        self.assertEqual(size, 12345)

    async def test_oversized_high_quality_is_downgraded(self):
        video_high = {"id": 80, "baseUrl": "high", "codecs": "avc1"}
        video_low = {"id": 64, "baseUrl": "low", "codecs": "avc1"}
        audio = {"id": 30280, "baseUrl": "audio"}
        client = AsyncMock(spec=httpx.AsyncClient)
        downloader = BiliVideoDownloader(client)
        sizes = {
            "audio": 5 * 1024 * 1024,
            "high": downloader.max_bytes,
            "low": 50 * 1024 * 1024,
        }
        downloader._probe_stream_size = AsyncMock(
            side_effect=lambda stream, referer: sizes[stream["baseUrl"]]
        )

        video, selected_audio = await downloader._select_fitting_dash_streams(
            {
                "dash": {
                    "video": [video_high, video_low],
                    "audio": [audio],
                }
            },
            "https://www.bilibili.com/video/BV1xx411c7mD",
        )

        self.assertIs(video, video_low)
        self.assertIs(selected_audio, audio)

    @unittest.skipUnless(
        os.getenv("HARUKA_TEST_BILI_VIDEO_URL"),
        "pass -u VIDEO_URL to run the real download test",
    )
    async def test_real_video_download_is_retained(self):
        url = os.environ["HARUKA_TEST_BILI_VIDEO_URL"]
        output_dir = TEST_OUTPUT_DIR / "real"
        output_dir.mkdir(parents=True, exist_ok=True)

        async with httpx.AsyncClient(**_http_client_options()) as client:
            references = await resolve_video_references(url, client)
            self.assertTrue(references, f"无法识别 B 站视频链接：{url}")
            info, video_path = await BiliVideoDownloader(client).download(
                references[0], output_dir
            )

        self.assertTrue(video_path.is_file())
        self.assertGreater(video_path.stat().st_size, 0)
        print(
            f"\n已保留真实下载视频：{video_path.resolve()} "
            f"({info.title})"
        )


class BiliVideoForwardTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        _temporary_video_files.clear()

    def tearDown(self):
        _temporary_video_files.clear()

    def _video_fixture(self):
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
        TEST_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        video_path = TEST_OUTPUT_DIR / "forward-placeholder.mp4"
        video_path.write_bytes(b"video-content")
        return bot, event, info, video_path

    async def test_sends_napcat_local_video_in_forward_node(self):
        bot, event, info, video_path = self._video_fixture()
        napcat_path = "/app/.config/QQ/NapCat/temp/video.mp4"
        bot.call_api.return_value = {"file": napcat_path}
        with patch.object(
            plugin_config,
            "haruka_bili_video_public_base_url",
            "http://192.168.31.131:7070",
        ):
            await send_forward_video(bot, event, info, video_path)

        download_call = bot.call_api.await_args
        self.assertEqual(download_call.args, ("download_file",))
        self.assertTrue(
            download_call.kwargs["url"].startswith(
                "http://192.168.31.131:7070/haruka/bili-video/"
            )
        )
        self.assertEqual(download_call.kwargs["thread_count"], 1)
        self.assertEqual(
            download_call.kwargs["_timeout"],
            Config().haruka_bili_video_timeout,
        )
        call = bot.send_group_forward_msg.await_args
        self.assertEqual(call.kwargs["group_id"], 123456)
        self.assertEqual(
            call.kwargs["_timeout"],
            Config().haruka_bili_video_timeout,
        )
        messages = call.kwargs["messages"]
        self.assertEqual(len(messages), 2)
        video = messages[1]["data"]["content"][0]
        self.assertEqual(video.type, "video")
        self.assertEqual(video.data["file"], napcat_path)
        self.assertFalse(_temporary_video_files)

    async def test_missing_napcat_path_revokes_temporary_url(self):
        bot, event, info, video_path = self._video_fixture()
        bot.call_api.return_value = {}
        with patch.object(
            plugin_config,
            "haruka_bili_video_public_base_url",
            "http://192.168.31.131:7070",
        ):
            with self.assertRaisesRegex(BiliVideoError, "没有返回本地文件路径"):
                await send_forward_video(bot, event, info, video_path)
        bot.send_group_forward_msg.assert_not_awaited()
        self.assertFalse(_temporary_video_files)

    async def test_download_failure_revokes_temporary_url(self):
        bot, event, info, video_path = self._video_fixture()
        bot.call_api.side_effect = RuntimeError("download failed")
        with patch.object(
            plugin_config,
            "haruka_bili_video_public_base_url",
            "http://192.168.31.131:7070",
        ):
            with self.assertRaisesRegex(RuntimeError, "download failed"):
                await send_forward_video(bot, event, info, video_path)
        self.assertFalse(_temporary_video_files)

    async def test_forward_failure_leaves_no_temporary_url(self):
        bot, event, info, video_path = self._video_fixture()
        bot.call_api.return_value = {"file": "/tmp/video.mp4"}
        bot.send_group_forward_msg.side_effect = RuntimeError("forward failed")
        with patch.object(
            plugin_config,
            "haruka_bili_video_public_base_url",
            "http://192.168.31.131:7070",
        ):
            with self.assertRaisesRegex(RuntimeError, "forward failed"):
                await send_forward_video(bot, event, info, video_path)
        self.assertFalse(_temporary_video_files)


if __name__ == "__main__":
    unittest.main()
