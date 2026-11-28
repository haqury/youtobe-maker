"""Экран студии для канала «язык во сне»: блоки сценария и кэш уникальных фраз."""

from __future__ import annotations

from pathlib import Path
from collections.abc import Callable

import streamlit as st

from isekai_config import (
    SLEEP_VOICES,
    load_isekai_settings,
    speech_pitch,
    speech_speed_bias,
    write_isekai_settings,
)
from isekai_voice import phrase_inventory


def _channel_root(episode_dir: Path) -> Path | None:
    for folder in (episode_dir, *episode_dir.parents):
        if (folder / "channel_config.md").is_file():
            return folder
    return None


def _voice_script_stale(episode_dir: Path) -> bool:
    script = episode_dir / "script.md"
    voice = episode_dir / "voiceover.mp3"
    if not script.is_file():
        return False
    if not voice.is_file():
        return True
    return script.stat().st_mtime > voice.stat().st_mtime


def _launch_voice_job(
    channel_slug: str,
    episode_dir: Path,
    files: dict[str, Path],
    save_texts: Callable[[str, dict[str, Path]], int],
    launch_job: Callable[..., None],
) -> None:
    saved = save_texts(channel_slug, files)
    if saved:
        st.toast(f"Сохранено файлов: {saved}")
    launch_job("generation", episode_dir, "generate_voice.py")
    st.success("Озвучка в фоне — смотри 📡 прогресс.")


def _voice_rows(lang: str, current: str) -> list[tuple[str, str]]:
    rows = list(SLEEP_VOICES.get(lang, ()))
    if current and current not in {voice for voice, _label in rows}:
        rows.insert(0, (current, f"Текущий — {current}"))
    return rows


def _pick_voice(label: str, lang: str, current: str, key: str) -> str:
    rows = _voice_rows(lang, current)
    labels = [caption for _voice, caption in rows]
    voices = [voice for voice, _caption in rows]
    index = voices.index(current) if current in voices else 0
    picked = st.selectbox(label, labels, index=index, key=key)
    return voices[labels.index(picked)]


def _render_isekai_voice_block(episode_dir: Path, ep_key: str) -> None:
    settings = load_isekai_settings(episode_dir)
    target = settings.get("TARGET_LANG", "?")
    native = settings.get("NATIVE_LANG", "?")
    st.caption(
        f"Урок: **{target}**, пояснения: **{native}**. "
        "Растянутость и мягкость влияют на следующую сборку voiceover.mp3."
    )
    native_voice = _pick_voice(
        "Пояснения на английском",
        native,
        settings.get("MENTOR_NATIVE", ""),
        f"isekai_voice_native_{ep_key}",
    )
    target_voice = _pick_voice(
        "Испанский урок",
        target,
        settings.get("MENTOR_TARGET", ""),
        f"isekai_voice_target_{ep_key}",
    )
    npc_voice = _pick_voice(
        "Реплики NPC",
        target,
        settings.get("NPC_TARGET", ""),
        f"isekai_voice_npc_{ep_key}",
    )
    stretch = st.slider(
        "Растянутость",
        min_value=0.75,
        max_value=1.0,
        value=float(speech_speed_bias(settings)),
        step=0.01,
        key=f"isekai_stretch_{ep_key}",
        help="1.00 — темп из сценария. Ниже — медленнее и длиннее.",
    )
    pitch_hz = int(speech_pitch(settings).replace("Hz", "").replace("+", "") or "0")
    softness = st.slider(
        "Мягкость тона",
        min_value=0,
        max_value=16,
        value=max(0, min(16, -pitch_hz if pitch_hz < 0 else 0)),
        step=1,
        key=f"isekai_soft_{ep_key}",
        help="0 — без снижения. 8 Гц — тихий мужской Альваро. Выше 12 уже заметно глухо.",
    )
    if st.button("Сохранить голос", key=f"isekai_voice_save_{ep_key}"):
        write_isekai_settings(
            episode_dir,
            {
                "MENTOR_NATIVE": native_voice,
                "MENTOR_TARGET": target_voice,
                "NPC_TARGET": npc_voice,
                "SPEECH_SPEED_BIAS": f"{stretch:.2f}",
                "SPEECH_PITCH": f"-{int(softness)}Hz",
            },
        )
        st.rerun()


