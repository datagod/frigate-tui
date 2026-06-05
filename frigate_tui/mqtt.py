"""Async MQTT client for subscribing to Frigate events in real time."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any, AsyncGenerator, Callable

import aiomqtt


@dataclass
class FrigateMqttEvent:
    """Normalized event received over MQTT."""

    type: str  # "new", "update", or "end"
    id: str
    camera: str
    label: str
    start_time: float
    end_time: float | None = None
    top_score: float | None = None
    has_snapshot: bool = False
    has_clip: bool = False
    zones: list[str] | None = None
    raw: dict[str, Any] | None = None  # full original payload for advanced use


class FrigateMqttClient:
    """
    Async MQTT client that subscribes to Frigate events.

    Example usage:
        async with FrigateMqttClient("localhost") as client:
            async for event in client.events():
                print(event)
    """

    def __init__(
        self,
        host: str,
        port: int = 1883,
        username: str | None = None,
        password: str | None = None,
        topic_prefix: str = "frigate",
        client_id: str = "frigate-tui",
    ) -> None:
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.topic_prefix = topic_prefix.rstrip("/")
        self.client_id = client_id
        self._client: aiomqtt.Client | None = None
        self._connected = asyncio.Event()

    async def __aenter__(self) -> FrigateMqttClient:
        await self.connect()
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.disconnect()

    async def connect(self) -> None:
        """Connect to the MQTT broker."""
        tls_context = None  # Add TLS support later if needed

        self._client = aiomqtt.Client(
            hostname=self.host,
            port=self.port,
            username=self.username,
            password=self.password,
            identifier=self.client_id,
            tls_context=tls_context,
        )

        await self._client.__aenter__()
        self._connected.set()

        # Subscribe to the main events topic
        # Frigate publishes to "frigate/events"
        topic = f"{self.topic_prefix}/events"
        await self._client.subscribe(topic)
        # Also subscribe to camera-specific events for broader coverage
        await self._client.subscribe(f"{self.topic_prefix}/+/events")

        # Subscribe to tracked object updates (used for GenAI/LLM descriptions, sub-labels, etc.)
        await self._client.subscribe(f"{self.topic_prefix}/tracked_object_update")
        await self._client.subscribe(f"{self.topic_prefix}/+/tracked_object_update")

        # Review updates (GenAI summaries land in after.data.metadata on update messages)
        await self._client.subscribe(f"{self.topic_prefix}/reviews")

    async def disconnect(self) -> None:
        """Disconnect from the MQTT broker."""
        if self._client:
            try:
                await self._client.__aexit__(None, None, None)
            except Exception:
                pass
            self._client = None
        self._connected.clear()

    async def messages(self) -> AsyncGenerator[dict[str, Any], None]:
        """
        Async generator that yields raw MQTT payloads (with _topic attached)
        from all subscribed topics (events, tracked_object_update for GenAI, etc.).
        """
        if not self._client:
            raise RuntimeError("MQTT client is not connected. Call connect() first.")

        async for message in self._client.messages:
            try:
                payload = json.loads(message.payload.decode())
                payload["_topic"] = str(message.topic)
                yield payload
            except Exception:
                # Ignore malformed messages
                continue

    async def events(self) -> AsyncGenerator[FrigateMqttEvent, None]:
        """
        Async generator that yields Frigate events as they arrive over MQTT (legacy).
        """
        if not self._client:
            raise RuntimeError("MQTT client is not connected. Call connect() first.")

        async for payload in self.messages():
            try:
                if "events" in payload.get("_topic", ""):
                    event = self._parse_event(payload)
                    if event:
                        yield event
            except Exception:
                continue

    def _parse_event(self, payload: dict[str, Any]) -> FrigateMqttEvent | None:
        """Convert a raw Frigate MQTT message into FrigateMqttEvent."""
        try:
            event_type = payload.get("type", "new")

            # Frigate sends "before" and "after" objects for updates
            event_data = payload.get("after") or payload.get("before") or payload

            if not event_data:
                return None

            event_id = event_data.get("id") or payload.get("id", "")
            camera = event_data.get("camera", "unknown")
            label = event_data.get("label", "object")

            start_time = float(event_data.get("start_time", 0))
            end_time = event_data.get("end_time")
            if end_time is not None:
                end_time = float(end_time)

            return FrigateMqttEvent(
                type=event_type,
                id=str(event_id) if event_id else "",
                camera=camera,
                label=label,
                start_time=start_time,
                end_time=end_time,
                top_score=event_data.get("top_score"),
                has_snapshot=bool(event_data.get("has_snapshot")),
                has_clip=bool(event_data.get("has_clip")),
                zones=event_data.get("zones") or [],
                raw=payload,
            )
        except Exception:
            return None

    async def wait_until_connected(self, timeout: float = 10.0) -> bool:
        """Wait until the client is connected (useful for startup)."""
        try:
            await asyncio.wait_for(self._connected.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            return False
