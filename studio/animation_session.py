"""Снять настройки оживления из session_state Streamlit → review.json."""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from animation_settings import normalize_animation_settings, save_episode_animation


def persist_animation_from_session(episode_dir: Path, ep_key: str) -> bool:
    """Если пользователь менял оживление в UI — сохранить перед ffmpeg-сборкой."""
    method = str(st.session_state.get(f"anim_method_{ep_key}", "ffmpeg_25d"))
    if method == "remotion":
        bundle = normalize_animation_settings(
            {
                "animation_mode": "remotion",
                "remotion_chapter_titles": bool(
                    st.session_state.get(f"remotion_titles_{ep_key}", True)
                ),
                "remotion_particles": bool(
                    st.session_state.get(f"remotion_particles_{ep_key}", True)
                ),
                "remotion_vignette": bool(
                    st.session_state.get(f"remotion_vignette_{ep_key}", True)
                ),
            }
        )
        save_episode_animation(episode_dir, bundle)
        return True
    if f"ffmpeg_ken_{ep_key}" not in st.session_state:
        return False
    bundle = normalize_animation_settings(
        {
            "animation_mode": "ffmpeg_25d",
            "enable_ken_burns": bool(st.session_state.get(f"ffmpeg_ken_{ep_key}", True)),
            "enable_breathing_light": bool(st.session_state.get(f"ffmpeg_breathe_{ep_key}", True)),
            "overlay_files": list(st.session_state.get(f"ffmpeg_overlays_{ep_key}", []) or []),
            "overlay_opacity": float(st.session_state.get(f"ffmpeg_ov_opacity_{ep_key}", 0.22)),
            "overlay_random_mix": bool(st.session_state.get(f"ffmpeg_ov_random_{ep_key}", True)),
            "overlay_max_layers": int(st.session_state.get(f"ffmpeg_ov_layers_{ep_key}", 3)),
            "overlay_variants": int(st.session_state.get(f"ffmpeg_ov_variants_{ep_key}", 12)),
        }
    )
    save_episode_animation(episode_dir, bundle)
    return True