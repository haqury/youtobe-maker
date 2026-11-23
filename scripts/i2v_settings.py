"""Настройки ComfyUI image-to-video и оценка длительности."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

I2V_PRESETS: dict[str, dict[str, int | float]] = {
    "fast": {"width": 512, "height": 288, "num_frames": 17, "steps": 15, "cfg": 3.0, "strength": 0.9},
    "balance": {"width": 768, "height": 432, "num_frames": 49, "steps": 20, "cfg": 3.0, "strength": 0.92},
    "quality": {"width": 768, "height": 432, "num_frames": 73, "steps": 25, "cfg": 3.0, "strength": 0.95},
}

DEFAULT_I2V: dict[str, Any] = {
    "preset": "balance",
    "ckpt_name": "ltx-video-2b-v0.9.5.safetensors",
    "clip_name": "t5xxl_fp16.safetensors",
    "width": 768,
    "height": 432,
    "num_frames": 49,
    "steps": 20,
    "cfg": 3.0,
    "strength": 0.92,
    "timeout_sec": 10800,
}

BASELINE_SEC_CPU = 25 * 60
BASELINE_PIXELS = 768 * 432
BASELINE_FRAMES = 49
BASELINE_STEPS = 20
GPU_TIME_FACTOR = 0.12


def comfy_models_root() -> Path:
    raw = os.getenv("COMFYUI_ROOT", r"C:\Users\haqury\comfyui-amd-windows-setup\ComfyUI").strip()
    return Path(raw).expanduser()


def list_checkpoint_names() -> list[str]:
    folder = comfy_models_root() / "models" / "checkpoints"
    if not folder.is_dir():
        return [str(DEFAULT_I2V["ckpt_name"])]
    names = sorted(path.name for path in folder.glob("*.safetensors") if path.is_file())
    return names or [str(DEFAULT_I2V["ckpt_name"])]


def list_clip_names() -> list[str]:
    for sub in ("text_encoders", "clip"):
        folder = comfy_models_root() / "models" / sub
        if folder.is_dir():
            names = sorted(path.name for path in folder.glob("*.safetensors") if path.is_file())
            if names:
                return names
    return [str(DEFAULT_I2V["clip_name"])]


def normalize_i2v_settings(raw: object | None) -> dict[str, Any]:
    out = dict(DEFAULT_I2V)
    if isinstance(raw, dict):
        for key in DEFAULT_I2V:
            if key in raw and raw[key] is not None:
                out[key] = raw[key]
    preset = str(out.get("preset") or "balance").strip().lower()
    if preset in I2V_PRESETS and preset != "custom":
        out.update(I2V_PRESETS[preset])
        out["preset"] = preset
    try:
        out["width"] = int(out["width"])
        out["height"] = int(out["height"])
        out["num_frames"] = int(out["num_frames"])
        out["steps"] = int(out["steps"])
        out["cfg"] = float(out["cfg"])
        out["strength"] = float(out["strength"])
        out["timeout_sec"] = int(out["timeout_sec"])
    except (TypeError, ValueError):
        return dict(DEFAULT_I2V)
    out["ckpt_name"] = str(out.get("ckpt_name") or DEFAULT_I2V["ckpt_name"])
    out["clip_name"] = str(out.get("clip_name") or DEFAULT_I2V["clip_name"])
    return out


def apply_preset(preset: str) -> dict[str, int | float]:
    return dict(I2V_PRESETS.get(preset, I2V_PRESETS["balance"]))


def detect_comfy_cpu_mode() -> bool:
    try:
        import requests

        from comfyui_client import comfyui_base_url

        response = requests.get(f"{comfyui_base_url().rstrip('/')}/system_stats", timeout=3)
        if response.status_code != 200:
            return True
        payload = response.json()
        argv = payload.get("system", {}).get("argv") or []
        if "--cpu" in argv:
            return True
        devices = payload.get("devices") or []
        if devices and str(devices[0].get("type", "")).lower() == "cpu":
            return True
    except Exception:
        pass
    return True


def estimate_clip_seconds(
    *,
    width: int,
    height: int,
    num_frames: int,
    steps: int,
    cpu_mode: bool | None = None,
) -> float:
    if cpu_mode is None:
        cpu_mode = detect_comfy_cpu_mode()
    scale = (width * height) / BASELINE_PIXELS
    scale *= num_frames / BASELINE_FRAMES
    scale *= steps / BASELINE_STEPS
    sec = BASELINE_SEC_CPU * scale
    if not cpu_mode:
        sec *= GPU_TIME_FACTOR
    return max(60.0, sec)


def format_duration(sec: float) -> str:
    sec = int(round(sec))
    if sec < 120:
        return f"~{sec} сек"
    minutes = sec // 60
    if minutes < 120:
        return f"~{minutes} мин"
    hours = minutes // 60
    rem = minutes % 60
    if rem:
        return f"~{hours} ч {rem} мин"
    return f"~{hours} ч"


def estimate_total_label(settings: dict[str, Any], clip_count: int) -> str:
    if clip_count <= 0:
        return "Выберите хотя бы один кадр."
    one = estimate_clip_seconds(
        width=int(settings["width"]),
        height=int(settings["height"]),
        num_frames=int(settings["num_frames"]),
        steps=int(settings["steps"]),
    )
    total = one * clip_count
    mode = "CPU" if detect_comfy_cpu_mode() else "GPU"
    return (
        f"Оценка: **{format_duration(total)}** на **{clip_count}** кадр(а) "
        f"({format_duration(one)} × {clip_count}, режим **{mode}**). "
        f"Точность ±30%."
    )


def animate_frames_cli_args(settings: dict[str, Any]) -> list[str]:
    s = normalize_i2v_settings(settings)
    return [
        "--ckpt",
        s["ckpt_name"],
        "--clip",
        s["clip_name"],
        "--width",
        str(s["width"]),
        "--height",
        str(s["height"]),
        "--frames",
        str(s["num_frames"]),
        "--steps",
        str(s["steps"]),
        "--cfg",
        str(s["cfg"]),
        "--strength",
        str(s["strength"]),
        "--timeout",
        str(s["timeout_sec"]),
    ]