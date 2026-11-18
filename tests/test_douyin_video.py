import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
import nonebot

nonebot.init()

from haruka_bot.config import Config
from haruka_bot.video import douyin
from haruka_bot.video.download import download_file
from haruka_bot.video.models import VideoError, VideoSettings, VideoTooLarge

VIDEO_ID = "7521023890996514083"
MP4 = b"\x00\x00\x00\x18ftypisom" + b"video" * 20
JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00" + b"image" * 12
PNG = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + b"image" * 12
WEBP = b"RIFF\x00\x01\x00\x00WEBPVP8 " + b"image" * 12


def settings():
    return VideoSettings("http://bot.example", 90, 3, 2, "ffmpeg", 30)


def detail(**overrides):
    value = {
        "aweme_id": VIDEO_ID, "desc": "作品标题", "author": {"nickname": "作者"},
        "video": {"duration": 65000, "play_addr": {
            "uri": "video-token", "url_list": ["https://cdn.example/video.mp4"],
        }},
    }
    value.update(overrides)
    return {"status_code": 0, "aweme_detail": value}


def album(images):
    return detail(images=images, video=None)


class DouyinUrlTests(unittest.TestCase):
    def test_share_text_and_supported_forms(self):
        text = "复制打开抖音 https://v.douyin.com/a-b_123/，看看作品 https://jx.douyin.com/ABC/。"
        self.assertEqual(douyin.extract_message_urls(text), [
            "https://v.douyin.com/a-b_123/", "https://jx.douyin.com/ABC/",
        ])
        for url in [
            f"https://www.douyin.com/video/{VIDEO_ID}?foo=bar",
            f"https://m.douyin.com/share/video/{VIDEO_ID}/",
            f"https://www.iesdouyin.com/share/video/{VIDEO_ID}/",
            f"https://jingxuan.douyin.com/m/video/{VIDEO_ID}",
            f"https://www.douyin.com/?modal_id={VIDEO_ID}",
        ]:
            with self.subTest(url=url):
                self.assertEqual(douyin.parse_video_url(url).aweme_id, VIDEO_ID)
        self.assertIsNone(douyin.parse_video_url(f"https://douyin.com.evil.example/video/{VIDEO_ID}"))
        self.assertIsNone(douyin.parse_video_url("https://www.douyin.com/user/author"))


