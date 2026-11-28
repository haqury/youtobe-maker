"""Подготовка intro.mp4 и быстрая вклейка в длинный ролик без перекодирования хвоста."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

from ambient_tail import probe_duration
from channel_settings import (
    channel_display_name,
    channel_root_from_episode,
    resolve_episode_branding,
    resolve_intro_source,
)

ZOOM = 1.084
PICTURE_FADE_SEC = 0.8
TEXT_OPACITY = 0.8
AMBIENT_FADE_SEC = 1.5


def _ffmpeg() -> str:
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        import shutil

        exe = shutil.which("ffmpeg")
        if exe:
            return exe
        raise


_CFR_FLAGS_CACHE: tuple[str, str] | None = None


def _cfr_flags() -> tuple[str, str]:
    global _CFR_FLAGS_CACHE
    if _CFR_FLAGS_CACHE is not None:
        return _CFR_FLAGS_CACHE
    proc = subprocess.run(
        [_ffmpeg(), "-hide_banner", "-h", "full"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    _CFR_FLAGS_CACHE = ("-fps_mode", "cfr") if "-fps_mode" in (proc.stdout or "") else ("-vsync", "cfr")
    return _CFR_FLAGS_CACHE


def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [_ffmpeg(), "-y", "-hide_banner", "-loglevel", "error", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def _require(proc: subprocess.CompletedProcess[str], what: str) -> None:
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg ({what}): {(proc.stderr or '')[-800:]}")


def text_alpha_expr(
    start: float,
    duration: float,
    fade: float,
    opacity: float = TEXT_OPACITY,
) -> str:
    """Появление и затухание: fade in, плато, fade out. Плато кончается в start+duration."""
    fade_sec = max(0.05, float(fade))
    t0 = max(0.0, float(start))
    hold_end = t0 + max(fade_sec, float(duration))
    t1 = hold_end + fade_sec
    return (
        f"{opacity:.3f}*if(lt(t\\,{t0:.3f})\\,0\\,"
        f"if(lt(t\\,{t0 + fade_sec:.3f})\\,(t-{t0:.3f})/{fade_sec:.3f}\\,"
        f"if(lt(t\\,{hold_end:.3f})\\,1\\,"
        f"if(lt(t\\,{t1:.3f})\\,({t1:.3f}-t)/{fade_sec:.3f}\\,0))))"
    )


INTRO_STYLE_CHOICES = (
    ("gold_serif", "Золото с засечками"),
    ("light_sans", "Светлый без засечек"),
    ("small_italic", "Мелкий курсив"),
)

_FONT_ROOTS = (
    Path(r"C:\Windows\Fonts"),
    Path("/usr/share/fonts/truetype/liberation"),
    Path("/usr/share/fonts/truetype/dejavu"),
    Path("/Library/Fonts"),
)

# size — доля высоты кадра, y — доля высоты от центра, sub — вторая строка относительно первой.
INTRO_STYLES: dict[str, dict[str, Any]] = {
    "gold_serif": {
        "fonts": ("georgia.ttf", "times.ttf", "LiberationSerif-Regular.ttf", "DejaVuSerif.ttf"),
        "color": "0xF5E6C8",
        "size": 0.056,
        "sub": 0.70,
        "y": 0.0,
    },
    "light_sans": {
        "fonts": ("segoeui.ttf", "calibri.ttf", "arial.ttf", "LiberationSans-Regular.ttf", "DejaVuSans.ttf"),
        "color": "0xF4F1EA",
        "size": 0.048,
        "sub": 0.66,
        "y": -0.045,
    },
    "small_italic": {
        "fonts": (
            "georgiai.ttf",
            "timesi.ttf",
            "LiberationSerif-Italic.ttf",
            "DejaVuSerif-Italic.ttf",
            "georgia.ttf",
            "times.ttf",
        ),
        "color": "0xE4D3B0",
        "size": 0.038,
        "sub": 0.82,
        "y": 0.07,
    },
}

_TITLE_PLACEHOLDERS = {"primary youtube title", "alternative title"}


def episode_title(episode_dir: Path) -> str:
    """Первый настоящий заголовок из блока English Metadata."""
    meta = episode_dir / "metadata.md"
    if not meta.is_file():
        return ""
    try:
        lines = meta.read_text(encoding="utf-8-sig").splitlines()
    except (OSError, UnicodeDecodeError):
        return ""
    in_english = False
    in_titles = False
    for line in lines:
        stripped = line.strip()
        lower = stripped.lower()
        if lower.startswith("## english"):
            in_english = True
            in_titles = False
            continue
        if in_english and stripped.startswith("## "):
            break
        if in_english and lower.startswith("### titles"):
            in_titles = True
            continue
        if in_titles and stripped.startswith("### "):
            break
        if not in_titles or not stripped[:1].isdigit():
            continue
        parts = stripped.split(".", 1)
        if len(parts) != 2:
            continue
        title = parts[1].strip()
        if title and title.lower() not in _TITLE_PLACEHOLDERS:
            return title
    return ""


def style_for(code: str | None) -> tuple[str, dict[str, Any]]:
    key = str(code or "gold_serif").strip()
    if key not in INTRO_STYLES:
        key = "gold_serif"
    return key, INTRO_STYLES[key]


def find_font(names: tuple[str, ...]) -> Path:
    for name in names:
        for root in _FONT_ROOTS:
            candidate = root / name
            if candidate.is_file():
                return candidate
    raise RuntimeError("Не найден шрифт для стиля интро: " + ", ".join(names[:3]))


def find_serif_font() -> Path:
    candidates = (
        Path(r"C:\Windows\Fonts\georgia.ttf"),
        Path(r"C:\Windows\Fonts\times.ttf"),
        Path(r"C:\Windows\Fonts\constan.ttf"),
        Path(r"C:\Windows\Fonts\pala.ttf"),
        Path("/usr/share/fonts/truetype/liberation/LiberationSerif-Regular.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf"),
        Path("/Library/Fonts/Georgia.ttf"),
    )
    for path in candidates:
        if path.is_file():
            return path
    raise RuntimeError("Не найден шрифт с засечками (Georgia, Times или Liberation Serif).")


def _escape_text(text: str) -> str:
    return (
        text.replace("\\", r"\\")
        .replace(":", r"\:")
        .replace("'", r"\'")
        .replace("%", r"\%")
        .replace(",", r"\,")
        .replace("[", r"\[")
        .replace("]", r"\]")
    )


def _filter_path(path: Path) -> str:
    return path.as_posix().replace(":", r"\:")


def _file_sig(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def intro_label(episode_dir: Path, branding: dict[str, Any] | None = None) -> str:
    block = branding if branding is not None else resolve_episode_branding(episode_dir)
    text = str(block.get("intro_text") or "").strip()
    if text:
        return text
    return channel_display_name(channel_root_from_episode(episode_dir))


def _stamp(intro_path: Path, payload: dict[str, Any]) -> tuple[Path, Path]:
    folder = intro_path.parent / "cache"
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()[:16]
    return folder / f"intro_{digest}.mp4", folder / f"intro_{digest}.json"


def _fit_font_size(base: int, text: str, width: int) -> int:
    if not text:
        return base
    estimated = len(text) * base * 0.52
    limit = width * 0.86
    if estimated <= limit:
        return base
    return max(14, int(base * limit / estimated))


def _drawtext_line(
    font: Path,
    text: str,
    size: int,
    color: str,
    alpha: str,
    y_expr: str,
) -> str:
    return (
        "drawtext="
        f"fontfile='{_filter_path(font)}':"
        f"text='{_escape_text(text)}':"
        f"fontsize={size}:fontcolor={color}:"
        f"alpha='{alpha}':"
        "shadowcolor=black@0.45:shadowx=2:shadowy=2:"
        f"x=(w-text_w)/2:y={y_expr}"
    )


def prepare_intro_clip(
    intro_path: Path,
    branding: dict[str, Any],
    width: int,
    height: int,
    fps: int,
    label: str = "",
    episode_dir: Path | None = None,
) -> Path:
    """Кроп угла, fade-in и две строки: канал сверху, название эпизода снизу."""
    fps = max(1, int(fps))
    width = max(2, int(width))
    height = max(2, int(height))
    style_id, style = style_for(str(branding.get("intro_text_style") or ""))
    font = find_font(style["fonts"])
    channel_line = label.strip()
    subtitle = ""
    if episode_dir is not None:
        if not channel_line:
            channel_line = intro_label(episode_dir, branding)
        subtitle = episode_title(episode_dir)
    payload = {
        "source": _file_sig(intro_path),
        "width": width,
        "height": height,
        "fps": fps,
        "label": channel_line,
        "subtitle": subtitle,
        "style": style_id,
        "start": round(float(branding.get("intro_text_start", 1.2)), 3),
        "duration": round(float(branding.get("intro_text_duration", 2.6)), 3),
        "fade": round(float(branding.get("intro_fade_sec", 1.0)), 3),
        "y": round(float(branding.get("intro_text_y", 0.0)), 1),
        "font": font.name,
        "zoom": ZOOM,
    }
    prepared, stamp_path = _stamp(intro_path, payload)
    if prepared.is_file() and stamp_path.is_file():
        try:
            saved = json.loads(stamp_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            saved = None
        if saved == payload and prepared.stat().st_size > 4096:
            print(f"Интро из кэша: {prepared.name}")
            return prepared

    source_dur = probe_duration(intro_path)
    if source_dur <= 0.2:
        raise RuntimeError(f"Битый intro: {intro_path}")
    picture_fade = min(PICTURE_FADE_SEC, max(0.1, source_dur * 0.35))
    scaled_w = int(round(width * ZOOM))
    scaled_h = int(round(height * ZOOM))
    if scaled_w % 2:
        scaled_w += 1
    if scaled_h % 2:
        scaled_h += 1
    alpha = text_alpha_expr(payload["start"], payload["duration"], payload["fade"])
    y_shift = int(round(height * float(style["y"]) + payload["y"]))
    title_size = _fit_font_size(max(14, int(round(height * float(style["size"])))), channel_line, width)
    lines: list[str] = []
    if channel_line:
        if subtitle:
            title_y = f"(h-text_h)/2+{y_shift}-{max(8, int(title_size * 0.65))}"
        else:
            title_y = f"(h-text_h)/2+{y_shift}"
        lines.append(_drawtext_line(font, channel_line, title_size, style["color"], alpha, title_y))
    if subtitle:
        sub_size = _fit_font_size(max(14, int(round(title_size * float(style["sub"])))), subtitle, width)
        sub_y = f"(h-text_h)/2+{y_shift}+{max(8, int(title_size * 0.55))}"
        lines.append(_drawtext_line(font, subtitle, sub_size, style["color"], alpha, sub_y))
    drawtext = ",".join(lines)
    video_filter = (
        f"scale={scaled_w}:{scaled_h}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height}:0:0,"
        f"fps={fps},format=yuv420p,"
        f"fade=t=in:st=0:d={picture_fade:.3f}"
        + (f",{drawtext}" if drawtext else "")
    )
    prepared.parent.mkdir(parents=True, exist_ok=True)
    temporary = prepared.with_name(prepared.stem + "_tmp.mp4")
    proc = _run(
        [
            "-i",
            str(intro_path),
            "-vf",
            video_filter,
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            "-r",
            str(fps),
            "-threads",
            "2",
            str(temporary),
        ]
    )
    if proc.returncode != 0:
        temporary.unlink(missing_ok=True)
        _require(proc, "intro prepare")
    temporary.replace(prepared)
    stamp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"Интро подготовлено: {prepared.name}, {probe_duration(prepared):.1f} с, "
        f"{width}x{height}, {fps} fps, стиль {style_id}, «{channel_line}»"
        + (f" / «{subtitle}»" if subtitle else "")
        + "."
    )
    return prepared


def _concat_copy(parts: list[Path], output: Path, list_file: Path) -> None:
    lines = []
    for part in parts:
        text = str(part.resolve()).replace("\\", "/").replace("'", r"'\''")
        lines.append(f"file '{text}'")
    list_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        _require(
            _run(["-f", "concat", "-safe", "0", "-i", str(list_file), "-c", "copy", str(output)]),
            "concat intro",
        )
    finally:
        list_file.unlink(missing_ok=True)


def _encode_cfr(source: Path, dest: Path, fps: int, duration: float | None = None) -> None:
    args = ["-i", str(source)]
    if duration is not None:
        args += ["-t", f"{duration:.3f}"]
    args += [
        "-vf",
        f"fps={fps}:round=near,format=yuv420p,setpts=PTS-STARTPTS",
        "-an",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "23",
        "-pix_fmt",
        "yuv420p",
        "-r",
        str(fps),
        *_cfr_flags(),
        "-threads",
        "2",
        str(dest),
    ]
    _require(_run(args), dest.name)


def attach_intro(
    main_video: Path,
    intro_clip: Path,
    crossfade_sec: float,
    fps: int,
    work_dir: Path,
) -> tuple[Path, float]:
    """xfade интро с началом ролика, хвост приклеивается через -c copy.

    Возвращает (видео, сдвиг речи): сдвиг = длительность интро − crossfade.
    """
    fps = max(1, int(fps))
    intro_dur = probe_duration(intro_clip)
    main_dur = probe_duration(main_video)
    if intro_dur <= 0.2 or main_dur <= 0.2:
        raise RuntimeError("Нечего склеивать: интро или основной ролик пустые.")
    crossfade = max(0.0, float(crossfade_sec))
    crossfade = min(crossfade, intro_dur * 0.45, max(0.0, main_dur * 0.45))
    head_len = min(main_dur, max(crossfade + 0.2, intro_dur + crossfade))
    cut_at = head_len
    lead = max(0.0, intro_dur - crossfade)
    work_dir.mkdir(parents=True, exist_ok=True)
    intro_cfr = work_dir / "_intro_cfr.mp4"
    head = work_dir / "_intro_head.mp4"
    _encode_cfr(intro_clip, intro_cfr, fps)
    _encode_cfr(main_video, head, fps, head_len)
    intro_dur = probe_duration(intro_cfr)
    head_len = probe_duration(head)
    crossfade = min(crossfade, intro_dur * 0.45, max(0.0, head_len * 0.45))
    lead = max(0.0, intro_dur - crossfade)
    offset = max(0.0, intro_dur - crossfade)
    opening = work_dir / "_intro_opening.mp4"
    graph = (
        f"[0:v][1:v]xfade=transition=fade:duration={crossfade:.3f}:offset={offset:.3f},format=yuv420p[v]"
    )
    _require(
        _run(
            [
                "-i",
                str(intro_cfr),
                "-i",
                str(head),
                "-filter_complex",
                graph,
                "-map",
                "[v]",
                "-an",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "23",
                "-pix_fmt",
                "yuv420p",
                "-r",
                str(fps),
                *_cfr_flags(),
                "-threads",
                "2",
                str(opening),
            ]
        ),
        "intro xfade",
    )
    output = work_dir / "video_with_intro.mp4"
    rest_dur = main_dur - cut_at
    if rest_dur < 0.35:
        opening.replace(output)
        return output, lead
    rest = work_dir / "_intro_rest.mp4"
    _require(
        _run(
            [
                "-ss",
                f"{cut_at:.3f}",
                "-i",
                str(main_video),
                "-c",
                "copy",
                "-avoid_negative_ts",
                "make_zero",
                str(rest),
            ]
        ),
        "intro tail copy",
    )
    if probe_duration(rest) < 0.2:
        opening.replace(output)
        rest.unlink(missing_ok=True)
        return output, lead
    _concat_copy([opening, rest], output, work_dir / "_intro_concat.txt")
    return output, lead
