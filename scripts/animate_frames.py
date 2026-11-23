"""Image-to-Video выбранных кадров через локальный ComfyUI."""

from __future__ import annotations

import argparse
import os
import random
import re
import sys
from pathlib import Path

from dotenv import load_dotenv

_SCRIPTS = Path(__file__).resolve().parent
ROOT = _SCRIPTS.parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from comfyui_client import (
    build_workflow_prompt,
    comfyui_base_url,
    is_comfyui_online,
    motion_prompt_suffix,
    run_i2v_and_download,
    workflow_meta,
)
from episode_review import load_review, normalize_animate_frames
from generate_images import extract_prompts
from i2v_settings import estimate_total_label, normalize_i2v_settings
from job_progress import emit_progress
from stdio_utf8 import configure_utf8_stdio

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
STEM_RE = re.compile(r"^(\d{2})")
DEFAULT_WORKFLOW = ROOT / "config" / "workflows" / "ltx_video_i2v.json"


def resolve_image(images_dir: Path, stem: str) -> Path | None:
    for suffix in IMAGE_SUFFIXES:
        candidate = images_dir / f"{stem}{suffix}"
        if candidate.is_file():
            return candidate
    return None


def prompt_for_stem(prompts: list[str], index: int) -> str:
    if 0 <= index - 1 < len(prompts):
        return prompts[index - 1]
    return "calm cinematic scene, warm soft light, detailed textures, 16:9, no text"


