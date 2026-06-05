"""Structured GenAI activity history and grouping for hourly reports."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def hour_bucket(ts: float) -> tuple[str, str]:
    """Return (sortable hour key, display label) in local timezone."""
    dt = datetime.fromtimestamp(ts, tz=timezone.utc).astimezone()
    key = dt.strftime("%Y-%m-%d %H:00")
    label = dt.strftime("%H:00")
    return key, label


def make_activity_record(
    *,
    kind: str,
    camera: str,
    title: str = "",
    text: str = "",
    threat: int | float | None = None,
    ref_id: str = "",
    ts: float | None = None,
) -> dict[str, Any]:
    import time

    t = float(ts if ts is not None else time.time())
    hk, hl = hour_bucket(t)
    dt = datetime.fromtimestamp(t, tz=timezone.utc).astimezone()
    return {
        "ts": t,
        "iso": dt.isoformat(timespec="seconds"),
        "time": dt.strftime("%H:%M:%S"),
        "hour": hk,
        "hour_label": hl,
        "kind": kind,
        "camera": camera,
        "title": title,
        "text": text,
        "threat": threat,
        "ref_id": ref_id,
    }


def group_by_hour(entries: list[dict[str, Any]], hours: float) -> list[dict[str, Any]]:
    """Filter to the last N hours and return hour sections (newest hour first)."""
    import time

    cutoff = time.time() - max(0.25, hours) * 3600.0
    filtered = [e for e in entries if float(e.get("ts", 0)) >= cutoff]
    filtered.sort(key=lambda x: float(x["ts"]), reverse=True)

    buckets: dict[str, list[dict[str, Any]]] = {}
    labels: dict[str, str] = {}
    for e in filtered:
        hk = str(e.get("hour", ""))
        buckets.setdefault(hk, []).append(e)
        labels[hk] = str(e.get("hour_label", hk))

    sections = []
    for hk in sorted(buckets.keys(), reverse=True):
        msgs = buckets[hk]
        sections.append(
            {
                "hour": hk,
                "hour_label": labels.get(hk, hk),
                "count": len(msgs),
                "messages": msgs,
            }
        )
    return sections


def format_messages_for_prompt(sections: list[dict[str, Any]]) -> str:
    """Build plain-text context for the summarization LLM."""
    lines: list[str] = []
    for sec in sections:
        lines.append(f"HOUR {sec['hour']} ({sec['count']} message(s))")
        for m in sec["messages"]:
            kind = m.get("kind", "?")
            cam = m.get("camera", "?")
            title = m.get("title") or ""
            text = (m.get("text") or "").strip()
            threat = m.get("threat")
            t = m.get("time", "")
            head = f"  [{t}] {kind} @ {cam}"
            if title:
                head += f" — {title}"
            if threat is not None and threat > 0:
                head += f" [threat={threat}]"
            lines.append(head)
            if text:
                for part in text.split("\n"):
                    part = part.strip()
                    if part:
                        lines.append(f"    {part}")
        lines.append("")
    return "\n".join(lines).strip()