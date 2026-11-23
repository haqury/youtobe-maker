"""Подготовка ComfyUI AMD (модель LTX 2B + опционально VHS)."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_COMFY_ROOT = Path(r"C:\Users\haqury\comfyui-amd-windows-setup\ComfyUI")
DEFAULT_AMD_SETUP = Path(r"C:\Users\haqury\comfyui-amd-windows-setup")

LTX_REPO = "Lightricks/LTX-Video"
# Полный distilled для CheckpointLoaderSimple (+ VAE). fp8 — только UNET, для API не подходит.
LTX_CKPT = os.getenv(
    "COMFYUI_LTX_CKPT",
    "ltx-video-2b-v0.9.5.safetensors",
)
T5_REPO = "comfyanonymous/flux_text_encoders"
T5_CLIP = os.getenv("COMFYUI_T5_CLIP", "t5xxl_fp16.safetensors")
VHS_REPO = "https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite"


def comfy_root() -> Path:
    raw = os.getenv("COMFYUI_ROOT", str(DEFAULT_COMFY_ROOT)).strip()
    return Path(raw).expanduser().resolve()


def check_ckpt(path: Path) -> bool:
    return path.is_file() and path.stat().st_size > 100_000_000


def _hf_download(repo_id: str, filename: str, local_dir: Path) -> Path:
    from huggingface_hub import hf_hub_download

    token = os.getenv("HF_TOKEN") or os.getenv("HUGGING_FACE_HUB_TOKEN")
    downloaded = hf_hub_download(
        repo_id=repo_id,
        filename=filename,
        local_dir=str(local_dir),
        local_dir_use_symlinks=False,
        token=token,
    )
    return Path(downloaded)


def download_t5(text_encoders_dir: Path) -> Path:
    text_encoders_dir.mkdir(parents=True, exist_ok=True)
    target = text_encoders_dir / T5_CLIP
    if check_ckpt(target):
        print(f"Уже есть T5: {target}")
        return target
    print(f"Скачиваю {T5_CLIP} в {text_encoders_dir}…")
    path = _hf_download(T5_REPO, T5_CLIP, text_encoders_dir)
    print(f"Готово: {path}")
    return path


def download_ltx(checkpoints_dir: Path) -> Path:
    target = checkpoints_dir / LTX_CKPT
    if check_ckpt(target):
        print(f"Уже есть: {target}")
        return target
    print(f"Скачиваю {LTX_CKPT} (~6+ GB) в {checkpoints_dir}…")
    path = _hf_download(LTX_REPO, LTX_CKPT, checkpoints_dir)
    print(f"Готово: {path}")
    return path


def install_vhs(custom_nodes: Path) -> None:
    dest = custom_nodes / "ComfyUI-VideoHelperSuite"
    if dest.is_dir():
        print(f"VHS уже установлен: {dest}")
        return
    print("Клонирую ComfyUI-VideoHelperSuite…")
    subprocess.run(
        ["git", "clone", "--depth", "1", VHS_REPO, str(dest)],
        check=True,
    )
    req = dest / "requirements.txt"
    if req.is_file():
        subprocess.run([sys.executable, "-m", "pip", "install", "-r", str(req)], check=False)
    print(f"Готово: {dest}")


def patch_env_example() -> None:
    env_path = ROOT / ".env"
    lines = []
    if env_path.is_file():
        lines = env_path.read_text(encoding="utf-8").splitlines()
    updates = {
        "COMFYUI_URL": "http://127.0.0.1:8188",
        "COMFYUI_ROOT": str(comfy_root()),
        "COMFYUI_LTX_CKPT": LTX_CKPT,
        "COMFYUI_T5_CLIP": T5_CLIP,
    }
    for key, value in updates.items():
        replaced = False
        for index, line in enumerate(lines):
            if line.startswith(f"{key}="):
                lines[index] = f"{key}={value}"
                replaced = True
                break
        if not replaced:
            lines.append(f"{key}={value}")
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Обновлён {env_path}")


def main(argv: list[str] | None = None) -> int:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    global LTX_CKPT
    LTX_CKPT = os.getenv("COMFYUI_LTX_CKPT", LTX_CKPT)
    parser = argparse.ArgumentParser(description="Настройка ComfyUI + LTX для youtobe-maker")
    parser.add_argument("--skip-download", action="store_true", help="Не качать чекпоинт")
    parser.add_argument("--skip-vhs", action="store_true", help="Не ставить VideoHelperSuite")
    args = parser.parse_args(argv)

    root = comfy_root()
    if not (root / "main.py").is_file():
        print(f"ComfyUI не найден: {root}", file=sys.stderr)
        print("Задайте COMFYUI_ROOT или установите comfyui-amd-windows-setup.", file=sys.stderr)
        return 1

    checkpoints = root / "models" / "checkpoints"
    checkpoints.mkdir(parents=True, exist_ok=True)
    custom_nodes = root / "custom_nodes"
    custom_nodes.mkdir(parents=True, exist_ok=True)

    if not args.skip_vhs:
        try:
            install_vhs(custom_nodes)
        except subprocess.CalledProcessError as exc:
            print(f"VHS не установлен: {exc}", file=sys.stderr)

    text_encoders = root / "models" / "text_encoders"
    if not args.skip_download:
        try:
            download_t5(text_encoders)
        except Exception as exc:
            print(f"Не удалось скачать T5: {exc}", file=sys.stderr)
            print(f"Положите {T5_CLIP} в {text_encoders}", file=sys.stderr)
        try:
            download_ltx(checkpoints)
        except Exception as exc:
            print(f"Не удалось скачать LTX: {exc}", file=sys.stderr)
            print(
                "Скачайте вручную "
                f"{LTX_CKPT} в {checkpoints}",
                file=sys.stderr,
            )

    patch_env_example()
    launch = DEFAULT_AMD_SETUP / "scripts" / "launch.py"
    print()
    print("Запуск ComfyUI:")
    if launch.is_file():
        print(f"  cd /d {DEFAULT_AMD_SETUP}")
        print("  python scripts\\launch.py")
    else:
        print(f"  cd /d {root}")
        print("  python main.py --directml --fp32 --listen 0.0.0.0 --port 8188")
    print()
    print("Проверка: http://127.0.0.1:8188/system_stats")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())