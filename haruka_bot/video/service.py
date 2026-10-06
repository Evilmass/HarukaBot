import asyncio
import shutil
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import httpx
from nonebot.adapters.onebot.v11 import Bot, Message, MessageSegment
from nonebot.adapters.onebot.v11.event import GroupMessageEvent
from nonebot.adapters.onebot.v11.exception import NetworkError
from nonebot.log import logger

from ..config import Config, plugin_config
from . import bilibili, douyin
from .files import VideoFileStore
from .messages import message_search_text
from .models import DownloadedVideo, VideoError, VideoSettings, VideoTooLarge

PLATFORM_NAMES = {"bilibili": "B 站", "douyin": "抖音"}


def allowed_platforms(group_id: int, config: Config = plugin_config) -> Set[str]:
    platforms = set()
    if group_id in config.haruka_bili_video_groups:
        platforms.add("bilibili")
    if group_id in config.haruka_douyin_video_groups:
        platforms.add("douyin")
    return platforms


def message_candidates(event: GroupMessageEvent, platforms: Set[str]) -> List[Tuple[str, str]]:
    text = message_search_text(event)
    candidates = []
    if "bilibili" in platforms:
        candidates.extend(("bilibili", url) for url in bilibili.extract_message_urls(text))
    if "douyin" in platforms:
        candidates.extend(("douyin", url) for url in douyin.extract_message_urls(text))
    candidates.sort(key=lambda item: text.find(item[1]))
    if "bilibili" in platforms:
        candidates.extend(("bilibili", url) for url in bilibili.extract_miniapp_video_urls(event))
    return list(dict.fromkeys(candidates))


async def send_video(
    bot: Bot, event: GroupMessageEvent, video: DownloadedVideo,
    store: VideoFileStore, task_id: str, settings: VideoSettings,
) -> None:
    if not settings.public_base_url:
        raise VideoError("未配置 HARUKA_VIDEO_PUBLIC_BASE_URL 或旧的 B 站公开地址")
    size = video.path.stat().st_size
    if not size:
        raise VideoError("下载的视频文件为空")
    if size > settings.max_bytes:
        raise VideoTooLarge(f"视频文件超过 {settings.max_size_mb} MB 限制")
    relative = store.register(task_id, video.path)
    video_url = f"{settings.public_base_url.rstrip('/')}{relative}"
    nodes = [
        {"type": "node", "data": {
            "name": "HarukaBot", "uin": bot.self_id, "content": video.description,
        }},
        {"type": "node", "data": {
            "name": "HarukaBot", "uin": bot.self_id,
            "content": Message(MessageSegment.video(video_url)),
        }},
    ]
    await bot.send_group_forward_msg(
        group_id=event.group_id, messages=nodes, _timeout=settings.timeout,
    )


