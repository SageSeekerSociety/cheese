"""When a scheduled routine fires next, in the person's own time zone."""

from __future__ import annotations

import calendar
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.core.errors import ValidationError

FREQS = ("hourly", "daily", "weekly", "monthly")
WEEKDAY_NAMES = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")


def zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValidationError(f"不认识的时区：{name}") from exc


def _clock(spec: dict) -> time:
    raw = str(spec.get("time") or "")
    try:
        hour, minute = (int(p) for p in raw.split(":"))
        return time(hour, minute)
    except (ValueError, TypeError) as exc:
        raise ValidationError("执行时间要写成 HH:MM，例如 09:00") from exc


def normalize(spec: dict, tz: str) -> dict:
    """Reject a schedule that does not name one unambiguous moment."""
    zone(tz)
    freq = spec.get("freq")
    if freq not in FREQS:
        raise ValidationError("频率只能是 hourly、daily、weekly、monthly 之一")
    if freq == "hourly":
        minute = spec.get("minute")
        if not isinstance(minute, int) or not 0 <= minute <= 59:
            raise ValidationError("每小时执行要给出第几分钟（0–59）")
        return {"freq": freq, "minute": minute}
    clock = _clock(spec)
    out: dict = {"freq": freq, "time": clock.strftime("%H:%M")}
    if freq == "weekly":
        days = spec.get("weekdays")
        if (
            not isinstance(days, list)
            or not days
            or any(not isinstance(d, int) or not 0 <= d <= 6 for d in days)
        ):
            raise ValidationError("每周执行要给出星期几（0=周一 … 6=周日）")
        out["weekdays"] = sorted(set(days))
    if freq == "monthly":
        day = spec.get("day")
        if not isinstance(day, int) or not 1 <= day <= 31:
            raise ValidationError("每月执行要给出几号（1–31）")
        out["day"] = day
    return out


def _local_days(start: date):
    day = start
    while True:
        yield day
        day += timedelta(days=1)


def next_after(spec: dict, tz: str, after: datetime) -> datetime:
    """The first scheduled instant strictly later than `after`, in UTC."""
    z = zone(tz)
    local_after = after.astimezone(z)
    freq = spec["freq"]
    if freq == "hourly":
        candidate = local_after.replace(minute=spec["minute"], second=0, microsecond=0)
        while candidate.astimezone(UTC) <= after:
            candidate += timedelta(hours=1)
        return candidate.astimezone(UTC)
    clock = _clock(spec)
    for day in _local_days(local_after.date()):
        if day > local_after.date() + timedelta(days=400):
            break
        if freq == "weekly" and day.weekday() not in spec["weekdays"]:
            continue
        if freq == "monthly":
            last = calendar.monthrange(day.year, day.month)[1]
            if day.day != min(spec["day"], last):
                continue
        candidate = datetime.combine(day, clock, tzinfo=z)
        if candidate.astimezone(UTC) > after:
            return candidate.astimezone(UTC)
    raise ValidationError("这个时间安排算不出下一次执行时间")


def describe(spec: dict, tz: str) -> str:
    freq = spec.get("freq")
    if freq == "hourly":
        return f"每小时第 {spec['minute']} 分（{tz}）"
    if freq == "daily":
        return f"每天 {spec['time']}（{tz}）"
    if freq == "weekly":
        days = "、".join(WEEKDAY_NAMES[d] for d in spec["weekdays"])
        return f"每周{days} {spec['time']}（{tz}）"
    if freq == "monthly":
        return f"每月 {spec['day']} 号 {spec['time']}（{tz}）"
    return ""
