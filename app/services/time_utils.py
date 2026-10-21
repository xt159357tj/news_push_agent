from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def utc_isoformat(value: datetime | None) -> str | None:
    """Serialize UTC database timestamps, including MySQL's naive DATETIME values."""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def resolve_timezone(name: str | None) -> ZoneInfo:
    """Return a ZoneInfo for the given IANA name, defaulting to Asia/Shanghai."""
    name = name or "Asia/Shanghai"
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        return ZoneInfo("Asia/Shanghai")


def local_date_in_tz(now: datetime, tz_name: str | None) -> str:
    """Return YYYY-MM-DD for `now` in the user's timezone (idempotency key)."""
    return now.astimezone(resolve_timezone(tz_name)).strftime("%Y-%m-%d")
