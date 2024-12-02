import aiohttp

from ..utils import on_command, permission_check, to_me
from ..version import __version__

# 要在 plugins/__init__.py 导入模块
server_ip = on_command(
    cmd="ip",
    aliases={"IP", "服务器"},
    rule=to_me(),
    priority=5,
)
server_ip.__doc__ = """获取联机服务器地址"""

server_ip.handle()(permission_check)


async def fetch(session, url):
    async with session.get(url) as response:
        return await response.json()


async def get_server_ip():
    url = f"https://api-ipv4.ip.sb/ip/jsonip"
    async with aiohttp.ClientSession() as session:
        response = await fetch(session, url)
        ip = response.get("ip", "无法获取")
        return ip


@server_ip.handle()
async def _():
    _server_ip = await get_server_ip()
    message = f"服务器IP ：{_server_ip}\nCitra端口：50005\nYuzu端口：24872"
    await server_ip.finish(message)
