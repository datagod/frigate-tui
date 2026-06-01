"""Main Textual application for the advanced Frigate TUI."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.reactive import reactive
from textual.widgets import DataTable, Footer, Header, Label, RichLog, Static, TabbedContent, TabPane

from frigate_tui import __version__
from frigate_tui.api import FrigateClient
from frigate_tui.models import (
    CameraStats,
    FrigateEvent,
    ReviewItem,
    SystemHealth,
    compute_health,
    label_color,
    parse_cameras,
)
try:
    from frigate_tui.mqtt import FrigateMqttClient
    MQTT_AVAILABLE = True
except ImportError:
    FrigateMqttClient = None  # type: ignore
    MQTT_AVAILABLE = False


# ------------------------------------------------------------------
# Widgets
# ------------------------------------------------------------------

class ConnectionStatus(Static):
    """Bottom status bar showing connection health + target URL + latency/error."""

    def update_status(
        self,
        url: str,
        ok: bool,
        latency_ms: int | None = None,
        error: str | None = None,
    ) -> None:
        txt = Text()
        txt.append("Target: ", style="dim")
        txt.append(url, style="cyan")
        txt.append("   ")

        if ok:
            txt.append("● ", style="bold green")
            txt.append("CONNECTED", style="green")
            if latency_ms is not None:
                txt.append(f"  {latency_ms}ms", style="dim")
        else:
            txt.append("● ", style="bold red")
            txt.append("OFFLINE — retrying", style="red")
            if error:
                txt.append(f"   {error}", style="dim red")

        self.update(txt)


class FPSBar(Static):
    """Colorful unicode bar + numeric value for any FPS-style metric."""

    def update_value(self, label: str, value: float, max_val: float = 10.0, width: int = 15, color: str | None = None) -> None:
        pct = min(1.0, max(0.0, value / max_val)) if max_val > 0 else 0
        filled = int(pct * width)
        bar = "█" * filled + "░" * (width - filled)

        if color is None:
            if value < 0.3:
                color = "red"
            elif pct < 0.55:
                color = "yellow"
            else:
                color = "green"

        txt = Text()
        txt.append(f"{label:<12}", style="dim")
        txt.append(bar + " ", style=color)
        txt.append(f"{value:5.1f}", style=f"bold {color}")
        self.update(txt)


class MetricCard(Static):
    """Big number + small label card."""

    def update_metric(self, label: str, value: str, color: str = "white") -> None:
        txt = Text()
        txt.append(f"{label}\n", style="dim")
        txt.append(value, style=f"bold {color} 150%")
        self.update(txt)


# ------------------------------------------------------------------
# Main App
# ------------------------------------------------------------------

class FrigateMonitor(App[None]):
    """Advanced colorful Frigate TUI — queues, cameras, events, health."""

    TITLE = "Frigate TUI"
    SUB_TITLE = f"v{__version__}"

    BINDINGS = [
        Binding("q", "quit", "Quit", show=True),
        Binding("r", "refresh", "Refresh", show=True),
        Binding("?", "help", "Help", show=True),
        Binding("c", "clear_log", "Clear Log", show=True),
        Binding("1", "switch_tab('overview')", "Overview", show=False),
        Binding("2", "switch_tab('cameras')", "Cameras", show=False),
        Binding("3", "switch_tab('events')", "Events", show=False),
        Binding("4", "switch_tab('health')", "Health", show=False),
    ]

    CSS = """
    Screen { background: #0f1117; color: #c0c5d1; }

    TabbedContent { height: 1fr; }

    #summary {
        height: 4;
        background: #161b26;
        border: tall #4a5263;
        padding: 0 2;
        margin-bottom: 1;
    }

    .summary-row > * {
        width: 1fr;
        margin-right: 2;
    }

    .metric-label { color: #6b7280; text-style: bold; }
    .metric-value { text-style: bold; }

    .good { color: #22c55e; }
    .warn { color: #eab308; }
    .bad  { color: #ef4444; }

    .camera-row {
        padding: 0 1;
        margin: 0 0 1 0;
        background: #161b26;
        border: tall #4a5263;
    }

    .camera-card {
        padding: 0 1;
        margin-bottom: 1;
        background: #161b26;
        border: tall #4a5263;
    }

    .camera-header {
        margin-bottom: 0;
    }

    .camera-name {
        text-style: bold;
    }

    .health-summary {
        text-style: bold;
    }

    .status {
        text-style: bold;
    }

    .legend {
        margin-bottom: 1;
        color: #6b7280;
    }

    DataTable {
        background: #0f1117;
    }

    .event-person { color: #ff6b6b; }
    .event-car    { color: #4ecdc4; }
    .event-dog    { color: #f7b731; }
    .event-cat    { color: #a55eea; }

    ConnectionStatus { dock: bottom; background: #1a1f2e; height: 1; padding: 0 1; }

    /* Right-hand activity log panel */
    #main-split {
        height: 1fr;
    }

    #left-panel {
        width: 58%;
        min-width: 70;
    }

    #log-panel {
        width: 42%;
        min-width: 50;
        border-left: tall #4a5263;
        background: #0c0e14;
    }

    #log-title {
        dock: top;
        text-style: bold;
        background: #1a1f2e;
        padding: 0 1;
        color: #a5b4fc;
    }

    #activity-log {
        height: 1fr;
        background: #0c0e14;
        padding: 0 1;
    }
    """

    # Reactive state driving the whole UI
    last_stats: reactive[dict[str, Any] | None] = reactive(None)
    recent_events: reactive[list[FrigateEvent]] = reactive([])
    health: reactive[SystemHealth | None] = reactive(None)
    cameras: reactive[list[CameraStats]] = reactive([])
    connection_ok: reactive[bool] = reactive(True)
    latency_ms: reactive[int | None] = reactive(None)
    last_error: reactive[str | None] = reactive(None)

    def __init__(self, settings: dict[str, Any] | None = None) -> None:
        super().__init__()
        s = settings or {}
        self.frigate_url: str = s.get("frigate_url", "http://localhost:5000")
        self.poll_interval: float = float(s.get("poll_interval", 1.0))
        self.stats_log_interval: float = float(s.get("stats_log_interval", 10.0))
        self.max_events: int = int(s.get("max_events", 150))
        self.demo = bool(s.get("demo", False))

        # MQTT configuration (optional)
        self.mqtt_config: dict[str, Any] | None = s.get("mqtt")
        self._use_mqtt_for_events = bool(self.mqtt_config) and MQTT_AVAILABLE

        self._client: FrigateClient | None = None
        self._frigate_version: str = "?"
        self._last_event_ts: float | None = None
        self._last_review_ts: float | None = None
        self._logged_connected: bool = False
        self._mqtt_client: FrigateMqttClient | None = None
        self._mqtt_task: asyncio.Task | None = None

        # Stats logging throttling
        self._last_stats_log_time: float = 0.0
        self._stats_had_error: bool = False

        # Diagnostic logging - only log camera parsing once
        self._cameras_logged: bool = False

        # Track camera cards for efficient updates (no flicker on refresh)
        self._camera_cards: dict[str, Vertical] = {}

    def _format_connection_error(self, e: Exception) -> str:
        """Turn raw socket/DNS errors into clearer messages for the Activity Log."""
        msg = str(e)
        if "errno -2" in msg or "Name or service not known" in msg:
            return f"DNS failure for '{self.frigate_url}' (errno -2). Wrong Docker network or hostname?"
        if "Connection refused" in msg:
            return "Connection refused — is Frigate running and listening on the port?"
        if "timeout" in msg.lower():
            return "Connection timed out"
        return msg[:70]

    def add_log(self, message: str, level: str = "info") -> None:
        """Append a timestamped message to the right-hand activity log."""
        try:
            log = self.query_one("#activity-log", RichLog)
            ts = datetime.now().astimezone().strftime("%H:%M:%S")

            if level == "error":
                style = "bold red"
                prefix = "✗"
            elif level == "success":
                style = "green"
                prefix = "✓"
            elif level == "warning":
                style = "yellow"
                prefix = "!"
            else:
                style = "dim"
                prefix = "•"

            log.write(f"[dim]{ts}[/] [{style}]{prefix}[/] {message}")
        except Exception:
            # Log widget not mounted yet or during shutdown
            pass

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)

        # Global summary strip (always visible)
        with Container(id="summary"):
            with Horizontal(classes="summary-row"):
                yield Label("FRIGATE", classes="metric-label")
                yield Label("—", id="frigate-version", classes="metric-value good")

                yield Label("DETECTION", classes="metric-label")
                yield Label("—", id="detection-fps", classes="metric-value")

                yield Label("GPU", classes="metric-label")
                yield Label("—", id="gpu-usage", classes="metric-value")

                yield Label("SKIPPED", classes="metric-label")
                yield Label("—", id="skipped-fps", classes="metric-value")

        # Main horizontal split: left = tabs/views, right = live activity log
        with Horizontal(id="main-split"):
            # Left side - the existing dashboard views
            with Vertical(id="left-panel"):
                with TabbedContent(initial="overview"):
                    with TabPane("Overview", id="overview"):
                        yield Static(
                            "Welcome to Frigate TUI.\n\n"
                            "The **Activity Log** on the right shows a live trace of what the TUI is doing.\n\n"
                            "Useful keys:\n"
                            "  r          Force refresh\n"
                            "  ?          Help\n"
                            "  2 / 3 / 4  Switch tabs\n"
                            "  c          Clear log\n\n"
                            "This is the best place to see connection attempts, errors, and polling results.",
                            id="overview-text",
                        )

                    with TabPane("Cameras", id="cameras"):
                        yield VerticalScroll(id="cameras-scroll")

                    with TabPane("Events", id="events"):
                        yield DataTable(id="events-table", zebra_stripes=True, show_cursor=True)

                    with TabPane("Health / Queues", id="health"):
                        yield Vertical(id="health-pane")

            # Right side - persistent running log
            with Vertical(id="log-panel"):
                yield Label("Activity Log", id="log-title")
                yield RichLog(id="activity-log", highlight=True, markup=True, auto_scroll=True)

        yield ConnectionStatus(id="conn-status")
        yield Footer()

    async def on_mount(self) -> None:
        self._client = FrigateClient(self.frigate_url)

        self.add_log(f"Starting Frigate TUI v{__version__}", "info")
        self.add_log(f"Target: {self.frigate_url}", "info")

        # Show the target URL immediately in the status bar
        self._update_connection()

        # One-time version fetch (important diagnostic)
        try:
            self._frigate_version = await self._client.get_version()
            self.add_log(f"Connected to Frigate {self._frigate_version}", "success")
        except Exception as e:
            self._frigate_version = "unknown"
            self.add_log(f"Failed to reach {self.frigate_url}/api/version: {self._format_connection_error(e)}", "error")

        self._update_version_label()

        # Seed events table columns with keys so we can update cells later
        table = self.query_one("#events-table", DataTable)
        table.add_columns(
            ("time", "Time"),
            ("camera", "Camera"),
            ("label", "Label / Person"),
            ("speed", "Speed"),
            ("score", "Score"),
            ("dur", "Dur"),
            ("clip", "Clip"),
            ("snap", "Snap"),
        )

        # Always poll stats (lightweight and useful)
        self.set_interval(self.poll_interval, self._refresh_stats)

        # Events: prefer MQTT subscription if configured, otherwise fall back to polling
        if self.mqtt_config and not MQTT_AVAILABLE:
            self.add_log("MQTT configured but aiomqtt not installed — falling back to polling", "warning")

        # Always do an initial load of recent historical events on startup
        # This ensures the Events tab is not empty when the TUI first launches.
        if not self.demo:
            await self._load_initial_events()

        if self._use_mqtt_for_events and self.mqtt_config:
            self._mqtt_task = asyncio.create_task(self._run_mqtt_listener())
            self.add_log("MQTT event subscription enabled", "info")
        else:
            self.set_interval(max(2.0, self.poll_interval * 2), self._refresh_events)
            self.add_log("Event polling started (MQTT not configured). Stats logged every ~10s.", "info")

        # Fetch recent Review items (higher signal than raw events, especially with sub_labels)
        self.set_interval(8.0, self._refresh_reviews)

        # Prime the pump for stats
        await self._refresh_stats()

    def _update_version_label(self) -> None:
        try:
            self.query_one("#frigate-version", Label).update(self._frigate_version)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Data refresh (stats + events)
    # ------------------------------------------------------------------

    async def _refresh_stats(self) -> None:
        start = asyncio.get_event_loop().time()

        try:
            if self.demo:
                stats = self._demo_stats()
            else:
                assert self._client
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

            if not self._cameras_logged:
                if self.cameras:
                    self.add_log(f"Parsed {len(self.cameras)} cameras from stats: {[c.name for c in self.cameras]}", "info")
                else:
                    self.add_log("Stats received but no cameras found in response", "warning")
                self._cameras_logged = True

        self._render_summary(stats)
        self._render_health()
        self._update_connection()

        # Try to render Cameras and Health content as soon as data is available.
        # This helps populate the tabs even before the user switches to them
        # (combined with the tab activation handler).
        self.call_after_refresh(self._render_cameras)
        self.call_after_refresh(self._render_health)



        # Log to the right-hand activity window
        # Always log errors immediately.
        # Only log successful stats periodically (every 10s) or on recovery from error.
        now = asyncio.get_event_loop().time()

        if ok:
            det = stats.get("detection_fps", 0) if stats else 0
            should_log = (
                now - self._last_stats_log_time > self.stats_log_interval or  # throttle successful logs
                self._stats_had_error
            )
            if should_log:
                self.add_log(f"Stats OK ({latency}ms) — detection {det:.1f} fps", "success")
                self._last_stats_log_time = now
                self._stats_had_error = False
        else:
            self.add_log(f"Stats poll failed: {err}", "error")
            self._stats_had_error = True
            self._last_stats_log_time = now  # ensure next success is logged promptly

    async def _refresh_events(self) -> None:
        if self._use_mqtt_for_events:
            # We're getting events via MQTT instead
            return

        if self.demo:
            # In demo we just keep the last synthetic events if any
            return
        try:
            assert self._client
            events_raw = await self._client.get_events(limit=25, after=self._last_event_ts)
            if not events_raw:
                return

            new_events: list[FrigateEvent] = []
            for e in events_raw:
                try:
                    data = e.get("data", {}) or {}
                    fe = FrigateEvent(
                        id=str(e.get("id", "")),
                        camera=str(e.get("camera", "unknown")),
                        label=str(e.get("label", "object")),
                        start_time=float(e.get("start_time", 0)),
                        end_time=e.get("end_time"),
                        top_score=e.get("top_score"),
                        has_snapshot=bool(e.get("has_snapshot")),
                        has_clip=bool(e.get("has_clip")),
                        zones=e.get("zones") or [],
                        sub_label=e.get("sub_label"),
                        average_estimated_speed=data.get("average_estimated_speed"),
                        velocity_angle=data.get("velocity_angle"),
                        attributes=data.get("attributes") or [],
                    )
                    new_events.append(fe)
                    if fe.start_time > (self._last_event_ts or 0):
                        self._last_event_ts = fe.start_time
                except Exception:
                    continue

            if new_events:
                # Prepend newest, keep only the most recent N
                combined = new_events + self.recent_events
                self.recent_events = combined[: self.max_events]
                self._render_events_table(new_events)  # only append the new ones for efficiency

                for ev in new_events[:3]:  # log up to 3 new ones
                    label = ev.display_label
                    self.add_log(f"New event: {label} on {ev.camera}", "success")
                if len(new_events) > 3:
                    self.add_log(f"+{len(new_events)-3} more events", "info")
        except Exception as e:
            self.add_log(f"Events poll error: {e}", "warning")

    async def _load_initial_events(self) -> None:
        """Load a batch of recent historical events on startup so the Events tab isn't empty."""
        if self.demo or not self._client:
            return
        try:
            # Load a larger initial batch for good history on startup
            events_raw = await self._client.get_events(limit=80)
            if not events_raw:
                self.add_log("No historical events found on startup", "info")
                return

            loaded_events: list[FrigateEvent] = []
            latest_ts = self._last_event_ts or 0

            for e in events_raw:
                try:
                    data = e.get("data", {}) or {}
                    fe = FrigateEvent(
                        id=str(e.get("id", "")),
                        camera=str(e.get("camera", "unknown")),
                        label=str(e.get("label", "object")),
                        start_time=float(e.get("start_time", 0)),
                        end_time=e.get("end_time"),
                        top_score=e.get("top_score"),
                        has_snapshot=bool(e.get("has_snapshot")),
                        has_clip=bool(e.get("has_clip")),
                        zones=e.get("zones") or [],
                        sub_label=e.get("sub_label"),
                        average_estimated_speed=data.get("average_estimated_speed"),
                        velocity_angle=data.get("velocity_angle"),
                        attributes=data.get("attributes") or [],
                    )
                    loaded_events.append(fe)
                    if fe.start_time > latest_ts:
                        latest_ts = fe.start_time
                except Exception:
                    continue

            if loaded_events:
                self.recent_events = sorted(loaded_events, key=lambda x: x.start_time, reverse=True)[:self.max_events]
                self._last_event_ts = latest_ts
                self._render_events_table(self.recent_events)
                self.add_log(f"Loaded {len(self.recent_events)} historical events on startup", "info")

        except Exception as e:
            self.add_log(f"Failed to load initial historical events: {e}", "warning")

    async def _refresh_reviews(self) -> None:
        """Fetch recent review items and surface important ones (especially with sub_labels)."""
        if self.demo or not self._client:
            return
        try:
            reviews_raw = await self._client.get_review_items(limit=15, has_been_reviewed=False)
            if not reviews_raw:
                return

            new_important = []
            for r in reviews_raw:
                try:
                    start = float(r.get("start_time", 0))
                    if self._last_review_ts and start <= self._last_review_ts:
                        continue

                    data = r.get("data", {}) or {}
                    review = ReviewItem(
                        id=str(r.get("id", "")),
                        camera=str(r.get("camera", "unknown")),
                        start_time=start,
                        end_time=r.get("end_time"),
                        severity=r.get("severity", "detection"),
                        has_been_reviewed=bool(r.get("has_been_reviewed")),
                        objects=data.get("objects") or [],
                        sub_labels=data.get("sub_labels") or [],
                        zones=data.get("zones") or [],
                    )

                    # Only surface high-value reviews (alerts with people or sub_labels)
                    if review.severity == "alert" or review.sub_labels:
                        new_important.append(review)

                    if start > (self._last_review_ts or 0):
                        self._last_review_ts = start
                except Exception:
                    continue

            for rev in new_important[:5]:
                msg = f"Review: {rev.severity.upper()} on {rev.camera}"
                if rev.sub_labels:
                    msg += f" — {', '.join(rev.sub_labels)}"
                elif rev.objects:
                    msg += f" — {', '.join(rev.objects)}"
                self.add_log(msg, "warning" if rev.severity == "alert" else "info")

        except Exception as e:
            self.add_log(f"Review fetch error: {e}", "warning")

    async def _run_mqtt_listener(self) -> None:
        """Background task that subscribes to Frigate events via MQTT."""
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

                    # Convert MQTT event to our internal model
                    raw = event.raw or {}
                    data = raw.get("data") or raw.get("after", {}).get("data") or {}
                    fe = FrigateEvent(
                        id=raw.get("id", ""),
                        camera=event.camera,
                        label=event.label,
                        start_time=event.start_time,
                        end_time=event.end_time,
                        top_score=event.top_score,
                        has_snapshot=event.has_snapshot,
                        has_clip=event.has_clip,
                        zones=event.zones or [],
                        sub_label=raw.get("sub_label") or event.raw.get("after", {}).get("sub_label") if raw else None,
                        average_estimated_speed=data.get("average_estimated_speed"),
                        velocity_angle=data.get("velocity_angle"),
                        attributes=data.get("attributes") or [],
                    )

                    # Add to recent events (avoid massive duplicates)
                    self.recent_events = [fe] + [e for e in self.recent_events if e.id != fe.id][: self.max_events - 1]
                    self._render_events_table([fe])

                    label = fe.display_label
                    self.add_log(f"New event (MQTT): {label} on {fe.camera}", "success")

        except Exception as e:
            self.add_log(f"MQTT listener error: {e}", "error")
            # Optionally fall back to polling here in the future

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    def _demo_stats(self) -> dict[str, Any]:
        return {
            "cameras": {
                "backdeck": {"camera_fps": 5.0, "process_fps": 5.0, "detection_fps": 4.9, "skipped_fps": 0.0, "detection_enabled": True},
                "fronthouse": {"camera_fps": 5.0, "process_fps": 4.8, "detection_fps": 1.8, "skipped_fps": 0.4, "detection_enabled": True},
                "driveway": {"camera_fps": 4.0, "process_fps": 4.0, "detection_fps": 3.9, "skipped_fps": 0.0, "detection_enabled": True},
            },
            "detection_fps": 13.8,
            "skipped_fps": 0.4,
            "gpu_usages": {"NVIDIA GeForce RTX 5060 Ti": {"gpu": "9.0%", "mem": "28.4%"}},
            "service": {"uptime": 48231},
        }

    def _render_summary(self, stats: dict[str, Any] | None) -> None:
        if not stats:
            return
        try:
            det = float(stats.get("detection_fps", 0))
            sk = float(stats.get("skipped_fps", 0))

            det_lbl = self.query_one("#detection-fps", Label)
            det_lbl.update(f"{det:.1f}")
            det_lbl.classes = f"metric-value {'good' if det > 8 else ('warn' if det > 2 else 'bad')}"

            sk_lbl = self.query_one("#skipped-fps", Label)
            sk_lbl.update(f"{sk:.1f}")
            sk_lbl.classes = f"metric-value {'bad' if sk > 0.05 else 'good'}"

            gpus = stats.get("gpu_usages") or {}
            if gpus:
                gpu_val = next(iter(gpus.values())).get("gpu", "—")
                self.query_one("#gpu-usage", Label).update(gpu_val)
        except Exception:
            pass

    def _render_cameras(self) -> None:
        if not self.cameras:
            return
        try:
            scroll = self.query_one("#cameras-scroll", VerticalScroll)

            # Mount legend only once
            if not any(getattr(c, "id", None) == "cameras-legend" for c in scroll.children):
                legend = Static(
                    "[dim]Legend:[/]  "
                    "Camera Input = raw frames from camera   |   "
                    "Processing = how fast Frigate ingests them   |   "
                    "Detection = frames actually analyzed by AI\n"
                    "Colors: [green]Green=Healthy[/]  [yellow]Yellow=Degraded[/]  [red]Red=Overloaded[/]  [dim]Gray=Disabled[/]",
                    classes="legend",
                    id="cameras-legend"
                )
                scroll.mount(legend, before=0)

            current_names = set()

            for cam in self.cameras:
                current_names.add(cam.name)

                if cam.name in self._camera_cards:
                    # Update existing card in place (no flicker)
                    card = self._camera_cards[cam.name]

                    # Update header children
                    header = card.children[0]  # first child is the header
                    if isinstance(header, Horizontal) and len(header.children) >= 3:
                        header.children[0].update(f"[bold]{cam.name}[/]")
                        header.children[1].update(f"  [{cam.health_color}]{cam.health_summary}[/]")
                        status = "ON" if cam.detection_enabled else "OFF"
                        header.children[2].update(f"   [{cam.health_color}]{status}[/]")

                    # Update the three bars (they are children 1,2,3)
                    if len(card.children) >= 4:
                        card.children[1].update_value("Camera Input", cam.camera_fps, 15, width=18)
                        card.children[2].update_value("Processing", cam.process_fps, 15, width=18)
                        card.children[3].update_value("Detection", cam.detection_fps, 15, width=18, color=cam.health_color)
                else:
                    # Create new card
                    name_label = Label(f"[bold]{cam.name}[/]", classes="camera-name")
                    health_label = Label(f"  [{cam.health_color}]{cam.health_summary}[/]", classes="health-summary")
                    status = "ON" if cam.detection_enabled else "OFF"
                    status_label = Label(f"   [{cam.health_color}]{status}[/]", classes="status")
                    header = Horizontal(name_label, health_label, status_label, classes="camera-header")

                    cam_bar = FPSBar()
                    cam_bar.update_value("Camera Input", cam.camera_fps, 15, width=18)

                    proc_bar = FPSBar()
                    proc_bar.update_value("Processing", cam.process_fps, 15, width=18)

                    det_bar = FPSBar()
                    det_bar.update_value("Detection", cam.detection_fps, 15, width=18, color=cam.health_color)

                    card = Vertical(header, cam_bar, proc_bar, det_bar, classes="camera-card")
                    scroll.mount(card)
                    self._camera_cards[cam.name] = card

            # Remove cards for cameras that no longer exist
            to_remove = [name for name in self._camera_cards if name not in current_names]
            for name in to_remove:
                card = self._camera_cards.pop(name)
                card.remove()

        except Exception as e:
            self.add_log(f"Failed to render cameras tab: {type(e).__name__}: {e}", "error")

    def _render_events_table(self, new_only: list[FrigateEvent] | None = None) -> None:
        try:
            table = self.query_one("#events-table", DataTable)
            to_process = new_only or self.recent_events

            for ev in to_process:
                ts = datetime.fromtimestamp(ev.start_time, tz=timezone.utc).astimezone().strftime("%H:%M:%S")
                dur = f"{ev.duration_s:.1f}s" if ev.duration_s else "—"
                clip = "📼" if ev.has_clip else ""
                snap = "📷" if ev.has_snapshot else ""

                display_label = ev.display_label
                label_cell = Text(display_label, style=label_color(ev.label))

                speed_str = f"{ev.average_estimated_speed:.1f}" if ev.average_estimated_speed else "—"

                if ev.id in table.rows:
                    # Update existing row with latest data (e.g. sub_label arriving later via MQTT)
                    row_key = ev.id
                    table.update_cell(row_key, "label", label_cell)
                    table.update_cell(row_key, "speed", speed_str)
                    table.update_cell(row_key, "score", f"{ev.top_score:.2f}" if ev.top_score else "—")
                    table.update_cell(row_key, "dur", dur)
                    table.update_cell(row_key, "clip", clip)
                    table.update_cell(row_key, "snap", snap)
                else:
                    # Add new event row at the top so newest are visible immediately
                    table.add_row(
                        ts,
                        ev.camera,
                        label_cell,
                        speed_str,
                        f"{ev.top_score:.2f}" if ev.top_score else "—",
                        dur,
                        clip,
                        snap,
                        key=ev.id,
                    )
                    # Move the newly added row to the top (position 0)
                    if table.row_count > 1:
                        # Get the current first row key (will be the previous newest)
                        first_key = next(iter(table.rows.keys()))
                        if first_key != ev.id:
                            table.move_row(ev.id, before_key=first_key)

                    # Move cursor to the newest event (now at top)
                    table.move_cursor(row=0)

            # Trim old rows (DataTable keeps insertion order)
            while table.row_count > self.max_events:
                oldest_key = next(iter(table.rows.keys()))
                table.remove_row(oldest_key)

        except Exception as e:
            if not getattr(self, "_events_render_error_logged", False):
                self.add_log(f"Failed to render events table: {type(e).__name__}: {e}", "error")
                self._events_render_error_logged = True

    def _render_health(self) -> None:
        if not self.health:
            return
        try:
            pane = self.query_one("#health-pane", Vertical)

            h = self.health
            color = h.status_color

            pressure_text = f"[bold {color}]DETECTION PRESSURE[/]\nExpected: {h.expected_fps:.1f} fps   Actual: {h.detection_fps:.1f}   Backlog: {h.detection_pressure:.1f} ({h.pressure_pct*100:.0f}%)"
            skipped_text = f"\nSkipped frames / sec: [bold {color}]{h.skipped_fps}[/]\nPipeline healthy: [{'green' if h.is_healthy else 'red'}]{h.is_healthy}[/]"

            # Create the two statics only on first render
            if not pane.query("#health-pressure"):
                pressure = Static(pressure_text, classes=f"metric-value {color}", id="health-pressure")
                skipped = Static(skipped_text, classes="metric-value", id="health-skipped")
                pane.mount(pressure)
                pane.mount(skipped)
            else:
                # Update in place — no flicker
                pane.query_one("#health-pressure", Static).update(pressure_text)
                pane.query_one("#health-pressure", Static).classes = f"metric-value {color}"
                pane.query_one("#health-skipped", Static).update(skipped_text)

        except Exception as e:
            self.add_log(f"Failed to render health tab: {type(e).__name__}: {e}", "error")

    def _update_connection(self) -> None:
        try:
            status_widget = self.query_one(ConnectionStatus)
            status_widget.update_status(
                self.frigate_url, self.connection_ok, self.latency_ms, self.last_error
            )

            # Log state changes to the activity log (avoid spamming on every poll)
            if self.connection_ok and self.last_error is None:
                # Only log successful connection once per state change
                if not getattr(self, "_logged_connected", False):
                    self.add_log("Connection established", "success")
                    self._logged_connected = True
            else:
                if getattr(self, "_logged_connected", False):
                    err = self.last_error or "unknown error"
                    self.add_log(f"Connection lost: {err}", "error")
                    self._logged_connected = False
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    async def action_quit(self) -> None:
        if self._client:
            await self._client.aclose()

        if self._mqtt_task and not self._mqtt_task.done():
            self._mqtt_task.cancel()
            try:
                await self._mqtt_task
            except asyncio.CancelledError:
                pass

        if self._mqtt_client:
            await self._mqtt_client.disconnect()

        self.exit()

    def action_refresh(self) -> None:
        self.add_log("Manual refresh requested", "info")
        self._refresh_stats()
        self._refresh_events()

    def action_clear_log(self) -> None:
        """Clear the right-hand activity log."""
        try:
            log = self.query_one("#activity-log", RichLog)
            log.clear()
            self.add_log("Log cleared", "info")
        except Exception:
            pass

    def action_help(self) -> None:
        self.notify(
            "q quit • r refresh • 1-4 tabs • Tab/Shift+Tab focus\n"
            "Events auto-append when new detections occur.",
            title="Frigate TUI Help",
            timeout=5,
        )

    def action_switch_tab(self, tab_id: str) -> None:
        tc = self.query_one(TabbedContent)
        tc.active = tab_id

    def on_tabbed_content_tab_activated(self, event: TabbedContent.TabActivated) -> None:
        """Re-render tab contents when activated (handles lazy TabPane mounting)."""
        if event.pane.id == "cameras":
            self.add_log("Cameras tab activated — attempting render", "info")
            self._render_cameras()
        elif event.pane.id == "health":
            self._render_health()
        elif event.pane.id == "events":
            # Populate the Events table with everything we have so far
            # (important when events arrived via MQTT while user was on another tab)
            self._render_events_table()


if __name__ == "__main__":
    FrigateMonitor({"demo": True}).run()
