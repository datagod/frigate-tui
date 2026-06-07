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
from starlette.staticfiles import StaticFiles

from frigate_tui.chatterbox_tts import (
    apply_delivery_mode_settings,
    apply_voice_override,
    get_or_synthesize_speech,
    list_chatterbox_voices,
    warm_event_tts_recordings,
)
from frigate_tui.delivery_modes import apply_delivery_mode, normalize_delivery_mode
from frigate_tui.core import FrigateMonitorCore
from frigate_tui.tts_recording_cache import (
    delete_recordings,
    list_recordings,
    media_type_for_filename,
    recording_file_path,
    resolve_recordings_dir,
    safe_recording_filename,
)
from frigate_tui.web.sounds_util import list_sound_files, sounds_directory


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
            elif kind == "genai_health":
                asyncio.create_task(
                    broadcaster.publish({"type": "genai_health", "data": payload or {}})
                )
            elif kind == "event_alerts":
                alerts = payload or []
                asyncio.create_task(
                    broadcaster.publish({"type": "event_alerts", "data": alerts})
                )
                asyncio.create_task(warm_event_tts_recordings(core, alerts))
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
                        "description": e.description,
                    })
                asyncio.create_task(broadcaster.publish({"type": "events", "data": evs}))
            elif kind == "connection":
                asyncio.create_task(broadcaster.publish({"type": "connection", "data": payload}))
            elif kind == "version":
                asyncio.create_task(broadcaster.publish({"type": "version", "data": payload}))
            elif kind == "poll_interval":
                asyncio.create_task(broadcaster.publish({"type": "poll_interval", "data": payload}))
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

    sounds_dir = sounds_directory()
    if sounds_dir is not None:
        app.mount("/sounds", StaticFiles(directory=sounds_dir), name="sounds")

    @app.get("/api/sounds")
    async def api_sounds():
        """List available sound files and configured alert → filename mappings."""
        root = sounds_dir or sounds_directory()
        return JSONResponse(
            {
                "ok": True,
                "directory": str(root) if root else None,
                "files": list_sound_files(root),
                "mapping": dict(core.web_alerts_sounds),
            }
        )

    def _recordings_cache_dir() -> str:
        return str(core.chatterbox_tts_config.get("cache_dir") or "localrecordings")

    @app.get("/api/recordings")
    async def api_recordings():
        """List cached Chatterbox TTS recordings under localrecordings/."""
        cache_dir = _recordings_cache_dir()
        root = resolve_recordings_dir(cache_dir)
        recordings = list_recordings(cache_dir)
        return JSONResponse(
            {
                "ok": True,
                "directory": str(root),
                "total": len(recordings),
                "recordings": recordings,
            }
        )

    @app.get("/api/recordings/{filename}")
    async def api_recording_file(filename: str, download: bool = False):
        """Stream a cached recording for inline play or download."""
        safe = safe_recording_filename(filename)
        if not safe:
            return JSONResponse({"ok": False, "error": "invalid filename"}, status_code=400)
        path = recording_file_path(safe, cache_dir=_recordings_cache_dir())
        if path is None or not path.is_file() or path.stat().st_size <= 0:
            return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
        media_type = media_type_for_filename(safe)
        disposition = "attachment" if download else "inline"
        headers = {
            "Content-Disposition": f'{disposition}; filename="{safe}"',
            "Cache-Control": "public, max-age=86400",
        }
        return Response(content=path.read_bytes(), media_type=media_type, headers=headers)

    @app.post("/api/recordings/delete")
    async def api_recordings_delete(request: Request):
        """Delete one or more cached recordings by basename."""
        try:
            body = await request.json()
        except Exception:
            body = {}
        names = body.get("filenames") or body.get("files") or []
        if isinstance(names, str):
            names = [names]
        if not isinstance(names, list) or not names:
            return JSONResponse(
                {"ok": False, "error": "filenames array required"},
                status_code=400,
            )
        cache_dir = _recordings_cache_dir()
        result = delete_recordings([str(n) for n in names], cache_dir=cache_dir)
        deleted = result.get("deleted") or []
        errors = result.get("errors") or {}
        if deleted:
            core.add_log(
                f"Deleted {len(deleted)} TTS recording(s) from {cache_dir}",
                "info",
            )
        if errors and not deleted:
            return JSONResponse(
                {
                    "ok": False,
                    "deleted": deleted,
                    "errors": errors,
                    "error": "no files deleted",
                },
                status_code=400,
            )
        return JSONResponse(
            {
                "ok": True,
                "deleted": deleted,
                "errors": errors,
                "remaining": len(list_recordings(cache_dir)),
            }
        )

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

    @app.post("/api/set_poll_interval")
    async def api_set_poll_interval(request: Request):
        try:
            body = await request.json()
            interval = float(body.get("interval", 5.0))
            core.set_poll_interval(interval)
            return JSONResponse({"ok": True, "poll_interval": core.poll_interval})
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=400)

    @app.get("/api/tts/voice")
    async def api_tts_voice_get():
        """Return the UI-chosen voice for event alerts and TTS generation."""
        cfg = core.chatterbox_tts_config
        if not cfg.get("enabled"):
            return JSONResponse(
                {"ok": False, "error": "Chatterbox TTS is disabled in config."},
                status_code=503,
            )
        chosen = core.get_event_tts_voice()
        default_mode = cfg.get("voice_mode", "clone")
        default_voice = (
            cfg.get("reference_audio_filename")
            if default_mode == "clone"
            else cfg.get("predefined_voice_id")
        )
        return JSONResponse(
            {
                "ok": True,
                "chosen": chosen,
                "delivery_mode": core.get_event_tts_delivery_mode(),
                "default": {"voice_mode": default_mode, "voice": default_voice},
            }
        )

    @app.post("/api/tts/voice")
    async def api_tts_voice_set(request: Request):
        """Persist the UI-chosen voice used for event alerts and cache misses."""
        cfg = core.chatterbox_tts_config
        if not cfg.get("enabled"):
            return JSONResponse(
                {"ok": False, "error": "Chatterbox TTS is disabled in config."},
                status_code=503,
            )
        try:
            body = await request.json()
        except Exception:
            body = {}
        if body.get("voice_mode") and body.get("voice"):
            chosen = core.set_event_tts_voice(
                body.get("voice_mode"),
                body.get("voice"),
            )
        else:
            chosen = core.get_event_tts_voice()
        if "delivery_mode" in body:
            delivery_mode = core.set_event_tts_delivery_mode(body.get("delivery_mode"))
        else:
            delivery_mode = core.get_event_tts_delivery_mode()
        if chosen and (body.get("voice_mode") and body.get("voice")):
            core.add_log(
                f"Event TTS voice set to {chosen['voice']} ({chosen['voice_mode']}, {delivery_mode})",
                "info",
            )
        elif "delivery_mode" in body:
            core.add_log(f"Event TTS delivery mode set to {delivery_mode}", "info")
        elif body.get("voice_mode") or body.get("voice"):
            core.add_log("Event TTS voice cleared (using config default)", "info")
        return JSONResponse(
            {
                "ok": True,
                "chosen": chosen,
                "delivery_mode": delivery_mode,
            }
        )

    @app.get("/api/tts/voices")
    async def api_tts_voices():
        """List Chatterbox clone and predefined voices for the test UI."""
        cfg = core.chatterbox_tts_config
        if not cfg.get("enabled"):
            return JSONResponse(
                {"ok": False, "error": "Chatterbox TTS is disabled in config."},
                status_code=503,
            )
        try:
            voices = await list_chatterbox_voices(cfg)
            default_mode = cfg.get("voice_mode", "clone")
            default_voice = (
                cfg.get("reference_audio_filename")
                if default_mode == "clone"
                else cfg.get("predefined_voice_id")
            )
            return JSONResponse(
                {
                    "ok": True,
                    "voices": voices,
                    "default": {"voice_mode": default_mode, "voice": default_voice},
                    "chosen": core.get_event_tts_voice(),
                    "delivery_mode": core.get_event_tts_delivery_mode(),
                }
            )
        except Exception as e:
            return JSONResponse(
                {"ok": False, "error": f"{type(e).__name__}: {e}"},
                status_code=502,
            )

    @app.post("/api/tts/speak")
    async def api_tts_speak(request: Request):
        """Proxy short text to Chatterbox TTS and return generated audio."""
        cfg = core.chatterbox_tts_config
        if not cfg.get("enabled"):
            return JSONResponse(
                {"ok": False, "error": "Chatterbox TTS is disabled in config."},
                status_code=503,
            )
        try:
            body = await request.json()
        except Exception:
            body = {}
        text = str(body.get("text") or cfg.get("default_test_message") or "").strip()
        if not text:
            return JSONResponse(
                {"ok": False, "error": "text is required"},
                status_code=400,
            )
        delivery_mode = normalize_delivery_mode(
            body.get("delivery_mode") or core.get_event_tts_delivery_mode()
        )
        spoken_text = apply_delivery_mode(text, delivery_mode)
        body_mode = str(body.get("voice_mode") or "").strip()
        body_voice = str(body.get("voice") or "").strip()
        if body_mode and body_voice:
            speak_cfg = apply_delivery_mode_settings(
                apply_voice_override(
                    cfg,
                    voice_mode=body_mode,
                    voice=body_voice,
                ),
                delivery_mode,
            )
        else:
            speak_cfg = core.get_event_tts_settings()
        try:
            audio, media_type, from_cache, saved_path = await get_or_synthesize_speech(
                spoken_text, settings=speak_cfg
            )
        except ValueError as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
        except TimeoutError as e:
            core.add_log(f"TTS failed: {e}", "error")
            return JSONResponse({"ok": False, "error": str(e), "logged": True}, status_code=504)
        except ConnectionError as e:
            core.add_log(f"TTS failed: {e}", "error")
            return JSONResponse({"ok": False, "error": str(e), "logged": True}, status_code=502)
        except RuntimeError as e:
            core.add_log(f"TTS failed: {e}", "error")
            return JSONResponse({"ok": False, "error": str(e), "logged": True}, status_code=502)
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
            core.add_log(f"TTS failed: {err}", "error")
            return JSONResponse(
                {"ok": False, "error": err, "logged": True},
                status_code=500,
            )

        voice = (
            speak_cfg.get("reference_audio_filename")
            if speak_cfg.get("voice_mode") == "clone"
            else speak_cfg.get("predefined_voice_id")
        )
        if from_cache:
            core.add_log(f"TTS cache hit ({voice or 'default'}): {saved_path}", "info")
        else:
            core.add_log(
                f"TTS generated {len(text)} chars ({voice or 'default'}), saved: {saved_path}",
                "success",
            )
        return Response(
            content=audio,
            media_type=media_type,
            headers={
                "Cache-Control": "no-store",
                "X-TTS-Cache": "hit" if from_cache else "miss",
            },
        )

    @app.post("/api/log")
    async def api_log(request: Request):
        """Append a line to the Activity Log (e.g. client-side summary request failures)."""
        try:
            body = await request.json()
        except Exception:
            body = {}
        message = str(body.get("message") or "").strip()
        if not message:
            return JSONResponse({"ok": False, "error": "message required"}, status_code=400)
        level = str(body.get("level") or "info").strip().lower()
        if level not in ("info", "success", "warning", "error"):
            level = "info"
        core.add_log(message, level)
        return JSONResponse({"ok": True})

    @app.get("/api/genai/health")
    async def api_genai_health(refresh: bool = False):
        """GenAI pipeline + LLM endpoint health for the Health tab."""
        if refresh or not core.genai_health:
            await core.refresh_genai_health(force=True)
        return JSONResponse({"ok": True, "health": core.genai_health or {}})

    @app.get("/api/genai/activity")
    async def api_genai_activity(hours: float | None = None):
        """Structured GenAI messages from the last N hours, grouped by hour."""
        hrs = max(0.25, min(72.0, float(hours if hours is not None else core.genai_report_default_hours)))
        sections = core.get_genai_sections(hrs)
        total = sum(s["count"] for s in sections)
        return JSONResponse({"ok": True, "hours": hrs, "total": total, "sections": sections})

    @app.get("/api/genai/messages")
    async def api_genai_messages(hours: float | None = None):
        """Chronological GenAI messages, newest first."""
        hrs = max(0.25, min(72.0, float(hours if hours is not None else core.genai_report_default_hours)))
        messages = core.get_genai_messages(hrs)
        return JSONResponse({"ok": True, "hours": hrs, "total": len(messages), "messages": messages})

    @app.post("/api/genai/frigate-report")
    async def api_genai_frigate_report(request: Request):
        """Frigate native GenAI review summarize report (suspicious review items)."""
        hrs = core.genai_report_default_hours
        try:
            try:
                body = await request.json()
            except Exception:
                body = {}
            hrs = max(0.25, min(24.0, float(body.get("hours", core.genai_report_default_hours))))
            result = await core.generate_frigate_review_report(hrs)
            status = 200 if result.get("ok") else 400
            return JSONResponse(result, status_code=status)
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
            core.add_log(f"Frigate report failed (API): {err}", "error")
            return JSONResponse(
                {
                    "ok": False,
                    "error": err,
                    "hours": hrs,
                    "summary": None,
                    "logged": True,
                },
                status_code=500,
            )

    @app.post("/api/genai/report")
    async def api_genai_report(request: Request):
        """Send recent GenAI messages to the configured LLM for a narrative hourly report."""
        hrs = core.genai_report_default_hours
        try:
            try:
                body = await request.json()
            except Exception:
                body = {}
            hrs = max(0.25, min(24.0, float(body.get("hours", core.genai_report_default_hours))))
            result = await core.generate_genai_report(hrs)
            status = 200 if result.get("ok") else 400
            return JSONResponse(result, status_code=status)
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
            core.add_log(f"Summary failed (API): {err}", "error")
            return JSONResponse(
                {
                    "ok": False,
                    "error": err,
                    "hours": hrs,
                    "sections": [],
                    "report": None,
                    "logged": True,
                },
                status_code=500,
            )

    @app.get("/api/event/{event_id}")
    async def api_event_detail(event_id: str):
        """Event detail for modal: full object description + linked review GenAI if any."""
        detail = await core.fetch_event_detail(event_id)
        if not detail:
            return JSONResponse({"ok": False, "error": "Event not found"}, status_code=404)
        return JSONResponse({"ok": True, **detail})

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
