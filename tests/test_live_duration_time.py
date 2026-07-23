import importlib.util
import unittest
from datetime import datetime
from pathlib import Path

MODULE_PATH = (
    Path(__file__).resolve().parent
    / "haruka_bot"
    / "utils"
    / "live_duration.py"
)
SPEC = importlib.util.spec_from_file_location("live_duration_time", MODULE_PATH)
assert SPEC and SPEC.loader
live_duration_time = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(live_duration_time)

SHANGHAI_TIMEZONE = live_duration_time.SHANGHAI_TIMEZONE
split_duration_by_stat_date = live_duration_time.split_duration_by_stat_date
resolve_stat_date_query = live_duration_time.resolve_stat_date_query
stat_date_for_timestamp = live_duration_time.stat_date_for_timestamp
stat_period_start_timestamp = live_duration_time.stat_period_start_timestamp


def timestamp(value: str) -> int:
    return int(
        datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
        .replace(tzinfo=SHANGHAI_TIMEZONE)
        .timestamp()
    )


class LiveDurationTimeTest(unittest.TestCase):
    def test_natural_day_interval_is_split_at_midnight(self):
        durations = split_duration_by_stat_date(
            timestamp("2026-07-22 23:59:50"),
            timestamp("2026-07-23 00:00:10"),
        )

        self.assertEqual(durations, {"2026-07-22": 10, "2026-07-23": 10})

    def test_custom_day_boundary_is_respected(self):
        durations = split_duration_by_stat_date(
            timestamp("2026-07-23 03:59:00"),
            timestamp("2026-07-23 04:01:00"),
            day_start_hour=4,
        )

        self.assertEqual(durations, {"2026-07-22": 60, "2026-07-23": 60})

    def test_period_start_uses_shanghai_timezone(self):
        current = timestamp("2026-07-23 03:30:00")

        self.assertEqual(
            stat_date_for_timestamp(current, day_start_hour=4).isoformat(),
            "2026-07-22",
        )
        self.assertEqual(
            stat_period_start_timestamp(current, day_start_hour=4),
            timestamp("2026-07-22 04:00:00"),
        )

    def test_empty_or_reversed_interval_has_no_duration(self):
        current = timestamp("2026-07-23 12:00:00")

        self.assertEqual(split_duration_by_stat_date(current, current), {})
        self.assertEqual(split_duration_by_stat_date(current, current - 1), {})

    def test_user_date_queries_are_resolved(self):
        current = datetime(2026, 7, 23).date()

        self.assertEqual(resolve_stat_date_query("", current), current)
        self.assertEqual(resolve_stat_date_query("今日", current), current)
        self.assertEqual(
            resolve_stat_date_query("昨日", current),
            datetime(2026, 7, 22).date(),
        )
        self.assertEqual(
            resolve_stat_date_query("2026-07-01", current),
            datetime(2026, 7, 1).date(),
        )
        with self.assertRaises(ValueError):
            resolve_stat_date_query("上周", current)


if __name__ == "__main__":
    unittest.main()
