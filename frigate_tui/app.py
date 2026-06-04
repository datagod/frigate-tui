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
from frigate_tui.core import FrigateMonitorCore
from frigate_tui.models import (
    CameraStats,
    FrigateEvent,
    SystemHealth,
    compute_health,
    label_color,
    parse_cameras,
)
# Note: api/mqtt/models are now primarily used via core for shared logic.
# We keep a couple of direct imports only for type hints / edge cases if needed.
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
        Binding("p", "set_poll_interval", "Cycle Poll", show=True),
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

        # Core owns ALL the hard logic (fetch, MQTT, state, logs, merge rules, etc.)
        self.core = FrigateMonitorCore(s)

        # Keep lightweight local aliases for the old reactive-driven code paths
        self.frigate_url: str = self.core.frigate_url
        self.demo = self.core.demo
        self.max_events = self.core.max_events

        # Camera card cache is TUI-only (for flicker-free in-place updates)
        self._camera_cards: dict[str, Vertical] = {}

        # We still let the old methods read/write these reactives for minimal diff in renders.
        # The listeners below will keep them in sync with core.

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

                yield Label("POLL", classes="metric-label")
                yield Label("1.0s", id="poll-interval", classes="metric-value")

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
                            "  p          Cycle poll interval (0.5/1/2/5/10s)\n"
                            "  ?          Help\n"
                            "  2 / 3 / 4  Switch tabs\n"
                            "  c          Clear log\n\n"
                            "This is the best place to see connection attempts, errors, and polling results.",
                            id="overview-welcome"
                        )
                        yield Static("", id="overview-status", classes="text-xs text-[#6b7280] mt-2")

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
        # Wire core listeners first so we receive logs/state immediately
        self.core.add_update_listener(self._on_core_update)

        # TUI-specific: show target early + create the events DataTable schema
        self._update_connection()
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

        # Start the core (it does version probe, initial loads, all polling/MQTT, log emission)
        await self.core.start()

        # Prime the on-screen version label from whatever the core discovered
        self._update_version_label()
        try:
            self.query_one("#poll-interval", Label).update(f"{self.core.poll_interval:.1f}s")
        except Exception:
            pass
        self._update_overview_status()

        # Initial pump (core already did a stats refresh inside start, but ensure renders)
        self.call_after_refresh(self._render_cameras)
        self.call_after_refresh(self._render_health)

    def _update_version_label(self) -> None:
        try:
            ver = getattr(self.core, "_frigate_version", "?")
            self.query_one("#frigate-version", Label).update(ver)
        except Exception:
            pass

    def _update_overview_status(self) -> None:
        try:
            status = self.query_one("#overview-status", Static)
            core = self.core
            tui_ver = getattr(core, "tui_version", None) or "0.1.0"
            fr_ver = getattr(core, "_frigate_version", "?")
            poll = getattr(core, "poll_interval", 1.0)
            cams = len(getattr(core, "cameras", []))
            h = getattr(core, "health", None)
            health_str = f"{(h.pressure_pct*100):.0f}% pressure" if h else "—"
            evs = len(getattr(core, "recent_events", []))
            status.update(
                f"frigate-tui v{tui_ver} • Frigate {fr_ver} • poll {poll:.1f}s • {cams} cams • {health_str} • {evs} events"
            )
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Core listener bridge (keeps old reactives + render methods working)
    # ------------------------------------------------------------------

    def _on_core_update(self, kind: str, payload: Any) -> None:
        """Receive notifications from the shared core and drive the TUI.

        This is the key integration point that lets us reuse 100% of the
        update / MQTT / health / log logic while keeping the existing
        Textual widgets and render methods.
        """
        try:
            if kind == "stats":
                self.last_stats = payload
                self._render_summary(payload)
                self._render_health()
                self._update_connection()
                self.call_after_refresh(self._render_cameras)
                self.call_after_refresh(self._render_health)
            elif kind == "cameras":
                self.cameras = payload or []
                self.call_after_refresh(self._render_cameras)
                self._update_overview_status()
            elif kind == "health":
                self.health = payload
                self._render_health()
                self._update_overview_status()
            elif kind == "events":
                self.recent_events = payload or []
                # Full authoritative list from core — rebuild for correct order (matches old behavior)
                self._render_events_table()
                self._update_overview_status()
            elif kind == "log":
                # Core already emitted the exact same message strings as the old TUI
                if isinstance(payload, dict):
                    self.add_log(payload.get("message", ""), payload.get("level", "info"))
                else:
                    self.add_log(str(payload))
            elif kind == "connection":
                if isinstance(payload, dict):
                    self.connection_ok = payload.get("ok", True)
                    self.latency_ms = payload.get("latency_ms")
                    self.last_error = payload.get("last_error")
                self._update_connection()
                self._update_overview_status()
            elif kind == "version":
                self._update_version_label()
                self._update_overview_status()
            elif kind == "poll_interval":
                try:
                    self.query_one("#poll-interval", Label).update(f"{float(payload):.1f}s")
                except Exception:
                    pass
                self._update_overview_status()
        except Exception:
            # Never let a listener break the TUI
            pass

    # ------------------------------------------------------------------
    # Thin delegation wrappers (kept for any direct calls from actions / tabs)
    # The real work now lives in core. These just forward.
    # ------------------------------------------------------------------

    async def _refresh_stats(self) -> None:
        await self.core.refresh_now()

    async def _refresh_events(self) -> None:
        # Core owns the cadence; manual nudge is a full refresh
        await self.core.refresh_now()

    # Rendering methods below are intentionally kept (they are TUI-specific and
    # operate on the reactives that are kept in sync by _on_core_update).
    # The data that drives them now comes exclusively from the shared core.

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

            # To guarantee correct sort order by start_time (newest first at top) and avoid
            # "late discovered" events (via MQTT update for ids not in initial load) appearing
            # out of order at the "current top", we do a full rebuild from the authoritative
            # sorted self.recent_events whenever we are adding any new IDs.
            # Pure updates (existing IDs) just refresh cells in place (no order change, less work).
            any_new_ids = new_only is None or any(ev.id not in table.rows for ev in to_process)
            if any_new_ids:
                # Full rebuild ensures table exactly matches current sorted recent_events.
                # This fixes mixed sort order (e.g. 19:05 then 19:36 then 19:06) and
                # "top records go missing" on tab return (previous visual had out-of-order
                # inserts; rebuild uses the list which is kept sorted + capped).
                table.clear()
                for ev in self.recent_events:
                    ts = datetime.fromtimestamp(ev.start_time, tz=timezone.utc).astimezone().strftime("%H:%M:%S")
                    dur = f"{ev.duration_s:.1f}s" if ev.duration_s else "—"
                    clip = "📼" if ev.has_clip else ""
                    snap = "📷" if ev.has_snapshot else ""
                    display_label = ev.display_label
                    label_cell = Text(display_label, style=label_color(ev.label))
                    speed_str = f"{ev.average_estimated_speed:.1f}" if ev.average_estimated_speed else "—"
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
                if self.recent_events:
                    table.move_cursor(row=0)
            else:
                # All items in to_process are updates to rows we already have: just refresh cells.
                for ev in to_process:
                    if ev.id in table.rows:
                        ts = datetime.fromtimestamp(ev.start_time, tz=timezone.utc).astimezone().strftime("%H:%M:%S")
                        dur = f"{ev.duration_s:.1f}s" if ev.duration_s else "—"
                        clip = "📼" if ev.has_clip else ""
                        snap = "📷" if ev.has_snapshot else ""
                        display_label = ev.display_label
                        label_cell = Text(display_label, style=label_color(ev.label))
                        speed_str = f"{ev.average_estimated_speed:.1f}" if ev.average_estimated_speed else "—"
                        row_key = ev.id
                        table.update_cell(row_key, "label", label_cell)
                        table.update_cell(row_key, "speed", speed_str)
                        table.update_cell(row_key, "score", f"{ev.top_score:.2f}" if ev.top_score else "—")
                        table.update_cell(row_key, "dur", dur)
                        table.update_cell(row_key, "clip", clip)
                        table.update_cell(row_key, "snap", snap)

            # Trim (defensive; full rebuilds from capped list shouldn't exceed, but live
            # incremental adds can temporarily).
            while table.row_count > self.max_events:
                keys = list(table.rows.keys())
                oldest_key = keys[-1]
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
        """Drive the bottom status bar from current connection reactives.

        Logging of connect/lost transitions is now owned exclusively by the core
        (ensures identical messages in TUI and web UIs).
        """
        try:
            status_widget = self.query_one(ConnectionStatus)
            status_widget.update_status(
                self.frigate_url, self.connection_ok, self.latency_ms, self.last_error
            )
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    async def action_quit(self) -> None:
        # Core owns clients + tasks; it will cancel and close everything cleanly.
        await self.core.stop()
        self.exit()

    async def action_refresh(self) -> None:
        self.add_log("Manual refresh requested", "info")
        await self._refresh_stats()
        await self._refresh_events()

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
            "q quit • r refresh • 1-4 tabs • Tab/Shift+Tab focus • p cycle poll speed\n"
            "Events auto-append when new detections occur.",
            title="Frigate TUI Help",
            timeout=5,
        )

    def action_switch_tab(self, tab_id: str) -> None:
        tc = self.query_one(TabbedContent)
        tc.active = tab_id

    def action_set_poll_interval(self) -> None:
        """Cycle through common poll intervals for live control from the TUI."""
        options = [0.5, 1.0, 2.0, 5.0, 10.0]
        current = getattr(self.core, "poll_interval", 1.0)
        try:
            idx = options.index(round(current, 1))
            new = options[(idx + 1) % len(options)]
        except ValueError:
            new = 1.0
        self.core.set_poll_interval(new)
        try:
            self.query_one("#poll-interval", Label).update(f"{new:.1f}s")
        except Exception:
            pass

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
        elif event.pane.id == "overview":
            self._update_overview_status()


if __name__ == "__main__":
    FrigateMonitor({"demo": True}).run()
