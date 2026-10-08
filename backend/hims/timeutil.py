"""Office-local time helpers. Times are stored as naive local time (default Asia/Karachi)."""
import os
from datetime import date, datetime
from zoneinfo import ZoneInfo

TZ_NAME = os.environ.get("HIMS_TIMEZONE", "Asia/Karachi")
TZ = ZoneInfo(TZ_NAME)


def now() -> datetime:
    return datetime.now(TZ).replace(tzinfo=None, microsecond=0)


def today() -> date:
    return now().date()
