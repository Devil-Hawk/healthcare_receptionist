from __future__ import annotations

from datetime import datetime

import dateparser
from tzlocal import get_localzone_name


def parse_human_range(value: str, timezone: str) -> tuple[datetime | None, datetime | None]:
    if not value:
        return None, None

    settings = {
        "RETURN_AS_TIMEZONE_AWARE": True,
        "TIMEZONE": timezone,
        "TO_TIMEZONE": timezone,
        "PREFER_DATES_FROM": "future",
    }

    parsed = dateparser.parse(value, settings=settings)
    if not parsed:
        return None, None

    start = parsed
    if "morning" in value.lower():
        end = start.replace(hour=12, minute=0)
    elif "afternoon" in value.lower():
        end = start.replace(hour=17, minute=0)
    elif "evening" in value.lower():
        end = start.replace(hour=20, minute=0)
    else:
        end = start
    return start, end


def get_local_timezone() -> str:
    try:
        return get_localzone_name()
    except Exception:
        return "UTC"
