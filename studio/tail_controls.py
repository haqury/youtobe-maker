"""Блок настроек эмбиент-хвоста в Streamlit."""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from episode_timing import DEFAULT_DIM_MIN_BRIGHTNESS, describe_fps_schedule
from ambient_tail import (
    category_label,
    describe_tail,
    list_ambient_categories,
    probe_duration,
)
from studio_ai import load_episode_config, save_episode_config

ROOT = Path(__file__).resolve().parent.parent

PRESET_OPTIONS = ("none", "180", "480", "600", "custom")
PRESET_LABELS = {
    "none": "Без хвоста",
    "180": "3 часа (180 мин)",
    "480": "8 часов (480 мин)",
    "600": "10 часов (600 мин)",
    "custom": "Своё значение",
}


def preset_code(minutes: float) -> str:
    if minutes <= 0:
        return "none"
    for code, value in (("180", 180.0), ("480", 480.0), ("600", 600.0)):
        if abs(minutes - value) < 0.51:
            return code
    return "custom"


def _fps_fields(ep_key: str, epi_cfg: dict) -> dict:
    start_key = f"ep_fps_start_{ep_key}"
    if start_key not in st.session_state:
        return {
            "fps_start": int(epi_cfg.get("fps_start", 24) or 24),
            "fps_end": int(epi_cfg.get("fps_end", 2) or 2),
            "fps_interval_min": float(epi_cfg.get("fps_interval_min", 30) or 30),
        }
    return {
        "fps_start": int(st.session_state.get(start_key, 24)),
        "fps_end": int(st.session_state.get(f"ep_fps_end_{ep_key}", 2)),
        "fps_interval_min": float(st.session_state.get(f"ep_fps_every_{ep_key}", 30)),
    }


def sync_tail_session_from_disk(ep_key: str, epi_cfg: dict) -> None:
    """Подставить настройки хвоста с диска в session (блок 4 читает их до отрисовки блока 5)."""
    token = f"ep_tail_synced_{ep_key}"
    if st.session_state.get(token):
        return
    minutes = float(epi_cfg.get("target_total_minutes", 600) or 0)
    st.session_state[f"ep_tail_on_{ep_key}"] = bool(epi_cfg.get("ambient_tail_enabled", False))
    st.session_state[f"ep_tail_preset_{ep_key}"] = preset_code(minutes)
    st.session_state[f"ep_tail_min_{ep_key}"] = minutes
    st.session_state[f"ep_tail_cat_{ep_key}"] = str(epi_cfg.get("ambient_category") or "mix")
    st.session_state[f"ep_tail_vol_{ep_key}"] = int(
        round(float(epi_cfg.get("ambient_volume", 0.12) or 0.12) * 100)
    )
    st.session_state[f"ep_fps_start_{ep_key}"] = int(epi_cfg.get("fps_start", 24) or 24)
    st.session_state[f"ep_fps_end_{ep_key}"] = int(epi_cfg.get("fps_end", 2) or 2)
    st.session_state[f"ep_fps_every_{ep_key}"] = float(epi_cfg.get("fps_interval_min", 30) or 30)
    st.session_state[f"ep_dim_speech_{ep_key}"] = bool(epi_cfg.get("dim_during_speech", True))
    st.session_state[f"ep_dim_floor_{ep_key}"] = int(
        round(float(epi_cfg.get("dim_min_brightness", DEFAULT_DIM_MIN_BRIGHTNESS) or DEFAULT_DIM_MIN_BRIGHTNESS) * 100)
    )
    st.session_state[f"ep_dim_start_{ep_key}"] = float(epi_cfg.get("dim_start_minutes", 37.5) or 37.5)
    st.session_state[f"ep_dim_fade_{ep_key}"] = float(epi_cfg.get("dim_fade_sec", 90) or 90)
    st.session_state[token] = True


