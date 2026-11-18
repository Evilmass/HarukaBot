import os
import unittest
from unittest.mock import patch

import nonebot

nonebot.init()

from haruka_bot.config import Config


class ConfigTests(unittest.TestCase):
    def test_nas_domain_defaults_to_none(self):
        self.assertIsNone(Config().nas_domain)

    def test_nas_domain_accepts_permanent_domain(self):
        self.assertEqual(
            Config(nas_domain="nas.example.com").nas_domain,
            "nas.example.com",
        )

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

    def test_video_only_groups_default_and_parsing(self):
        self.assertEqual(Config().haruka_video_only_groups, [])
        self.assertEqual(
            Config(haruka_video_only_groups="123, 456 789").haruka_video_only_groups,
            [123, 456, 789],
        )
        with patch.dict(os.environ, {"HARUKA_VIDEO_ONLY_GROUPS": "[123,456]"}):
            self.assertEqual(Config().haruka_video_only_groups, [123, 456])

    def test_group_features_parse_and_validate(self):
        from haruka_bot.features import ALL_FEATURES

        self.assertEqual(Config().haruka_group_features, {})
        config = Config(haruka_group_features='{"123": ["video", "save"]}')
        self.assertEqual(
            config.haruka_group_features, {123: ["video", "save_sticker"]}
        )
        with patch.dict(os.environ, {"HARUKA_GROUP_FEATURES": '{"456": "all"}'}):
            self.assertEqual(
                set(Config().haruka_group_features[456]), set(ALL_FEATURES)
            )
        with self.assertRaises(ValueError):
            Config(haruka_group_features={"123": ["unknown"]})


if __name__ == "__main__":
    unittest.main()
