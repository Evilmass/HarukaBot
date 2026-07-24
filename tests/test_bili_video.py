import asyncio
import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import nonebot
from fastapi import HTTPException

nonebot.init()
nonebot.load_plugin("nonebot_plugin_guild_patch")

from haruka_bot.config import Config, plugin_config
from haruka_bot.plugins.bili_video import (
    VIDEO_SERVE_PREFIX,
    BiliVideoDownloader,
    BiliVideoError,
    VideoInfo,
    _build_video_url,
    _http_client_options,
    _serve_video_file,
    _set_video_serve_dir,
    _video_serve_dir,
    compress_video,
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


class BiliVideoServeFileTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        TEST_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        self.serve_dir = TEST_OUTPUT_DIR / "serve"
        self.serve_dir.mkdir(parents=True, exist_ok=True)
        self.video_path = self.serve_dir / "BV1xx411c7mD" / "video.mp4"
        self.video_path.parent.mkdir(parents=True, exist_ok=True)
        self.video_path.write_bytes(b"serve-video-content")
        _set_video_serve_dir(self.serve_dir)

    def tearDown(self):
        _set_video_serve_dir(None)

    async def test_serve_video_returns_file(self):
        response = await _serve_video_file("BV1xx411c7mD/video.mp4")
        sent_messages = []

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            sent_messages.append(message)

        await response(
            {"type": "http", "method": "GET", "path": "/test", "headers": []},
            receive,
            send,
        )
        body = b"".join(message.get("body", b"") for message in sent_messages)
        self.assertEqual(body, b"serve-video-content")
        self.assertEqual(response.media_type, "video/mp4")
        self.assertEqual(response.headers["cache-control"], "no-store")

    async def test_serve_missing_file_returns_404(self):
        with self.assertRaises(HTTPException) as ctx:
            await _serve_video_file("nonexistent/video.mp4")
        self.assertEqual(ctx.exception.status_code, 404)

    async def test_serve_video_blocks_path_traversal(self):
        with self.assertRaises(HTTPException) as ctx:
            await _serve_video_file("../../../etc/passwd")
        self.assertEqual(ctx.exception.status_code, 404)

    async def test_serve_video_when_dir_not_set(self):
        _set_video_serve_dir(None)
        with self.assertRaises(HTTPException) as ctx:
            await _serve_video_file("test/video.mp4")
        self.assertEqual(ctx.exception.status_code, 503)

    def test_route_registered(self):
        self.assertIn(
            f"{VIDEO_SERVE_PREFIX}/{{filename:path}}",
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
            "http://192.168.31.131:7070/haruka/bili-video/files/BV1xx411c7mD/video.mp4",
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

    async def test_send_video_builds_correct_url(self):
        """视频 URL 按 BVID 组织路径。"""
        with patch.object(
            plugin_config,
            "haruka_bili_video_public_base_url",
            "http://192.168.31.131:7070",
        ):
            url = _build_video_url("BV1xx411c7mD/video.mp4")
        self.assertEqual(
            url,
            "http://192.168.31.131:7070/haruka/bili-video/files/BV1xx411c7mD/video.mp4",
        )

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

    async def test_send_video_compress_label(self):
        """send_video 在描述前附加压缩标记。"""
        bot = AsyncMock()
        bot.self_id = "999"
        event = SimpleNamespace(group_id=123456)
        info = VideoInfo(
            bvid="BV1xx411c7mD",
            title="title",
            owner="owner",
            page_name="",
            page_number=1,
            page_count=1,
            cid=100,
            duration=120,
        )
        video_path = TEST_OUTPUT_DIR / "compress-label.mp4"
        video_path.write_bytes(b"video-content")

        with patch.object(
            plugin_config,
            "haruka_bili_video_public_base_url",
            "http://192.168.31.131:7070",
        ):
            await send_video(bot, event, info, video_path, compress_label="（已压缩）")

        # 描述内容应包含压缩标记
        node1_content = bot.send_group_forward_msg.await_args.kwargs["messages"][0][
            "data"
        ]["content"]
        self.assertIn("（已压缩）", node1_content)


class BiliVideoCompressTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        TEST_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        self.video_path = TEST_OUTPUT_DIR / "compress-input.mp4"
        self.video_path.write_bytes(b"x" * 200 * 1024 * 1024)  # 200 MB fake
        self.compress_output = self.video_path.parent / "compressed.mp4"
        # Clean up any leftover compressed files
        if self.compress_output.exists():
            self.compress_output.unlink()

    def tearDown(self):
        if self.compress_output.exists():
            self.compress_output.unlink()

    async def test_compress_video_success(self):
        """压缩成功：返回压缩后的路径且大小在目标内。"""

        async def mock_communicate():
            self.compress_output.write_bytes(b"y" * 90 * 1024 * 1024)  # 90 MB
            return b"", b""

        mock_process = AsyncMock()
        mock_process.communicate.side_effect = mock_communicate
        mock_process.returncode = 0

        with patch(
            "haruka_bot.plugins.bili_video.asyncio.create_subprocess_exec",
            AsyncMock(return_value=mock_process),
        ):
            result = await compress_video(
                self.video_path,
                duration_seconds=600,
                log_id="test",
                target_bytes=95 * 1024 * 1024,
                min_bitrate_bps=500_000,
            )

        self.assertEqual(result, self.compress_output)
        self.assertTrue(self.compress_output.is_file())

    async def test_compress_video_ffmpeg_not_found(self):
        """FFmpeg 未安装时抛出 BiliVideoError。"""
        with patch(
            "haruka_bot.plugins.bili_video.asyncio.create_subprocess_exec",
            AsyncMock(side_effect=FileNotFoundError),
        ):
            with self.assertRaisesRegex(BiliVideoError, "未找到 FFmpeg"):
                await compress_video(
                    self.video_path,
                    duration_seconds=600,
                    log_id="test",
                    target_bytes=95 * 1024 * 1024,
                    min_bitrate_bps=500_000,
                )

    async def test_compress_video_timeout(self):
        """压缩超时时抛出 BiliVideoError。"""
        mock_process = AsyncMock()
        mock_process.communicate = AsyncMock(return_value=(b"", b""))
        mock_process.kill = AsyncMock()
        mock_process.returncode = 0

        with (
            patch(
                "haruka_bot.plugins.bili_video.asyncio.create_subprocess_exec",
                AsyncMock(return_value=mock_process),
            ),
            patch(
                "haruka_bot.plugins.bili_video.asyncio.wait_for",
                AsyncMock(side_effect=asyncio.TimeoutError),
            ),
        ):
            with self.assertRaisesRegex(BiliVideoError, "压缩视频超时"):
                await compress_video(
                    self.video_path,
                    duration_seconds=600,
                    log_id="test",
                    target_bytes=95 * 1024 * 1024,
                    min_bitrate_bps=500_000,
                )

    async def test_compress_video_retry_on_oversized(self):
        """第一次压缩仍超限时，降低码率重试成功。"""
        call_count = 0

        async def mock_communicate():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                self.compress_output.write_bytes(b"z" * 100 * 1024 * 1024)  # 仍超 95MB
            else:
                self.compress_output.write_bytes(b"w" * 90 * 1024 * 1024)  # 90 MB - OK
            return b"", b""

        mock_process = AsyncMock()
        mock_process.communicate.side_effect = mock_communicate
        mock_process.returncode = 0

        with patch(
            "haruka_bot.plugins.bili_video.asyncio.create_subprocess_exec",
            AsyncMock(return_value=mock_process),
        ):
            result = await compress_video(
                self.video_path,
                duration_seconds=600,
                log_id="test",
                target_bytes=95 * 1024 * 1024,
                min_bitrate_bps=500_000,
            )

        self.assertEqual(result, self.compress_output)
        self.assertEqual(call_count, 2)

    async def test_compress_video_min_bitrate_fallback(self):
        """两次压缩都失败时抛出 BiliVideoError（回退群文件）。"""

        async def mock_communicate():
            # Always produce > target size
            self.compress_output.write_bytes(b"z" * 100 * 1024 * 1024)
            return b"", b""

        mock_process = AsyncMock()
        mock_process.communicate.side_effect = mock_communicate
        mock_process.returncode = 0

        with patch(
            "haruka_bot.plugins.bili_video.asyncio.create_subprocess_exec",
            AsyncMock(return_value=mock_process),
        ):
            with self.assertRaisesRegex(BiliVideoError, "超过 95 MB 限制"):
                await compress_video(
                    self.video_path,
                    duration_seconds=600,
                    log_id="test",
                    target_bytes=95 * 1024 * 1024,
                    min_bitrate_bps=500_000,
                )


if __name__ == "__main__":
    unittest.main()
