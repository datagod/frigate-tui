"""Lightweight data models + derived health/queue calculations for Frigate TUI."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class CameraStats:
    name: str
    camera_fps: float = 0.0
    process_fps: float = 0.0
    detection_fps: float = 0.0
    skipped_fps: float = 0.0
    detection_enabled: bool = True

    @property
    def health_color(self) -> str:
        if not self.detection_enabled:
            return "dim"
        if self.skipped_fps > 0.2:
            return "red"
        if self.detection_fps < self.camera_fps * 0.6:
            return "yellow"
        return "green"

    @property
    def health_summary(self) -> str:
        if not self.detection_enabled:
            return "Disabled"
        if self.skipped_fps > 0.2:
            return "Overloaded"
        if self.detection_fps < self.camera_fps * 0.6:
            return "Degraded"
        if self.detection_fps < self.camera_fps * 0.85:
            return "Slightly Behind"
        return "Healthy"


@dataclass
class SystemHealth:
    """Derived queue / pipeline pressure numbers (the core of what the user asked for)."""

    expected_fps: float = 0.0
    detection_fps: float = 0.0
    detection_pressure: float = 0.0
    pressure_pct: float = 0.0
    skipped_fps: float = 0.0
    is_healthy: bool = True

    @property
    def status_color(self) -> str:
        if self.skipped_fps > 0.1 or self.pressure_pct > 0.25:
            return "red"
        if self.pressure_pct > 0.10:
            return "yellow"
        return "green"


def compute_health(raw_stats: dict[str, Any]) -> SystemHealth:
    """Central function that turns raw /api/stats into actionable queue pressure."""
    cameras = raw_stats.get("cameras", {}) or {}
    detection_fps = float(raw_stats.get("detection_fps", 0.0))
    skipped = float(raw_stats.get("skipped_fps", 0.0))

    active = [
        c for c in cameras.values()
        if isinstance(c, dict) and c.get("detection_enabled", True)
    ]
    expected = sum(float(c.get("camera_fps", 0.0)) for c in active)

    pressure = max(0.0, expected - detection_fps)
    pressure_pct = (pressure / expected) if expected > 0.01 else 0.0

    return SystemHealth(
        expected_fps=round(expected, 1),
        detection_fps=round(detection_fps, 1),
        detection_pressure=round(pressure, 1),
        pressure_pct=round(pressure_pct, 3),
        skipped_fps=round(skipped, 2),
        is_healthy=(skipped < 0.1 and pressure_pct < 0.15),
    )


def parse_cameras(raw_stats: dict[str, Any]) -> list[CameraStats]:
    out: list[CameraStats] = []
    for name, data in (raw_stats.get("cameras") or {}).items():
        if not isinstance(data, dict):
            continue
        out.append(
            CameraStats(
                name=name,
                camera_fps=float(data.get("camera_fps", 0)),
                process_fps=float(data.get("process_fps", 0)),
                detection_fps=float(data.get("detection_fps", 0)),
                skipped_fps=float(data.get("skipped_fps", 0)),
                detection_enabled=bool(data.get("detection_enabled", True)),
            )
        )
    return sorted(out, key=lambda c: c.name)


def label_color(label: str) -> str:
    """Consistent colors for the common tracked objects."""
    l = (label or "").lower()
    if l == "person":
        return "#ff6b6b"
    if l in ("car", "vehicle"):
        return "#4ecdc4"
    if l == "dog":
        return "#f7b731"
    if l == "cat":
        return "#a55eea"
    if l in ("bicycle", "motorcycle"):
        return "#45b7d1"
    return "#c0c5d1"


@dataclass
class FrigateEvent:
    """Normalized recent event (from /api/events)."""

    id: str
    camera: str
    label: str
    start_time: float
    end_time: float | None = None
    top_score: float | None = None
    has_snapshot: bool = False
    has_clip: bool = False
    zones: list[str] = field(default_factory=list)
    sub_label: str | None = None
    average_estimated_speed: float | None = None
    velocity_angle: float | None = None
    attributes: list[str] = field(default_factory=list)
    description: str | None = None  # LLM/GenAI generated description (object descriptions feature)

    @property
    def duration_s(self) -> float:
        if self.end_time:
            return max(0.0, self.end_time - self.start_time)
        return 0.0

    @property
    def color(self) -> str:
        return label_color(self.label)

    @property
    def display_label(self) -> str:
        """Returns label with sub_label if available (e.g. 'person (Bill)')."""
        if self.sub_label:
            return f"{self.label} ({self.sub_label})"
        return self.label


def merge_frigate_event_updates(existing: FrigateEvent, incoming: FrigateEvent) -> FrigateEvent:
    """Merge poll/MQTT event updates without dropping fields the other source already had."""

    def _str(new: str | None, old: str | None) -> str | None:
        return (new or "").strip() or old

    def _opt_float(new: float | None, old: float | None) -> float | None:
        return new if new is not None else old

    def _lst(new: list[str], old: list[str]) -> list[str]:
        return new if new else old

    return FrigateEvent(
        id=incoming.id or existing.id,
        camera=incoming.camera or existing.camera,
        label=incoming.label or existing.label,
        start_time=incoming.start_time or existing.start_time,
        end_time=_opt_float(incoming.end_time, existing.end_time),
        top_score=_opt_float(incoming.top_score, existing.top_score),
        has_snapshot=incoming.has_snapshot or existing.has_snapshot,
        has_clip=incoming.has_clip or existing.has_clip,
        zones=_lst(incoming.zones, existing.zones),
        sub_label=_str(incoming.sub_label, existing.sub_label),
        average_estimated_speed=_opt_float(
            incoming.average_estimated_speed, existing.average_estimated_speed
        ),
        velocity_angle=_opt_float(incoming.velocity_angle, existing.velocity_angle),
        attributes=_lst(incoming.attributes, existing.attributes),
        description=_str(incoming.description, existing.description),
    )


@dataclass
class ReviewItem:
    """Normalized review item from /api/review."""
    id: str
    camera: str
    start_time: float
    end_time: float | None = None
    severity: str = "detection"   # "alert", "detection", etc.
    has_been_reviewed: bool = False
    objects: list[str] = field(default_factory=list)
    sub_labels: list[str] = field(default_factory=list)
    zones: list[str] = field(default_factory=list)
    genai_summary: dict | None = None  # LLM-generated review summary (title, scene, shortSummary, etc.)

    @property
    def duration_s(self) -> float:
        if self.end_time:
            return max(0.0, self.end_time - self.start_time)
        return 0.0

    @property
    def display_objects(self) -> str:
        if self.sub_labels:
            return ", ".join(self.sub_labels)
        return ", ".join(self.objects) if self.objects else "—"


@dataclass
class TimelineEntry:
    """Normalized timeline entry from /api/timeline (used for activity log)."""

    timestamp: float
    camera: str
    class_type: str  # "visible", "gone", "stationary", "active", etc.
    source: str = "tracked_object"
    source_id: str = ""
    label: str = ""
    sub_label: str | None = None
    score: float | None = None
    zones: list[str] = field(default_factory=list)
    attribute: str = ""


# ------------------------------------------------------------------
# Normalization helpers (used by core to avoid duplication across
# REST poll, initial load, and MQTT paths; also available to web/TUI)
# ------------------------------------------------------------------

def normalize_sub_label(value: Any) -> str | None:
    """Extract a spoken name/tag from Frigate sub_label ([name, score] or string)."""
    if value is None:
        return None
    if isinstance(value, list):
        for item in value:
            if isinstance(item, str) and item.strip():
                return _strip_classification_prefix(item)
        return None
    if isinstance(value, str):
        return _strip_classification_prefix(value)
    return None


def _strip_classification_prefix(text: str) -> str | None:
    """Frigate marks classifier tags with a leading * (e.g. *family)."""
    cleaned = (text or "").strip()
    if cleaned.startswith("*"):
        cleaned = cleaned[1:].strip()
    return cleaned or None


def frigate_event_from_dict(raw: dict[str, Any], *, data: dict[str, Any] | None = None) -> FrigateEvent | None:
    """Normalize a raw event dict (from /api/events or similar) into FrigateEvent.

    The 'data' sub-dict (for speed/attributes) can be passed explicitly or will be
    taken from raw["data"]. Returns None if essential fields cannot be parsed.
    """
    try:
        if data is None:
            data = raw.get("data", {}) or {}
        end_time = raw.get("end_time")
        if end_time is not None:
            try:
                end_time = float(end_time)
            except Exception:
                end_time = None
        sub_label = normalize_sub_label(raw.get("sub_label") or data.get("sub_label"))
        if not sub_label:
            plate = raw.get("recognized_license_plate") or data.get("recognized_license_plate")
            if plate is not None:
                plate_text = str(plate).strip()
                sub_label = plate_text or None
        return FrigateEvent(
            id=str(raw.get("id", "")),
            camera=str(raw.get("camera", "unknown")),
            label=str(raw.get("label", "object")),
            start_time=float(raw.get("start_time", 0)),
            end_time=end_time,
            top_score=raw.get("top_score") if raw.get("top_score") is not None else data.get("top_score"),
            has_snapshot=bool(raw.get("has_snapshot")),
            has_clip=bool(raw.get("has_clip")),
            zones=raw.get("zones") or [],
            sub_label=sub_label,
            average_estimated_speed=data.get("average_estimated_speed"),
            velocity_angle=data.get("velocity_angle"),
            attributes=data.get("attributes") or [],
            description=raw.get("description") or data.get("description"),
        )
    except Exception:
        return None


def frigate_event_from_mqtt_payload(payload: dict[str, Any]) -> FrigateEvent | None:
    """Normalize a Frigate MQTT event message (frigate/events or frigate/<camera>/events)."""
    topic = str(payload.get("_topic", ""))
    if "tracked_object_update" in topic or "events" not in topic:
        return None
    after = payload.get("after") or {}
    before = payload.get("before") or {}
    event_data = after or before
    if not event_data:
        return None
    merged: dict[str, Any] = dict(event_data)
    for key in (
        "id",
        "camera",
        "label",
        "start_time",
        "end_time",
        "top_score",
        "has_snapshot",
        "has_clip",
        "zones",
        "sub_label",
        "description",
    ):
        if payload.get(key) is not None and merged.get(key) is None:
            merged[key] = payload[key]
    if not merged.get("data"):
        merged["data"] = after.get("data") or before.get("data") or {}
    return frigate_event_from_dict(merged)


def genai_summary_from_review_data(data: dict[str, Any]) -> dict[str, Any] | None:
    """Extract Frigate GenAI review summary from review item data.

    Frigate 0.17+ stores LLM output in data.metadata (title, scene, shortSummary, …).
    Older payloads may use data.genai directly.
    """
    if not isinstance(data, dict):
        return None
    legacy = data.get("genai")
    if isinstance(legacy, dict) and (legacy.get("title") or legacy.get("shortSummary") or legacy.get("scene")):
        return legacy
    meta = data.get("metadata")
    if isinstance(meta, dict) and (meta.get("title") or meta.get("shortSummary") or meta.get("scene")):
        return meta
    return None


def review_item_from_dict(raw: dict[str, Any]) -> ReviewItem | None:
    """Normalize a raw review item (from /api/review) into ReviewItem."""
    try:
        start = float(raw.get("start_time", 0))
        end = raw.get("end_time")
        if end is not None:
            try:
                end = float(end)
            except Exception:
                end = None
        data = raw.get("data", {}) or {}
        genai_summary = genai_summary_from_review_data(data)
        if not genai_summary:
            legacy = raw.get("genai")
            if isinstance(legacy, dict) and (
                legacy.get("title") or legacy.get("shortSummary") or legacy.get("scene")
            ):
                genai_summary = legacy
        return ReviewItem(
            id=str(raw.get("id", "")),
            camera=str(raw.get("camera", "unknown")),
            start_time=start,
            end_time=end,
            severity=str(raw.get("severity", "detection")),
            has_been_reviewed=bool(raw.get("has_been_reviewed")),
            objects=data.get("objects") or [],
            sub_labels=data.get("sub_labels") or [],
            zones=data.get("zones") or [],
            genai_summary=genai_summary if isinstance(genai_summary, dict) else None,
        )
    except Exception:
        return None


def timeline_entry_from_dict(raw: dict[str, Any]) -> TimelineEntry | None:
    """Normalize a raw timeline entry (from /api/timeline) into TimelineEntry.

    Handles sub_label that may be a list in the payload.
    """
    try:
        ts = float(raw.get("timestamp", 0))
        data = raw.get("data", {}) or {}
        return TimelineEntry(
            timestamp=ts,
            camera=str(raw.get("camera", "unknown")),
            class_type=str(raw.get("class_type", "")),
            source=str(raw.get("source", "")),
            source_id=str(raw.get("source_id", "")),
            label=str(data.get("label", "")),
            sub_label=normalize_sub_label(data.get("sub_label")),
            score=data.get("score"),
            zones=data.get("zones") or [],
            attribute=str(data.get("attribute", "")),
        )
    except Exception:
        return None
