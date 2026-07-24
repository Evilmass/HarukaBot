import asyncio
import base64
import json
import re
import shutil
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import parse_qs, urlparse

import httpx
from nonebot import on_message
from nonebot.adapters.onebot.v11 import Bot, Message, MessageSegment
from nonebot.adapters.onebot.v11.event import GroupMessageEvent
from nonebot.adapters.onebot.v11.exception import NetworkError
from nonebot.log import logger
from nonebot.rule import Rule

from ..config import plugin_config
from ..utils import get_path

VIDEO_URL_RE = re.compile(
    r"https?://(?:www\.|m\.)?bilibili\.com/video/"
    r"(?:BV[0-9A-Za-z]{10}|av\d+)[0-9A-Za-z?&=_%./:+~#@-]*",
    re.IGNORECASE,
)
SHORT_URL_RE = re.compile(
    r"https?://(?:b23\.tv|b23\.wtf|bili2233\.cn)/"
    r"[0-9A-Za-z]+[0-9A-Za-z?&=_%./:+~#@-]*",
    re.IGNORECASE,
)
VIDEO_ID_RE = re.compile(r"^(BV[0-9A-Za-z]{10}|av\d+)$", re.IGNORECASE)
TRAILING_URL_CHARS = ".,;:!?，。；：！？)]}）】》\"'"
API_VIEW = "https://api.bilibili.com/x/web-interface/view"
API_PLAYURL = "https://api.bilibili.com/x/player/playurl"
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


class BiliVideoError(RuntimeError):
    """可安全展示给群聊用户的下载错误。"""


@dataclass(frozen=True)
class VideoReference:
    original_url: str
    bvid: Optional[str] = None
    aid: Optional[int] = None
    page: int = 1

    @property
    def key(self) -> Tuple[str, int]:
        return (self.bvid or f"av{self.aid}", self.page)

    @property
    def api_params(self) -> Dict[str, Any]:
        if self.bvid:
            return {"bvid": self.bvid}
        return {"aid": self.aid}


@dataclass(frozen=True)
class VideoInfo:
    bvid: str
    title: str
    owner: str
    page_name: str
    page_number: int
    page_count: int
    cid: int
    duration: int

    @property
    def canonical_url(self) -> str:
        suffix = f"?p={self.page_number}" if self.page_count > 1 else ""
        return f"https://www.bilibili.com/video/{self.bvid}{suffix}"


def _clean_url(url: str) -> str:
    return url.rstrip(TRAILING_URL_CHARS)


def parse_video_url(url: str) -> Optional[VideoReference]:
    """将 B 站标准视频链接解析为下载引用。"""
    url = _clean_url(url)
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if host not in {"bilibili.com", "www.bilibili.com", "m.bilibili.com"}:
        return None

    path_parts = [part for part in parsed.path.split("/") if part]
    if len(path_parts) < 2 or path_parts[0].lower() != "video":
        return None
    match = VIDEO_ID_RE.fullmatch(path_parts[1])
    if not match:
        return None

    raw_id = match.group(1)
    page_value = parse_qs(parsed.query).get("p", ["1"])[0]
    try:
        page = max(int(page_value), 1)
    except (TypeError, ValueError):
        page = 1

    if raw_id.lower().startswith("av"):
        return VideoReference(url, aid=int(raw_id[2:]), page=page)
    return VideoReference(url, bvid="BV" + raw_id[2:], page=page)


def extract_message_urls(text: str) -> List[str]:
    """按消息中的出现顺序提取标准链接和短链接。"""
    matches = list(VIDEO_URL_RE.finditer(text)) + list(SHORT_URL_RE.finditer(text))
    matches.sort(key=lambda item: item.start())
    result: List[str] = []
    seen = set()
    for match in matches:
        url = _clean_url(match.group(0))
        if url not in seen:
            seen.add(url)
            result.append(url)
    return result


