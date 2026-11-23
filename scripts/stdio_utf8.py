"""UTF-8 для stdout/stderr в фоновых задачах на Windows (лог jobs)."""

from __future__ import annotations

import os
import sys


def configure_utf8_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError, AttributeError):
            pass


def subprocess_env() -> dict[str, str]:
    from pathlib import Path

    from dotenv import load_dotenv

    env = os.environ.copy()
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    root = Path(__file__).resolve().parents[1]
    load_dotenv(root / ".env", override=True)
    for key in (
        "COMFYUI_URL",
        "COMFYUI_ROOT",
        "COMFYUI_LTX_CKPT",
        "COMFYUI_T5_CLIP",
        "COMFYUI_WORKFLOW",
        "HF_TOKEN",
    ):
        val = os.getenv(key)
        if val:
            env[key] = val
    return env