"""Stream Docker container logs for the Frigate Logs web tab."""

from __future__ import annotations

import struct
from pathlib import Path
from typing import Any, AsyncIterator

import httpx

_DOCKER_API = "http://docker"


def frigate_logs_settings_from_config(raw: dict[str, Any] | None) -> dict[str, Any]:
    """Normalize frigate_logs config with defaults."""
    cfg = dict(raw or {})
    return {
        "enabled": bool(cfg.get("enabled", True)),
        "container": str(cfg.get("container", "frigate")).strip() or "frigate",
        "initial_lines": max(1, min(2000, int(cfg.get("initial_lines", 200)))),
        "docker_socket": str(cfg.get("docker_socket", "/var/run/docker.sock")).strip()
        or "/var/run/docker.sock",
    }


def docker_socket_available(socket_path: str) -> bool:
    return Path(socket_path).exists()


def _docker_client(*, docker_socket: str, timeout: float | None = 10.0) -> httpx.AsyncClient:
    transport = httpx.AsyncHTTPTransport(uds=docker_socket)
    return httpx.AsyncClient(transport=transport, timeout=timeout)


async def inspect_container(container: str, *, docker_socket: str) -> dict[str, Any]:
    """Return container metadata or an error dict."""
    if not docker_socket_available(docker_socket):
        return {
            "ok": False,
            "error": f"Docker socket not found at {docker_socket}",
            "socket": docker_socket,
        }
    try:
        async with _docker_client(docker_socket=docker_socket) as client:
            response = await client.get(f"{_DOCKER_API}/containers/{container}/json")
            if response.status_code == 404:
                return {
                    "ok": False,
                    "error": f"Container '{container}' not found",
                    "container": container,
                }
            response.raise_for_status()
            data = response.json()
    except httpx.HTTPError as e:
        return {"ok": False, "error": str(e), "container": container}
    state = data.get("State") or {}
    config = data.get("Config") or {}
    return {
        "ok": True,
        "id": str(data.get("Id") or "")[:12],
        "name": str(data.get("Name") or "").lstrip("/") or container,
        "state": state.get("Status"),
        "running": bool(state.get("Running")),
        "image": config.get("Image"),
        "container": container,
    }


class _DockerLogDecoder:
    """Decode Docker's multiplexed log stream into complete text lines."""

    def __init__(self) -> None:
        self._frame_buf = bytearray()
        self._line_buf = ""
        self._raw_mode: bool | None = None

    def feed(self, chunk: bytes) -> list[str]:
        if not chunk:
            return []
        if self._raw_mode is None:
            self._raw_mode = chunk[0] not in (1, 2)
        if self._raw_mode:
            text = chunk.decode("utf-8", errors="replace")
            return self._split_lines(text)
        self._frame_buf.extend(chunk)
        lines: list[str] = []
        while len(self._frame_buf) >= 8:
            stream_type = self._frame_buf[0]
            if stream_type not in (1, 2):
                self._raw_mode = True
                text = bytes(self._frame_buf).decode("utf-8", errors="replace")
                self._frame_buf.clear()
                lines.extend(self._split_lines(text))
                break
            size = struct.unpack(">I", self._frame_buf[4:8])[0]
            if len(self._frame_buf) < 8 + size:
                break
            payload = bytes(self._frame_buf[8 : 8 + size])
            del self._frame_buf[: 8 + size]
            lines.extend(self._split_lines(payload.decode("utf-8", errors="replace")))
        return lines

    def flush(self) -> list[str]:
        if self._line_buf:
            line = self._line_buf
            self._line_buf = ""
            return [line]
        return []

    def _split_lines(self, text: str) -> list[str]:
        self._line_buf += text
        parts = self._line_buf.split("\n")
        self._line_buf = parts.pop()
        return parts


async def stream_container_logs(
    container: str,
    *,
    docker_socket: str,
    initial_lines: int = 200,
) -> AsyncIterator[str]:
    """Yield log lines oldest-first, starting with the last N then following new output."""
    if not docker_socket_available(docker_socket):
        raise FileNotFoundError(f"Docker socket not found at {docker_socket}")

    decoder = _DockerLogDecoder()
    params = {
        "stdout": "1",
        "stderr": "1",
        "timestamps": "1",
        "tail": str(initial_lines),
        "follow": "1",
    }
    async with _docker_client(docker_socket=docker_socket, timeout=None) as client:
        async with client.stream(
            "GET",
            f"{_DOCKER_API}/containers/{container}/logs",
            params=params,
        ) as response:
            if response.status_code == 404:
                raise LookupError(f"Container '{container}' not found")
            response.raise_for_status()
            async for chunk in response.aiter_bytes():
                for line in decoder.feed(chunk):
                    yield line
    for line in decoder.flush():
        yield line