"""Main Textual application for the advanced Frigate TUI."""

from __future__ import annotations

import asyncio
from datetime import datetime
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
    SystemHealth,
    compute_health,
    label_color,
    parse_cameras,
)


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

    def update_value(self, label: str, value: float, max_val: float = 10.0, color: str | None = None) -> None:
        pct = min(1.0, max(0.0, value / max_val)) if max_val > 0 else 0
        filled = int(pct * 8)
        bar = "█" * filled + "░" * (8 - filled)

        if color is None:
            if value < 0.3:
                color = "red"
            elif pct < 0.55:
                color = "yellow"
            else:
                color = "green"

        txt = Text()
        txt.append(f"{label:<11}", style="dim")
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
    """Advanced colorful Frigate NVR monitor — queues, cameras, events, health."""

    TITLE = "Frigate Monitor"
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
        border: tall #2a3142;
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
        border: tall #2a3142;
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
        width: 70%;
        min-width: 60;
    }

    #log-panel {
        width: 30%;
        min-width: 35;
        border-left: tall #2a3142;
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
        self.max_events: int = int(s.get("max_events", 150))
        self.demo = bool(s.get("demo", False))

        self._client: FrigateClient | None = None
        self._frigate_version: str = "?"
        self._last_event_ts: float | None = None
        self._logged_connected: bool = False

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
            ts = datetime.now().strftime("%H:%M:%S")

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

        # Seed events table columns
        table = self.query_one("#events-table", DataTable)
        table.add_columns("Time", "Camera", "Label", "Score", "Dur", "Clip", "Snap")

        # Start live polling
        self.set_interval(self.poll_interval, self._refresh_stats)
        self.set_interval(max(2.0, self.poll_interval * 2), self._refresh_events)

        self.add_log("Polling started", "info")

        # Prime the pump
        await self._refresh_stats()
        await self._refresh_events()

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

        self._render_summary(stats)
        self._render_cameras()
        self._render_health()
        self._update_connection()

        # Log to the right-hand activity window
        if ok:
            det = stats.get("detection_fps", 0) if stats else 0
            self.add_log(f"Stats OK ({latency}ms) — detection {det:.1f} fps", "success")
        else:
            self.add_log(f"Stats poll failed: {err}", "error")

    async def _refresh_events(self) -> None:
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
                    self.add_log(f"New event: {ev.label} on {ev.camera}", "success")
                if len(new_events) > 3:
                    self.add_log(f"+{len(new_events)-3} more events", "info")
        except Exception as e:
            self.add_log(f"Events poll error: {e}", "warning")

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
            scroll.remove_children()

            for cam in self.cameras:
                row = Horizontal(classes="camera-row")
                row.mount(Label(f"[bold]{cam.name}[/]", classes="metric-label"))

                # Three bars
                for label, val, mx in [
                    ("cam", cam.camera_fps, 8),
                    ("proc", cam.process_fps, 8),
                    ("det", cam.detection_fps, 8),
                ]:
                    bar = FPSBar()
                    bar.update_value(label, val, mx, cam.health_color if label == "det" else None)
                    row.mount(bar)

                status = "ON" if cam.detection_enabled else "OFF"
                row.mount(Label(f"[{cam.health_color}]{status}[/]", classes="metric-value"))
                scroll.mount(row)
        except Exception:
            pass

    def _render_events_table(self, new_only: list[FrigateEvent] | None = None) -> None:
        try:
            table = self.query_one("#events-table", DataTable)
            to_add = new_only or self.recent_events

            for ev in to_add:
                # Skip duplicates (we may receive the same event again on refresh)
                if ev.id in table.rows:
                    continue

                ts = datetime.fromtimestamp(ev.start_time).strftime("%H:%M:%S")
                dur = f"{ev.duration_s:.1f}s" if ev.duration_s else "—"
                clip = "📼" if ev.has_clip else ""
                snap = "📷" if ev.has_snapshot else ""

                label_cell = Text(ev.label, style=label_color(ev.label))

                table.add_row(
                    ts,
                    ev.camera,
                    label_cell,
                    f"{ev.top_score:.2f}" if ev.top_score else "—",
                    dur,
                    clip,
                    snap,
                    key=ev.id,
                )

            # Trim old rows (DataTable keeps insertion order)
            while table.row_count > self.max_events:
                # Remove the oldest row (first key in the rows dict)
                oldest_key = next(iter(table.rows.keys()))
                table.remove_row(oldest_key)
        except Exception:
            pass

    def _render_health(self) -> None:
        if not self.health:
            return
        try:
            pane = self.query_one("#health-pane", Vertical)
            pane.remove_children()

            h = self.health
            color = h.status_color

            pane.mount(Static(f"[bold {color}]DETECTION PRESSURE[/]\n"
                              f"Expected: {h.expected_fps:.1f} fps   "
                              f"Actual: {h.detection_fps:.1f}   "
                              f"Backlog: {h.detection_pressure:.1f} ({h.pressure_pct*100:.0f}%)",
                              classes=f"metric-value {color}"))

            pane.mount(Static(f"\nSkipped frames / sec: [bold {color}]{h.skipped_fps}[/]\n"
                              f"Pipeline healthy: [{'green' if h.is_healthy else 'red'}]{h.is_healthy}[/]",
                              classes="metric-value"))
        except Exception:
            pass

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


if __name__ == "__main__":
    FrigateMonitor({"demo": True}).run()