def tail_fields_from_session(ep_key: str, epi_cfg: dict) -> dict:
    enabled_key = f"ep_tail_on_{ep_key}"
    if enabled_key not in st.session_state:
        return {
            "ambient_tail_enabled": bool(epi_cfg.get("ambient_tail_enabled", False)),
            "target_total_minutes": float(epi_cfg.get("target_total_minutes", 600) or 0),
            "ambient_category": str(epi_cfg.get("ambient_category") or "mix"),
        "ambient_volume": float(epi_cfg.get("ambient_volume", 0.12) or 0.12),
        "dim_during_speech": bool(epi_cfg.get("dim_during_speech", True)),
        "dim_min_brightness": float(
            epi_cfg.get("dim_min_brightness", DEFAULT_DIM_MIN_BRIGHTNESS) or DEFAULT_DIM_MIN_BRIGHTNESS
        ),
        "dim_start_minutes": float(epi_cfg.get("dim_start_minutes", 37.5) or 37.5),
        "dim_fade_sec": float(epi_cfg.get("dim_fade_sec", 90) or 90),
        **_fps_fields(ep_key, epi_cfg),
    }
    choice = str(st.session_state.get(f"ep_tail_preset_{ep_key}", "600"))
    minutes = float(st.session_state.get(f"ep_tail_min_{ep_key}", 600) or 0)
    if choice == "none":
        minutes = 0.0
    enabled = bool(st.session_state[enabled_key]) and choice != "none" and minutes > 0
    percent = int(st.session_state.get(f"ep_tail_vol_{ep_key}", 12))
    return {
        "ambient_tail_enabled": enabled,
        "target_total_minutes": minutes,
        "ambient_category": str(st.session_state.get(f"ep_tail_cat_{ep_key}", "mix")),
        "ambient_volume": max(0.05, min(0.30, percent / 100.0)),
        "dim_during_speech": bool(st.session_state.get(f"ep_dim_speech_{ep_key}", True)),
        "dim_min_brightness": max(
            0.05,
            min(
                1.0,
                int(st.session_state.get(f"ep_dim_floor_{ep_key}", round(DEFAULT_DIM_MIN_BRIGHTNESS * 100))) / 100.0,
            ),
        ),
        "dim_start_minutes": float(st.session_state.get(f"ep_dim_start_{ep_key}", 37.5) or 37.5),
        "dim_fade_sec": float(st.session_state.get(f"ep_dim_fade_{ep_key}", 90) or 90),
        **_fps_fields(ep_key, epi_cfg),
    }


def save_tail_fields(episode_dir: Path, channel_slug: str, ep_key: str) -> None:
    channel_dir = ROOT / "channels" / channel_slug
    cfg = load_episode_config(episode_dir, channel_slug, channel_dir)
    cfg.update(tail_fields_from_session(ep_key, cfg))
    save_episode_config(episode_dir, cfg)


def _voice_seconds(episode_dir: Path, ep_key: str) -> float | None:
    voice = episode_dir / "voiceover.mp3"
    if not voice.is_file():
        return None
    token = f"{voice.stat().st_mtime_ns}:{voice.stat().st_size}"
    token_key = f"ep_voice_tok_{ep_key}"
    cache_key = f"ep_voice_dur_{ep_key}"
    if st.session_state.get(token_key) != token:
        st.session_state[cache_key] = probe_duration(voice)
        st.session_state[token_key] = token
    duration = float(st.session_state.get(cache_key) or 0)
    return duration if duration > 0 else None


