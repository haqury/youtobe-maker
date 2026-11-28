"""Трёхэтапная сборка длинного ролика в Studio."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import streamlit as st

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from ambient_tail import probe_duration
from long_build_stages import (
    ambient_bed_path,
    ambient_stage_ready,
    ambient_tail_path,
    mux_stage_ready,
    video_for_mux,
    video_stage_ready,
)


def _fmt_duration(sec: float) -> str:
    if sec <= 0:
        return "0:00"
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = int(round(sec % 60))
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def _human_size(path: Path) -> str:
    try:
        mb = path.stat().st_size / (1024 * 1024)
        return f"{mb:.0f} МБ" if mb >= 1 else f"{path.stat().st_size / 1024:.0f} КБ"
    except OSError:
        return "—"


def _ffmpeg_exe() -> str:
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        import shutil

        found = shutil.which("ffmpeg")
        if found:
            return found
        raise RuntimeError("ffmpeg не найден")


def _clip_preview(source: Path, out: Path, *, start: float, seconds: float = 45.0) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    if (
        out.is_file()
        and out.stat().st_size > 4096
        and source.is_file()
        and out.stat().st_mtime >= source.stat().st_mtime
    ):
        return out
    args = [
        _ffmpeg_exe(),
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        f"{max(0.0, start):.3f}",
        "-i",
        str(source),
        "-t",
        f"{seconds:.3f}",
        "-c",
        "copy",
        "-movflags",
        "+faststart",
        str(out),
    ]
    proc = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or "ffmpeg")[-400:])
    return out


def _can_stage_long(episode_dir: Path) -> bool:
    voice = episode_dir / "voiceover.mp3"
    if not voice.is_file():
        return False
    try:
        from isekai_video import inspect_long_episode

        plan = inspect_long_episode(episode_dir)
        return plan is not None
    except (FileNotFoundError, RuntimeError, OSError):
        return False


def _render_stage_previews(
    episode_dir: Path,
    ep_key: str,
    *,
    key_ns: str,
    show_video: bool = False,
    show_ambient: bool = False,
    show_final: bool = False,
) -> None:
    cache = episode_dir / ".video_cache"
    v_ok = video_stage_ready(episode_dir)
    a_ok = ambient_stage_ready(episode_dir)
    if show_video and not v_ok:
        show_video = False
    if show_ambient and not a_ok:
        show_ambient = False
    if not (show_video or show_ambient or show_final):
        return

    title = {
        "show_video": "👁 Превью видеодорожки",
        "show_ambient": "👁 Превью эмбиента",
    }
    if show_video:
        label = title["show_video"]
    elif show_ambient:
        label = title["show_ambient"]
    else:
        label = "👁 Итог сборки"

    with st.expander(label, expanded=False):
        if show_video:
            video_path = video_for_mux(episode_dir)
            if video_path and video_path.is_file():
                dur = probe_duration(video_path)
                st.markdown(
                    f"**Видеодорожка (этап ①):** `{video_path.name}` · "
                    f"{_fmt_duration(dur)} · {_human_size(video_path)}"
                )
                st.caption(
                    "Полный файл тяжёлый для браузера — ниже короткие срезы (~45 с). "
                    "Или откройте файл в плеере по пути."
                )
                st.code(str(video_path.resolve()), language=None)
                try:
                    from isekai_video import inspect_long_episode

                    plan = inspect_long_episode(episode_dir)
                    voice_sec = plan.duration if plan else 0.0
                except (FileNotFoundError, RuntimeError, OSError):
                    voice_sec = 0.0
                pcol1, pcol2 = st.columns(2)
                preview_start = cache / "preview_video_start.mp4"
                preview_tail_vid = cache / "preview_video_at_voice.mp4"
                if pcol1.button("Срез: начало", key=f"prev_v0_{key_ns}_{ep_key}"):
                    try:
                        _clip_preview(video_path, preview_start, start=0.0)
                        st.toast("Готово: начало")
                    except RuntimeError as exc:
                        st.error(str(exc))
                if pcol2.button(
                    "Срез: после озвучки",
                    key=f"prev_vvoice_{key_ns}_{ep_key}",
                    disabled=voice_sec <= 0,
                ):
                    try:
                        start = max(0.0, voice_sec + 4.0)
                        _clip_preview(video_path, preview_tail_vid, start=start)
                        st.toast(f"Готово: с {_fmt_duration(start)}")
                    except RuntimeError as exc:
                        st.error(str(exc))
                if preview_start.is_file():
                    st.caption("Начало")
                    st.video(str(preview_start))
                if preview_tail_vid.is_file():
                    st.caption("После озвучки (≈ хвост видео)")
                    st.video(str(preview_tail_vid))

        if show_ambient:
            bed = ambient_bed_path(episode_dir)
            tail = ambient_tail_path(episode_dir)
            st.markdown("**Эмбиент (этап ②)**")
            if bed.is_file():
                bed_d = probe_duration(bed)
                st.caption(f"Под речь: `{bed.name}` · {_fmt_duration(bed_d)} · {_human_size(bed)}")
                start = st.slider(
                    "Прослушать bed с (сек)",
                    0.0,
                    max(0.0, bed_d - 60.0),
                    0.0,
                    step=60.0,
                    key=f"bed_off_{key_ns}_{ep_key}",
                )
                preview_bed = cache / "preview_ambient_bed.m4a"
                if st.button(
                    "Подготовить фрагмент bed (60 с)",
                    key=f"prev_bed_{key_ns}_{ep_key}",
                ):
                    try:
                        _clip_preview(bed, preview_bed, start=start, seconds=60.0)
                        st.rerun()
                    except RuntimeError as exc:
                        st.error(str(exc))
                if preview_bed.is_file():
                    st.audio(str(preview_bed))
            if tail.is_file():
                tail_d = probe_duration(tail)
                st.caption(f"Хвост после речи: `{tail.name}` · {_fmt_duration(tail_d)} · {_human_size(tail)}")
                preview_tail = cache / "preview_ambient_tail.m4a"
                if st.button(
                    "Прослушать начало хвоста (60 с)",
                    key=f"prev_tail_{key_ns}_{ep_key}",
                ):
                    try:
                        _clip_preview(tail, preview_tail, start=0.0, seconds=60.0)
                        st.rerun()
                    except RuntimeError as exc:
                        st.error(str(exc))
                if preview_tail.is_file():
                    st.audio(str(preview_tail))

        if show_final:
            final = episode_dir / "final_video.mp4"
            if final.is_file():
                st.markdown(
                    f"**Итог:** `final_video.mp4` · {_fmt_duration(probe_duration(final))}"
                )


def render_long_build_ambient_stage(
    episode_dir: Path,
    ep_key: str,
    *,
    launch_job,
) -> bool:
    """Этап ② — звуковой хвост (блок 5)."""
    if not _can_stage_long(episode_dir):
        return False
    v_ok = video_stage_ready(episode_dir)
    a_ok = ambient_stage_ready(episode_dir)
    st.caption("② Уникальный эмбиент под речь и после неё (.video_cache).")
    if st.button(
        "② Собрать звуковой хвост",
        key=f"long_amb_{ep_key}",
        use_container_width=True,
        disabled=not v_ok,
        help="Нужен готовый этап ① (видеодорожка).",
    ):
        launch_job(
            "build_video",
            episode_dir,
            "build_sleep_video.py",
            "--stage",
            "ambient",
        )
    st.caption("✅ Эмбиент готов" if a_ok else "⬜ Эмбиент")
    if a_ok:
        _render_stage_previews(
            episode_dir, ep_key, key_ns="d_amb", show_ambient=True
        )
    return True


def render_long_build_video_stage(
    episode_dir: Path,
    ep_key: str,
    *,
    launch_job,
    persist_config,
    channel_slug: str,
    channel_dir: Path,
) -> bool:
    """Этап ① — сборка видеодорожки (блок 4)."""
    if not _can_stage_long(episode_dir):
        return False
    from studio_ai import load_episode_config

    v_ok = video_stage_ready(episode_dir)
    st.markdown("**Длинная сборка — этап ①**")
    st.caption("Batch, оверлеи, concat, интро → кэш в `.video_cache`.")
    c1, c2 = st.columns(2)
    with c1:
        st.caption("✅ Видеодорожка готова" if v_ok else "⬜ Видеодорожка")
        if st.button("① Собрать видеодорожку", key=f"long_vid_{ep_key}", use_container_width=True):
            epi = load_episode_config(episode_dir, channel_slug, channel_dir)
            persist_config(episode_dir, ep_key, epi)
            launch_job("build_video", episode_dir, "build_sleep_video.py", "--stage", "video")
    with c2:
        if st.button("▶ Всё сразу (①+②+③)", key=f"long_all_{ep_key}", use_container_width=True):
            epi = load_episode_config(episode_dir, channel_slug, channel_dir)
            persist_config(episode_dir, ep_key, epi)
            launch_job("build_video", episode_dir, "build_sleep_video.py")
    if v_ok:
        _render_stage_previews(episode_dir, ep_key, key_ns="g_vid", show_video=True)
    return True


def render_long_build_mux_stage(
    episode_dir: Path,
    ep_key: str,
    *,
    launch_job,
) -> bool:
    """Этап ③ — склейка в final_video.mp4 (блок 5)."""
    if not _can_stage_long(episode_dir):
        return False

    v_ok = video_stage_ready(episode_dir)
    a_ok = ambient_stage_ready(episode_dir)
    m_ok = mux_stage_ready(episode_dir)
    st.markdown("**Длинная сборка — этап ③**")
    st.caption("Голос + bed + tail на видеодорожку → `final_video.mp4`.")
    st.caption(
        f"{'✅ Видео' if v_ok else '⬜ Видео'} · {'✅ Эмбиент' if a_ok else '⬜ Эмбиент'}"
    )
    if st.button(
        "③ Объединить в final_video",
        key=f"long_mux_{ep_key}",
        use_container_width=True,
        disabled=not (v_ok and a_ok),
        help="Нужны готовые этапы ① и ② (блок 4 и настройки хвоста в блоке 5).",
    ):
        launch_job("build_video", episode_dir, "build_sleep_video.py", "--stage", "mux")
    st.caption("✅ final_video.mp4" if m_ok else "⬜ Склейка")
    if m_ok:
        _render_stage_previews(episode_dir, ep_key, key_ns="d_mux", show_final=True)
    return True


def render_long_build_video_mux(
    episode_dir: Path,
    ep_key: str,
    *,
    launch_job,
    persist_config,
    channel_slug: str,
    channel_dir: Path,
) -> bool:
    """Совместимость: ① + ③ одним вызовом."""
    a = render_long_build_video_stage(
        episode_dir, ep_key, launch_job=launch_job, persist_config=persist_config,
        channel_slug=channel_slug, channel_dir=channel_dir,
    )
    render_long_build_mux_stage(episode_dir, ep_key, launch_job=launch_job)
    return a


def render_long_build_stages(
    episode_dir: Path,
    ep_key: str,
    *,
    launch_job,
    persist_config,
    channel_slug: str,
    channel_dir: Path,
) -> bool:
    """Совместимость: полный трёхэтапный блок."""
    render_long_build_video_stage(
        episode_dir, ep_key, launch_job=launch_job, persist_config=persist_config,
        channel_slug=channel_slug, channel_dir=channel_dir,
    )
    render_long_build_ambient_stage(episode_dir, ep_key, launch_job=launch_job)
    return render_long_build_mux_stage(episode_dir, ep_key, launch_job=launch_job)