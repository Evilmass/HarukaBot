import asyncio
import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import nonebot

nonebot.init()
nonebot.load_plugin("nonebot_plugin_guild_patch")

from haruka_bot.config import Config, plugin_config
from haruka_bot.video.service import video_service
from haruka_bot.video.bilibili import BiliVideoProvider, VideoReference
from haruka_bot.video.models import VideoSettings
from haruka_bot.plugins.bili_video import (
    BiliVideoDownloader,
    BiliVideoError,
    VideoInfo,
    _http_client_options,
    extract_message_urls,
    get_dash_stream_candidates,
    parse_video_url,
    resolve_video_references,
    select_dash_streams,
    send_video,
)

TEST_OUTPUT_DIR = Path(__file__).resolve().parents[1] / "test" / "bili_video"


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
        reference = parse_video_url("https://m.bilibili.com/video/av170001?p=invalid")
        self.assertEqual(reference.aid, 170001)
        self.assertEqual(reference.page, 1)

    async def test_resolve_short_link_and_deduplicate(self):
        request = httpx.Request("GET", "https://b23.tv/abc123")
        response = httpx.Response(
            200,
            request=request,
        )
        response._url = httpx.URL("https://www.bilibili.com/video/BV1xx411c7mD?p=2")
        client = AsyncMock(spec=httpx.AsyncClient)
        client.get.return_value = response

        result = await resolve_video_references(
            "https://b23.tv/abc123 https://www.bilibili.com/video/BV1xx411c7mD?p=2",
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


    async def test_all_qualities_exceed_limit_raises(self):
        """所有清晰度超过大小限制时抛出异常。"""
        video_high = {"id": 80, "baseUrl": "high", "codecs": "avc1"}
        video_low = {"id": 64, "baseUrl": "low", "codecs": "avc1"}
        audio = {"id": 30280, "baseUrl": "audio"}
        client = AsyncMock(spec=httpx.AsyncClient)
        downloader = BiliVideoDownloader(client)
        sizes = {
            "audio": 200 * 1024 * 1024,
            "high": 200 * 1024 * 1024,
            "low": 200 * 1024 * 1024,
        }
        downloader._probe_stream_size = AsyncMock(
            side_effect=lambda stream, referer: sizes[stream["baseUrl"]]
        )

        with self.assertRaises(BiliVideoError):
            await downloader._select_fitting_dash_streams(
                {
                    "dash": {
                        "video": [video_high, video_low],
                        "audio": [audio],
                    }
                },
                "https://www.bilibili.com/video/BV1xx411c7mD",
            )

    async def test_combined_audio_video_exceeds_limit_raises(self):
        """视频本身未超限，但合计音频大小超限时仍拒绝下载。"""
        video_high = {"id": 80, "baseUrl": "high", "codecs": "avc1"}
        video_low = {"id": 64, "baseUrl": "low", "codecs": "avc1"}
        audio = {"id": 30280, "baseUrl": "audio"}
        client = AsyncMock(spec=httpx.AsyncClient)
        downloader = BiliVideoDownloader(client)
        max_bytes = downloader.max_bytes
        sizes = {
            "audio": 20 * 1024 * 1024,
            "high": max_bytes,
            "low": max_bytes - 5 * 1024 * 1024,
        }
        downloader._probe_stream_size = AsyncMock(
            side_effect=lambda stream, referer: sizes[stream["baseUrl"]]
        )

        with self.assertRaises(BiliVideoError):
            await downloader._select_fitting_dash_streams(
                {
                    "dash": {
                        "video": [video_high, video_low],
                        "audio": [audio],
                    }
                },
                "https://www.bilibili.com/video/BV1xx411c7mD",
            )

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
        print(f"\n已保留真实下载视频：{video_path.resolve()} ({info.title})")


class BiliVideoSendTests(unittest.IsolatedAsyncioTestCase):
    async def asyncTearDown(self):
        await video_service.store.close()

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
        video_path = TEST_OUTPUT_DIR / "send-placeholder.mp4"
        video_path.write_bytes(b"video-content")
        return bot, event, info, video_path

    async def test_sends_video_via_forward_message(self):
        """send_video 使用 send_group_forward_msg 发送合并转发消息。"""
        bot, event, info, video_path = self._video_fixture()
        with patch.object(
            plugin_config,
            "haruka_bili_video_public_base_url",
            "http://192.168.31.131:7070",
        ):
            await send_video(bot, event, info, video_path)

        # 应调用 send_group_forward_msg 而非 send_group_msg 或 call_api
        bot.send_group_forward_msg.assert_awaited_once()
        bot.send_group_msg.assert_not_awaited()
        bot.call_api.assert_not_awaited()

        # 验证合并转发消息结构
        call_kwargs = bot.send_group_forward_msg.await_args.kwargs
        self.assertEqual(call_kwargs["group_id"], 123456)
        self.assertEqual(
            call_kwargs["_timeout"],
            Config().haruka_bili_video_timeout,
        )
        messages = call_kwargs["messages"]
        self.assertEqual(len(messages), 2)

        # 节点 1：描述
        node1 = messages[0]
        self.assertEqual(node1["type"], "node")
        self.assertEqual(node1["data"]["name"], "HarukaBot")
        self.assertEqual(node1["data"]["uin"], "10000")
        self.assertIn("title", node1["data"]["content"])
        self.assertIn("UP：owner", node1["data"]["content"])
        self.assertIn("01:05", node1["data"]["content"])
        self.assertIn("BV1xx411c7mD", node1["data"]["content"])

        # 节点 2：视频
        node2 = messages[1]
        self.assertEqual(node2["type"], "node")
        content2 = node2["data"]["content"]
        self.assertEqual(content2[0].type, "video")
        self.assertIn(
            "http://192.168.31.131:7070/haruka/video/files/",
            content2[0].data["file"],
        )

    async def test_forward_message_includes_multi_page_label(self):
        """多分 P 视频的描述中包含分 P 信息。"""
        bot, event, _, video_path = self._video_fixture()
        info = VideoInfo(
            bvid="BV1xx411c7mD",
            title="title",
            owner="owner",
            page_name="第二章",
            page_number=2,
            page_count=5,
            cid=1,
            duration=120,
        )
        with patch.object(
            plugin_config,
            "haruka_bili_video_public_base_url",
            "http://192.168.31.131:7070",
        ):
            await send_video(bot, event, info, video_path)

        node1_content = bot.send_group_forward_msg.await_args.kwargs["messages"][0][
            "data"
        ]["content"]
        self.assertIn("P2", node1_content)
        self.assertIn("第二章", node1_content)


    async def test_send_video_failure_propagates(self):
        """合并转发发送失败时异常正确传播。"""
        bot, event, info, video_path = self._video_fixture()
        bot.send_group_forward_msg.side_effect = RuntimeError("forward failed")
        with patch.object(
            plugin_config,
            "haruka_bili_video_public_base_url",
            "http://192.168.31.131:7070",
        ):
            with self.assertRaisesRegex(RuntimeError, "forward failed"):
                await send_video(bot, event, info, video_path)


class BiliVideoPipelineTests(unittest.IsolatedAsyncioTestCase):
    async def test_av_reference_is_canonicalized_for_bv_deduplication(self):
        def handler(request):
            return httpx.Response(200, json={"code": 0, "data": {
                "bvid": "BV1xx411c7mD", "title": "title", "owner": {"name": "owner"},
                "pages": [{"cid": 100, "part": "part", "duration": 65}],
            }})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = BiliVideoProvider(client, VideoSettings(None, 90, 3, 2, "ffmpeg", 30))
            av = await provider.resolve("https://www.bilibili.com/video/av123")
            bv = await provider.resolve("https://www.bilibili.com/video/BV1xx411c7mD")
            self.assertEqual(av.key, bv.key)
            self.assertEqual((await provider.downloader.get_video_info(av)).cid, 100)

    async def test_ffmpeg_merge_uses_both_inputs_and_copy_codec(self):
        client = AsyncMock(spec=httpx.AsyncClient)
        downloader = BiliVideoDownloader(client)
        TEST_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        output = TEST_OUTPUT_DIR / "merged-video.mp4"
        process = MagicMock()
        process.returncode = 0
        async def communicate():
            output.write_bytes(b"merged-video")
            return b"", b""
        process.communicate = AsyncMock(side_effect=communicate)
        with patch("haruka_bot.video.bilibili.asyncio.create_subprocess_exec", AsyncMock(return_value=process)) as create:
            await downloader._run_ffmpeg([Path("video.m4s"), Path("audio.m4s")], output)
        args = create.await_args.args
        self.assertEqual(args.count("-i"), 2)
        self.assertEqual(args[args.index("-c") + 1], "copy")
        self.assertEqual(args[args.index("-movflags") + 1], "+faststart")
        self.assertEqual(output.read_bytes(), b"merged-video")

    async def test_ffmpeg_missing_and_timeout_are_safe_errors(self):
        downloader = BiliVideoDownloader(AsyncMock(spec=httpx.AsyncClient))
        with patch("haruka_bot.video.bilibili.asyncio.create_subprocess_exec", AsyncMock(side_effect=FileNotFoundError)):
            with self.assertRaisesRegex(BiliVideoError, "未找到 FFmpeg"):
                await downloader._run_ffmpeg([], Path("unused.mp4"))
        process = MagicMock()
        process.returncode = None
        process.communicate = AsyncMock(return_value=(b"", b""))
        async def timeout(awaitable, **kwargs):
            awaitable.close()
            raise asyncio.TimeoutError
        with patch("haruka_bot.video.bilibili.asyncio.create_subprocess_exec", AsyncMock(return_value=process)), patch("haruka_bot.video.bilibili.asyncio.wait_for", side_effect=timeout):
            with self.assertRaisesRegex(BiliVideoError, "超时"):
                await downloader._run_ffmpeg([], Path("unused.mp4"))
        process.kill.assert_called_once()

    async def test_cancelling_ffmpeg_kills_and_reaps_process(self):
        downloader = BiliVideoDownloader(AsyncMock(spec=httpx.AsyncClient))
        process = MagicMock()
        process.returncode = None
        started = asyncio.Event()
        async def communicate():
            started.set()
            if not process.kill.called:
                await asyncio.Event().wait()
            return b"", b""
        process.communicate = AsyncMock(side_effect=communicate)
        with patch("haruka_bot.video.bilibili.asyncio.create_subprocess_exec", AsyncMock(return_value=process)):
            task = asyncio.create_task(downloader._run_ffmpeg([], Path("unused.mp4")))
            await started.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        process.kill.assert_called_once()





if __name__ == "__main__":
    unittest.main()
