"""Structured GenAI activity history and grouping for hourly reports."""

from __future__ import annotations

from typing import Any

from frigate_tui.local_time import format_iso, from_epoch


def hour_bucket(ts: float, tz_name: str) -> tuple[str, str]:
    """Return (sortable hour key, display label) in the display timezone."""
    dt = from_epoch(ts, tz_name)
    key = dt.strftime("%Y-%m-%d %H:00")
    label = dt.strftime("%H:00")
    return key, label


def make_activity_record(
    *,
    kind: str,
    camera: str,
    title: str = "",
    text: str = "",
    label: str = "",
    objects: list[str] | None = None,
    sub_labels: list[str] | None = None,
    threat: int | float | None = None,
    ref_id: str = "",
    ts: float | None = None,
    tz_name: str = "America/New_York",
) -> dict[str, Any]:
    import time

    t = float(ts if ts is not None else time.time())
    hk, hl = hour_bucket(t, tz_name)
    dt = from_epoch(t, tz_name)
    return {
        "ts": t,
        "iso": format_iso(t, tz_name),
        "time": dt.strftime("%H:%M:%S"),
        "hour": hk,
        "hour_label": hl,
        "kind": kind,
        "camera": camera,
        "title": title,
        "text": text,
        "label": label or (title if kind == "object" else ""),
        "objects": list(objects or []),
        "sub_labels": list(sub_labels or []),
        "threat": threat,
        "ref_id": ref_id,
    }


def list_chronological(entries: list[dict[str, Any]], hours: float) -> list[dict[str, Any]]:
    """All GenAI messages in the window, newest first (current time backward)."""
    import time

    cutoff = time.time() - max(0.25, hours) * 3600.0
    filtered = [e for e in entries if float(e.get("ts", 0)) >= cutoff]
    return sorted(filtered, key=lambda x: float(x.get("ts", 0)), reverse=True)


def _message_detail_suffix(m: dict[str, Any]) -> str:
    parts: list[str] = []
    label = (m.get("label") or "").strip()
    if label:
        parts.append(f"object={label}")
    objs = m.get("objects") or []
    if objs:
        parts.append(f"objects={', '.join(str(o) for o in objs)}")
    subs = m.get("sub_labels") or []
    if subs:
        parts.append(f"names={', '.join(str(s) for s in subs)}")
    if m.get("title") and m.get("kind") == "review":
        parts.append(f"title={m['title']}")
    if m.get("kind") == "object" and (m.get("text") or "").strip():
        parts.append("has_genai_description=yes")
    threat = m.get("threat")
    if threat is not None and float(threat) > 0:
        parts.append(f"threat={threat}")
    return ("; " + "; ".join(parts)) if parts else ""


def format_genai_message_line(m: dict[str, Any]) -> str:
    """Single-line summary for prompts and chronological lists."""
    kind = "review" if m.get("kind") == "review" else "object"
    cam = m.get("camera", "?")
    t = m.get("time", "")
    head = f"[{t}] {kind} camera={cam}"
    head += _message_detail_suffix(m)
    return head


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
    for hk in buckets:
        msgs = sorted(buckets[hk], key=lambda x: float(x.get("ts", 0)), reverse=True)
        sort_ts = max((float(m.get("ts", 0)) for m in msgs), default=0.0)
        sections.append(
            {
                "hour": hk,
                "hour_label": labels.get(hk, hk),
                "sort_ts": sort_ts,
                "count": len(msgs),
                "messages": msgs,
            }
        )
    # Newest hour first: sort by full hour key (YYYY-MM-DD HH:00), then by latest message
    sections.sort(key=lambda s: (str(s.get("hour", "")), float(s.get("sort_ts", 0))), reverse=True)
    return sections


def format_messages_for_prompt(sections: list[dict[str, Any]]) -> str:
    """Build plain-text context for the summarization LLM."""
    lines: list[str] = []
    for sec in sections:
        lbl = sec.get("hour_label") or sec.get("hour", "")
        lines.append(f"{lbl} ({sec['count']} message(s), newest hour first)")
        for m in sec["messages"]:
            lines.append(f"  {format_genai_message_line(m)}")
            body = (m.get("text") or "").strip()
            if body:
                for part in body.split("\n"):
                    part = part.strip()
                    if part:
                        lines.append(f"    {part}")
        lines.append("")
    return "\n".join(lines).strip()