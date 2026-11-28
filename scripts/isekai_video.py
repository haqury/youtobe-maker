"""Длинный ролик (часы) без MoviePy: стриминговая сборка только через ffmpeg.

maybe_build_long(episode_dir) -> Path | None. None = использовать обычный MoviePy-путь.
Результат: episode_dir/final_video.mp4 (то же имя, что у build_sleep_video.build).
"""

from __future__ import annotations

import json
import math
import random
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from ambient_tail import (
    AmbientTailSettings,
    brightness_at,
    collect_tracks,
    dim_start_for,
    episode_seed,
    mix_voice_with_tail,
    render_unique_ambient,
    safe_category,
    tail_seconds,
)
from job_progress import emit_progress
from long_build_stages import (
    AMBIENT_BED,
    AMBIENT_TAIL,
    mark_ambient_stage,
    mark_video_stage,
    video_for_mux,
    video_stage_ready,
    ambient_stage_ready,
)

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


@dataclass(frozen=True)
class OverlayLayerSpec:
    path: Path
    opacity: float
    start_sec: float = 0.0
    hflip: bool = False


@dataclass(frozen=True)
class LongBuildPlan:
    episode_dir: Path
    voice: Path
    duration: float
    tail: AmbientTailSettings | None
    use_tail: bool
    total: float


@dataclass(frozen=True)
class StorySlice:
    """Фрагмент сюжета по выбранным блокам PLAYLIST (превью / поактовая сборка)."""

    start_sec: float
    duration_sec: float
    blocks: tuple[str, ...] = ()
    playlist_mode: str = "full"


def story_slice_from_part(episode_dir: Path, part_index: int) -> StorySlice | None:
    from isekai_story_blocks import story_segment_for_part

    seg = story_segment_for_part(episode_dir, part_index)
    if seg is None:
        return None
    return StorySlice(
        start_sec=float(seg["start_sec"]),
        duration_sec=float(seg["duration_sec"]),
        blocks=tuple(seg["blocks"]),
        playlist_mode=str(seg["playlist_mode"]),
    )


def story_slice_from_blocks(
    episode_dir: Path,
    blocks: tuple[str, ...],
    *,
    playlist_mode: str = "full",
) -> StorySlice | None:
    if not blocks:
        return None
    from isekai_story_blocks import story_segment_for_blocks

    seg = story_segment_for_blocks(episode_dir, list(blocks), playlist_mode=playlist_mode)
    if seg is None:
        return None
    return StorySlice(
        start_sec=float(seg["start_sec"]),
        duration_sec=float(seg["duration_sec"]),
        blocks=tuple(blocks),
        playlist_mode=playlist_mode,
    )


STORY_VIDEO_PARTIAL = "story_video_partial.mp4"
STORY_MUX_PARTIAL = "story_with_audio_partial.mp4"


def inspect_long_episode(episode_dir: Path) -> LongBuildPlan | None:
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
    use_tail = extend and settings.enabled
    total = float(settings.target_total_sec) if use_tail else duration
    return LongBuildPlan(
        episode_dir=episode_dir,
        voice=voice,
        duration=duration,
        tail=settings if use_tail else None,
        use_tail=use_tail,
        total=total,
    )


def _write_mux_meta(
    cache: Path,
    lead: float,
    story_sec: float,
    full_total: float,
) -> None:
    cache.mkdir(exist_ok=True)
    (cache / "long_mux_meta.json").write_text(
        json.dumps(
            {
                "lead_sec": lead,
                "story_sec": story_sec,
                "total_sec": full_total,
            },
            indent=0,
        ),
        encoding="utf-8",
    )


def _read_mux_meta(cache: Path) -> tuple[float, float, float]:
    path = cache / "long_mux_meta.json"
    if not path.is_file():
        return 0.0, 0.0, 0.0
    data = json.loads(path.read_text(encoding="utf-8"))
    lead = float(data.get("lead_sec", 0) or 0)
    full = float(data.get("total_sec", 0) or 0)
    story = float(data.get("story_sec", 0) or 0)
    if story <= 0:
        story = full
    return lead, story, full