def render_isekai_production(
    episode_dir: Path,
    channel_slug: str,
    ep_key: str,
    *,
    editor_key: Callable[[Path, str], str],
    save_texts: Callable[[str, dict[str, Path]], int],
    launch_job: Callable[..., None],
    acknowledge,
    sync_editors,
    show_section_headings: bool = True,
    blocks: tuple[str, ...] = ("script", "voice"),
) -> None:
    settings = load_isekai_settings(episode_dir)
    target = settings.get("TARGET_LANG", "?")
    native = settings.get("NATIVE_LANG", "?")
    show_script = "script" in blocks
    show_voice = "voice" in blocks
    inventory: dict = {"ok": False}
    files = {
        "script.md (блоки)": episode_dir / "script.md",
        "visual_prompts.md": episode_dir / "visual_prompts.md",
        "metadata.md": episode_dir / "metadata.md",
    }

    if show_script:
        if show_section_headings:
            st.markdown("---")
            st.subheader("1 — Сценарий блоками")
        st.caption(
            f"Урок: **{target}**, пояснения: **{native}**. "
            "В script.md три части: сюжет на родном языке, RECALL, тот же сюжет на выбранном языке."
        )
        st.caption("`script_ru.md` этот формат не озвучивает. Перевод сюжета пишется в блоках PLOT_TARGET.")

        brief_key = f"isekai_brief_{ep_key}"
        brief_path = episode_dir / "plot_brief.txt"
        if brief_key not in st.session_state:
            st.session_state[brief_key] = (
                brief_path.read_text(encoding="utf-8-sig") if brief_path.is_file() else ""
            )
        st.text_area(
            "О чём эта ночь",
            height=90,
            key=brief_key,
            placeholder="Например: кот ведёт через ночной лес к повозке. Новые слова: bosque, gato, linterna.",
        )
        if st.button(
            "✍️ Сгенерировать блоки по этому брифу",
            key=f"isekai_gen_{ep_key}",
            type="primary",
        ):
            brief_path.write_text(str(st.session_state.get(brief_key, "")).strip() + "\n", encoding="utf-8")
            launch_job("generation", episode_dir, "generate_isekai_script.py")
            st.caption(
                "Дописывает RECALL и сюжет на выбранном языке. "
                "Готовые акты 001–108 не переписывает."
            )

        acknowledge(episode_dir, ep_key)
        sync_editors(channel_slug, files)
        tabs = st.tabs(list(files.keys()))
        for tab, (label, path) in zip(tabs, files.items()):
            with tab:
                content = path.read_text(encoding="utf-8-sig") if path.is_file() else ""
                st.text_area(label, content, height=320, key=editor_key(path, channel_slug))
        save_col, voice_col = st.columns([1, 1])
        with save_col:
            if st.button("💾 Сохранить тексты", key=f"isekai_save_{ep_key}", use_container_width=True):
                saved = save_texts(channel_slug, files)
                st.success(f"Сохранено файлов: {saved}")
                st.rerun()
        with voice_col:
            if st.button(
                "🎙️ Сохранить и пересобрать озвучку",
                key=f"isekai_save_voice_{ep_key}",
                type="primary",
                use_container_width=True,
                help="Пишет script.md с вкладок и запускает generate_voice.py → voiceover.mp3 (только script + audio_cache).",
            ):
                _launch_voice_job(
                    channel_slug,
                    episode_dir,
                    files,
                    save_texts,
                    launch_job,
                )

        script_key = editor_key(episode_dir / "script.md", channel_slug)
        editor_text = st.session_state.get(script_key)
        inventory = phrase_inventory(
            episode_dir,
            text=str(editor_text) if editor_text is not None else None,
        )
        if not inventory["ok"]:
            st.warning(inventory["error"])
        else:
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Уникальных фраз", inventory["unique"])
            c2.metric("Уже в кэше", inventory["cached"])
            c3.metric("Нужно озвучить", inventory["missing"])
            c4.metric("Вставок в ролик", inventory["timeline_lines"])
            st.caption(
                "В edge-tts уходят только строки «Нужно озвучить». "
                "Повторы плейлиста склеиваются из кэша."
            )
            table = [
                {
                    "Блок": row["block"],
                    "ID": row["id"],
                    "Роль": row["role"],
                    "Скорость": row["speed"],
                    "Фраза": row["text"],
                    "Кэш": "есть" if row["cached"] else "новая",
                }
                for row in inventory["rows"]
            ]
            st.dataframe(table, hide_index=True, use_container_width=True)
            with st.expander("Как плейлист растягивает эти фразы", expanded=False):
                for play in inventory["plays"]:
                    st.write(
                        f"`{play['block']}` · {play['mode']} · повтор {play['repeat']} · "
                        f"темп ×{play['speed_mult']} · пауза {play['extra_pause']} с"
                    )

    if not show_voice:
        return

    if not show_script:
        inventory = phrase_inventory(episode_dir)

    if show_section_headings:
        st.markdown("---")
        st.subheader("2 — Озвучка")
    _render_isekai_voice_block(episode_dir, ep_key)
    stale = _voice_script_stale(episode_dir)
    if stale:
        st.info(
            "`script.md` новее `voiceover.mp3` (или озвучки ещё нет). "
            "После правок сценария нажми **пересобрать** — досинтезируются только новые/изменённые фразы."
        )
    voice = episode_dir / "voiceover.mp3"
    play_col, act_col = st.columns([3, 1])
    with play_col:
        if voice.is_file():
            st.caption("Одна дорожка: EN + ES по PLAYLIST_SEQUENCE. Новые строки — TTS, остальное из кэша.")
            st.audio(voice.read_bytes(), format="audio/mp3")
        else:
            st.caption("voiceover.mp3 ещё нет. Кнопка справа запустит длинную фоновую сборку.")
    with act_col:
        missing = int(inventory.get("missing", 0)) if inventory.get("ok") else 0
        if missing > 0:
            label = f"🎙️ Озвучить новые ({missing})"
        elif stale or not voice.is_file():
            label = "🔄 Пересобрать voiceover"
        else:
            label = "🔁 Собрать ролик из кэша"
        if st.button(label, key=f"isekai_voice_{ep_key}", use_container_width=True):
            from tail_controls import save_tail_fields

            save_tail_fields(episode_dir, channel_slug, ep_key)
            _launch_voice_job(
                channel_slug,
                episode_dir,
                files,
                save_texts,
                launch_job,
            )