async def resolve_video_references(
    text: str, client: httpx.AsyncClient
) -> List[VideoReference]:
    """解析消息中的链接，并展开 b23 等 B 站短链接。"""
    references: List[VideoReference] = []
    seen = set()
    for url in extract_message_urls(text):
        reference = parse_video_url(url)
        if reference is None:
            try:
                response = await client.get(url)
                response.raise_for_status()
                reference = parse_video_url(str(response.url))
            except (httpx.HTTPError, ValueError) as error:
                logger.warning(f"无法展开 B 站短链接 {url}: {error}")
                continue
        if reference is not None and reference.key not in seen:
            seen.add(reference.key)
            references.append(reference)
    return references


def message_search_text(event: GroupMessageEvent) -> str:
    """同时检查纯文本、原始消息及卡片消息段数据。"""
    parts = [event.get_plaintext(), event.raw_message, str(event.message)]
    parts.extend(
        json.dumps(segment.data, ensure_ascii=False) for segment in event.message
    )
    return "\n".join(part for part in parts if part)


def _stream_url(stream: Dict[str, Any]) -> str:
    url = stream.get("baseUrl") or stream.get("base_url") or stream.get("url")
    if not url:
        raise BiliVideoError("B 站没有返回可用的媒体地址")
    return str(url)


def _stream_urls(stream: Dict[str, Any]) -> Iterable[str]:
    yield _stream_url(stream)
    backups = stream.get("backupUrl") or stream.get("backup_url") or []
    for url in backups:
        if url:
            yield str(url)


