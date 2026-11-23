"""Минимальный клиент API ComfyUI для image-to-video."""

from __future__ import annotations

import json
import os
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

import requests

try:
    import imageio_ffmpeg
except ImportError:
    imageio_ffmpeg = None  # type: ignore[assignment]

DEFAULT_URL = "http://127.0.0.1:8188"
POLL_INTERVAL_SEC = 2.0


def comfyui_base_url() -> str:
    import os
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    return os.getenv("COMFYUI_URL", DEFAULT_URL).rstrip("/")


def is_comfyui_online(url: str | None = None, timeout: float = 3.0) -> bool:
    base = (url or comfyui_base_url()).rstrip("/")
    try:
        response = requests.get(f"{base}/system_stats", timeout=timeout)
        return response.status_code == 200
    except requests.RequestException:
        return False


def load_workflow_template(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if "workflow" in data and isinstance(data["workflow"], dict):
        return data["workflow"]
    return {k: v for k, v in data.items() if k != "_meta"}


def workflow_meta(path: Path) -> dict[str, Any]:
    raw = path.read_text(encoding="utf-8-sig")
    try:
        data = json.loads(raw)
        meta = data.get("_meta")
        return meta if isinstance(meta, dict) else {}
    except json.JSONDecodeError:
        import re

        match = re.search(
            r'"_meta"\s*:\s*(\{(?:[^{}]|\{[^{}]*\})*\})',
            raw,
            flags=re.DOTALL,
        )
        if not match:
            return {}
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            return {}


def build_workflow_prompt(
    template_path: Path,
    *,
    image_name: str,
    prompt: str,
    seed: int,
    width: int,
    height: int,
    num_frames: int,
    ckpt_name: str = "",
    steps: int = 20,
    cfg: float = 3.0,
    strength: float | None = None,
) -> dict[str, Any]:
    raw = template_path.read_text(encoding="utf-8-sig")
    prompt_escaped = json.dumps(prompt, ensure_ascii=False)[1:-1]
    meta = {}
    try:
        meta = json.loads(raw).get("_meta", {})
    except json.JSONDecodeError:
        pass
    defaults = meta.get("defaults") if isinstance(meta.get("defaults"), dict) else {}
    if not ckpt_name:
        ckpt_name = os.getenv(
            "COMFYUI_LTX_CKPT",
            str(defaults.get("ckpt_name", "ltx-video-2b-v0.9.8.safetensors")),
        )
    clip_name = os.getenv(
        "COMFYUI_T5_CLIP",
        str(defaults.get("clip_name", "t5xxl_fp16.safetensors")),
    )
    if strength is None:
        strength = float(
            os.getenv(
                "COMFYUI_I2V_STRENGTH",
                str(defaults.get("strength", 0.92)),
            )
        )
    replacements = {
        "IMAGE": image_name,
        "PROMPT": prompt_escaped,
        "SEED": str(seed),
        "WIDTH": str(width),
        "HEIGHT": str(height),
        "NUM_FRAMES": str(num_frames),
        "CKPT_NAME": ckpt_name,
        "CLIP_NAME": clip_name,
        "STEPS": str(steps),
        "CFG": str(cfg),
        "STRENGTH": str(strength),
    }
    for key, value in replacements.items():
        raw = raw.replace(f"{{{{{key}}}}}", value)
    data = json.loads(raw)
    if "workflow" in data and isinstance(data["workflow"], dict):
        return data["workflow"]
    return {k: v for k, v in data.items() if k != "_meta"}


def upload_image(base_url: str, image_path: Path) -> str:
    with image_path.open("rb") as handle:
        response = requests.post(
            f"{base_url}/upload/image",
            files={"image": (image_path.name, handle, "application/octet-stream")},
            data={"overwrite": "true"},
            timeout=120,
        )
    response.raise_for_status()
    payload = response.json()
    name = str(payload.get("name") or image_path.name)
    subfolder = str(payload.get("subfolder") or "").strip()
    if subfolder:
        return f"{subfolder}/{name}"
    return name


def queue_prompt(base_url: str, workflow: dict[str, Any], client_id: str | None = None) -> str:
    client_id = client_id or uuid.uuid4().hex
    response = requests.post(
        f"{base_url}/prompt",
        json={"prompt": workflow, "client_id": client_id},
        timeout=60,
    )
    if response.status_code >= 400:
        detail = response.text.strip()[:2000]
        try:
            err_payload = response.json()
            if err_payload.get("node_errors"):
                detail = json.dumps(err_payload, ensure_ascii=False)[:2000]
        except ValueError:
            pass
        raise requests.HTTPError(
            f"{response.status_code} {response.reason} for url: {response.url}\n{detail}",
            response=response,
        )
    payload = response.json()
    prompt_id = payload.get("prompt_id")
    if not prompt_id:
        raise RuntimeError(f"ComfyUI /prompt: нет prompt_id: {payload}")
    return str(prompt_id)


def _history_entry(base_url: str, prompt_id: str) -> dict[str, Any] | None:
    response = requests.get(f"{base_url}/history/{prompt_id}", timeout=30)
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict):
        return None
    return data.get(prompt_id)


