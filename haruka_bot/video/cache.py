"""已下载视频的短期缓存，避免同一作品重复解析与下载。"""

import hashlib
import json
import math
import os
import re
import shutil
import time
import uuid
from pathlib import Path
from typing import Optional, Tuple

from .models import DownloadedVideo, VideoError

CACHE_VERSION = 1
PLATFORMS = {"bilibili", "douyin"}
_UNSAFE_NAME = re.compile(r"[^0-9A-Za-z_-]+")


def link_or_copy(source: Path, target: Path) -> None:
    """优先建立硬链接，跨文件系统等场景回退为复制。"""
    try:
        os.link(source, target)
    except OSError:
        shutil.copyfile(source, target)


class VideoCache:
    """按平台和作品键保存最终 MP4，并按创建时间判断是否过期。"""

    def __init__(self, root: Path, ttl: float, enabled: bool = True):
        self.root = root.resolve()
        self.ttl = ttl
        self.enabled = enabled

    def _entry(self, platform: str, key: Tuple[str, int]) -> Path:
        if platform not in PLATFORMS:
            raise VideoError("不支持的视频平台")
        name = _UNSAFE_NAME.sub("_", f"{key[0]}-p{key[1]}").strip("_") or "video"
        if len(name) > 96:
            digest = hashlib.sha256(name.encode("utf-8")).hexdigest()[:16]
            name = f"{name[:79]}-{digest}"
        return self.root / platform / name

    def _inside(self, path: Path) -> bool:
        try:
            path.resolve().relative_to(self.root)
            return True
        except (OSError, ValueError):
            return False

    def _remove(self, entry: Path) -> None:
        if entry == self.root or not self._inside(entry):
            return
        shutil.rmtree(entry, ignore_errors=True)

    def lookup(self, platform: str, key: Tuple[str, int]) -> Optional[DownloadedVideo]:
        if not self.enabled:
            return None
        entry = self._entry(platform, key)
        if not self._inside(entry):
            return None
        try:
            meta = json.loads((entry / "meta.json").read_text("utf-8"))
        except (OSError, ValueError):
            self._remove(entry)
            return None
        if not isinstance(meta, dict):
            self._remove(entry)
            return None
        created = meta.get("created_at")
        if (
            not isinstance(created, (int, float))
            or not math.isfinite(created)
            or time.time() - created > self.ttl
            or meta.get("version") != CACHE_VERSION
            or meta.get("platform") != platform
            or meta.get("key") != [str(key[0]), int(key[1])]
        ):
            self._remove(entry)
            return None
        video_path = entry / "video.mp4"
        try:
            if not video_path.is_file() or not video_path.stat().st_size:
                raise OSError
            return DownloadedVideo(
                platform=platform,
                video_id=str(meta["video_id"]),
                title=str(meta["title"]),
                author=str(meta["author"]),
                duration=max(int(meta.get("duration", 0)), 0),
                canonical_url=str(meta["canonical_url"]),
                path=video_path,
                part_label=(
                    str(meta["part_label"]) if meta.get("part_label") else None
                ),
            )
        except (KeyError, TypeError, ValueError, OSError):
            self._remove(entry)
            return None

    def save(self, video: DownloadedVideo, platform: str, key: Tuple[str, int]) -> None:
        if not self.enabled:
            return
        source = video.path.resolve()
        if not source.is_file() or not source.stat().st_size:
            raise VideoError("没有可缓存的视频文件")
        entry = self._entry(platform, key)
        if not self._inside(entry):
            raise VideoError("视频缓存目录不合法")
        entry.mkdir(parents=True, exist_ok=True)
        target = entry / "video.mp4"
        temp_video = entry / f".video-{uuid.uuid4().hex}.tmp"
        temp_meta = entry / f".meta-{uuid.uuid4().hex}.tmp"
        try:
            link_or_copy(source, temp_video)
            os.replace(temp_video, target)
            meta = {
                "version": CACHE_VERSION,
                "platform": platform,
                "key": [str(key[0]), int(key[1])],
                "video_id": video.video_id,
                "title": video.title,
                "author": video.author,
                "duration": int(video.duration),
                "canonical_url": video.canonical_url,
                "part_label": video.part_label,
                "created_at": time.time(),
            }
            temp_meta.write_text(
                json.dumps(meta, ensure_ascii=False), encoding="utf-8"
            )
            os.replace(temp_meta, entry / "meta.json")
        except BaseException:
            temp_video.unlink(missing_ok=True)
            temp_meta.unlink(missing_ok=True)
            raise

    def cleanup(self) -> None:
        """清理过期条目和异常退出留下的不完整目录。"""
        if not self.enabled or not self.root.is_dir():
            return
        now = time.time()
        for platform in PLATFORMS:
            directory = self.root / platform
            if not directory.is_dir() or directory.is_symlink():
                continue
            try:
                entries = list(directory.iterdir())
            except OSError:
                continue
            for entry in entries:
                if entry.is_symlink() or not entry.is_dir():
                    continue
                try:
                    meta = json.loads((entry / "meta.json").read_text("utf-8"))
                    created = float(meta["created_at"])
                    valid = (
                        isinstance(meta, dict)
                        and math.isfinite(created)
                        and meta.get("version") == CACHE_VERSION
                        and meta.get("platform") == platform
                        and (entry / "video.mp4").is_file()
                    )
                except (OSError, ValueError, KeyError, TypeError):
                    valid, created = False, None
                if valid:
                    if now - created > self.ttl:
                        self._remove(entry)
                    continue
                try:
                    # 给正在写入的目录留出余量，避免与保存流程冲突。
                    age = now - entry.stat().st_mtime
                except OSError:
                    continue
                if age > 60:
                    self._remove(entry)
