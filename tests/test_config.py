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

    def test_dynamic_max_push_per_poll_defaults_to_one(self):
        self.assertEqual(Config().haruka_dynamic_max_push_per_poll, 1)

    def test_dynamic_max_push_per_poll_accepts_zero_and_positive_values(self):
        self.assertEqual(
            Config(haruka_dynamic_max_push_per_poll=0).haruka_dynamic_max_push_per_poll,
            0,
        )
        self.assertEqual(
            Config(haruka_dynamic_max_push_per_poll=3).haruka_dynamic_max_push_per_poll,
            3,
        )

    def test_dynamic_max_push_per_poll_rejects_negative_values(self):
        with self.assertRaises(ValueError):
            Config(haruka_dynamic_max_push_per_poll=-1)


if __name__ == "__main__":
    unittest.main()
