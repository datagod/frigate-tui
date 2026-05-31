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

# Install the package (normal install is correct and faster for containers).
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir .

# Copy the remaining supporting files (compose, example config, docs, etc.)
COPY config.example.yaml docker-compose.yml ./

# Default to running the TUI.
# The CLI automatically treats bare invocation as `frigate-tui run`,
# so `docker compose run --rm frigate-tui` (and `docker run ... frigate-tui`)
# launch the monitor directly. Pass --demo or other options as usual.
CMD ["frigate-tui"]
