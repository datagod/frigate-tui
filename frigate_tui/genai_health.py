"""GenAI / LLM health probes for the Health tab and API."""

from __future__ import annotations

import time
from typing import Any

import httpx

from frigate_tui.genai_report import resolve_llm_settings
from frigate_tui.models import genai_summary_from_review_data


def _model_listed(model: str, names: list[str]) -> bool:
    """True if configured model appears in an Ollama/OpenAI model list."""
    if not model or not names:
        return False
    m = model.strip()
    base = m.split(":", 1)[0]
    for n in names:
        if not n:
            continue
        if n == m or n.startswith(f"{m}:") or m.startswith(f"{n}:"):
            return True
        if base and (n == base or n.startswith(f"{base}:")):
            return True
    return False


def _extract_model_names(data: Any) -> list[str]:
    if not isinstance(data, dict):
        return []
    if isinstance(data.get("models"), list):
        return [str(m.get("name", "")).strip() for m in data["models"] if m.get("name")]
    if isinstance(data.get("data"), list):
        return [str(m.get("id", "")).strip() for m in data["data"] if m.get("id")]
    return []


async def probe_llm(
    base_url: str,
    model: str,
    *,
    timeout: float = 8.0,
) -> dict[str, Any]:
    """Check LLM reachability (Ollama /api/tags or OpenAI /v1/models)."""
    url = base_url.rstrip("/")
    t0 = time.monotonic()
    result: dict[str, Any] = {
        "reachable": False,
        "model_listed": False,
        "latency_ms": None,
        "probe": None,
        "error": None,
        "models_found": 0,
    }

    async def _try_get(path: str, probe: str) -> httpx.Response | None:
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                return await client.get(f"{url}{path}")
        except httpx.TimeoutException:
            result["error"] = f"timed out after {timeout:.0f}s"
            result["probe"] = probe
            return None
        except httpx.RequestError as e:
            result["error"] = f"{type(e).__name__}: {e}"
            result["probe"] = probe
            return None

    for path, probe in (("/api/tags", "ollama_tags"), ("/v1/models", "openai_models")):
        resp = await _try_get(path, probe)
        if resp is None:
            continue
        result["latency_ms"] = int((time.monotonic() - t0) * 1000)
        result["probe"] = probe
        if resp.status_code >= 400:
            result["error"] = f"HTTP {resp.status_code}"
            continue
        try:
            data = resp.json()
        except Exception:
            result["error"] = "invalid JSON"
            continue
        names = _extract_model_names(data)
        result["models_found"] = len(names)
        result["reachable"] = True
        result["model_listed"] = _model_listed(model, names)
        result["error"] = None if result["model_listed"] else f"model {model!r} not in list ({len(names)} loaded)"
        return result

    if result["latency_ms"] is None:
        result["latency_ms"] = int((time.monotonic() - t0) * 1000)
    if not result["error"]:
        result["error"] = "no supported model listing endpoint (/api/tags or /v1/models)"
    return result


def genai_ingestion_status(
    *,
    demo: bool,
    mqtt_configured: bool,
    mqtt_available: bool,
    mqtt_connected: bool,
    use_mqtt_for_events: bool,
) -> dict[str, Any]:
    """How Frigate GenAI messages reach this monitor."""
    if demo:
        return {
            "mode": "demo",
            "summary": "Demo mode (sample descriptions)",
            "status": "ok",
            "status_color": "good",
        }
    if use_mqtt_for_events and mqtt_connected:
        return {
            "mode": "realtime_mqtt",
            "summary": "Real-time via MQTT (events + tracked_object_update + reviews)",
            "status": "ok",
            "status_color": "good",
        }
    if mqtt_configured and not mqtt_available:
        return {
            "mode": "unavailable",
            "summary": "MQTT configured but aiomqtt not installed",
            "status": "degraded",
            "status_color": "warn",
        }
    if mqtt_configured and not mqtt_connected:
        return {
            "mode": "mqtt_down",
            "summary": "MQTT configured but not connected — descriptions via HTTP poll",
            "status": "degraded",
            "status_color": "warn",
        }
    if mqtt_configured:
        return {
            "mode": "mqtt_partial",
            "summary": "MQTT up; events may still use HTTP — GenAI updates via MQTT when subscribed",
            "status": "warn",
            "status_color": "warn",
        }
    return {
        "mode": "http_poll",
        "summary": "HTTP polling only — add mqtt to config.yaml for live GenAI updates",
        "status": "degraded",
        "status_color": "warn",
    }


def activity_stats(genai_activity: list[dict[str, Any]], hours: float = 1.0) -> dict[str, Any]:
    """Counts and recency for stored GenAI messages."""
    now = time.time()
    cutoff = now - hours * 3600.0
    recent = [e for e in genai_activity if float(e.get("ts", 0)) >= cutoff]
    last_ts = max((float(e.get("ts", 0)) for e in genai_activity), default=0.0)
    by_kind: dict[str, int] = {}
    for e in recent:
        k = str(e.get("kind") or "unknown")
        by_kind[k] = by_kind.get(k, 0) + 1
    age_min: float | None = None
    if last_ts > 0:
        age_min = max(0.0, (now - last_ts) / 60.0)
    return {
        "messages_1h": len(recent),
        "messages_total": len(genai_activity),
        "by_kind_1h": by_kind,
        "last_message_ts": last_ts or None,
        "last_message_age_min": round(age_min, 1) if age_min is not None else None,
    }