class VideoService:
    def __init__(self, config: Config = plugin_config, store: Optional[VideoFileStore] = None):
        self.config = config
        self.settings = VideoSettings.from_config(config)
        root = Path(config.haruka_dir or Path.cwd() / "data") / "video"
        self.store = store or VideoFileStore(root)
        self.semaphore = asyncio.Semaphore(self.settings.concurrency)
        self.handlers: Set[asyncio.Task] = set()
        self.sweeper: Optional[asyncio.Task] = None

    async def _provider(self, platform: str, stack: AsyncExitStack):
        if platform == "bilibili":
            client = await stack.enter_async_context(httpx.AsyncClient(
                **bilibili._http_client_options(self.settings)
            ))
            return bilibili.BiliVideoProvider(client, self.settings)
        client = await stack.enter_async_context(httpx.AsyncClient(
            **douyin.metadata_client_options(self.settings, self.config)
        ))
        media_client = await stack.enter_async_context(httpx.AsyncClient(
            **self.settings.client_options()
        ))
        return douyin.DouyinVideoProvider(client, media_client, self.settings)

    async def _process(self, provider, platform, url, bot, event, seen) -> bool:
        reference = await provider.resolve(url)
        key = (platform, *reference.key)
        if key in seen:
            return False
        seen.add(key)
        task_id = self.store.create(platform)
        job = self.store.jobs[task_id]
        succeeded = False
        try:
            video = await provider.download(reference, job.directory)
            await send_video(bot, event, video, self.store, task_id, self.settings)
            succeeded = True
            # 最终 MP4 已登记；原始 DASH 流不需要在保留期间继续占用空间。
            for path in job.directory.iterdir():
                if path != video.path and path.is_file():
                    try:
                        path.unlink()
                    except OSError:
                        logger.warning("[视频下载] 原始流清理失败，将随任务目录延迟清理")
            logger.info(f"[{PLATFORM_NAMES[platform]}视频][{video.video_id}] 下载及合并转发完成")
        finally:
            if succeeded:
                self.store.release(task_id)
            else:
                self.store.remove(task_id)
        return True

    async def _notify(self, bot, event, message: str) -> None:
        try:
            await bot.send_group_msg(group_id=event.group_id, message=message)
        except Exception as error:
            logger.warning(f"[视频下载] 提示发送失败：{type(error).__name__}")

    async def handle(self, bot: Bot, event: GroupMessageEvent) -> None:
        if str(getattr(event, "user_id", "")) == str(bot.self_id):
            return
        candidates = message_candidates(event, allowed_platforms(event.group_id, self.config))
        if not candidates:
            return
        if not self.settings.public_base_url:
            await self._notify(bot, event, "未配置 HARUKA_VIDEO_PUBLIC_BASE_URL 或旧的 B 站公开地址")
            return
        current = asyncio.current_task()
        self.handlers.add(current)
        try:
            await self._notify(bot, event, "检测到视频链接，正在下载……")
            providers: Dict[str, object] = {}
            seen = set()
            processed = 0
            async with AsyncExitStack() as stack:
                for platform, url in candidates:
                    if processed >= self.settings.max_links:
                        break
                    name = PLATFORM_NAMES[platform]
                    try:
                        async with self.semaphore:
                            if platform not in providers:
                                providers[platform] = await self._provider(platform, stack)
                            consumed = await asyncio.wait_for(
                                self._process(providers[platform], platform, url, bot, event, seen),
                                timeout=self.settings.timeout,
                            )
                            processed += int(consumed)
                    except VideoError as error:
                        processed += 1
                        await self._notify(bot, event, f"{name}视频处理失败：{error}")
                    except (NetworkError, asyncio.TimeoutError):
                        processed += 1
                        await self._notify(bot, event, f"{name}视频处理或发送超时，请稍后重试")
                    except Exception as error:
                        processed += 1
                        logger.warning(f"[{name}视频] 处理失败：{type(error).__name__}")
                        await self._notify(bot, event, f"{name}视频处理或发送失败，请稍后重试")
        finally:
            self.handlers.discard(current)

    async def start(self) -> None:
        self.store.cleanup_stale(self.settings.timeout + 300)
        self.sweeper = asyncio.create_task(self._sweep())

    async def _sweep(self) -> None:
        while True:
            await asyncio.sleep(60)
            self.store.cleanup_stale(self.settings.timeout + 300)

    async def close(self) -> None:
        tasks = list(self.handlers)
        if self.sweeper is not None:
            tasks.append(self.sweeper)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await self.store.close()


video_service = VideoService()


async def send_bili_video(bot, event, info, path: Path) -> None:
    """兼容原 send_video 接口，独立调用也使用共用发送与清理流程。"""
    task_id = video_service.store.create("bilibili")
    target = video_service.store.jobs[task_id].directory / "video.mp4"
    try:
        shutil.copyfile(path, target)
        await send_video(
            bot, event, bilibili.as_downloaded(info, target), video_service.store,
            task_id, VideoSettings.from_config(),
        )
    except BaseException:
        video_service.store.remove(task_id)
        raise
    video_service.store.release(task_id)
