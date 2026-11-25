"""Собрать final_video.mp4: кадры 1920x1080, медленный зум, тихий фон."""

from __future__ import annotations

import argparse
import gc
import math
import os
import re
import sys
from pathlib import Path

import numpy as np
from PIL import Image

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from animation_settings import load_episode_animation, resolve_overlay_path
from channel_settings import (
    brand_asset_path,
    channel_dir_from_episode,
    resolve_ambient_track,
    resolve_episode_branding,
    resolve_intro_source,
)
from intro_brand import prepare_intro_clip

from moviepy.audio.AudioClip import CompositeAudioClip
from moviepy.audio.fx.AudioFadeIn import AudioFadeIn
from moviepy.audio.fx.AudioFadeOut import AudioFadeOut
from moviepy.audio.fx.AudioLoop import AudioLoop
from episode_timing import episode_timing_from_config
from moviepy.audio.io.AudioFileClip import AudioFileClip
from moviepy.video.VideoClip import VideoClip
from moviepy.video.fx.Loop import Loop
from moviepy.video.io.VideoFileClip import VideoFileClip
from moviepy.video.compositing.CompositeVideoClip import concatenate_videoclips
from moviepy.video.fx.CrossFadeIn import CrossFadeIn

from job_progress import emit_progress

ROOT = Path(__file__).resolve().parents[1]
AMBIENT_DIR = ROOT / "assets" / "ambient"
WIDTH = 1920
HEIGHT = 1080
FPS = 24
ZOOM_END = 1.07
CROSSFADE_SECONDS = 1.5
AMBIENT_VOLUME = 0.08
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
VIDEO_SUFFIXES = {".mp4", ".webm"}
FRAME_STEM_RE = re.compile(r"^(\d{2})")
MIN_VIDEO_BYTES = 4096


def is_usable_video(path: Path) -> bool:
    """Пустой или обрубленный mp4 (нет moov) не должен блокировать сборку."""
    if path.suffix.lower() not in VIDEO_SUFFIXES:
        return False
    try:
        size = path.stat().st_size
    except OSError:
        return False
    if size < MIN_VIDEO_BYTES:
        return False
    try:
        from moviepy.video.io.VideoFileClip import VideoFileClip

        probe = VideoFileClip(str(path))
        try:
            duration = probe.duration
        finally:
            probe.close()
        return duration is not None and duration > 0
    except Exception:
        return False


def still_image_for_stem(folder: Path, stem: str) -> Path | None:
    for suffix in IMAGE_SUFFIXES:
        candidate = folder / f"{stem}{suffix}"
        if candidate.is_file():
            return candidate
    return None


def list_images(folder: Path) -> list[Path]:
    """Кадры по номеру: валидный NN.mp4 приоритетнее NN.jpg."""
    by_stem: dict[str, Path] = {}
    videos: dict[str, Path] = {}
    images: dict[str, Path] = {}
    for path in folder.iterdir():
        if not path.is_file():
            continue
        match = FRAME_STEM_RE.match(path.name)
        if not match:
            continue
        stem = match.group(1)
        suffix = path.suffix.lower()
        if suffix in VIDEO_SUFFIXES:
            videos[stem] = path
        elif suffix in IMAGE_SUFFIXES:
            images[stem] = path
    stems = sorted(set(videos) | set(images))
    for stem in stems:
        video = videos.get(stem)
        if video is not None and is_usable_video(video):
            by_stem[stem] = video
        elif stem in images:
            by_stem[stem] = images[stem]
        elif video is not None:
            print(f"[WARN] Пропуск битого видео {video.name} — нет замены jpg/png.", file=sys.stderr)
    return [by_stem[key] for key in sorted(by_stem)]


def first_ambient_track() -> Path | None:
    if not AMBIENT_DIR.is_dir():
        return None
    tracks = sorted(
        (path for path in AMBIENT_DIR.iterdir() if path.is_file() and path.suffix.lower() == ".mp3"),
        key=lambda path: path.name.lower(),
    )
    return tracks[0] if tracks else None


