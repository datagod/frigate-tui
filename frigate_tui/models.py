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

    @property
    def duration_s(self) -> float:
        if self.end_time:
            return max(0.0, self.end_time - self.start_time)
        return 0.0

    @property
    def color(self) -> str:
        return label_color(self.label)
