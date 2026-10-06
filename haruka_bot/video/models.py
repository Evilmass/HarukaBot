from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

import httpx

from ..config import Config, plugin_config

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)


class VideoError(RuntimeError):
    """可以展示给用户的错误，不包含 Cookie 或接口响应正文。"""


class VideoTooLarge(VideoError):
    pass


@dataclass(frozen=True)
class DownloadedVideo:
    platform: str
    video_id: str
    title: str
    author: str
    duration: int
    canonical_url: str
    path: Path
    part_label: Optional[str] = None

    @property
    def description(self) -> str:
        seconds = max(int(self.duration), 0)
        minutes, seconds = divmod(seconds, 60)
        hours, minutes = divmod(minutes, 60)
        duration = (
            f"{hours:02d}:{minutes:02d}:{seconds:02d}"
            if hours else f"{minutes:02d}:{seconds:02d}"
        )
        author_label = "UP" if self.platform == "bilibili" else "作者"
        part = f"\n分P：{self.part_label}" if self.part_label else ""
        return (
            f"{self.title}\n{author_label}：{self.author}{part}\n"
            f"时长：{duration}\n{self.canonical_url}"
        )


@dataclass(frozen=True)
class VideoSettings:
    public_base_url: Optional[str]
    max_size_mb: int
    max_links: int
    concurrency: int
    ffmpeg: str
    timeout: int

    @classmethod
    def from_config(cls, config: Config = plugin_config) -> "VideoSettings":
        def value(name: str):
            common = getattr(config, f"haruka_video_{name}")
            return common if common is not None else getattr(
                config, f"haruka_bili_video_{name}"
            )

        return cls(**{name: value(name) for name in cls.__dataclass_fields__})

    @property
    def max_bytes(self) -> int:
        return self.max_size_mb * 1024 * 1024

    def client_options(self) -> Dict[str, Any]:
        options: Dict[str, Any] = {
            "headers": {"User-Agent": DEFAULT_USER_AGENT, "Accept-Encoding": "identity"},
            "timeout": httpx.Timeout(self.timeout, connect=20),
            "follow_redirects": True,
        }
        if plugin_config.haruka_proxy:
            options["proxies"] = plugin_config.haruka_proxy
        return options
