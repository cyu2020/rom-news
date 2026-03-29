from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone


def utc_window_for_week(week_end: date, *, window_days: int) -> tuple[datetime, datetime]:
    """Inclusive window [start_of_first_day, end_of_last_day] in UTC."""
    if window_days < 1:
        raise ValueError("window_days must be >= 1")
    end = datetime.combine(week_end, time(23, 59, 59), tzinfo=timezone.utc)
    start_day = week_end - timedelta(days=window_days - 1)
    start = datetime.combine(start_day, time(0, 0, 0), tzinfo=timezone.utc)
    return start, end