def cover_image(path: Path, size: tuple[int, int]) -> Image.Image:
    image = Image.open(path).convert("RGB")
    target_w, target_h = size
    scale = max(target_w / image.width, target_h / image.height)
    resized = image.resize(
        (max(1, round(image.width * scale)), max(1, round(image.height * scale))),
        Image.Resampling.LANCZOS,
    )
    left = max((resized.width - target_w) // 2, 0)
    top = max((resized.height - target_h) // 2, 0)
    return resized.crop((left, top, left + target_w, top + target_h))


def clip_length(audio_length: float, count: int, crossfade: float) -> tuple[float, float]:
    """Длина клипа с запасом на перехлёст, чтобы ролик совпал с озвучкой.

    Слот одного кадра на таймлайне остаётся audio_length / count.
    """
    if count <= 1 or audio_length <= crossfade:
        return audio_length, 0.0
    duration = (audio_length + (count - 1) * crossfade) / count
    return duration, crossfade


def frame_clip(
    path: Path,
    duration: float,
    zoom_size: tuple[int, int],
    *,
    index: int,
    anim: dict,
) -> VideoClip:
    if path.suffix.lower() in VIDEO_SUFFIXES and is_usable_video(path):
        source = VideoFileClip(str(path)).without_audio()
        clip = source
        if clip.duration is None or clip.duration <= 0:
            clip.close()
            raise RuntimeError(f"Пустой клип: {path.name}")
        if clip.duration < duration:
            clip = clip.with_effects([Loop(duration=duration)])
        elif clip.duration > duration:
            clip = clip.subclipped(0, duration)
        clip = clip.resized(new_size=(WIDTH, HEIGHT)).with_fps(FPS).with_duration(duration)
        return clip
    if path.suffix.lower() in VIDEO_SUFFIXES:
        fallback = still_image_for_stem(path.parent, path.stem)
        if fallback is not None:
            print(f"[WARN] {path.name} битый/пустой — беру {fallback.name}", file=sys.stderr)
            path = fallback
    picture = cover_image(path, zoom_size)
    return ken_burns_clip(picture, duration, index=index, anim=anim)


def _crop_to_output(frame_source: np.ndarray, zoom: float, center_x: float, center_y: float) -> np.ndarray:
    source_h, source_w = frame_source.shape[:2]
    window_w = min(source_w, max(2, int(round(source_w / zoom))))
    window_h = min(source_h, max(2, int(round(source_h / zoom))))
    left = int(round(center_x * max(source_w - window_w, 0)))
    top = int(round(center_y * max(source_h - window_h, 0)))
    left = max(0, min(left, source_w - window_w))
    top = max(0, min(top, source_h - window_h))
    crop = frame_source[top : top + window_h, left : left + window_w]
    if crop.shape[1] == WIDTH and crop.shape[0] == HEIGHT:
        return crop
    fitted = Image.fromarray(crop).resize((WIDTH, HEIGHT), Image.Resampling.BILINEAR)
    return np.asarray(fitted)


def ken_burns_motion(index: int, progress: float, ken_burns: bool) -> tuple[float, float, float]:
    """Возвращает zoom, center_x, center_y (0..1)."""
    if not ken_burns:
        zoom = 1.0 + (ZOOM_END - 1.0) * progress
        return zoom, 0.5, 0.5
    pattern = index % 5
    if pattern == 0:
        return 1.0 + 0.08 * progress, 0.5, 0.5
    if pattern == 1:
        return 1.08 - 0.08 * progress, 0.5, 0.5
    if pattern == 2:
        drift = 0.42 + 0.16 * progress
        return 1.09, drift, drift
    if pattern == 3:
        return 1.08, 0.35 + 0.30 * progress, 0.5
    return 1.08, 0.65 - 0.30 * progress, 0.5


def ken_burns_clip(image: Image.Image, duration: float, *, index: int, anim: dict) -> VideoClip:
    frame_source = np.asarray(image)
    ken_burns = bool(anim.get("enable_ken_burns", True))
    breathing = bool(anim.get("enable_breathing_light", True))

    def frame(t: float) -> np.ndarray:
        progress = 0.0 if duration <= 0 else min(max(t, 0.0), duration) / duration
        zoom, cx, cy = ken_burns_motion(index, progress, ken_burns)
        out = _crop_to_output(frame_source, zoom, cx, cy)
        if breathing:
            bright = 1.0 + 0.04 * math.sin(2.0 * math.pi * t / 6.0)
            out = np.clip(out.astype(np.float32) * bright, 0, 255).astype(np.uint8)
        return out

    return VideoClip(frame_function=frame, duration=duration).with_fps(FPS)


def screen_blend(base: np.ndarray, overlay: np.ndarray, alpha: float) -> np.ndarray:
    bg = base.astype(np.float32) / 255.0
    fg = overlay.astype(np.float32) / 255.0
    if fg.shape[:2] != bg.shape[:2]:
        fg_img = Image.fromarray((fg * 255).astype(np.uint8)).resize(
            (bg.shape[1], bg.shape[0]), Image.Resampling.BILINEAR
        )
        fg = np.asarray(fg_img).astype(np.float32) / 255.0
    screened = 1.0 - (1.0 - bg) * (1.0 - fg)
    mix = bg * (1.0 - alpha) + screened * alpha
    return (np.clip(mix, 0, 1) * 255).astype(np.uint8)


def apply_video_overlay(base: VideoClip, overlay_path: Path, opacity: float, opened: list) -> VideoClip:
    overlay_src = VideoFileClip(str(overlay_path)).without_audio()
    opened.append(overlay_src)
    if overlay_src.duration is None or overlay_src.duration <= 0:
        return base

    def frame(t: float) -> np.ndarray:
        base_f = base.get_frame(t)
        ov_t = t % overlay_src.duration
        ov_f = overlay_src.get_frame(ov_t)
        return screen_blend(base_f, ov_f, opacity)

    layered = VideoClip(frame_function=frame, duration=base.duration).with_fps(FPS)
    opened.append(layered)
    return layered


def build_ambient(voice: AudioFileClip, track: Path, volume: float, opened: list) -> AudioFileClip:
    source = AudioFileClip(str(track))
    opened.append(source)
    quiet = source.with_volume_scaled(volume)
    opened.append(quiet)
    looped = quiet.with_effects([AudioLoop(duration=voice.duration)]).with_duration(voice.duration)
    opened.append(looped)
    return looped


def brand_fitted_clip(path: Path, opened: list) -> VideoClip:
    src = VideoFileClip(str(path)).without_audio()
    opened.append(src)
    duration = float(src.duration or 0)
    if duration <= 0:
        raise RuntimeError(f"Битый brand-ролик: {path}")

    def frame(t: float) -> np.ndarray:
        t_use = min(max(t, 0.0), max(duration - 1 / FPS, 0.0))
        arr = src.get_frame(t_use)
        return np.asarray(cover_image(Image.fromarray(arr), (WIDTH, HEIGHT)))

    clip = VideoClip(frame_function=frame, duration=duration).with_fps(FPS)
    opened.append(clip)
    return clip


def apply_png_watermark(base: VideoClip, wm_path: Path, opacity: float, opened: list) -> VideoClip:
    overlay = Image.open(wm_path).convert("RGBA")
    max_w = int(WIDTH * 0.14)
    scale = max_w / max(overlay.width, 1)
    new_size = (max_w, max(1, int(overlay.height * scale)))
    overlay = overlay.resize(new_size, Image.Resampling.LANCZOS)
    wm = np.array(overlay)
    h, w = wm.shape[:2]
    margin = 32

    def frame(t: float) -> np.ndarray:
        base_f = base.get_frame(t).astype(np.float32)
        y0 = max(0, HEIGHT - h - margin)
        x0 = max(0, WIDTH - w - margin)
        patch = base_f[y0 : y0 + h, x0 : x0 + w]
        alpha = (wm[:, :, 3:4].astype(np.float32) / 255.0) * opacity
        rgb = wm[:, :, :3].astype(np.float32)
        blend = patch * (1.0 - alpha) + rgb * alpha
        out = base_f.copy()
        out[y0 : y0 + h, x0 : x0 + w] = np.clip(blend, 0, 255)
        return out.astype(np.uint8)

    wm_clip = VideoClip(frame_function=frame, duration=base.duration).with_fps(FPS)
    opened.append(wm_clip)
    return wm_clip


def assemble_branded_timeline(
    main: VideoClip,
    *,
    episode_dir: Path,
    opened: list,
) -> tuple[VideoClip, float]:
    channel_dir = channel_dir_from_episode(episode_dir)
    branding = resolve_episode_branding(episode_dir)
    crossfade = float(branding.get("crossfade_sec", CROSSFADE_SECONDS))
    parts: list[VideoClip] = []
    intro_len = 0.0
    if branding.get("use_intro"):
        intro_path = resolve_intro_source(episode_dir)
        if intro_path is not None:
            prepared = prepare_intro_clip(
                intro_path,
                branding,
                WIDTH,
                HEIGHT,
                FPS,
                episode_dir=episode_dir,
            )
            intro_clip = brand_fitted_clip(prepared, opened)
            intro_len = float(intro_clip.duration or 0)
            parts.append(intro_clip)
            print(f"Интро: {prepared.name} ({intro_len:.1f} с)")
        else:
            print("Интро включено, но intro.mp4 не найден — сборка без интро.")
    if branding.get("use_watermark"):
        wm_path = brand_asset_path(channel_dir, "watermark")
        if wm_path.is_file():
            opacity = float(branding.get("watermark_opacity", 0.25))
            main = apply_png_watermark(main, wm_path, opacity, opened)
            print(f"Водяной знак: {wm_path.name}, opacity={opacity:.2f}")
    if intro_len > 0 and crossfade > 0:
        main = main.with_effects([CrossFadeIn(min(crossfade, intro_len * 0.45))])
        opened.append(main)
    parts.append(main)
    if branding.get("use_outro"):
        outro_path = brand_asset_path(channel_dir, "outro")
        if outro_path.is_file():
            parts.append(brand_fitted_clip(outro_path, opened))
            print(f"Аутро: {outro_path.name}")
    voice_delay = max(0.0, intro_len - crossfade) if intro_len > 0 else 0.0
    if intro_len > 0:
        print(f"Речь сдвинута на {voice_delay:.1f} с.")
    if len(parts) == 1:
        return main, voice_delay
    pad = -crossfade if crossfade > 0 and len(parts) > 1 else 0
    merged = concatenate_videoclips(parts, method="compose", padding=pad)
    opened.append(merged)
    return merged, voice_delay


def close_clips(clips: list) -> None:
    """MoviePy на Windows иногда зависает на close() — закрываем с конца, без фатала."""
    for clip in reversed(clips):
        if clip is None:
            continue
        try:
            clip.close()
        except Exception:
            pass
    gc.collect()


def build(episode_dir: Path) -> Path:
    from isekai_video import maybe_build_long

    long_video = maybe_build_long(episode_dir)
    if long_video is not None:
        return long_video
    anim = load_episode_animation(episode_dir)
    voice_path = episode_dir / "voiceover.mp3"
    image_dir = episode_dir / "images"
    if not voice_path.is_file():
        raise FileNotFoundError(f"Нет озвучки: {voice_path}")
    if not image_dir.is_dir():
        raise FileNotFoundError(f"Нет папки с кадрами: {image_dir}")

    images = list_images(image_dir)
    if not images:
        raise FileNotFoundError(f"В {image_dir} нет png, jpg или webp.")

    total_steps = len(images) + 2
    emit_progress(0, total_steps, "Подготовка сборки")

    voice = AudioFileClip(str(voice_path))
    opened: list = [voice]
    try:
        if voice.duration is None or voice.duration <= 0:
            raise RuntimeError("У voiceover.mp3 нулевая длина.")

        zoom_size = (round(WIDTH * ZOOM_END), round(HEIGHT * ZOOM_END))
        duration, crossfade = clip_length(voice.duration, len(images), CROSSFADE_SECONDS)
        slot = voice.duration / len(images)
        print(f"Озвучка: {voice.duration:.2f} с. Кадров: {len(images)}.")
        print(f"Показ кадра: {slot:.2f} с. Переход: {crossfade:.2f} с.")
        emit_progress(1, total_steps, f"Озвучка {voice.duration:.0f} с, кадров {len(images)}")

        clips = []
        for index, path in enumerate(images):
            emit_progress(index + 2, total_steps, f"Клип {index + 1}/{len(images)}: {path.name}")
            print(f"Кадр {index + 1}/{len(images)}: {path.name}")
            picture = frame_clip(path, duration, zoom_size, index=index, anim=anim)
            if index > 0 and crossfade > 0:
                picture = picture.with_effects([CrossFadeIn(crossfade)])
            clips.append(picture)
            opened.append(picture)

        video = concatenate_videoclips(clips, method="compose", padding=-crossfade)
        video = video.with_duration(voice.duration).with_fps(FPS)

        timing = episode_timing_from_config(episode_dir)
        ambient_tail = float(timing["ambient_tail_sec"])
        voice_fade = float(timing["voice_fade_out_sec"])
        if ambient_tail > 0:
            last_path = images[-1]
            hold = frame_clip(
                last_path,
                ambient_tail,
                zoom_size,
                index=len(images) - 1,
                anim=anim,
            )
            opened.append(hold)
            video = concatenate_videoclips([video, hold], method="compose")
            print(f"Хвост ambient: {ambient_tail:.1f} с (последний кадр).")
        main_duration = float(voice.duration or 0) + ambient_tail
        video = video.with_duration(main_duration)
        opened.append(video)

        overlay_path = resolve_overlay_path(anim, episode_dir)
        if overlay_path is not None:
            opacity = float(anim.get("overlay_opacity", 0.22))
            print(f"Оверлей: {overlay_path.name}, Screen, opacity={opacity:.2f}")
            video = apply_video_overlay(video, overlay_path, opacity, opened)
        else:
            mode = anim.get("animation_mode", "ffmpeg_25d")
            print(f"2.5D режим ({mode}): Ken Burns={anim.get('enable_ken_burns')}, свет={anim.get('enable_breathing_light')}")

        video, intro_len = assemble_branded_timeline(video, episode_dir=episode_dir, opened=opened)
        final_dur = float(video.duration or main_duration)
        print(
            f"Озвучка {voice.duration:.2f} с, хвост {ambient_tail:.1f} с, финал {final_dur:.2f} с."
        )

        channel_dir = channel_dir_from_episode(episode_dir)
        fallback_ambient = first_ambient_track()
        ambient_path, ambient_volume = resolve_ambient_track(channel_dir, fallback_ambient)
        voice_dur = float(voice.duration or 0)
        fade_sec = min(voice_fade, max(0.0, voice_dur * 0.9)) if voice_fade > 0 and voice_dur > 0 else 0.0
        voice_processed = voice
        if fade_sec > 0.05:
            voice_processed = voice.with_effects([AudioFadeOut(fade_sec)])
            print(f"Затухание голоса: {fade_sec:.1f} с до конца речи.")
        voice_track = voice_processed.with_start(intro_len) if intro_len > 0 else voice_processed
        audio_parts: list = [voice_track]
        if ambient_path is not None:
            print(f"Фон: {ambient_path.name}, громкость {ambient_volume:.0%}.")
            amb_src = AudioFileClip(str(ambient_path))
            opened.append(amb_src)
            amb_quiet = amb_src.with_volume_scaled(ambient_volume)
            opened.append(amb_quiet)
            amb_effects = [AudioLoop(duration=final_dur)]
            if intro_len > 0:
                amb_effects.append(AudioFadeIn(min(1.5, max(0.1, final_dur * 0.25))))
            amb_loop = amb_quiet.with_effects(amb_effects).with_duration(final_dur)
            opened.append(amb_loop)
            audio_parts.append(amb_loop)
        else:
            print("Фон не найден (brand_assets/default_ambient.mp3 и assets/ambient/).")
        mixed = CompositeAudioClip(audio_parts).with_duration(final_dur)
        opened.append(mixed)

        output = episode_dir / "final_video.mp4"
        final = video.with_audio(mixed).with_duration(final_dur)
        opened.append(final)
        emit_progress(total_steps, total_steps, "Экспорт final_video.mp4")
        final.write_videofile(
            str(output),
            fps=FPS,
            codec="libx264",
            audio_codec="aac",
            audio_bitrate="192k",
            preset="medium",
            threads=4,
            ffmpeg_params=["-pix_fmt", "yuv420p", "-crf", "20"],
            logger=None,
        )
        emit_progress(total_steps, total_steps, "Завершено")
        print(f"MoviePy - video ready {output}", flush=True)
        return output
    finally:
        close_clips(opened)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Собрать final_video.mp4 из voiceover.mp3 и images/."
    )
    parser.add_argument(
        "episode",
        type=Path,
        help="Папка эпизода, например channels/channel_02_sleep_history/episodes/SH01_roman_apartment",
    )
    args = parser.parse_args(argv)
    episode_dir = args.episode.expanduser().resolve()
    if not episode_dir.is_dir():
        print(f"Папка не найдена: {episode_dir}", file=sys.stderr)
        return 1
    try:
        output = build(episode_dir)
    except (FileNotFoundError, RuntimeError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"Готово: {output}", flush=True)
    return 0


if __name__ == "__main__":
    exit_code = main()
    if exit_code == 0:
        os._exit(0)
    raise SystemExit(exit_code)