class DouyinCookieTests(unittest.TestCase):
    def test_netscape_domain_path_expiration_and_inline_precedence(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "douyin_cookies.txt"
            path.write_text(
                "# Netscape HTTP Cookie File\n"
                ".douyin.com\tTRUE\t/\tTRUE\t0\tsession\tfile-secret\n"
                "#HttpOnly_.douyin.com\tTRUE\t/\tTRUE\t0\tttwid\tvisitor\n"
                ".douyin.com\tTRUE\t/private\tTRUE\t0\tprivate\tprivate-secret\n"
                ".douyin.com\tTRUE\t/\tTRUE\t1\texpired\texpired-secret\n"
                ".example.com\tTRUE\t/\tTRUE\t0\tother\tother-secret\n",
                encoding="utf-8",
            )
            config = Config(haruka_dir=directory)
            cookies = douyin.load_cookies(config)
            client = httpx.Client(cookies=cookies)
            try:
                header = client.build_request("GET", douyin.DETAIL_URL).headers["cookie"]
                self.assertIn("session=file-secret", header)
                self.assertIn("ttwid=visitor", header)
                self.assertNotIn("private=", header)
                self.assertNotIn("expired=", header)
                self.assertNotIn("cookie", client.build_request("GET", "https://cdn.example/").headers)
            finally:
                client.close()
            config.haruka_douyin_video_cookie = "session=inline=secret; ttwid=inline-visitor"
            inline = douyin.load_cookies(config)
            self.assertEqual(inline.get("session"), "inline=secret")
            self.assertEqual(inline.get("ttwid"), "inline-visitor")

    def test_missing_default_cookie_allows_anonymous_requests(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(len(douyin.load_cookies(Config(haruka_dir=directory))), 0)

    def test_bad_cookie_errors_do_not_expose_contents(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "douyin_cookies.txt"
            path.write_text("# Netscape HTTP Cookie File\nsecret-session-token\n", encoding="utf-8")
            with self.assertRaises(VideoError) as caught:
                douyin.load_cookies(Config(haruka_dir=directory))
            self.assertNotIn("secret-session-token", str(caught.exception))
            self.assertTrue(caught.exception.__suppress_context__)
            with self.assertRaises(VideoError):
                douyin.load_cookies(Config(haruka_dir=directory, haruka_douyin_video_cookie_file="missing.txt"))


class DouyinDownloadTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.cache_patch = patch.object(douyin, "_ttwid", None)
        self.cache_patch.start()
        self.addCleanup(self.cache_patch.stop)
        self.lock_patch = patch.object(douyin, "_ttwid_lock", asyncio.Lock())
        self.lock_patch.start()
        self.addCleanup(self.lock_patch.stop)

    async def make_provider(
        self, metadata_handler, media_handler=None, cookies=None, video_settings=None,
    ):
        client = httpx.AsyncClient(transport=httpx.MockTransport(metadata_handler), cookies=cookies)
        media_client = httpx.AsyncClient(transport=httpx.MockTransport(media_handler or metadata_handler))
        self.addAsyncCleanup(client.aclose)
        self.addAsyncCleanup(media_client.aclose)
        return douyin.DouyinVideoProvider(client, media_client, video_settings or settings())

    async def test_short_link_redirect_and_unsupported_destinations(self):
        def handler(request):
            target = "note" if request.url.path == "/note-link/" else "video"
            return httpx.Response(302, headers={"Location": f"https://www.douyin.com/{target}/{VIDEO_ID}"})
        provider = await self.make_provider(handler)
        reference = await provider.resolve("https://v.douyin.com/abc/")
        self.assertEqual(reference.aweme_id, VIDEO_ID)
        note = await provider.resolve("https://v.douyin.com/note-link/")
        self.assertEqual((note.aweme_id, note.kind), (VIDEO_ID, "note"))
        direct = await provider.resolve(f"https://www.douyin.com/note/{VIDEO_ID}")
        self.assertEqual((direct.aweme_id, direct.kind), (VIDEO_ID, "note"))
        with self.assertRaisesRegex(VideoError, "跳转"):
            await provider.resolve("https://outside.example/video/123")

    async def test_short_link_does_not_follow_external_redirect(self):
        visited = []
        def handler(request):
            visited.append(request.url.host)
            return httpx.Response(302, headers={"Location": "https://outside.example/private"})
        provider = await self.make_provider(handler)
        with self.assertRaises(VideoError):
            await provider.resolve("https://v.douyin.com/abc/")
        self.assertEqual(visited, ["v.douyin.com"])

    async def test_ttwid_registration_is_cached_and_cookie_is_scoped(self):
        registrations = []
        def handler(request):
            if request.url.host == "ttwid.bytedance.com":
                self.assertNotIn("cookie", request.headers)
                registrations.append(request)
                return httpx.Response(200, headers={"Set-Cookie": "ttwid=new-visitor; Path=/"}, json={})
            self.assertIn("ttwid=new-visitor", request.headers["cookie"])
            return httpx.Response(200, json=detail())
        first = await self.make_provider(handler)
        second = await self.make_provider(handler)
        reference = douyin.DouyinReference("url", VIDEO_ID)
        await asyncio.gather(first.fetch_detail(reference), second.fetch_detail(reference))
        self.assertEqual(len(registrations), 1)

    async def test_download_preserves_ttwid_and_falls_back_without_cookies(self):
        calls = []
        def metadata(request):
            self.assertEqual(request.url.host, "www.douyin.com")
            self.assertIn("ttwid=configured-visitor", request.headers["cookie"])
            return httpx.Response(200, json=detail())
        def media(request):
            self.assertNotIn("cookie", request.headers)
            self.assertEqual(request.headers["range"], "bytes=0-")
            calls.append(request.url.host)
            if request.url.host == "aweme.snssdk.com":
                return httpx.Response(403)
            return httpx.Response(200, headers={"Content-Type": "video/mp4"}, content=MP4)
        cookies = httpx.Cookies()
        cookies.set("ttwid", "configured-visitor", domain=".douyin.com")
        provider = await self.make_provider(metadata, media, cookies)
        downloaded = await provider.download(douyin.DouyinReference("url", VIDEO_ID), Path(self.directory.name))
        self.assertEqual(calls, ["aweme.snssdk.com", "cdn.example"])
        self.assertEqual(downloaded.path.read_bytes(), MP4)
        self.assertEqual(downloaded.duration, 65)
        self.assertIn("作者：作者", downloaded.description)

    async def test_album_downloads_images_in_order_and_detects_format(self):
        calls = []
        def metadata(request):
            return httpx.Response(200, json=album([
                {"url_list": [
                    "https://p3-sign.douyinpic.com/tos-cn-i-0813/aaa~tplv-dy-aweme-images:q75.jpeg"
                    "?biz_tag=aweme_images&x-expires=1",
                ]},
                {"url_list": [
                    "https://p3-sign.douyinpic.com/tos-cn-i-0813/bbb~tplv-dy-aweme-images:q75.webp"
                    "?biz_tag=aweme_images&x-signature=abc",
                ]},
                {"label": "没有地址的图片"},
            ]))
        def media(request):
            self.assertNotIn("cookie", request.headers)
            self.assertEqual(request.headers["referer"], "https://www.douyin.com/")
            self.assertTrue(request.url.query)
            calls.append(request.url.path)
            content = WEBP if "bbb" in request.url.path else JPEG
            return httpx.Response(
                200, headers={"Content-Type": "image/jpeg"}, content=content,
            )
        provider = await self.make_provider(metadata, media)
        downloaded = await provider.download(
            douyin.DouyinReference("url", VIDEO_ID, "note"), Path(self.directory.name),
        )
        self.assertTrue(downloaded.is_album)
        self.assertEqual(len(calls), 2)
        self.assertIn("aaa~tplv-dy-aweme-images:q75.jpeg", calls[0])
        self.assertIn("bbb~tplv-dy-aweme-images:q75.webp", calls[1])
        self.assertEqual(
            [path.name for path in downloaded.image_paths],
            ["image-001.jpg", "image-002.webp"],
        )
        self.assertEqual(downloaded.image_paths[0].read_bytes(), JPEG)
        self.assertEqual(downloaded.image_paths[1].read_bytes(), WEBP)
        self.assertEqual(downloaded.canonical_url, f"https://www.douyin.com/note/{VIDEO_ID}")
        self.assertIn("图集：2 张", downloaded.description)
        self.assertNotIn("时长", downloaded.description)

    async def test_album_uses_backup_urls_and_skips_broken_images(self):
        def metadata(request):
            return httpx.Response(200, json=album([
                {"url_list": ["https://p3.example/first", "https://p4.example/backup"]},
                {"url_list": ["https://p3.example/broken"]},
            ]))
        def media(request):
            if request.url.path == "/first":
                return httpx.Response(403)
            if request.url.path == "/broken":
                return httpx.Response(
                    200, headers={"Content-Type": "text/html"}, content=b"<html>expired</html>",
                )
            return httpx.Response(200, content=PNG)
        provider = await self.make_provider(metadata, media)
        downloaded = await provider.download(
            douyin.DouyinReference("url", VIDEO_ID, "note"), Path(self.directory.name),
        )
        self.assertEqual([path.name for path in downloaded.image_paths], ["image-001.png"])
        self.assertEqual(downloaded.image_paths[0].read_bytes(), PNG)
        self.assertIn("图集：1 张", downloaded.description)

    async def test_album_without_usable_images_fails(self):
        def metadata(request):
            return httpx.Response(200, json=album([
                {"url_list": ["https://p3.example/broken"]},
                {"label": "没有地址的图片"},
            ]))
        def media(request):
            return httpx.Response(
                200, headers={"Content-Type": "text/html"}, content=b"<html></html>",
            )
        provider = await self.make_provider(metadata, media)
        with self.assertRaisesRegex(VideoError, "没有可下载的图片"):
            await provider.download(
                douyin.DouyinReference("url", VIDEO_ID, "note"), Path(self.directory.name),
            )

    async def test_album_total_size_and_image_count_are_limited(self):
        image = JPEG + b"x" * (600 * 1024)
        def metadata(request):
            return httpx.Response(200, json=album([
                {"url_list": ["https://p3.example/1"]},
                {"url_list": ["https://p3.example/2"]},
                {"url_list": ["https://p3.example/3"]},
            ]))
        def media(request):
            return httpx.Response(200, content=image)
        provider = await self.make_provider(
            metadata, media, video_settings=VideoSettings("http://bot.example", 1, 3, 2, "ffmpeg", 30),
        )
        with self.assertRaisesRegex(VideoTooLarge, "图集超过 1 MB"):
            await provider.download(
                douyin.DouyinReference("url", VIDEO_ID, "note"), Path(self.directory.name),
            )
        with patch.object(douyin, "MAX_ALBUM_IMAGES", 2):
            provider = await self.make_provider(metadata, media)
            downloaded = await provider.download(
                douyin.DouyinReference("url", VIDEO_ID, "note"), Path(self.directory.name),
            )
        self.assertEqual(len(downloaded.image_paths), 2)

    async def test_detail_failure_modes_are_safe(self):
        cases = [
            (httpx.Response(403, text="secret session info"), "Cookie"),
            (httpx.Response(404), "不存在"),
            (httpx.Response(200, content=b""), "Cookie"),
            (httpx.Response(200, json=[]), "Cookie"),
            (httpx.Response(200, json={"status_code": 0}), "不可用"),
            (httpx.Response(200, json=detail(images=[], video=None)), "没有可下载"),
            (httpx.Response(200, json=detail(video=None)), "没有可下载"),
            (httpx.Response(200, json=detail(aweme_id="wrong")), "不匹配"),
        ]
        for response, expected in cases:
            with self.subTest(expected=expected):
                provider = await self.make_provider(lambda request: response, cookies={"ttwid": "existing"})
                with self.assertRaisesRegex(VideoError, expected) as caught:
                    await provider.fetch_detail(douyin.DouyinReference("url", VIDEO_ID))
                self.assertNotIn("secret session info", str(caught.exception))


class StreamingDownloadTests(unittest.IsolatedAsyncioTestCase):
    async def test_cookie_removed_on_every_media_redirect(self):
        visited = []
        def handler(request):
            self.assertNotIn("cookie", request.headers)
            visited.append(request.url.host)
            if len(visited) == 1:
                return httpx.Response(302, headers={"Location": "https://second.example/video"})
            return httpx.Response(200, content=MP4)
        with tempfile.TemporaryDirectory() as directory:
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler), headers={"Cookie": "secret"}, cookies={"session": "secret"}) as client:
                await download_file(client, "https://first.example/video", Path(directory) / "video.mp4", 1000, {}, require_mp4=True)
        self.assertEqual(visited, ["first.example", "second.example"])

    async def test_non_video_incomplete_and_unknown_length_oversize_are_removed(self):
        cases = [
            (httpx.Response(200, headers={"Content-Type": "text/html"}, content=b"<html>blocked</html>"), VideoError, 1000),
            (httpx.Response(200, content=b"not-video"), VideoError, 1000),
            (httpx.Response(206, headers={"Content-Range": "bytes 0-111/1000"}, content=MP4), VideoError, 1000),
            (httpx.Response(200, content=MP4), VideoTooLarge, 20),
        ]
        for response, error_class, limit in cases:
            with self.subTest(error=error_class):
                # 移除 MockTransport 自动设置的长度，模拟 chunked/未知大小响应。
                response.headers.pop("content-length", None)
                with tempfile.TemporaryDirectory() as directory:
                    target = Path(directory) / "video.mp4"
                    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: response)) as client:
                        with self.assertRaises(error_class):
                            await download_file(client, "https://cdn.example/video", target, limit, {}, require_mp4=True)
                    self.assertFalse(target.exists())

    async def test_cancelled_stream_removes_partial_file(self):
        started = asyncio.Event()
        class Stream(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield MP4
                started.set()
                await asyncio.Event().wait()
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "video.mp4"
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, stream=Stream()))) as client:
                task = asyncio.create_task(download_file(client, "https://cdn.example/video", target, 1000, {}))
                await started.wait()
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
            self.assertFalse(target.exists())
