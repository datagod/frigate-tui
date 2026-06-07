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


def _review_genai_eligible(raw: dict[str, Any], review_genai_cfg: dict[str, Any] | None) -> bool:
    """True if this review should receive Frigate GenAI metadata per config."""
    cfg = review_genai_cfg or {}
    if cfg.get("enabled", True) is False:
        return False
    severity = str(raw.get("severity") or "detection")
    if severity == "alert":
        return cfg.get("alerts", True) is not False
    return cfg.get("detections", False) is True


def frigate_review_genai_probe(
    reviews: list[dict[str, Any]],
    *,
    hours: float = 1.0,
    review_genai_cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """How many recent Frigate /api/review items include GenAI metadata."""
    now = time.time()
    cutoff = now - max(0.25, hours) * 3600.0
    recent = [r for r in reviews if float(r.get("start_time") or 0) >= cutoff]
    with_genai = 0
    completed = 0
    eligible_completed = 0
    eligible_with_genai = 0
    alerts_completed = 0
    alerts_with_genai = 0
    for raw in recent:
        has_genai = bool(genai_summary_from_review_data(raw.get("data") or {}))
        is_completed = raw.get("end_time") is not None
        if is_completed:
            completed += 1
        if has_genai:
            with_genai += 1
        if str(raw.get("severity") or "") == "alert":
            if is_completed:
                alerts_completed += 1
            if has_genai:
                alerts_with_genai += 1
        if _review_genai_eligible(raw, review_genai_cfg):
            if is_completed:
                eligible_completed += 1
                if has_genai:
                    eligible_with_genai += 1
    missing_eligible = max(0, eligible_completed - eligible_with_genai)
    success_rate: float | None = None
    if eligible_completed > 0:
        success_rate = round(eligible_with_genai / eligible_completed, 3)
    return {
        "reviews_1h": len(recent),
        "reviews_completed_1h": completed,
        "reviews_with_genai_1h": with_genai,
        "alerts_completed_1h": alerts_completed,
        "alerts_with_genai_1h": alerts_with_genai,
        "eligible_completed_1h": eligible_completed,
        "eligible_with_genai_1h": eligible_with_genai,
        "eligible_missing_genai_1h": missing_eligible,
        "genai_success_rate_1h": success_rate,
    }


def _extract_configured_num_ctx(frigate_config: dict[str, Any] | None) -> int | None:
    if not frigate_config:
        return None
    g = frigate_config.get("genai")
    if not isinstance(g, dict):
        return None
    opts = g.get("provider_options") or {}
    if not isinstance(opts, dict):
        return None
    inner = opts.get("options") or {}
    if not isinstance(inner, dict):
        return None
    raw = inner.get("num_ctx")
    try:
        return int(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


async def probe_ollama_context(
    base_url: str,
    model: str,
    *,
    configured_num_ctx: int | None = None,
    timeout: float = 8.0,
) -> dict[str, Any]:
    """Probe Ollama runtime context for Frigate's vision/review LLM."""
    url = base_url.rstrip("/")
    result: dict[str, Any] = {
        "reachable": False,
        "model_loaded": False,
        "loaded_context_length": None,
        "configured_num_ctx": configured_num_ctx,
        "context_status": "unknown",
        "context_summary": "Ollama context not checked",
        "error": None,
    }
    if not url or not model:
        result["context_status"] = "not_configured"
        result["context_summary"] = "Frigate GenAI base_url or model not set"
        return result

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(f"{url}/api/ps")
    except httpx.TimeoutException:
        result["context_status"] = "unreachable"
        result["context_summary"] = f"Ollama unreachable at {url}"
        result["error"] = f"timed out after {timeout:.0f}s"
        return result
    except httpx.RequestError as e:
        result["context_status"] = "unreachable"
        result["context_summary"] = f"Ollama unreachable at {url}"
        result["error"] = f"{type(e).__name__}: {e}"
        return result

    result["reachable"] = True
    if resp.status_code >= 400:
        result["context_status"] = "unreachable"
        result["context_summary"] = f"Ollama /api/ps returned HTTP {resp.status_code}"
        result["error"] = f"HTTP {resp.status_code}"
        return result

    try:
        data = resp.json()
    except Exception:
        result["context_status"] = "unknown"
        result["context_summary"] = "Ollama /api/ps returned invalid JSON"
        result["error"] = "invalid JSON"
        return result

    target = model.strip()
    target_base = target.split(":", 1)[0]
    loaded_ctx: int | None = None
    for entry in data.get("models") or []:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or entry.get("model") or "").strip()
        if not name:
            continue
        if name == target or name.startswith(f"{target}:") or target.startswith(f"{name}:"):
            result["model_loaded"] = True
            raw_ctx = entry.get("context_length")
            try:
                loaded_ctx = int(raw_ctx) if raw_ctx is not None else None
            except (TypeError, ValueError):
                loaded_ctx = None
            break
        if target_base and (name == target_base or name.startswith(f"{target_base}:")):
            result["model_loaded"] = True
            raw_ctx = entry.get("context_length")
            try:
                loaded_ctx = int(raw_ctx) if raw_ctx is not None else None
            except (TypeError, ValueError):
                loaded_ctx = None
            break

    result["loaded_context_length"] = loaded_ctx
    cfg = configured_num_ctx

    if not result["model_loaded"]:
        if cfg and cfg >= 16384:
            result["context_status"] = "ok"
            result["context_summary"] = (
                f"Model not loaded in Ollama; next Frigate request should use num_ctx={cfg:,}"
            )
        elif cfg:
            result["context_status"] = "too_small"
            result["context_summary"] = (
                f"Configured num_ctx={cfg:,} is likely too small for multi-image review summaries "
                "(recommend ≥32768)"
            )
        else:
            result["context_status"] = "unknown"
            result["context_summary"] = "Model not loaded; num_ctx not found in Frigate config"
        return result

    if loaded_ctx is None:
        result["context_status"] = "unknown"
        result["context_summary"] = "Model loaded but Ollama did not report context_length"
        return result

    if cfg and loaded_ctx < int(cfg * 0.9):
        result["context_status"] = "stale"
        result["context_summary"] = (
            f"Ollama has stale context: loaded {loaded_ctx:,} but Frigate config requests {cfg:,} "
            "(unload model or restart Ollama so GenAI uses the new limit)"
        )
        return result

    if loaded_ctx < 16384 or (cfg is not None and cfg < 16384):
        result["context_status"] = "too_small"
        effective = cfg if cfg is not None else loaded_ctx
        result["context_summary"] = (
            f"Context {effective:,} tokens is too small for 13–20 preview frames "
            "(typical failures at 15k–23k tokens; use num_ctx ≥32768)"
        )
        return result

    result["context_status"] = "ok"
    shown = f"loaded {loaded_ctx:,}"
    if cfg:
        shown += f" (configured {cfg:,})"
    result["context_summary"] = f"Ollama context OK — {shown}"
    return result


def diagnose_genai_service(
    *,
    frigate_genai: dict[str, Any] | None,
    ollama_context_probe: dict[str, Any] | None,
    frigate_review_probe: dict[str, Any] | None,
) -> dict[str, Any]:
    """Synthesize whether Frigate's vision LLM is likely failing on review summaries."""
    ctx = ollama_context_probe or {}
    probe = frigate_review_probe or {}
    fg = frigate_genai or {}

    eligible_done = int(probe.get("eligible_completed_1h") or 0)
    eligible_ok = int(probe.get("eligible_with_genai_1h") or 0)
    missing = int(probe.get("eligible_missing_genai_1h") or 0)
    rate = probe.get("genai_success_rate_1h")

    issues: list[str] = []
    status = "ok"
    status_color = "good"

    if not fg.get("configured"):
        return {
            "status": "unknown",
            "status_color": "dim",
            "summary": "Frigate GenAI not configured",
            "likely_context_overflow": False,
            "missing_genai_reviews_1h": missing,
            "issues": [],
        }

    ctx_status = str(ctx.get("context_status") or "unknown")
    if ctx_status == "unreachable":
        issues.append(f"Frigate Ollama unreachable ({fg.get('base_url') or '?'})")
        status = "error"
        status_color = "bad"
    elif ctx_status == "stale":
        issues.append(str(ctx.get("context_summary") or "Ollama context is stale"))
        status = "degraded"
        status_color = "warn"
    elif ctx_status == "too_small":
        issues.append(str(ctx.get("context_summary") or "num_ctx too small"))
        status = "error"
        status_color = "bad"

    context_unhealthy = ctx_status in ("too_small", "stale")
    likely_overflow = context_unhealthy
    if eligible_done >= 2 and missing > 0:
        if rate is not None and rate < 0.5:
            if context_unhealthy:
                issues.append(
                    f"LLM likely rejecting review requests: only {eligible_ok}/{eligible_done} eligible "
                    f"reviews got GenAI metadata in the last hour "
                    "(check Frigate logs for exceed_context_size_error)"
                )
                likely_overflow = True
            else:
                issues.append(
                    f"GenAI review failures: only {eligible_ok}/{eligible_done} eligible reviews "
                    f"got metadata in the last hour ({missing} missing — check Frigate logs)"
                )
            if status == "ok":
                status = "degraded"
            if status_color == "good":
                status_color = "warn"
        elif rate is not None and rate < 0.8:
            issues.append(
                f"Partial GenAI failures: {eligible_ok}/{eligible_done} eligible reviews succeeded "
                f"({missing} missing metadata)"
            )
            if status == "ok":
                status = "degraded"
            if status_color == "good":
                status_color = "warn"

    if not issues:
        summary = "Frigate vision LLM healthy"
        if eligible_done > 0 and rate is not None:
            summary += f" ({eligible_ok}/{eligible_done} review summaries in the last hour)"
    else:
        summary = "; ".join(issues)

    return {
        "status": status,
        "status_color": status_color,
        "summary": summary,
        "likely_context_overflow": likely_overflow,
        "missing_genai_reviews_1h": missing,
        "issues": issues,
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
    genai_service: dict[str, Any] | None = None,
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
            issues.append("Summary LLM unreachable")
            color = "bad"
        elif not llm_probe.get("model_listed"):
            issues.append("Summary LLM model not loaded")
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

    svc = genai_service or {}
    svc_color = str(svc.get("status_color") or "")
    if svc.get("issues"):
        for item in svc["issues"]:
            if item not in issues:
                issues.append(item)
        if svc_color == "bad":
            color = "bad"
        elif svc_color == "warn" and color == "good":
            color = "warn"

    if activity.get("messages_1h", 0) == 0 and not demo:
        probe = frigate_review_probe or {}
        eligible_done = int(probe.get("eligible_completed_1h") or 0)
        eligible_ok = int(probe.get("eligible_with_genai_1h") or 0)
        if eligible_done > 0 and eligible_ok == 0 and not svc.get("issues"):
            issues.append(
                "no GenAI messages in the last hour — eligible Frigate reviews lack metadata "
                "(check Frigate logs: Ollama timeouts or num_ctx too small for preview frames)"
            )
            if color == "good":
                color = "warn"
        elif int(probe.get("reviews_1h") or 0) > 0 and activity.get("messages_1h", 0) == 0:
            if not svc.get("issues"):
                issues.append("no GenAI messages stored in the last hour")
                if color == "good":
                    color = "warn"

    if not issues:
        svc_summary = str(svc.get("summary") or "").strip()
        if svc_summary and svc_summary != "Frigate vision LLM healthy":
            return "ok", color, svc_summary
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
    review_genai_cfg: dict[str, Any] | None = None
    configured_num_ctx: int | None = None
    if frigate_config is not None:
        g = frigate_config.get("genai") if isinstance(frigate_config.get("genai"), dict) else {}
        review_genai_cfg = (
            frigate_config.get("review", {}).get("genai")
            if isinstance(frigate_config.get("review"), dict)
            else None
        )
        if not isinstance(review_genai_cfg, dict):
            review_genai_cfg = None
        configured_num_ctx = _extract_configured_num_ctx(frigate_config)
        objects_genai_cfg = (
            frigate_config.get("objects", {}).get("genai")
            if isinstance(frigate_config.get("objects"), dict)
            else None
        )
        if not isinstance(objects_genai_cfg, dict):
            objects_genai_cfg = None
        if g:
            provider = g.get("provider") or g.get("provider_type") or ""
            frigate_genai = {
                "configured": True,
                "enabled": g.get("enabled", True) is not False,
                "provider": str(provider) if provider else None,
                "model": str(g.get("model") or "") or None,
                "base_url": str(g.get("base_url") or "").rstrip("/") or None,
                "num_ctx": configured_num_ctx,
                "objects_genai_enabled": (
                    objects_genai_cfg.get("enabled", False) is True if objects_genai_cfg else False
                ),
                "objects_use_snapshot": (
                    objects_genai_cfg.get("use_snapshot", False) is True if objects_genai_cfg else False
                ),
                "review_alerts": (
                    review_genai_cfg.get("alerts", True) is not False if review_genai_cfg else True
                ),
                "review_detections": (
                    review_genai_cfg.get("detections", False) is True if review_genai_cfg else False
                ),
                "review_image_source": (
                    str(review_genai_cfg.get("image_source") or "")
                    if review_genai_cfg and review_genai_cfg.get("image_source")
                    else None
                ),
            }
        else:
            frigate_genai = {"configured": False}

    ollama_context_probe: dict[str, Any] | None = None
    if frigate_genai and frigate_genai.get("configured") and not demo:
        base = frigate_genai.get("base_url") or ""
        model = frigate_genai.get("model") or ""
        if base and model:
            ollama_context_probe = await probe_ollama_context(
                base,
                model,
                configured_num_ctx=configured_num_ctx,
                timeout=llm_probe_timeout,
            )

    genai_service = diagnose_genai_service(
        frigate_genai=frigate_genai,
        ollama_context_probe=ollama_context_probe,
        frigate_review_probe=frigate_review_probe,
    )

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
        genai_service=genai_service,
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
        "ollama_context_probe": ollama_context_probe,
        "genai_service": genai_service,
        "ingestion": ingestion,
        "activity": activity,
    }