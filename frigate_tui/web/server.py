"""FastAPI web server for the Frigate TUI web interface.

This server reuses FrigateMonitorCore for 100% of the data acquisition, MQTT,
event list maintenance, health computation, and activity log generation.
The HTML/JS UI is a thin live view over SSE + initial snapshot.

Run via the CLI: `frigate-tui web --port 8080`
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.templating import Jinja2Templates
from starlette.responses import HTMLResponse, JSONResponse, Response, StreamingResponse

from frigate_tui.core import FrigateMonitorCore


class Broadcaster:
    """Simple in-process pub/sub for SSE clients using asyncio.Queue."""

    def __init__(self) -> None:
        self._queues: set[asyncio.Queue] = set()
        self._lock = asyncio.Lock()

    async def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=100)
        async with self._lock:
            self._queues.add(q)
        return q

    async def unsubscribe(self, q: asyncio.Queue) -> None:
        async with self._lock:
            self._queues.discard(q)

    async def publish(self, message: dict[str, Any]) -> None:
        """Send to all connected queues (best effort, drop on full)."""
        async with self._lock:
            qs = list(self._queues)
        for q in qs:
            try:
                q.put_nowait(message)
            except asyncio.QueueFull:
                # Drop oldest if consumer is slow (LAN dashboard, rare)
                try:
                    q.get_nowait()
                    q.put_nowait(message)
                except Exception:
                    pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    core: FrigateMonitorCore = app.state.core
    # Wire broadcaster so core updates become SSE events
    broadcaster: Broadcaster = app.state.broadcaster

    def _forward(kind: str, payload: Any) -> None:
        # Convert core notifications into small JSON messages for the browser
        try:
            if kind == "log":
                asyncio.create_task(broadcaster.publish({"type": "log", "data": payload}))
            elif kind == "stats":
                asyncio.create_task(broadcaster.publish({"type": "stats", "data": payload}))
            elif kind == "cameras":
                # Send lightweight camera list (core already enriched with health_*)
                cams = []
                for c in (payload or []):
                    cams.append({
                        "name": c.name,
                        "camera_fps": c.camera_fps,
                        "process_fps": c.process_fps,
                        "detection_fps": c.detection_fps,
                        "skipped_fps": c.skipped_fps,
                        "detection_enabled": c.detection_enabled,
                        "health_color": c.health_color,
                        "health_summary": c.health_summary,
                    })
                asyncio.create_task(broadcaster.publish({"type": "cameras", "data": cams}))
            elif kind == "health":
                if payload is not None:
                    h = {
                        "expected_fps": payload.expected_fps,
                        "detection_fps": payload.detection_fps,
                        "detection_pressure": payload.detection_pressure,
                        "pressure_pct": payload.pressure_pct,
                        "skipped_fps": payload.skipped_fps,
                        "is_healthy": payload.is_healthy,
                        "status_color": payload.status_color,
                    }
                    asyncio.create_task(broadcaster.publish({"type": "health", "data": h}))
            elif kind == "events":
                # Send full authoritative list (browser does the right thing for order)
                evs = []
                for e in (payload or []):
                    evs.append({
                        "id": e.id,
                        "camera": e.camera,
                        "label": e.label,
                        "start_time": e.start_time,
                        "end_time": e.end_time,
                        "top_score": e.top_score,
                        "has_snapshot": e.has_snapshot,
                        "has_clip": e.has_clip,
                        "zones": e.zones,
                        "sub_label": e.sub_label,
                        "average_estimated_speed": e.average_estimated_speed,
                        "color": e.color,
                        "display_label": e.display_label,
                        "duration_s": e.duration_s,
                    })
                asyncio.create_task(broadcaster.publish({"type": "events", "data": evs}))
            elif kind == "connection":
                asyncio.create_task(broadcaster.publish({"type": "connection", "data": payload}))
            elif kind == "version":
                asyncio.create_task(broadcaster.publish({"type": "version", "data": payload}))
        except Exception:
            # Broadcaster must never crash the core loop
            pass

    core.add_update_listener(_forward)

    await core.start()
    try:
        yield
    finally:
        await core.stop()


def create_app(core: FrigateMonitorCore | None = None, settings: dict[str, Any] | None = None) -> FastAPI:
    """Create the FastAPI application, wiring the provided (or newly created) core."""
    if core is None:
        core = FrigateMonitorCore(settings or {})

    app = FastAPI(
        title="Frigate TUI Web",
        description="Local-network web dashboard with full feature parity to the Frigate TUI.",
        version="0.1.0",
        lifespan=lifespan,
    )

    # Attach shared state
    app.state.core = core
    app.state.broadcaster = Broadcaster()

    # Templates live next to this file
    templates = Jinja2Templates(directory=Path(__file__).parent / "templates")

    @app.get("/")
    async def index(request: Request):
        """Render the dashboard.

        We render via the underlying Jinja template directly and return HTMLResponse
        to avoid a Starlette/Jinja2Templates caching interaction that chokes on
        the complex (but fully JSON-serializable) snapshot dict under some versions.
        """
        snap = core.get_snapshot()
        ctx = {
            "request": request,
            "snapshot": snap,
            "frigate_url": core.frigate_url,
            "demo": core.demo,
        }
        html = templates.get_template("index.html").render(ctx)
        return HTMLResponse(html)

    @app.get("/api/state")
    async def api_state():
        return JSONResponse(core.get_snapshot())

    @app.post("/api/refresh")
    async def api_refresh():
        await core.refresh_now()
        return {"ok": True}

    @app.get("/api/snapshot/{event_id}")
    async def api_snapshot(event_id: str, request: Request):
        """Proxy snapshot images so the browser doesn't need direct access to Frigate.

        This solves the common case where the web server can reach Frigate (via
        localhost / Docker networking / host.docker.internal) but the user's
        browser cannot.
        """
        if not core._client:
            return Response(status_code=503, content="No Frigate client available")
        try:
            params = dict(request.query_params)
            data = await core._client.get_snapshot(event_id, **params)
            return StreamingResponse(
                iter([data]),
                media_type="image/jpeg",
                headers={"Cache-Control": "public, max-age=3600"},
            )
        except Exception:
            return Response(status_code=404)

    @app.get("/api/clip/{event_id}")
    async def api_clip(event_id: str):
        """Proxy event clips (mp4) through the web server."""
        if not core._client:
            return Response(status_code=503)
        try:
            data = await core._client.get_clip(event_id)
            return StreamingResponse(
                iter([data]),
                media_type="video/mp4",
                headers={
                    "Cache-Control": "public, max-age=3600",
                    "Content-Disposition": f'inline; filename="clip_{event_id}.mp4"',
                },
            )
        except Exception:
            return Response(status_code=404)

    @app.get("/api/stream")
    async def api_stream(request: Request):
        """Server-Sent Events endpoint. Clients receive live updates."""
        q = await app.state.broadcaster.subscribe()

        async def event_generator():
            # Send a hello so the client knows it's connected
            yield "event: hello\ndata: {}\n\n"
            try:
                while True:
                    # If client disconnects, this will raise when we next yield
                    if await request.is_disconnected():
                        break
                    msg = await q.get()
                    # msg is already a small dict with "type" and "data"
                    yield f"data: {json.dumps(msg, default=str)}\n\n"
            except asyncio.CancelledError:
                pass
            finally:
                await app.state.broadcaster.unsubscribe(q)

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",  # important behind some proxies
            },
        )

    return app


def run_web(settings: dict[str, Any], host: str = "0.0.0.0", port: int = 8080) -> None:
    """Entry point used by the CLI `frigate-tui web` command."""
    import uvicorn

    core = FrigateMonitorCore(settings)
    app = create_app(core=core)

    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level="info",
        access_log=True,
    )
