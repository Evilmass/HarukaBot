import unittest
from unittest.mock import AsyncMock, patch

import aiohttp
import nonebot

nonebot.init()

from haruka_bot.plugins import server_ip


class _FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        return None


class ServerIpTests(unittest.IsolatedAsyncioTestCase):
    async def test_get_server_ip_returns_valid_ipv4(self):
        with patch.object(
            server_ip.aiohttp,
            "ClientSession",
            return_value=_FakeSession(),
        ), patch.object(
            server_ip,
            "fetch",
            new=AsyncMock(return_value="203.0.113.8"),
        ) as fetch:
            result = await server_ip.get_server_ip()

        self.assertEqual(result, "203.0.113.8")
        fetch.assert_awaited_once_with(
            unittest.mock.ANY,
            "https://api-ipv4.ip.sb/ip",
        )

    async def test_get_server_ip_handles_http_error(self):
        with patch.object(
            server_ip.aiohttp,
            "ClientSession",
            return_value=_FakeSession(),
        ), patch.object(
            server_ip,
            "fetch",
            new=AsyncMock(side_effect=aiohttp.ClientError("upstream failed")),
        ):
            result = await server_ip.get_server_ip()

        self.assertEqual(result, "无法获取")

    async def test_get_server_ip_rejects_unexpected_response(self):
        with patch.object(
            server_ip.aiohttp,
            "ClientSession",
            return_value=_FakeSession(),
        ), patch.object(
            server_ip,
            "fetch",
            new=AsyncMock(return_value="<html>not found</html>"),
        ):
            result = await server_ip.get_server_ip()

        self.assertEqual(result, "无法获取")


if __name__ == "__main__":
    unittest.main()
