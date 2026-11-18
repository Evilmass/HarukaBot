"""已下载视频与图集的短期缓存，避免同一作品重复解析与下载。"""

import hashlib
import json
import math
import os
import re
import shutil
import time
import uuid
from pathlib import Path
from typing import List, Optional, Tuple

from .models import IMAGE_NAME_RE, IMAGE_SUFFIXES, DownloadedVideo, VideoError

CACHE_VERSION = 2
PLATFORMS = {"bilibili", "douyin"}
VIDEO_FILE = "video.mp4"
_UNSAFE_NAME = re.compile(r"[^0-9A-Za-z_-]+")


def _image_suffix(path: Path) -> str:
    suffix = path.suffix.lower()
    return suffix if suffix in IMAGE_SUFFIXES else ".jpg"


def _cache_files(video: DownloadedVideo) -> List[Tuple[str, Path]]:
    """缓存内统一使用 video.mp4 或 image-001.jpg 形式的文件名。"""
    if not video.image_paths:
        return [(VIDEO_FILE, video.path)]
    return [
        (f"image-{index:03d}{_image_suffix(path)}", path)
        for index, path in enumerate(video.image_paths, 1)
    ]


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

    @staticmethod
    def _load_meta(entry: Path, platform: str) -> Optional[dict]:
        try:
            meta = json.loads((entry / "meta.json").read_text("utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(meta, dict):
            return None
        created = meta.get("created_at")
        if (
            not isinstance(created, (int, float))
            or not math.isfinite(created)
            or meta.get("version") != CACHE_VERSION
            or meta.get("platform") != platform
        ):
            return None
        return meta

    @staticmethod
    def _media_names(meta: dict) -> Optional[List[str]]:
        """校验缓存记录的文件名，避免通过元数据访问条目目录之外的文件。"""
        names = meta.get("files")
        kind = meta.get("kind")
        if not isinstance(names, list) or not names:
            return None
        if kind == "video":
            return [VIDEO_FILE] if names == [VIDEO_FILE] else None
        if kind != "images" or not all(
            isinstance(name, str) and IMAGE_NAME_RE.fullmatch(name) for name in names
        ):
            return None
        return names

    def lookup(self, platform: str, key: Tuple[str, int]) -> Optional[DownloadedVideo]:
        if not self.enabled:
            return None
        entry = self._entry(platform, key)
        if not self._inside(entry):
            return None
        meta = self._load_meta(entry, platform)
        if meta is None:
            self._remove(entry)
            return None
        if (
            meta.get("key") != [str(key[0]), int(key[1])]
            or time.time() - meta["created_at"] > self.ttl
        ):
            self._remove(entry)
            return None
        names = self._media_names(meta)
        if names is None:
            self._remove(entry)
            return None
        paths = [entry / name for name in names]
        try:
            if not all(path.is_file() and path.stat().st_size for path in paths):
                raise OSError
            return DownloadedVideo(
                platform=platform,
                video_id=str(meta["video_id"]),
                title=str(meta["title"]),
                author=str(meta["author"]),
                duration=max(int(meta.get("duration", 0)), 0),
                canonical_url=str(meta["canonical_url"]),
                path=paths[0],
                part_label=(
                    str(meta["part_label"]) if meta.get("part_label") else None
                ),
                image_paths=tuple(paths) if meta.get("kind") == "images" else (),
            )
        except (KeyError, TypeError, ValueError, OSError):
            self._remove(entry)
            return None

    def save(self, video: DownloadedVideo, platform: str, key: Tuple[str, int]) -> None:
        if not self.enabled:
            return
        files = _cache_files(video)
        for _, source in files:
            if not source.is_file() or not source.stat().st_size:
                raise VideoError("没有可缓存的媒体文件")
        entry = self._entry(platform, key)
        if not self._inside(entry):
            raise VideoError("视频缓存目录不合法")
        entry.mkdir(parents=True, exist_ok=True)
        staged: List[Tuple[Path, Path]] = []
        temp_meta = entry / f".meta-{uuid.uuid4().hex}.tmp"
        try:
            for name, source in files:
                temp = entry / f".{uuid.uuid4().hex}.tmp"
                link_or_copy(source, temp)
                staged.append((temp, entry / name))
            for temp, target in staged:
                os.replace(temp, target)
            meta = {
                "version": CACHE_VERSION,
                "platform": platform,
                "key": [str(key[0]), int(key[1])],
                "kind": "images" if video.image_paths else "video",
                "files": [name for name, _ in files],
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
            # 作品从视频变为图集（或图片数量减少）时移除旧文件。
            keep = {name for name, _ in files} | {"meta.json"}
            for child in entry.iterdir():
                if (
                    child.is_file()
                    and child.name not in keep
                    and (
                        child.name == VIDEO_FILE
                        or IMAGE_NAME_RE.fullmatch(child.name)
                    )
                ):
                    child.unlink(missing_ok=True)
        except BaseException:
            for temp, _ in staged:
                temp.unlink(missing_ok=True)
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
                meta = self._load_meta(entry, platform)
                names = self._media_names(meta) if meta is not None else None
                if names is not None:
                    try:
                        complete = all((entry / name).is_file() for name in names)
                    except OSError:
                        complete = False
                    if complete:
                        if now - float(meta["created_at"]) > self.ttl:
                            self._remove(entry)
                        continue
                try:
                    # 给正在写入的目录留出余量，避免与保存流程冲突。
                    age = now - entry.stat().st_mtime
                except OSError:
                    continue
                if age > 60:
                    self._remove(entry)
