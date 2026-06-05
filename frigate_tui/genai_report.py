"""Generate narrative activity reports from GenAI messages via Ollama/OpenAI-compatible APIs."""

from __future__ import annotations

import re
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
    return f"""You are writing a detailed home security activity briefing for a homeowner.

Below are GenAI-generated messages from Frigate NVR cameras over the last {hours:g} hour(s), grouped by local hour (newest hour first). Each line includes camera name, object types, identified names (sub_labels), and review titles when present.

Write a clear, detailed, factual report using only simple Markdown (no code fences, no tables):
1. First line must be exactly: ## Overall
2. Then 2–4 sentences for the whole period. Name specific cameras, object types (person, car, dog, etc.), and identified people when the data includes them.
3. After Overall, write hour sections in the SAME order as the DATA (first hour line is the current/most recent hour).
4. For each hour, use a heading exactly like: ## 22:00 (24-hour clock matching the hour line in DATA, no date, no extra words).
5. Under each hour heading, write a detailed paragraph (3–6 sentences). Include which cameras had activity, what objects were detected, any named individuals, and notable patterns. Mention threat level only if non-zero.
6. Do not use ###, #, **, or bullet lists unless necessary. Do not invent cameras, objects, or people not in the DATA.

DATA:
{context}
"""


def normalize_hour_clock(value: str) -> str | None:
    """Extract HH:00 from hour_label, hour key, or LLM heading text."""
    if not value:
        return None
    m = re.search(r"(\d{1,2}):(\d{2})", str(value))
    if not m:
        return None
    return f"{int(m.group(1)):02d}:{m.group(2)}"


def _section_clock(sec: dict[str, Any]) -> str:
    return normalize_hour_clock(str(sec.get("hour_label", ""))) or normalize_hour_clock(
        str(sec.get("hour", ""))
    ) or str(sec.get("hour_label", "")).strip()


def reorder_report_markdown(report: str, sections: list[dict[str, Any]]) -> str:
    """Reorder LLM markdown: Overall first, then hour blocks newest-first (per sections)."""
    if not report or not sections:
        return report

    lines = report.replace("\r\n", "\n").split("\n")
    blocks: dict[str, list[str]] = {}
    current = "__overall__"
    blocks[current] = []

    for line in lines:
        m = re.match(r"^##\s+(.+?)\s*$", line.strip())
        if m:
            title = m.group(1).strip()
            if title.lower() == "overall":
                current = "__overall__"
            else:
                current = title
            blocks.setdefault(current, []).append(line)
        else:
            blocks.setdefault(current, []).append(line)

    hour_bodies: dict[str, list[str]] = {}
    for title, body in blocks.items():
        if title == "__overall__":
            continue
        clock = normalize_hour_clock(title)
        if not clock:
            continue
        content = list(body)
        if content and re.match(r"^##\s+", content[0].strip()):
            content = content[1:]
        while content and not content[0].strip():
            content.pop(0)
        if content:
            hour_bodies[clock] = content

    out: list[str] = []
    overall = blocks.get("__overall__", [])
    if overall:
        if not overall[0].strip().lower().startswith("## overall"):
            out.append("## Overall")
        out.extend(overall)
    else:
        out.append("## Overall")

    for sec in sections:
        clock = _section_clock(sec)
        if not clock or clock not in hour_bodies:
            continue
        if out and out[-1].strip():
            out.append("")
        out.append(f"## {clock}")
        out.extend(hour_bodies[clock])

    return "\n".join(out).strip()


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

    def _response_detail(resp: httpx.Response) -> str:
        try:
            body = resp.json()
            if isinstance(body, dict):
                if body.get("error"):
                    return str(body["error"])
                msg = (body.get("message") or {}).get("content")
                if msg and not str(msg).strip():
                    return "message.content empty"
        except Exception:
            pass
        text = (resp.text or "").strip()
        return text[:500] if text else "(empty body)"

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()
    except httpx.TimeoutException:
        return None, f"LLM timed out after {timeout:.0f}s — POST {url} (model {model})"
    except httpx.HTTPStatusError as e:
        detail = _response_detail(e.response)
        return None, f"LLM HTTP {e.response.status_code} — POST {url} (model {model}): {detail}"
    except httpx.RequestError as e:
        return None, f"LLM request error — POST {url} (model {model}): {type(e).__name__}: {e}"
    except Exception as e:
        return None, f"LLM request failed — POST {url} (model {model}): {type(e).__name__}: {e}"

    if isinstance(data, dict) and data.get("error"):
        return None, f"LLM error — POST {url} (model {model}): {data['error']}"

    content = (data.get("message") or {}).get("content")
    if not content or not str(content).strip():
        return None, f"LLM returned empty content — POST {url} (model {model})"
    report = reorder_report_markdown(str(content).strip(), sections)
    return report, None