def get_dash_stream_candidates(
    play_data: Dict[str, Any], max_quality: int
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """按清晰度降序返回视频流，每档编码优先 AVC。"""
    dash = play_data.get("dash") or {}
    videos = list(dash.get("video") or [])
    audios = list(dash.get("audio") or [])
    if not videos or not audios:
        raise BiliVideoError("该视频没有可下载的 DASH 视频或音频流")

    allowed = [item for item in videos if int(item.get("id", 0)) <= max_quality]
    if not allowed:
        allowed = videos

    def video_rank(item: Dict[str, Any]) -> Tuple[int, int]:
        codec = str(item.get("codecs", "")).lower()
        avc_compatible = int(codec.startswith("avc") or codec.startswith("h264"))
        return avc_compatible, int(item.get("bandwidth", 0))

    candidates = []
    qualities = sorted({int(item.get("id", 0)) for item in allowed}, reverse=True)
    for quality in qualities:
        same_quality = [item for item in allowed if int(item.get("id", 0)) == quality]
        candidates.append(max(same_quality, key=video_rank))
    audio = max(audios, key=lambda item: int(item.get("bandwidth", 0)))
    return candidates, audio


def select_dash_streams(
    play_data: Dict[str, Any], max_quality: int
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """选择不超过配置清晰度的最高画质，编码相同时优先 AVC。"""
    videos, audio = get_dash_stream_candidates(play_data, max_quality)
    return videos[0], audio


class BiliVideoDownloader:
    def __init__(self, client: httpx.AsyncClient):
        self.client = client
        self.max_bytes = plugin_config.haruka_bili_video_max_size_mb * 1024 * 1024

    async def _api_get(self, url: str, params: Dict[str, Any]) -> Dict[str, Any]:
        try:
            response = await self.client.get(url, params=params)
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise BiliVideoError(f"请求 B 站接口失败：{error}") from error
        if payload.get("code") != 0:
            message = payload.get("message") or payload.get("msg") or "未知错误"
            raise BiliVideoError(f"B 站接口拒绝请求：{message}")
        return payload.get("data") or {}

    async def get_video_info(self, reference: VideoReference) -> VideoInfo:
        data = await self._api_get(API_VIEW, reference.api_params)
        pages = data.get("pages") or []
        if not pages:
            raise BiliVideoError("B 站没有返回视频分 P 信息")
        if reference.page > len(pages):
            raise BiliVideoError(
                f"视频只有 {len(pages)} 个分 P，无法下载第 {reference.page} P"
            )
        page = pages[reference.page - 1]
        owner = data.get("owner") or {}
        return VideoInfo(
            bvid=str(data.get("bvid") or reference.bvid or ""),
            title=str(data.get("title") or "未命名视频"),
            owner=str(owner.get("name") or "未知 UP 主"),
            page_name=str(page.get("part") or f"P{reference.page}"),
            page_number=reference.page,
            page_count=len(pages),
            cid=int(page["cid"]),
            duration=int(page.get("duration") or data.get("duration") or 0),
        )

    async def get_play_data(self, info: VideoInfo) -> Dict[str, Any]:
        return await self._api_get(
            API_PLAYURL,
            {
                "bvid": info.bvid,
                "cid": info.cid,
                "qn": plugin_config.haruka_bili_video_quality,
                "fnval": 16,
                "fnver": 0,
                "fourk": 1,
                "otype": "json",
            },
        )

    async def _download_stream(
        self,
        stream: Dict[str, Any],
        target: Path,
        referer: str,
        log_id: Optional[str] = None,
        stream_kind: Optional[str] = None,
    ) -> int:
        scope = f"[B站视频][{log_id or target.name}]"
        if stream_kind:
            scope += f"[{stream_kind}]"
        last_error = "未知错误"
        for url in _stream_urls(stream):
            # 收集所有待尝试的 URL：原始 URL + 备用 CDN 镜像
            urls_to_try = [url]
            for fallback_host in self._CDN_FALLBACK_HOSTS:
                fallback_url = self._replace_cdn_host(url, fallback_host)
                if fallback_url != url and fallback_url not in urls_to_try:
                    urls_to_try.append(fallback_url)

            for attempt_url in urls_to_try:
                size = 0
                attempt_started = time.perf_counter()
                host = urlparse(attempt_url).hostname or "未知 CDN"
                logger.info(f"{scope} 开始下载媒体流，CDN={host}")
                try:
                    # B 站的媒体 CDN 会拒绝不带 Range 的普通 GET 请求。
                    async with self._media_stream(
                        attempt_url, "bytes=0-", referer
                    ) as response:
                        response.raise_for_status()
                        content_length = int(response.headers.get("content-length", 0))
                        if content_length > self.max_bytes:
                            raise BiliVideoError(
                                "视频文件超过 "
                                f"{plugin_config.haruka_bili_video_max_size_mb} MB 限制"
                            )
                        with target.open("wb") as output:
                            async for chunk in response.aiter_bytes(1024 * 1024):
                                size += len(chunk)
                                if size > self.max_bytes:
                                    raise BiliVideoError(
                                        "视频文件超过 "
                                        f"{plugin_config.haruka_bili_video_max_size_mb} MB 限制"
                                    )
                                output.write(chunk)
                    elapsed = time.perf_counter() - attempt_started
                    size_mb = size / 1024 / 1024
                    speed = size_mb / elapsed if elapsed > 0 else 0
                    logger.info(
                        f"{scope} 媒体流下载完成："
                        f"{size_mb:.1f} MB，耗时 {elapsed:.2f} 秒，"
                        f"平均 {speed:.1f} MB/s，CDN={host}"
                    )
                    return size
                except BiliVideoError:
                    target.unlink(missing_ok=True)
                    raise
                except (httpx.HTTPError, OSError, ValueError) as error:
                    if isinstance(error, httpx.HTTPStatusError):
                        host = error.request.url.host
                        last_error = f"HTTP {error.response.status_code}（{host}）"
                    else:
                        last_error = type(error).__name__
                    elapsed = time.perf_counter() - attempt_started
                    logger.warning(
                        f"{scope} 媒体流下载尝试失败："
                        f"{last_error}，耗时 {elapsed:.2f} 秒，CDN={host}"
                    )
                    target.unlink(missing_ok=True)
        raise BiliVideoError(f"下载 B 站媒体流失败：{last_error}")

    # 当主 CDN 返回 403 时可尝试的备用镜像域名
    _CDN_FALLBACK_HOSTS = [
        "upos-sz-mirrorali.bilivideo.com",
        "upos-sz-mirrorhw.bilivideo.com",
        "upos-sz-mirrorcos.bilivideo.com",
    ]

    @staticmethod
    def _replace_cdn_host(url: str, new_host: str) -> str:
        """将 URL 中的 CDN 主机名替换为指定的镜像主机名。"""
        import re as _re

        return _re.sub(
            r"https?://[^/]+\.bilivideo\.com/",
            f"https://{new_host}/",
            url,
            count=1,
        )

    @asynccontextmanager
    async def _media_stream(self, url: str, byte_range: str, referer: str):
        """使用视频页 Referer 访问 CDN，且不向 CDN 发送登录 Cookie。"""
        request = self.client.build_request(
            "GET",
            url,
            headers={
                "Range": byte_range,
                "User-Agent": DEFAULT_USER_AGENT,
                "Referer": referer,
                "Origin": "https://www.bilibili.com",
                "Accept-Encoding": "identity",
            },
        )
        request.headers.pop("cookie", None)
        response = await self.client.send(request, stream=True)
        try:
            yield response
        finally:
            await response.aclose()

    async def _probe_stream_size(
        self, stream: Dict[str, Any], referer: str
    ) -> Optional[int]:
        """通过单字节 Range 请求获取媒体流的完整大小。"""
        for url in _stream_urls(stream):
            urls_to_try = [url]
            for fallback_host in self._CDN_FALLBACK_HOSTS:
                fallback_url = self._replace_cdn_host(url, fallback_host)
                if fallback_url != url and fallback_url not in urls_to_try:
                    urls_to_try.append(fallback_url)

            for attempt_url in urls_to_try:
                try:
                    async with self._media_stream(
                        attempt_url, "bytes=0-0", referer
                    ) as response:
                        response.raise_for_status()
                        content_range = response.headers.get("content-range", "")
                        if "/" in content_range:
                            total = content_range.rsplit("/", 1)[-1]
                            if total.isdigit():
                                return int(total)
                        if response.status_code == 200:
                            content_length = response.headers.get("content-length")
                            if content_length and content_length.isdigit():
                                return int(content_length)
                except (httpx.HTTPError, OSError, ValueError):
                    continue
        return None

    async def _select_fitting_dash_streams(
        self,
        play_data: Dict[str, Any],
        referer: str,
        log_id: str = "未知视频",
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        selection_started = time.perf_counter()
        videos, audio = get_dash_stream_candidates(
            play_data, plugin_config.haruka_bili_video_quality
        )
        probe_started = time.perf_counter()
        audio_size = await self._probe_stream_size(audio, referer)
        logger.info(
            f"[B站视频][{log_id}] 音频流大小探测完成："
            f"{_format_size(audio_size)}，耗时 "
            f"{time.perf_counter() - probe_started:.2f} 秒"
        )
        for index, video in enumerate(videos):
            probe_started = time.perf_counter()
            video_size = await self._probe_stream_size(video, referer)
            quality = video.get("id", "未知")
            logger.info(
                f"[B站视频][{log_id}] 视频流大小探测完成："
                f"清晰度 ID {quality}，{_format_size(video_size)}，耗时 "
                f"{time.perf_counter() - probe_started:.2f} 秒"
            )
            known_size = (audio_size or 0) + (video_size or 0)
            total_size = (
                audio_size + video_size
                if audio_size is not None and video_size is not None
                else None
            )
            if known_size <= self.max_bytes:
                if index:
                    logger.info(
                        f"[B站视频][{log_id}] 最高画质超过大小限制，"
                        f"自动降级到清晰度 ID {video.get('id')}"
                    )
                logger.info(
                    f"[B站视频][{log_id}] DASH 流选择完成："
                    f"清晰度 ID {quality}，预计合计 {_format_size(total_size)}，"
                    f"耗时 {time.perf_counter() - selection_started:.2f} 秒"
                )
                return video, audio
        raise BiliVideoError(
            f"最低清晰度仍超过 {plugin_config.haruka_bili_video_max_size_mb} MB 限制"
        )

    async def _run_ffmpeg(
        self,
        inputs: Sequence[Path],
        output: Path,
        log_id: str = "未知视频",
    ) -> None:
        ffmpeg_started = time.perf_counter()
        logger.info(f"[B站视频][{log_id}] 开始 FFmpeg 无损合并")
        command: List[str] = [plugin_config.haruka_bili_video_ffmpeg, "-y"]
        for input_path in inputs:
            command.extend(["-i", str(input_path)])
        command.extend(["-c", "copy", "-movflags", "+faststart", str(output)])
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as error:
            raise BiliVideoError("未找到 FFmpeg，请安装后重启机器人") from error

        try:
            _, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=plugin_config.haruka_bili_video_timeout,
            )
        except asyncio.TimeoutError as error:
            process.kill()
            await process.communicate()
            raise BiliVideoError("FFmpeg 合并视频超时") from error
        if process.returncode != 0:
            detail = stderr.decode("utf-8", errors="replace")[-500:]
            logger.error(f"FFmpeg 合并 B 站视频失败：{detail}")
            raise BiliVideoError("FFmpeg 无法合并该视频")
        output_size = output.stat().st_size if output.is_file() else None
        logger.info(
            f"[B站视频][{log_id}] FFmpeg 合并完成："
            f"{_format_size(output_size)}，耗时 "
            f"{time.perf_counter() - ffmpeg_started:.2f} 秒"
        )

    async def download(
        self, reference: VideoReference, directory: Path
    ) -> Tuple[VideoInfo, Path]:
        download_started = time.perf_counter()
        log_id = reference.key[0]
        stage_started = time.perf_counter()
        info = await self.get_video_info(reference)
        log_id = info.bvid or log_id
        logger.info(
            f"[B站视频][{log_id}] 视频信息获取完成，耗时 "
            f"{time.perf_counter() - stage_started:.2f} 秒"
        )
        stage_started = time.perf_counter()
        play_data = await self.get_play_data(info)
        logger.info(
            f"[B站视频][{log_id}] 播放地址获取完成，耗时 "
            f"{time.perf_counter() - stage_started:.2f} 秒"
        )
        output = directory / "video.mp4"

        if play_data.get("dash"):
            video_stream, audio_stream = await self._select_fitting_dash_streams(
                play_data, info.canonical_url, log_id
            )
            video_path = directory / "video.m4s"
            audio_path = directory / "audio.m4s"
            tasks = [
                asyncio.create_task(
                    self._download_stream(
                        video_stream,
                        video_path,
                        info.canonical_url,
                        log_id,
                        "视频流",
                    )
                ),
                asyncio.create_task(
                    self._download_stream(
                        audio_stream,
                        audio_path,
                        info.canonical_url,
                        log_id,
                        "音频流",
                    )
                ),
            ]
            try:
                sizes = await asyncio.gather(*tasks)
            except Exception:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                raise
            if sum(sizes) > self.max_bytes:
                raise BiliVideoError(
                    "视频文件超过 "
                    f"{plugin_config.haruka_bili_video_max_size_mb} MB 限制"
                )
            await self._run_ffmpeg([video_path, audio_path], output, log_id)
        else:
            durl = play_data.get("durl") or []
            if not durl:
                raise BiliVideoError("B 站没有返回可下载的视频流")
            source = directory / "video_source"
            await self._download_stream(
                durl[0], source, info.canonical_url, log_id, "混合流"
            )
            await self._run_ffmpeg([source], output, log_id)

        if not output.is_file() or output.stat().st_size == 0:
            raise BiliVideoError("视频合并完成后没有生成有效文件")
        if output.stat().st_size > self.max_bytes:
            raise BiliVideoError(
                "合并后的视频超过 "
                f"{plugin_config.haruka_bili_video_max_size_mb} MB 限制"
            )
        logger.info(
            f"[B站视频][{log_id}] 下载与合并全部完成："
            f"{_format_size(output.stat().st_size)}，总耗时 "
            f"{time.perf_counter() - download_started:.2f} 秒"
        )
        return info, output


def _format_duration(seconds: int) -> str:
    minutes, second = divmod(max(seconds, 0), 60)
    hour, minute = divmod(minutes, 60)
    if hour:
        return f"{hour:02d}:{minute:02d}:{second:02d}"
    return f"{minute:02d}:{second:02d}"


def _format_size(size: Optional[int]) -> str:
    if size is None:
        return "大小未知"
    return f"{size / 1024 / 1024:.1f} MB"


def _video_base64_uri(video_path: Path) -> str:
    """将视频编码为 OneBot 可跨进程、跨容器传输的资源地址。"""
    encoded = base64.b64encode(video_path.read_bytes()).decode("ascii")
    return f"base64://{encoded}"


def _cleanup_stale_downloads(download_root: Path, min_age_seconds: int = 300) -> None:
    """清理超过 min_age_seconds 秒的旧下载目录（上次异常退出残留）。"""
    if not download_root.is_dir():
        return
    now = time.time()
    for child in download_root.iterdir():
        if child.is_dir() and child.name.startswith("download-"):
            try:
                if now - child.stat().st_mtime > min_age_seconds:
                    shutil.rmtree(child, ignore_errors=True)
                    logger.debug(f"已清理残留下载目录：{child}")
            except OSError:
                pass


async def send_forward_video(
    bot: Bot,
    event: GroupMessageEvent,
    info: VideoInfo,
    video_path: Path,
) -> None:
    part = f"\n分P：P{info.page_number} {info.page_name}" if info.page_count > 1 else ""
    description = Message(
        f"{info.title}\nUP：{info.owner}{part}\n"
        f"时长：{_format_duration(info.duration)}\n{info.canonical_url}"
    )
    size = video_path.stat().st_size
    size_mb = size / 1024 / 1024
    loop = asyncio.get_running_loop()
    encode_started = time.perf_counter()
    logger.info(
        f"[B站视频][{info.bvid}] 开始 Base64 编码：{size_mb:.1f} MB"
    )
    video_uri = await loop.run_in_executor(None, _video_base64_uri, video_path)
    encoded_size_mb = len(video_uri) / 1024 / 1024
    logger.info(
        f"[B站视频][{info.bvid}] Base64 编码完成："
        f"{encoded_size_mb:.1f} MB，耗时 "
        f"{time.perf_counter() - encode_started:.2f} 秒"
    )
    video = Message(MessageSegment.video(video_uri))
    node_base = {"name": "HarukaBot", "uin": str(bot.self_id)}
    logger.info(
        f"[B站视频][{info.bvid}] 开始调用 OneBot 合并转发："
        f"原文件 {size_mb:.1f} MB，Base64 {encoded_size_mb:.1f} MB，"
        f"OneBot 超时 {plugin_config.haruka_bili_video_timeout} 秒"
    )
    onebot_started = time.perf_counter()
    try:
        await bot.send_group_forward_msg(
            group_id=event.group_id,
            messages=[
                {
                    "type": "node",
                    "data": {**node_base, "content": description},
                },
                {
                    "type": "node",
                    "data": {**node_base, "content": video},
                },
            ],
            _timeout=plugin_config.haruka_bili_video_timeout,
        )
    except Exception as error:
        logger.warning(
            f"[B站视频][{info.bvid}] OneBot 合并转发失败："
            f"耗时 {time.perf_counter() - onebot_started:.2f} 秒，"
            f"异常类型 {type(error).__name__}：{error}"
        )
        raise
    logger.info(
        f"[B站视频][{info.bvid}] OneBot 合并转发成功，耗时 "
        f"{time.perf_counter() - onebot_started:.2f} 秒"
    )


def _http_client_options() -> Dict[str, Any]:
    headers = {
        "User-Agent": DEFAULT_USER_AGENT,
        "Referer": "https://www.bilibili.com/",
        "Accept-Encoding": "identity",
    }
    if plugin_config.haruka_bili_video_cookie:
        headers["Cookie"] = plugin_config.haruka_bili_video_cookie
    options: Dict[str, Any] = {
        "headers": headers,
        "follow_redirects": True,
        "timeout": httpx.Timeout(plugin_config.haruka_bili_video_timeout, connect=20),
    }
    if plugin_config.haruka_proxy:
        options["proxies"] = plugin_config.haruka_proxy
    return options


async def _enabled_group(event: GroupMessageEvent) -> bool:
    return event.group_id in set(plugin_config.haruka_bili_video_groups)


download_semaphore = asyncio.Semaphore(plugin_config.haruka_bili_video_concurrency)
bili_video = on_message(
    rule=Rule(_enabled_group),
    priority=20,
    block=False,
)


@bili_video.handle()
async def handle_bili_video(bot: Bot, event: GroupMessageEvent):
    async with httpx.AsyncClient(**_http_client_options()) as client:
        search_text = message_search_text(event)
        detected_urls = extract_message_urls(search_text)
        if not detected_urls:
            return
        resolve_started = time.perf_counter()
        references = await resolve_video_references(search_text, client)
        references = references[: plugin_config.haruka_bili_video_max_links]
        logger.info(
            f"[B站视频] 群 {event.group_id} 链接解析完成："
            f"检测 {len(detected_urls)} 个链接，识别 {len(references)} 个视频，耗时 "
            f"{time.perf_counter() - resolve_started:.2f} 秒"
        )
        if not references:
            return

        await bot.send_group_msg(
            group_id=event.group_id,
            message=f"检测到 {len(references)} 个 B 站视频，正在下载……",
        )
        downloader = BiliVideoDownloader(client)
        download_root = Path(get_path("bili_video"))
        download_root.mkdir(parents=True, exist_ok=True)

        # 清理上次异常退出可能残留的旧下载目录
        _cleanup_stale_downloads(download_root)

        for reference in references:
            task_started = time.perf_counter()
            wait_started = time.perf_counter()
            log_id = reference.key[0]
            logger.info(f"[B站视频][{log_id}] 等待下载并发槽位")
            try:
                async with download_semaphore:
                    logger.info(
                        f"[B站视频][{log_id}] 已取得下载并发槽位，等待 "
                        f"{time.perf_counter() - wait_started:.2f} 秒"
                    )
                    download_dir = download_root / f"download-{uuid.uuid4().hex[:12]}"
                    download_dir.mkdir(parents=True, exist_ok=True)
                    try:
                        info, video_path = await downloader.download(
                            reference, download_dir
                        )
                        await send_forward_video(bot, event, info, video_path)
                        logger.info(
                            f"[B站视频][{info.bvid}] 整条处理链路完成，总耗时 "
                            f"{time.perf_counter() - task_started:.2f} 秒"
                        )
                    finally:
                        # 视频内容已随 OneBot 请求传输，不再依赖 NapCat 读取本地路径。
                        shutil.rmtree(download_dir, ignore_errors=True)
            except BiliVideoError as error:
                logger.warning(f"B 站视频 {reference.key} 下载失败：{error}")
                await bot.send_group_msg(
                    group_id=event.group_id,
                    message=f"B 站视频下载失败：{error}",
                )
            except NetworkError as error:
                logger.warning(f"B 站视频 {reference.key} 发送超时：{error}")
                try:
                    await bot.send_group_msg(
                        group_id=event.group_id,
                        message=(
                            "B 站视频发送超时，OneBot 客户端可能仍在处理中，"
                            "请先确认群内是否收到视频"
                        ),
                    )
                except NetworkError as notify_error:
                    logger.warning(f"B 站视频发送超时提示发送失败：{notify_error}")
            except Exception:
                logger.exception(f"B 站视频 {reference.key} 处理失败")
                await bot.send_group_msg(
                    group_id=event.group_id,
                    message="B 站视频处理失败，请稍后重试",
                )
