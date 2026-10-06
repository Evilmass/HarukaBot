"""参考 ByteColtX/nonebot-plugin-ifollow 的详情接口和播放 URI 解析方式。"""

import asyncio
import http.cookiejar
import re
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlencode, urlparse

import httpx
from nonebot.log import logger

from ..config import Config, plugin_config
from .download import download_file
from .models import DownloadedVideo, VideoError, VideoSettings, VideoTooLarge

DETAIL_URL = "https://www.douyin.com/aweme/v1/web/aweme/detail/"
TTWID_URL = "https://ttwid.bytedance.com/ttwid/union/register/"
DOUYIN_HOSTS = {
    "douyin.com", "www.douyin.com", "m.douyin.com", "v.douyin.com",
    "jx.douyin.com", "jingxuan.douyin.com", "iesdouyin.com", "www.iesdouyin.com",
}
DOUYIN_URL_RE = re.compile(
    r"https?://(?:(?:(?:www|m|v|jx|jingxuan)\.)?douyin\.com|"
    r"(?:www\.)?iesdouyin\.com)/[0-9A-Za-z?&=_%./:+~#@-]*", re.IGNORECASE,
)
TRAILING_URL_CHARS = ".,;:!?，。；：！？)]}）】》\"'"
_ttwid: Optional[str] = None
_ttwid_lock = asyncio.Lock()


@dataclass(frozen=True)
class DouyinReference:
    original_url: str
    aweme_id: str
    kind: str = "video"

    @property
    def key(self) -> Tuple[str, int]:
        return self.aweme_id, 1


def extract_message_urls(text: str) -> List[str]:
    return list(dict.fromkeys(
        match.group(0).rstrip(TRAILING_URL_CHARS)
        for match in DOUYIN_URL_RE.finditer(text)
    ))


def parse_video_url(url: str) -> Optional[DouyinReference]:
    parsed = urlparse(url.rstrip(TRAILING_URL_CHARS))
    if parsed.scheme not in {"http", "https"} or parsed.hostname not in DOUYIN_HOSTS:
        return None
    matched = re.fullmatch(r"/(?:share/|m/)?(video|note)/([0-9]+)/?", parsed.path)
    if matched:
        return DouyinReference(url, matched[2], matched[1])
    modal_id = parse_qs(parsed.query).get("modal_id", [""])[0]
    if re.fullmatch(r"[0-9]+", modal_id):
        return DouyinReference(url, modal_id)
    return None


def load_cookies(config: Config = plugin_config) -> httpx.Cookies:
    """保留 Netscape 文件的域和路径信息，不输出文件内容。"""
    cookies = httpx.Cookies()
    if config.haruka_douyin_video_cookie:
        for item in config.haruka_douyin_video_cookie.split(";"):
            name, separator, value = item.strip().partition("=")
            if name and separator:
                cookies.set(name, value, domain=".douyin.com")
        return cookies
    root = Path(config.haruka_dir or Path.cwd() / "data")
    configured_path = config.haruka_douyin_video_cookie_file
    if configured_path == "":
        return cookies
    path = Path(configured_path) if configured_path else Path("douyin_cookies.txt")
    if not path.is_absolute():
        path = root / path
    if not path.exists():
        if configured_path:
            raise VideoError("找不到配置的抖音 Cookie 文件，请检查路径")
        return cookies
    jar = http.cookiejar.MozillaCookieJar(str(path))
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            jar.load(ignore_discard=True, ignore_expires=True)
    except (OSError, ValueError, http.cookiejar.LoadError):
        # LoadError 的正文可能带原始 Cookie 行，不能保留异常链。
        raise VideoError("无法读取抖音 Cookie 文件，请检查 Netscape 格式及权限") from None
    for cookie in jar:
        # 浏览器导出的 Netscape 文件常用 0 表示会话 Cookie。
        if cookie.expires == 0:
            cookie.expires = None
            cookie.discard = True
        if cookie.is_expired():
            continue
        domain = cookie.domain.lstrip(".").lower()
        if domain in {"douyin.com", "iesdouyin.com"} or domain.endswith(
            (".douyin.com", ".iesdouyin.com")
        ):
            cookies.jar.set_cookie(cookie)
    return cookies


def metadata_client_options(
    settings: VideoSettings, config: Config = plugin_config,
) -> Dict[str, Any]:
    options = settings.client_options()
    options["headers"].update({
        "Referer": "https://www.douyin.com/", "Origin": "https://open.douyin.com",
        "Accept": "application/json, text/plain, */*",
    })
    options["cookies"] = load_cookies(config)
    return options


