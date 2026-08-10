"""Ollama runtime options — GPU inference only."""

from __future__ import annotations

from typing import Any

OLLAMA_GPU_LAYERS = -1


def build_ollama_gpu_options(*, main_gpu: int | None = None, **extra: Any) -> dict[str, Any]:
    opts: dict[str, Any] = {"num_gpu": OLLAMA_GPU_LAYERS}
    opts.update(extra)
    if main_gpu is not None and main_gpu >= 0:
        opts["main_gpu"] = int(main_gpu)
    return opts


def is_dedicated_ollama_instance(base_url: str) -> bool:
    url = (base_url or "").strip().lower().rstrip("/")
    if ":11434" in url or ":11435" in url:
        return True
    if "ollama-gpu0" in url:
        return True
    if "://ollama:" in url or url.endswith("/ollama"):
        return True
    return False


def main_gpu_from_config(
    cfg: dict[str, Any] | None, *, base_url: str = ""
) -> int | None:
    if not cfg:
        return None
    if is_dedicated_ollama_instance(base_url or str(cfg.get("base_url") or "")):
        return None
    raw = cfg.get("main_gpu")
    if raw is None or raw == "":
        return None
    try:
        gpu = int(raw)
    except (TypeError, ValueError):
        return None
    if gpu < 0:
        return None
    return max(0, min(15, gpu))