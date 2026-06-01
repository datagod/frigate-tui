# Frigate TUI

A colorful, advanced terminal user interface for monitoring your [Frigate](https://github.com/blakeblackshear/frigate) NVR in real time.

Watch camera FPS, detector queues / pressure, GPU usage, incoming events, and system health directly from your terminal (or tmux).

![screenshot placeholder](https://via.placeholder.com/800x400/1a1a2e/00d4ff?text=Frigate+TUI+Screenshot)

## Features

- Live updating dashboard (1s poll for stats)
- **Real-time events via MQTT** (optional but recommended — no polling for new detections)
- **Queues & Health**: Derived detection pressure, skipped frames, inference speed — the signals that matter when the pipeline backs up
- Per-camera view with colorful unicode FPS bars (camera / process / detection)
- Live events feed with object-type color coding (person, car, dog, cat…)
- GPU, storage, uptime, and detector metrics
- Keyboard-first navigation, clean error states, auto-retry
- Works great over SSH and inside tmux

## Requirements

- Python 3.10+
- A running Frigate instance (0.13+ recommended) reachable over HTTP
- Modern terminal (truecolor support recommended: Kitty, WezTerm, iTerm2, Windows Terminal, etc.)

## Quick Start

```bash
# Install (editable + dev tools for hot reload)
pip install -e ".[dev]"

# Run with live reload + debug console (F2)
textual run --dev frigate_tui.app:FrigateMonitor

# Or the installed command
frigate-tui
```

Override the URL:

```bash
FRIGATE_TUI_URL=http://your-frigate-host:5000 frigate-tui
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
