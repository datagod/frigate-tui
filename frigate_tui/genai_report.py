"""Generate narrative activity reports from GenAI messages via Ollama/OpenAI-compatible APIs."""

from __future__ import annotations

from typing import Any

import httpx

from frigate_tui.genai_activity import format_messages_for_prompt


def resolve_llm_settings(
    genai_report_cfg: dict[str, Any] | None,
    frigate_config: dict[str, Any] | None = None,
) -> dict[str, str] | None:
    """Merge genai_report config with Frigate's global genai provider when needed."""
    cfg = dict(genai_report_cfg or {})
    if frigate_config:
        g = frigate_config.get("genai") or {}
        if not cfg.get("base_url") and g.get("base_url"):
            cfg["base_url"] = str(g["base_url"]).rstrip("/")
        if not cfg.get("model") and g.get("model"):
            cfg["model"] = str(g["model"])
    base = (cfg.get("base_url") or "").strip().rstrip("/")
    model = (cfg.get("model") or "").strip()
    if not base or not model:
        return None
    return {"base_url": base, "model": model}


def build_summary_prompt(hours: float, context: str) -> str:
    return f"""You are writing a home security activity briefing for a homeowner.

Below are GenAI-generated messages from Frigate NVR cameras over the last {hours:g} hour(s), grouped by local hour.

Write a clear, factual report using only simple Markdown (no code fences, no tables):
1. First line must be exactly: ## Overall
2. Then one short paragraph (plain text, no bullet list) for the whole period.
3. For each hour that has messages, use a heading exactly like: ## 22:00 (24-hour clock from the HOUR lines, no extra words).
4. Under each hour heading, one compact paragraph (2–4 sentences) summarizing activity and cameras. Mention threat only if non-zero.
5. Do not use ###, #, **, or bullet lists unless necessary. Do not invent events.

DATA:
{context}
"""


async def generate_activity_report(
    *,
    base_url: str,
    model: str,
    sections: list[dict[str, Any]],
    hours: float,
    timeout: float = 180.0,
) -> tuple[str | None, str | None]:
    """Call Ollama /api/chat. Returns (markdown_report, error_message)."""
    if not sections:
        return None, "No GenAI messages in the selected time window."

    context = format_messages_for_prompt(sections)
    if not context:
        return None, "No GenAI messages in the selected time window."

    prompt = build_summary_prompt(hours, context)
    url = f"{base_url.rstrip('/')}/api/chat"
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
    }

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()
    except httpx.TimeoutException:
        return None, f"LLM request timed out after {timeout:.0f}s"
    except httpx.HTTPStatusError as e:
        return None, f"LLM HTTP {e.response.status_code}: {e.response.text[:200]}"
    except Exception as e:
        return None, f"LLM request failed: {e}"

    content = (data.get("message") or {}).get("content")
    if not content or not str(content).strip():
        return None, "LLM returned an empty response"
    return str(content).strip(), None