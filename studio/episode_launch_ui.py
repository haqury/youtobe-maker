"""Блок «Эпизод и запуск» в студии."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

import streamlit as st

if TYPE_CHECKING:
    import pandas as pd

from channel_settings import load_channel_settings, save_channel_settings
from intro_brand import INTRO_STYLE_CHOICES
from playlist_manager import (
    STANDALONE_ID,
    STANDALONE_UI_LABEL,
    backlog_row_by_episode_id,
    create_playlist,
    folder_matches_playlist,
    format_episode_folder_label,
    next_series_episode_from_folders,
    next_series_episode_id,
    playlist_by_id,
    playlist_dir_for_series,
    read_episode_idea,
    slugify_episode_slug,
)
from studio_ai import generate_next_series_episode

def _popover(label: str):
    if hasattr(st, "popover"):
        return st.popover(label)
    return st.expander(label, expanded=False)


def render_episode_launch_controls(
    *,
    channel_slug: str,
    ch: Path,
    episodes_root: Path,
    episode_by_name: dict[str, Path],
    episode_names: list[str],
    playlist_key: str,
    playlist_ids: list[str],
    playlist_labels: list[str],
    write_nav_to_url,
    remember_episode_title: Callable[..., None],
    rename_playlist: Callable[..., None],
    next_manual_standalone_id: Callable[[Path], str],
    ensure_episode_files: Callable[..., None],
    load_channel_backlog: Callable[..., "pd.DataFrame"],
) -> tuple[str, Path | None]:
    """Плейлист → папка → кнопки правки/создания. Возвращает (selected_playlist, episode_dir)."""
    cur_pl = st.session_state[playlist_key]
    if cur_pl not in playlist_ids:
        cur_pl = STANDALONE_ID
        st.session_state[playlist_key] = cur_pl

    selected_playlist = playlist_ids[
        playlist_labels.index(
            st.selectbox(
                "Плейлист",
                playlist_labels,
                index=playlist_ids.index(cur_pl),
                key=f"studio_playlist_select_{channel_slug}",
            )
        )
    ]
    st.session_state[playlist_key] = selected_playlist

    backlog = load_channel_backlog(ch / "backlog.csv")
    filtered_episodes = [
        name
        for name in episode_names
        if folder_matches_playlist(
            name,
            backlog,
            selected_playlist,
            episode_by_name.get(name),
        )
    ]
    episode_dir: Path | None = None
    if filtered_episodes:
        current_ep = st.session_state.get("episode_name") or filtered_episodes[0]
        if current_ep not in filtered_episodes:
            current_ep = filtered_episodes[0]
        picked_ep = st.selectbox(
            "Папка эпизода",
            filtered_episodes,
            index=filtered_episodes.index(current_ep),
            format_func=lambda name: format_episode_folder_label(
                name, ch, backlog, episode_by_name.get(name)
            ),
            key=f"studio_ep_select_{channel_slug}_{selected_playlist}",
        )
        st.session_state.episode_name = picked_ep
        episode_dir = episode_by_name.get(picked_ep) or (episodes_root / picked_ep)
        ensure_episode_files(episode_dir)
        write_nav_to_url()
    else:
        st.info("Нет папок эпизодов для этого плейлиста — создайте видео ниже.")

    e1, e2, e3 = st.columns(3)
    with e1:
        with _popover("✏️ Название плейлиста"):
            if selected_playlist == STANDALONE_ID:
                st.caption("Только для сериалов — выберите плейлист серии.")
            else:
                current_playlist = playlist_by_id(ch, selected_playlist) or {}
                rename_key = f"playlist_rename_{channel_slug}_{selected_playlist}"
                if rename_key not in st.session_state:
                    st.session_state[rename_key] = str(
                        current_playlist.get("title_ru") or current_playlist.get("title_en") or ""
                    )
                st.text_input("Название этого плейлиста", key=rename_key)
                if st.button(
                    "Сохранить",
                    key=f"save_playlist_name_pop_{channel_slug}_{selected_playlist}",
                    use_container_width=True,
                ):
                    renamed = str(st.session_state.get(rename_key, "")).strip()
                    if renamed:
                        rename_playlist(ch, selected_playlist, renamed)
                        st.session_state.pop(f"studio_playlist_select_{channel_slug}", None)
                        st.success("Сохранено")
                        st.rerun()
                    else:
                        st.error("Введите название.")

    with e2:
        with _popover("✏️ Стиль текста интро"):
            if selected_playlist == STANDALONE_ID:
                st.caption("Интро серии задаётся в плейлисте сериала.")
            else:
                series_path = playlist_dir_for_series(ch, selected_playlist)
                if series_path is None:
                    st.caption("Плейлист не найден.")
                else:
                    series_settings = load_channel_settings(series_path)
                    style_ids = [code for code, _name in INTRO_STYLE_CHOICES]
                    style_labels = [name for _code, name in INTRO_STYLE_CHOICES]
                    current_style = str(
                        series_settings.get("branding", {}).get("intro_text_style") or "gold_serif"
                    )
                    if current_style not in style_ids:
                        current_style = "gold_serif"
                    picked_style = style_ids[
                        style_labels.index(
                            st.selectbox(
                                "Стиль",
                                style_labels,
                                index=style_ids.index(current_style),
                                key=f"intro_style_pop_{channel_slug}_{selected_playlist}",
                            )
                        )
                    ]
                    if st.button(
                        "Сохранить стиль",
                        key=f"save_intro_pop_{channel_slug}_{selected_playlist}",
                        use_container_width=True,
                    ):
                        series_settings.setdefault("branding", {})["intro_text_style"] = picked_style
                        save_channel_settings(series_path, series_settings)
                        st.success("Сохранено")
                        st.rerun()

    with e3:
        with _popover("✏️ Название этого видео"):
            if episode_dir is None:
                st.caption("Сначала выберите или создайте папку эпизода.")
            else:
                current_title_key = f"video_title_{channel_slug}_{episode_dir.name}"
                if current_title_key not in st.session_state:
                    idea = read_episode_idea(episode_dir)
                    row = backlog_row_by_episode_id(backlog, episode_dir.name.split("_", 1)[0])
                    saved = ""
                    if row is not None:
                        saved = str(row.get("Topic_RU") or row.get("Topic") or "").strip()
                    st.session_state[current_title_key] = (
                        saved or str(idea.get("topic_ru") or idea.get("topic") or "")
                    )
                st.text_input("Название", key=current_title_key)
                if st.button(
                    "Сохранить",
                    key=f"save_video_title_pop_{channel_slug}_{episode_dir.name}",
                    use_container_width=True,
                ):
                    current_title = str(st.session_state.get(current_title_key, "")).strip()
                    if not current_title:
                        st.error("Введите название.")
                    else:
                        ep_id = episode_dir.name.split("_", 1)[0]
                        row = backlog_row_by_episode_id(backlog, ep_id)
                        series_id = (
                            str(row.get("Series_ID") or selected_playlist)
                            if row is not None
                            else selected_playlist
                        )
                        episode_num = (
                            str(row.get("Episode_Num") or "1") if row is not None else "1"
                        )
                        remember_episode_title(ch, episode_dir, current_title, series_id, episode_num)
                        st.success("Сохранено")
                        st.rerun()

    c1, c2, c3 = st.columns(3)
    new_playlist_key = f"new_playlist_name_{channel_slug}"
    playlist_error_key = f"playlist_create_error_{channel_slug}"
    playlist_ok_key = f"playlist_create_ok_{channel_slug}"

    with c1:
        with _popover("➕ Создать плейлист"):
            st.text_input(
                "Название нового плейлиста",
                key=new_playlist_key,
                placeholder="Например: Ночной лес",
            )
            if st.button("Создать", key=f"create_playlist_pop_{channel_slug}", use_container_width=True):
                name = str(st.session_state.get(new_playlist_key, "")).strip()
                try:
                    created = create_playlist(ch, title=name)
                except ValueError as exc:
                    st.error(str(exc))
                else:
                    st.session_state[playlist_key] = str(created["id"])
                    st.session_state.pop(f"studio_playlist_select_{channel_slug}", None)
                    st.session_state[new_playlist_key] = ""
                    st.success(f"Плейлист {created['id']}")
                    st.rerun()

    new_video_key = f"new_video_name_{channel_slug}_{selected_playlist}"
    video_error_key = f"new_video_error_{channel_slug}_{selected_playlist}"
    video_ok_key = f"new_video_ok_{channel_slug}_{selected_playlist}"

    with c2:
        with _popover("➕ Создать видео"):
            st.text_input(
                "Название нового видео",
                key=new_video_key,
                placeholder="Например: Ночь в лесу — урок 1",
            )
            if st.button("Создать папку", key=f"create_video_pop_{channel_slug}", use_container_width=True):
                video_title = str(st.session_state.get(new_video_key, "")).strip()
                if not video_title:
                    st.error("Введите название.")
                else:
                    if selected_playlist == STANDALONE_ID:
                        episode_id = next_manual_standalone_id(ch)
                        episode_num = "1"
                        folder_root = episodes_root
                    else:
                        _, from_folders = next_series_episode_from_folders(ch, selected_playlist)
                        _, from_backlog = next_series_episode_id(
                            backlog,
                            selected_playlist,
                        )
                        episode_num_int = max(from_folders, from_backlog)
                        episode_id = f"{selected_playlist.upper()}E{episode_num_int:02d}"
                        episode_num = str(episode_num_int)
                        playlist_folder = playlist_dir_for_series(ch, selected_playlist)
                        folder_root = (
                            (playlist_folder / "episodes") if playlist_folder is not None else episodes_root
                        )
                        folder_root.mkdir(parents=True, exist_ok=True)
                    folder = folder_root / f"{episode_id}_{slugify_episode_slug(video_title)}"
                    if folder.exists():
                        st.error(f"Папка уже есть: {folder.name}")
                    else:
                        folder.mkdir(parents=True)
                        ensure_episode_files(folder, topic_en=video_title, topic_ru=video_title)
                        remember_episode_title(ch, folder, video_title, selected_playlist, episode_num)
                        st.session_state.episode_name = folder.name
                        st.session_state.pop(f"studio_ep_select_{channel_slug}_{selected_playlist}", None)
                        st.session_state[new_video_key] = ""
                        st.success(folder.name)
                        st.rerun()

    with c3:
        with _popover("➕ Следующее видео"):
            if selected_playlist == STANDALONE_ID:
                st.caption("Для сериала выберите плейлист, не Standalone.")
            else:
                hint_key = f"next_series_hint_{channel_slug}_{selected_playlist}"
                st.text_input(
                    "Пожелание к сюжету (необязательно)",
                    key=hint_key,
                    placeholder="напр.: герой идёт на ночной рынок",
                )
                if st.button("Сгенерировать эпизод N+1", key=f"next_ep_pop_{channel_slug}", use_container_width=True):
                    with st.spinner("ИИ — следующий выпуск…"):
                        new_ep_id, err = generate_next_series_episode(
                            ch,
                            series_id=selected_playlist,
                            preference_keywords=st.session_state.get(f"idea_prefs_kw_{channel_slug}", ""),
                            preference_brief=st.session_state.get(f"idea_prefs_brief_{channel_slug}", ""),
                            user_hint=st.session_state.get(hint_key, "") or "",
                        )
                    if err:
                        st.error(err)
                    else:
                        st.success(new_ep_id)
                        st.rerun()

    if st.session_state.get(playlist_error_key):
        st.error(st.session_state[playlist_error_key])
    elif st.session_state.get(playlist_ok_key):
        st.success(f"Плейлист {st.session_state[playlist_ok_key]}")

    return selected_playlist, episode_dir