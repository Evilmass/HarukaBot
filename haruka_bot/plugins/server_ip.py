import asyncio
import ipaddress

import aiohttp
from nonebot import logger

from ..utils import on_command, permission_check, to_me
from ..version import __version__

IP_API_URL = "https://api-ipv4.ip.sb/ip"
IP_API_TIMEOUT = 10
IP_API_HEADERS = {
    "User-Agent": f"HarukaBot/{__version__} (+https://github.com/SK-415/HarukaBot)"
}

# 要在 plugins/__init__.py 导入模块
server_ip = on_command(
    cmd="ip",
    aliases={"IP", "联机ip", "服务器ip"},
    rule=to_me(),
    priority=5,
)
server_ip.__doc__ = """获取联机服务器地址 -> ip"""

server_ip.handle()(permission_check)


async def fetch(session: aiohttp.ClientSession, url: str) -> str:
    async with session.get(url, headers=IP_API_HEADERS) as response:
        response.raise_for_status()
        return (await response.text()).strip()


async def get_server_ip() -> str:
    timeout = aiohttp.ClientTimeout(total=IP_API_TIMEOUT)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            value = await fetch(session, IP_API_URL)
        ip = ipaddress.ip_address(value)
        if ip.version != 4:
            raise ValueError(f"接口返回的不是 IPv4 地址: {value!r}")
        return str(ip)
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as error:
        logger.warning(f"获取服务器公网 IP 失败: {error}")
        return "无法获取"


@server_ip.handle()
async def _():
    _server_ip = await get_server_ip()
    message = f"服务器IP ：{_server_ip}\nCitra端口：50005\nYuzu端口：24872\nYuzu私服：24873，密码：1145"
    await server_ip.finish(message)
