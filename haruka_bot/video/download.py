import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Dict, Optional

import httpx
from nonebot.log import logger

from .models import VideoError, VideoTooLarge


@asynccontextmanager
async def media_stream(
    client: httpx.AsyncClient, url: str, headers: Dict[str, str],
    byte_range: str = "bytes=0-",
):
    """每次重定向都移除 Cookie，避免复用 API 客户端时泄露登录凭据。"""
    for _ in range(11):
        request = client.build_request("GET", url, headers={**headers, "Range": byte_range})
        request.headers.pop("cookie", None)
        response = await client.send(request, stream=True, follow_redirects=False)
        if response.has_redirect_location:
            next_url = str(response.url.join(response.headers["location"]))
            await response.aclose()
            if httpx.URL(next_url).scheme not in {"http", "https"}:
                raise VideoError("视频地址跳转到了不支持的协议")
            url = next_url
            continue
        try:
            yield response
        finally:
            await response.aclose()
        return
    raise VideoError("视频地址重定向次数过多")


async def download_file(
    client: httpx.AsyncClient, url: str, target: Path, max_bytes: int,
    headers: Dict[str, str], *, require_mp4: bool = False,
    scope: str = "[视频下载]",
) -> int:
    started = time.perf_counter()
    size = 0
    prefix = b""
    try:
        async with media_stream(client, url, headers) as response:
            response.raise_for_status()
            content_length = int(response.headers.get("content-length", 0))
            if content_length > max_bytes:
                raise VideoTooLarge(f"视频文件超过 {max_bytes // 1024 // 1024} MB 限制")
            content_type = response.headers.get("content-type", "").lower()
            if require_mp4 and (
                content_type.startswith(("text/", "image/")) or "json" in content_type
            ):
                raise VideoError("视频地址没有返回有效的 MP4 文件")
            with target.open("wb") as output:
                async for chunk in response.aiter_bytes(1024 * 1024):
                    size += len(chunk)
                    if size > max_bytes:
                        raise VideoTooLarge(f"视频文件超过 {max_bytes // 1024 // 1024} MB 限制")
                    if len(prefix) < 64:
                        prefix += chunk[:64 - len(prefix)]
                    if require_mp4 and len(prefix) == 64 and b"ftyp" not in prefix:
                        raise VideoError("视频地址没有返回有效的 MP4 文件")
                    output.write(chunk)
            if not size or (require_mp4 and b"ftyp" not in prefix):
                raise VideoError("视频地址没有返回有效的 MP4 文件")
            if content_length and size != content_length:
                raise VideoError("视频下载不完整，请稍后重试")
            # Range 返回部分文件时，不能把缺失的其余内容当作完整视频发送。
            content_range = response.headers.get("content-range", "")
            total: Optional[int] = None
            if "/" in content_range and content_range.rsplit("/", 1)[1].isdigit():
                total = int(content_range.rsplit("/", 1)[1])
            if total is not None and size != total:
                raise VideoError("视频下载不完整，请稍后重试")
        logger.info(
            f"{scope} 下载完成：{size / 1024 / 1024:.1f} MB，"
            f"耗时 {time.perf_counter() - started:.2f} 秒"
        )
        return size
    except BaseException:
        target.unlink(missing_ok=True)
        raise
