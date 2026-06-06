"""Async client for Chatterbox TTS Server (POST /tts) with localrecordings cache."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from frigate_tui.tts_recording_cache import (
    load_cached_recording,
    recording_path,
    save_recording,
    voice_key_from_settings,
)

_MEDIA_TYPES = {
    "wav": "audio/wav",
    "mp3": "audio/mpeg",
    "opus": "audio/opus",
}


def chatterbox_settings_from_config(raw: dict[str, Any] | None) -> dict[str, Any]:
    """Normalize chatterbox_tts config with defaults."""
    cfg = dict(raw or {})
    return {
        "enabled": bool(cfg.get("enabled", False)),
        "base_url": str(cfg.get("base_url", "http://host.docker.internal:8004")).rstrip("/"),
        "voice_mode": str(cfg.get("voice_mode", "clone")),
        "reference_audio_filename": str(
            cfg.get("reference_audio_filename", "JLC_William_McEvoy.mp3")
        ).strip(),
        "predefined_voice_id": str(cfg.get("predefined_voice_id", "")).strip(),
        "output_format": str(cfg.get("output_format", "wav")).lower(),
        "split_text": bool(cfg.get("split_text", False)),
        "timeout": max(5.0, float(cfg.get("timeout", 60))),
        "default_test_message": str(
            cfg.get("default_test_message", "Person detected on driveway.")
        ).strip()
        or "Person detected on driveway.",
        "cache_dir": str(cfg.get("cache_dir", "localrecordings")).strip() or "localrecordings",
        "event_alerts": bool(cfg.get("event_alerts", False)),
        "timeline_alerts": bool(cfg.get("timeline_alerts", cfg.get("event_alerts", False))),
        "alert_cooldown": max(
            5.0,
            float(cfg.get("alert_cooldown", cfg.get("timeline_alert_cooldown", 120))),
        ),
        "timeline_alert_cooldown": max(
            5.0, float(cfg.get("timeline_alert_cooldown", 120))
        ),
        "event_template": str(cfg.get("event_template", "{label} on {camera}")).strip()
        or "{label} on {camera}",
    }


_tts_locks: dict[str, asyncio.Lock] = {}


def apply_voice_override(
    settings: dict[str, Any],
    *,
    voice_mode: str | None = None,
    voice: str | None = None,
) -> dict[str, Any]:
    """Return settings copy with per-request voice selection."""
    merged = dict(settings)
    mode = (voice_mode or "").strip().lower()
    name = (voice or "").strip()
    if not mode or not name:
        return merged
    if mode == "clone":
        merged["voice_mode"] = "clone"
        merged["reference_audio_filename"] = name
    elif mode == "predefined":
        merged["voice_mode"] = "predefined"
        merged["predefined_voice_id"] = name
    return merged


async def list_chatterbox_voices(settings: dict[str, Any]) -> dict[str, list[dict[str, str]]]:
    """Fetch clone + predefined voices from Chatterbox (clones first in UI)."""
    base_url = settings["base_url"]
    timeout = httpx.Timeout(min(15.0, float(settings.get("timeout", 60))))
    clones: list[dict[str, str]] = []
    predefined: list[dict[str, str]] = []

    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        try:
            ref_resp = await client.get(f"{base_url}/get_reference_files")
            if ref_resp.status_code == 200:
                data = ref_resp.json()
                if isinstance(data, list):
                    for filename in data:
                        fn = str(filename).strip()
                        if fn:
                            clones.append({"id": fn, "label": fn, "kind": "clone"})
        except Exception:
            pass

        try:
            pre_resp = await client.get(f"{base_url}/get_predefined_voices")
            if pre_resp.status_code == 200:
                data = pre_resp.json()
                if isinstance(data, list):
                    for entry in data:
                        if not isinstance(entry, dict):
                            continue
                        fn = str(entry.get("filename") or "").strip()
                        if not fn:
                            continue
                        label = str(entry.get("display_name") or fn).strip() or fn
                        predefined.append({"id": fn, "label": label, "kind": "predefined"})
        except Exception:
            pass

    return {"clone": clones, "predefined": predefined}


async def synthesize_speech(text: str, *, settings: dict[str, Any]) -> tuple[bytes, str]:
    """Call Chatterbox POST /tts; return (audio_bytes, media_type)."""
    message = (text or "").strip()
    if not message:
        raise ValueError("Text is required.")

    base_url = settings["base_url"]
    voice_mode = settings["voice_mode"]
    output_format = settings["output_format"]
    if output_format not in _MEDIA_TYPES:
        output_format = "wav"

    payload: dict[str, Any] = {
        "text": message,
        "voice_mode": voice_mode,
        "output_format": output_format,
        "split_text": settings["split_text"],
        "stream": False,
    }
    if voice_mode == "clone":
        ref = settings["reference_audio_filename"]
        if not ref:
            raise ValueError("reference_audio_filename is required for clone voice mode.")
        payload["reference_audio_filename"] = ref
    else:
        voice_id = settings["predefined_voice_id"]
        if not voice_id:
            raise ValueError("predefined_voice_id is required for predefined voice mode.")
        payload["predefined_voice_id"] = voice_id

    timeout = httpx.Timeout(settings["timeout"])
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        try:
            resp = await client.post(f"{base_url}/tts", json=payload)
        except httpx.TimeoutException as e:
            raise TimeoutError(f"Chatterbox TTS timed out after {settings['timeout']}s") from e
        except httpx.RequestError as e:
            raise ConnectionError(f"Cannot reach Chatterbox at {base_url}: {e}") from e

    if resp.status_code >= 400:
        detail = resp.text.strip()[:300] or resp.reason_phrase
        raise RuntimeError(f"Chatterbox TTS HTTP {resp.status_code}: {detail}")

    media_type = resp.headers.get("content-type", "").split(";")[0].strip()
    if not media_type.startswith("audio/"):
        media_type = _MEDIA_TYPES[output_format]
    return resp.content, media_type


def _lock_for_recording(text: str, *, settings: dict[str, Any]) -> asyncio.Lock:
    key = str(recording_path(text, settings=settings))
    lock = _tts_locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _tts_locks[key] = lock
    return lock


async def get_or_synthesize_speech(
    text: str, *, settings: dict[str, Any]
) -> tuple[bytes, str, bool, str | None]:
    """Return (audio, media_type, from_cache, saved_path).

    Uses localrecordings when a matching file already exists; otherwise calls Chatterbox and saves.
    """
    async with _lock_for_recording(text, settings=settings):
        cached = load_cached_recording(text, settings=settings)
        if cached is not None:
            audio, media_type = cached
            return audio, media_type, True, str(recording_path(text, settings=settings))

        audio, media_type = await synthesize_speech(text, settings=settings)
        saved_path = save_recording(text, audio, settings=settings)
        return audio, media_type, False, saved_path


async def warm_event_tts_recordings(core: Any, items: list[dict[str, Any]]) -> None:
    """Generate or load cached TTS for new event alert messages."""
    cfg = core.get_event_tts_settings()
    if not cfg.get("enabled"):
        return
    voice = voice_key_from_settings(cfg)
    for item in items or []:
        text = str((item or {}).get("tts_text") or "").strip()
        if not text:
            continue
        try:
            _audio, _mt, from_cache, saved_path = await get_or_synthesize_speech(
                text, settings=cfg
            )
            if from_cache:
                core.add_log(f"Event TTS cache hit ({text!r})", "info")
            else:
                core.add_log(
                    f"Event TTS generated ({text!r}, {voice}), saved: {saved_path}",
                    "success",
                )
        except Exception as e:
            core.add_log(f"Event TTS failed ({text!r}): {e}", "warning")