"""Resolve and list user-provided alert sound files (MP3, etc.)."""

from __future__ import annotations

import os
from pathlib import Path

_SOUND_EXTENSIONS = {".mp3", ".ogg", ".wav", ".m4a", ".aac", ".webm"}


def sounds_directory() -> Path | None:
    """Return the first existing sounds directory, or None."""
    env = (os.environ.get("FRIGATE_TUI_SOUNDS_DIR") or "").strip()
    candidates: list[Path] = []
    if env:
        candidates.append(Path(env))
    candidates.extend(
        [
            Path("/app/sounds"),
            Path.cwd() / "sounds",
            Path(__file__).resolve().parents[2] / "sounds",
        ]
    )
    seen: set[Path] = set()
    for path in candidates:
        try:
            resolved = path.resolve()
        except OSError:
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        if resolved.is_dir():
            return resolved
    return None


def list_sound_files(directory: Path | None = None) -> list[str]:
    """Basenames of supported audio files in the sounds directory."""
    root = directory or sounds_directory()
    if not root or not root.is_dir():
        return []
    names: list[str] = []
    for entry in sorted(root.iterdir()):
        if entry.is_file() and entry.suffix.lower() in _SOUND_EXTENSIONS:
            names.append(entry.name)
    return names