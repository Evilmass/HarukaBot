import asyncio
import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import nonebot
from fastapi import FastAPI, HTTPException
from nonebot.adapters.onebot.v11 import Message, MessageSegment

nonebot.init()

from haruka_bot.config import Config
from haruka_bot.video import bilibili, douyin
from haruka_bot.video.files import VIDEO_SERVE_PREFIX, VideoFileStore
from haruka_bot.video.models import DownloadedVideo, VideoError, VideoSettings
from haruka_bot.video.service import VideoService, allowed_platforms, message_candidates

MP4 = b"\x00\x00\x00\x18ftypisom" + b"video" * 20


def event(text, group_id=123, user_id=42, message=None):
    message = message if message is not None else Message(text)
    return SimpleNamespace(
        group_id=group_id, user_id=user_id, raw_message=text, message=message,
        get_plaintext=message.extract_plain_text,
    )


def video(path, platform="douyin"):
    return DownloadedVideo(platform, "123", "标题", "作者", 65, "https://www.douyin.com/video/123", path)


class VideoConfigTests(unittest.TestCase):
    def test_old_settings_fallback_and_new_settings_precedence(self):
        config = Config(
            haruka_bili_video_max_size_mb=512, haruka_bili_video_max_links=5,
            haruka_bili_video_concurrency=4, haruka_bili_video_timeout=120,
            haruka_bili_video_public_base_url="http://legacy.example",
            haruka_bili_video_ffmpeg="old-ffmpeg",
        )
        old = VideoSettings.from_config(config)
        self.assertEqual(old, VideoSettings("http://legacy.example", 512, 5, 4, "old-ffmpeg", 120))
        config.haruka_video_max_size_mb = 90
        config.haruka_video_public_base_url = "http://new.example"
        config.haruka_video_ffmpeg = "new-ffmpeg"
        new = VideoSettings.from_config(config)
        self.assertEqual(new.max_size_mb, 90)
        self.assertEqual(new.public_base_url, "http://new.example")
        self.assertEqual(new.ffmpeg, "new-ffmpeg")
        self.assertEqual(new.max_links, 5)

    def test_group_forms_environment_and_positive_limits(self):
        self.assertEqual(Config().haruka_douyin_video_groups, [])
        self.assertEqual(Config(haruka_douyin_video_groups="123, 456 789").haruka_douyin_video_groups, [123, 456, 789])
        with patch.dict(os.environ, {"HARUKA_DOUYIN_VIDEO_GROUPS": "[123,456]"}):
            self.assertEqual(Config().haruka_douyin_video_groups, [123, 456])
        self.assertEqual(Config(haruka_video_max_links=0).haruka_video_max_links, 1)
        config = Config(haruka_bili_video_groups=[123], haruka_douyin_video_groups=[456])
        self.assertEqual(allowed_platforms(123, config), {"bilibili"})
        self.assertEqual(allowed_platforms(456, config), {"douyin"})
        self.assertEqual(allowed_platforms(789, config), set())

    def test_mixed_links_order_and_miniapp_extraction(self):
        mixed = event("https://v.douyin.com/abc/ https://www.bilibili.com/video/BV1xx411c7mD")
        self.assertEqual([p for p, _ in message_candidates(mixed, {"douyin", "bilibili"})], ["douyin", "bilibili"])
        self.assertEqual([p for p, _ in message_candidates(mixed, {"bilibili"})], ["bilibili"])
        card = Message(MessageSegment.json('{"meta":{"detail_1":{"bvid":"BV1xx411c7mD"}}}'))
        self.assertEqual(message_candidates(event("", message=card), {"bilibili"}), [
            ("bilibili", "https://www.bilibili.com/video/BV1xx411c7mD"),
        ])


class VideoFileTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.store = VideoFileStore(Path(self.directory.name), ttl=0.03)
        self.addCleanup(self.directory.cleanup)
        self.addAsyncCleanup(self.store.close)

    async def test_only_registered_files_are_served_and_expire(self):
        task_id = self.store.create("douyin")
        path = self.store.jobs[task_id].directory / "video.mp4"
        path.write_bytes(MP4)
        with self.assertRaises(HTTPException):
            await self.store.serve(task_id)
        relative = self.store.register(task_id, path)
        app = FastAPI()
        app.add_api_route(
            f"{VIDEO_SERVE_PREFIX}/{{task_id}}/video.mp4", self.store.serve,
            methods=["GET", "HEAD"],
        )
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://bot.example") as client:
            response = await client.get(relative, headers={"Range": "bytes=0-7"})
            self.assertEqual(response.status_code, 206)
            self.assertEqual(response.content, MP4[:8])
            self.assertEqual(response.headers["cache-control"], "no-store")
            head = await client.head(relative)
            self.assertEqual(head.status_code, 200)
            self.assertEqual(head.content, b"")
            self.assertEqual(int(head.headers["content-length"]), len(MP4))
            self.assertEqual((await client.get(f"{VIDEO_SERVE_PREFIX}/unknown/video.mp4")).status_code, 404)
        self.store.release(task_id)
        await asyncio.sleep(0.06)
        self.assertFalse(path.exists())
        with self.assertRaises(HTTPException):
            await self.store.serve(task_id)

    async def test_path_escape_and_unregistered_files_are_rejected(self):
        task_id = self.store.create("bilibili")
        outside = Path(self.directory.name).parent / "outside-video.mp4"
        with self.assertRaises(VideoError):
            self.store.register(task_id, outside)
        with self.assertRaises(HTTPException):
            await self.store.serve("../outside")

    async def test_stale_cleanup_skips_active_and_retained_jobs(self):
        active = self.store.create("douyin")
        retained = self.store.create("douyin")
        self.store.release(retained)
        stale = self.store.root / "douyin" / "old-task"
        stale.mkdir()
        for path in [stale, self.store.jobs[active].directory, self.store.jobs[retained].directory]:
            os.utime(path, (time.time() - 10000, time.time() - 10000))
        self.store.cleanup_stale(900)
        self.assertFalse(stale.exists())
        self.assertTrue(self.store.jobs[active].directory.exists())
        self.assertTrue(self.store.jobs[retained].directory.exists())


class FakeProvider:
    def __init__(self, platform, downloads):
        self.platform = platform
        self.downloads = downloads

    async def resolve(self, url):
        if self.platform == "bilibili":
            return bilibili.parse_video_url(url)
        return douyin.parse_video_url(url)

    async def download(self, reference, directory):
        path = directory / "video.mp4"
        path.write_bytes(MP4)
        self.downloads.append((self.platform, reference.key, path))
        await asyncio.sleep(0)
        return video(path, self.platform)


class VideoServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        config = Config(
            haruka_dir=self.directory.name, haruka_bili_video_groups=[123, 456],
            haruka_douyin_video_groups=[123, 456],
            haruka_video_public_base_url="http://bot.example", haruka_video_concurrency=2,
        )
        self.service = VideoService(config)
        self.addAsyncCleanup(self.service.close)
        self.bot = AsyncMock()
        self.bot.self_id = "10000"
        self.downloads = []
        async def provider(platform, stack):
            return FakeProvider(platform, self.downloads)
        self.provider_patch = patch.object(self.service, "_provider", side_effect=provider)
        self.provider_patch.start()
        self.addCleanup(self.provider_patch.stop)

    async def test_mixed_platform_deduplication_and_max_links(self):
        self.service.settings = VideoSettings("http://bot.example", 90, 2, 2, "ffmpeg", 30)
        await self.service.handle(self.bot, event(
            "https://www.douyin.com/video/123 https://m.douyin.com/share/video/123 "
            "https://www.bilibili.com/video/BV1xx411c7mD?p=1 "
            "https://www.bilibili.com/video/BV1xx411c7mD?p=2"
        ))
        self.assertEqual(len(self.downloads), 2)
        self.assertEqual(self.bot.send_group_forward_msg.await_count, 2)
        nodes = self.bot.send_group_forward_msg.await_args_list[0].kwargs["messages"]
        self.assertEqual(len(nodes), 2)
        self.assertIn("作者：作者", nodes[0]["data"]["content"])
        self.assertEqual(nodes[1]["data"]["content"][0].type, "video")

    async def test_same_video_across_groups_and_parts_has_independent_files(self):
        await asyncio.gather(
            self.service.handle(self.bot, event("https://www.bilibili.com/video/BV1xx411c7mD?p=1", 123)),
            self.service.handle(self.bot, event("https://www.bilibili.com/video/BV1xx411c7mD?p=2", 123)),
            self.service.handle(self.bot, event("https://www.bilibili.com/video/BV1xx411c7mD?p=1", 456)),
        )
        paths = [item[2] for item in self.downloads]
        self.assertEqual(len(set(paths)), 3)
        self.assertTrue(all(path.is_file() for path in paths))
        urls = [call.kwargs["messages"][1]["data"]["content"][0].data["file"] for call in self.bot.send_group_forward_msg.await_args_list]
        self.assertEqual(len(set(urls)), 3)
        jobs = list(self.service.store.jobs)
        self.service.store.remove(jobs[0])
        self.assertEqual(sum(path.is_file() for path in paths), 2)

    async def test_ignored_groups_self_messages_and_non_video_text_do_nothing(self):
        for item in [event("https://www.douyin.com/video/123", 999), event("https://www.douyin.com/video/123", user_id=10000), event("hello")]:
            await self.service.handle(self.bot, item)
        self.bot.send_group_msg.assert_not_awaited()
        self.bot.send_group_forward_msg.assert_not_awaited()
        self.assertEqual(self.downloads, [])

    async def test_failure_does_not_stop_other_platform_and_cleans_job(self):
        self.bot.send_group_forward_msg.side_effect = [RuntimeError("send failed"), None]
        await self.service.handle(self.bot, event(
            "https://www.bilibili.com/video/BV1xx411c7mD https://www.douyin.com/video/123"
        ))
        self.assertEqual(self.bot.send_group_forward_msg.await_count, 2)
        self.assertFalse(self.downloads[0][2].exists())
        self.assertTrue(self.downloads[1][2].exists())

    async def test_cancellation_cleans_active_job(self):
        started = asyncio.Event()
        provider = FakeProvider("douyin", [])
        async def wait_download(reference, directory):
            (directory / "partial").write_bytes(b"partial")
            started.set()
            await asyncio.Event().wait()
        provider.download = wait_download
        with patch.object(self.service, "_provider", return_value=provider):
            task = asyncio.create_task(self.service.handle(self.bot, event("https://www.douyin.com/video/123")))
            await started.wait()
            directory = next(iter(self.service.store.jobs.values())).directory
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertFalse(directory.exists())
        self.assertFalse(self.service.store.jobs)

    async def test_shared_concurrency_limit(self):
        active = 0
        maximum = 0
        provider = FakeProvider("douyin", self.downloads)
        original = provider.download
        async def download(reference, directory):
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)
            try:
                await asyncio.sleep(0.02)
                return await original(reference, directory)
            finally:
                active -= 1
        provider.download = download
        with patch.object(self.service, "_provider", return_value=provider):
            await asyncio.gather(*(
                self.service.handle(self.bot, event(f"https://www.douyin.com/video/{number}"))
                for number in range(5)
            ))
        self.assertEqual(maximum, 2)
        self.assertEqual(len(self.downloads), 5)

    async def test_oversized_final_file_is_rejected_before_sending(self):
        self.service.settings = VideoSettings("http://bot.example", 1, 3, 2, "ffmpeg", 30)
        provider = FakeProvider("douyin", self.downloads)
        async def oversized(reference, directory):
            path = directory / "video.mp4"
            path.write_bytes(b"v" * (1024 * 1024 + 1))
            return video(path)
        provider.download = oversized
        with patch.object(self.service, "_provider", return_value=provider):
            await self.service.handle(self.bot, event("https://www.douyin.com/video/123"))
        self.bot.send_group_forward_msg.assert_not_awaited()
        self.assertFalse(self.service.store.jobs)
        self.assertIn("1 MB", self.bot.send_group_msg.await_args.kwargs["message"])

    async def test_total_timeout_cleans_job_and_reports_failure(self):
        self.service.settings = VideoSettings("http://bot.example", 90, 3, 2, "ffmpeg", 0.01)
        provider = FakeProvider("douyin", self.downloads)
        async def stalled(reference, directory):
            await asyncio.Event().wait()
        provider.download = stalled
        with patch.object(self.service, "_provider", return_value=provider):
            await self.service.handle(self.bot, event("https://www.douyin.com/video/123"))
        self.bot.send_group_forward_msg.assert_not_awaited()
        self.assertFalse(self.service.store.jobs)
        self.assertIn("超时", self.bot.send_group_msg.await_args.kwargs["message"])
