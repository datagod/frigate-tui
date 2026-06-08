"""Shared core monitor logic for Frigate TUI and Web.

This module contains all the stateful orchestration, polling, MQTT subscription,
event list maintenance (with exact dedup/ordering rules), review/timeline surfacing,
activity log generation, demo mode, connection tracking, and derived health.

Both the Textual TUI (app.py) and the web server (web/server.py) are thin consumers
that drive their respective UIs from this core via listeners + get_snapshot().

The goal is "exact same features" with zero duplication of the complex update logic.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any, Callable, Awaitable

from frigate_tui import __version__
from frigate_tui.chatterbox_tts import (
    apply_delivery_mode_settings,
    apply_voice_override,
    chatterbox_settings_from_config,
)
from frigate_tui.delivery_modes import DELIVERY_MODES, normalize_delivery_mode
from frigate_tui.tts_recording_cache import (
    format_event_tts_message,
    load_event_tts_prefs,
    load_event_voice_pref,
    resolve_event_tts_label,
    save_event_tts_prefs,
    save_event_voice_pref,
)
from frigate_tui.video_history import video_history_settings_from_config
from frigate_tui.local_time import (
    configured_timezone_name,
    format_iso,
    format_time,
    localize_frigate_report_times,
    now_local,
    tz_label_now,
)
from frigate_tui.api import FrigateClient
from frigate_tui.genai_activity import group_by_hour, list_chronological, make_activity_record
from frigate_tui.genai_health import collect_genai_health, frigate_review_genai_probe
from frigate_tui.genai_report import generate_activity_report, resolve_llm_settings
from frigate_tui.report_storage import save_frigate_genai_report
from frigate_tui.models import (
    CameraStats,
    FrigateEvent,
    ReviewItem,
    SystemHealth,
    merge_frigate_event_updates,
    TimelineEntry,
    compute_health,
    parse_cameras,
    frigate_event_from_dict,
    frigate_event_from_mqtt_payload,
    genai_summary_from_review_data,
    normalize_sub_label,
    review_item_from_dict,
    timeline_entry_from_dict,
)

try:
    from frigate_tui.mqtt import FrigateMqttClient, FrigateMqttEvent
    MQTT_AVAILABLE = True
except ImportError:
    FrigateMqttClient = None  # type: ignore
    FrigateMqttEvent = None  # type: ignore
    MQTT_AVAILABLE = False

from frigate_tui.web.sounds_util import (
    DEFAULT_DOG_SOUND,
    DEFAULT_EVENT_SOUND,
    event_alert_sound_key,
)


class FrigateMonitorCore:
    """Headless core that owns Frigate data fetching, state, and live updates.

    Usage (TUI or web):
        core = FrigateMonitorCore(settings)
        core.add_update_listener(my_handler)   # receives (kind, payload)
        await core.start()
        # ... later
        snap = core.get_snapshot()
        await core.stop()
    """

    def __init__(self, settings: dict[str, Any] | None = None) -> None:
        s = settings or {}
        self.frigate_url: str = str(s.get("frigate_url", "http://localhost:5000")).rstrip("/")
        self.poll_interval: float = float(s.get("poll_interval", 5.0))
        self.review_interval: float = float(s.get("review_interval", 8.0))
        self.timeline_interval: float = float(s.get("timeline_interval", 5.0))
        self.stats_log_interval: float = float(s.get("stats_log_interval", 600.0))
        self.events_poll_interval: float = float(s.get("events_poll_interval", 8.0))
        self.events_reconcile_interval: float = float(s.get("events_reconcile_interval", 10.0))
        self.max_events: int = int(s.get("max_events", 150))
        # Activity Log: timeline can flood the log (visible/stationary/gone per object)
        self.timeline_log_enabled: bool = bool(s.get("timeline_log_enabled", True))
        self.timeline_log_stationary: bool = bool(s.get("timeline_log_stationary", False))
        self.timeline_log_gone_min_score: float = float(s.get("timeline_log_gone_min_score", 0.80))
        self.timeline_log_visible_min_score: float = float(
            s.get("timeline_log_visible_min_score", 0.85)
        )
        self.genai_report_config: dict[str, Any] = dict(s.get("genai_report") or {})
        self.genai_report_default_hours: float = float(
            self.genai_report_config.get("default_hours", 1.0)
        )
        frigate_report = dict(s.get("frigate_report") or {})
        self.frigate_report_auto_interval: float = max(
            0.0, float(frigate_report.get("auto_interval", 600))
        )
        self.frigate_report_save_dir: str = (
            str(frigate_report.get("save_dir", "reports")).strip() or "reports"
        )
        web_alerts = dict(s.get("web_alerts") or {})
        self.web_alerts_enabled: bool = bool(web_alerts.get("enabled", True))
        self.web_alerts_max_queue: int = max(1, int(web_alerts.get("max_queue", 24)))
        raw_sounds = web_alerts.get("sounds") or {}
        self.web_alerts_sounds: dict[str, str] = (
            {str(k): str(v) for k, v in raw_sounds.items()}
            if isinstance(raw_sounds, dict)
            else {}
        )
        if "default" not in self.web_alerts_sounds:
            self.web_alerts_sounds.setdefault("default", "")
        self.web_alerts_sounds.setdefault("event", DEFAULT_EVENT_SOUND)
        self.web_alerts_sounds.setdefault("dog", DEFAULT_DOG_SOUND)
        self.chatterbox_tts_config: dict[str, Any] = chatterbox_settings_from_config(
            s.get("chatterbox_tts")
        )
        self.video_history_config: dict[str, Any] = video_history_settings_from_config(
            s.get("video_history")
        )
        saved_prefs = load_event_tts_prefs(
            self.chatterbox_tts_config.get("cache_dir", "localrecordings")
        )
        self._event_tts_voice_mode: str | None = saved_prefs.get("voice_mode")
        self._event_tts_voice: str | None = saved_prefs.get("voice")
        self._event_tts_delivery_mode: str = normalize_delivery_mode(
            saved_prefs.get("delivery_mode")
        )
        self._genai_activity_hours_keep: float = float(
            s.get("genai_activity_hours_keep", 48.0)
        )
        self._max_genai_activity: int = int(s.get("genai_activity_max", 1500))
        self.tui_version = __version__
        self.display_timezone: str = configured_timezone_name(str(s.get("timezone") or ""))
        self.demo: bool = bool(s.get("demo", False))

        # MQTT (optional)
        self.mqtt_config: dict[str, Any] | None = s.get("mqtt")
        self._use_mqtt_for_events: bool = bool(self.mqtt_config) and MQTT_AVAILABLE

        # Clients / tasks
        self._client: FrigateClient | None = None
        self._frigate_version: str = "?"
        self._mqtt_client: FrigateMqttClient | None = None
        self._mqtt_task: asyncio.Task | None = None
        self.mqtt_connected: bool = False
        self.genai_health: dict[str, Any] | None = None
        self._frigate_config_cache: dict[str, Any] | None = None
        self._frigate_config_cached_at: float = 0.0
        self._last_genai_health_check: float = 0.0
        self._genai_health_interval: float = float(s.get("genai_health_interval", 30.0))
        self._event_polling_active: bool = False

        # Cursors for incremental fetch (exact same semantics as TUI)
        self._last_event_ts: float | None = None
        self._last_review_ts: float | None = None
        self._last_timeline_ts: float | None = None

        # Log throttling / one-time flags (exact behavior)
        self._last_stats_log_time: float = 0.0
        self._stats_had_error: bool = False
        self._cameras_logged: bool = False
        self._logged_connected: bool = False

        # For de-duping review logs (same review id shouldn't spam the activity log)
        self._logged_review_ids: set[str] = set()
        # Separate from review alerts — an alert log must not suppress the GenAI summary line
        self._logged_review_genai_ids: set[str] = set()

        # Track event ids for which we've already logged an LLM/GenAI description
        self._logged_llm_descriptions: set[str] = set()
        self._detection_alert_at: dict[str, float] = {}
        self._timeline_reconcile_task: asyncio.Task | None = None
        self.genai_activity: list[dict[str, Any]] = []
        self._genai_activity_keys: set[str] = set()

        # Canonical live state (source of truth for TUI + web)
        self.last_stats: dict[str, Any] | None = None
        self.recent_events: list[FrigateEvent] = []
        self.health: SystemHealth | None = None
        self.cameras: list[CameraStats] = []
        self.connection_ok: bool = True
        self.latency_ms: int | None = None
        self.last_error: str | None = None

        # Activity log buffer (replay for late web clients + diagnostics)
        self.log_entries: list[dict[str, Any]] = []  # [{"ts": "HH:MM:SS", "level": "...", "message": "..."}]
        self._max_log_entries: int = 300

        # History for web histograms / time series (global + per-camera)
        # Keep last ~10 minutes of 1s samples (600 points)
        self._max_history: int = 600
        self.health_history: list[dict[str, Any]] = []
        self.cameras_history: list[dict[str, Any]] = []  # each entry: {'timestamp': , 'cameras': list of dicts}

        # Listeners (TUI renderers + web SSE broadcaster)
        self._listeners: list[Callable[[str, Any], None | Awaitable[None]]] = []

        # Lifecycle
        self._running: bool = False
        self._tasks: list[asyncio.Task] = []

    # ------------------------------------------------------------------
    # Observer API (used by TUI and web server)
    # ------------------------------------------------------------------

    def add_update_listener(self, callback: Callable[[str, Any], None | Awaitable[None]]) -> None:
        """Register a listener for state changes.

        Kinds emitted:
          - "version" -> str
          - "stats" -> raw stats dict | None
          - "cameras" -> list[CameraStats]
          - "health" -> SystemHealth | None
          - "events" -> list[FrigateEvent]  (authoritative current list; events may include .description from Frigate GenAI/LLM)
          - "log" -> {"ts": str, "level": str, "message": str}
          - "connection" -> {"ok": bool, "latency_ms": int|None, "last_error": str|None, "url": str}
          - "poll_interval" -> float (current main poll rate in seconds)
        """
        self._listeners.append(callback)

    def _notify(self, kind: str, payload: Any = None) -> None:
        for cb in list(self._listeners):
            try:
                result = cb(kind, payload)
                if asyncio.iscoroutine(result):
                    # Fire-and-forget; errors are swallowed to keep core robust
                    asyncio.create_task(result)  # type: ignore[arg-type]
            except Exception:
                # Listener must never break the core
                pass

    # ------------------------------------------------------------------
    # Public state access (for snapshots, initial renders, manual queries)
    # ------------------------------------------------------------------

    @property
    def frigate_version(self) -> str:
        return self._frigate_version

    @staticmethod
    def event_to_dict(e: FrigateEvent) -> dict[str, Any]:
        """JSON-friendly event for web modal / API."""
        d = asdict(e)
        d["color"] = e.color
        d["display_label"] = e.display_label
        d["duration_s"] = e.duration_s
        return d

    async def fetch_event_detail(self, event_id: str) -> dict[str, Any] | None:
        """Load latest event fields from Frigate plus any GenAI review tied to this event id."""
        if self.demo:
            for e in self.recent_events:
                if e.id == event_id:
                    return {"event": self.event_to_dict(e), "review_genai": None}
            return None
        if not self._client:
            return None
        try:
            raw = await self._client.get_event(event_id)
            if not raw:
                return None
            fe = frigate_event_from_dict(raw)
            if fe is None:
                return None
            for i, existing in enumerate(self.recent_events):
                if existing.id != fe.id:
                    continue
                merged = merge_frigate_event_updates(existing, fe)
                if self._events_signature([merged]) != self._events_signature([existing]):
                    self.recent_events[i] = merged
                    self._sync_event_descriptions_to_genai_activity([merged])
                    self._log_llm_description(merged, source="detail")
                    self._notify("events", self.recent_events)
                fe = merged
                break
            review_genai: dict[str, Any] | None = None
            try:
                for r in await self._client.get_review_items(limit=50):
                    dets = (r.get("data") or {}).get("detections") or []
                    if event_id in dets:
                        review_genai = genai_summary_from_review_data(r.get("data") or {})
                        if review_genai:
                            break
            except Exception:
                pass
            return {"event": self.event_to_dict(fe), "review_genai": review_genai}
        except Exception:
            return None

    def get_snapshot(self) -> dict[str, Any]:
        """Return a JSON-serializable snapshot suitable for web initial state or debug."""
        # Convert dataclasses to plain dicts for transport
        def _cam(c: CameraStats) -> dict[str, Any]:
            d = asdict(c)
            d["health_color"] = c.health_color
            d["health_summary"] = c.health_summary
            return d

        def _evt(e: FrigateEvent) -> dict[str, Any]:
            return self.event_to_dict(e)

        def _health(h: SystemHealth | None) -> dict[str, Any] | None:
            if not h:
                return None
            d = asdict(h)
            d["status_color"] = h.status_color
            return d

        summary = {}
        if self.last_stats:
            summary["detection_fps"] = float(self.last_stats.get("detection_fps", 0))
            summary["skipped_fps"] = float(self.last_stats.get("skipped_fps", 0))
            gpus = self.last_stats.get("gpu_usages") or {}
            if gpus:
                summary["gpu"] = next(iter(gpus.values())).get("gpu", "—")

        return {
            "frigate_url": self.frigate_url,
            "frigate_version": self._frigate_version,
            "cameras": [_cam(c) for c in self.cameras],
            "recent_events": [_evt(e) for e in self.recent_events],
            "health": _health(self.health),
            "connection": {
                "ok": self.connection_ok,
                "latency_ms": self.latency_ms,
                "last_error": self.last_error,
                "url": self.frigate_url,
            },
            "logs": list(self.log_entries[-200:]),  # last N for UI
            "summary": summary,
            "demo": self.demo,
            "max_events": self.max_events,
            "poll_interval": self.poll_interval,
            "tui_version": __version__,
            "health_history": self.health_history[-300:],  # ~5 min for charts
            "cameras_history": self.cameras_history[-300:],
            "web_alerts": {
                "enabled": self.web_alerts_enabled,
                "max_queue": self.web_alerts_max_queue,
                "sounds": dict(self.web_alerts_sounds),
            },
            "genai_health": self.genai_health,
            "frigate_report": {
                "auto_interval": self.frigate_report_auto_interval,
                "save_dir": self.frigate_report_save_dir,
            },
            "display_timezone": self.display_timezone,
            "display_timezone_label": tz_label_now(self.display_timezone),
            "video_history": {
                "enabled": self.video_history_config.get("enabled", True),
                "recordings_dir": self.video_history_config.get(
                    "recordings_dir", "/media/frigate/recordings"
                ),
                "max_files": self.video_history_config.get("max_files", 500),
            },
            "chatterbox_tts": {
                "enabled": self.chatterbox_tts_config.get("enabled", False),
                "voice_mode": self.chatterbox_tts_config.get("voice_mode", "clone"),
                "reference_audio_filename": self.chatterbox_tts_config.get(
                    "reference_audio_filename", ""
                ),
                "predefined_voice_id": self.chatterbox_tts_config.get("predefined_voice_id", ""),
                "default_test_message": self.chatterbox_tts_config.get(
                    "default_test_message", ""
                ),
                "event_alerts": self.chatterbox_tts_config.get("event_alerts", False),
                "timeline_alerts": self.chatterbox_tts_config.get("timeline_alerts", False),
                "event_template": self.chatterbox_tts_config.get(
                    "event_template", "{label} on {camera}"
                ),
                "chosen_voice": self.get_event_tts_voice(),
                "delivery_mode": self.get_event_tts_delivery_mode(),
                "delivery_modes": list(DELIVERY_MODES),
            },
        }

    def get_event_tts_voice(self) -> dict[str, str] | None:
        """UI-selected voice for event alerts and TTS generation (overrides config)."""
        mode = (self._event_tts_voice_mode or "").strip()
        voice = (self._event_tts_voice or "").strip()
        if mode in ("clone", "predefined") and voice:
            return {"voice_mode": mode, "voice": voice}
        return None

    def set_event_tts_voice(
        self,
        voice_mode: str | None,
        voice: str | None,
    ) -> dict[str, str] | None:
        """Save the UI-chosen voice used when generating new alert recordings."""
        mode = str(voice_mode or "").strip().lower()
        name = str(voice or "").strip()
        if mode in ("clone", "predefined") and name:
            self._event_tts_voice_mode = mode
            self._event_tts_voice = name
        else:
            self._event_tts_voice_mode = None
            self._event_tts_voice = None
        save_event_tts_prefs(
            self.chatterbox_tts_config.get("cache_dir", "localrecordings"),
            voice_mode=self._event_tts_voice_mode,
            voice=self._event_tts_voice,
            delivery_mode=self._event_tts_delivery_mode,
        )
        return self.get_event_tts_voice()

    def get_event_tts_delivery_mode(self) -> str:
        return normalize_delivery_mode(self._event_tts_delivery_mode)

    def set_event_tts_delivery_mode(self, delivery_mode: str | None) -> str:
        """Save the UI-chosen delivery style for alerts and Speak tests."""
        self._event_tts_delivery_mode = normalize_delivery_mode(delivery_mode)
        save_event_tts_prefs(
            self.chatterbox_tts_config.get("cache_dir", "localrecordings"),
            voice_mode=self._event_tts_voice_mode,
            voice=self._event_tts_voice,
            delivery_mode=self._event_tts_delivery_mode,
        )
        return self.get_event_tts_delivery_mode()

    def get_event_tts_settings(self) -> dict[str, Any]:
        """Chatterbox settings with UI-chosen voice and delivery mode applied."""
        chosen = self.get_event_tts_voice()
        if chosen:
            settings = apply_voice_override(
                self.chatterbox_tts_config,
                voice_mode=chosen["voice_mode"],
                voice=chosen["voice"],
            )
        else:
            settings = dict(self.chatterbox_tts_config)
        return apply_delivery_mode_settings(
            settings,
            self.get_event_tts_delivery_mode(),
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        if self._running:
            return
        self._running = True

        self._client = FrigateClient(self.frigate_url)

        self.add_log(f"Starting Frigate TUI v{__version__}", "info")
        self.add_log(f"Target: {self.frigate_url}", "info")

        # One-time version (important diagnostic, same as TUI)
        try:
            self._frigate_version = await self._client.get_version()
            self.add_log(f"Connected to Frigate {self._frigate_version}", "success")
        except Exception as e:
            self._frigate_version = "unknown"
            self.add_log(
                f"Failed to reach {self.frigate_url}/api/version: {self._format_connection_error(e)}",
                "error",
            )
        self._notify("version", self._frigate_version)

        # Seed initial data (non-demo)
        if self.demo:
            self._seed_demo_events()
        else:
            await self._load_initial_events()
            await self._load_initial_timeline()
            await self._backfill_genai_activity()

        # Always poll stats
        self._tasks.append(
            asyncio.create_task(self._interval_loop("stats", lambda: self.poll_interval, self._refresh_stats))
        )

        # Events: MQTT preferred, else polling (exact same decision tree)
        if self.mqtt_config and not MQTT_AVAILABLE:
            self.add_log("MQTT configured but aiomqtt not installed — falling back to polling", "warning")

        # HTTP /api/events poll runs alongside MQTT. Timeline & review lines can be minutes
        # ahead of frigate/events; polling keeps the Events tab close to the Activity Log.
        if not self.demo:
            self._tasks.append(
                asyncio.create_task(
                    self._interval_loop(
                        "events",
                        lambda: self.events_poll_interval,
                        self._refresh_events,
                    )
                )
            )
            self._event_polling_active = True

        if self._use_mqtt_for_events and self.mqtt_config:
            self._mqtt_task = asyncio.create_task(self._run_mqtt_listener())
            self._tasks.append(self._mqtt_task)
            self.add_log(
                f"MQTT event subscription enabled; Events table also refreshed via HTTP every {self.events_poll_interval:.0f}s",
                "info",
            )
        elif not self.demo:
            self.add_log(
                f"Event polling every {self.events_poll_interval:.0f}s (MQTT not configured)",
                "info",
            )

        self._log_genai_monitoring_mode()
        asyncio.create_task(self.refresh_genai_health(force=True))

        # Higher-level signals (only used for activity log, same as TUI)
        # These are fixed but could be made dynamic via self.review_interval etc if needed
        self._tasks.append(
            asyncio.create_task(self._interval_loop("reviews", lambda: self.review_interval, self._refresh_reviews))
        )
        self._tasks.append(
            asyncio.create_task(self._interval_loop("timeline", lambda: self.timeline_interval, self._refresh_timeline))
        )
        # HTTP reconciliation keeps the Events table current when MQTT misses events
        # (GenAI tracked_object_update can still flow while frigate/events does not).
        if not self.demo:
            self._tasks.append(
                asyncio.create_task(
                    self._interval_loop(
                        "events_reconcile",
                        lambda: self.events_reconcile_interval,
                        self._reconcile_events,
                    )
                )
            )

        # Prime stats immediately
        await self._refresh_stats()
        if not self.demo:
            await self._reconcile_events()

    async def stop(self) -> None:
        self._running = False
        for task in self._tasks:
            if not task.done():
                task.cancel()
        # Best-effort await (ignore errors)
        try:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        except Exception:
            pass
        self._tasks.clear()

        if self._client:
            try:
                await self._client.aclose()
            except Exception:
                pass
            self._client = None

        if self._mqtt_client:
            try:
                await self._mqtt_client.disconnect()
            except Exception:
                pass
            self._mqtt_client = None

    async def refresh_now(self) -> None:
        """Manual full refresh (wired to 'r' in both UIs)."""
        self.add_log("Manual refresh requested", "info")
        await self._refresh_stats()
        await self._refresh_events()

    def set_poll_interval(self, interval: float) -> None:
        """Change the main polling speed at runtime (affects stats + events polling rate).
        Reviews and timeline use their own *_interval attrs (also overridable in config)."""
        old = self.poll_interval
        self.poll_interval = max(1.0, round(float(interval)))
        self.add_log(f"Poll interval set to {self.poll_interval:.0f}s (was {old:.0f}s)", "info")
        self._notify("poll_interval", self.poll_interval)

    async def _interval_loop(
        self, name: str, get_interval: Callable[[], float], func: Callable[[], Awaitable[None]]
    ) -> None:
        """Generic interval driver (replacement for Textual set_interval). The get_interval callable allows runtime changes to poll speed."""
        while self._running:
            try:
                await func()
            except asyncio.CancelledError:
                break
            except Exception as e:
                # Never let a single interval kill the core
                self.add_log(f"{name} interval error: {e}", "warning")
            await asyncio.sleep(get_interval())

    # ------------------------------------------------------------------
    # Error formatting (exact messages for parity in logs/status)
    # ------------------------------------------------------------------

    def _format_connection_error(self, e: Exception) -> str:
        msg = str(e)
        if "errno -2" in msg or "Name or service not known" in msg:
            return f"DNS failure for '{self.frigate_url}' (errno -2). Wrong Docker network or hostname?"
        if "Connection refused" in msg:
            return "Connection refused — is Frigate running and listening on the port?"
        if "timeout" in msg.lower():
            return "Connection timed out"
        return msg[:70]

    # ------------------------------------------------------------------
    # Logging (now central; feeds both RichLog in TUI and SSE buffer in web)
    # ------------------------------------------------------------------

    def add_log(self, message: str, level: str = "info") -> None:
        dt = now_local(self.display_timezone)
        label = dt.strftime("%Z")
        ts = f"{dt.strftime('%H:%M:%S')} {label}" if label else dt.strftime("%H:%M:%S")
        entry = {"ts": ts, "level": level, "message": message}
        self.log_entries.append(entry)
        if len(self.log_entries) > self._max_log_entries:
            self.log_entries = self.log_entries[-self._max_log_entries :]
        self._notify("log", entry)

    def _log_genai_monitoring_mode(self) -> None:
        """One-time Activity Log note on how Frigate GenAI/LLM output reaches the monitor."""
        if self.demo:
            self.add_log(
                "GenAI/LLM (demo): sample object descriptions appear in the Activity Log as LLM: lines",
                "info",
            )
            return
        if self._use_mqtt_for_events and self.mqtt_config:
            self.add_log(
                "GenAI/LLM: real-time via MQTT (tracked_object_update + events); "
                "review summaries via /api/review poll",
                "info",
            )
        elif self.mqtt_config:
            self.add_log(
                "GenAI/LLM: HTTP polling for descriptions (MQTT unavailable); "
                "enable aiomqtt or fix broker for lower latency",
                "warning",
            )
        else:
            self.add_log(
                "GenAI/LLM: HTTP polling only — add mqtt to config.yaml for live description updates",
                "info",
            )

    @staticmethod
    def _truncate_log_text(text: str, max_len: int = 100) -> str:
        text = str(text).strip()
        if len(text) <= max_len:
            return text
        cut = text[:max_len]
        if " " in cut:
            cut = cut.rsplit(" ", 1)[0]
        return cut.rstrip(".,;:") + "…"

    def _event_alerts_enabled(self) -> bool:
        if not self.web_alerts_enabled:
            return False
        return bool(self.chatterbox_tts_config.get("event_alerts"))

    def _timeline_alerts_enabled(self) -> bool:
        if not self.web_alerts_enabled:
            return False
        cfg = self.chatterbox_tts_config
        return bool(cfg.get("timeline_alerts", cfg.get("event_alerts", False)))

    def _detection_alerts_enabled(self) -> bool:
        return self._event_alerts_enabled() or self._timeline_alerts_enabled()

    def _detection_alert_cooldown(self) -> float:
        cfg = self.chatterbox_tts_config
        return float(
            cfg.get("alert_cooldown", cfg.get("timeline_alert_cooldown", 120))
        )

    def _detection_alert_key(
        self,
        *,
        camera: str,
        label: str,
        sub_label: str | None,
    ) -> str:
        """Cooldown bucket for what the user hears, e.g. family on amcrest1."""
        spoken = resolve_event_tts_label(label, sub_label).strip().lower()
        cam = (camera or "camera").strip().lower()
        return f"{cam}|{spoken}"

    def _should_emit_detection_alert(
        self,
        *,
        camera: str,
        label: str,
        sub_label: str | None,
    ) -> bool:
        if not self._detection_alerts_enabled():
            return False
        key = self._detection_alert_key(
            camera=camera,
            label=label,
            sub_label=sub_label,
        )
        now = time.time()
        cooldown = self._detection_alert_cooldown()
        last = self._detection_alert_at.get(key, 0.0)
        if now - last < cooldown:
            return False
        self._detection_alert_at[key] = now
        if len(self._detection_alert_at) > 500:
            cutoff = now - max(cooldown * 4, 600.0)
            self._detection_alert_at = {
                k: ts for k, ts in self._detection_alert_at.items() if ts >= cutoff
            }
        return True

    def _notify_event_alerts(
        self,
        detections: list[dict[str, Any]],
        *,
        source: str = "event",
    ) -> None:
        if not detections:
            return
        if source == "timeline":
            if not self._timeline_alerts_enabled():
                return
        elif not self._event_alerts_enabled():
            return
        tts_cfg = self.chatterbox_tts_config
        use_tts = bool(tts_cfg.get("enabled"))
        tts_template = str(tts_cfg.get("event_template") or "{label} on {camera}")
        alert_items: list[dict[str, Any]] = []
        for det in detections:
            label = str(det.get("label") or "object")
            sub_label = det.get("sub_label")
            camera = str(det.get("camera") or "camera")
            det_id = str(det.get("id") or "")
            if not self._should_emit_detection_alert(
                camera=camera,
                label=label,
                sub_label=sub_label,
            ):
                continue
            item: dict[str, Any] = {
                "id": det_id,
                "camera": camera,
                "label": str(det.get("display_label") or label),
                "sound": event_alert_sound_key(label, sub_label),
            }
            if use_tts:
                base_tts = format_event_tts_message(
                    label,
                    camera,
                    sub_label=sub_label,
                    template=tts_template,
                )
                item["tts_text"] = base_tts
            alert_items.append(item)
        if alert_items:
            self._notify("event_alerts", alert_items)

    def _maybe_emit_timeline_alert(self, entry: TimelineEntry) -> None:
        if not self._timeline_alerts_enabled():
            return
        if entry.class_type not in ("visible", "active"):
            return
        sub_label = normalize_sub_label(entry.sub_label)
        display = f"{entry.label} ({sub_label})" if sub_label else entry.label
        det_id = (entry.source_id or "").strip()
        self._notify_event_alerts(
            [
                {
                    "id": det_id,
                    "camera": entry.camera,
                    "label": entry.label,
                    "sub_label": sub_label,
                    "display_label": display,
                }
            ],
            source="timeline",
        )

    def _should_log_timeline_entry(self, entry: TimelineEntry) -> bool:
        """Filter timeline rows so GenAI/review lines stay readable in the Activity Log."""
        if not self.timeline_log_enabled:
            return False
        score = float(entry.score or 0)
        if entry.class_type == "stationary":
            return self.timeline_log_stationary
        if entry.class_type == "gone":
            if entry.sub_label:
                return score >= self.timeline_log_gone_min_score
            return score > 0.85
        if entry.class_type in ("visible", "active"):
            return True
        return entry.sub_label or score > 0.85

    def _record_genai_activity(
        self,
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
    ) -> bool:
        """Store structured GenAI output for reports (upserted by kind+ref_id)."""
        if not ref_id:
            return False
        if not (text or "").strip() and not (title or "").strip():
            return False
        key = f"{kind}:{ref_id}"
        record = make_activity_record(
            kind=kind,
            camera=camera,
            title=title,
            text=text,
            label=label,
            objects=objects,
            sub_labels=sub_labels,
            threat=threat,
            ref_id=ref_id,
            ts=ts,
            tz_name=self.display_timezone,
        )
        for i, entry in enumerate(self.genai_activity):
            if f"{entry.get('kind')}:{entry.get('ref_id')}" != key:
                continue
            old_text = (entry.get("text") or "").strip()
            new_text = (text or "").strip()
            if new_text and new_text != old_text:
                self.genai_activity[i] = record
                return True
            return False
        self._genai_activity_keys.add(key)
        self.genai_activity.append(record)
        self._prune_genai_activity()
        return True

    def _sync_event_descriptions_to_genai_activity(
        self,
        events: list[FrigateEvent] | None = None,
        *,
        hours: float | None = None,
    ) -> int:
        """Ensure in-memory events with GenAI descriptions feed summary reports."""
        import time

        source = events if events is not None else self.recent_events
        window_h = hours if hours is not None else self._genai_activity_hours_keep
        cutoff = time.time() - max(0.25, window_h) * 3600.0
        synced = 0
        for event in source:
            desc = (event.description or "").strip()
            if not desc or not event.id or event.start_time < cutoff:
                continue
            obj_label = event.label or "object"
            subs = [event.sub_label] if event.sub_label else []
            if self._record_genai_activity(
                kind="object",
                camera=event.camera,
                title=event.display_label or obj_label,
                label=obj_label,
                objects=[obj_label],
                sub_labels=subs,
                text=desc,
                ref_id=event.id,
                ts=event.start_time,
            ):
                synced += 1
        return synced

    def _prune_genai_activity(self) -> None:
        import time

        cutoff = time.time() - self._genai_activity_hours_keep * 3600.0
        self.genai_activity = [e for e in self.genai_activity if float(e.get("ts", 0)) >= cutoff]
        if len(self.genai_activity) > self._max_genai_activity:
            self.genai_activity = self.genai_activity[-self._max_genai_activity :]
        self._genai_activity_keys = {
            f"{e['kind']}:{e['ref_id']}" for e in self.genai_activity if e.get("ref_id")
        }

    def get_genai_sections(self, hours: float | None = None) -> list[dict[str, Any]]:
        hrs = hours if hours is not None else self.genai_report_default_hours
        self._sync_event_descriptions_to_genai_activity(hours=hrs)
        return group_by_hour(self.genai_activity, hrs)

    def get_genai_messages(self, hours: float | None = None) -> list[dict[str, Any]]:
        hrs = hours if hours is not None else self.genai_report_default_hours
        self._sync_event_descriptions_to_genai_activity(hours=hrs)
        return list_chronological(self.genai_activity, hrs)

    async def generate_genai_report(self, hours: float | None = None) -> dict[str, Any]:
        """Build hourly sections and ask the configured LLM for a narrative report."""
        hrs = hours if hours is not None else self.genai_report_default_hours
        synced = self._sync_event_descriptions_to_genai_activity(hours=hrs)
        sections = group_by_hour(self.genai_activity, hrs)
        if synced:
            self.add_log(
                f"Summary: synced {synced} event GenAI description(s) from the events list",
                "info",
            )
        if self.demo:
            report = (
                "## Overall\n\n"
                "Demo mode sample: one person approached the driveway; routine vehicle traffic "
                "on the back deck; a dog was visible near the front porch.\n\n"
            )
            for sec in sections:  # newest hour first
                report += f"## {sec['hour_label']}\n\n"
                report += (
                    f"{sec['count']} GenAI message(s) on cameras "
                    f"{', '.join(sorted({m['camera'] for m in sec['messages']}))}.\n\n"
                )
            return {
                "ok": True,
                "demo": True,
                "hours": hrs,
                "sections": sections,
                "report": report.strip(),
                "generated_at": format_iso(time.time(), self.display_timezone),
            }
        if not sections:
            msg = f"Summary failed: no GenAI messages in the last {hrs:g} hour(s)"
            self.add_log(msg, "warning")
            return {
                "ok": False,
                "error": "No GenAI messages in the selected time window.",
                "hours": hrs,
                "sections": [],
                "report": None,
                "logged": True,
            }

        frigate_cfg: dict[str, Any] | None = None
        if self._client and not self.demo:
            try:
                frigate_cfg = await self._client.get_config()
            except Exception:
                pass

        llm = resolve_llm_settings(self.genai_report_config, frigate_cfg)
        if not llm:
            msg = (
                "Summary failed: no LLM configured — set genai_report.base_url and model in config.yaml "
                "(or ensure Frigate config has genai.provider settings)"
            )
            self.add_log(msg, "error")
            return {
                "ok": False,
                "error": "No LLM configured. Add genai_report.base_url and model to config.yaml "
                "(or ensure Frigate config has genai.provider settings).",
                "hours": hrs,
                "sections": sections,
                "report": None,
                "logged": True,
            }

        n_msgs = sum(s["count"] for s in sections)
        timeout = float(self.genai_report_config.get("timeout", 180))
        self.add_log(
            f"Summary: requesting report for {n_msgs} GenAI message(s) over {hrs:g}h "
            f"via {llm['model']} @ {llm['base_url']} (timeout {timeout:.0f}s)",
            "info",
        )
        report, err = await generate_activity_report(
            base_url=llm["base_url"],
            model=llm["model"],
            sections=sections,
            hours=hrs,
            timeout=timeout,
        )
        if err:
            self.add_log(f"Summary failed ({llm['model']} @ {llm['base_url']}): {err}", "error")
            return {
                "ok": False,
                "error": err,
                "hours": hrs,
                "sections": sections,
                "report": None,
                "llm": llm,
                "logged": True,
            }
        self.add_log(
            f"Summary generated for {n_msgs} GenAI message(s) over {hrs:g}h ({llm['model']})",
            "success",
        )
        return {
            "ok": True,
            "hours": hrs,
            "sections": sections,
            "report": report,
            "llm": llm,
            "generated_at": format_iso(time.time(), self.display_timezone),
        }

    def _persist_frigate_review_report(
        self,
        summary: str,
        *,
        hours: float,
        start_ts: float,
        end_ts: float,
        generated_at: datetime | None = None,
    ) -> str | None:
        when = generated_at or now_local(self.display_timezone)
        try:
            path = save_frigate_genai_report(
                summary,
                save_dir=self.frigate_report_save_dir,
                hours=hours,
                start_ts=start_ts,
                end_ts=end_ts,
                generated_at=when,
                tz_name=self.display_timezone,
            )
        except OSError as e:
            self.add_log(f"Frigate report save failed: {type(e).__name__}: {e}", "warning")
            return None
        if path:
            self.add_log(f"Frigate report saved: {path}", "info")
        return path

    async def generate_frigate_review_report(self, hours: float | None = None) -> dict[str, Any]:
        """Request Frigate's native GenAI review summarize report for a time window."""
        hrs = hours if hours is not None else self.genai_report_default_hours
        end_ts = time.time()
        start_ts = end_ts - hrs * 3600.0
        generated_at = now_local(self.display_timezone)
        generated_iso = format_iso(end_ts, self.display_timezone)
        if self.demo:
            summary = (
                "Demo mode: no suspicious review activity was flagged in this window. "
                "Frigate's native report summarizes alert-level review GenAI summaries."
            )
            saved_path = self._persist_frigate_review_report(
                summary,
                hours=hrs,
                start_ts=start_ts,
                end_ts=end_ts,
                generated_at=generated_at,
            )
            return {
                "ok": True,
                "demo": True,
                "hours": hrs,
                "start_ts": start_ts,
                "end_ts": end_ts,
                "summary": summary,
                "generated_at": generated_iso,
                "saved_path": saved_path,
            }
        if not self._client:
            return {
                "ok": False,
                "error": "Frigate client not available.",
                "hours": hrs,
                "summary": None,
                "logged": True,
            }
        timeout = float(self.genai_report_config.get("timeout", 180))
        self.add_log(
            f"Frigate report: requesting review summarize for the last {hrs:g}h",
            "info",
        )
        try:
            result = await self._client.summarize_reviews(
                start_ts=start_ts,
                end_ts=end_ts,
                timeout=timeout,
            )
        except Exception as e:
            err = self._format_connection_error(e)
            self.add_log(f"Frigate report failed: {err}", "error")
            return {
                "ok": False,
                "error": err,
                "hours": hrs,
                "start_ts": start_ts,
                "end_ts": end_ts,
                "summary": None,
                "logged": True,
            }
        if not result.get("success"):
            err = str(result.get("message") or result.get("error") or "Frigate summarize failed")
            self.add_log(f"Frigate report failed: {err}", "error")
            return {
                "ok": False,
                "error": err,
                "hours": hrs,
                "start_ts": start_ts,
                "end_ts": end_ts,
                "summary": result.get("summary"),
                "logged": True,
            }
        summary = str(result.get("summary") or "").strip()
        if summary:
            summary = localize_frigate_report_times(
                summary,
                window_start_ts=start_ts,
                tz_name=self.display_timezone,
            )
        saved_path = self._persist_frigate_review_report(
            summary,
            hours=hrs,
            start_ts=start_ts,
            end_ts=end_ts,
            generated_at=generated_at,
        )
        self.add_log(f"Frigate report ready ({hrs:g}h)", "success")
        return {
            "ok": True,
            "hours": hrs,
            "start_ts": start_ts,
            "end_ts": end_ts,
            "summary": summary,
            "generated_at": generated_iso,
            "saved_path": saved_path,
        }

    async def _backfill_genai_activity(self) -> None:
        """Seed history from recent Frigate reviews that already have GenAI metadata."""
        if self.demo or not self._client:
            return
        import time

        cutoff = time.time() - self._genai_activity_hours_keep * 3600.0
        try:
            reviews = await self._client.get_review_items(limit=100)
            added = 0
            for raw in reviews:
                rev = review_item_from_dict(raw)
                if rev is None or not rev.genai_summary or not rev.id:
                    continue
                if rev.start_time < cutoff:
                    continue
                g = rev.genai_summary
                text = str(g.get("shortSummary") or g.get("scene") or "").strip()
                self._record_genai_activity(
                    kind="review",
                    camera=rev.camera,
                    title=str(g.get("title") or ""),
                    text=text,
                    objects=rev.objects,
                    sub_labels=rev.sub_labels,
                    threat=g.get("potential_threat_level"),
                    ref_id=rev.id,
                    ts=rev.start_time,
                )
                added += 1
            event_synced = self._sync_event_descriptions_to_genai_activity()
            if added:
                self.add_log(f"GenAI report: loaded {added} review summaries from Frigate history", "info")
            if event_synced:
                self.add_log(
                    f"GenAI report: synced {event_synced} event description(s) from loaded events",
                    "info",
                )
        except Exception as e:
            self.add_log(f"GenAI history backfill error: {e}", "warning")

    def _log_review_genai_summary(self, rev: ReviewItem) -> None:
        """Log Frigate GenAI review summary (title + shortSummary) once per review id."""
        if not rev.genai_summary or not rev.id or rev.id in self._logged_review_genai_ids:
            return
        g = rev.genai_summary
        title = g.get("title", "")
        short = g.get("shortSummary", "")
        threat = g.get("potential_threat_level")
        text = str(short or g.get("scene") or "").strip()
        self._record_genai_activity(
            kind="review",
            camera=rev.camera,
            title=str(title or ""),
            text=text,
            objects=rev.objects,
            sub_labels=rev.sub_labels,
            threat=threat,
            ref_id=rev.id,
            ts=rev.start_time,
        )
        msg = f"GenAI [review]: {rev.camera}"
        if title:
            msg += f" — {title}"
        if short:
            msg += f" — {self._truncate_log_text(short)}"
        if threat is not None and threat > 0:
            msg += f" [threat={threat}]"
        self.add_log(msg, "info")
        self._logged_review_genai_ids.add(rev.id)

    def _log_llm_description(
        self,
        event: FrigateEvent,
        *,
        source: str = "",
        demo: bool = False,
    ) -> None:
        if not event.description or event.id in self._logged_llm_descriptions:
            return
        label = event.display_label or event.label
        prefix = "LLM (demo)" if demo else "LLM"
        via = f" [{source}]" if source else ""
        obj_label = event.label or "object"
        subs = [event.sub_label] if event.sub_label else []
        self._record_genai_activity(
            kind="object",
            camera=event.camera,
            title=label,
            label=obj_label,
            objects=[obj_label],
            sub_labels=subs,
            text=str(event.description),
            ref_id=event.id,
            ts=event.start_time,
        )
        self.add_log(
            f"{prefix}{via}: Description for {label} on {event.camera} — {event.description[:180]}",
            "info",
        )
        self._logged_llm_descriptions.add(event.id)

    def _surface_recent_llm_descriptions(self, events: list[FrigateEvent], *, limit: int = 3) -> None:
        """Seed summary data and log the newest descriptions already present in Frigate."""
        synced = self._sync_event_descriptions_to_genai_activity(events)
        with_desc = [e for e in events if e.description]
        if not with_desc:
            return
        msg = f"GenAI/LLM: {len(with_desc)} loaded event(s) already include descriptions"
        if synced:
            msg += f" ({synced} synced for Summary)"
        self.add_log(msg, "info")
        for e in sorted(with_desc, key=lambda x: x.start_time, reverse=True)[:limit]:
            self._log_llm_description(e, source="startup")

    # ------------------------------------------------------------------
    # GenAI health (LLM probe + ingestion + message activity)
    # ------------------------------------------------------------------

    async def _get_frigate_config_cached(self, max_age: float = 300.0) -> dict[str, Any] | None:
        now = time.time()
        if self._frigate_config_cache is not None and (now - self._frigate_config_cached_at) < max_age:
            return self._frigate_config_cache
        if self.demo or not self._client or not self.connection_ok:
            return self._frigate_config_cache
        try:
            cfg = await self._client.get_config()
            if isinstance(cfg, dict):
                self._frigate_config_cache = cfg
                self._frigate_config_cached_at = now
        except Exception:
            pass
        return self._frigate_config_cache

    async def refresh_genai_health(self, *, force: bool = False) -> dict[str, Any]:
        """Probe LLM and summarize GenAI ingestion; notify listeners."""
        frigate_cfg = await self._get_frigate_config_cached()
        timeout = float(self.genai_report_config.get("health_probe_timeout", 8.0))
        review_probe: dict[str, Any] | None = None
        if not self.demo and self._client and self.connection_ok:
            try:
                reviews_raw = await self._client.get_review_items(limit=50)
                frigate_cfg = frigate_cfg or await self._get_frigate_config_cached()
                review_cfg = None
                if isinstance(frigate_cfg, dict) and isinstance(frigate_cfg.get("review"), dict):
                    review_cfg = frigate_cfg["review"].get("genai")
                    if not isinstance(review_cfg, dict):
                        review_cfg = None
                review_probe = frigate_review_genai_probe(
                    reviews_raw,
                    hours=1.0,
                    review_genai_cfg=review_cfg,
                )
            except Exception:
                review_probe = None
        health = await collect_genai_health(
            demo=self.demo,
            genai_report_config=self.genai_report_config,
            genai_activity=self.genai_activity,
            mqtt_configured=bool(self.mqtt_config),
            mqtt_available=MQTT_AVAILABLE,
            mqtt_connected=self.mqtt_connected,
            use_mqtt_for_events=self._use_mqtt_for_events,
            frigate_config=frigate_cfg,
            frigate_review_probe=review_probe,
            llm_probe_timeout=timeout,
        )
        self.genai_health = health
        self._last_genai_health_check = time.time()
        self._notify("genai_health", health)
        return health

    async def _maybe_refresh_genai_health(self) -> None:
        if self.demo:
            if not self.genai_health:
                await self.refresh_genai_health(force=True)
            return
        elapsed = time.time() - self._last_genai_health_check
        if elapsed >= self._genai_health_interval:
            await self.refresh_genai_health()

    # ------------------------------------------------------------------
    # Stats (always running, drives summary + cameras + health + conn)
    # ------------------------------------------------------------------

    async def _refresh_stats(self) -> None:
        start = asyncio.get_event_loop().time()

        try:
            if self.demo:
                stats = self._demo_stats()
            else:
                assert self._client is not None
                stats = await self._client.get_stats()

            ok = True
            err = None
        except Exception as e:
            stats = None
            ok = False
            err = self._format_connection_error(e)

        latency = int((asyncio.get_event_loop().time() - start) * 1000)

        self.last_stats = stats
        self.connection_ok = ok
        self.latency_ms = latency if ok else None
        self.last_error = err

        if stats:
            self.cameras = parse_cameras(stats)
            self.health = compute_health(stats)

            # Record history for web charts/histograms
            ts = time.time()
            self.health_history.append({
                'timestamp': ts,
                'pressure_pct': self.health.pressure_pct,
                'detection_pressure': self.health.detection_pressure,
                'skipped_fps': self.health.skipped_fps,
                'detection_fps': self.health.detection_fps,
            })
            self.cameras_history.append({
                'timestamp': ts,
                'cameras': [
                    {
                        'name': c.name,
                        'detection_fps': c.detection_fps,
                        'skipped_fps': c.skipped_fps,
                        'camera_fps': c.camera_fps,
                    }
                    for c in self.cameras
                ],
            })
            if len(self.health_history) > self._max_history:
                self.health_history = self.health_history[-self._max_history:]
            if len(self.cameras_history) > self._max_history:
                self.cameras_history = self.cameras_history[-self._max_history:]

            if not self._cameras_logged:
                if self.cameras:
                    self.add_log(
                        f"Parsed {len(self.cameras)} cameras from stats",
                        "info",
                    )
                else:
                    self.add_log("Stats received but no cameras found in response", "warning")
                self._cameras_logged = True

        # Notify consumers (TUI re-renders, web pushes via SSE)
        self._notify("stats", stats)
        self._notify("cameras", self.cameras)
        self._notify("health", self.health)
        await self._maybe_refresh_genai_health()
        self._update_connection_state()

        # Throttled success logging + immediate errors (exact same rules)
        now = asyncio.get_event_loop().time()
        if ok:
            det = float(stats.get("detection_fps", 0)) if stats else 0
            should_log = (
                now - self._last_stats_log_time > self.stats_log_interval or self._stats_had_error
            )
            if should_log:
                self.add_log(f"Stats OK ({latency}ms) — detection {det:.1f} fps", "success")
                self._last_stats_log_time = now
                self._stats_had_error = False
        else:
            self.add_log(f"Stats poll failed: {err}", "error")
            self._stats_had_error = True
            self._last_stats_log_time = now

    def _update_connection_state(self) -> None:
        """Central connection state change detection + logging (parity with TUI)."""
        if self.connection_ok and self.last_error is None:
            if not self._logged_connected:
                self.add_log("Connection established", "success")
                self._logged_connected = True
        else:
            if self._logged_connected:
                err = self.last_error or "unknown error"
                self.add_log(f"Connection lost: {err}", "error")
                self._logged_connected = False

        self._notify(
            "connection",
            {
                "ok": self.connection_ok,
                "latency_ms": self.latency_ms,
                "last_error": self.last_error,
                "url": self.frigate_url,
            },
        )

    # ------------------------------------------------------------------
    # Events (initial + poll + MQTT with identical merge/ordering/dedup)
    # ------------------------------------------------------------------

    def _events_signature(self, events: list[FrigateEvent]) -> tuple[Any, ...]:
        return tuple(
            (e.id, e.start_time, e.end_time, e.description, e.top_score, e.has_clip, e.has_snapshot)
            for e in events
        )

    def _merge_events(
        self,
        incoming: list[FrigateEvent],
        *,
        log_new: bool = False,
        log_prefix: str = "Event",
        llm_source: str = "",
    ) -> bool:
        """Merge events into recent_events; notify listeners when the list changes."""
        if not incoming:
            return False

        old_ids = {e.id for e in self.recent_events}
        for fe in incoming:
            if fe.start_time > (self._last_event_ts or 0):
                self._last_event_ts = fe.start_time
            if llm_source:
                self._log_llm_description(fe, source=llm_source)

        by_id = {e.id: e for e in self.recent_events}
        for fe in incoming:
            if fe.id in by_id:
                by_id[fe.id] = merge_frigate_event_updates(by_id[fe.id], fe)
            else:
                by_id[fe.id] = fe
        merged = sorted(by_id.values(), key=lambda x: x.start_time, reverse=True)[
            : self.max_events
        ]

        if self._events_signature(merged) == self._events_signature(self.recent_events):
            return False

        old_by_id = {e.id: e for e in self.recent_events}
        new_only = [e for e in incoming if e.id not in old_ids]
        sub_label_new: list[FrigateEvent] = []
        for e in incoming:
            if e.id not in old_ids:
                continue
            prev = old_by_id.get(e.id)
            if prev is None:
                continue
            if not normalize_sub_label(prev.sub_label) and normalize_sub_label(e.sub_label):
                sub_label_new.append(e)
        self.recent_events = merged
        self._sync_event_descriptions_to_genai_activity(incoming)
        self._notify("events", self.recent_events)
        alert_events = new_only + [e for e in sub_label_new if e.id not in {n.id for n in new_only}]
        if alert_events:
            self._notify_event_alerts(
                [
                    {
                        "id": e.id,
                        "camera": e.camera,
                        "label": e.label,
                        "sub_label": e.sub_label,
                        "display_label": e.display_label or e.label,
                    }
                    for e in alert_events
                ],
            )

        if log_new and new_only:
            for ev in new_only[:3]:
                self.add_log(f"{log_prefix}: {ev.display_label} on {ev.camera}", "success")
            if len(new_only) > 3:
                self.add_log(f"+{len(new_only)-3} more events", "info")
        return True

    async def _reconcile_events(self) -> None:
        """Refresh the event list from /api/events (no 'after' cursor) to heal MQTT gaps."""
        if self.demo or not self._client:
            return
        try:
            assert self._client is not None
            limit = min(80, self.max_events)
            events_raw = await self._client.get_events(limit=limit)
            if not events_raw:
                return

            loaded: list[FrigateEvent] = []
            for e in events_raw:
                fe = frigate_event_from_dict(e)
                if fe is not None:
                    loaded.append(fe)

            old_ids = {e.id for e in self.recent_events}
            if self._merge_events(loaded, llm_source="reconcile"):
                added = [e for e in loaded if e.id not in old_ids]
                if added:
                    newest = max(e.start_time for e in added)
                    self.add_log(
                        f"Events table updated from API (+{len(added)}; newest {format_time(newest, self.display_timezone)})",
                        "info",
                    )
        except Exception as e:
            self.add_log(f"Events reconcile error: {e}", "warning")

    def _schedule_timeline_reconcile(self) -> None:
        """After timeline activity, refresh /api/events (Frigate often lags timeline in the API)."""
        if self.demo or not self._client:
            return
        if self._timeline_reconcile_task and not self._timeline_reconcile_task.done():
            return

        async def _run() -> None:
            await asyncio.sleep(3.0)
            await self._reconcile_events()

        self._timeline_reconcile_task = asyncio.create_task(_run())

    async def _refresh_events(self) -> None:
        if self.demo:
            return
        try:
            assert self._client is not None
            # Overlap the cursor so events missed by MQTT or equal start_time still appear.
            after: float | None = None
            if self._last_event_ts:
                after = max(0.0, self._last_event_ts - 120.0)
            events_raw = await self._client.get_events(limit=50, after=after)
            if not events_raw:
                return

            new_events: list[FrigateEvent] = []
            for e in events_raw:
                fe = frigate_event_from_dict(e)
                if fe is not None:
                    new_events.append(fe)

            if self._merge_events(new_events, log_new=True, llm_source="poll"):
                for e in self.recent_events:
                    self._log_llm_description(e, source="poll")
        except Exception as e:
            self.add_log(f"Events poll error: {e}", "warning")

    async def _load_initial_events(self) -> None:
        if self.demo or not self._client:
            return
        try:
            events_raw = await self._client.get_events(limit=80)
            if not events_raw:
                self.add_log("No historical events found on startup", "info")
                return

            loaded: list[FrigateEvent] = []
            latest_ts = self._last_event_ts or 0
            for e in events_raw:
                fe = frigate_event_from_dict(e)
                if fe is None:
                    continue
                loaded.append(fe)
                if fe.start_time > latest_ts:
                    latest_ts = fe.start_time

            if loaded:
                self.recent_events = sorted(
                    loaded, key=lambda x: x.start_time, reverse=True
                )[: self.max_events]
                self._last_event_ts = latest_ts
                self._notify("events", self.recent_events)
                self.add_log(f"Loaded {len(self.recent_events)} historical events on startup", "info")
                self._surface_recent_llm_descriptions(self.recent_events)
        except Exception as e:
            self.add_log(f"Failed to load initial historical events: {e}", "warning")

    # ------------------------------------------------------------------
    # Reviews & Timeline (surfaced only to activity log, exact same rules)
    # ------------------------------------------------------------------

    async def _refresh_reviews(self) -> None:
        if self.demo or not self._client:
            return
        try:
            reviews_raw = await self._client.get_review_items(limit=15, has_been_reviewed=False)
            if not reviews_raw:
                return
            for r in reviews_raw:
                rev = review_item_from_dict(r)
                if rev is None:
                    continue

                # GenAI summaries often arrive after the alert line — check every poll, not only "new" reviews
                self._log_review_genai_summary(rev)

                start = rev.start_time
                if self._last_review_ts and start <= self._last_review_ts:
                    continue
                # Also de-dupe by review id so the exact same review never spams the log
                # even if the time-based guard is bypassed for some reason (multiple polls, etc.)
                if rev.id and rev.id in self._logged_review_ids:
                    if start > (self._last_review_ts or 0):
                        self._last_review_ts = start
                    continue

                if rev.severity == "alert" or rev.sub_labels:
                    msg = f"Review: {rev.severity.upper()} on {rev.camera}"
                    if rev.sub_labels:
                        msg += f" — {', '.join(rev.sub_labels)}"
                    elif rev.objects:
                        msg += f" — {', '.join(rev.objects)}"
                    self.add_log(msg, "warning" if rev.severity == "alert" else "info")
                    if rev.id:
                        self._logged_review_ids.add(rev.id)

                if start > (self._last_review_ts or 0):
                    self._last_review_ts = start
        except Exception as e:
            self.add_log(f"Review fetch error: {e}", "warning")

    async def _refresh_timeline(self) -> None:
        if self.demo or not self._client:
            return
        try:
            timeline_raw = await self._client.get_timeline(
                limit=20, after=self._last_timeline_ts, source="tracked_object"
            )
            if not timeline_raw:
                return
            for t in timeline_raw:
                entry = timeline_entry_from_dict(t)
                if entry is None:
                    continue
                ts = entry.timestamp
                if self._last_timeline_ts and ts <= self._last_timeline_ts:
                    continue
                if entry.class_type in ("visible", "gone", "stationary", "active"):
                    if self._should_log_timeline_entry(entry):
                        self._log_timeline_entry(entry)
                if ts > (self._last_timeline_ts or 0):
                    self._last_timeline_ts = ts
        except Exception as e:
            self.add_log(f"Timeline fetch error: {e}", "warning")

    def _log_timeline_entry(self, entry: TimelineEntry, *, emit_alert: bool = True) -> None:
        sub_label = normalize_sub_label(entry.sub_label)
        sub = f" ({sub_label})" if sub_label else ""
        score_str = f" ({entry.score:.0%})" if entry.score else ""
        attr = f" [{entry.attribute}]" if entry.attribute else ""
        zone = f" in {', '.join(entry.zones)}" if entry.zones else ""
        body = f"{entry.label}{sub}{score_str}{attr}{zone} on {entry.camera}"
        if entry.class_type in ("visible", "active"):
            msg = f"Event: {body}"
            level = "success"
        else:
            msg = f"{entry.class_type.capitalize()}: {body}"
            level = "warning" if entry.class_type == "stationary" else "dim"
        self.add_log(msg, level)
        if entry.class_type in ("visible", "active"):
            if emit_alert:
                self._maybe_emit_timeline_alert(entry)
            self._schedule_timeline_reconcile()

    async def _load_initial_timeline(self) -> None:
        if self.demo or not self._client:
            return
        try:
            timeline_raw = await self._client.get_timeline(limit=15, source="tracked_object")
            if not timeline_raw:
                return
            loaded: list[TimelineEntry] = []
            latest_ts = self._last_timeline_ts or 0
            for t in timeline_raw:
                entry = timeline_entry_from_dict(t)
                if entry is None:
                    continue
                loaded.append(entry)
                if entry.timestamp > latest_ts:
                    latest_ts = entry.timestamp

            if loaded:
                self._last_timeline_ts = latest_ts
                all_interesting = [
                    e
                    for e in sorted(loaded, key=lambda x: x.timestamp, reverse=True)
                    if e.class_type in ("visible", "gone", "stationary", "active")
                    and self._should_log_timeline_entry(e)
                ]
                to_surface = all_interesting[:3]
                for entry in reversed(to_surface):
                    self._log_timeline_entry(entry, emit_alert=False)
                if to_surface:
                    self.add_log(f"Loaded {len(to_surface)} recent timeline items on startup", "info")
        except Exception as e:
            self.add_log(f"Failed to load initial timeline: {e}", "warning")

    # ------------------------------------------------------------------
    # MQTT real-time path (exact same complex handling as original TUI)
    # ------------------------------------------------------------------

    async def _run_mqtt_listener(self) -> None:
        if not self.mqtt_config:
            return

        mqtt_cfg = self.mqtt_config
        host = mqtt_cfg.get("host", "localhost")
        port = int(mqtt_cfg.get("port", 1883))
        username = mqtt_cfg.get("username")
        password = mqtt_cfg.get("password")
        topic_prefix = mqtt_cfg.get("topic_prefix", "frigate")

        self.add_log(f"Connecting to MQTT at {host}:{port}...", "info")

        try:
            self._mqtt_client = FrigateMqttClient(
                host=host,
                port=port,
                username=username,
                password=password,
                topic_prefix=topic_prefix,
            )

            async with self._mqtt_client:
                connected = await self._mqtt_client.wait_until_connected(timeout=8.0)
                self.mqtt_connected = bool(connected)
                if connected:
                    self.add_log(
                        "MQTT connected — events, GenAI object descriptions (tracked_object_update), "
                        "and review summaries (reviews) in real time",
                        "success",
                    )
                else:
                    self.add_log("MQTT connection timeout — falling back to polling?", "warning")

                async for payload in self._mqtt_client.messages():
                    if self.demo:
                        continue
                    try:
                        topic = payload.get("_topic", "")
                        if topic.endswith("/reviews") or topic == f"{topic_prefix}/reviews":
                            self._handle_review_mqtt_update(payload)
                            continue
                        if "tracked_object_update" in topic:
                            self._handle_genai_update(payload)
                            continue

                        fe = frigate_event_from_mqtt_payload(payload)
                        if fe is None:
                            continue

                        event_type = payload.get("type", "new")
                        is_tracked = any(e.id == fe.id for e in self.recent_events)
                        if not is_tracked:
                            current_min_start = min(
                                (e.start_time for e in self.recent_events), default=0
                            )
                            if event_type != "new" and fe.start_time < current_min_start:
                                continue

                        changed = self._merge_events([fe], llm_source="mqtt-event")
                        label = fe.display_label
                        if changed:
                            if event_type == "new" or not is_tracked:
                                self.add_log(f"Event: {label} on {fe.camera}", "success")
                            elif event_type == "end":
                                self.add_log(f"Event ended (MQTT): {label} on {fe.camera}", "info")
                        elif event_type == "end":
                            self.add_log(f"Event ended (MQTT): {label} on {fe.camera}", "info")
                    except Exception as e:
                        self.add_log(f"MQTT event parse/render error: {e}", "warning")
                        continue

        except Exception as e:
            self.mqtt_connected = False
            err = str(e)
            is_dns_error = (
                "Name or service not known" in err
                or "getaddrinfo" in err.lower()
                or getattr(e, "errno", None) in (-2, -3)
            )

            if is_dns_error:
                host = (self.mqtt_config or {}).get("host", "mqtt")
                self.add_log(
                    f"MQTT host '{host}' could not be resolved (DNS error). "
                    "This is common if 'mqtt' is only valid inside the Frigate Docker network. "
                    "Falling back to HTTP polling for events.",
                    "warning",
                )
            else:
                self.add_log(f"MQTT listener error: {err}", "error")

            if not getattr(self, "_event_polling_active", False):
                ev_int = max(2.0, self.poll_interval * 2)
                self._tasks.append(
                    asyncio.create_task(self._interval_loop("events", ev_int, self._refresh_events))
                )
                self._event_polling_active = True
                if not is_dns_error:
                    # For other errors, keep the original short fallback notice
                    self.add_log("MQTT failed — falling back to event polling", "warning")
        finally:
            self.mqtt_connected = False
            asyncio.create_task(self.refresh_genai_health(force=True))

    def _handle_review_mqtt_update(self, payload: dict) -> None:
        """Handle frigate/reviews MQTT when GenAI metadata is attached to a review."""
        try:
            after = payload.get("after") or {}
            if not after:
                return
            rev = review_item_from_dict(after)
            if rev:
                self._log_review_genai_summary(rev)
        except Exception as e:
            self.add_log(f"GenAI review update parse error: {e}", "warning")

    def _handle_genai_update(self, payload: dict) -> None:
        """Handle Frigate tracked_object_update messages for GenAI/LLM activity (e.g. new descriptions)."""
        try:
            if payload.get("type") != "description":
                return
            event_id = payload.get("id") or payload.get("after", {}).get("id", "")
            if not event_id or event_id in self._logged_llm_descriptions:
                return
            camera = payload.get("camera") or payload.get("after", {}).get("camera", "unknown")
            label = payload.get("label") or payload.get("after", {}).get("label", "object")
            desc = payload.get("description") or payload.get("after", {}).get("description", "")
            if desc:
                self._record_genai_activity(
                    kind="object",
                    camera=camera,
                    title=label,
                    label=label,
                    objects=[label] if label else [],
                    text=desc,
                    ref_id=event_id,
                )
                self.add_log(f"LLM [mqtt]: Description for {label} on {camera} — {desc[:180]}", "info")
                self._logged_llm_descriptions.add(event_id)
                # Update any in-memory event so the web UI / TUI can show the description immediately
                for e in self.recent_events:
                    if e.id == event_id:
                        e.description = desc
                        self._notify("events", self.recent_events)
                        break
        except Exception as e:
            self.add_log(f"LLM description update parse error: {e}", "warning")

    # ------------------------------------------------------------------
    # Demo data (identical to original TUI)
    # ------------------------------------------------------------------

    def _demo_stats(self) -> dict[str, Any]:
        return {
            "cameras": {
                "backdeck": {
                    "camera_fps": 5.0,
                    "process_fps": 5.0,
                    "detection_fps": 4.9,
                    "skipped_fps": 0.0,
                    "detection_enabled": True,
                },
                "fronthouse": {
                    "camera_fps": 5.0,
                    "process_fps": 4.8,
                    "detection_fps": 1.8,
                    "skipped_fps": 0.4,
                    "detection_enabled": True,
                },
                "driveway": {
                    "camera_fps": 4.0,
                    "process_fps": 4.0,
                    "detection_fps": 3.9,
                    "skipped_fps": 0.0,
                    "detection_enabled": True,
                },
            },
            "detection_fps": 13.8,
            "skipped_fps": 0.4,
            "gpu_usages": {"NVIDIA GeForce RTX 5060 Ti": {"gpu": "9.0%", "mem": "28.4%"}},
            "service": {"uptime": 48231},
        }

    def _seed_demo_events(self) -> None:
        """Populate a few fake events when running with --demo.

        This makes the Events tab immediately useful for UI testing (time column,
        labels, snapshots icons, modal, etc.) without needing a real Frigate.
        The start_times are chosen so that toLocaleTimeString() in most locales
        will show a mix that exercises AM/PM formatting in 12h regions.
        """
        if not self.demo or self.recent_events:
            return

        import time

        now = time.time()
        # Recent events with varied times (will format according to browser/OS locale)
        samples = [
            FrigateEvent(
                id="demo-evt-1",
                camera="driveway",
                label="person",
                start_time=now - 420,   # ~7 min ago
                end_time=now - 412,
                top_score=0.94,
                has_snapshot=True,
                has_clip=True,
                zones=["front_yard"],
                sub_label=None,
                average_estimated_speed=2.3,
                description="A person in a dark jacket is walking up the driveway carrying what appears to be a bag.",
            ),
            FrigateEvent(
                id="demo-evt-2",
                camera="backdeck",
                label="car",
                start_time=now - 95,
                end_time=now - 88,
                top_score=0.87,
                has_snapshot=True,
                has_clip=False,
                zones=["driveway", "side"],
                sub_label="suv",
                average_estimated_speed=15.0,
            ),
            FrigateEvent(
                id="demo-evt-3",
                camera="fronthouse",
                label="dog",
                start_time=now - 15,
                end_time=now - 5,
                top_score=0.71,
                has_snapshot=False,
                has_clip=False,
                zones=["porch"],
                sub_label=None,
                average_estimated_speed=None,
            ),
        ]
        self.recent_events = sorted(samples, key=lambda x: x.start_time, reverse=True)
        self._notify("events", self.recent_events)
        self.add_log("Seeded 3 demo events (for Events tab testing)", "info")
        for e in self.recent_events:
            self._log_llm_description(e, demo=True)
