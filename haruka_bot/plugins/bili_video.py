"""B 站视频接口的兼容入口，群消息处理统一由 video 插件负责。"""

from ..video.bilibili import (  # noqa: F401
    BiliVideoDownloader,
    BiliVideoError,
    VideoInfo,
    VideoReference,
    _http_client_options,
    extract_message_urls,
    extract_miniapp_video_urls,
    get_dash_stream_candidates,
    message_search_text,
    parse_video_url,
    resolve_video_references,
    select_dash_streams,
)
from ..video.service import send_bili_video as send_video  # noqa: F401
