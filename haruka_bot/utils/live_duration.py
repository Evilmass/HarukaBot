from datetime import date, datetime, time, timedelta, timezone
from typing import Dict


SHANGHAI_TIMEZONE = timezone(timedelta(hours=8))


def resolve_stat_date_query(query: str, current_stat_date: date) -> date:
    """Resolve a user-facing today/yesterday/ISO-date leaderboard query."""
    query = query.strip()
    if not query or query in {"今日", "今天"}:
        return current_stat_date
    if query in {"昨日", "昨天"}:
        return current_stat_date - timedelta(days=1)
    return date.fromisoformat(query)


def stat_date_for_timestamp(timestamp: int, day_start_hour: int = 0) -> date:
    """Return the statistics date containing a Unix timestamp."""
    local_time = datetime.fromtimestamp(timestamp, SHANGHAI_TIMEZONE)
    return (local_time - timedelta(hours=day_start_hour)).date()


def stat_period_start_timestamp(timestamp: int, day_start_hour: int = 0) -> int:
    """Return the start timestamp of the statistics period containing timestamp."""
    stat_date = stat_date_for_timestamp(timestamp, day_start_hour)
    period_start = datetime.combine(
        stat_date,
        time(hour=day_start_hour),
        tzinfo=SHANGHAI_TIMEZONE,
    )
    return int(period_start.timestamp())


def split_duration_by_stat_date(
    start_timestamp: int,
    end_timestamp: int,
    day_start_hour: int = 0,
) -> Dict[str, int]:
    """Split a half-open time interval across statistics-day boundaries."""
    if end_timestamp <= start_timestamp:
        return {}

    result: Dict[str, int] = {}
    cursor = start_timestamp
    while cursor < end_timestamp:
        stat_date = stat_date_for_timestamp(cursor, day_start_hour)
        next_period_start = datetime.combine(
            stat_date + timedelta(days=1),
            time(hour=day_start_hour),
            tzinfo=SHANGHAI_TIMEZONE,
        )
        segment_end = min(end_timestamp, int(next_period_start.timestamp()))
        date_key = stat_date.isoformat()
        result[date_key] = result.get(date_key, 0) + segment_end - cursor
        cursor = segment_end

    return result
