"""Display timezone helpers — all user-facing timestamps use the configured local zone."""

from __future__ import annotations

import calendar
import os
import re
from datetime import datetime, time, timezone
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

_DEFAULT_TZ = "America/New_York"

_MONTHS = {name.lower(): i for i, name in enumerate(calendar.month_name) if name}


def configured_timezone_name(settings_value: str | None = None) -> str:
    """Resolve IANA timezone name from config, env, /etc/timezone, or default."""
    for candidate in (
        (settings_value or "").strip(),
        os.environ.get("FRIGATE_TUI_TZ", "").strip(),
        os.environ.get("TZ", "").strip(),
    ):
        if candidate:
            return candidate
    tz_file = Path("/etc/timezone")
    if tz_file.is_file():
        name = tz_file.read_text(encoding="utf-8").strip()
        if name:
            return name
    return _DEFAULT_TZ


@lru_cache(maxsize=4)
def get_display_tz(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except Exception:
        return ZoneInfo(_DEFAULT_TZ)


def now_local(tz_name: str) -> datetime:
    return datetime.now(get_display_tz(tz_name))


def from_epoch(ts: float, tz_name: str) -> datetime:
    return datetime.fromtimestamp(float(ts), tz=timezone.utc).astimezone(get_display_tz(tz_name))


def format_time(ts: float, tz_name: str, *, with_tz: bool = False) -> str:
    dt = from_epoch(ts, tz_name)
    text = dt.strftime("%H:%M:%S")
    if with_tz:
        label = dt.strftime("%Z")
        if label:
            text = f"{text} {label}"
    return text


def format_iso(ts: float, tz_name: str) -> str:
    return from_epoch(ts, tz_name).isoformat(timespec="seconds")


def tz_label_now(tz_name: str) -> str:
    return now_local(tz_name).strftime("%Z") or tz_name


def _parse_12h(hour: int, minute: int, ampm: str) -> time:
    h = int(hour) % 12
    if ampm.upper() == "PM":
        h += 12
    return time(h, int(minute))


def _fmt_12h(dt: datetime) -> str:
    h = dt.hour % 12 or 12
    return f"{h}:{dt.minute:02d} {dt.strftime('%p')}"


def _fmt_full_date(dt: datetime) -> str:
    return f"{dt.strftime('%B')} {dt.day:02d}, {dt.year}"


def _utc_to_local_time_str(
    hour: int,
    minute: int,
    ampm: str,
    *,
    ref_utc_date,
    tz_name: str,
) -> str:
    dt_utc = datetime.combine(ref_utc_date, _parse_12h(hour, minute, ampm), tzinfo=timezone.utc)
    return _fmt_12h(dt_utc.astimezone(get_display_tz(tz_name)))


_FULL_DT_RE = re.compile(
    r"\b(January|February|March|April|May|June|July|August|September|October|November|December)"
    r" (\d{1,2}), (\d{4}),? at (\d{1,2}):(\d{2}) (AM|PM)\b",
    re.IGNORECASE,
)

_TIME_RANGE_RE = re.compile(
    r"\b(\d{1,2}):(\d{2}) (AM|PM) - (\d{1,2}):(\d{2}) (AM|PM)\b",
    re.IGNORECASE,
)

_TIME_TO_TIME_RE = re.compile(
    r"\b(\d{1,2}):(\d{2}) (AM|PM) to (\d{1,2}):(\d{2}) (AM|PM)\b",
    re.IGNORECASE,
)

_TIME_ONLY_RE = re.compile(
    r"\b(\d{1,2}):(\d{2}) (AM|PM)\b",
    re.IGNORECASE,
)


def _stash_sub(pattern: re.Pattern[str], repl, text: str, stash: list[str]) -> str:
    """Regex sub that stores replacements so later passes cannot re-convert them."""

    def _wrap(match: re.Match[str]) -> str:
        value = repl(match)
        token = f"\x00FTZ{len(stash)}\x00"
        stash.append(value)
        return token

    return pattern.sub(_wrap, text)


def localize_frigate_report_times(
    text: str,
    *,
    window_start_ts: float,
    tz_name: str,
) -> str:
    """Rewrite Frigate summarize report times from UTC to the display timezone."""
    if not (text or "").strip():
        return text

    tz = get_display_tz(tz_name)
    ref_utc_date = datetime.fromtimestamp(float(window_start_ts), tz=timezone.utc).date()
    stash: list[str] = []

    def _replace_full(match: re.Match[str]) -> str:
        month_name, day, year, hour, minute, ampm = match.groups()
        month = _MONTHS.get(month_name.lower())
        if not month:
            return match.group(0)
        t = _parse_12h(int(hour), int(minute), ampm)
        dt_utc = datetime(int(year), month, int(day), t.hour, t.minute, tzinfo=timezone.utc)
        dt_local = dt_utc.astimezone(tz)
        return f"{_fmt_full_date(dt_local)} at {_fmt_12h(dt_local)}"

    def _replace_range(match: re.Match[str]) -> str:
        h1, m1, ap1, h2, m2, ap2 = match.groups()
        start = _utc_to_local_time_str(int(h1), int(m1), ap1, ref_utc_date=ref_utc_date, tz_name=tz_name)
        end = _utc_to_local_time_str(int(h2), int(m2), ap2, ref_utc_date=ref_utc_date, tz_name=tz_name)
        return f"{start} - {end}"

    def _replace_to(match: re.Match[str]) -> str:
        h1, m1, ap1, h2, m2, ap2 = match.groups()
        start = _utc_to_local_time_str(int(h1), int(m1), ap1, ref_utc_date=ref_utc_date, tz_name=tz_name)
        end = _utc_to_local_time_str(int(h2), int(m2), ap2, ref_utc_date=ref_utc_date, tz_name=tz_name)
        return f"{start} to {end}"

    def _replace_single(match: re.Match[str]) -> str:
        hour, minute, ampm = match.groups()
        return _utc_to_local_time_str(
            int(hour), int(minute), ampm, ref_utc_date=ref_utc_date, tz_name=tz_name
        )

    out = text
    for pattern, repl in (
        (_FULL_DT_RE, _replace_full),
        (_TIME_RANGE_RE, _replace_range),
        (_TIME_TO_TIME_RE, _replace_to),
        (_TIME_ONLY_RE, _replace_single),
    ):
        out = _stash_sub(pattern, repl, out, stash)

    for idx, value in enumerate(stash):
        out = out.replace(f"\x00FTZ{idx}\x00", value)
    return out