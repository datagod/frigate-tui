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
