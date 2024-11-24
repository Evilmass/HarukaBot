import aiohttp

from nonebot.matcher import matchers

from ..utils import on_command, to_me
from ..version import __version__

server_ip = on_command("ip", rule=to_me(), priority=5)


async def fetch(session, url):
    async with session.get(url) as response:
        return await response.json()


async def get_server_ip():
    url = f'https://api.ip.sb/jsonip'
    async with aiohttp.ClientSession() as session:
        response = await fetch(session, url)
        ip = response.get("ip", "无法获取")
        return ip


@server_ip.handle()
async def _():
    _server_ip = await get_server_ip()
    message = f"服务器IP ：{_server_ip}\nCitra端口：50005\nYuzu端口：24872"
    await server_ip.finish(message)
