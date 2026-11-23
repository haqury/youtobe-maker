"""Локальная генерация кадров: stable-diffusion.cpp (Vulkan) + GGUF в models/image/."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from PIL import Image

_SCRIPTS = Path(__file__).resolve().parent
ROOT = _SCRIPTS.parent
SD_EXE = ROOT / "tools" / "sd" / "sd.exe"
MODELS_DIR = ROOT / "models" / "image"
DEFAULT_MODEL = "flux1-schnell-q4_0.gguf"
FRAME_WIDTH = 1920
FRAME_HEIGHT = 1080
THUMB_WIDTH = 1280
THUMB_HEIGHT = 720
SD_STEPS = "4"


def list_local_sd_models() -> list[str]:
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    names = sorted(p.name for p in MODELS_DIR.glob("*.gguf"))
    if names:
        return names
    return [DEFAULT_MODEL]


def resolve_model_path(model_name: str) -> Path:
    return MODELS_DIR / model_name


def local_sd_status(*, model_name: str | None = None) -> dict[str, object]:
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    SD_EXE.parent.mkdir(parents=True, exist_ok=True)
    name = model_name or DEFAULT_MODEL
    model_path = resolve_model_path(name)
    exe_ok = SD_EXE.is_file()
    model_ok = model_path.is_file()
    return {
        "exe_ok": exe_ok,
        "model_ok": model_ok,
        "ready": exe_ok and model_ok,
        "exe_path": SD_EXE,
        "model_path": model_path,
        "model_name": name,
    }


def print_local_sd_setup_help(model_name: str | None = None) -> None:
    st = local_sd_status(model_name=model_name)
    print(
        "[local_sd] Локальный генератор не готов.\n"
        f"  1) Положите stable-diffusion.cpp (Vulkan) в: {st['exe_path']}\n"
        f"  2) Скачайте GGUF-модель (например {DEFAULT_MODEL}) в: {MODELS_DIR}\n"
        "  Документация: https://github.com/leejet/stable-diffusion.cpp",
        file=sys.stderr,
        flush=True,
    )


def _run_sd_once(
    prompt: str,
    output_path: Path,
    *,
    model_path: Path,
    width: int,
    height: int,
) -> bool:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    for extra_vulkan in (["-v"], []):
        cmd = [
            str(SD_EXE),
            "-m",
            str(model_path),
            "-p",
            prompt[:2048],
            "-o",
            str(output_path),
            "-W",
            str(width),
            "-H",
            str(height),
            "--steps",
            SD_STEPS,
            *extra_vulkan,
        ]
        try:
            result = subprocess.run(
                cmd,
                cwd=str(ROOT),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=900,
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            print(f"[local_sd] subprocess: {exc}", file=sys.stderr)
            return False
        if result.returncode == 0 and output_path.is_file() and output_path.stat().st_size > 1024:
            return True
        err = (result.stderr or result.stdout or "")[:400]
        if extra_vulkan:
            continue
        print(f"[local_sd] sd.exe код {result.returncode}: {err}", file=sys.stderr)
    return False


def _to_jpeg_bytes(path: Path, *, width: int, height: int) -> bytes | None:
    try:
        img = Image.open(path).convert("RGB")
        if img.size != (width, height):
            img = img.resize((width, height), Image.Resampling.LANCZOS)
        import io

        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=92, optimize=True)
        if path.suffix.lower() not in {".jpg", ".jpeg"}:
            jpg_path = path.with_suffix(".jpg")
            path.unlink(missing_ok=True)
            jpg_path.write_bytes(buf.getvalue())
        return buf.getvalue()
    except OSError as exc:
        print(f"[local_sd] конвертация: {exc}", file=sys.stderr)
        return None


def generate_local_sd_batch(
    items: list[tuple[int, str]],
    image_dir: Path,
    *,
    model_name: str,
) -> dict[int, bytes]:
    """Одна пачка: все кадры подряд в этом вызове (без повторной загрузки Python-скрипта)."""
    st = local_sd_status(model_name=model_name)
    if not st["ready"]:
        print_local_sd_setup_help(model_name)
        return {}
    model_path = Path(str(st["model_path"]))
    out: dict[int, bytes] = {}
    total = len(items)
    print(f"[local_sd] Пачка {total} кадров · {model_name}", flush=True)
    for index, (number, prompt) in enumerate(items, start=1):
        stem = f"{number:02d}"
        tmp = image_dir / f"_sd_tmp_{stem}.png"
        print(f"[local_sd] ({index}/{total}) {stem}", flush=True)
        if not _run_sd_once(prompt, tmp, model_path=model_path, width=FRAME_WIDTH, height=FRAME_HEIGHT):
            tmp.unlink(missing_ok=True)
            continue
        jpeg = _to_jpeg_bytes(tmp, width=FRAME_WIDTH, height=FRAME_HEIGHT)
        if jpeg:
            out[number] = jpeg
        tmp.unlink(missing_ok=True)
    print(f"[local_sd] Готово кадров: {len(out)}/{total}", flush=True)
    return out


def fetch_one_local_sd(prompt: str, model_name: str, *, width: int = THUMB_WIDTH, height: int = THUMB_HEIGHT) -> bytes | None:
    st = local_sd_status(model_name=model_name)
    if not st["ready"]:
        print_local_sd_setup_help(model_name)
        return None
    model_path = Path(str(st["model_path"]))
    tmp = MODELS_DIR / "_thumbnail_sd_tmp.png"
    if not _run_sd_once(prompt, tmp, model_path=model_path, width=width, height=height):
        tmp.unlink(missing_ok=True)
        return None
    data = _to_jpeg_bytes(tmp, width=width, height=height)
    tmp.unlink(missing_ok=True)
    return data