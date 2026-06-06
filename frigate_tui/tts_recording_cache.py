"""Cache Chatterbox TTS output under localrecordings/ keyed by message text."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

_MAX_BASENAME_LEN = 120


def resolve_recordings_dir(save_dir: str) -> Path:
    root = Path(save_dir).expanduser()
    if not root.is_absolute():
        root = (Path.cwd() / root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def voice_key_from_settings(settings: dict[str, Any]) -> str:
    if settings.get("voice_mode") == "clone":
        return str(settings.get("reference_audio_filename") or "clone").strip()
    return str(settings.get("predefined_voice_id") or "predefined").strip()


def _slug_part(value: str, *, max_len: int = _MAX_BASENAME_LEN) -> str:
    text = (value or "").strip().lower()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_-]+", "_", text).strip("_")
    if not text:
        return ""
    if len(text) > max_len:
        text = text[:max_len].rstrip("_")
    return text


def recording_filename(text: str, *, settings: dict[str, Any]) -> str:
    """Filesystem-safe name derived from message text (and voice for uniqueness)."""
    output_format = str(settings.get("output_format", "wav")).lower()
    if output_format not in ("wav", "mp3", "opus"):
        output_format = "wav"

    message_slug = _slug_part(text)
    if not message_slug:
        message_slug = "message"

    voice_slug = _slug_part(Path(voice_key_from_settings(settings)).stem, max_len=48)
    if voice_slug:
        base = f"{message_slug}__{voice_slug}"
    else:
        base = message_slug

    return f"{base}.{output_format}"


def recording_path(text: str, *, settings: dict[str, Any]) -> Path:
    root = resolve_recordings_dir(str(settings.get("cache_dir", "localrecordings")))
    return root / recording_filename(text, settings=settings)


def load_cached_recording(text: str, *, settings: dict[str, Any]) -> tuple[bytes, str] | None:
    """Return (audio_bytes, media_type) if a non-empty cached file exists."""
    path = recording_path(text, settings=settings)
    if not path.is_file() or path.stat().st_size <= 0:
        return None

    ext = path.suffix.lower().lstrip(".")
    media_types = {"wav": "audio/wav", "mp3": "audio/mpeg", "opus": "audio/opus"}
    media_type = media_types.get(ext, "application/octet-stream")
    return path.read_bytes(), media_type


def format_event_tts_message(
    label: str | None,
    camera: str | None,
    *,
    template: str = "{label} on {camera}",
) -> str:
    """Build a short spoken alert, e.g. 'dog on driveway'."""
    lbl = (label or "motion").strip().lower()
    cam = (camera or "camera").strip().replace("_", " ")
    try:
        return template.format(label=lbl, camera=cam).strip()
    except (KeyError, ValueError):
        return f"{lbl} on {cam}"


def save_recording(text: str, audio: bytes, *, settings: dict[str, Any]) -> str:
    """Write audio to localrecordings atomically; return absolute path string."""
    path = recording_path(text, settings=settings)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(audio)
    tmp.replace(path)
    return str(path)