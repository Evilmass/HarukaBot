import asyncio
import json
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import parse_qs, urlparse

import httpx
from nonebot import on_message
from nonebot.adapters.onebot.v11 import Bot, Message, MessageSegment
from nonebot.adapters.onebot.v11.event import GroupMessageEvent
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


def select_dash_streams(
    play_data: Dict[str, Any], max_quality: int
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """选择不超过配置清晰度的最高画质，编码相同时优先 AVC。"""
    dash = play_data.get("dash") or {}
    videos = list(dash.get("video") or [])
    audios = list(dash.get("audio") or [])
    if not videos or not audios:
        raise BiliVideoError("该视频没有可下载的 DASH 视频或音频流")

    allowed = [item for item in videos if int(item.get("id", 0)) <= max_quality]
    if not allowed:
        allowed = videos
    quality = max(int(item.get("id", 0)) for item in allowed)
    same_quality = [
        item for item in allowed if int(item.get("id", 0)) == quality
    ]

    def video_rank(item: Dict[str, Any]) -> Tuple[int, int]:
        codec = str(item.get("codecs", "")).lower()
        avc_compatible = int(codec.startswith("avc") or codec.startswith("h264"))
        return avc_compatible, int(item.get("bandwidth", 0))

    video = max(same_quality, key=video_rank)
    audio = max(audios, key=lambda item: int(item.get("bandwidth", 0)))
    return video, audio


class BiliVideoDownloader:
    def __init__(self, client: httpx.AsyncClient):
        self.client = client
        self.max_bytes = plugin_config.haruka_bili_video_max_size_mb * 1024 * 1024

    async def _api_get(
        self, url: str, params: Dict[str, Any]
    ) -> Dict[str, Any]:
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
        self, stream: Dict[str, Any], target: Path
    ) -> int:
        last_error: Optional[Exception] = None
        for url in _stream_urls(stream):
            size = 0
            try:
                async with self.client.stream("GET", url) as response:
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
                return size
            except BiliVideoError:
                target.unlink(missing_ok=True)
                raise
            except (httpx.HTTPError, OSError, ValueError) as error:
                last_error = error
                target.unlink(missing_ok=True)
        raise BiliVideoError(f"下载 B 站媒体流失败：{last_error}")

    async def _run_ffmpeg(self, inputs: Sequence[Path], output: Path) -> None:
        command: List[str] = [plugin_config.haruka_bili_video_ffmpeg, "-y"]
        for input_path in inputs:
            command.extend(["-i", str(input_path)])
        command.extend(
            ["-c", "copy", "-movflags", "+faststart", str(output)]
        )
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as error:
            raise BiliVideoError(
                "未找到 FFmpeg，请安装后重启机器人"
            ) from error

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

    async def download(
        self, reference: VideoReference, directory: Path
    ) -> Tuple[VideoInfo, Path]:
        info = await self.get_video_info(reference)
        play_data = await self.get_play_data(info)
        output = directory / "video.mp4"

        if play_data.get("dash"):
            video_stream, audio_stream = select_dash_streams(
                play_data, plugin_config.haruka_bili_video_quality
            )
            video_path = directory / "video.m4s"
            audio_path = directory / "audio.m4s"
            tasks = [
                asyncio.create_task(
                    self._download_stream(video_stream, video_path)
                ),
                asyncio.create_task(
                    self._download_stream(audio_stream, audio_path)
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
            await self._run_ffmpeg([video_path, audio_path], output)
        else:
            durl = play_data.get("durl") or []
            if not durl:
                raise BiliVideoError("B 站没有返回可下载的视频流")
            source = directory / "video_source"
            await self._download_stream(durl[0], source)
            await self._run_ffmpeg([source], output)

        if not output.is_file() or output.stat().st_size == 0:
            raise BiliVideoError("视频合并完成后没有生成有效文件")
        if output.stat().st_size > self.max_bytes:
            raise BiliVideoError(
                "合并后的视频超过 "
                f"{plugin_config.haruka_bili_video_max_size_mb} MB 限制"
            )
        return info, output


def _format_duration(seconds: int) -> str:
    minutes, second = divmod(max(seconds, 0), 60)
    hour, minute = divmod(minutes, 60)
    if hour:
        return f"{hour:02d}:{minute:02d}:{second:02d}"
    return f"{minute:02d}:{second:02d}"


async def send_forward_video(
    bot: Bot,
    event: GroupMessageEvent,
    info: VideoInfo,
    video_path: Path,
) -> None:
    part = (
        f"\n分P：P{info.page_number} {info.page_name}"
        if info.page_count > 1
        else ""
    )
    description = Message(
        f"{info.title}\nUP：{info.owner}{part}\n"
        f"时长：{_format_duration(info.duration)}\n{info.canonical_url}"
    )
    video = Message(MessageSegment.video(video_path.resolve().as_uri()))
    node_base = {"name": "HarukaBot", "uin": str(bot.self_id)}
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
    )


def _http_client_options() -> Dict[str, Any]:
    headers = {
        "User-Agent": plugin_config.haruka_browser_ua or DEFAULT_USER_AGENT,
        "Referer": "https://www.bilibili.com/",
        "Accept-Encoding": "identity",
    }
    if plugin_config.haruka_bili_video_cookie:
        headers["Cookie"] = plugin_config.haruka_bili_video_cookie
    options: Dict[str, Any] = {
        "headers": headers,
        "follow_redirects": True,
        "timeout": httpx.Timeout(
            plugin_config.haruka_bili_video_timeout, connect=20
        ),
    }
    if plugin_config.haruka_proxy:
        options["proxies"] = plugin_config.haruka_proxy
    return options


async def _enabled_group(event: GroupMessageEvent) -> bool:
    return event.group_id in set(plugin_config.haruka_bili_video_groups)


download_semaphore = asyncio.Semaphore(
    plugin_config.haruka_bili_video_concurrency
)
bili_video = on_message(
    rule=Rule(_enabled_group),
    priority=20,
    block=False,
)


@bili_video.handle()
async def handle_bili_video(bot: Bot, event: GroupMessageEvent):
    async with httpx.AsyncClient(**_http_client_options()) as client:
        references = await resolve_video_references(
            message_search_text(event), client
        )
        references = references[: plugin_config.haruka_bili_video_max_links]
        if not references:
            return

        await bot.send_group_msg(
            group_id=event.group_id,
            message=f"检测到 {len(references)} 个 B 站视频，正在下载……",
        )
        downloader = BiliVideoDownloader(client)
        download_root = Path(get_path("bili_video"))
        download_root.mkdir(parents=True, exist_ok=True)

        for reference in references:
            try:
                async with download_semaphore:
                    with tempfile.TemporaryDirectory(
                        prefix="download-", dir=str(download_root)
                    ) as temporary_dir:
                        info, video_path = await downloader.download(
                            reference, Path(temporary_dir)
                        )
                        await send_forward_video(
                            bot, event, info, video_path
                        )
            except BiliVideoError as error:
                logger.warning(
                    f"B 站视频 {reference.key} 下载失败：{error}"
                )
                await bot.send_group_msg(
                    group_id=event.group_id,
                    message=f"B 站视频下载失败：{error}",
                )
            except Exception:
                logger.exception(f"B 站视频 {reference.key} 处理失败")
                await bot.send_group_msg(
                    group_id=event.group_id,
                    message="B 站视频处理失败，请稍后重试",
                )
