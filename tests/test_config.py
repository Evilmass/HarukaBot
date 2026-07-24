import unittest

import nonebot

nonebot.init()

from haruka_bot.config import Config


class ConfigTests(unittest.TestCase):
    def test_dynamic_enabled_defaults_to_true(self):
        self.assertTrue(Config().haruka_dynamic_enabled)

    def test_dynamic_enabled_parses_false_string(self):
        config = Config(haruka_dynamic_enabled="False")

        self.assertFalse(config.haruka_dynamic_enabled)


if __name__ == "__main__":
    unittest.main()
