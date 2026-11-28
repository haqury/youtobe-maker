"""Блок 6 — финальная склейка."""

from __future__ import annotations

from pathlib import Path

import streamlit as st


def render_final_mux_block(
    episode_dir: Path,
    ep_key: str,
    *,
    launch_job,
    persist_config,
    channel_slug: str,
    channel_dir: Path,
) -> bool:
    from long_build_stages import (
        ambient_stage_ready,
        mux_stage_ready,
        tail_video_ready,
        video_stage_ready,
    )
    from studio.long_build_controls import _can_stage_long, _render_stage_previews

    if not _can_stage_long(episode_dir):
        return False
    from isekai_video import inspect_long_episode

    try:
        plan = inspect_long_episode(episode_dir)
    except (FileNotFoundError, RuntimeError, OSError):
        plan = None

    use_tail = bool(plan and plan.use_tail)
    v_ok = video_stage_ready(episode_dir)
    tv_ok = tail_video_ready(episode_dir) if use_tail else True
    a_ok = ambient_stage_ready(episode_dir) if use_tail else True
    m_ok = mux_stage_ready(episode_dir)
    st.caption("⑤ Склейка сюжета + хвоста + полное сведение → `final_video.mp4`.")
    if use_tail:
        st.caption(
            f"{'✅ Сюжет' if v_ok else '⬜ Сюжет'} · "
            f"{'✅ Видео хвоста' if tv_ok else '⬜ Видео хвоста'} · "
            f"{'✅ Эмбиент' if a_ok else '⬜ Эмбиент'} · "
            f"{'✅ final' if m_ok else '⬜ final'}"
        )
    else:
        st.caption(
            f"{'✅ Сюжет' if v_ok else '⬜ Сюжет'} · "
            f"{'✅ final' if m_ok else '⬜ final'}"
        )
    c1, c2 = st.columns(2)
    with c1:
        disabled = not v_ok or (use_tail and not (tv_ok and a_ok))
        if st.button(
            "⑤ Финальная склейка",
            key=f"final_mux_{ep_key}",
            use_container_width=True,
            disabled=disabled,
        ):
            launch_job("build_video", episode_dir, "build_sleep_video.py", "--stage", "mux")
    with c2:
        if st.button("▶ Полная сборка (1→5)", key=f"full_build_{ep_key}", use_container_width=True):
            from studio_ai import load_episode_config
            from tail_controls import save_tail_fields

            save_tail_fields(episode_dir, channel_slug, ep_key)
            epi = load_episode_config(episode_dir, channel_slug, channel_dir)
            persist_config(episode_dir, ep_key, epi)
            launch_job("build_video", episode_dir, "build_sleep_video.py")
    if m_ok:
        _render_stage_previews(episode_dir, ep_key, key_ns="final_mux", show_final=True)
    return True