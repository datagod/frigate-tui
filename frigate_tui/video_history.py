"""Browse Frigate continuous recording files on disk."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

_VIDEO_EXTENSIONS = {".mp4", ".mkv", ".avi", ".webm", ".mov"}
_MEDIA_TYPES = {
    "mp4": "video/mp4",
    "mkv": "video/x-matroska",
    "avi": "video/x-msvideo",
    "webm": "video/webm",
    "mov": "video/quicktime",
}


def video_history_settings_from_config(raw: dict[str, Any] | None) -> dict[str, Any]:
    """Normalize video_history config with defaults."""
    cfg = dict(raw or {})
    return {
        "enabled": bool(cfg.get("enabled", True)),
        "recordings_dir": str(cfg.get("recordings_dir", "/media/frigate/recordings")).strip()
        or "/media/frigate/recordings",
        "max_files": max(1, min(5000, int(cfg.get("max_files", 500)))),
    }


def resolve_video_history_dir(recordings_dir: str) -> Path:
    root = Path(recordings_dir).expanduser()
    if not root.is_absolute():
        root = (Path.cwd() / root).resolve()
    return root


def safe_relative_path(name: str) -> str | None:
    """Reject path traversal; return a normalized relative path under the recordings root."""
    raw = str(name or "").strip().replace("\\", "/")
    if not raw or raw.startswith("/") or ".." in raw.split("/"):
        return None
    parts = [p for p in raw.split("/") if p and p != "."]
    if not parts:
        return None
    rel = "/".join(parts)
    if not re.match(r"^[\w][\w./ -]*$", rel, re.ASCII):
        return None
    suffix = Path(rel).suffix.lower()
    if suffix not in _VIDEO_EXTENSIONS:
        return None
    return rel


def video_file_path(rel: str, *, recordings_dir: str) -> Path | None:
    safe = safe_relative_path(rel)
    if not safe:
        return None
    root = resolve_video_history_dir(recordings_dir).resolve()
    path = (root / safe).resolve()
    try:
        path.relative_to(root)
    except ValueError:
        return None
    return path


def media_type_for_video(name: str) -> str:
    ext = Path(name).suffix.lower().lstrip(".")
    return _MEDIA_TYPES.get(ext, "application/octet-stream")


def _looks_like_date(part: str) -> bool:
    text = (part or "").strip()
    return len(text) >= 8 and text[:4].isdigit() and text[4] == "-"


def parse_camera_from_relpath(rel: str) -> str:
    """Infer camera name from common Frigate recording layouts."""
    parts = Path(rel).parts
    if not parts:
        return ""
    if parts[0] == "recordings" and len(parts) > 1:
        return parts[1]
    # {date}/{hour}/{camera}/file.mp4
    if _looks_like_date(parts[0]) and len(parts) >= 3:
        return parts[2]
    # {camera}/{date}/...
    if len(parts) >= 2 and _looks_like_date(parts[1]):
        return parts[0]
    return parts[0]


def list_videos(
    recordings_dir: str,
    *,
    max_files: int = 500,
    camera: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Return (video rows newest-first, status metadata)."""
    root = resolve_video_history_dir(recordings_dir)
    status: dict[str, Any] = {
        "directory": str(root),
        "exists": root.is_dir(),
        "readable": root.is_dir() and root.exists(),
    }
    if not root.is_dir():
        return [], status

    camera_filter = (camera or "").strip().lower()
    items: list[dict[str, Any]] = []
    try:
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if path.suffix.lower() not in _VIDEO_EXTENSIONS:
                continue
            if path.name.startswith("."):
                continue
            try:
                rel = path.relative_to(root).as_posix()
                stat = path.stat()
            except (OSError, ValueError):
                continue
            cam = parse_camera_from_relpath(rel)
            if camera_filter and cam.lower() != camera_filter:
                continue
            items.append(
                {
                    "path": rel,
                    "filename": path.name,
                    "camera": cam,
                    "size_bytes": stat.st_size,
                    "modified_at": stat.st_mtime,
                }
            )
    except OSError as e:
        status["error"] = str(e)
        return [], status

    items.sort(key=lambda row: float(row.get("modified_at") or 0), reverse=True)
    if len(items) > max_files:
        items = items[:max_files]
        status["truncated"] = True
    status["total"] = len(items)
    return items, status