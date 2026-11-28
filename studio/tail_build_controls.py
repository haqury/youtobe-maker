"""Блок 5 — хвост: настройки, видео и звук после сюжета."""

from __future__ import annotations

from pathlib import Path

import streamlit as st


def render_tail_build_block(
    episode_dir: Path,
    ep_key: str,
    *,
    launch_job,
    channel_slug: str,
) -> bool:
    from long_build_stages import (
        ambient_stage_ready,
        tail_video_path,
        tail_video_ready,
        video_stage_ready,
    )
    from studio.long_build_controls import _can_stage_long, _render_stage_previews
    from tail_controls import render_ambient_tail_controls, save_tail_fields

    if not _can_stage_long(episode_dir):
        return False
    render_ambient_tail_controls(episode_dir, channel_slug, ep_key)
    from isekai_video import inspect_long_episode

    try:
        plan = inspect_long_episode(episode_dir)
    except (FileNotFoundError, RuntimeError, OSError):
        plan = None
    if plan is None or not plan.use_tail:
        st.info("Хвост выключен — задайте цель > речи в настройках выше.")
        return True

    v_ok = video_stage_ready(episode_dir)
    tv_ok = tail_video_ready(episode_dir)
    a_ok = ambient_stage_ready(episode_dir)
    st.caption(
        f"После сюжета до **{plan.total / 60:.0f} мин**: картинка (tail_video) + "
        f"уникальный эмбиент (bed + tail)."
    )
    c1, c2 = st.columns(2)
    with c1:
        st.caption("✅ Видео хвоста" if tv_ok else "⬜ Видео хвоста")
        if st.button(
            "③ Видео хвоста",
            key=f"tail_vid_{ep_key}",
            use_container_width=True,
            disabled=not v_ok,
        ):
            save_tail_fields(episode_dir, channel_slug, ep_key)
            launch_job(
                "build_video",
                episode_dir,
                "build_sleep_video.py",
                "--stage",
                "tail_video",
            )
    with c2:
        st.caption("✅ Эмбиент" if a_ok else "⬜ Эмбиент")
        if st.button(
            "④ Звук хвоста (bed + tail)",
            key=f"tail_amb_{ep_key}",
            use_container_width=True,
            disabled=not v_ok,
        ):
            save_tail_fields(episode_dir, channel_slug, ep_key)
            launch_job("build_video", episode_dir, "build_sleep_video.py", "--stage", "ambient")
    if tv_ok:
        tv = tail_video_path(episode_dir)
        st.caption(f"Хвост видео: {tv.name}")
    if a_ok:
        _render_stage_previews(episode_dir, ep_key, key_ns="tail_amb", show_ambient=True)
    return True