class DouyinVideoProvider:
    def __init__(
        self, client: httpx.AsyncClient, media_client: httpx.AsyncClient,
        settings: VideoSettings,
    ):
        self.client = client
        self.media_client = media_client
        self.settings = settings

    async def resolve(self, url: str) -> DouyinReference:
        for _ in range(11):
            reference = parse_video_url(url)
            if reference:
                if reference.kind == "note":
                    raise VideoError("暂不支持抖音图集，仅支持视频作品")
                return reference
            parsed = urlparse(url)
            if parsed.scheme not in {"http", "https"} or parsed.hostname not in DOUYIN_HOSTS:
                raise VideoError("链接没有跳转到可识别的抖音视频")
            try:
                response = await self.client.get(url, follow_redirects=False)
                if not response.has_redirect_location:
                    response.raise_for_status()
            except httpx.HTTPError:
                raise VideoError("抖音短链接解析失败，请稍后重试") from None
            if not response.has_redirect_location:
                raise VideoError("仅支持抖音视频作品，不支持作者主页或直播")
            url = str(response.url.join(response.headers["location"]))
        raise VideoError("抖音短链接重定向次数过多")

    async def _ensure_ttwid(self) -> None:
        global _ttwid
        # 用实际请求的 Cookie 头判断域和路径是否匹配，保留用户有效的 ttwid。
        cookie_header = self.client.build_request("GET", DETAIL_URL).headers.get("cookie", "")
        if any(
            item.strip().partition("=")[0] == "ttwid"
            and item.strip().partition("=")[2]
            for item in cookie_header.split(";")
        ):
            return
        async with _ttwid_lock:
            if _ttwid is None:
                try:
                    response = await self.client.post(TTWID_URL, json={
                        "region": "cn", "aid": 1768, "needFid": False,
                        "service": "www.douyin.com",
                        "migrate_info": {"ticket": "", "source": "node"},
                        "cbUrlProtocol": "https", "union": True,
                    })
                    response.raise_for_status()
                    _ttwid = response.cookies.get("ttwid")
                except httpx.HTTPError:
                    raise VideoError("抖音访客凭据获取失败") from None
                if not _ttwid:
                    raise VideoError("抖音接口未返回访客凭据")
            self.client.cookies.set("ttwid", _ttwid, domain=".douyin.com")

    async def fetch_detail(self, reference: DouyinReference) -> Dict[str, Any]:
        try:
            await self._ensure_ttwid()
        except VideoError:
            logger.warning("[抖音视频] 获取访客凭据失败，继续尝试作品详情接口")
        try:
            response = await self.client.get(
                DETAIL_URL, params={"aweme_id": reference.aweme_id, "aid": "6383"},
            )
            if response.status_code == 404:
                raise VideoError("抖音作品不存在、已删除或无法访问")
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError):
            raise VideoError("抖音接口受限或请求失败，请更新 Cookie 后重试") from None
        if not isinstance(payload, dict) or payload.get("status_code", 0) not in (0, "0", None):
            raise VideoError("抖音接口拒绝请求，请更新 Cookie 后重试")
        detail = payload.get("aweme_detail")
        if not isinstance(detail, dict):
            raise VideoError("抖音作品不可用或接口受限，请检查链接及 Cookie")
        if str(detail.get("aweme_id", "")) != reference.aweme_id:
            raise VideoError("抖音接口返回了不匹配的作品")
        if detail.get("images"):
            raise VideoError("暂不支持抖音图集，仅支持视频作品")
        if not isinstance(detail.get("video"), dict):
            raise VideoError("该抖音作品没有可下载的视频")
        return detail

    async def download(self, reference: DouyinReference, directory: Path) -> DownloadedVideo:
        detail = await self.fetch_detail(reference)
        video = detail["video"]
        address = video.get("play_addr") or {}
        if not isinstance(address, dict):
            raise VideoError("抖音没有返回可用的播放地址")
        urls: List[str] = []
        uri = address.get("uri")
        if uri:
            urls.append("https://aweme.snssdk.com/aweme/v1/play/?" + urlencode({
                "video_id": str(uri), "ratio": "1080p", "line": "0",
            }))
        url_list = address.get("url_list") or []
        if isinstance(url_list, list):
            urls.extend(url for url in url_list if isinstance(url, str))
        urls = list(dict.fromkeys(
            url for url in urls if urlparse(url).scheme in {"http", "https"}
        ))
        if not urls:
            raise VideoError("抖音没有返回可用的播放地址")
        path = directory / "video.mp4"
        for url in urls:
            try:
                await download_file(
                    self.media_client, url, path, self.settings.max_bytes,
                    {"Referer": "https://www.douyin.com/", "Accept-Encoding": "identity"},
                    require_mp4=True, scope=f"[抖音视频][{reference.aweme_id}]",
                )
                break
            except VideoTooLarge:
                raise
            except (httpx.HTTPError, ValueError, VideoError) as error:
                logger.warning(
                    f"[抖音视频][{reference.aweme_id}] 尝试备用播放地址：{type(error).__name__}"
                )
        else:
            raise VideoError("抖音视频下载失败，播放地址可能已失效，请稍后重试") from None
        author = detail.get("author") or {}
        duration = video.get("duration") or 0
        return DownloadedVideo(
            platform="douyin", video_id=reference.aweme_id,
            title=str(detail.get("desc") or "未命名抖音视频"),
            author=str(author.get("nickname") or "未知作者") if isinstance(author, dict) else "未知作者",
            duration=max(int(duration) // 1000, 0),
            canonical_url=f"https://www.douyin.com/video/{reference.aweme_id}", path=path,
        )
