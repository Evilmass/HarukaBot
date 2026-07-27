import unittest

import nonebot

nonebot.init()

from haruka_bot.plugins import server_ip


class ServerDomainTests(unittest.TestCase):
    def setUp(self):
        self.old_domain = server_ip.plugin_config.nas_domain

    def tearDown(self):
        server_ip.plugin_config.nas_domain = self.old_domain

    def test_get_server_domain_returns_configured_domain(self):
        server_ip.plugin_config.nas_domain = "nas.example.com"

        self.assertEqual(server_ip.get_server_domain(), "nas.example.com")

    def test_get_server_domain_strips_whitespace(self):
        server_ip.plugin_config.nas_domain = "  nas.example.com  "

        self.assertEqual(server_ip.get_server_domain(), "nas.example.com")

    def test_get_server_domain_handles_missing_config(self):
        server_ip.plugin_config.nas_domain = None

        self.assertEqual(server_ip.get_server_domain(), "未配置")


if __name__ == "__main__":
    unittest.main()
