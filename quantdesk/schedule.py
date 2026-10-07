"""Handelskalender der New York Stock Exchange (XNYS) – Feiertage und verkürzte Tage via exchange_calendars."""
from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

NEW_YORK = ZoneInfo("America/New_York")


def ny_today(now: dt.datetime | None = None) -> dt.date:
    now = now or dt.datetime.now(dt.timezone.utc)
    return now.astimezone(NEW_YORK).date()


def is_trading_day(day: dt.date) -> bool:
    import exchange_calendars as xc
    import pandas as pd

    cal = xc.get_calendar("XNYS")
    ts = pd.Timestamp(day)
    if ts < cal.first_session or ts > cal.last_session:
        return day.weekday() < 5  # ausserhalb des Kalenders: nur Wochenende ausschliessen
    return bool(cal.is_session(ts))


def market_open_now(now: dt.datetime | None = None) -> bool:
    """Ist die Börse gerade offen (inkl. verkürzter Tage)?"""
    import exchange_calendars as xc
    import pandas as pd

    now = now or dt.datetime.now(dt.timezone.utc)
    cal = xc.get_calendar("XNYS")
    return bool(cal.is_open_on_minute(pd.Timestamp(now).tz_convert("UTC").floor("min")))
