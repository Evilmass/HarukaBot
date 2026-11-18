import asyncio
import shutil
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional, Set

from fastapi import HTTPException
from fastapi.responses import FileResponse

from .models import IMAGE_NAME_RE, VideoError

VIDEO_SERVE_PREFIX = "/haruka/video/files"
MEDIA_TYPES = {
    ".mp4": "video/mp4", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".png": "image/png", ".gif": "image/gif", ".webp": "image/webp",
}


@dataclass
class VideoJob:
    directory: Path
    path: Optional[Path] = None
    images: Dict[str, Path] = field(default_factory=dict)
    active: bool = True
    expires_at: Optional[float] = None


class VideoFileStore:
    """只提供已登记的媒体文件，目录和清理均按任务隔离。"""

    def __init__(self, root: Path, ttl: float = 300):
        self.root = root.resolve()
        self.ttl = ttl
        self.jobs: Dict[str, VideoJob] = {}
        self.cleanup_tasks: Set[asyncio.Task] = set()

    def create(self, platform: str) -> str:
        if platform not in {"bilibili", "douyin"}:
            raise VideoError("不支持的视频平台")
        task_id = uuid.uuid4().hex
        directory = self.root / platform / task_id
        directory.resolve().relative_to(self.root)
        directory.mkdir(parents=True)
        self.jobs[task_id] = VideoJob(directory)
        return task_id

    def register(self, task_id: str, path: Path) -> str:
        job = self.jobs[task_id]
        resolved = path.resolve()
        try:
            resolved.relative_to(self.root)
            resolved.relative_to(job.directory.resolve())
        except ValueError:
            raise VideoError("视频文件不在当前任务目录中") from None
        if not resolved.is_file() or resolved.name != "video.mp4":
            raise VideoError("没有生成有效的视频文件")
        job.path = resolved
        return f"{VIDEO_SERVE_PREFIX}/{task_id}/video.mp4"

    def register_image(self, task_id: str, path: Path) -> str:
        job = self.jobs[task_id]
        resolved = path.resolve()
        try:
            resolved.relative_to(self.root)
            resolved.relative_to(job.directory.resolve())
        except ValueError:
            raise VideoError("图片文件不在当前任务目录中") from None
        if not resolved.is_file() or not IMAGE_NAME_RE.fullmatch(resolved.name):
            raise VideoError("没有生成有效的图片文件")
        job.images[resolved.name] = resolved
        return f"{VIDEO_SERVE_PREFIX}/{task_id}/{resolved.name}"

    async def serve(self, task_id: str, filename: str = "video.mp4"):
        job = self.jobs.get(task_id)
        if job and job.expires_at is not None and time.time() >= job.expires_at:
            self.remove(task_id)
            job = None
        if job is None:
            raise HTTPException(status_code=404, detail="文件不存在或已过期")
        if filename == "video.mp4":
            path, media_type = job.path, MEDIA_TYPES[".mp4"]
        else:
            path, media_type = job.images.get(filename), MEDIA_TYPES.get(
                Path(filename).suffix
            )
        if path is None or media_type is None or not path.is_file():
            raise HTTPException(status_code=404, detail="文件不存在或已过期")
        try:
            path.resolve().relative_to(self.root)
            path.resolve().relative_to(job.directory.resolve())
        except ValueError:
            raise HTTPException(status_code=404, detail="文件不存在") from None
        return FileResponse(
            path, media_type=media_type,
            # 图片不设置 attachment 文件名，便于客户端直接内联显示。
            filename=filename if filename == "video.mp4" else None,
            headers={"Cache-Control": "no-store"},
        )

    def release(self, task_id: str) -> None:
        job = self.jobs[task_id]
        job.active = False
        job.expires_at = time.time() + self.ttl
        task = asyncio.create_task(self._expire(task_id))
        self.cleanup_tasks.add(task)
        task.add_done_callback(self.cleanup_tasks.discard)

    async def _expire(self, task_id: str) -> None:
        await asyncio.sleep(self.ttl)
        self.remove(task_id)

    def remove(self, task_id: str) -> None:
        job = self.jobs.pop(task_id, None)
        if job is not None:
            # 删除前再次验证目标，避免目录被替换后触及数据根目录之外。
            job.directory.resolve().relative_to(self.root)
            shutil.rmtree(job.directory, ignore_errors=True)

    def cleanup_stale(self, max_age: float = 900) -> None:
        """清理异常退出残留；当前进程管理的活跃/保留目录都不能删。"""
        tracked = {job.directory for job in self.jobs.values()}
        for platform in ("bilibili", "douyin"):
            directory = self.root / platform
            if not directory.is_dir() or directory.is_symlink():
                continue
            for child in directory.iterdir():
                if child in tracked or child.is_symlink() or not child.is_dir():
                    continue
                try:
                    child.resolve().relative_to(self.root)
                    if time.time() - child.stat().st_mtime > max_age:
                        shutil.rmtree(child, ignore_errors=True)
                except (OSError, ValueError):
                    continue

    async def close(self) -> None:
        tasks = list(self.cleanup_tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.cleanup_tasks.clear()
        for task_id in list(self.jobs):
            self.remove(task_id)