def _ffmpeg() -> str:
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        exe = shutil.which("ffmpeg")
        if exe:
            return exe
        raise


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


def build_overlay_variants(
    overlays: list[Path],
    durations: dict[Path, float],
    *,
    base_opacity: float,
    random_mix: bool,
    max_layers: int,
    variant_count: int,
    rng: random.Random,
) -> list[tuple[OverlayLayerSpec, ...]]:
    """Генерирует пул комбинаций оверлеев (по 1 или несколько слоёв один на другой)."""
    if not overlays:
        return []
    if not random_mix:
        fixed = tuple(
            OverlayLayerSpec(
                path=path,
                opacity=round(max(0.08, min(0.45, base_opacity * (0.90 ** idx))), 3),
                start_sec=0.0,
                hflip=False,
            )
            for idx, path in enumerate(overlays)
        )
        return [fixed]

    cap = min(len(overlays), max(1, int(max_layers)))
    target = max(1, int(variant_count))
    weights = [1.0] if cap == 1 else ([0.42, 0.58] if cap == 2 else [0.28, 0.44, 0.28, *([0.16] * (cap - 3))])
    counts = list(range(1, cap + 1))

    variants: list[tuple[OverlayLayerSpec, ...]] = []
    seen_sigs: set[tuple[tuple[str, bool], ...]] = set()
    has_stacked = False

    for idx in range(target):
        best_specs: tuple[OverlayLayerSpec, ...] | None = None
        best_sig: tuple[tuple[str, bool], ...] | None = None
        force_stack = cap >= 2 and target >= 2 and idx == target - 1 and not has_stacked
        for attempt in range(8):
            if force_stack:
                k = min(cap, 2)
            else:
                k = rng.choices(counts, weights=weights[:cap], k=1)[0]
            chosen = rng.sample(overlays, k)
            rng.shuffle(chosen)
            specs: list[OverlayLayerSpec] = []
            for layer_i, path in enumerate(chosen):
                dur = float(durations.get(path, 0.0) or 0.0)
                start_sec = round(rng.uniform(0.0, max(0.0, dur - 0.5)), 2) if dur > 1.0 else 0.0
                hflip = bool(rng.random() < 0.5)
                jitter = rng.uniform(-0.04, 0.04)
                layer_op = round(max(0.08, min(0.45, (base_opacity + jitter) * (0.90 ** layer_i))), 3)
                specs.append(
                    OverlayLayerSpec(
                        path=path,
                        opacity=layer_op,
                        start_sec=start_sec,
                        hflip=hflip,
                    )
                )
            sig = tuple((s.path.name, s.hflip) for s in specs)
            best_specs = tuple(specs)
            best_sig = sig
            if sig not in seen_sigs or attempt == 7:
                break
        assert best_specs is not None and best_sig is not None
        seen_sigs.add(best_sig)
        if len(best_specs) >= 2:
            has_stacked = True
        variants.append(best_specs)
    return variants


def pick_batch_overlay_variant(
    batch_count: int,
    variant_count: int,
    rng: random.Random,
) -> list[int]:
    """Распределяет варианты оверлеев по batch без повторов подряд."""
    if batch_count <= 0 or variant_count <= 0:
        return []
    if variant_count == 1:
        return [0] * batch_count
    schedule: list[int] = []
    deck: list[int] = []
    prev = -1
    for _ in range(batch_count):
        if not deck:
            deck = list(range(variant_count))
            rng.shuffle(deck)
            if len(deck) > 1 and deck[0] == prev:
                deck[0], deck[1] = deck[1], deck[0]
        pick = deck.pop(0)
        schedule.append(pick)
        prev = pick
    return schedule


