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
from frigate_tui.api import FrigateClient
from frigate_tui.models import (
    CameraStats,
    FrigateEvent,
    ReviewItem,
    SystemHealth,
    TimelineEntry,
    compute_health,
    parse_cameras,
    frigate_event_from_dict,
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
        self.poll_interval: float = float(s.get("poll_interval", 1.0))
        self.stats_log_interval: float = float(s.get("stats_log_interval", 600.0))
        self.max_events: int = int(s.get("max_events", 150))
        self.demo: bool = bool(s.get("demo", False))

        # MQTT (optional)
        self.mqtt_config: dict[str, Any] | None = s.get("mqtt")
        self._use_mqtt_for_events: bool = bool(self.mqtt_config) and MQTT_AVAILABLE

        # Clients / tasks
        self._client: FrigateClient | None = None
        self._frigate_version: str = "?"
        self._mqtt_client: FrigateMqttClient | None = None
        self._mqtt_task: asyncio.Task | None = None
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
          - "events" -> list[FrigateEvent]  (authoritative current list)
          - "log" -> {"ts": str, "level": str, "message": str}
          - "connection" -> {"ok": bool, "latency_ms": int|None, "last_error": str|None, "url": str}
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

    def get_snapshot(self) -> dict[str, Any]:
        """Return a JSON-serializable snapshot suitable for web initial state or debug."""
        # Convert dataclasses to plain dicts for transport
        def _cam(c: CameraStats) -> dict[str, Any]:
            d = asdict(c)
            d["health_color"] = c.health_color
            d["health_summary"] = c.health_summary
            return d

        def _evt(e: FrigateEvent) -> dict[str, Any]:
            d = asdict(e)
            d["color"] = e.color
            d["display_label"] = e.display_label
            d["duration_s"] = e.duration_s
            return d

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
            "health_history": self.health_history[-300:],  # ~5 min for charts
            "cameras_history": self.cameras_history[-300:],
        }

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
        if not self.demo:
            await self._load_initial_events()
            await self._load_initial_timeline()

        # Always poll stats
        self._tasks.append(
            asyncio.create_task(self._interval_loop("stats", self.poll_interval, self._refresh_stats))
        )

        # Events: MQTT preferred, else polling (exact same decision tree)
        if self.mqtt_config and not MQTT_AVAILABLE:
            self.add_log("MQTT configured but aiomqtt not installed — falling back to polling", "warning")

        if self._use_mqtt_for_events and self.mqtt_config:
            self._mqtt_task = asyncio.create_task(self._run_mqtt_listener())
            self._tasks.append(self._mqtt_task)
            self.add_log("MQTT event subscription enabled", "info")
        else:
            ev_interval = max(2.0, self.poll_interval * 2)
            self._tasks.append(
                asyncio.create_task(self._interval_loop("events", ev_interval, self._refresh_events))
            )
            self._event_polling_active = True
            self.add_log("Event polling started (MQTT not configured). Stats logged every ~10s.", "info")

        # Higher-level signals (only used for activity log, same as TUI)
        self._tasks.append(
            asyncio.create_task(self._interval_loop("reviews", 8.0, self._refresh_reviews))
        )
        self._tasks.append(
            asyncio.create_task(self._interval_loop("timeline", 5.0, self._refresh_timeline))
        )

        # Prime stats immediately
        await self._refresh_stats()

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

    async def _interval_loop(
        self, name: str, interval: float, func: Callable[[], Awaitable[None]]
    ) -> None:
        """Generic interval driver (replacement for Textual set_interval)."""
        while self._running:
            try:
                await func()
            except asyncio.CancelledError:
                break
            except Exception as e:
                # Never let a single interval kill the core
                self.add_log(f"{name} interval error: {e}", "warning")
            await asyncio.sleep(interval)

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
        ts = datetime.now().astimezone().strftime("%H:%M:%S")
        entry = {"ts": ts, "level": level, "message": message}
        self.log_entries.append(entry)
        if len(self.log_entries) > self._max_log_entries:
            self.log_entries = self.log_entries[-self._max_log_entries :]
        self._notify("log", entry)

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
                        f"Parsed {len(self.cameras)} cameras from stats: {[c.name for c in self.cameras]}",
                        "info",
                    )
                else:
                    self.add_log("Stats received but no cameras found in response", "warning")
                self._cameras_logged = True

        # Notify consumers (TUI re-renders, web pushes via SSE)
        self._notify("stats", stats)
        self._notify("cameras", self.cameras)
        self._notify("health", self.health)
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

    async def _refresh_events(self) -> None:
        if getattr(self, "_use_mqtt_for_events", False) and not getattr(self, "_event_polling_active", False):
            return
        if self.demo:
            return
        try:
            assert self._client is not None
            events_raw = await self._client.get_events(limit=25, after=self._last_event_ts)
            if not events_raw:
                return

            new_events: list[FrigateEvent] = []
            for e in events_raw:
                fe = frigate_event_from_dict(e)
                if fe is None:
                    continue
                new_events.append(fe)
                if fe.start_time > (self._last_event_ts or 0):
                    self._last_event_ts = fe.start_time

            if new_events:
                combined = new_events + self.recent_events
                self.recent_events = sorted(
                    {e.id: e for e in combined}.values(), key=lambda x: x.start_time, reverse=True
                )[: self.max_events]

                self._notify("events", self.recent_events)

                for ev in new_events[:3]:
                    self.add_log(f"New event: {ev.display_label} on {ev.camera}", "success")
                if len(new_events) > 3:
                    self.add_log(f"+{len(new_events)-3} more events", "info")
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
                start = rev.start_time
                if self._last_review_ts and start <= self._last_review_ts:
                    continue
                if rev.severity == "alert" or rev.sub_labels:
                    msg = f"Review: {rev.severity.upper()} on {rev.camera}"
                    if rev.sub_labels:
                        msg += f" — {', '.join(rev.sub_labels)}"
                    elif rev.objects:
                        msg += f" — {', '.join(rev.objects)}"
                    self.add_log(msg, "warning" if rev.severity == "alert" else "info")
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
                    if entry.sub_label or (entry.score or 0) > 0.85:
                        self._log_timeline_entry(entry)
                if ts > (self._last_timeline_ts or 0):
                    self._last_timeline_ts = ts
        except Exception as e:
            self.add_log(f"Timeline fetch error: {e}", "warning")

    def _log_timeline_entry(self, entry: TimelineEntry) -> None:
        sub = f" ({entry.sub_label})" if entry.sub_label else ""
        score_str = f" ({entry.score:.0%})" if entry.score else ""
        attr = f" [{entry.attribute}]" if entry.attribute else ""
        zone = f" in {', '.join(entry.zones)}" if entry.zones else ""
        msg = f"{entry.class_type.capitalize()}: {entry.label}{sub}{score_str}{attr}{zone} on {entry.camera}"
        level = "info" if entry.class_type == "visible" else ("warning" if entry.class_type == "stationary" else "dim")
        self.add_log(msg, level)

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
                    if e.class_type in ("visible", "gone", "stationary")
                    and (e.sub_label or (e.score or 0) > 0.8)
                ]
                to_surface = all_interesting[:3]
                for entry in reversed(to_surface):
                    self._log_timeline_entry(entry)
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
                if connected:
                    self.add_log("MQTT connected — receiving events in real time", "success")
                else:
                    self.add_log("MQTT connection timeout — falling back to polling?", "warning")

                async for event in self._mqtt_client.events():
                    if self.demo:
                        continue
                    try:
                        # Reconstruct using the same fallbacks as original
                        raw = event.raw or {}
                        after = raw.get("after") or {}
                        before = raw.get("before") or {}
                        data = raw.get("data") or after.get("data") or before.get("data") or {}

                        # Prefer the already-normalized FrigateMqttEvent fields, fall back to raw
                        fe = FrigateEvent(
                            id=event.id or after.get("id") or before.get("id") or raw.get("id", ""),
                            camera=event.camera,
                            label=event.label,
                            start_time=event.start_time,
                            end_time=event.end_time,
                            top_score=event.top_score,
                            has_snapshot=event.has_snapshot,
                            has_clip=event.has_clip,
                            zones=event.zones or [],
                            sub_label=raw.get("sub_label") or after.get("sub_label") or before.get("sub_label"),
                            average_estimated_speed=data.get("average_estimated_speed"),
                            velocity_angle=data.get("velocity_angle"),
                            attributes=data.get("attributes") or [],
                        )

                        if fe.start_time > (self._last_event_ts or 0):
                            self._last_event_ts = fe.start_time

                        # Exact late-arrival guard from original
                        is_tracked = any(e.id == fe.id for e in self.recent_events)
                        if not is_tracked:
                            current_min_start = min(
                                (e.start_time for e in self.recent_events), default=0
                            )
                            if event.type != "new" and fe.start_time < current_min_start:
                                continue

                        # Exact insert/replace logic
                        if event.type == "new" or not is_tracked:
                            self.recent_events = [fe] + [
                                e for e in self.recent_events if e.id != fe.id
                            ][: self.max_events - 1]
                        else:
                            for i, e in enumerate(self.recent_events):
                                if e.id == fe.id:
                                    self.recent_events[i] = fe
                                    break
                            else:
                                self.recent_events = [fe] + self.recent_events[: self.max_events - 1]

                        # Canonical sort + cap (defensive, same as TUI)
                        self.recent_events = sorted(
                            {e.id: e for e in self.recent_events}.values(),
                            key=lambda x: x.start_time,
                            reverse=True,
                        )[: self.max_events]

                        self._notify("events", self.recent_events)

                        label = fe.display_label
                        if event.type == "new" or not is_tracked:
                            self.add_log(f"New event (MQTT): {label} on {fe.camera}", "success")
                        elif event.type == "end":
                            self.add_log(f"Event ended (MQTT): {label} on {fe.camera}", "info")
                    except Exception as e:
                        self.add_log(f"MQTT event parse/render error: {e}", "warning")
                        continue

        except Exception as e:
            self.add_log(f"MQTT listener error: {e}", "error")
            if not getattr(self, "_event_polling_active", False):
                ev_int = max(2.0, self.poll_interval * 2)
                self._tasks.append(
                    asyncio.create_task(self._interval_loop("events", ev_int, self._refresh_events))
                )
                self._event_polling_active = True
                self.add_log("MQTT failed — falling back to event polling", "warning")

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
