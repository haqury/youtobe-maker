"""Длинный ролик (часы) без MoviePy: стриминговая сборка только через ffmpeg.

maybe_build_long(episode_dir) -> Path | None. None = использовать обычный MoviePy-путь.
Результат: episode_dir/final_video.mp4 (то же имя, что у build_sleep_video.build).
"""

from __future__ import annotations

import math
import re
import subprocess
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from ambient_tail import (
    AmbientTailSettings,
    brightness_at,
    collect_tracks,
    dim_start_for,
    mix_voice_with_tail,
    render_unique_ambient,
    safe_category,
    tail_seconds,
)
from job_progress import emit_progress

ROOT = _SCRIPTS.parent
AMBIENT_DIR = ROOT / "assets" / "ambient"
OUTPUT_NAME = "final_video.mp4"
CACHE_NAME = ".video_cache"
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}  # как в build_sleep_video
FRAME_STEM_RE = re.compile(r"^(\d{2})")
LONG_THRESHOLD_SEC = 40 * 60
WIDTH, HEIGHT, FPS = 1280, 720, 24
CLIP_SEC = 12
ZOOM_END = 1.07
XFADE_SEC = 0.5
BATCH_SIZE = 6
AMBIENT_VOLUME = 0.08
DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")


def _ffmpeg() -> str:
    import imageio_ffmpeg

    return imageio_ffmpeg.get_ffmpeg_exe()


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [_ffmpeg(), "-y", "-hide_banner", "-loglevel", "error", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def _require(proc: subprocess.CompletedProcess, what: str) -> None:
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg ({what}): {(proc.stderr or '')[-600:]}")


def probe_duration(path: Path) -> float:
    """Длительность из stderr `ffmpeg -i` (ffprobe в imageio_ffmpeg нет)."""
    proc = subprocess.run(
        [_ffmpeg(), "-hide_banner", "-i", str(path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    match = DURATION_RE.search(proc.stderr or "")
    if not match:
        return 0.0
    hours, minutes, seconds = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def list_still_images(folder: Path) -> list[Path]:
    by_stem: dict[str, Path] = {}
    for path in sorted(folder.iterdir()):
        if not path.is_file() or path.suffix.lower() not in IMAGE_SUFFIXES:
            continue
        match = FRAME_STEM_RE.match(path.name)
        if match:
            by_stem.setdefault(match.group(1), path)
    return [by_stem[key] for key in sorted(by_stem)]


def _ambient_for(episode_dir: Path) -> Path | None:
    fallback: Path | None = None
    if AMBIENT_DIR.is_dir():
        tracks = sorted(
            (p for p in AMBIENT_DIR.iterdir() if p.is_file() and p.suffix.lower() == ".mp3"),
            key=lambda p: p.name.lower(),
        )
        fallback = tracks[0] if tracks else None
    try:
        from channel_settings import channel_dir_from_episode, resolve_ambient_track

        path, _volume = resolve_ambient_track(channel_dir_from_episode(episode_dir), fallback)
        return path
    except Exception:
        return fallback


def ken_burns_vf(variant: int = 0, fps: int = FPS) -> str:
    """Медленный Ken Burns. Вариант 0 при 24 fps совпадает с прежним зумом в центр."""
    fps = max(1, int(fps))
    span = max(1, CLIP_SEC * fps - 1)
    big_w, big_h = WIDTH * 2, HEIGHT * 2
    delta = ZOOM_END - 1
    kind = variant % 4
    if kind == 1:
        z = f"{ZOOM_END:.4f}-{delta:.4f}*on/{span}"
        x = "iw/2-(iw/zoom/2)"
        y = "ih/2-(ih/zoom/2)"
    elif kind == 2:
        z = "1.0500"
        x = f"(iw-iw/zoom)*on/{span}"
        y = "ih/2-(ih/zoom/2)"
    elif kind == 3:
        z = "1.0500"
        x = "iw/2-(iw/zoom/2)"
        y = f"(ih-ih/zoom)*on/{span}"
    else:
        z = f"1+{delta:.4f}*on/{span}"
        x = "iw/2-(iw/zoom/2)"
        y = "ih/2-(ih/zoom/2)"
    zoom = f"zoompan=z='{z}':x='{x}':y='{y}':d=1:s={WIDTH}x{HEIGHT}:fps={fps}"
    return (
        f"scale={big_w}:{big_h}:force_original_aspect_ratio=increase,"
        f"crop={big_w}:{big_h},{zoom},format=yuv420p"
    )


def encode_image_clip(image: Path, output: Path, variant: int = 0, fps: int = FPS) -> bool:
    """Один раз на кадр: Ken Burns, 12 с. True = пересобрано."""
    fps = max(1, int(fps))
    if output.is_file() and output.stat().st_size > 0 and output.stat().st_mtime >= image.stat().st_mtime:
        return False
    vf = ken_burns_vf(variant, fps)
    tmp = output.with_suffix(".tmp.mp4")
    proc = _run(
        [
            "-loop", "1", "-framerate", str(fps), "-t", str(CLIP_SEC), "-i", str(image),
            "-vf", vf, "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
            "-pix_fmt", "yuv420p", "-r", str(fps), "-threads", "2", str(tmp),
        ]
    )
    if proc.returncode != 0:
        tmp.unlink(missing_ok=True)
        _require(proc, f"клип {image.name}")
    tmp.replace(output)
    return True


def _apply_brightness(
    graph: str,
    last: str,
    brightness: float,
    ramp: tuple[float, float, float] | None,
) -> tuple[str, str]:
    if ramp is not None and abs(ramp[0] - ramp[1]) >= 0.02:
        b0, b1, dur = ramp
        expr = f"({b0:.5f}+({b1:.5f}-{b0:.5f})*min(max(T,0)/{max(dur, 0.1):.3f},1))"
        piece = (
            f"[{last}]format=rgb24,"
            f"geq=r='r(X,Y)*{expr}':g='g(X,Y)*{expr}':b='b(X,Y)*{expr}',"
            f"format=yuv420p[vout]"
        )
        return f"{graph};{piece}", "vout"
    if brightness < 0.985:
        level = min(1.0, max(0.05, brightness))
        piece = (
            f"[{last}]format=rgb24,colorchannelmixer=rr={level:.4f}:gg={level:.4f}:bb={level:.4f},"
            f"format=yuv420p[vout]"
        )
        return f"{graph};{piece}", "vout"
    return graph, last


def encode_batch(
    clips: list[Path],
    output: Path,
    brightness: float = 1.0,
    ramp: tuple[float, float, float] | None = None,
    fps: int = FPS,
    overlay: Path | None = None,
    overlay_opacity: float = 0.22,
    keyframe_at: float | None = None,
) -> None:
    """До 6 клипов с xfade 0.5 с в один batch (без O(n^2) пере-кодирования)."""
    fps = max(1, int(fps))
    args: list[str] = []
    for clip in clips:
        args += ["-i", str(clip)]
    prep = [f"[{i}:v]settb=AVTB,fps={fps},format=yuv420p[p{i}]" for i in range(len(clips))]
    chain: list[str] = []
    last = "p0"
    for i in range(1, len(clips)):
        offset = i * (CLIP_SEC - XFADE_SEC)
        out = f"x{i}"
        chain.append(f"[{last}][p{i}]xfade=transition=fade:duration={XFADE_SEC}:offset={offset:.3f}[{out}]")
        last = out
    graph = ";".join(prep + chain)
    if len(clips) == 1:
        graph = prep[0].replace("[p0]", "[x0]")
        last = "x0"
    if overlay is not None:
        ov_index = len(clips)
        args += ["-stream_loop", "-1", "-i", str(overlay)]
        opacity = max(0.0, min(1.0, float(overlay_opacity)))
        graph += (
            f";[{ov_index}:v]scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase,"
            f"crop={WIDTH}:{HEIGHT},fps={fps},format=yuv420p[ov]"
            f";[{last}][ov]blend=all_mode=screen:all_opacity={opacity:.3f}:shortest=1[vlay]"
        )
        last = "vlay"
    graph, last = _apply_brightness(graph, last, brightness, ramp)
    cmd = [
        *args, "-filter_complex", graph, "-map", f"[{last}]", "-an",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-pix_fmt", "yuv420p", "-r", str(fps), "-threads", "2",
    ]
    if keyframe_at is not None and keyframe_at > 0:
        cmd += ["-force_key_frames", f"{float(keyframe_at):.3f}"]
    cmd.append(str(output))
    proc = _run(cmd)
    _require(proc, output.name)


def _list_line(path: Path) -> str:
    text = str(path.resolve()).replace("\\", "/").replace("'", "'\\''")
    return f"file '{text}'"


def concat_copy(parts: list[Path], output: Path, list_file: Path) -> None:
    list_file.write_text("\n".join(_list_line(p) for p in parts) + "\n", encoding="utf-8")
    try:
        _require(
            _run(["-f", "concat", "-safe", "0", "-i", str(list_file), "-c", "copy", str(output)]),
            "concat batches",
        )
    finally:
        list_file.unlink(missing_ok=True)


def mux(
    video: Path,
    voice: Path,
    ambient: Path | None,
    duration: float,
    output: Path,
    lead_sec: float = 0.0,
) -> None:
    args = ["-i", str(video), "-i", str(voice)]
    lead_ms = int(round(max(0.0, lead_sec) * 1000))
    if ambient is not None:
        args += ["-stream_loop", "-1", "-i", str(ambient)]
        if lead_ms > 0:
            graph = (
                f"[1:a]aformat=channel_layouts=stereo,adelay={lead_ms}|{lead_ms}[voice];"
                f"[2:a]volume={AMBIENT_VOLUME},afade=t=in:st=0:d=1.5[amb];"
                "[voice][amb]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[a]"
            )
        else:
            graph = (
                f"[2:a]volume={AMBIENT_VOLUME}[amb];"
                "[1:a][amb]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[a]"
            )
        args += ["-filter_complex", graph, "-map", "0:v", "-map", "[a]"]
    elif lead_ms > 0:
        args += [
            "-filter_complex",
            f"[1:a]aformat=channel_layouts=stereo,adelay={lead_ms}|{lead_ms}[a]",
            "-map", "0:v", "-map", "[a]",
        ]
    else:
        args += ["-map", "0:v", "-map", "1:a"]
    args += [
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-t", f"{duration:.3f}",
        "-threads", "2", "-movflags", "+faststart", str(output),
    ]
    _require(_run(args), "mux")


def _clip_cache_path(cache: Path, image: Path, variant: int, fps: int = FPS) -> Path:
    fps = max(1, int(fps))
    if variant <= 0 and fps == FPS:
        return cache / f"{image.stem}.mp4"
    if variant <= 0:
        return cache / f"{image.stem}_f{fps}.mp4"
    if fps == FPS:
        return cache / f"{image.stem}_k{variant}.mp4"
    return cache / f"{image.stem}_f{fps}_k{variant}.mp4"


def _ensure_clip(cache: Path, image: Path, variant: int, fps: int = FPS) -> Path:
    clip = _clip_cache_path(cache, image, variant, fps)
    rebuilt = encode_image_clip(image, clip, variant, fps)
    if rebuilt:
        print(f"Клип {clip.name}: собран")
    return clip


def build_long(
    episode_dir: Path,
    voice: Path,
    duration: float,
    tail: AmbientTailSettings | None = None,
) -> Path:
    image_dir = episode_dir / "images"
    if not image_dir.is_dir():
        raise FileNotFoundError(f"Нет папки с кадрами: {image_dir}")
    images = list_still_images(image_dir)
    if not images:
        raise FileNotFoundError(f"В {image_dir} нет png, jpg или webp.")

    cache = episode_dir / CACHE_NAME
    cache.mkdir(exist_ok=True)
    use_tail = tail is not None and tail_seconds(duration, tail) > 1.0
    total = float(tail.target_total_sec) if use_tail and tail is not None else duration
    if use_tail and tail is not None and not collect_tracks(tail.category):
        folder = "assets/ambient" if safe_category(tail.category) == "mix" else f"assets/ambient/{safe_category(tail.category)}"
        raise RuntimeError(
            f"Нет аудио в {folder}. Положите длинные записи без речи или выберите другую категорию."
        )
    total_steps = len(images) + 4
    if use_tail and tail is not None:
        print(
            f"Эмбиент-хвост: речь {duration / 60:.1f} мин, итог {total / 60:.1f} мин, "
            f"категория {tail.category}, громкость {tail.volume:.0%}."
        )
        print(
            f"Затемнение до {int(brightness_at(10**9, dim_start_for(duration)) * 100)}% "
            f"с {dim_start_for(duration) / 60:.1f} мин."
        )
    from animation_settings import load_episode_animation, resolve_overlay_path
    from episode_timing import describe_fps_schedule, episode_timing_from_config, fps_at

    timing = episode_timing_from_config(episode_dir)
    fps_start = int(timing["fps_start"])
    fps_end = int(timing["fps_end"])
    fps_every = float(timing["fps_interval_min"])
    anim = load_episode_animation(episode_dir)
    overlay = resolve_overlay_path(anim, episode_dir)
    overlay_opacity = float(anim.get("overlay_opacity", 0.22) or 0.22)
    from intro_brand import attach_intro, prepare_intro_clip
    from channel_settings import resolve_episode_branding, resolve_intro_source

    branding = resolve_episode_branding(episode_dir)
    intro_source = resolve_intro_source(episode_dir) if branding.get("use_intro") else None
    intro_ready: tuple[Path, float] | None = None
    intro_head: float | None = None
    if branding.get("use_intro") and intro_source is None:
        print("Интро включено, но intro.mp4 не найден — сборка без интро.")
    elif intro_source is not None:
        prepared = prepare_intro_clip(
            intro_source,
            branding,
            WIDTH,
            HEIGHT,
            fps_start,
            episode_dir=episode_dir,
        )
        intro_dur = probe_duration(prepared)
        crossfade = min(float(branding.get("crossfade_sec", 1.0)), intro_dur * 0.45)
        intro_head = intro_dur + max(0.0, crossfade)
        intro_ready = (prepared, crossfade)
        print(
            f"Интро: {intro_source.name}, {intro_dur:.1f} с, переход {crossfade:.1f} с, "
            f"речь с {max(0.0, intro_dur - crossfade):.1f} с."
        )
    print(f"Длинный ролик: озвучка {duration / 60:.1f} мин, кадров {len(images)} (ffmpeg, без MoviePy).")
    print(f"FPS: {describe_fps_schedule(total, fps_start, fps_end, fps_every)}")
    if overlay is None:
        print("Оверлей не выбран — и речь, и хвост без него.")
    else:
        print(f"Оверлей: {overlay.name}, screen {overlay_opacity:.0%}, на всю длину включая хвост.")

    for index, image in enumerate(images, start=1):
        emit_progress(index, total_steps, f"Клип {index}/{len(images)}: {image.name}")
        _ensure_clip(cache, image, 0, fps_start)

    batch_len = BATCH_SIZE * CLIP_SEC - (BATCH_SIZE - 1) * XFADE_SEC
    batch_count = max(1, math.ceil(total / batch_len))
    for stale in cache.glob("batch_*.mp4"):
        stale.unlink(missing_ok=True)
    unique: dict[tuple, Path] = {}
    sequence: list[Path] = []
    dim_start = dim_start_for(duration) if use_tail else None
    emit_progress(len(images) + 1, total_steps, f"Batch xfade (всего {batch_count})")
    for batch in range(batch_count):
        start_t = batch * batch_len
        end_t = min(total, start_t + batch_len)
        batch_fps = fps_at(start_t, total, fps_start, fps_end, fps_every)
        brightness = 1.0
        ramp: tuple[float, float, float] | None = None
        paths: list[Path] = []
        slots: list[tuple[int, int, int]] = []
        for k in range(BATCH_SIZE):
            occ = batch * BATCH_SIZE + k
            img_i = occ % len(images)
            variant = (occ // len(images)) % 4 if use_tail else 0
            slots.append((img_i, variant, batch_fps))
            paths.append(_ensure_clip(cache, images[img_i], variant, batch_fps))
        if use_tail and dim_start is not None:
            b0 = brightness_at(start_t, dim_start)
            b1 = brightness_at(end_t, dim_start)
            if abs(b0 - b1) < 0.03:
                brightness = (b0 + b1) / 2.0
                key = tuple(slots) + (round(brightness, 2),)
            else:
                ramp = (b0, b1, batch_len)
                key = tuple(slots) + ("ramp", round(b0, 2), round(b1, 2))
        else:
            key = tuple(slots)
        if overlay is not None:
            key = key + ("ov", round(overlay_opacity, 2))
        keyframe_at: float | None = None
        if intro_head is not None and start_t <= intro_head < start_t + batch_len:
            keyframe_at = intro_head - start_t
            key = key + ("kf", round(keyframe_at, 3))
        if key not in unique:
            target = cache / f"batch_{len(unique):04d}.mp4"
            encode_batch(
                paths,
                target,
                brightness,
                ramp,
                batch_fps,
                overlay,
                overlay_opacity,
                keyframe_at,
            )
            unique[key] = target
        sequence.append(unique[key])
    print(f"Batch: {batch_count} в ленте, уникальных {len(unique)}.")

    emit_progress(len(images) + 2, total_steps, "Склейка batch (-c copy)")
    video_all = cache / "video_all.mp4"
    concat_copy(sequence, video_all, cache / "_batches.txt")

    output = episode_dir / OUTPUT_NAME
    video_for_audio = video_all
    lead = 0.0
    if intro_ready is not None:
        prepared, crossfade = intro_ready
        emit_progress(len(images) + 2, total_steps, "Интро xfade + хвост (-c copy)")
        video_for_audio, lead = attach_intro(video_all, prepared, crossfade, fps_start, cache)
        print(f"Интро вклеено, речь сдвинута на {lead:.1f} с, эмбиент с 00:00.")
    emit_progress(len(images) + 3, total_steps, f"Mux {OUTPUT_NAME}")
    if use_tail and tail is not None:
        join_pad = 8.0
        bed = render_unique_ambient(
            episode_dir, tail.category, duration, cache / "ambient_bed.m4a", "bed"
        )
        tail_audio = render_unique_ambient(
            episode_dir,
            tail.category,
            (total - duration) + join_pad,
            cache / "ambient_tail.m4a",
            "tail",
        )
        mix_voice_with_tail(
            episode_dir=episode_dir,
            video=video_for_audio,
            voice=voice,
            bed=bed,
            tail=tail_audio,
            voice_dur=duration,
            total_dur=probe_duration(video_for_audio) if lead > 0 else total,
            volume=tail.volume,
            voice_fade_sec=tail.voice_fade_out_sec,
            output=output,
            lead_sec=lead,
        )
    else:
        ambient = _ambient_for(episode_dir)
        if ambient is None:
            print("Ambient пропущен: файл не найден (brand_assets/default_ambient.mp3, assets/ambient/).")
        else:
            print(f"Ambient: {ambient.name}, громкость {AMBIENT_VOLUME:.2f}.")
        mux(
            video_for_audio,
            voice,
            ambient,
            probe_duration(video_for_audio) if lead > 0 else duration,
            output,
            lead_sec=lead,
        )
    emit_progress(total_steps, total_steps, "Завершено")
    print(f"isekai_video ready {output}", flush=True)
    return output


def maybe_build_long(episode_dir: Path) -> Path | None:
    voice = episode_dir / "voiceover.mp3"
    script = episode_dir / "script.md"
    playlist = False
    if script.is_file():
        try:
            playlist = "## PLAYLIST_SEQUENCE" in script.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeDecodeError):
            playlist = False
    if not voice.is_file():
        if playlist:
            raise FileNotFoundError(f"Нет озвучки: {voice}")
        return None
    duration = probe_duration(voice)
    if duration <= 0:
        raise RuntimeError("У voiceover.mp3 нулевая длина.")
    from ambient_tail import settings_from_episode

    settings = settings_from_episode(episode_dir)
    extend = tail_seconds(duration, settings) > 1.0
    if not playlist and duration < LONG_THRESHOLD_SEC and not extend:
        return None
    return build_long(episode_dir, voice, duration, settings if extend else None)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Использование: python scripts/isekai_video.py <папка эпизода>", file=sys.stderr)
        raise SystemExit(2)
    result = maybe_build_long(Path(sys.argv[1]).expanduser().resolve())
    print(result if result else "Не длинный ролик: используйте build_sleep_video.py")