def render_ambient_tail_controls(episode_dir: Path, channel_slug: str, ep_key: str) -> None:
    channel_dir = ROOT / "channels" / channel_slug
    epi_cfg = load_episode_config(episode_dir, channel_slug, channel_dir)
    st.markdown("**Эмбиент-хвост после речи**")
    enabled = st.checkbox(
        "Добавлять эмбиент-хвост после речи",
        value=bool(epi_cfg.get("ambient_tail_enabled", False)),
        key=f"ep_tail_on_{ep_key}",
        help="После voiceover.mp3 ролик продолжается до целевой длины: кадры по кругу и уникальный фон.",
    )
    stored_minutes = float(epi_cfg.get("target_total_minutes", 600) or 0)
    preset_key = f"ep_tail_preset_{ep_key}"
    if preset_key not in st.session_state:
        st.session_state[preset_key] = preset_code(stored_minutes)
    choice = st.selectbox(
        "Общая длительность видео",
        PRESET_OPTIONS,
        format_func=lambda code: PRESET_LABELS[code],
        key=preset_key,
        disabled=not enabled,
    )
    min_key = f"ep_tail_min_{ep_key}"
    if choice == "custom":
        if min_key not in st.session_state:
            st.session_state[min_key] = stored_minutes if stored_minutes > 0 else 600.0
    elif choice == "none":
        st.session_state[min_key] = 0.0
    else:
        st.session_state[min_key] = float(choice)
    st.number_input(
        "Общая целевая длительность видео (мин)",
        min_value=0.0,
        max_value=720.0,
        step=10.0,
        key=min_key,
        disabled=not enabled or choice != "custom",
        help="Считается всё видео: речь плюс хвост. По умолчанию 600 минут.",
    )
    categories = list_ambient_categories()
    options = ["mix", *categories]
    stored_cat = str(epi_cfg.get("ambient_category") or "mix")
    if stored_cat not in options:
        options = [stored_cat, *options]
    cat_col, vol_col = st.columns([2, 1])
    cat_key = f"ep_tail_cat_{ep_key}"
    if cat_key not in st.session_state:
        st.session_state[cat_key] = stored_cat if stored_cat in options else "mix"
    with cat_col:
        st.selectbox(
            "Категория фонового звука",
            options,
            format_func=category_label,
            key=cat_key,
            disabled=not enabled,
        )
    with vol_col:
        st.slider(
            "Громкость фона, %",
            min_value=5,
            max_value=30,
            value=int(round(float(epi_cfg.get("ambient_volume", 0.12)) * 100)),
            step=1,
            key=f"ep_tail_vol_{ep_key}",
            disabled=not enabled,
            help="Тихий фон под речь и чистый эмбиент в хвосте.",
        )
    st.markdown("**Затемнение картинки**")
    st.checkbox(
        "Темнеть во время речи (с минуты ниже)",
        value=bool(epi_cfg.get("dim_during_speech", True)),
        key=f"ep_dim_speech_{ep_key}",
        help="Выключи, если картинка должна оставаться яркой всю озвучку. "
        "Этап сюжета (блок 4) затемнение не применяет.",
    )
    d1, d2, d3 = st.columns(3)
    with d1:
        st.slider(
            "Мин. яркость, %",
            min_value=5,
            max_value=80,
            value=int(round(float(epi_cfg.get("dim_min_brightness", DEFAULT_DIM_MIN_BRIGHTNESS) or DEFAULT_DIM_MIN_BRIGHTNESS) * 100)),
            step=1,
            key=f"ep_dim_floor_{ep_key}",
            help="Нижний уровень яркости после fade. По умолчанию 50%.",
        )
    with d2:
        st.number_input(
            "Старт затемнения, мин",
            min_value=0.0,
            max_value=240.0,
            value=float(epi_cfg.get("dim_start_minutes", 37.5) or 37.5),
            step=2.5,
            key=f"ep_dim_start_{ep_key}",
            help="Раньше фиксировано 37.5 мин.",
        )
    with d3:
        st.number_input(
            "Длительность fade, с",
            min_value=15.0,
            max_value=600.0,
            value=float(epi_cfg.get("dim_fade_sec", 90) or 90),
            step=5.0,
            key=f"ep_dim_fade_{ep_key}",
        )
    st.markdown("**Частота кадров**")
    fps1, fps2, fps3 = st.columns(3)
    with fps1:
        st.number_input(
            "Начальный FPS",
            min_value=1,
            max_value=30,
            value=int(epi_cfg.get("fps_start", 24) or 24),
            step=1,
            key=f"ep_fps_start_{ep_key}",
            help="Первый отрезок ролика, обычно 24.",
        )
    with fps2:
        st.number_input(
            "Конечный FPS",
            min_value=1,
            max_value=30,
            value=int(epi_cfg.get("fps_end", 2) or 2),
            step=1,
            key=f"ep_fps_end_{ep_key}",
            help="К этому значению fps падает ступеньками. Для скорости хвоста: 2 или 1.",
        )
    with fps3:
        st.number_input(
            "Шаг, минуты",
            min_value=5.0,
            max_value=180.0,
            value=float(epi_cfg.get("fps_interval_min", 30) or 30),
            step=5.0,
            key=f"ep_fps_every_{ep_key}",
            help="Раз в столько минут fps снижается на одну ступень от начального к конечному.",
        )
    fields = tail_fields_from_session(ep_key, epi_cfg)
    estimate = (
        float(epi_cfg["target_min_minutes"]) + float(epi_cfg["target_max_minutes"])
    ) / 2.0 * 60.0
    st.caption(
        describe_tail(
            _voice_seconds(episode_dir, ep_key),
            enabled=bool(fields["ambient_tail_enabled"]),
            target_minutes=float(fields["target_total_minutes"]),
            estimated_voice_sec=estimate,
        )
    )
    schedule_sec = (
        float(fields["target_total_minutes"]) * 60.0
        if fields["ambient_tail_enabled"]
        else estimate
    )
    st.caption(describe_fps_schedule(
        schedule_sec,
        int(fields["fps_start"]),
        int(fields["fps_end"]),
        float(fields["fps_interval_min"]),
    ))
    floor_pct = int(round(float(fields["dim_min_brightness"]) * 100))
    st.caption(
        f"Оверлей — на хвост и финал. Затемнение: до **{floor_pct}%** яркости "
        f"({'во время речи с ' + str(fields['dim_start_minutes']) + ' мин' if fields['dim_during_speech'] else 'только после речи'}). "
        "Блок 4 (сюжет) — всегда без затемнения."
    )
