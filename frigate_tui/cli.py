"""Typer CLI entrypoint for frigate-tui."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

import typer
import yaml
from dotenv import load_dotenv

from frigate_tui.app import FrigateMonitor

app = typer.Typer(
    name="frigate-tui",
    help="Advanced colorful TUI for monitoring your Frigate NVR (queues, detections, events).",
    add_completion=True,
    rich_markup_mode="rich",
)

# Load .env early if present (in project dir or parent)
load_dotenv(dotenv_path=Path.cwd() / ".env")
load_dotenv(dotenv_path=Path.home() / ".config" / "frigate-tui" / ".env", override=False)


def _load_yaml_config(path: Path | None) -> dict:
    """Load YAML config if the file exists."""
    if path is None:
        candidates = [
            Path.cwd() / "config.yaml",
            Path.home() / ".config" / "frigate-tui" / "config.yaml",
        ]
        for c in candidates:
            if c.exists():
                path = c
                break
    if path and path.exists():
        with open(path) as f:
            return yaml.safe_load(f) or {}
    return {}


@app.command()
def run(
    url: Optional[str] = typer.Option(
        None,
        "--url",
        "-u",
        envvar="FRIGATE_TUI_URL",
        help="Frigate base URL (e.g. http://localhost:5000 or http://192.168.1.100:5000)",
    ),
    interval: Optional[float] = typer.Option(
        None,
        "--interval",
        "-i",
        envvar="FRIGATE_TUI_INTERVAL",
        help="Poll interval in seconds for /api/stats (default 1.0)",
    ),
    config: Optional[Path] = typer.Option(
        None,
        "--config",
        "-c",
        exists=True,
        dir_okay=False,
        help="Path to config.yaml (otherwise auto-discovered)",
    ),
    demo: bool = typer.Option(
        False,
        "--demo",
        envvar="FRIGATE_TUI_DEMO",
        help="Run with synthetic demo data (no Frigate required)",
    ),
) -> None:
    """Launch the Frigate TUI monitor."""
    yaml_cfg = _load_yaml_config(config)

    final_url = url or yaml_cfg.get("frigate_url") or os.getenv("FRIGATE_TUI_URL") or "http://localhost:5000"
    final_interval = interval or yaml_cfg.get("poll_interval") or 1.0
    stats_log_interval = yaml_cfg.get("stats_log_interval", 10.0)
    max_events = yaml_cfg.get("max_events", 150)

    # Support common truthy values for FRIGATE_TUI_DEMO env var
    if not demo:
        demo_env = os.getenv("FRIGATE_TUI_DEMO", "").lower()
        demo = demo_env in ("1", "true", "yes", "on")

    # Minimal settings object for the app (will become a proper dataclass later)
    settings = {
        "frigate_url": str(final_url).rstrip("/"),
        "poll_interval": float(final_interval),
        "stats_log_interval": float(stats_log_interval),
        "max_events": int(max_events),
        "demo": demo,
        "mqtt": yaml_cfg.get("mqtt"),  # Optional MQTT configuration for real-time events
    }

    FrigateMonitor(settings).run()


@app.command()
def web(
    url: Optional[str] = typer.Option(
        None,
        "--url",
        "-u",
        envvar="FRIGATE_TUI_URL",
        help="Frigate base URL",
    ),
    interval: Optional[float] = typer.Option(
        None,
        "--interval",
        "-i",
        envvar="FRIGATE_TUI_INTERVAL",
        help="Poll interval (seconds)",
    ),
    config: Optional[Path] = typer.Option(
        None,
        "--config",
        "-c",
        exists=True,
        dir_okay=False,
        help="Path to config.yaml",
    ),
    demo: bool = typer.Option(
        False,
        "--demo",
        envvar="FRIGATE_TUI_DEMO",
        help="Run with synthetic demo data",
    ),
    host: str = typer.Option(
        "0.0.0.0",
        "--host",
        help="Host interface to bind (use 0.0.0.0 for LAN access)",
    ),
    port: int = typer.Option(
        8080,
        "--port",
        "-p",
        help="TCP port for the web UI",
    ),
) -> None:
    """Launch the web dashboard (same features as the TUI, served locally over HTTP)."""
    yaml_cfg = _load_yaml_config(config)

    final_url = url or yaml_cfg.get("frigate_url") or os.getenv("FRIGATE_TUI_URL") or "http://localhost:5000"
    final_interval = interval or yaml_cfg.get("poll_interval") or 1.0
    stats_log_interval = yaml_cfg.get("stats_log_interval", 10.0)
    max_events = yaml_cfg.get("max_events", 150)

    if not demo:
        demo_env = os.getenv("FRIGATE_TUI_DEMO", "").lower()
        demo = demo_env in ("1", "true", "yes", "on")

    settings = {
        "frigate_url": str(final_url).rstrip("/"),
        "poll_interval": float(final_interval),
        "stats_log_interval": float(stats_log_interval),
        "max_events": int(max_events),
        "demo": demo,
        "mqtt": yaml_cfg.get("mqtt"),
    }

    # Lazy import so that a plain `pip install frigate-tui` (TUI only) never pulls web deps.
    try:
        from frigate_tui.web.server import run_web
    except ImportError as exc:
        typer.secho(
            "Web dependencies not installed.",
            fg=typer.colors.RED,
            err=True,
        )
        typer.secho(
            "Run: pip install 'frigate-tui[web]'",
            fg=typer.colors.RED,
            err=True,
        )
        typer.secho(
            "If using Docker: docker compose --profile web up --build frigate-web",
            fg=typer.colors.YELLOW,
            err=True,
        )
        raise typer.Exit(code=1) from exc

    typer.echo(f"Starting Frigate web UI on http://{host}:{port} (target {settings['frigate_url']})")

    if host in ("0.0.0.0", "::"):
        # Help the user discover the LAN address
        import socket
        addrs = []
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            addrs.append(s.getsockname()[0])
            s.close()
        except Exception:
            pass
        try:
            hostname_ips = socket.gethostbyname_ex(socket.gethostname())[2]
            for ip in hostname_ips:
                if not ip.startswith("127.") and ip not in addrs:
                    addrs.append(ip)
        except Exception:
            pass

        if addrs:
            typer.echo("Accessible on your network at:")
            for a in addrs:
                typer.echo(f"  http://{a}:{port}")
        else:
            typer.echo(f"Try: http://<this-machine-lan-ip>:{port}")
    else:
        typer.echo(f"Open http://{host}:{port} in your browser")

    run_web(settings, host=host, port=port)


@app.command()
def version() -> None:
    """Show frigate-tui version."""
    from frigate_tui import __version__

    typer.echo(f"frigate-tui {__version__}")


def main() -> None:
    """Public entry point for the `frigate-tui` command.

    Makes bare invocation (`frigate-tui`, or `docker ... frigate-tui`) default
    to launching the monitor (equivalent to `frigate-tui run`).
    """
    known_top_level = {"run", "web", "version", "--help", "-h", "--version", "completion"}

    if len(sys.argv) <= 1 or sys.argv[1] not in known_top_level:
        sys.argv.insert(1, "run")

    app()


if __name__ == "__main__":
    main()
