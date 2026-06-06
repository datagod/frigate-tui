"""Persist generated Frigate GenAI reports to disk."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from frigate_tui.local_time import from_epoch, now_local

REPORT_FILENAME_PREFIX = "Frigate_GenAI_Report"


def save_frigate_genai_report(
    summary: str,
    *,
    save_dir: str,
    hours: float,
    start_ts: float,
    end_ts: float,
    generated_at: datetime | None = None,
    tz_name: str = "America/New_York",
) -> str | None:
    """Write summary to Frigate_GenAI_Report_YYYYMMDD_HHMMSS.txt; return path or None."""
    text = (summary or "").strip()
    if not text:
        return None

    root = Path(save_dir).expanduser()
    if not root.is_absolute():
        root = (Path.cwd() / root).resolve()
    root.mkdir(parents=True, exist_ok=True)

    when = generated_at or now_local(tz_name)
    filename = f"{REPORT_FILENAME_PREFIX}_{when.strftime('%Y%m%d_%H%M%S')}.txt"
    path = root / filename

    start_dt = from_epoch(start_ts, tz_name)
    end_dt = from_epoch(end_ts, tz_name)
    body = (
        f"Frigate GenAI Report\n"
        f"Generated: {when.isoformat(timespec='seconds')}\n"
        f"Window: {hours:g} hour(s)\n"
        f"From: {start_dt.isoformat(timespec='seconds')}\n"
        f"To: {end_dt.isoformat(timespec='seconds')}\n"
        f"\n"
        f"{text}\n"
    )
    path.write_text(body, encoding="utf-8")
    return str(path)