def wait_for_prompt(
    base_url: str,
    prompt_id: str,
    *,
    timeout_sec: float = 10800.0,
    poll: float = POLL_INTERVAL_SEC,
) -> dict[str, Any]:
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        entry = _history_entry(base_url, prompt_id)
        if entry:
            status = entry.get("status") or {}
            if status.get("status_str") == "error":
                messages = status.get("messages") or entry
                raise RuntimeError(f"ComfyUI ошибка: {messages}")
            outputs = entry.get("outputs")
            if outputs:
                return entry
        time.sleep(poll)
    raise TimeoutError(f"ComfyUI: таймаут ожидания prompt_id={prompt_id}")


def _iter_output_files(outputs: dict[str, Any]) -> list[dict[str, str]]:
    found: list[dict[str, str]] = []
    for node_out in outputs.values():
        if not isinstance(node_out, dict):
            continue
        for key in ("gifs", "videos", "images"):
            items = node_out.get(key) or []
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, dict):
                    continue
                filename = str(item.get("filename") or "")
                if not filename:
                    continue
                if filename.lower().endswith((".mp4", ".webm", ".gif", ".webp")) or key in (
                    "gifs",
                    "videos",
                    "images",
                ):
                    found.append(
                        {
                            "filename": filename,
                            "subfolder": str(item.get("subfolder") or ""),
                            "type": str(item.get("type") or "output"),
                        }
                    )
    return found


def download_output_file(base_url: str, file_info: dict[str, str], dest: Path) -> None:
    params = {
        "filename": file_info["filename"],
        "subfolder": file_info.get("subfolder") or "",
        "type": file_info.get("type") or "output",
    }
    response = requests.get(f"{base_url}/view", params=params, timeout=300)
    response.raise_for_status()
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(response.content)


def webp_to_mp4(source: Path, dest: Path, *, fps: float = 24.0) -> None:
    """Comfy SaveAnimatedWEBP часто не читается ffmpeg напрямую — кадры через Pillow."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    frames: list[Any] = []
    try:
        from PIL import Image
        import numpy as np

        with Image.open(source) as handle:
            index = 0
            while True:
                try:
                    handle.seek(index)
                except EOFError:
                    break
                frames.append(np.asarray(handle.convert("RGB")))
                index += 1
    except Exception as exc:
        raise RuntimeError(f"webp: не удалось прочитать кадры ({source.name}): {exc}") from exc
    if not frames:
        raise RuntimeError(f"webp: нет кадров в {source.name}")

    try:
        import imageio.v3 as iio

        iio.imwrite(
            dest,
            frames,
            fps=fps,
            codec="libx264",
            pixelformat="yuv420p",
            ffmpeg_params=["-movflags", "+faststart"],
        )
        return
    except Exception as exc:
        last_error = exc

    if imageio_ffmpeg is None:
        raise RuntimeError(f"webp -> mp4: imageio не сработал ({last_error})")
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    command = [
        ffmpeg,
        "-y",
        "-i",
        str(source),
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        str(dest),
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "")[:800]
        raise RuntimeError(f"webp->mp4: {last_error}; ffmpeg: {detail}")


def run_i2v_and_download(
    base_url: str,
    workflow: dict[str, Any],
    dest_mp4: Path,
    *,
    preferred_node_ids: list[str] | None = None,
    timeout_sec: float = 10800.0,
) -> Path:
    prompt_id = queue_prompt(base_url, workflow)
    entry = wait_for_prompt(base_url, prompt_id, timeout_sec=timeout_sec)
    outputs = entry.get("outputs") or {}
    if preferred_node_ids:
        narrowed = {k: outputs[k] for k in preferred_node_ids if k in outputs}
        if narrowed:
            outputs = narrowed
    files = _iter_output_files(outputs)
    if not files:
        files = _iter_output_files(entry.get("outputs") or {})
    if not files:
        raise RuntimeError("ComfyUI: в outputs нет видеофайла")
    mp4_candidates = [f for f in files if f["filename"].lower().endswith(".mp4")]
    pick = mp4_candidates[0] if mp4_candidates else files[-1]
    if pick["filename"].lower().endswith(".webp"):
        temp_webp = dest_mp4.with_suffix(".webp")
        download_output_file(base_url, pick, temp_webp)
        webp_to_mp4(temp_webp, dest_mp4, fps=24.0)
        try:
            temp_webp.unlink(missing_ok=True)
        except OSError:
            pass
    else:
        download_output_file(base_url, pick, dest_mp4)
    return dest_mp4


def motion_prompt_suffix() -> str:
    return "slow subtle cinematic motion, gentle breathing light, calm atmosphere"