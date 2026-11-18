"""B 站和抖音共用的视频与图集下载服务。"""

from .models import DownloadedVideo, VideoError, VideoSettings

__all__ = ["DownloadedVideo", "VideoError", "VideoSettings"]
