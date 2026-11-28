"""Блок 4 — сюжет: оживление + сборка видео/звука (вся ночь)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import streamlit as st


def _before_video_job(episode_dir: Path, ep_key: str, persist_config, channel_slug, channel_dir) -> None:
    from studio.animation_session import persist_animation_from_session
    from studio_ai import load_episode_config

    if persist_animation_from_session(episode_dir, ep_key):
        st.toast("Настройки оживления сохранены в review.json")
    epi = load_episode_config(episode_dir, channel_slug, channel_dir)
    persist_config(episode_dir, ep_key, epi)


def render_story_build_block(
    episode_dir: Path,
    ep_key: str,
    *,
    launch_job,
    persist_config,
    channel_slug: str,
    channel_dir: Path,
    render_animation: Callable[..., None] | None = None,
    frame_stem_options: list[str] | None = None,
) -> bool:
    from long_build_stages import story_mux_ready, story_with_audio_path, video_stage_ready
    from studio.long_build_controls import _can_stage_long, _render_stage_previews

    if not _can_stage_long(episode_dir):
        st.warning("Нет voiceover.mp3 или эпизод не подходит для длинной FFmpeg-сборки.")
        if render_animation is not None and frame_stem_options is not None:
            with st.expander("🎞️ Оживление / Анимация", expanded=False):
                render_animation(
                    episode_dir,
                    ep_key,
                    frame_stem_options,
                    channel_slug=channel_slug,
                    channel_dir=channel_dir,
                )
        return False

    from isekai_video import inspect_long_episode

    try:
        plan = inspect_long_episode(episode_dir)
    except (FileNotFoundError, RuntimeError, OSError):
        plan = None

    if plan is None:
        st.caption("Короткий выпуск без PLAYLIST.")
        if st.button("🎞️ Собрать видео (короткий)", key=f"story_short_{ep_key}", use_container_width=True):
            from animation_settings import load_episode_animation, resolve_video_build_script
            from studio_ai import load_episode_config

            script = resolve_video_build_script(load_episode_animation(episode_dir))
            epi = load_episode_config(episode_dir, channel_slug, channel_dir)
            persist_config(episode_dir, ep_key, epi)
            launch_job("build_video", episode_dir, script)
        if render_animation is not None and frame_stem_options is not None:
            with st.expander("🎞️ Оживление / Анимация", expanded=False):
                render_animation(
                    episode_dir,
                    ep_key,
                    frame_stem_options,
                    channel_slug=channel_slug,
                    channel_dir=channel_dir,
                )
        return False

    st.markdown("**Сюжет (вся озвучка)**")
    st.caption(
        f"Длина речи **≈{plan.duration / 60:.1f} мин**. Хвост — блок **5**, финал — **6**. "
        "Интро добавляется на **① Видео** (если включено в конфиге). "
        "**①** — картинка + 2.5D; **②** — voiceover поверх готового видео."
    )

    v_ok = video_stage_ready(episode_dir)
    s_ok = story_mux_ready(episode_dir)
    c1, c2 = st.columns(2)
    with c1:
        st.caption("✅ Видео сюжета" if v_ok else "⬜ Видео сюжета")
        if st.button("① Видео по сюжету", key=f"story_vid_{ep_key}", use_container_width=True):
            _before_video_job(episode_dir, ep_key, persist_config, channel_slug, channel_dir)
            launch_job("build_video", episode_dir, "build_sleep_video.py", "--stage", "video")
    with c2:
        st.caption("✅ Со звуком" if s_ok else "⬜ Со звуком")
        if st.button(
            "② Свести сюжет + voiceover",
            key=f"story_mux_{ep_key}",
            use_container_width=True,
            disabled=not v_ok,
        ):
            launch_job("build_video", episode_dir, "build_sleep_video.py", "--stage", "story_mux")

    if s_ok:
        prev = story_with_audio_path(episode_dir)
        st.caption(f"Превью: `{prev.name}`")
        st.video(str(prev))
    elif v_ok:
        _render_stage_previews(episode_dir, ep_key, key_ns="story_vid", show_video=True)

    if render_animation is not None and frame_stem_options is not None:
        with st.expander("🎞️ Оживление / Анимация", expanded=False):
            render_animation(
                episode_dir,
                ep_key,
                frame_stem_options,
                channel_slug=channel_slug,
                channel_dir=channel_dir,
            )
    return True