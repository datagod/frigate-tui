"""Cache Chatterbox TTS output under localrecordings/ keyed by message text."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from frigate_tui.models import normalize_sub_label

_MAX_BASENAME_LEN = 120
_RECORDING_EXTENSIONS = {".wav", ".mp3", ".opus"}
_MEDIA_TYPES = {"wav": "audio/wav", "mp3": "audio/mpeg", "opus": "audio/opus"}


def resolve_recordings_dir(save_dir: str) -> Path:
    root = Path(save_dir).expanduser()
    if not root.is_absolute():
        root = (Path.cwd() / root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def event_voice_pref_path(cache_dir: str) -> Path:
    return resolve_recordings_dir(cache_dir) / ".event_voice.json"


def load_event_voice_pref(cache_dir: str) -> dict[str, str] | None:
    """Return {voice_mode, voice} when a saved UI voice preference exists."""
    path = event_voice_pref_path(cache_dir)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    mode = str(data.get("voice_mode") or "").strip().lower()
    voice = str(data.get("voice") or "").strip()
    if mode not in ("clone", "predefined") or not voice:
        return None
    return {"voice_mode": mode, "voice": voice}


def save_event_voice_pref(
    cache_dir: str,
    *,
    voice_mode: str | None = None,
    voice: str | None = None,
) -> None:
    """Persist or clear the UI-chosen voice for event alert TTS."""
    path = event_voice_pref_path(cache_dir)
    mode = str(voice_mode or "").strip().lower()
    name = str(voice or "").strip()
    if mode in ("clone", "predefined") and name:
        path.write_text(
            json.dumps({"voice_mode": mode, "voice": name}, indent=2) + "\n",
            encoding="utf-8",
        )
        return
    if path.is_file():
        try:
            path.unlink()
        except OSError:
            pass


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


def resolve_event_tts_label(label: str | None, sub_label: str | None = None) -> str:
    """Prefer Frigate recognition tag (sub_label) over object label for speech."""
    tag = normalize_sub_label(sub_label) or ""
    if tag:
        return tag
    return (label or "motion").strip()


def format_event_tts_message(
    label: str | None,
    camera: str | None,
    *,
    sub_label: str | None = None,
    template: str = "{label} on {camera}",
) -> str:
    """Build a short spoken alert, e.g. 'family on fronthouse' when sub_label is Family."""
    lbl = resolve_event_tts_label(label, sub_label).strip().lower()
    cam = (camera or "camera").strip().replace("_", " ")
    try:
        return template.format(label=lbl, camera=cam).strip()
    except (KeyError, ValueError):
        return f"{lbl} on {cam}"


def _deslug(value: str) -> str:
    return (value or "").replace("_", " ").strip()


def parse_recording_basename(stem: str) -> tuple[str, str]:
    """Split cached filename stem into (message, voice) display strings."""
    text = (stem or "").strip()
    if "__" in text:
        msg_slug, voice_slug = text.rsplit("__", 1)
    else:
        msg_slug, voice_slug = text, ""
    return _deslug(msg_slug), _deslug(voice_slug)


def safe_recording_filename(name: str) -> str | None:
    """Reject path traversal; return basename if it looks like a cached recording."""
    base = Path(name).name
    if not base or base != name.strip() or base.startswith("."):
        return None
    suffix = Path(base).suffix.lower()
    if suffix not in _RECORDING_EXTENSIONS:
        return None
    stem = Path(base).stem
    if not stem or not re.match(r"^[\w][\w._-]*$", stem):
        return None
    return base


def media_type_for_filename(name: str) -> str:
    ext = Path(name).suffix.lower().lstrip(".")
    return _MEDIA_TYPES.get(ext, "application/octet-stream")


def recording_file_path(filename: str, *, cache_dir: str) -> Path | None:
    """Resolved path for a safe recording basename, or None."""
    safe = safe_recording_filename(filename)
    if not safe:
        return None
    return resolve_recordings_dir(cache_dir) / safe


def delete_recording(filename: str, *, cache_dir: str) -> tuple[bool, str | None]:
    """Delete one cached recording. Returns (deleted, error_message)."""
    path = recording_file_path(filename, cache_dir=cache_dir)
    if path is None:
        return False, "invalid filename"
    if not path.is_file():
        return False, "not found"
    try:
        path.unlink()
        return True, None
    except OSError as e:
        return False, str(e)


def delete_recordings(
    filenames: list[str],
    *,
    cache_dir: str,
) -> dict[str, Any]:
    """Delete multiple recordings; return per-file results."""
    deleted: list[str] = []
    errors: dict[str, str] = {}
    seen: set[str] = set()
    for raw in filenames or []:
        name = str(raw or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        ok, err = delete_recording(name, cache_dir=cache_dir)
        if ok:
            deleted.append(name)
        elif err:
            errors[name] = err
    return {"deleted": deleted, "errors": errors}


def list_recordings(cache_dir: str) -> list[dict[str, Any]]:
    """Metadata for non-empty cached recordings, newest first."""
    root = resolve_recordings_dir(cache_dir)
    items: list[dict[str, Any]] = []
    for entry in root.iterdir():
        if not entry.is_file():
            continue
        if entry.name.startswith(".") or entry.suffix.lower() == ".tmp":
            continue
        if entry.suffix.lower() not in _RECORDING_EXTENSIONS:
            continue
        try:
            stat = entry.stat()
        except OSError:
            continue
        if stat.st_size <= 0:
            continue
        message, voice = parse_recording_basename(entry.stem)
        items.append(
            {
                "filename": entry.name,
                "message": message,
                "voice": voice,
                "format": entry.suffix.lower().lstrip("."),
                "size_bytes": stat.st_size,
                "modified_at": stat.st_mtime,
            }
        )
    items.sort(key=lambda r: float(r.get("modified_at") or 0), reverse=True)
    return items


def save_recording(text: str, audio: bytes, *, settings: dict[str, Any]) -> str:
    """Write audio to localrecordings atomically; return absolute path string."""
    path = recording_path(text, settings=settings)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(audio)
    tmp.replace(path)
    return str(path)