def encode_batch(
    clips: list[Path],
    output: Path,
    brightness: float = 1.0,
    ramp: tuple[float, float, float] | None = None,
    fps: int = FPS,
    timeline_fps: int | None = None,
    overlay: Path | None = None,
    overlay_opacity: float = 0.22,
    keyframe_at: float | None = None,
    overlays: list[OverlayLayerSpec] | tuple[OverlayLayerSpec, ...] | None = None,
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

    active_overlays: list[OverlayLayerSpec]
    if overlays:
        active_overlays = list(overlays)
    elif overlay is not None:
        active_overlays = [OverlayLayerSpec(path=overlay, opacity=overlay_opacity)]
    else:
        active_overlays = []

    if active_overlays:
        graph += f";[{last}]setsar=1[vbase]"
        last = "vbase"
        for idx, spec in enumerate(active_overlays):
            ov_index = len(clips) + idx
            args += ["-stream_loop", "-1", "-i", str(spec.path)]
            ov_filters: list[str] = []
            if spec.start_sec > 0.01:
                ov_filters.append(f"trim=start={spec.start_sec:.2f},setpts=PTS-STARTPTS")
            ov_filters.append(f"settb=AVTB,fps={fps}")
            if spec.hflip:
                ov_filters.append("hflip")
            ov_filters.append(
                f"scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase,"
                f"crop={WIDTH}:{HEIGHT},setsar=1,format=yuv420p"
            )
            ov_label = f"ov{idx}"
            out_label = f"vlay{idx}"
            opacity = max(0.0, min(1.0, float(spec.opacity)))
            graph += (
                f";[{ov_index}:v]{','.join(ov_filters)}[{ov_label}]"
                f";[{last}][{ov_label}]blend=all_mode=screen:all_opacity={opacity:.3f}:shortest=1[{out_label}]"
            )
            last = out_label

    graph, last = _apply_brightness(graph, last, brightness, ramp)
    out_fps = max(1, int(timeline_fps or fps))
    if out_fps != fps:
        graph += f";[{last}]fps={out_fps}[vout_cfr]"
        last = "vout_cfr"
    cmd = [
        *args, "-filter_complex", graph, "-map", f"[{last}]", "-an",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-pix_fmt", "yuv420p", "-r", str(out_fps), "-fps_mode", "cfr", "-threads", "2",
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


def _trim_media(source: Path, output: Path, *, start: float, duration: float) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    args = [
        "ffmpeg",
        "-y",
        "-ss",
        f"{start:.3f}",
        "-i",
        str(source),
        "-t",
        f"{duration:.3f}",
        "-c",
        "copy",
        str(output),
    ]
    _require(_run(args), f"trim {source.name}")


def build_long_video_stage(plan: LongBuildPlan, story_slice: StorySlice | None = None) -> Path:
    episode_dir = plan.episode_dir
    voice = plan.voice
    duration = plan.duration
    tail = plan.tail
    use_tail = plan.use_tail and story_slice is None
    full_total = plan.total if plan.use_tail else plan.duration
    total = story_slice.duration_sec if story_slice else plan.duration
    image_dir = episode_dir / "images"
    if not image_dir.is_dir():
        raise FileNotFoundError(f"Нет папки с кадрами: {image_dir}")
    images = list_still_images(image_dir)
    if not images:
        raise FileNotFoundError(f"В {image_dir} нет png, jpg или webp.")

    cache = episode_dir / CACHE_NAME
    cache.mkdir(exist_ok=True)
    if use_tail and tail is not None and not collect_tracks(tail.category):
        folder = "assets/ambient" if safe_category(tail.category) == "mix" else f"assets/ambient/{safe_category(tail.category)}"
        raise RuntimeError(
            f"Нет аудио в {folder}. Положите длинные записи без речи или выберите другую категорию."
        )
    total_steps = len(images) + 3
    if use_tail and tail is not None:
        print(
            f"Эмбиент-хвост: речь {duration / 60:.1f} мин, итог {total / 60:.1f} мин, "
            f"категория {tail.category}, громкость {tail.volume:.0%}."
        )
        dim_at = dim_start_for(
            duration,
            dim_during_speech=tail.dim_during_speech,
            dim_start_sec=tail.dim_start_sec,
        )
        print(
            f"Затемнение до {int(tail.dim_min_brightness * 100)}% "
            f"с {dim_at / 60:.1f} мин (хвост; сюжет в блоке 4 без затемнения)."
        )
    from animation_settings import load_episode_animation, resolve_overlay_paths
    from episode_timing import describe_fps_schedule, episode_timing_from_config, fps_at

    timing = episode_timing_from_config(episode_dir)
    fps_start = int(timing["fps_start"])
    fps_end = int(timing["fps_end"])
    fps_every = float(timing["fps_interval_min"])
    anim = load_episode_animation(episode_dir)
    overlay_paths = resolve_overlay_paths(anim, episode_dir)
    overlay_opacity = float(anim.get("overlay_opacity", 0.22) or 0.22)
    overlay_random_mix = bool(anim.get("overlay_random_mix", True))
    overlay_max_layers = int(anim.get("overlay_max_layers", 3) or 3)
    overlay_variants_count = int(anim.get("overlay_variants", 12) or 12)
    from intro_brand import attach_intro, prepare_intro_clip
    from channel_settings import resolve_episode_branding, resolve_intro_source

    branding = resolve_episode_branding(episode_dir)
    intro_source = resolve_intro_source(episode_dir) if branding.get("use_intro") else None
    intro_ready: tuple[Path, float] | None = None
    intro_head: float | None = None
    if story_slice is not None:
        print(
            f"Поактовая сборка: {', '.join(story_slice.blocks)} · mode={story_slice.playlist_mode} · "
            f"{story_slice.duration_sec / 60:.1f} мин"
        )
    if story_slice is not None:
        pass
    elif branding.get("use_intro") and intro_source is None:
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
    print(
        f"Длинный ролик: озвучка {duration / 60:.1f} мин, "
        f"видеодорожка до {total / 60:.1f} мин "
        f"({'хвост вкл' if use_tail else 'без хвоста на таймлайне'}), "
        f"кадров {len(images)} (ffmpeg, без MoviePy)."
    )
    print(
        f"FPS сюжета ({total / 60:.1f} мин): "
        f"{describe_fps_schedule(total, fps_start, fps_end, fps_every)}"
    )
    if use_tail:
        print(
            f"Хвост на таймлайне ({full_total / 60:.1f} мин всего) — отдельный этап tail_video."
        )

    ov_durations = {path: probe_duration(path) for path in overlay_paths}
    ov_pool = build_overlay_variants(
        overlay_paths,
        ov_durations,
        base_opacity=overlay_opacity,
        random_mix=overlay_random_mix,
        max_layers=overlay_max_layers,
        variant_count=overlay_variants_count,
        rng=random.Random(episode_seed(episode_dir, "overlays")),
    )
    if not overlay_paths:
        print("Оверлей не выбран — и речь, и хвост без него.")
    elif overlay_random_mix:
        names = ", ".join(p.name for p in overlay_paths)
        print(
            f"Оверлеи ({len(overlay_paths)}): {names} — рандомный микс (до {min(len(overlay_paths), overlay_max_layers)} сл., "
            f"{len(ov_pool)} вариантов, база screen {overlay_opacity:.0%})."
        )
    else:
        names = " + ".join(p.name for p in overlay_paths)
        print(f"Оверлеи (фиксированно): {names}, screen {overlay_opacity:.0%}.")

    for index, image in enumerate(images, start=1):
        emit_progress(index, total_steps, f"Клип {index}/{len(images)}: {image.name}")
        _ensure_clip(cache, image, 0, fps_start)

    batch_len = BATCH_SIZE * CLIP_SEC - (BATCH_SIZE - 1) * XFADE_SEC
    batch_count = max(1, math.ceil(total / batch_len))
    ov_schedule = (
        pick_batch_overlay_variant(
            batch_count,
            len(ov_pool),
            random.Random(episode_seed(episode_dir, "overlay_seq")),
        )
        if ov_pool
        else []
    )
    for stale in cache.glob("batch_*.mp4"):
        stale.unlink(missing_ok=True)
    for stale in cache.glob("tail_batch_*.mp4"):
        stale.unlink(missing_ok=True)
    unique: dict[tuple, Path] = {}
    sequence: list[Path] = []
    # Сюжет (этап video): полная яркость на всём таймлайне речи
    dim_start = None
    dim_kw: dict = {}
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
            variant = 0
            slots.append((img_i, variant, batch_fps))
            paths.append(_ensure_clip(cache, images[img_i], variant, batch_fps))
        if dim_start is not None:
            b0 = brightness_at(start_t, dim_start, **dim_kw)
            b1 = brightness_at(end_t, dim_start, **dim_kw)
            if abs(b0 - b1) < 0.03:
                brightness = (b0 + b1) / 2.0
                key = tuple(slots) + (round(brightness, 2),)
            else:
                ramp = (b0, b1, batch_len)
                key = tuple(slots) + ("ramp", round(b0, 2), round(b1, 2))
        else:
            key = tuple(slots)
        batch_overlays: tuple[OverlayLayerSpec, ...] | None = None
        if ov_pool:
            ov_idx = ov_schedule[batch]
            batch_overlays = ov_pool[ov_idx]
            key = key + ("ov_v", ov_idx, round(overlay_opacity, 2))
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
                timeline_fps=fps_start,
                keyframe_at=keyframe_at,
                overlays=batch_overlays,
            )
            unique[key] = target
        sequence.append(unique[key])
    print(f"Batch: {batch_count} в ленте, уникальных {len(unique)}.")

    emit_progress(len(images) + 2, total_steps, "Склейка batch (-c copy)")
    video_all = cache / "video_all.mp4"
    concat_copy(sequence, video_all, cache / "_batches.txt")

    video_for_audio = video_all
    lead = 0.0
    if intro_ready is not None:
        prepared, crossfade = intro_ready
        emit_progress(len(images) + 3, total_steps, "Интро xfade (-c copy)")
        video_for_audio, lead = attach_intro(video_all, prepared, crossfade, fps_start, cache)
        print(f"Интро вклеено, речь сдвинута на {lead:.1f} с, эмбиент с 00:00.")
    story_probe = probe_duration(video_for_audio)
    if story_slice is not None:
        partial_out = cache / STORY_VIDEO_PARTIAL
        if abs(story_probe - story_slice.duration_sec) > 2.0:
            _trim_media(
                video_for_audio,
                partial_out,
                start=0.0,
                duration=story_slice.duration_sec,
            )
            video_for_audio = partial_out
        else:
            shutil.copy2(video_for_audio, partial_out)
            video_for_audio = partial_out
        print(f"Превью-видео сюжета: {video_for_audio.name}")
    else:
        _write_mux_meta(cache, lead, story_probe, full_total)
        mark_video_stage(episode_dir, video_for_audio)
    emit_progress(total_steps, total_steps, "Видеодорожка сюжета готова")
    print(f"Готово: этап video (сюжет) — {video_for_audio}", flush=True)
    return video_for_audio


def build_story_mux_stage(plan: LongBuildPlan, story_slice: StorySlice | None = None) -> Path:
    from long_build_stages import STORY_WITH_AUDIO, mark_story_mux_stage, video_for_mux

    episode_dir = plan.episode_dir
    cache = episode_dir / CACHE_NAME
    if story_slice is not None:
        video_path = cache / STORY_VIDEO_PARTIAL
        if not video_path.is_file():
            raise RuntimeError("Сначала соберите этап video для выбранных актов.")
        voice_clip = cache / f"_voice_slice_{story_slice.start_sec:.0f}.mp3"
        _trim_media(
            plan.voice,
            voice_clip,
            start=story_slice.start_sec,
            duration=story_slice.duration_sec,
        )
        output = cache / STORY_MUX_PARTIAL
        vid_dur = probe_duration(video_path)
        ambient = _ambient_for(episode_dir)
        mux(video_path, voice_clip, ambient, vid_dur, output, lead_sec=0.0)
        print(f"Готово: этап story_mux (по актам) — {output.name}", flush=True)
        return output
    video_path = video_for_mux(episode_dir)
    if video_path is None:
        raise RuntimeError("Сначала соберите этап video (сюжет).")
    lead, _, _ = _read_mux_meta(cache)
    output = cache / STORY_WITH_AUDIO
    vid_dur = probe_duration(video_path)
    ambient = _ambient_for(episode_dir)
    if ambient is None:
        print("Сюжет: только voiceover (фон не найден).")
    else:
        print(f"Сюжет: voiceover + {ambient.name}.")
    mux(video_path, plan.voice, ambient, vid_dur, output, lead_sec=lead)
    mark_story_mux_stage(episode_dir)
    print(f"Готово: этап story_mux — {output.name}", flush=True)
    return output


def build_tail_video_stage(plan: LongBuildPlan) -> Path | None:
    from long_build_stages import TAIL_VIDEO, mark_tail_video_stage, video_for_mux

    if not plan.use_tail or plan.tail is None:
        print("Хвост на таймлайне отключён — этап tail_video пропущен.")
        return None
    episode_dir = plan.episode_dir
    story_path = video_for_mux(episode_dir)
    if story_path is None:
        raise RuntimeError("Сначала соберите видео сюжета (этап video).")
    t0 = probe_duration(story_path)
    t1 = plan.total
    if t1 <= t0 + 5.0:
        print(f"Хвост видео: 0 с (сюжет уже {t0 / 60:.1f} мин, цель {t1 / 60:.1f} мин).")
        return None

    cache = episode_dir / CACHE_NAME
    cache.mkdir(exist_ok=True)
    image_dir = episode_dir / "images"
    images = list_still_images(image_dir)
    tail = plan.tail
    duration = plan.duration
    from animation_settings import load_episode_animation, resolve_overlay_paths
    from episode_timing import describe_fps_schedule, episode_timing_from_config, fps_at

    timing = episode_timing_from_config(episode_dir)
    fps_start = int(timing["fps_start"])
    fps_end = int(timing["fps_end"])
    fps_every = float(timing["fps_interval_min"])
    anim = load_episode_animation(episode_dir)
    overlay_paths = resolve_overlay_paths(anim, episode_dir)
    overlay_opacity = float(anim.get("overlay_opacity", 0.22) or 0.22)
    overlay_random_mix = bool(anim.get("overlay_random_mix", True))
    overlay_max_layers = int(anim.get("overlay_max_layers", 3) or 3)
    overlay_variants_count = int(anim.get("overlay_variants", 12) or 12)
    ov_durations = {path: probe_duration(path) for path in overlay_paths}
    ov_pool = build_overlay_variants(
        overlay_paths,
        ov_durations,
        base_opacity=overlay_opacity,
        random_mix=overlay_random_mix,
        max_layers=overlay_max_layers,
        variant_count=overlay_variants_count,
        rng=random.Random(episode_seed(episode_dir, "overlays_tail")),
    )
    batch_len = BATCH_SIZE * CLIP_SEC - (BATCH_SIZE - 1) * XFADE_SEC
    span = t1 - t0
    batch_count = max(1, math.ceil(span / batch_len))
    dim_start = dim_start_for(
        duration,
        dim_during_speech=tail.dim_during_speech,
        dim_start_sec=tail.dim_start_sec,
    )
    dim_kw = {"dim_floor": tail.dim_min_brightness, "dim_fade_sec": tail.dim_fade_sec}
    print(
        f"Хвост видео: {t0 / 60:.1f}–{t1 / 60:.1f} мин, batch {batch_count}, "
        f"затемнение до {int(tail.dim_min_brightness * 100)}%."
    )
    print(f"FPS хвост: {describe_fps_schedule(t1, fps_start, fps_end, fps_every)}")
    for stale in cache.glob("tail_batch_*.mp4"):
        stale.unlink(missing_ok=True)
    ov_schedule = (
        pick_batch_overlay_variant(
            batch_count,
            len(ov_pool),
            random.Random(episode_seed(episode_dir, "overlay_tail_seq")),
        )
        if ov_pool
        else []
    )
    unique: dict[tuple, Path] = {}
    sequence: list[Path] = []
    global_batch = int(t0 // batch_len)
    for batch in range(batch_count):
        start_t = t0 + batch * batch_len
        end_t = min(t1, start_t + batch_len)
        batch_fps = fps_at(start_t, t1, fps_start, fps_end, fps_every)
        b0 = brightness_at(start_t, dim_start, **dim_kw)
        b1 = brightness_at(end_t, dim_start, **dim_kw)
        paths: list[Path] = []
        slots: list[tuple[int, int, int]] = []
        ramp: tuple[float, float, float] | None = None
        for k in range(BATCH_SIZE):
            occ = global_batch + batch * BATCH_SIZE + k
            img_i = occ % len(images)
            variant = (occ // len(images)) % 4
            slots.append((img_i, variant, batch_fps))
            paths.append(_ensure_clip(cache, images[img_i], variant, batch_fps))
        if abs(b0 - b1) < 0.03:
            brightness = (b0 + b1) / 2.0
            key = tuple(slots) + (round(brightness, 2),)
        else:
            ramp = (b0, b1, batch_len)
            key = tuple(slots) + ("ramp", round(b0, 2), round(b1, 2))
        if ov_pool:
            ov_idx = ov_schedule[batch]
            key = key + ("ov_v", ov_idx, round(overlay_opacity, 2))
            batch_overlays = ov_pool[ov_idx]
        else:
            batch_overlays = None
        if key not in unique:
            target = cache / f"tail_batch_{len(unique):04d}.mp4"
            encode_batch(
                paths,
                target,
                brightness if ramp is None else (b0 + b1) / 2.0,
                ramp,
                batch_fps,
                timeline_fps=fps_start,
                overlays=batch_overlays,
            )
            unique[key] = target
        sequence.append(unique[key])
    tail_out = cache / TAIL_VIDEO
    concat_copy(sequence, tail_out, cache / "_tail_batches.txt")
    mark_tail_video_stage(episode_dir)
    print(f"Готово: этап tail_video — {tail_out.name}", flush=True)
    return tail_out


def build_long_ambient_stage(plan: LongBuildPlan) -> tuple[Path, Path]:
    if not plan.use_tail or plan.tail is None:
        raise RuntimeError("Этап ambient: у эпизода нет эмбиент-хвоста (ambient_tail_enabled).")
    if not video_stage_ready(plan.episode_dir):
        raise RuntimeError("Сначала соберите этап video (build_sleep_video.py --stage video).")
    tail = plan.tail
    episode_dir = plan.episode_dir
    cache = episode_dir / CACHE_NAME
    cache.mkdir(exist_ok=True)
    total_steps = 2
    emit_progress(1, total_steps, "Эмбиент под речь (bed)")
    bed = render_unique_ambient(
        episode_dir, tail.category, plan.duration, cache / AMBIENT_BED, "bed"
    )
    join_pad = 8.0
    emit_progress(2, total_steps, "Эмбиент-хвост (tail)")
    tail_audio = render_unique_ambient(
        episode_dir,
        tail.category,
        (plan.total - plan.duration) + join_pad,
        cache / AMBIENT_TAIL,
        "tail",
    )
    mark_ambient_stage(episode_dir)
    print(f"Готово: этап ambient — {bed.name}, {tail_audio.name}", flush=True)
    return bed, tail_audio


def build_long_mux_stage(plan: LongBuildPlan) -> Path:
    episode_dir = plan.episode_dir
    cache = episode_dir / CACHE_NAME
    video_path = video_for_mux(episode_dir)
    if video_path is None:
        raise RuntimeError("Сначала соберите этап video.")
    lead, _story_sec, total_dur = _read_mux_meta(cache)
    if total_dur <= 0:
        total_dur = plan.total
    output = episode_dir / OUTPUT_NAME
    emit_progress(1, 1, f"Mux {OUTPUT_NAME}")
    from long_build_stages import tail_video_path

    tail_vid = tail_video_path(episode_dir)
    if plan.use_tail and tail_vid.is_file() and tail_vid.stat().st_size > 4096:
        combined = cache / "video_combined_for_mux.mp4"
        concat_copy([video_path, tail_vid], combined, cache / "_full_video_concat.txt")
        video_path = combined
        print(f"Склейка видео: сюжет + хвост → {combined.name}")
    if plan.use_tail and plan.tail is not None:
        if not ambient_stage_ready(episode_dir):
            raise RuntimeError("Сначала соберите этап ambient (build_sleep_video.py --stage ambient).")
        bed = cache / AMBIENT_BED
        tail_audio = cache / AMBIENT_TAIL
        probed_v = probe_duration(video_path)
        if probed_v > total_dur + 1.0:
            print(
                f"Длина видео {probed_v / 60:.1f} мин > цели {total_dur / 60:.1f} мин — "
                f"mux обрежет по целевым {total_dur / 60:.1f} мин."
            )
        mix_voice_with_tail(
            episode_dir=episode_dir,
            video=video_path,
            voice=plan.voice,
            bed=bed,
            tail=tail_audio,
            voice_dur=plan.duration,
            total_dur=total_dur,
            volume=plan.tail.volume,
            voice_fade_sec=plan.tail.voice_fade_out_sec,
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
            video_path,
            plan.voice,
            ambient,
            probe_duration(video_path) if lead > 0 else plan.duration,
            output,
            lead_sec=lead,
        )
    print(f"isekai_video ready {output}", flush=True)
    print(f"Готово: {output}", flush=True)
    return output


def build_long(
    episode_dir: Path,
    voice: Path,
    duration: float,
    tail: AmbientTailSettings | None = None,
) -> Path:
    use_tail = tail is not None and tail_seconds(duration, tail) > 1.0
    total = float(tail.target_total_sec) if use_tail and tail is not None else duration
    plan = LongBuildPlan(
        episode_dir=episode_dir,
        voice=voice,
        duration=duration,
        tail=tail if use_tail else None,
        use_tail=use_tail,
        total=total,
    )
    build_long_video_stage(plan)
    build_story_mux_stage(plan)
    if plan.use_tail:
        build_tail_video_stage(plan)
        build_long_ambient_stage(plan)
    return build_long_mux_stage(plan)


def maybe_build_long_staged(
    episode_dir: Path,
    stage: str,
    *,
    blocks: tuple[str, ...] | None = None,
    playlist_mode: str = "full",
    play_part: int | None = None,
) -> Path | None:
    plan = inspect_long_episode(episode_dir)
    if plan is None:
        return None
    stage = (stage or "all").strip().lower()
    if play_part is not None:
        story_slice = story_slice_from_part(episode_dir, play_part - 1)
        if story_slice is None:
            raise RuntimeError(
                "Не удалось оценить таймкоды части плейлиста (нужны voiceover.mp3 и audio_cache)."
            )
    else:
        story_slice = story_slice_from_blocks(episode_dir, blocks or (), playlist_mode=playlist_mode)
        if blocks and story_slice is None:
            raise RuntimeError("Не удалось оценить таймкоды для выбранных блоков (нужен audio_cache).")
    if stage == "all":
        if story_slice is not None:
            raise RuntimeError("Поактовая сборка: только --stage video или story_mux.")
        return build_long(plan.episode_dir, plan.voice, plan.duration, plan.tail)
    if stage == "video":
        return build_long_video_stage(plan, story_slice)
    if stage == "story_mux":
        return build_story_mux_stage(plan, story_slice)
    if stage in ("tail_video", "tail"):
        from long_build_stages import video_for_mux as _video_for_mux

        out = build_tail_video_stage(plan)
        return out if out is not None else _video_for_mux(plan.episode_dir)
    if stage == "ambient":
        return build_long_ambient_stage(plan)[0]
    if stage == "mux":
        return build_long_mux_stage(plan)
    raise ValueError(
        f"Неизвестный этап: {stage!r}. Допустимо: video, story_mux, tail_video, ambient, mux, all."
    )


def maybe_build_long(episode_dir: Path) -> Path | None:
    plan = inspect_long_episode(episode_dir)
    if plan is None:
        return None
    return build_long(plan.episode_dir, plan.voice, plan.duration, plan.tail)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Использование: python scripts/isekai_video.py <папка эпизода>", file=sys.stderr)
        raise SystemExit(2)
    result = maybe_build_long(Path(sys.argv[1]).expanduser().resolve())
    print(result if result else "Не длинный ролик: используйте build_sleep_video.py")