def main(argv: list[str] | None = None) -> int:
    configure_utf8_stdio()
    load_dotenv(ROOT / ".env", override=True)
    parser = argparse.ArgumentParser(description="Оживить выбранные кадры через ComfyUI")
    parser.add_argument("episode", type=Path)
    parser.add_argument("--ckpt", default="", help="Имя checkpoint в ComfyUI/models/checkpoints")
    parser.add_argument("--clip", default="", help="Имя T5 в text_encoders или clip")
    parser.add_argument("--width", type=int, default=0)
    parser.add_argument("--height", type=int, default=0)
    parser.add_argument("--frames", type=int, default=0)
    parser.add_argument("--steps", type=int, default=0)
    parser.add_argument("--cfg", type=float, default=0.0)
    parser.add_argument("--strength", type=float, default=-1.0)
    parser.add_argument("--timeout", type=float, default=0.0, help="Секунд ждать ComfyUI на кадр")
    args = parser.parse_args(argv)
    episode_dir = args.episode.expanduser().resolve()
    if not episode_dir.is_dir():
        print(f"Папка не найдена: {episode_dir}", file=sys.stderr)
        return 1

    base_url = comfyui_base_url()
    if not is_comfyui_online(base_url, timeout=3.0):
        print(
            f"[ERROR] ComfyUI не запущен ({base_url}). "
            "В студии: ▶️ Запустить ComfyUI, дождитесь 🟢 онлайн.",
            file=sys.stderr,
        )
        return 1

    review = load_review(episode_dir)
    review_i2v = normalize_i2v_settings(review.get("animate_i2v"))

    ckpt_name = args.ckpt.strip() or str(review_i2v.get("ckpt_name")) or os.getenv(
        "COMFYUI_LTX_CKPT", "ltx-video-2b-v0.9.5.safetensors"
    )
    if ckpt_name.endswith("-fp8.safetensors") or "distilled-fp8" in ckpt_name:
        print(
            "[ERROR] COMFYUI_LTX_CKPT указывает на fp8 (только UNET). "
            "Поставьте ltxv-2b-0.9.8-distilled.safetensors + t5xxl_fp16.safetensors: "
            "python scripts/setup_comfyui_ltx.py",
            file=sys.stderr,
        )
        return 1

    comfy_root = Path(os.getenv("COMFYUI_ROOT", r"C:\Users\haqury\comfyui-amd-windows-setup\ComfyUI"))
    ckpt_path = comfy_root / "models" / "checkpoints" / ckpt_name
    clip_name = args.clip.strip() or str(review_i2v.get("clip_name")) or os.getenv(
        "COMFYUI_T5_CLIP", "t5xxl_fp16.safetensors"
    )
    clip_path = comfy_root / "models" / "text_encoders" / clip_name
    if not clip_path.is_file():
        clip_path = comfy_root / "models" / "clip" / clip_name
    if not ckpt_path.is_file():
        print(f"[ERROR] Нет чекпоинта LTX: {ckpt_path}", file=sys.stderr)
        return 1
    if not clip_path.is_file():
        print(
            f"[ERROR] Нет T5 для LTX: {clip_path}. "
            "Запустите: python scripts/setup_comfyui_ltx.py",
            file=sys.stderr,
        )
        return 1

    workflow_path = Path(
        os.getenv("COMFYUI_WORKFLOW", str(DEFAULT_WORKFLOW))
    ).expanduser()
    if not workflow_path.is_file():
        print(f"[ERROR] Нет workflow: {workflow_path}", file=sys.stderr)
        return 1

    meta = workflow_meta(workflow_path)
    defaults = meta.get("defaults") if isinstance(meta.get("defaults"), dict) else {}
    width = args.width or int(review_i2v.get("width")) or int(
        os.getenv("COMFYUI_I2V_WIDTH", str(defaults.get("width", 768)))
    )
    height = args.height or int(review_i2v.get("height")) or int(
        os.getenv("COMFYUI_I2V_HEIGHT", str(defaults.get("height", 432)))
    )
    num_frames = args.frames or int(review_i2v.get("num_frames")) or int(
        os.getenv("COMFYUI_I2V_FRAMES", str(defaults.get("num_frames", 49)))
    )
    steps = args.steps or int(review_i2v.get("steps")) or int(defaults.get("steps", 20))
    cfg = args.cfg if args.cfg > 0 else float(review_i2v.get("cfg", defaults.get("cfg", 3.0)))
    strength = (
        args.strength
        if args.strength >= 0
        else float(review_i2v.get("strength", defaults.get("strength", 0.92)))
    )
    timeout_sec = (
        args.timeout
        or float(review_i2v.get("timeout_sec"))
        or float(os.getenv("COMFYUI_I2V_TIMEOUT", "10800"))
    )

    images_dir = episode_dir / "images"
    if not images_dir.is_dir():
        print(f"[ERROR] Нет images/: {images_dir}", file=sys.stderr)
        return 1

    stems = normalize_animate_frames(review.get("animate_frames"))

    prompts_path = episode_dir / "visual_prompts.md"
    prompts: list[str] = []
    if prompts_path.is_file():
        prompts = extract_prompts(prompts_path.read_text(encoding="utf-8-sig"))

    targets = [stem for stem in stems if resolve_image(images_dir, stem)]
    if not targets:
        print(
            "[ERROR] Нет кадров для анимации: отметьте 🎞️ на кадрах или выберите в списке "
            "«Кадры для AI-анимации» и снова нажмите «Оживить».",
            file=sys.stderr,
        )
        return 1

    total = len(targets)
    settings_for_eta = {
        "width": width,
        "height": height,
        "num_frames": num_frames,
        "steps": steps,
    }
    print(estimate_total_label(settings_for_eta, total).replace("**", ""), file=sys.stderr)
    emit_progress(0, total, "ComfyUI: старт image-to-video")
    output_nodes = meta.get("output_node_ids")
    preferred = [str(x) for x in output_nodes] if isinstance(output_nodes, list) else None

    for idx, stem in enumerate(targets, start=1):
        emit_progress(idx - 1, total, f"Анимирую кадр {stem} через ComfyUI")
        image_path = resolve_image(images_dir, stem)
        if image_path is None:
            continue
        try:
            from comfyui_client import upload_image

            uploaded_name = upload_image(base_url, image_path)
        except Exception as exc:
            print(f"[ERROR] Загрузка {image_path.name}: {exc}", file=sys.stderr)
            return 1

        index = int(stem)
        base_prompt = prompt_for_stem(prompts, index)
        full_prompt = f"{base_prompt}, {motion_prompt_suffix()}"
        workflow = build_workflow_prompt(
            workflow_path,
            image_name=uploaded_name,
            prompt=full_prompt,
            seed=random.randint(1, 2_147_483_647),
            width=width,
            height=height,
            num_frames=num_frames,
            ckpt_name=ckpt_name,
            steps=steps,
            cfg=cfg,
            strength=strength,
        )
        dest = images_dir / f"{stem}.mp4"
        try:
            run_i2v_and_download(
                base_url,
                workflow,
                dest,
                preferred_node_ids=preferred,
                timeout_sec=timeout_sec,
            )
            print(f"Сохранено: {dest}")
        except Exception as exc:
            print(f"[ERROR] Кадр {stem}: {exc}", file=sys.stderr)
            return 1
        emit_progress(idx, total, f"Кадр {stem} готов -> {dest.name}")

    emit_progress(total, total, "ComfyUI анимация завершена")
    print("Готово.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())