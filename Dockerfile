# Frigate TUI - Advanced colorful terminal monitor for Frigate NVR
# Designed to be run interactively: docker compose run --rm frigate-tui

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TERM=xterm-256color \
    COLORTERM=truecolor

# Install system dependencies needed for a good terminal experience
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy metadata + the actual package source **before** running pip install.
# The pyproject.toml declares `readme = "README.md"`, so README.md must exist.
# The frigate_tui/ package directory must also be present for the wheel to contain code.
COPY pyproject.toml README.md ./
COPY frigate_tui ./frigate_tui

# Allow optional extras (e.g. web) to be installed via build arg.
# Usage in compose: build with args: { INSTALL_EXTRAS: "[web]" }
ARG INSTALL_EXTRAS=""
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir .${INSTALL_EXTRAS}

# Copy the remaining supporting files (compose, example config, docs, etc.)
COPY config.example.yaml docker-compose.yml ./

# Default to running the TUI.
# The CLI automatically treats bare invocation as `frigate-tui run`,
# so `docker compose run --rm frigate-tui` (and `docker run ... frigate-tui`)
# launch the monitor directly. Pass --demo or other options as usual.
#
# For the web UI (feature parity, served on LAN):
#   docker compose --profile web up --build frigate-web
#   (the --build is needed the first time to install the [web] extras)
#
# GenAI/LLM Activity Log lines need mqtt in config.yaml; TUI compose uses
# network_mode: container:frigate so host "mqtt" works; web must reach the broker separately.
#
#   or for a one-off container:
#   docker run -p 8080:8080 ... frigate-tui frigate-tui web --host 0.0.0.0 --port 8080
CMD ["frigate-tui"]
