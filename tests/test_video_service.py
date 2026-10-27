import asyncio
import json
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
from haruka_bot.video.cache import VideoCache
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

    def test_video_cache_settings_defaults_and_limits(self):
        config = Config()
        self.assertTrue(config.haruka_video_cache_enabled)
        self.assertEqual(config.haruka_video_cache_days, 7)
        self.assertEqual(Config(haruka_video_cache_days=0).haruka_video_cache_days, 1)
        self.assertFalse(Config(haruka_video_cache_enabled=False).haruka_video_cache_enabled)

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


class VideoCacheTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name) / "cache"
        self.cache = VideoCache(self.root, ttl=7 * 86400)

    def _video(self, **overrides):
        path = Path(self.directory.name) / "source.mp4"
        path.write_bytes(MP4)
        values = {
            "platform": "bilibili", "video_id": "BV1xx411c7mD", "title": "标题",
            "author": "UP", "duration": 65,
            "canonical_url": "https://www.bilibili.com/video/BV1xx411c7mD",
            "path": path, "part_label": "P1 分P",
        }
        values.update(overrides)
        return DownloadedVideo(**values)

    def test_roundtrip_metadata_and_file_survive_source_removal(self):
        video = self._video()
        key = ("BV1xx411c7mD", 1)
        self.cache.save(video, "bilibili", key)
        video.path.unlink()
        cached = self.cache.lookup("bilibili", key)
        self.assertIsNotNone(cached)
        self.assertEqual(cached.description, video.description)
        self.assertEqual(cached.path.read_bytes(), MP4)
        self.assertEqual(cached.path.name, "video.mp4")
        self.assertIsNone(self.cache.lookup("bilibili", ("BV1xx411c7mD", 2)))
        self.assertIsNone(self.cache.lookup("douyin", key))

    def test_expired_or_tampered_entries_are_discarded(self):
        video = self._video()
        key = ("BV1xx411c7mD", 1)
        self.cache.save(video, "bilibili", key)
        entry = self.root / "bilibili" / "BV1xx411c7mD-p1"
        meta_path = entry / "meta.json"
        meta = json.loads(meta_path.read_text("utf-8"))
        meta["created_at"] = time.time() - 8 * 86400
        meta_path.write_text(json.dumps(meta), encoding="utf-8")
        self.assertIsNone(self.cache.lookup("bilibili", key))
        self.assertFalse(entry.exists())

    def test_cleanup_removes_expired_and_incomplete_entries(self):
        self.cache.save(self._video(), "bilibili", ("BV1xx411c7mD", 1))
        stale = self.root / "douyin" / "stale"
        stale.mkdir(parents=True)
        os.utime(stale, (time.time() - 100, time.time() - 100))
        self.cache.ttl = -1
        self.cache.cleanup()
        self.assertFalse((self.root / "bilibili" / "BV1xx411c7mD-p1").exists())
        self.assertFalse(stale.exists())

    def test_disabled_cache_does_not_store_or_read(self):
        video = self._video()
        key = ("BV1xx411c7mD", 1)
        self.cache.enabled = False
        self.cache.save(video, "bilibili", key)
        self.assertIsNone(self.cache.lookup("bilibili", key))
        self.assertFalse(self.root.exists())

    def test_unsafe_keys_stay_under_cache_root(self):
        video = self._video()
        key = ("../../escape", 1)
        self.cache.save(video, "bilibili", key)
        cached = self.cache.lookup("bilibili", key)
        self.assertIsNotNone(cached)
        cached.path.resolve().relative_to(self.root)


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

    async def test_same_video_across_groups_and_parts_reuses_cache(self):
        await asyncio.gather(
            self.service.handle(self.bot, event("https://www.bilibili.com/video/BV1xx411c7mD?p=1", 123)),
            self.service.handle(self.bot, event("https://www.bilibili.com/video/BV1xx411c7mD?p=2", 123)),
            self.service.handle(self.bot, event("https://www.bilibili.com/video/BV1xx411c7mD?p=1", 456)),
        )
        # p=1 只下载一次，另一群直接使用缓存；p=2 单独下载。
        self.assertEqual(len(self.downloads), 2)
        jobs = list(self.service.store.jobs.values())
        self.assertEqual(len(jobs), 3)
        paths = [job.path for job in jobs]
        self.assertTrue(all(path is not None and path.is_file() for path in paths))
        self.assertEqual(len(set(paths)), 3)
        urls = [call.kwargs["messages"][1]["data"]["content"][0].data["file"] for call in self.bot.send_group_forward_msg.await_args_list]
        self.assertEqual(len(set(urls)), 3)
        self.service.store.remove(next(iter(self.service.store.jobs)))
        self.assertEqual(sum(path.is_file() for path in paths), 2)

    async def test_repeated_video_uses_cache_and_survives_job_cleanup(self):
        url = "https://www.douyin.com/video/123"
        await self.service.handle(self.bot, event(url, 123))
        self.assertEqual(len(self.downloads), 1)
        await self.service.handle(self.bot, event(url, 456))
        self.assertEqual(len(self.downloads), 1)
        self.assertEqual(self.bot.send_group_forward_msg.await_count, 2)
        nodes = self.bot.send_group_forward_msg.await_args_list[1].kwargs["messages"]
        self.assertIn("标题", nodes[0]["data"]["content"])
        self.assertEqual(nodes[1]["data"]["content"][0].type, "video")
        cache_video = Path(self.directory.name) / "video" / "cache" / "douyin" / "123-p1" / "video.mp4"
        self.assertTrue(cache_video.is_file())
        jobs = list(self.service.store.jobs.values())
        self.assertEqual(len(jobs), 2)
        self.assertTrue(all(job.path.is_file() for job in jobs))
        for task_id in list(self.service.store.jobs):
            self.service.store.remove(task_id)
        self.assertTrue(cache_video.is_file())
        await self.service.handle(self.bot, event(url, 123))
        self.assertEqual(len(self.downloads), 1)
        self.assertEqual(self.bot.send_group_forward_msg.await_count, 3)

    async def test_concurrent_same_video_downloads_once(self):
        await asyncio.gather(
            self.service.handle(self.bot, event("https://www.douyin.com/video/123", 123)),
            self.service.handle(self.bot, event("https://www.douyin.com/video/123", 456)),
        )
        self.assertEqual(len(self.downloads), 1)
        self.assertEqual(self.bot.send_group_forward_msg.await_count, 2)

    async def test_expired_cache_is_removed_and_video_redownloaded(self):
        url = "https://www.douyin.com/video/123"
        await self.service.handle(self.bot, event(url, 123))
        self.service.cache.ttl = -1
        await self.service.handle(self.bot, event(url, 123))
        self.assertEqual(len(self.downloads), 2)

    async def test_cache_can_be_disabled(self):
        self.service.cache.enabled = False
        url = "https://www.douyin.com/video/123"
        await self.service.handle(self.bot, event(url, 123))
        await self.service.handle(self.bot, event(url, 456))
        self.assertEqual(len(self.downloads), 2)
        self.assertFalse((Path(self.directory.name) / "video" / "cache").exists())

    async def test_ignored_groups_self_messages_and_non_video_text_do_nothing(self):
        for item in [event("https://www.douyin.com/video/123", 999), event("https://www.douyin.com/video/123", user_id=10000), event("hello")]:
            await self.service.handle(self.bot, item)
        self.bot.send_group_msg.assert_not_awaited()
        self.bot.send_group_forward_msg.assert_not_awaited()
        self.assertEqual(self.downloads, [])

    async def test_non_video_short_link_is_ignored_silently(self):
        """短链指向直播间等非视频页面时不提示，也不占用处理数量上限。"""
        await self.service.handle(self.bot, event(
            "https://b23.tv/liveroom https://www.bilibili.com/video/BV1xx411c7mD"
        ))
        self.assertEqual(len(self.downloads), 1)
        self.assertEqual(self.bot.send_group_forward_msg.await_count, 1)
        self.assertEqual(self.bot.send_group_msg.await_count, 1)
        self.assertIn("正在处理", self.bot.send_group_msg.await_args.kwargs["message"])

        self.bot.reset_mock()
        await self.service.handle(self.bot, event("https://b23.tv/liveroom"))
        self.bot.send_group_msg.assert_not_awaited()
        self.bot.send_group_forward_msg.assert_not_awaited()

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
