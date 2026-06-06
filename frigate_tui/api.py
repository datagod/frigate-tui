"""Async HTTP client for the Frigate REST API."""

from __future__ import annotations

from typing import Any

import httpx


class FrigateClient:
    """Small async client for Frigate.

    Usage:
        async with FrigateClient("http://localhost:5000") as client:
            stats = await client.get_stats()
            events = await client.get_events(limit=30)
    """

    def __init__(self, base_url: str, timeout: float = 6.0) -> None:
        self.base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": "frigate-tui/0.1"},
        )

    async def __aenter__(self) -> FrigateClient:
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _get(self, path: str, **params: Any) -> Any:
        url = f"{self.base_url}{path}"
        resp = await self._client.get(url, params=params or None)
        resp.raise_for_status()
        if resp.headers.get("content-type", "").startswith("application/json"):
            return resp.json()
        return resp.text.strip()

    async def _post_json(
        self,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> Any:
        url = f"{self.base_url}{path}"
        resp = await self._client.post(url, json=json_body or {}, timeout=timeout)
        resp.raise_for_status()
        if resp.headers.get("content-type", "").startswith("application/json"):
            return resp.json()
        return resp.text.strip()

    async def get_version(self) -> str:
        try:
            return await self._get("/api/version")
        except Exception:
            return "unknown"

    async def get_stats(self) -> dict[str, Any]:
        return await self._get("/api/stats")

    async def get_events(self, *, limit: int = 30, after: float | None = None) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"limit": limit}
        if after is not None:
            params["after"] = after
        data = await self._get("/api/events", **params)
        return data if isinstance(data, list) else []

    async def get_config(self) -> dict[str, Any]:
        return await self._get("/api/config")

    async def get_review_items(self, *, limit: int = 30, has_been_reviewed: bool | None = None) -> list[dict[str, Any]]:
        """Fetch review items. These are higher-level 'things that need attention'."""
        params: dict[str, Any] = {"limit": limit}
        if has_been_reviewed is not None:
            params["has_been_reviewed"] = has_been_reviewed
        data = await self._get("/api/review", **params)
        return data if isinstance(data, list) else []

    async def get_event(self, event_id: str) -> dict[str, Any] | None:
        """Fetch details for a single event."""
        try:
            return await self._get(f"/api/events/{event_id}")
        except Exception:
            return None

    async def get_timeline(self, *, limit: int = 30, after: float | None = None, source: str | None = None) -> list[dict[str, Any]]:
        """Fetch recent timeline entries (fine-grained tracked object visible/gone etc.)."""
        params: dict[str, Any] = {"limit": limit}
        if after is not None:
            params["after"] = after
        if source:
            params["source"] = source
        data = await self._get("/api/timeline", **params)
        return data if isinstance(data, list) else []

    async def get_snapshot(self, event_id: str, **params: Any) -> bytes:
        """Fetch snapshot image for an event (binary JPEG).

        Supports Frigate query params such as bbox=1, crop=1, quality=80, etc.
        """
        url = f"{self.base_url}/api/events/{event_id}/snapshot.jpg"
        resp = await self._client.get(url, params=params or None)
        resp.raise_for_status()
        return resp.content

    async def summarize_reviews(
        self,
        *,
        start_ts: float,
        end_ts: float,
        timeout: float = 180.0,
    ) -> dict[str, Any]:
        """Frigate GenAI: narrative report for suspicious review items in a time window."""
        path = f"/api/review/summarize/start/{start_ts}/end/{end_ts}"
        data = await self._post_json(path, json_body={}, timeout=timeout)
        return data if isinstance(data, dict) else {"success": False, "summary": str(data)}

    async def get_clip(self, event_id: str) -> bytes:
        """Fetch the clip (mp4) for an event, if available."""
        url = f"{self.base_url}/api/events/{event_id}/clip.mp4"
        resp = await self._client.get(url)
        resp.raise_for_status()
        return resp.content