def frigate_review_genai_probe(
    reviews: list[dict[str, Any]],
    *,
    hours: float = 1.0,
) -> dict[str, Any]:
    """How many recent Frigate /api/review items include GenAI metadata."""
    now = time.time()
    cutoff = now - max(0.25, hours) * 3600.0
    recent = [r for r in reviews if float(r.get("start_time") or 0) >= cutoff]
    with_genai = 0
    completed = 0
    for raw in recent:
        if raw.get("end_time") is not None:
            completed += 1
        if genai_summary_from_review_data(raw.get("data") or {}):
            with_genai += 1
    return {
        "reviews_1h": len(recent),
        "reviews_completed_1h": completed,
        "reviews_with_genai_1h": with_genai,
    }


def _overall_status(
    *,
    demo: bool,
    llm: dict[str, str] | None,
    llm_probe: dict[str, Any] | None,
    ingestion: dict[str, Any],
    activity: dict[str, Any],
    frigate_genai: dict[str, Any] | None,
    frigate_review_probe: dict[str, Any] | None = None,
) -> tuple[str, str, str]:
    """Return (status, status_color, summary)."""
    if demo:
        return "ok", "good", "Demo mode"

    issues: list[str] = []
    color = "good"

    if llm is None:
        issues.append("LLM not configured for Summary tab")
        color = "warn"
    elif llm_probe:
        if not llm_probe.get("reachable"):
            issues.append("LLM unreachable")
            color = "bad"
        elif not llm_probe.get("model_listed"):
            issues.append("LLM model not loaded")
            if color != "bad":
                color = "warn"

    if ingestion.get("status_color") == "warn" and color == "good":
        color = "warn"
    if ingestion.get("status") == "degraded":
        issues.append("ingestion degraded")

    fg = frigate_genai or {}
    if fg.get("configured") and not fg.get("enabled", True):
        issues.append("Frigate GenAI disabled in config")
        color = "warn"

    if activity.get("messages_1h", 0) == 0 and not demo:
        probe = frigate_review_probe or {}
        completed = int(probe.get("reviews_completed_1h") or 0)
        with_genai = int(probe.get("reviews_with_genai_1h") or 0)
        if completed > 0 and with_genai == 0:
            issues.append(
                "no GenAI messages in the last hour — Frigate reviews lack metadata "
                "(check Frigate logs: Ollama timeouts or num_ctx too small for preview frames)"
            )
            color = "warn"
        elif int(probe.get("reviews_1h") or 0) > 0 and with_genai == 0:
            issues.append(
                "no GenAI messages in the last hour — Frigate has not written review metadata yet"
            )
            if color == "good":
                color = "warn"
        else:
            issues.append("no GenAI messages in the last hour")
            if color == "good":
                color = "warn"

    if not issues:
        return "ok", color, "GenAI pipeline healthy"
    return "degraded" if color != "bad" else "error", color, "; ".join(issues)


async def collect_genai_health(
    *,
    demo: bool,
    genai_report_config: dict[str, Any],
    genai_activity: list[dict[str, Any]],
    mqtt_configured: bool,
    mqtt_available: bool,
    mqtt_connected: bool,
    use_mqtt_for_events: bool,
    frigate_config: dict[str, Any] | None = None,
    frigate_review_probe: dict[str, Any] | None = None,
    llm_probe_timeout: float = 8.0,
) -> dict[str, Any]:
    """Build a JSON-serializable GenAI health snapshot."""
    llm = resolve_llm_settings(genai_report_config, frigate_config)
    llm_probe: dict[str, Any] | None = None
    if llm and not demo:
        llm_probe = await probe_llm(
            llm["base_url"],
            llm["model"],
            timeout=llm_probe_timeout,
        )

    frigate_genai: dict[str, Any] | None = None
    if frigate_config is not None:
        g = frigate_config.get("genai") if isinstance(frigate_config.get("genai"), dict) else {}
        if g:
            provider = g.get("provider") or g.get("provider_type") or ""
            frigate_genai = {
                "configured": True,
                "enabled": g.get("enabled", True) is not False,
                "provider": str(provider) if provider else None,
                "model": str(g.get("model") or "") or None,
                "base_url": str(g.get("base_url") or "").rstrip("/") or None,
            }
        else:
            frigate_genai = {"configured": False}

    ingestion = genai_ingestion_status(
        demo=demo,
        mqtt_configured=mqtt_configured,
        mqtt_available=mqtt_available,
        mqtt_connected=mqtt_connected,
        use_mqtt_for_events=use_mqtt_for_events,
    )
    activity = activity_stats(genai_activity, hours=1.0)
    status, status_color, summary = _overall_status(
        demo=demo,
        llm=llm,
        llm_probe=llm_probe,
        ingestion=ingestion,
        activity=activity,
        frigate_genai=frigate_genai,
        frigate_review_probe=frigate_review_probe,
    )

    return {
        "checked_at": time.time(),
        "status": status,
        "status_color": status_color,
        "summary": summary,
        "llm": llm,
        "llm_probe": llm_probe,
        "frigate_genai": frigate_genai,
        "frigate_review_probe": frigate_review_probe,
        "ingestion": ingestion,
        "activity": activity,
    }