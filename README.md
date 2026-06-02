# Frigate TUI

A colorful, advanced terminal user interface **and web dashboard** for monitoring your [Frigate](https://github.com/blakeblackshear/frigate) NVR in real time.

Both the TUI and the optional web UI share the exact same core logic (`FrigateMonitorCore`) for stats, health calculations, event handling, MQTT, activity logging, and demo mode — guaranteeing full feature parity.

Watch camera FPS, detector queues / pressure, GPU usage, incoming events, and system health directly from your terminal (or tmux), or in a browser over your local network.

![screenshot placeholder](https://via.placeholder.com/800x400/1a1a2e/00d4ff?text=Frigate+TUI+Screenshot)

## Features

- Live updating dashboard (1s poll for stats) — available in both **TUI** and **Web**
- **Real-time events via MQTT** (optional but recommended — no polling for new detections)
- **Queues & Health**: Derived detection pressure, skipped frames, inference speed — the signals that matter when the pipeline backs up
- Per-camera view with colorful unicode FPS bars (camera / process / detection)
- Live events feed with object-type color coding (person, car, dog, cat…)
- GPU, storage, uptime, and detector metrics
- **Web dashboard** (optional): full feature parity in the browser, LAN-accessible, with proxied snapshots/clips and live SSE updates
- Keyboard-first navigation (TUI), clean error states, auto-retry
- Works great over SSH and inside tmux (TUI); or over the local network in any browser (Web)

## Requirements

- Python 3.10+
- A running Frigate instance (0.13+ recommended) reachable over HTTP
- Modern terminal (truecolor support recommended: Kitty, WezTerm, iTerm2, Windows Terminal, etc.) — for the TUI
- For the **Web UI**: `pip install "frigate-tui[web]"` (pulls in FastAPI + Uvicorn)

## Quick Start

```bash
# Install (editable + dev tools for hot reload)
pip install -e ".[dev]"

# Run the TUI with live reload + debug console (F2)
textual run --dev frigate_tui.app:FrigateMonitor

# Or the installed command
frigate-tui
```

### Web Dashboard

```bash
# Install with web extras
pip install "frigate-tui[web]"

# Run the web UI (default: http://0.0.0.0:8080 — accessible on your LAN)
frigate-tui web

# Demo mode (no Frigate needed)
frigate-tui web --demo
```

The terminal will print the LAN-accessible URLs.

Override the URL (same as TUI):

```bash
FRIGATE_TUI_URL=http://your-frigate-host:5000 frigate-tui web --port 8080
```

## Running with Docker (Simple "It Just Works" Mode)

The compose file is configured so that this is usually all you need:

```bash
# Build once (or after code changes)
docker compose build

# Run the TUI
docker compose run --rm frigate-tui
```

The default `docker-compose.yml` uses `network_mode: "container:frigate"` and talks to Frigate on `localhost`. This works great if you have a container named `frigate` running on the same machine.

### If your Frigate container has a different name

Edit `docker-compose.yml` and change:

```yaml
network_mode: "container:frigate"
```

to the actual name of your container, or run with an override:

```bash
docker compose run --rm --network container:your-frigate-container frigate-tui
```

Once the TUI starts, look at the **right-hand Activity Log** for connection status and live activity.

### Demo mode (no Frigate required)

```bash
# Easiest way
DEMO=1 docker compose run --rm frigate-tui

# Alternative (explicit flag)
docker compose run --rm frigate-tui frigate-tui --demo
```

### Passing other options

```bash
# Force a specific Frigate URL
FRIGATE_TUI_URL=http://192.168.1.100:5000 docker compose run --rm frigate-tui

# Or pass flags directly
docker compose run --rm frigate-tui frigate-tui -u http://192.168.1.100:5000
```

### Advanced / One-off usage

If you need to point at a different Frigate instance:

```bash
# Different host
FRIGATE_TUI_URL=http://192.168.1.50:5000 docker compose run --rm frigate-tui

# Or run without compose
docker build -t frigate-tui .
docker run -it --rm \
  --network container:frigate \
  -e FRIGATE_TUI_URL=http://localhost:5000 \
  frigate-tui
```

## Web Interface (Browser Dashboard)

A full web UI with **exact feature parity** to the TUI is available on the same branch.

It re-uses the identical core (`FrigateMonitorCore`) for stats, health/pressure calculations, event list maintenance (dedup, ordering, MQTT vs poll), activity log messages, review/timeline surfacing, connection state, and demo mode. The result is the same numbers, same log lines, and same behavior — just rendered in a browser.

### Install & Run

```bash
# Install the web extras (adds fastapi + uvicorn + jinja2)
pip install "frigate-tui[web]"

# Run the web dashboard (binds on all interfaces by default)
frigate-tui web

# Or with overrides
FRIGATE_TUI_URL=http://192.168.1.50:5000 frigate-tui web --port 8765

# Demo (no Frigate needed)
frigate-tui web --demo
```

Then open the address shown in the terminal (it will print the LAN-accessible URLs when you bind to `0.0.0.0`).

Example output:
```
Starting Frigate web UI on http://0.0.0.0:8080 (target http://localhost:5000)
Accessible on your network at:
  http://192.168.1.42:8080
```

The UI has the same four tabs (Overview / Cameras with FPS bars / Events / Health), the persistent Activity Log, top summary strip, and connection status. Live updates arrive via SSE. Camera cards, color coding, health pressure, and every log message you see in the TUI appear here too.

Keyboard shortcuts that make sense in a browser (r, c, 1-4, ?) are supported. Click an event row to see a snapshot preview + clip link (when Frigate has the media).

### Docker (web profile)

The web service needs the optional `[web]` dependencies and cannot share `network_mode: container:frigate` (Docker doesn't allow port publishing with container network mode).

```bash
# Build the image with web dependencies included + start
docker compose --profile web up --build frigate-web

# Demo mode
DEMO=1 docker compose --profile web up --build frigate-web
```

**First time / after changing extras**: Always include `--build` (or run `docker compose --profile web build frigate-web`).

The compose file sets `FRIGATE_TUI_URL=http://host.docker.internal:5000` by default (with `extra_hosts` for Linux compatibility). 

If Frigate isn't reachable that way, override it:

```bash
FRIGATE_TUI_URL=http://192.168.1.42:5000 docker compose --profile web up --build frigate-web
```

Access the UI from other machines on your network using the Docker host's LAN IP + port (default 8080), e.g. `http://192.168.1.42:8080`.

### Why a web UI?

- **Exact same features** as the TUI, with zero duplication of the important logic (powered by the shared `FrigateMonitorCore`).
- Works over the local network without SSH/tmux — just open a browser.
- Snapshots and clips are **proxied** through the web server, so they load even if your browser can't directly reach Frigate (common in Docker setups).
- Live updates via SSE (stats, events, activity log, connection status).
- Easy snapshot previews + clip links when clicking events.
- Multiple viewers can watch the same dashboard simultaneously.
- Same configuration, MQTT support, demo mode, and health/queue calculations.

The TUI remains the primary interactive terminal experience (great over SSH); the web dashboard is a first-class peer for LAN/browser access. Both are fully supported.

## Configuration


Priority (highest wins):

1. CLI flags (`--url`, `--interval`)
2. Environment variables (`FRIGATE_TUI_URL`, `FRIGATE_TUI_INTERVAL`, …)
3. `~/.config/frigate-tui/config.yaml` or local `./config.yaml`
4. Built-in defaults (`http://localhost:5000`, 1.0s interval)

See `config.example.yaml` for the full schema.

### Real-time Events via MQTT (Recommended)

Instead of polling, you can subscribe to Frigate events over MQTT for near-instant updates.

Add this to your config:

```yaml
mqtt:
  host: "localhost"
  port: 1883
  username: ""
  password: ""
```

When enabled, the Activity Log will say **"MQTT connected — receiving events in real time"**.

## Key Bindings

| Key     | Action                  |
|---------|-------------------------|
| `q`     | Quit                    |
| `r`     | Force refresh           |
| `1-4`   | Switch tabs             |
| `Tab`   | Cycle focus             |
| `?`     | Help / key legend       |
| `F2`    | Textual dev console (dev mode) |

## Color Meaning

- **Green** — Healthy / on target
- **Yellow** — Warning / moderate pressure
- **Red** — Critical (skipped frames, detection stalled, high queue pressure)
- **Object labels**: person (red), car (cyan), dog (gold), cat (purple)

## Development

```bash
# Hot-reloading development
textual run --dev frigate_tui.app:FrigateMonitor

# Run tests (when added)
pytest

# Format & lint
ruff check --fix .
ruff format .
```

## Roadmap / Nice-to-Haves

- Optional MQTT push path (lower latency, less polling)
- Snapshot thumbnail previews in event detail (Pillow + sixel/kitty protocol)
- Review items tab (higher-signal than raw events)
- Simple `--demo` mode with synthetic data
- Docker image for "attach and run TUI inside container"

## License

MIT — do whatever you want with it.
