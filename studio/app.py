"""Веб-панель управления YouTube-контент-фермой (Streamlit)."""

from __future__ import annotations

import re
import shutil
import statistics
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_SCRIPTS = ROOT / "scripts"
for path in (ROOT, _SCRIPTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
from isekai_config import load_isekai_settings
from episode_review import (
    apply_video_segments_to_images,
    flag_images_batch,
    flagged_image_names,
    flagged_stems,
    image_note_map,
    is_animate_frame,
    load_review,
    normalize_animate_frames,
    remove_video_segment,
    save_review,
    stem_from_filename,
    toggle_animate_frame,
    toggle_problem_image,
    update_video_segment_note,
)
from job_manager import (
    episode_has_running_job,
    get_job_details,
    get_jobs,
    job_can_restart,
    restart_job,
    start_script_job,
    stop_job,
)
from niche_advisor import (
    load_presets,
    niche_to_form_fields,
    suggest_niches_ai,
    suggest_niches_catalog,
)
import importlib

import channel_settings
import intro_brand
import playlist_manager
import studio_ai
import tail_controls

importlib.reload(channel_settings)
importlib.reload(intro_brand)
importlib.reload(playlist_manager)
importlib.reload(studio_ai)
importlib.reload(tail_controls)
from intro_brand import INTRO_STYLE_CHOICES
from channel_settings import (
    apply_intro_to_series,
    brand_asset_path,
    channel_display_name,
    ensure_brand_assets_dir,
    load_channel_settings,
    save_channel_settings,
)
from episode_snapshot import list_episode_snapshots, restore_episode_snapshot, snapshot_display_line
from episode_timing import auto_frame_count
from tail_controls import render_ambient_tail_controls, tail_fields_from_session
from studio_ai import (
    episode_plan_summary,
    expand_backlog_idea_to_series,
    generate_backlog_ideas,
    generate_next_series_episode,
    load_episode_config,
    load_plot_brief,
    regenerate_series_bible_from_brief,
    save_episode_config,
    save_plot_brief,
    sync_and_regenerate_bible,
)
from playlist_manager import (
    BACKLOG_COLUMNS,
    STANDALONE_ID,
    STANDALONE_UI_LABEL,
    filter_backlog_by_playlist,
    folder_matches_playlist,
    format_backlog_row_label,
    format_episode_folder_label,
    list_episode_dirs,
    create_playlist,
    load_playlists,
    playlist_id_from_episode_dir,
    next_series_episode_from_folders,
    next_series_episode_id,
    normalize_backlog_frame,
    save_playlists,
    playlist_label,
    next_standalone_episode_id,
    BIBLE_SECTION_KEYS,
    ensure_series_bible_artifacts,
    load_series_bible_json,
    playlist_by_id,
    playlist_dir_for_series,
    read_episode_idea,
    read_producer_brief,
    read_series_bible,
    save_producer_brief,
    save_series_bible_data,
    backlog_row_by_episode_id,
    purge_series_from_backlog,
    row_can_expand_to_series,
    write_episode_idea,
    series_bible_path,
)
from ai_router import (
    DEFAULT_TEXT_FALLBACK_CHAIN,
    IMAGE_MODELS,
    IMAGE_PROVIDERS,
    TEXT_MODELS,
    TEXT_PROVIDERS,
    generate_text,
    get_ollama_models,
    load_ai_settings,
    normalize_text_fallback_chain,
    ollama_server_up,
    provider_has_key,
    save_ai_settings,
)
from local_sd import list_local_sd_models, local_sd_status
from project_update import project_status, pull_master, schedule_restart
from quota_tracker import (
    all_provider_statuses,
    estimate_episode_workload,
    estimate_youtube_scout_units,
    load_specs,
    save_specs,
    refresh_remote_quotas,
    sync_usage_from_logs,
)

try:
    from quota_tracker import refresh_cloudflare_remote
except ImportError:
    def refresh_cloudflare_remote() -> dict:
        """Совместимость со старым quota_tracker / кэшем Streamlit."""
        remote = refresh_remote_quotas()
        if isinstance(remote, dict):
            cf = remote.get("cloudflare_workers_ai")
            if isinstance(cf, dict):
                return cf
        return {}

from components.video_marker import video_marker

import pandas as pd
import streamlit as st


def _streamlit_supports_popover() -> bool:
    """st.popover доступен с Streamlit 1.33."""
    if not hasattr(st, "popover"):
        return False
    try:
        parts = [int(x) for x in str(getattr(st, "__version__", "0")).split(".")[:3]]
        while len(parts) < 3:
            parts.append(0)
        return tuple(parts) >= (1, 33, 0)
    except (ValueError, TypeError):
        return True
CHANNELS_DIR = ROOT / "channels"
TEMPLATES_DIR = ROOT / "templates"
SCRIPTS_DIR = ROOT / "scripts"
MARKET_DIR = ROOT / "market_radar"
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
SLEEP_CHANNEL = "channel_02_sleep_history"
NAV_SECTIONS: dict[str, str] = {
    "analytics": "Аналитика и бэклог",
    "studio": "🎬 Студия производства",
    "channel_settings": "⚙️ Настройки канала",
    "capacity": "⚙️ Мощности и Лимиты",
    "jobs": "📡 Фоновые задачи",
    "radar": "Радар и публикация",
    "new_channel": "Новый канал",
}



def list_channels() -> list[str]:
    if not CHANNELS_DIR.is_dir():
        return []
    channels = [
        path.name
        for path in CHANNELS_DIR.iterdir()
        if path.is_dir() and (path / "channel_config.md").is_file()
    ]
    return sorted(channels)


def channel_dir(slug: str) -> Path:
    return CHANNELS_DIR / slug


def run_script(script_name: str, *args: str, timeout: int = 7200) -> subprocess.CompletedProcess[str]:
    command = [sys.executable, str(SCRIPTS_DIR / script_name), *args]
    return subprocess.run(
        command,
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


JOB_TYPE_LABELS = {
    "generation": "Генерация",
    "regeneration": "Регенерация",
    "build_video": "Сборка",
    "full_pipeline": "Полный цикл",
}
JOB_STATUS_LABELS = {
    "running": "⏳ В процессе",
    "done": "✅ Готово",
    "error": "❌ Ошибка",
    "stopped": "⏹ Остановлено",
}


def episode_job_path(episode_dir: Path) -> str:
    return str(episode_dir.resolve())


def launch_background_job(job_type: str, episode_dir: Path, script_name: str, *args: str) -> None:
    ep = episode_job_path(episode_dir)
    active = [row for row in get_jobs(200) if row.get("status") == "running" and row.get("episode") == ep]
    if active:
        st.warning("Уже выполняется фоновая задача для этого эпизода.")
        st.toast("Задача уже выполняется для этого эпизода", icon="⚠️")
        return
    job_id = start_script_job(job_type, episode_dir, script_name, str(episode_dir), *args)
    st.session_state["studio_active_job"] = job_id
    st.success(f"Задача `{job_id}` запущена. Смотрите 📡 вверху справа.")
    st.toast(f"Запущено: {job_id}", icon="▶️")
    st.rerun()


def offer_restart_job(job: dict, *, key: str) -> None:
    if not job_can_restart(job):
        if job.get("status") in ("error", "stopped") and not job.get("script_name"):
            st.caption("Перезапуск недоступен — в записи нет скрипта (запуск до обновления).")
        return
    script = job.get("script_name", "—")
    if st.button(
        f"🔁 Перезапустить ({script})",
        key=key,
        help="Тот же скрипт и аргументы, новая запись в журнале",
    ):
        new_id, err = restart_job(str(job.get("id")))
        if err:
            st.warning(err)
            st.toast(err, icon="⚠️")
            return
        st.session_state["studio_active_job"] = new_id
        st.success(f"Перезапуск: `{new_id}`")
        st.toast(f"Перезапуск: {new_id}", icon="🔁")
        st.rerun()


def format_job_episode_label(episode_path: str) -> str:
    try:
        return str(Path(episode_path).resolve().relative_to(ROOT))
    except ValueError:
        return Path(episode_path).name


def format_job_interval(job: dict) -> str:
    started = job.get("started_at") or "—"
    finished = job.get("finished_at") or "…"
    return f"{started} → {finished}"


def render_jobs_monitor(episode_dir: Path, ep_key: str, *, auto_refresh: bool = True) -> None:
    st.subheader("📋 Очередь и история генераций")
    ep_path = episode_job_path(episode_dir)
    jobs = get_jobs(80)
    running = [row for row in jobs if row.get("status") == "running"]
    running_here = [row for row in running if row.get("episode") == ep_path]

    head_l, head_r = st.columns([4, 1])
    with head_r:
        if st.button("🔄 Обновить статус", key=f"jobs_refresh_{ep_key}"):
            st.rerun()

    if running_here:
        job = running_here[0]
        st.markdown(f"**Активная задача** `{job['id']}` · {JOB_TYPE_LABELS.get(job.get('type'), job.get('type'))}")
        st.progress(float(job.get("progress") or 0.0), text=str(job.get("step_text") or "…"))
        if st.button("⏹ Остановить", key=f"stop_job_{job['id']}"):
            stop_job(job["id"])
            st.toast("Остановка отправлена")
            st.rerun()
    elif running:
        st.caption(f"В фоне: {len(running)} задач(и) на других эпизодах.")

    st.markdown("**Журнал**")
    if not jobs:
        st.caption("Запусков пока нет.")
    for job in jobs:
        type_badge = JOB_TYPE_LABELS.get(str(job.get("type")), str(job.get("type")))
        status_badge = JOB_STATUS_LABELS.get(str(job.get("status")), str(job.get("status")))
        ep_label = format_job_episode_label(str(job.get("episode", "")))
        title = f"`{job.get('id', '—')}` · {type_badge} · {status_badge} · {ep_label}"
        with st.expander(title, expanded=False):
            st.caption(format_job_interval(job))
            if job.get("step_text"):
                st.write(job["step_text"])
            details = get_job_details(str(job.get("id")))
            log_text = (details or {}).get("log_tail") or ""
            if log_text:
                st.code(log_text, language="text")
            else:
                st.caption("Лог пуст или ещё не создан.")
            offer_restart_job(job, key=f"restart_job_monitor_{job.get('id')}")

    if auto_refresh and running:
        import time

        time.sleep(2)
        st.rerun()


def inject_jobs_fab_css() -> None:
    st.markdown(
        """
<style>
/* FAB: фиксированный поповер задач (обходим transform у stVerticalBlock) */
section.main .block-container { padding-top: 0.5rem; }
.st-key-jobs_fab_popover,
.st-key-jobs_fab_popover > div,
section.main .st-key-jobs_fab_popover,
section.main .st-key-jobs_fab_popover [data-testid="stVerticalBlock"],
[data-testid="stVerticalBlock"].st-key-jobs_fab_popover,
[data-testid="stVerticalBlockBorderWrapper"].st-key-jobs_fab_popover {
    position: fixed !important;
    top: 3.5rem !important;
    right: 1rem !important;
    left: auto !important;
    width: auto !important;
    min-width: 4.25rem !important;
    max-width: 6.5rem !important;
    z-index: 99999 !important;
    margin: 0 !important;
    padding: 0 !important;
    pointer-events: auto !important;
    transform: none !important;
}
.st-key-jobs_fab_popover [data-testid="stPopover"] { width: 100%; pointer-events: auto !important; }
.st-key-jobs_fab_popover button {
    font-weight: 600 !important;
    box-shadow: 0 2px 14px rgba(0, 0, 0, 0.35) !important;
    border-radius: 0.65rem !important;
    white-space: nowrap;
    pointer-events: auto !important;
    background-color: var(--secondary-background-color, #262730) !important;
    border: 1px solid rgba(250, 250, 250, 0.2) !important;
}
.st-key-jobs_fab_popover button p { font-size: 0.92rem !important; }
</style>
""",
        unsafe_allow_html=True,
    )


def render_jobs_popover_panel(
    jobs: list[dict],
    running: list[dict],
    count: int,
    *,
    key_prefix: str = "",
) -> None:
    """Содержимое popover: активные + последние 10."""
    st.markdown("**Фоновые задачи**")
    if running:
        st.caption(f"Активных: **{count}**")
        for job in running[:5]:
            type_badge = JOB_TYPE_LABELS.get(str(job.get("type")), str(job.get("type")))
            ep_label = format_job_episode_label(str(job.get("episode", "")))
            st.progress(
                float(job.get("progress") or 0.0),
                text=f"`{job.get('id')}` · {type_badge} · {job.get('step_text') or '…'}",
            )
            st.caption(ep_label)
    else:
        st.caption("Сейчас ничего не выполняется.")

    st.markdown("**Последние 10**")
    recent = jobs[:10]
    if not recent:
        st.caption("Запусков пока не было.")
    for job in recent:
        status_badge = JOB_STATUS_LABELS.get(str(job.get("status")), str(job.get("status")))
        type_badge = JOB_TYPE_LABELS.get(str(job.get("type")), str(job.get("type")))
        ep_label = format_job_episode_label(str(job.get("episode", "")))
        line = (
            f"`{job.get('id', '—')}` · {type_badge} · {status_badge} · "
            f"{(job.get('step_text') or '—')[:72]}"
        )
        with st.expander(line, expanded=job.get("status") == "running"):
            st.caption(ep_label)
            st.caption(format_job_interval(job))
            details = get_job_details(str(job.get("id")))
            log_text = ((details or {}).get("log_tail") or "")[-3500:]
            if log_text:
                st.code(log_text, language="text")
            if job.get("status") == "running" and st.button(
                "⏹ Остановить",
                key=f"{key_prefix}fab_stop_{job.get('id')}",
            ):
                stop_job(str(job.get("id")))
                st.toast("Остановка отправлена")
                st.rerun()
            offer_restart_job(job, key=f"{key_prefix}fab_restart_{job.get('id')}")

    if st.button(
        "Все задачи →",
        key=f"{key_prefix}jobs_popover_goto_page",
        use_container_width=True,
    ):
        st.session_state.nav_section = "jobs"
        write_nav_to_url()
        st.rerun()


def render_sidebar_jobs_widget() -> None:
    """Боковая панель: счётчик и поповер/экспандер задач."""
    jobs = get_jobs(50)
    running = [row for row in jobs if row.get("status") == "running"]
    count = len(running)
    st.sidebar.caption(f"Активных фоновых задач: **{count}**")
    popover_label = f"📡 Задачи · {count}"
    if _streamlit_supports_popover():
        with st.sidebar.popover(popover_label, help="Фоновые задачи"):
            render_jobs_popover_panel(jobs, running, count, key_prefix="sidebar_")
    else:
        with st.sidebar.expander(f"📡 Задачи · {count}", expanded=False):
            render_jobs_popover_panel(jobs, running, count, key_prefix="sidebar_")
    st.sidebar.markdown("---")


def render_global_jobs_toolbar() -> int:
    """Фиксированный 📡 (popover) в правом верхнем углу."""
    inject_jobs_fab_css()
    jobs = get_jobs(50)
    running = [row for row in jobs if row.get("status") == "running"]
    count = len(running)
    label = f"📡 {count}" if count else "📡 0"

    with st.container(key="jobs_fab_popover"):
        if _streamlit_supports_popover():
            with st.popover(label, help="Фоновые задачи"):
                render_jobs_popover_panel(jobs, running, count, key_prefix="fab_")
        elif st.button(label, key="jobs_fab_open_jobs", help="Открыть раздел задач"):
            st.session_state.nav_section = "jobs"
            write_nav_to_url()
            st.rerun()

    return count


def maybe_auto_refresh_jobs(active_count: int) -> None:
    if active_count and st.session_state.get("nav_section") in {"studio", "jobs"}:
        import time

        time.sleep(2)
        st.rerun()


def page_jobs(channel_slug: str) -> None:
    st.header("📡 Фоновые задачи")
    st.caption("Все запуски скриптов из студии: озвучка, кадры, ComfyUI, сборка.")
    jobs = get_jobs(300)
    running = [row for row in jobs if row.get("status") == "running"]
    c1, c2, c3 = st.columns(3)
    c1.metric("Активных", len(running))
    c2.metric("Всего в журнале", len(jobs))
    c3.metric("С ошибкой", sum(1 for row in jobs if row.get("status") == "error"))

    if running:
        st.subheader("Сейчас выполняется")
        for job in running:
            type_badge = JOB_TYPE_LABELS.get(str(job.get("type")), str(job.get("type")))
            ep_label = format_job_episode_label(str(job.get("episode", "")))
            st.markdown(f"**`{job.get('id')}`** · {type_badge} · {ep_label}")
            st.progress(float(job.get("progress") or 0.0), text=str(job.get("step_text") or "…"))
            if st.button("⏹ Остановить", key=f"jobs_page_stop_{job.get('id')}"):
                stop_job(str(job.get("id")))
                st.rerun()

    st.subheader("Журнал")
    if not jobs:
        st.caption("Запусков пока нет.")
    for job in jobs:
        type_badge = JOB_TYPE_LABELS.get(str(job.get("type")), str(job.get("type")))
        status_badge = JOB_STATUS_LABELS.get(str(job.get("status")), str(job.get("status")))
        ep_label = format_job_episode_label(str(job.get("episode", "")))
        title = f"`{job.get('id', '—')}` · {type_badge} · {status_badge} · {ep_label}"
        with st.expander(title, expanded=False):
            st.caption(format_job_interval(job))
            if job.get("step_text"):
                st.write(job["step_text"])
            details = get_job_details(str(job.get("id")))
            log_text = (details or {}).get("log_tail") or ""
            if log_text:
                st.code(log_text, language="text")
            offer_restart_job(job, key=f"jobs_page_restart_{job.get('id')}")


def render_studio_progress_pinned(episode_dir: Path, ep_key: str) -> bool:
    """Компактный прогресс вверху студии. True → авто-обновление."""
    ep_path = episode_job_path(episode_dir)
    jobs = get_jobs(80)
    running_here = [
        row for row in jobs if row.get("status") == "running" and row.get("episode") == ep_path
    ]
    was_running = bool(st.session_state.get(f"studio_job_running_{ep_key}", False))
    if was_running and not running_here:
        bump_episode_text_editors(episode_dir)
    st.session_state[f"studio_job_running_{ep_key}"] = bool(running_here)
    if not running_here:
        acknowledge_episode_job_outputs(episode_dir, ep_key)
    with st.container(border=True):
        head_l, head_r = st.columns([5, 1])
        with head_l:
            st.markdown("**📡 Активная задача**")
        with head_r:
            if st.button("🔄", key=f"studio_pin_refresh_{ep_key}", help="Обновить статус"):
                st.rerun()
        if running_here:
            job = running_here[0]
            st.caption(
                f"`{job.get('id', '—')}` · "
                f"{JOB_TYPE_LABELS.get(job.get('type'), job.get('type'))} · "
                f"{JOB_STATUS_LABELS.get(job.get('status'), job.get('status'))}"
            )
            st.progress(float(job.get("progress") or 0.0), text=str(job.get("step_text") or "…"))
            stop_c, _ = st.columns([1, 3])
            with stop_c:
                if st.button("⏹ Остановить", key=f"studio_pin_stop_{job['id']}"):
                    stop_job(job["id"])
                    st.toast("Остановка отправлена")
                    st.rerun()
            details = get_job_details(str(job.get("id")))
            log_text = ((details or {}).get("log_tail") or "")[-3500:]
            if log_text:
                st.code(log_text, language="text")
        else:
            failed_here = [
                row
                for row in jobs
                if row.get("episode") == ep_path and row.get("status") in ("error", "stopped")
            ]
            if failed_here:
                job = failed_here[0]
                status_badge = JOB_STATUS_LABELS.get(str(job.get("status")), str(job.get("status")))
                type_badge = JOB_TYPE_LABELS.get(str(job.get("type")), str(job.get("type")))
                st.markdown(
                    f"**Последняя задача** `{job.get('id', '—')}` · {type_badge} · {status_badge}"
                )
                if job.get("step_text"):
                    st.caption(str(job["step_text"])[:300])
                offer_restart_job(job, key=f"studio_pin_restart_{job.get('id')}")
                details = get_job_details(str(job.get("id")))
                log_tail = ((details or {}).get("log_tail") or "")[-2000:]
                if log_tail:
                    with st.expander("Хвост лога", expanded=False):
                        st.code(log_tail, language="text")
            else:
                st.caption("Нет активных фоновых задач для этого эпизода.")
    return bool(running_here)


def read_editor_text(path: Path, channel_slug: str) -> str:
    key = editor_key(path, channel_slug)
    if key in st.session_state:
        return str(st.session_state[key])
    if path.is_file():
        return path.read_text(encoding="utf-8-sig")
    return ""


def persist_episode_config_before_job(episode_dir: Path, ep_key: str, epi_cfg: dict) -> None:
    min_v = float(st.session_state.get(f"ep_min_min_{ep_key}", epi_cfg["target_min_minutes"]))
    max_v = float(st.session_state.get(f"ep_max_min_{ep_key}", epi_cfg["target_max_minutes"]))
    frame_raw = int(
        st.session_state.get(
            f"ep_frame_count_{ep_key}",
            epi_cfg.get("target_frame_count") or 0,
        )
    )
    tfc = frame_raw if frame_raw > 0 else None
    spf_default = float(epi_cfg.get("seconds_per_frame", 40))
    cfg_out = {
        "target_min_minutes": min_v,
        "target_max_minutes": max(max_v, min_v),
        "words_per_minute": int(epi_cfg.get("words_per_minute", 125)),
        "update_bible_recap": bool(epi_cfg.get("update_bible_recap", False)),
        "target_frame_count": tfc,
        "seconds_per_frame": float(
            st.session_state.get(f"ep_spf_{ep_key}", spf_default)
        ),
        "voice_fade_out_sec": float(
            st.session_state.get(
                f"ep_voice_fade_{ep_key}",
                epi_cfg.get("voice_fade_out_sec", 12),
            )
        ),
        "ambient_tail_sec": float(
            st.session_state.get(
                f"ep_ambient_tail_{ep_key}",
                epi_cfg.get("ambient_tail_sec", 0),
            )
        ),
        **tail_fields_from_session(ep_key, epi_cfg),
    }
    save_episode_config(episode_dir, cfg_out)


def persist_episode_plan_before_job(
    episode_dir: Path,
    ep_key: str,
    epi_cfg: dict,
    plan_key: str,
) -> None:
    persist_episode_config_before_job(episode_dir, ep_key, epi_cfg)
    save_plot_brief(episode_dir, str(st.session_state.get(plan_key, "")))


def acknowledge_episode_job_outputs(episode_dir: Path, ep_key: str) -> None:
    """Сбросить кэш вкладок редактора после успешной фоновой задачи по эпизоду."""
    ep_path = episode_job_path(episode_dir)
    script_jobs = frozenset({"sync_episode_pack.py", "generate_episode_pack.py"})
    done_here = [
        row
        for row in get_jobs(80)
        if row.get("episode") == ep_path
        and row.get("status") == "done"
        and str(row.get("script_name") or "") in script_jobs
    ]
    if not done_here:
        return
    done_here.sort(key=lambda r: str(r.get("finished_at") or ""), reverse=True)
    job_id = str(done_here[0].get("id") or "")
    if not job_id:
        return
    ack_key = f"job_files_ack_{ep_key}"
    if st.session_state.get(ack_key) == job_id:
        return
    bump_episode_text_editors(episode_dir)
    st.session_state[ack_key] = job_id


def save_episode_text_files(channel_slug: str, files: dict[str, Path]) -> int:
    saved = 0
    skipped: list[str] = []
    for path in files.values():
        text = read_editor_text(path, channel_slug)
        if path.is_file():
            disk = path.read_text(encoding="utf-8-sig")
            if len(disk.strip()) > 600 and len(text.strip()) < len(disk.strip()) * 0.25:
                skipped.append(path.name)
                continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        bump_editor(path)
        saved += 1
    if skipped:
        st.warning(
            "Не перезаписывал с диска: "
            + ", ".join(skipped)
            + " — в полях устаревший/пустой текст. Нажмите «Обновить тексты с диска»."
        )
    return saved


def comfyui_status_label() -> str:
    try:
        from comfyui_launcher import comfyui_status

        return str(comfyui_status().get("label", "⚪ ComfyUI оффлайн"))
    except Exception:
        return "⚪ ComfyUI оффлайн"


def render_animate_i2v_panel(
    episode_dir: Path,
    ep_key: str,
    frame_stem_options: list[str],
    *,
    channel_slug: str,
    channel_dir: Path,
) -> None:
    """Оживление / Анимация: 2.5D FFmpeg или ComfyUI I2V."""
    from animation_settings import (
        infer_animation_method_from_review,
        list_overlay_files,
        load_episode_animation,
        normalize_animation_settings,
        resolve_video_build_script,
        save_episode_animation,
    )
    from remotion_env import ensure_npm_install, node_status
    from i2v_settings import (
        animate_frames_cli_args,
        estimate_total_label,
        list_checkpoint_names,
        list_clip_names,
        normalize_i2v_settings,
    )

    review = load_review(episode_dir)
    anim = load_episode_animation(episode_dir)
    saved = normalize_i2v_settings(review.get("animate_i2v"))
    default_stems = [
        stem
        for stem in normalize_animate_frames(review.get("animate_frames"))
        if stem in frame_stem_options
    ] or normalize_animate_frames(review.get("animate_frames"))[:3]

    preset_labels = {
        "fast": "Быстрый тест (512×288, мало кадров)",
        "balance": "Баланс (768×432, ~2 с)",
        "quality": "Качество (дольше, больше кадров)",
        "custom": "Свои параметры",
    }
    preset_codes = list(preset_labels.keys())
    preset_idx = preset_codes.index(saved.get("preset", "balance")) if saved.get("preset") in preset_codes else 1

    method_labels = {
        "ffmpeg_25d": "⚡ Киношная 2.5D (FFmpeg)",
        "remotion": "⚛️ Remotion Studio",
    }
    method_codes = list(method_labels.keys())
    inferred = infer_animation_method_from_review(review)
    current_mode = str(anim.get("animation_mode", inferred or "ffmpeg_25d"))
    if current_mode in ("comfyui_i2v", "comfyui"):
        current_mode = "ffmpeg_25d"
    elif current_mode not in method_codes:
        current_mode = inferred if inferred in method_codes else "ffmpeg_25d"

    method_key = f"anim_method_{ep_key}"
    episode_token = f"anim_method_ep_{ep_key}"
    if (
        episode_token not in st.session_state
        or st.session_state.get(episode_token) != episode_dir.name
    ):
        st.session_state[method_key] = current_mode
        st.session_state[episode_token] = episode_dir.name
    elif st.session_state.get(method_key) not in method_codes:
        st.session_state[method_key] = current_mode

    with st.expander("🎞️ Оживление / Анимация", expanded=False):
        if hasattr(st, "segmented_control"):
            method = st.segmented_control(
                "Метод оживления",
                options=method_codes,
                format_func=lambda code: method_labels[code],
                key=method_key,
            )
        else:
            method = st.radio(
                "Метод оживления",
                method_codes,
                format_func=lambda code: method_labels[code],
                horizontal=True,
                key=method_key,
                label_visibility="visible",
            )
        st.caption(
            "Сейчас в интерфейсе: **"
            + method_labels.get(str(method), str(method))
            + "**. Сохраните настройки, чтобы Full Pipeline взял тот же метод."
        )

        if method == "ffmpeg_25d":
            st.caption("Быстрый Ken Burns, «живой» свет и оверлей — при сборке **final_video.mp4**.")
            ken = st.checkbox(
                "5 траекторий движения камеры (Pan & Zoom)",
                value=bool(anim.get("enable_ken_burns", True)),
                key=f"ffmpeg_ken_{ep_key}",
            )
            breathe = st.checkbox(
                "Эффект живого света (пульсация лампы/огня)",
                value=bool(anim.get("enable_breathing_light", True)),
                key=f"ffmpeg_breathe_{ep_key}",
            )
            available_overlays = list_overlay_files()
            overlay_options = ["auto", *available_overlays]
            cur_ov_list = list(anim.get("overlay_files") or [])
            for item in cur_ov_list:
                if item not in overlay_options:
                    overlay_options.append(item)
            overlay_labels = {"auto": "🎲 Авто (все файлы из assets/overlays/)"}
            overlay_labels.update({name: name for name in available_overlays})
            ov_picks = st.multiselect(
                "Видео-оверлеи (assets/overlays/) — можно выбрать несколько",
                overlay_options,
                default=cur_ov_list,
                format_func=lambda x: overlay_labels.get(x, x),
                placeholder="Без оверлея (выберите один или несколько)",
                key=f"ffmpeg_overlays_{ep_key}",
            )
            opacity = st.slider(
                "Прозрачность оверлея (Screen)",
                min_value=0.10,
                max_value=0.40,
                value=float(anim.get("overlay_opacity", 0.22)),
                step=0.02,
                key=f"ffmpeg_ov_opacity_{ep_key}",
            )
            ov_random_mix = st.checkbox(
                "Рандомно смешивать и накладывать друг на друга (анти-повтор для хвоста)",
                value=bool(anim.get("overlay_random_mix", True)),
                key=f"ffmpeg_ov_random_{ep_key}",
            )
            if ov_random_mix:
                ov_c1, ov_c2 = st.columns(2)
                ov_max_layers = ov_c1.slider(
                    "Макс. слоёв одновременно",
                    min_value=1,
                    max_value=5,
                    value=int(anim.get("overlay_max_layers", 3)),
                    key=f"ffmpeg_ov_layers_{ep_key}",
                )
                ov_variants = ov_c2.slider(
                    "Вариантов комбинаций в хвосте",
                    min_value=2,
                    max_value=32,
                    value=int(anim.get("overlay_variants", 12)),
                    key=f"ffmpeg_ov_variants_{ep_key}",
                )
            else:
                ov_max_layers = int(anim.get("overlay_max_layers", 3))
                ov_variants = int(anim.get("overlay_variants", 12))
            ff_save, ff_build = st.columns(2)
            if ff_save.button("💾 Сохранить настройки", key=f"ffmpeg_save_{ep_key}", use_container_width=True):
                bundle = normalize_animation_settings(
                    {
                        "animation_mode": "ffmpeg_25d",
                        "enable_ken_burns": ken,
                        "enable_breathing_light": breathe,
                        "overlay_files": ov_picks,
                        "overlay_opacity": opacity,
                        "overlay_random_mix": ov_random_mix,
                        "overlay_max_layers": ov_max_layers,
                        "overlay_variants": ov_variants,
                        "comfy_frames": default_stems,
                    }
                )
                save_episode_animation(episode_dir, bundle)
                st.toast("Сохранено в review.json (Full Pipeline подхватит)")
                st.rerun()
            if ff_build.button(
                "🎬 Собрать видео с 2.5D-эффектами",
                key=f"ffmpeg_build_{ep_key}",
                use_container_width=True,
            ):
                bundle = normalize_animation_settings(
                    {
                        "animation_mode": "ffmpeg_25d",
                        "enable_ken_burns": ken,
                        "enable_breathing_light": breathe,
                        "overlay_files": ov_picks,
                        "overlay_opacity": opacity,
                        "overlay_random_mix": ov_random_mix,
                        "overlay_max_layers": ov_max_layers,
                        "overlay_variants": ov_variants,
                    }
                )
                save_episode_animation(episode_dir, bundle)
                epi_save = load_episode_config(episode_dir, channel_slug, channel_dir)
                persist_episode_config_before_job(episode_dir, ep_key, epi_save)
                launch_background_job("build_video", episode_dir, "build_sleep_video.py")
        elif method == "remotion":
            r_status = node_status()
            if r_status["ready"]:
                st.success("Node.js + remotion_studio готовы к рендеру.")
            else:
                parts = []
                if not r_status["node"]:
                    parts.append("нет Node.js")
                if not r_status["npm"]:
                    parts.append("нет npm")
                if not r_status["node_modules"]:
                    parts.append("нужен `npm install` в remotion_studio/")
                st.warning("Remotion: " + ", ".join(parts) if parts else "не готов")
            if st.button("📦 npm install (remotion_studio)", key=f"remotion_npm_{ep_key}"):
                ok, msg = ensure_npm_install()
                (st.success if ok else st.error)(msg)
                st.rerun()
            ch_titles = st.checkbox(
                "Красивые титры глав из сценария",
                value=bool(anim.get("remotion_chapter_titles", True)),
                key=f"remotion_titles_{ep_key}",
            )
            particles = st.checkbox(
                "Процедурные пылинки / звёздная пыль (React-слой)",
                value=bool(anim.get("remotion_particles", True)),
                key=f"remotion_particles_{ep_key}",
            )
            vignette = st.checkbox(
                "Мягкая дышащая виньетка",
                value=bool(anim.get("remotion_vignette", True)),
                key=f"remotion_vignette_{ep_key}",
            )
            kb_intensity = st.slider(
                "Интенсивность Ken Burns",
                min_value=0.2,
                max_value=1.2,
                value=float(anim.get("ken_burns_intensity", 0.85)),
                step=0.05,
                key=f"remotion_kb_{ep_key}",
            )
            r_save, r_render = st.columns(2)
            if r_save.button("💾 Сохранить Remotion", key=f"remotion_save_{ep_key}", use_container_width=True):
                bundle = normalize_animation_settings(
                    {
                        "animation_mode": "remotion",
                        "remotion_chapter_titles": ch_titles,
                        "remotion_particles": particles,
                        "remotion_vignette": vignette,
                        "ken_burns_intensity": kb_intensity,
                    }
                )
                save_episode_animation(episode_dir, bundle)
                st.toast("Метод remotion сохранён")
                st.rerun()
            if r_render.button(
                "⚛️ Рендер final_video (Remotion)",
                key=f"remotion_render_{ep_key}",
                type="primary",
                use_container_width=True,
            ):
                bundle = normalize_animation_settings(
                    {
                        "animation_mode": "remotion",
                        "remotion_chapter_titles": ch_titles,
                        "remotion_particles": particles,
                        "remotion_vignette": vignette,
                        "ken_burns_intensity": kb_intensity,
                    }
                )
                save_episode_animation(episode_dir, bundle)
                epi_save = load_episode_config(episode_dir, channel_slug, channel_dir)
                persist_episode_config_before_job(episode_dir, ep_key, epi_save)
                launch_background_job("build_video", episode_dir, "build_remotion_video.py")

def render_comfyui_controls(key_prefix: str) -> None:
    """Блок запуска ComfyUI из Streamlit."""
    from comfyui_launcher import comfyui_status, start_comfyui_server, stop_comfyui_server
    from comfyui_client import comfyui_base_url

    status = comfyui_status()
    c_stat, c_start, c_stop, c_open = st.columns([2.2, 1, 1, 1])
    with c_stat:
        st.markdown(f"**{status['label']}**")
        st.caption(status.get("detail", ""))
    with c_start:
        if st.button(
            "▶️ Запустить ComfyUI",
            key=f"comfy_start_{key_prefix}",
            disabled=bool(status.get("online")),
            use_container_width=True,
        ):
            ok, msg = start_comfyui_server()
            (st.success if ok else st.error)(msg)
            st.rerun()
    with c_stop:
        if st.button(
            "⏹ Остановить",
            key=f"comfy_stop_{key_prefix}",
            disabled=not status.get("process_alive") and not status.get("online"),
            use_container_width=True,
        ):
            ok, msg = stop_comfyui_server()
            (st.success if ok else st.warning)(msg)
            st.rerun()
    with c_open:
        st.link_button(
            "🌐 Comfy UI",
            comfyui_base_url(),
            use_container_width=True,
        )
    if status.get("log_tail") and not status.get("online"):
        with st.expander("Лог запуска ComfyUI", expanded=status.get("process_alive", False)):
            st.code(status["log_tail"])


def show_process_result(result: subprocess.CompletedProcess[str], title: str) -> None:
    st.subheader(title)
    if result.stdout.strip():
        st.code(result.stdout.strip())
    if result.stderr.strip():
        st.warning(result.stderr.strip())
    if result.returncode != 0:
        st.error(f"Код завершения: {result.returncode}")
    else:
        st.success("Команда завершилась успешно.")


def load_csv(path: Path) -> pd.DataFrame:
    if not path.is_file():
        return pd.DataFrame(columns=BACKLOG_COLUMNS)
    return pd.read_csv(path, encoding="utf-8-sig")


def load_channel_backlog(path: Path) -> pd.DataFrame:
    frame = normalize_backlog_frame(load_csv(path))
    channel_dir = path.parent
    if channel_dir.is_dir():
        cleaned, dropped = purge_series_from_backlog(channel_dir, frame)
        if dropped:
            save_backlog(path, cleaned)
        return cleaned
    return frame


def save_backlog(path: Path, frame: pd.DataFrame) -> None:
    frame = normalize_backlog_frame(frame)
    frame.to_csv(path, index=False, encoding="utf-8-sig")


def _titles_from_manual_name(title: str) -> tuple[str, str]:
    title = title.strip()
    return title, title


def _next_manual_standalone_id(channel: Path) -> str:
    prefix = "SH" if channel.name.startswith("channel_02") else "GM" if channel.name.startswith("channel_01") else "EP"
    numbers: list[int] = []
    backlog = load_channel_backlog(channel / "backlog.csv")
    for value in backlog.get("ID", pd.Series(dtype=str)).astype(str):
        match = re.fullmatch(rf"{prefix}(\d+)", value.strip(), flags=re.IGNORECASE)
        if match:
            numbers.append(int(match.group(1)))
    for path in list_episode_dirs(channel):
        match = re.fullmatch(rf"{prefix}(\d+)", path.name.split("_", 1)[0], flags=re.IGNORECASE)
        if match:
            numbers.append(int(match.group(1)))
    return f"{prefix}{max(numbers, default=0) + 1:02d}"


def _remember_episode_title(channel: Path, episode_dir: Path, title: str, series_id: str, episode_num: str) -> None:
    topic, topic_ru = _titles_from_manual_name(title)
    ep_id = episode_dir.name.split("_", 1)[0]
    backlog_path = channel / "backlog.csv"
    frame = load_channel_backlog(backlog_path)
    playlist = playlist_by_id(channel, series_id) if series_id != STANDALONE_ID else None
    series_en = str(playlist.get("title_en", "")) if playlist else ""
    series_ru = str(playlist.get("title_ru", "")) if playlist else ""
    match = frame["ID"].astype(str).str.strip().eq(ep_id) if not frame.empty else None
    if match is not None and bool(match.any()):
        idx = frame.index[match][0]
        frame.at[idx, "Topic"] = topic
        frame.at[idx, "Topic_RU"] = topic_ru
    else:
        row = {column: "" for column in BACKLOG_COLUMNS}
        row.update(
            {
                "ID": ep_id,
                "Topic": topic,
                "Topic_RU": topic_ru,
                "Series_ID": series_id,
                "Episode_Num": episode_num or "1",
                "Series": series_en,
                "Series_RU": series_ru,
                "Expected_Views": "1000-5000 (Тест)",
                "Status": "Idea",
                "Priority": "P1",
            }
        )
        frame = pd.concat([frame, pd.DataFrame([row])], ignore_index=True)
    save_backlog(backlog_path, frame)
    idea = read_episode_idea(episode_dir)
    idea.update(
        {
            "id": ep_id,
            "topic": topic,
            "topic_ru": topic_ru,
            "series_id": series_id,
            "episode_num": episode_num or idea.get("episode_num") or "1",
            "series_en": series_en,
            "series_ru": series_ru,
        }
    )
    write_episode_idea(episode_dir, idea)
    _write_metadata_title(episode_dir / "metadata.md", title)


def _write_metadata_title(path: Path, title: str) -> None:
    if not path.is_file():
        path.write_text(
            "# Episode metadata\n\n## English Metadata\n\n### Titles\n\n"
            f"1. {title}\n\n## Russian Metadata\n\n### Title\n\n{title}\n",
            encoding="utf-8",
        )
        return
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    in_titles = False
    replaced = False
    for index, line in enumerate(lines):
        if line.strip() == "### Titles":
            in_titles = True
            continue
        if in_titles and line.startswith("### "):
            in_titles = False
        if in_titles and re.match(r"^1\.\s+", line):
            lines[index] = f"1. {title}"
            replaced = True
            break
    if not replaced:
        lines.extend(["", "### Titles", "", f"1. {title}"])
    in_ru = False
    ru_replaced = False
    for index, line in enumerate(lines):
        if line.strip() == "## Russian Metadata":
            in_ru = True
            continue
        if in_ru and line.startswith("## "):
            in_ru = False
        if in_ru and line.strip() == "### Title":
            for follow in range(index + 1, len(lines)):
                if lines[follow].strip():
                    lines[follow] = title
                    ru_replaced = True
                    break
            if not ru_replaced:
                lines.insert(index + 1, "")
                lines.insert(index + 2, title)
                ru_replaced = True
            break
    if not ru_replaced:
        lines.extend(["", "## Russian Metadata", "", "### Title", "", title])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _rename_playlist(channel: Path, series_id: str, title: str) -> None:
    playlists = load_playlists(channel)
    found = False
    for row in playlists:
        if str(row.get("id", "")).strip().upper() == series_id.strip().upper():
            row["title_en"] = title
            row["title_ru"] = title
            found = True
            break
    if not found:
        raise ValueError(f"Плейлист {series_id} не найден.")
    save_playlists(channel, playlists)


def median_views_7d(published_path: Path) -> str:
    if not published_path.is_file():
        return "—"
    frame = pd.read_csv(published_path, encoding="utf-8-sig")
    if "Views_7d" not in frame.columns:
        return "—"
    values = pd.to_numeric(frame["Views_7d"], errors="coerce").dropna()
    if values.empty:
        return "—"
    return str(int(statistics.median(values)))


def count_ready_episodes(episodes_dir: Path) -> int:
    if not episodes_dir.is_dir():
        return 0
    return sum(1 for path in episodes_dir.iterdir() if path.is_dir() and (path / "final_video.mp4").is_file())


def slugify_topic(topic: str) -> str:
    ascii_text = re.sub(r"[^\w\s-]", "", topic, flags=re.ASCII)
    words = re.sub(r"\s+", "_", ascii_text.strip().lower()).strip("_")
    return words[:48] or "episode"


def next_backlog_id(backlog: pd.DataFrame, channel_slug: str) -> str:
    if channel_slug.startswith("channel_02"):
        prefix = "SH"
    elif channel_slug.startswith("channel_01"):
        prefix = "GM"
    else:
        prefix = "EP"
    numbers = []
    for value in backlog.get("ID", pd.Series(dtype=str)).astype(str):
        match = re.fullmatch(rf"{prefix}(\d+)", value.strip(), flags=re.IGNORECASE)
        if match:
            numbers.append(int(match.group(1)))
    n = max(numbers, default=0) + 1
    return f"{prefix}{n:02d}"


STANDARD_TEMPLATE_LABEL = "Стандартный шаблон (templates/)"


def list_channel_templates() -> list[tuple[str, str]]:
    """[("", стандартный), ("template_x", "template_x"), ...] — пакеты = подпапки templates/ с channel_config.md."""
    options: list[tuple[str, str]] = [("", STANDARD_TEMPLATE_LABEL)]
    if TEMPLATES_DIR.is_dir():
        for pack in sorted(TEMPLATES_DIR.iterdir(), key=lambda p: p.name.lower()):
            if pack.is_dir() and (pack / "channel_config.md").is_file():
                options.append((pack.name, f"{pack.name} (пакет)"))
    return options


def _create_channel_from_pack(target: Path, slug: str, pack_name: str) -> Path:
    pack = TEMPLATES_DIR / pack_name
    if pack.parent != TEMPLATES_DIR or not (pack / "channel_config.md").is_file():
        raise FileNotFoundError(f"Пакет шаблона не найден: {pack_name}")
    config = (pack / "channel_config.md").read_text(encoding="utf-8-sig").replace("{{SLUG}}", slug)
    (target / "channel_config.md").write_text(config, encoding="utf-8")
    for name in ("backlog.csv", "published.csv"):
        source = pack / name if (pack / name).is_file() else TEMPLATES_DIR / name
        shutil.copy2(source, target / name)
    if (pack / "episodes").is_dir():
        shutil.copytree(pack / "episodes", target / "episodes", dirs_exist_ok=True)
    if (pack / "database").is_dir():
        shutil.copytree(pack / "database", target / "database", dirs_exist_ok=True)
    return target


def create_channel_folder(
    slug: str, niche: str, duration: str, keywords: str, template: str = ""
) -> Path:
    target = channel_dir(slug)
    if target.exists():
        raise FileExistsError(f"Канал уже существует: {slug}")
    target.mkdir(parents=True)
    (target / "episodes").mkdir()
    (target / "analytics_dump").mkdir()
    if template:
        try:
            return _create_channel_from_pack(target, slug, template)
        except Exception:
            shutil.rmtree(target, ignore_errors=True)
            raise
    for name in ("channel_config.md", "backlog.csv", "published.csv"):
        shutil.copy2(TEMPLATES_DIR / name, target / name)
    keyword_lines = [line.strip() for line in keywords.splitlines() if line.strip()]
    keyword_block = "\n".join(f"- {line}" for line in keyword_lines) if keyword_lines else "- faceless youtube"
    config = f"""# Конфиг канала: {slug}

## Ниша

- Тема: {niche}
- Язык контента: английский
- Формат: длинные видео
- Длина ролика: {duration}

## Search Keywords

{keyword_block}

## Идентификаторы

- Slug папки (`channels/<slug>/`): `{slug}`
- Название канала: {slug}
- Дата запуска: {datetime.now().date().isoformat()}
"""
    (target / "channel_config.md").write_text(config, encoding="utf-8")
    return target


def parse_timecode(text: str) -> float:
    text = text.strip()
    if not text:
        raise ValueError("Пустой таймкод")
    if ":" not in text:
        return float(text)
    parts = [float(part) for part in text.split(":")]
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    raise ValueError("Формат: секунды, ММ:СС или ЧЧ:ММ:СС")


def _isekai_script_stub(config_text: str) -> str:
    target = "es"
    native = "en"
    for line in config_text.splitlines():
        if line.startswith("TARGET_LANG:"):
            target = line.split(":", 1)[1].strip() or target
        elif line.startswith("NATIVE_LANG:"):
            native = line.split(":", 1)[1].strip() or native
    gloss = {
        "es": "Cierra los ojos.",
        "fr": "Ferme les yeux.",
        "de": "Schließ die Augen.",
        "it": "Chiudi gli occhi.",
    }.get(target, "Mira.")
    return (
        "# EPISODE: NEW\n"
        f"# TARGET_LANG: {target}\n"
        f"# NATIVE_LANG: {native}\n\n"
        "## BLOCK: ACT_1_START\n"
        "[001|NARRATOR_NATIVE_en|0.85] Rain finds you on the dark stone street. Water keeps to the middle of the road.\n"
        "[PAUSE|1.5]\n"
        f"[001|NARRATOR_TARGET_{target}|0.78] {gloss}\n"
        "[PAUSE|1.5]\n"
        "[001|NARRATOR_NATIVE_en_translate|0.85] Close your eyes.\n"
        "[PAUSE|2.0]\n\n"
        "## BLOCK: RECALL_1\n"
        "[109|NARRATOR_NATIVE_en|0.80] Rain finds you on the dark stone street. Water keeps to the middle of the road.\n"
        "[PAUSE|2.0]\n"
        f"[109|NARRATOR_TARGET_{target}|0.75] {gloss}\n"
        "[PAUSE|2.0]\n"
        f"[109|NARRATOR_TARGET_{target}_repeat|0.70] {gloss}\n"
        "[PAUSE|4.0]\n\n"
        "## BLOCK: PLOT_TARGET_1\n"
        f"[217|NARRATOR_TARGET_{target}|0.75] {gloss}\n"
        "[PAUSE|2.5]\n\n"
        "## PLAYLIST_SEQUENCE\n"
        "PLAY: ACT_1_START (mode=full, repeat=1)\n"
        "PLAY: RECALL_1 (mode=full, repeat=1)\n"
        "PLAY: PLOT_TARGET_1 (mode=full, speed_mult=0.82, repeat=1)\n"
    )


def ensure_episode_files(episode_dir: Path, topic_en: str = "", topic_ru: str = "") -> None:
    channel_cfg = episode_dir.parents[1] / "channel_config.md"
    config_text = channel_cfg.read_text(encoding="utf-8-sig") if channel_cfg.is_file() else ""
    isekai = "TARGET_LANG:" in config_text and "BASE_STYLE_PROMPT:" in config_text
    if isekai:
        defaults = {
            "script.md": _isekai_script_stub(config_text),
            "script_ru.md": (
                "Этот файл озвучка формата «язык во сне» не читает. "
                "Пояснения на родном языке стоят в script.md, роль MENTOR_NATIVE.\n"
            ),
            "visual_prompts.md": (
                "# Visual prompts\n\n"
                "PROMPT: Night forest path, gray cat in a cloak seen from behind, dim lantern, 16:9, no text\n"
            ),
        }
    else:
        defaults = {
            "script.md": f"# {topic_en or 'Episode'}\n\n",
            "script_ru.md": (
                f"# {topic_ru or topic_en or 'Эпизод'}\n\n"
                "Русская адаптация `script.md`. Заполняется вместе с английским сценарием.\n"
            ),
            "visual_prompts.md": "# Visual prompts\n\nPROMPT: Cinematic scene, dark mood, no text\n",
        }
    meta_template = TEMPLATES_DIR / "episode_metadata.md"
    if meta_template.is_file():
        defaults["metadata.md"] = meta_template.read_text(encoding="utf-8-sig")
    else:
        defaults["metadata.md"] = "## English Metadata\n\n## Russian Metadata\n"
    for name, body in defaults.items():
        path = episode_dir / name
        if not path.is_file():
            path.write_text(body, encoding="utf-8")
    (episode_dir / "images").mkdir(exist_ok=True)


def _episode_editor_token(path: Path) -> str:
    return re.sub(r"[^a-zA-Z0-9_]", "_", path.parent.name)


def editor_key(path: Path, channel_slug: str) -> str:
    ep_token = _episode_editor_token(path)
    ver_key = f"editor_version_{ep_token}_{path.name}"
    version = st.session_state.get(ver_key, 0)
    return f"editor_{ep_token}_{path.name}_{channel_slug}_v{version}"


def bump_editor(path: Path) -> None:
    ep_token = _episode_editor_token(path)
    ver_key = f"editor_version_{ep_token}_{path.name}"
    st.session_state[ver_key] = st.session_state.get(ver_key, 0) + 1


def bump_episode_text_editors(episode_dir: Path) -> None:
    for name in ("script.md", "script_ru.md", "visual_prompts.md", "metadata.md"):
        bump_editor(episode_dir / name)


def sync_editors_from_disk_if_newer(channel_slug: str, files: dict[str, Path]) -> int:
    """Сбросить кэш textarea, если на диске заметно больше текста (фоновая ИИ-генерация)."""
    reloaded = 0
    for path in files.values():
        if not path.is_file():
            continue
        disk = path.read_text(encoding="utf-8-sig")
        key = editor_key(path, channel_slug)
        if key not in st.session_state:
            continue
        session = str(st.session_state.get(key, ""))
        if len(disk.strip()) > len(session.strip()) + 400:
            bump_editor(path)
            reloaded += 1
    return reloaded


def format_media_time(seconds: float) -> str:
    whole = int(max(0, seconds))
    return f"{whole // 60}:{whole % 60:02d}"


def image_cell_key(ep_key: str, name: str) -> str:
    raw = f"img_{ep_key}_{name}"
    return re.sub(r"[^a-zA-Z0-9_]", "_", raw)


def disk_names_for_flagged(review: dict, image_names: list[str]) -> set[str]:
    stems = flagged_stems(review)
    return {name for name in image_names if stem_from_filename(name) in stems}


def is_image_path_flagged(review: dict, filename: str) -> bool:
    return stem_from_filename(filename) in flagged_stems(review)


def inject_flagged_image_css(flagged_filenames: set[str], ep_key: str, image_names: list[str]) -> None:
    flagged_keys = [image_cell_key(ep_key, name) for name in image_names if name in flagged_filenames]
    rules = "\n".join(
        (
            f'div[data-testid="stVerticalBlockBorderWrapper"].st-key-{key} {{'
            " border-color: #e74c3c !important;"
            " border-width: 2px !important;"
            " }}"
        )
        for key in flagged_keys
    )
    st.markdown(
        f"<style>{rules}</style>" if rules else "",
        unsafe_allow_html=True,
    )


def create_episode_folder(channel_slug: str, idea_row: pd.Series) -> Path:
    backlog_frame = load_channel_backlog(channel_dir(channel_slug) / "backlog.csv")
    episode_id = str(idea_row.get("ID", "")).strip() or next_standalone_episode_id(backlog_frame)
    short = slugify_topic(str(idea_row.get("Topic", "episode")))
    folder = channel_dir(channel_slug) / "episodes" / f"{episode_id}_{short}"
    folder.mkdir(parents=True, exist_ok=False)
    ensure_episode_files(
        folder,
        topic_en=str(idea_row.get("Topic", "Episode")),
        topic_ru=str(idea_row.get("Topic_RU", "")),
    )
    write_episode_idea(
        folder,
        {
            "id": episode_id,
            "topic": str(idea_row.get("Topic", "Episode")),
            "topic_ru": str(idea_row.get("Topic_RU", "")),
            "angle": str(idea_row.get("Angle", "")),
            "series_id": STANDALONE_ID,
            "episode_num": "1",
            "series_en": "",
            "series_ru": "",
        },
    )
    return folder


def import_scout_rows(channel_slug: str, selected: pd.DataFrame) -> int:
    backlog_path = channel_dir(channel_slug) / "backlog.csv"
    backlog = load_channel_backlog(backlog_path)
    existing_topics = {str(topic).strip().lower() for topic in backlog.get("Topic", [])}
    added = 0
    for _, row in selected.iterrows():
        topic = str(row.get("Video_Title", "")).strip()
        if not topic or topic.lower() in existing_topics:
            continue
        new_id = next_standalone_episode_id(backlog)
        backlog.loc[len(backlog)] = {
            "ID": new_id,
            "Topic": topic,
            "Topic_RU": "",
            "Series_ID": STANDALONE_ID,
            "Episode_Num": "1",
            "Series": "",
            "Series_RU": "",
            "Angle": "Imported from scout",
            "Competitor_Ref": row.get("URL", ""),
            "Competitor_Views": row.get("Views", ""),
            "Outlier_Score": row.get("Outlier_Score", ""),
            "Expected_Views": "1000-5000 (Тест)",
            "Status": "Idea",
            "Priority": "P1",
        }
        existing_topics.add(topic.lower())
        added += 1
    save_backlog(backlog_path, backlog)
    return added


def qp_first(qp: object, key: str, default: str = "") -> str:
    getter = getattr(qp, "get", None)
    if getter is None:
        return default
    value = getter(key, default)
    if isinstance(value, list):
        return value[0] if value else default
    return str(value) if value is not None else default


def read_nav_from_url(channels: list[str]) -> None:
    qp = st.query_params
    section = qp_first(qp, "section")
    if section in NAV_SECTIONS:
        st.session_state.nav_section = section
    channel = qp_first(qp, "channel")
    if channel and channel in channels:
        st.session_state.channel_slug = channel
    episode = qp_first(qp, "episode")
    if episode:
        st.session_state.episode_name = episode
    playlist = qp_first(qp, "playlist")
    if playlist:
        st.session_state["_url_playlist"] = playlist


def write_nav_to_url() -> None:
    qp = st.query_params
    section = st.session_state.get("nav_section", "analytics")
    if section in NAV_SECTIONS and qp_first(qp, "section") != section:
        qp["section"] = section
    channel = st.session_state.get("channel_slug", "")
    if channel:
        if qp_first(qp, "channel") != channel:
            qp["channel"] = channel
    elif "channel" in qp:
        del qp["channel"]
    episode = st.session_state.get("episode_name", "")
    if st.session_state.get("nav_section") == "studio" and episode:
        if qp_first(qp, "episode") != episode:
            qp["episode"] = episode
    elif "episode" in qp:
        del qp["episode"]
    channel = st.session_state.get("channel_slug", "")
    if st.session_state.get("nav_section") == "studio" and channel:
        playlist = st.session_state.get(f"studio_playlist_{channel}", "")
        if playlist:
            if qp_first(qp, "playlist") != playlist:
                qp["playlist"] = playlist
        elif "playlist" in qp:
            del qp["playlist"]


def channel_label(slug: str) -> str:
    return slug.replace("channel_", "", 1).replace("_", " ")


def render_channel_tabs(channels: list[str]) -> str:
    if not channels:
        st.warning("В `channels/` пока нет каналов. Откройте раздел **Новый канал**.")
        return ""
    current = st.session_state.get("channel_slug", channels[0])
    if current not in channels:
        current = channels[0]
        st.session_state.channel_slug = current
    labels = [channel_label(slug) for slug in channels]
    slug_by_label = {channel_label(slug): slug for slug in channels}
    picked_label = st.pills(
        "Проект",
        options=labels,
        selection_mode="single",
        default=channel_label(current),
        key="channel_pills",
    )
    if picked_label in slug_by_label:
        st.session_state.channel_slug = slug_by_label[picked_label]
    return st.session_state.channel_slug


def extract_section(text: str, heading: str) -> str:
    pattern = rf"^##\s*{re.escape(heading)}\s*$"
    lines = text.splitlines()
    capture: list[str] = []
    active = False
    for line in lines:
        if re.match(pattern, line.strip(), flags=re.IGNORECASE):
            active = True
            continue
        if active and line.startswith("## "):
            break
        if active:
            capture.append(line)
    return "\n".join(capture).strip()


_TEXT_PROVIDER_LABELS = {
    "cloudflare": "Cloudflare Workers AI",
    "gemini": "Google Gemini",
    "groq": "Groq",
    "openrouter": "OpenRouter (:free)",
    "ollama": "🏠 Ollama (Локально)",
}

_IMAGE_PROVIDER_LABELS = {
    "cloudflare": "Cloudflare Workers AI",
    "gemini_imagen": "Google Imagen",
    "together_flux": "Together FLUX",
    "pollinations": "Pollinations.ai",
    "local_sd": "🏠 Local SD / Vulkan (Локально)",
}


def _format_text_provider_option(provider: str) -> str:
    if provider == "ollama":
        mark = "🟢 Готова" if ollama_server_up() else "⚪ Запустится при старте"
    else:
        mark = "🟢 Ключ задан" if provider_has_key(provider, kind="text") else "⚪ Нет ключа в .env"
    return f"{_TEXT_PROVIDER_LABELS.get(provider, provider)} — {mark}"


def _fallback_provider_label(provider: str) -> str:
    return _TEXT_PROVIDER_LABELS.get(provider, provider)


def _model_options_for_text_provider(provider: str) -> list[str]:
    if provider == "ollama":
        models = get_ollama_models(start_if_needed=True)
        return models if models else ["(нет локальных моделей)"]
    return list(TEXT_MODELS.get(provider, TEXT_MODELS["cloudflare"]))


def render_text_fallback_chain_editor(settings: dict, primary_provider: str) -> tuple[list[str], dict[str, str]]:
    """Редактор порядка резервных LLM и модели на каждый провайдер."""
    chain_key = "sidebar_text_fallback_chain"
    models_key = "sidebar_text_fallback_models"
    if chain_key not in st.session_state:
        st.session_state[chain_key] = normalize_text_fallback_chain(settings, primary_provider) or list(
            DEFAULT_TEXT_FALLBACK_CHAIN
        )
    if models_key not in st.session_state:
        raw = settings.get("text_fallback_models")
        st.session_state[models_key] = dict(raw) if isinstance(raw, dict) else {}

    chain: list[str] = [p for p in st.session_state[chain_key] if p != primary_provider]
    st.session_state[chain_key] = chain
    models_map: dict[str, str] = dict(st.session_state.get(models_key, {}))

    if not chain:
        st.caption("Цепочка пуста — при сбое основного резерва не будет.")

    for idx, prov in enumerate(chain):
        row_l, row_r1, row_r2, row_r3 = st.columns([4, 1, 1, 1])
        with row_l:
            options = _model_options_for_text_provider(prov)
            cur = str(models_map.get(prov, "") or "")
            if cur not in options:
                cur = options[0] if options else ""
            picked = st.selectbox(
                f"{idx + 1}. {_fallback_provider_label(prov)}",
                options,
                index=options.index(cur) if cur in options else 0,
                key=f"sidebar_fb_model_{prov}_{idx}",
            )
            if not str(picked).startswith("("):
                models_map[prov] = picked
        with row_r1:
            if st.button("↑", key=f"sidebar_fb_up_{idx}", disabled=idx == 0):
                chain[idx - 1], chain[idx] = chain[idx], chain[idx - 1]
                st.session_state[chain_key] = chain
                st.rerun()
        with row_r2:
            if st.button("↓", key=f"sidebar_fb_down_{idx}", disabled=idx >= len(chain) - 1):
                chain[idx + 1], chain[idx] = chain[idx], chain[idx + 1]
                st.session_state[chain_key] = chain
                st.rerun()
        with row_r3:
            if st.button("✕", key=f"sidebar_fb_del_{idx}"):
                chain.pop(idx)
                st.session_state[chain_key] = chain
                st.rerun()

    remaining = [p for p in TEXT_PROVIDERS if p not in chain and p != primary_provider]
    add_col, btn_col = st.columns([3, 1])
    with add_col:
        pick = st.selectbox(
            "Добавить в цепочку",
            options=["—"] + remaining,
            format_func=lambda p: "—" if p == "—" else _fallback_provider_label(p),
            key="sidebar_fb_add_pick",
            label_visibility="collapsed",
        )
    with btn_col:
        st.write("")
        if st.button("➕", key="sidebar_fb_add_btn") and pick != "—":
            chain.append(pick)
            st.session_state[chain_key] = chain
            st.rerun()

    st.session_state[models_key] = models_map
    return chain, {k: v for k, v in models_map.items() if k in chain and v}


def _format_image_provider_option(provider: str) -> str:
    if provider == "local_sd":
        st = local_sd_status()
        if st.get("ready"):
            mark = "🟢 sd.exe + модель"
        elif st.get("exe_ok"):
            mark = "⚪ Нет GGUF в models/image"
        elif st.get("model_ok"):
            mark = "⚪ Нет tools/sd/sd.exe"
        else:
            mark = "⚪ Установите sd.exe и модель"
    else:
        mark = "🟢 Ключ задан" if provider_has_key(provider, kind="image") else "⚪ Нет ключа в .env"
    return f"{_IMAGE_PROVIDER_LABELS.get(provider, provider)} — {mark}"


def render_sidebar_ai_settings() -> None:
    st.sidebar.markdown("---")
    st.sidebar.subheader("🤖 Активные нейросети")
    settings = load_ai_settings()

    text_provider = st.sidebar.selectbox(
        "Текст / Сценарии (LLM)",
        list(TEXT_PROVIDERS),
        index=list(TEXT_PROVIDERS).index(settings["text_provider"])
        if settings["text_provider"] in TEXT_PROVIDERS
        else 0,
        format_func=_format_text_provider_option,
        key="sidebar_text_provider",
    )
    if text_provider == "ollama":
        text_models = get_ollama_models(start_if_needed=True)
        if not text_models:
            text_models = ["(нет локальных моделей — ollama pull …)"]
    else:
        text_models = TEXT_MODELS.get(text_provider, TEXT_MODELS["cloudflare"])
    text_model_cur = (
        settings["text_model"]
        if settings.get("text_provider") == text_provider and settings["text_model"] in text_models
        else text_models[0]
    )
    text_model = st.sidebar.selectbox(
        "Модель (текст)",
        text_models,
        index=text_models.index(text_model_cur) if text_model_cur in text_models else 0,
        key="sidebar_text_model",
    )
    if text_provider == "ollama":
        st.sidebar.caption(
            "Ollama: "
            + ("🟢 сервер отвечает" if ollama_server_up() else "⚪ при первом запросе попробуем ollama serve")
        )

    image_provider = st.sidebar.selectbox(
        "Картинки (Image Gen)",
        list(IMAGE_PROVIDERS),
        index=list(IMAGE_PROVIDERS).index(settings["image_provider"])
        if settings["image_provider"] in IMAGE_PROVIDERS
        else 0,
        format_func=_format_image_provider_option,
        key="sidebar_image_provider",
    )
    if image_provider == "local_sd":
        image_models = list_local_sd_models()
    else:
        image_models = IMAGE_MODELS.get(image_provider, IMAGE_MODELS["cloudflare"])
    image_model_cur = (
        settings["image_model"]
        if settings.get("image_provider") == image_provider and settings["image_model"] in image_models
        else image_models[0]
    )
    image_model = st.sidebar.selectbox(
        "Модель (картинки)",
        image_models,
        index=image_models.index(image_model_cur) if image_model_cur in image_models else 0,
        key="sidebar_image_model",
    )
    if image_provider == "local_sd":
        lsd = local_sd_status(model_name=image_model)
        st.sidebar.caption(
            f"`tools/sd/sd.exe`: {'✅' if lsd.get('exe_ok') else '❌'} · "
            f"`models/image/{image_model}`: {'✅' if lsd.get('model_ok') else '❌'}"
        )

    st.sidebar.checkbox(
        "Резерв при сбое/лимите",
        value=bool(settings.get("text_fallback_on_quota", True)),
        key="sidebar_text_fallback_on_quota",
    )
    with st.sidebar.expander("🔗 Резервная цепочка (порядок + модели)", expanded=True):
        st.caption(
            f"Основной: **{_fallback_provider_label(text_provider)}** — ниже кто подхватит при 429/ошибке."
        )
        fb_chain, fb_models = render_text_fallback_chain_editor(settings, text_provider)
        st.checkbox(
            "Разрешить Gemini в цепочке",
            value=bool(settings.get("text_fallback_include_gemini", False)),
            key="sidebar_text_fallback_gemini",
            help="Иначе пункт «Google Gemini» в цепочке игнорируется (квота free tier).",
        )

    safe_text_model = text_model
    if text_provider == "ollama" and text_model.startswith("("):
        safe_text_model = settings.get("text_model", text_model)
    new_settings = {
        **settings,
        "text_provider": text_provider,
        "text_model": safe_text_model,
        "image_provider": image_provider,
        "image_model": image_model,
        "text_fallback_on_quota": bool(st.session_state.get("sidebar_text_fallback_on_quota", True)),
        "text_fallback_include_gemini": bool(st.session_state.get("sidebar_text_fallback_gemini", False)),
        "text_fallback_chain": fb_chain,
        "text_fallback_models": fb_models,
    }
    if (
        new_settings["text_provider"] != settings["text_provider"]
        or new_settings["text_model"] != settings["text_model"]
        or new_settings["image_provider"] != settings["image_provider"]
        or new_settings["image_model"] != settings["image_model"]
        or new_settings.get("text_fallback_on_quota") != settings.get("text_fallback_on_quota")
        or new_settings.get("text_fallback_include_gemini") != settings.get("text_fallback_include_gemini")
        or new_settings.get("text_fallback_chain") != settings.get("text_fallback_chain")
        or new_settings.get("text_fallback_models") != settings.get("text_fallback_models")
    ):
        save_ai_settings(new_settings)

    st.sidebar.caption("Выбор пишется в `config/ai_settings.json` — скрипты и Full Pipeline подхватывают сразу.")


def render_sidebar_quota_widget() -> None:
    sync_usage_from_logs()
    if not st.session_state.get("_quota_remote_refreshed"):
        refresh_remote_quotas()
        st.session_state["_quota_remote_refreshed"] = True
    st.sidebar.markdown("---")
    st.sidebar.caption("Квоты API")
    for row in all_provider_statuses():
        if row.get("unit") == "unlimited":
            continue
        cap = row.get("cap")
        if not cap:
            continue
        count = int(row.get("count", 0))
        daily = int(row.get("daily_count", count))
        ratio = min(1.0, count / cap)
        short = row.get("name", row["provider_id"]).split("(")[0].strip()
        st.sidebar.progress(ratio, text=f"{short} — {row['status_label']}")
        st.sidebar.caption(
            f"{row.get('period_label', '')}: {count}/{cap} · сегодня: {daily}"
            + (f" · ошибок: {row['failed_attempts']}" if row.get("failed_attempts") else "")
        )


def render_sidebar_project_update() -> None:
    status = project_status(ROOT)
    st.sidebar.markdown("---")
    with st.sidebar.expander("Обновление с master"):
        if status["branch"]:
            st.caption(f"Сейчас: `{status['branch']}` · {status['head'] or 'без коммитов'}")
        else:
            st.caption("Git в этом окружении недоступен, пока образ не пересобран с git.")
        st.caption(
            "Переключает рабочую копию на `master`, подтягивает `origin/master` "
            "и перезапускает контейнер `youtobe-studio`. "
            "Несохранённые правки в отслеживаемых файлах обновление остановят. "
            "Новые пакеты из requirements попадут в контейнер только после пересборки образа."
        )
        confirm = st.checkbox(
            "Подтянуть master и перезапустить Docker",
            key="confirm_pull_master_restart",
        )
        if st.button(
            "Обновить и перезапустить",
            type="primary",
            disabled=not confirm,
            key="pull_master_restart",
        ):
            ok, log = pull_master(ROOT)
            st.session_state["project_update_log"] = log
            st.session_state["project_update_ok"] = ok
            if ok:
                schedule_restart(ROOT)
        if "project_update_log" in st.session_state:
            if st.session_state.get("project_update_ok"):
                st.success("Master подтянут. Контейнер перезапустится через пару секунд, страница оборвётся и поднимется сама.")
            else:
                st.error("Обновление не выполнено.")
            st.code(st.session_state["project_update_log"] or "пусто")
        st.caption(
            "Локальные правки в смонтированной папке уже на диске контейнера. "
            "Чтобы Python их подхватил, нужен перезапуск процесса, без git pull."
        )
        if st.button("Применить файлы и перезапустить", key="restart_studio_only"):
            schedule_restart(ROOT)
            st.success("Контейнер перезапускается. Обновите страницу через несколько секунд.")


def sidebar() -> str:
    st.sidebar.title("YouTube Panel")
    render_sidebar_jobs_widget()
    labels = list(NAV_SECTIONS.values())
    keys = list(NAV_SECTIONS.keys())
    current = st.session_state.get("nav_section", "analytics")
    if current not in keys:
        current = "analytics"
        st.session_state.nav_section = current
    picked_label = st.sidebar.radio(
        "Раздел",
        labels,
        index=keys.index(current),
        label_visibility="visible",
        key="nav_section_radio",
    )
    section = keys[labels.index(picked_label)]
    if st.session_state.get("nav_section") != section:
        st.session_state.nav_section = section
        write_nav_to_url()
        st.rerun()
    render_sidebar_ai_settings()
    render_sidebar_quota_widget()
    render_sidebar_project_update()
    return section


def page_channel_settings(channel_slug: str) -> None:
    ch = CHANNELS_DIR / channel_slug
    if not ch.is_dir():
        st.warning("Канал не найден.")
        return
    ensure_brand_assets_dir(ch)
    settings = load_channel_settings(ch)
    branding = settings.get("branding", {})
    audio = settings.get("audio", {})
    visual = settings.get("visual", {})
    seo = settings.get("seo", {})

    st.header("⚙️ Настройки канала")
    st.caption(f"`{ch.relative_to(ROOT)}` · `channel_settings.json` + `brand_assets/`")

    with st.expander("🎬 Интро, аутро и водяной знак", expanded=True):
        use_intro = st.checkbox("Использовать intro.mp4", value=bool(branding.get("use_intro")))
        intro_text = st.text_input(
            "Текст интро",
            value=str(branding.get("intro_text") or ""),
            placeholder=channel_display_name(ch),
            help="Пустое поле — название канала из channel_config.md. Цвет титра #F5E6C8.",
        )
        text_start, text_dur, text_fade = st.columns(3)
        with text_start:
            intro_text_start = st.number_input(
                "Текст с, сек",
                min_value=0.0,
                max_value=30.0,
                value=float(branding.get("intro_text_start", 1.2)),
                step=0.1,
            )
        with text_dur:
            intro_text_duration = st.number_input(
                "Текст держится, сек",
                min_value=0.2,
                max_value=20.0,
                value=float(branding.get("intro_text_duration", 2.6)),
                step=0.1,
                help="От начала появления до начала затухания.",
            )
        with text_fade:
            intro_fade_sec = st.number_input(
                "Появление текста, сек",
                min_value=0.1,
                max_value=5.0,
                value=float(branding.get("intro_fade_sec", 1.0)),
                step=0.1,
            )
        intro_text_y = st.number_input(
            "Сдвиг текста по Y",
            min_value=-400.0,
            max_value=400.0,
            value=float(branding.get("intro_text_y", 0.0)),
            step=4.0,
            help="0 — по центру. Плюс сдвигает вниз.",
        )
        apply_series = st.checkbox(
            "Применить настройки интро ко всем сериям канала",
            value=bool(branding.get("apply_intro_to_series")),
            help="Записывает текст, тайминги и галочку интро в playlists/<серия>/channel_settings.json. Файл intro.mp4 серии, если он есть, важнее файла канала.",
        )
        st.caption(
            "Эпизод серии берёт интро канала, пока в json серии нет `intro_override: true`. "
            "Свой intro.mp4 в папке серии перекрывает файл канала. "
            "Шрифт выбирается у плейлиста в студии: сверху название канала, под ним заголовок эпизода."
        )
        use_outro = st.checkbox("Использовать outro.mp4", value=bool(branding.get("use_outro")))
        use_wm = st.checkbox("Водяной знак на ролике", value=bool(branding.get("use_watermark")))
        crossfade = st.slider(
            "Длительность перехода (с)",
            min_value=0.0,
            max_value=4.0,
            value=float(branding.get("crossfade_sec", 1.0)),
            step=0.1,
        )
        wm_opacity = st.slider(
            "Прозрачность watermark",
            min_value=0.1,
            max_value=0.5,
            value=float(branding.get("watermark_opacity", 0.25)),
            step=0.05,
        )
        intro_path = brand_asset_path(ch, "intro")
        if intro_path.is_file():
            st.video(intro_path.read_bytes())
        intro_up = st.file_uploader("intro.mp4", type=["mp4"], key=f"up_intro_{channel_slug}")
        outro_path = brand_asset_path(ch, "outro")
        if outro_path.is_file():
            st.video(outro_path.read_bytes())
        outro_up = st.file_uploader("outro.mp4", type=["mp4"], key=f"up_outro_{channel_slug}")
        wm_path = brand_asset_path(ch, "watermark")
        if wm_path.is_file():
            st.image(str(wm_path), width=220)
        wm_up = st.file_uploader("watermark.png", type=["png", "webp"], key=f"up_wm_{channel_slug}")

    with st.expander("🎙️ Звук и голос", expanded=True):
        voice_en = st.text_input("Голос EN (edge-tts)", value=str(audio.get("voice_en", "")))
        voice_ru = st.text_input("Голос RU (edge-tts)", value=str(audio.get("voice_ru", "")))
        speech_rate = st.text_input("Темп речи", value=str(audio.get("speech_rate", "-12%")))
        amb_vol = st.slider(
            "Громкость фона",
            0.0,
            0.35,
            float(audio.get("ambient_volume", 0.08)),
            step=0.01,
        )
        amb_path = brand_asset_path(ch, "ambient")
        if amb_path.is_file():
            st.audio(amb_path.read_bytes())
        amb_up = st.file_uploader("default_ambient.mp3", type=["mp3"], key=f"up_amb_{channel_slug}")

    with st.expander("🎨 Визуальный ДНК", expanded=True):
        style_suffix = st.text_area(
            "Суффикс стиля (добавляется к каждому PROMPT:)",
            value=str(visual.get("style_suffix", "")),
            height=80,
        )
        anim_method = st.selectbox(
            "Анимация по умолчанию",
            options=("ffmpeg_25d", "remotion", "comfyui"),
            index=("ffmpeg_25d", "remotion", "comfyui").index(
                str(visual.get("default_animation_method", "ffmpeg_25d"))
            )
            if str(visual.get("default_animation_method", "ffmpeg_25d")) in ("ffmpeg_25d", "remotion", "comfyui")
            else 0,
        )

    with st.expander("📝 SEO и публикация", expanded=False):
        footer_en = st.text_area("Подвал описания EN", value=str(seo.get("description_footer_en", "")), height=80)
        footer_ru = st.text_area("Подвал описания RU", value=str(seo.get("description_footer_ru", "")), height=80)
        tags_raw = seo.get("default_tags") or []
        tags_str = ", ".join(tags_raw) if isinstance(tags_raw, list) else str(tags_raw)
        default_tags = st.text_input("Базовые теги (через запятую)", value=tags_str)
        pub_time = st.text_input("Время публикации (EST)", value=str(seo.get("default_publish_time_est", "18:00")))

    if st.button("💾 Сохранить настройки канала", type="primary", key=f"save_ch_set_{channel_slug}"):
        uploads = {
            "intro": intro_up,
            "outro": outro_up,
            "watermark": wm_up,
            "ambient": amb_up,
        }
        for kind, blob in uploads.items():
            if blob is not None:
                dest = brand_asset_path(ch, kind)
                dest.write_bytes(blob.getvalue())
        new_settings = {
            "branding": {
                "use_intro": use_intro,
                "use_outro": use_outro,
                "crossfade_sec": float(crossfade),
                "use_watermark": use_wm,
                "watermark_opacity": float(wm_opacity),
                "intro_text": intro_text.strip(),
                "intro_text_start": float(intro_text_start),
                "intro_text_duration": float(intro_text_duration),
                "intro_fade_sec": float(intro_fade_sec),
                "intro_text_y": float(intro_text_y),
                "apply_intro_to_series": bool(apply_series),
            },
            "audio": {
                "voice_en": voice_en.strip(),
                "voice_ru": voice_ru.strip(),
                "speech_rate": speech_rate.strip(),
                "ambient_volume": float(amb_vol),
            },
            "visual": {
                "style_suffix": style_suffix.strip(),
                "default_animation_method": anim_method,
            },
            "seo": {
                "description_footer_en": footer_en.strip(),
                "description_footer_ru": footer_ru.strip(),
                "default_tags": [t.strip() for t in default_tags.split(",") if t.strip()],
                "default_publish_time_est": pub_time.strip() or "18:00",
            },
        }
        save_channel_settings(ch, new_settings)
        if apply_series:
            series_count = apply_intro_to_series(ch, new_settings["branding"])
            st.success(f"Сохранено. Интро записано в {series_count} серий.")
        else:
            st.success("Сохранено: channel_settings.json и brand_assets")
        st.rerun()


def page_capacity(channel_slug: str) -> None:
    st.header("⚙️ Мощности и Лимиты")
    st.caption(
        "Балансы — **запрос к API** (Cloudflare GraphQL neurons, HF billing, проба Pollinations). "
        "Расход кадров — локально из скриптов и `logs/job_*.log`. "
        "Кадры и LLM — провайдер из бокового меню **«Активные нейросети»** (`config/ai_settings.json`); "
        "резерв при ошибке: HF / Pollinations для кадров, другой LLM при 402/429."
    )

    if st.button("🔄 Обновить с серверов API", key="quota_remote_refresh"):
        st.session_state["quota_remote_cache"] = refresh_remote_quotas()
        sync_usage_from_logs()
        st.rerun()
    elif "quota_remote_cache" not in st.session_state:
        st.session_state["quota_remote_cache"] = refresh_remote_quotas()

    sync_info = sync_usage_from_logs()
    if sync_info.get("files_scanned"):
        st.info(
            f"Синхронизация с логами задач: файлов **{sync_info['files_scanned']}**, "
            f"CF OK **{sync_info.get('cloudflare_workers_ai_ok', 0)}**, "
            f"HF OK **{sync_info['huggingface_flux_ok']}**, "
            f"Pollinations OK **{sync_info['pollinations_fallback_ok']}**, "
            f"отказов Pollinations **{sync_info['pollinations_fallback_fail']}**."
        )
    else:
        st.caption("За сегодня фоновых job-логов не найдено — счётчики только из живых запусков скриптов.")

    st.subheader("Остаток бесплатных лимитов")
    cols = st.columns(3)
    idx = 0
    for row in all_provider_statuses():
        if row.get("unit") == "unlimited":
            continue
        cap = row.get("cap")
        if not cap:
            continue
        with cols[idx % 3]:
            count = int(row.get("count", 0))
            daily = int(row.get("daily_count", count))
            remaining = row.get("remaining", 0)
            ratio = min(1.0, count / cap)
            st.markdown(f"**{row.get('name')}** — {row['status_label']}")
            st.progress(ratio, text=f"{row.get('period_label', 'Период')}: {count} / {cap} {row.get('unit')}")
            st.caption(row.get("source_badge", ""))
            remote = row.get("remote") or {}
            if row["provider_id"] == "cloudflare_workers_ai":
                neurons_cap = int(remote.get("neurons_cap", 10_000))
                neurons_used = remote.get("neurons_used")
                if neurons_used is None:
                    neurons_used = int(daily) * 173
                neurons_used = int(neurons_used)
                meter_label = remote.get(
                    "neurons_meter_label",
                    "🌐 Cloudflare GraphQL API" if remote.get("neurons_ok") else "💻 Локальный счётчик",
                )
                neuron_ratio = min(1.0, neurons_used / neurons_cap) if neurons_cap else 0.0
                st.caption(
                    f"Использовано: **{neurons_used:,}** / {neurons_cap:,} neurons "
                    f"(сегодня по UTC) · {meter_label}"
                )
                st.progress(
                    neuron_ratio,
                    text=f"neurons: {neurons_used:,} / {neurons_cap:,}",
                )
                if remote.get("images_estimated_remaining") is not None:
                    st.caption(
                        f"≈ осталось кадров (~173 neurons/кадр, 4 steps): **{remote['images_estimated_remaining']}**"
                    )
                if remote.get("neurons_graphql_error") or (
                    not remote.get("neurons_ok") and remote.get("error")
                ):
                    err = remote.get("neurons_graphql_error") or remote.get("error")
                    st.warning(f"Cloudflare GraphQL: {err}")
                elif remote.get("error") and not remote.get("ok"):
                    st.warning(f"Cloudflare token: {remote['error']}")
                elif remote.get("message"):
                    st.caption(remote.get("message"))
                if remote.get("utc_date"):
                    st.caption(f"Окно UTC: `{remote['utc_date']}`")
                if st.button(
                    "🔄 Обновить данные с сервера",
                    key="cf_graphql_refresh",
                    width="stretch",
                ):
                    refresh_cloudflare_remote()
                    sync_usage_from_logs()
                    st.rerun()
            if row["provider_id"] == "huggingface_flux" and remote.get("ok"):
                st.caption(
                    f"**Баланс HF (API): ${remote.get('balance_usd', 0):.2f}** "
                    f"· режим: {remote.get('billing_mode', '—')}"
                )
            elif row["provider_id"] == "huggingface_flux" and remote.get("error"):
                st.warning(f"HF API: {remote['error']}")
            if row["provider_id"] == "youtube_data_api" and remote.get("console_url"):
                st.caption(remote.get("message", ""))
                st.link_button("YouTube Quotas (GCP)", remote["console_url"])
            if row["provider_id"] == "pollinations_fallback" and remote.get("http_status"):
                st.caption(
                    f"Проба Pollinations: HTTP **{remote['http_status']}** · {remote.get('message', '')}"
                )
            st.caption(
                f"Локальный расход — сегодня: **{daily}** · период: {count}/{cap} · окно: `{row.get('period_key')}`"
            )
            if row.get("failed_attempts"):
                st.caption(f"Неудачных запросов сегодня: **{row['failed_attempts']}**")
            if row.get("last_error_code"):
                st.caption(f"Последняя ошибка: **{row['last_error_code']}**")
            if row.get("last_success_at"):
                st.caption(f"Последний успех: {row['last_success_at']}")
        idx += 1

    events_rows: list[dict] = []
    for row in all_provider_statuses():
        for event in row.get("events") or []:
            events_rows.append(
                {
                    "Провайдер": row.get("name"),
                    "Время (UTC)": event.get("at", ""),
                    "Тип": event.get("type"),
                    "Кол-во": event.get("amount"),
                    "Примечание": event.get("note", ""),
                }
            )
    if events_rows:
        st.subheader("Журнал событий (последние)")
        st.dataframe(pd.DataFrame(events_rows).iloc[::-1], use_container_width=True)

    st.subheader("Pre-flight: планирование эпизода")
    if channel_slug:
        ch = channel_dir(channel_slug)
        episode_by_name = {path.name: path for path in list_episode_dirs(ch)}
        episode_names = sorted(episode_by_name)
        if episode_names:
            pick = st.selectbox("Эпизод", episode_names, key="capacity_episode")
            episode_dir = episode_by_name[pick]
            if st.button("Рассчитать запуск", key="capacity_preflight"):
                try:
                    plan = estimate_episode_workload(episode_dir)
                    st.session_state["capacity_plan"] = plan
                except Exception as exc:
                    st.error(f"Ошибка расчёта: {exc}")
            plan = st.session_state.get("capacity_plan")
            if plan and Path(plan.get("episode", "")).name == pick:
                c1, c2, c3, c4 = st.columns(4)
                c1.metric("Кадров к генерации", plan["images_to_generate"])
                c2.metric("Промптов всего", plan["prompt_count"])
                c3.metric("Помечено regen", plan["regen_flagged"])
                c4.metric("Озвучка, мин", plan["voice_minutes"])
                st.write(
                    f"Провайдер кадров: **{plan['image_provider']}** · "
                    f"ETA картинки: **{plan['eta_images_sec']} с** · "
                    f"озвучка: **{plan['eta_voice_sec']} с** · "
                    f"рендер: **{plan['eta_render_sec']} с** · "
                    f"**итого ~{plan['eta_total_min']} мин**"
                )
                if plan["quota_images_ok"]:
                    st.success(f"Квота на кадры: {plan['quota_images_label']}")
                else:
                    st.warning(f"Квота на кадры: {plan['quota_images_label']} — разбейте запуск или смените провайдер.")
            kw_path = ch / "channel_config.md"
            if kw_path.is_file():
                try:
                    from scout_trends import load_keywords

                    scout_units = estimate_youtube_scout_units(len(load_keywords(kw_path)))
                    st.caption(f"Один scout_trends ≈ **{scout_units}** YouTube units (поиск по keywords).")
                except Exception:
                    pass
        else:
            st.info("Нет эпизодов в канале.")
    else:
        st.info("Выберите канал в пилюлях сверху.")

    st.subheader("Справочник инструментов")
    specs = load_specs().get("providers", {})
    rows: list[dict] = []
    for provider_id, spec in specs.items():
        rows.append(
            {
                "id": provider_id,
                "name": spec.get("name", provider_id),
                "free_limit_description": spec.get("free_limit_description", ""),
                "cap_period": spec.get("cap_period", "daily"),
                "daily_or_hourly_cap": spec.get("daily_or_hourly_cap"),
                "unit": spec.get("unit", "units"),
                "avg_seconds_per_item": spec.get("avg_seconds_per_item", 0),
                "safe_delay_sec": spec.get("safe_delay_sec", 0),
            }
        )
    frame = pd.DataFrame(rows)
    edited = st.data_editor(
        frame,
        use_container_width=True,
        num_rows="fixed",
        key="providers_specs_editor",
        column_config={
            "id": st.column_config.TextColumn(disabled=True),
            "daily_or_hourly_cap": st.column_config.NumberColumn(min_value=0, step=1),
            "avg_seconds_per_item": st.column_config.NumberColumn(min_value=0.0, step=0.5),
            "safe_delay_sec": st.column_config.NumberColumn(min_value=0.0, step=0.5),
        },
    )
    if st.button("Сохранить спецификации", key="save_provider_specs"):
        providers: dict = {}
        for _, row in edited.iterrows():
            pid = str(row["id"])
            cap = row["daily_or_hourly_cap"]
            providers[pid] = {
                "name": str(row["name"]),
                "free_limit_description": str(row["free_limit_description"]),
                "cap_period": str(row["cap_period"]),
                "daily_or_hourly_cap": None if pd.isna(cap) else int(cap),
                "unit": str(row["unit"]),
                "avg_seconds_per_item": float(row["avg_seconds_per_item"]),
                "safe_delay_sec": float(row["safe_delay_sec"]),
            }
        save_specs({"providers": providers})
        st.success("Сохранено в config/providers_specs.json")
        st.rerun()


def _apply_niche_to_form(row: dict) -> None:
    fields = niche_to_form_fields(row)
    st.session_state["new_channel_slug"] = fields["slug"]
    st.session_state["new_channel_niche"] = fields["niche"]
    st.session_state["new_channel_duration"] = fields["duration"]
    st.session_state["new_channel_keywords"] = fields["keywords"]


def _render_niche_card(row: dict, idx: int, key_prefix: str) -> None:
    title = row.get("title", "—")
    badges = (
        f"**Востребованность** `{row.get('demand_score', '—')}/10` · "
        f"**RPM** `{row.get('estimated_rpm_usd', '—')}` · "
        f"**100k views** `{row.get('income_100k_views', '—')}` · "
        f"**Сложность** `{row.get('production_difficulty', '—')}/10` · "
        f"**Индекс** `{row.get('profitability_index', '—')}`"
    )
    with st.container(border=True):
        st.markdown(f"### {title}")
        st.markdown(badges)
        st.caption(f"{row.get('category', '—')} · `{row.get('slug_suggestion', '')}`")
        st.write(row.get("description", ""))
        st.caption(row.get("production_difficulty_label", ""))
        market = row.get("youtube_market") or {}
        if isinstance(market, dict):
            if market.get("ok"):
                st.markdown(
                    f"YouTube 30д · `{market.get('keyword', '')}`: "
                    f"ср. просмотры топа **{market.get('avg_views_top', 0):,}** · "
                    f"молодых каналов (до {market.get('young_subs_threshold', 15000):,} subs) "
                    f"с выстрелом **{market.get('young_breakouts', 0)}**"
                )
            elif market.get("error"):
                st.caption(f"YouTube: {market['error']}")
        conflict = row.get("channel_conflict")
        if conflict:
            st.warning(conflict)
        kws = row.get("keywords") or []
        if kws:
            st.code("\n".join(str(k) for k in kws), language=None)
        if st.button("📥 Подставить в форму", key=f"{key_prefix}_apply_{idx}"):
            _apply_niche_to_form(row)
            st.rerun()


def page_new_channel() -> None:
    st.header("➕ Новый канал")
    st.caption("ИИ-подбор — всегда свежие 5–6 подниш через Cloudflare LLM + YouTube по каждой идее.")

    tab_ai, tab_catalog = st.tabs(["🧠 ИИ-подбор", "📚 Быстрый выбор из базы"])

    with tab_ai:
        query = st.text_input(
            "Тема для анализа",
            placeholder="например: ai it sleep history, компьютерные игры, космос",
            key="niche_query",
        )
        if st.button("🧠 Подобрать и оценить подниши", key="niche_analyze_btn"):
            with st.spinner("Cloudflare LLM + YouTube по каждой идее…"):
                result = suggest_niches_ai(query)
                st.session_state["niche_ai_error"] = result.get("error")
                st.session_state["niche_ai_warning"] = result.get("warning")
                if result.get("ok"):
                    st.session_state["niche_ai_error"] = None
                    st.session_state["niche_suggestions"] = result.get("items") or []
                else:
                    st.session_state["niche_suggestions"] = []

        err = st.session_state.get("niche_ai_error")
        if err:
            st.error(err)
        warn = st.session_state.get("niche_ai_warning")
        if warn:
            st.warning(warn)

        suggestions: list[dict] = st.session_state.get("niche_suggestions") or []
        if suggestions:
            st.markdown(f"**Свежих подниш:** {len(suggestions)} (по индексу выгодности)")
            for idx, row in enumerate(suggestions):
                _render_niche_card(row, idx, "niche_ai")

    with tab_catalog:
        presets = load_presets()
        if not presets:
            st.info("Файл `config/niche_presets.json` пуст или отсутствует.")
        else:
            filter_q = st.text_input("Фильтр каталога", key="niche_catalog_filter")
            catalog = suggest_niches_catalog(filter_q)
            labels = [p.get("title", f"Preset {i}") for i, p in enumerate(catalog)]
            pick = st.selectbox(
                "Быстрый выбор из базы",
                options=list(range(len(catalog))),
                format_func=lambda i: labels[i],
                key="niche_preset_pick",
            )
            if catalog:
                _render_niche_card(catalog[pick], pick, "niche_cat")

    st.subheader("Создание папки канала")
    st.caption("Копия шаблонов в `channels/<slug>/`.")
    with st.form("new_channel_form"):
        template_options = list_channel_templates()
        template_pick = st.selectbox(
            "Шаблон канала",
            options=list(range(len(template_options))),
            index=0,
            format_func=lambda i: template_options[i][1],
            key="new_channel_template",
        )
        slug = st.text_input(
            "Имя папки",
            placeholder="channel_03_space_sleep",
            value=st.session_state.get("new_channel_slug", ""),
        )
        niche = st.text_area(
            "Ниша",
            value=st.session_state.get("new_channel_niche", ""),
        )
        duration = st.text_input(
            "Целевой хронометраж",
            value=st.session_state.get("new_channel_duration", "10-15 минут"),
        )
        keywords = st.text_area(
            "Search keywords (по одному на строку)",
            value=st.session_state.get("new_channel_keywords", ""),
        )
        submitted = st.form_submit_button("Создать канал")
    if submitted and slug.strip():
        try:
            create_channel_folder(
                slug.strip(),
                niche.strip(),
                duration.strip(),
                keywords,
                template_options[template_pick][0],
            )
            st.success(f"Канал создан: {slug}")
            st.session_state.channel_slug = slug.strip()
            st.session_state.nav_section = "analytics"
            write_nav_to_url()
            st.rerun()
        except Exception as exc:
            st.error(str(exc))


def page_analytics(channel_slug: str) -> None:
    st.header("Аналитика и бэклог")
    if not channel_slug:
        st.info("Сначала создайте или выберите канал.")
        return
    ch = channel_dir(channel_slug)
    backlog_path = ch / "backlog.csv"
    published_path = ch / "published.csv"
    backlog = load_channel_backlog(backlog_path)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Идей в бэклоге", len(backlog))
    c2.metric("Готовых видео", sum(1 for path in list_episode_dirs(ch) if (path / "final_video.mp4").is_file()))
    published_count = max(len(load_csv(published_path)) - 0, 0) if published_path.is_file() else 0
    if published_path.is_file():
        published_count = len(pd.read_csv(published_path, encoding="utf-8-sig"))
    c3.metric("Опубликовано", published_count)
    c4.metric("Медиана Views_7d", median_views_7d(published_path))

    st.subheader("Backlog")
    st.caption(
        "Одиночные идеи и серии плейлистов лежат в одном файле. "
        "Серия определяется `Series_ID` и папкой `playlists/<плейлист>/episodes/`."
    )
    if not backlog.empty:
        backlog = backlog.copy()
        backlog["Badge"] = [format_backlog_row_label(row, ch) for _, row in backlog.iterrows()]
        display = backlog[["Badge", *BACKLOG_COLUMNS]]
    else:
        display = backlog
    edited = st.data_editor(
        display if not backlog.empty else backlog,
        num_rows="dynamic",
        use_container_width=True,
        column_config={
            "Badge": st.column_config.TextColumn("Формат", disabled=True),
            "Status": st.column_config.SelectboxColumn(options=["skeleton_ready", "Idea", "research", "scripted", "ready", "published", "dropped"]),
            "Priority": st.column_config.SelectboxColumn(options=["P0", "P1", "P2"]),
            "Series_ID": st.column_config.TextColumn(help="standalone или S01, S02…"),
        },
        key=f"backlog_editor_{channel_slug}",
    )
    if st.button("Сохранить изменения backlog", key="save_backlog"):
        frame = pd.DataFrame(edited)
        if "Badge" in frame.columns:
            frame = frame.drop(columns=["Badge"])
        save_backlog(backlog_path, frame)
        st.success("backlog.csv сохранён.")

    st.subheader("Scout трендов")
    if st.button("🔍 Запустить поиск трендов (Scout)"):
        with st.spinner("Scout..."):
            result = run_script("scout_trends.py", f"channels/{channel_slug}", timeout=1800)
        show_process_result(result, "Scout")
        st.rerun()

    scout_path = ch / "analytics_dump" / "latest_scout.csv"
    if scout_path.is_file():
        scout_df = pd.read_csv(scout_path, encoding="utf-8-sig")
        st.dataframe(scout_df, use_container_width=True)
        pick = st.multiselect(
            "Строки для переноса в бэклог",
            scout_df.index.tolist(),
            format_func=lambda i: scout_df.loc[i, "Video_Title"],
        )
        if st.button("Перенести выбранные в бэклог") and pick:
            added = import_scout_rows(channel_slug, scout_df.loc[pick])
            st.success(f"Добавлено идей: {added}")
            st.rerun()
    else:
        st.caption("Отчёта latest_scout.csv пока нет.")


def render_phrase_database(channel: Path, channel_slug: str) -> None:
    """Таблицы transitions и phrases из channel.db, с прослушиванием mp3."""
    database = channel / "database" / "channel.db"
    if not database.is_file():
        return
    import sqlite3

    with st.expander("База фраз и аудио (SQLite)", expanded=False):
        conn = None
        try:
            conn = sqlite3.connect(database)
            transitions = pd.read_sql(
                "SELECT id, category, text_en, audio_path, usage_count FROM transitions ORDER BY category, usage_count, id",
                conn,
            )
            phrases = pd.read_sql(
                """
                SELECT id, playlist_name, episode_id, semantic_id, role, text_target, text_native,
                       audio_target_path, audio_native_path, created_at
                FROM phrases
                ORDER BY episode_id, semantic_id
                """,
                conn,
            )
        except sqlite3.Error as exc:
            st.warning(f"Не удалось прочитать channel.db: {exc}")
            return
        finally:
            if conn is not None:
                conn.close()

        tab_transitions, tab_phrases = st.tabs(["Переходы", "Фразы"])
        with tab_transitions:
            st.dataframe(transitions, use_container_width=True, hide_index=True)
            _play_db_audio(channel, transitions, "audio_path", "text_en", f"tr_{channel_slug}")
        with tab_phrases:
            st.dataframe(phrases, use_container_width=True, hide_index=True)
            _play_db_audio(channel, phrases, "audio_target_path", "text_target", f"ph_t_{channel_slug}")
            _play_db_audio(channel, phrases, "audio_native_path", "text_native", f"ph_n_{channel_slug}")


def _play_db_audio(channel: Path, frame: pd.DataFrame, path_column: str, label_column: str, key: str) -> None:
    if frame.empty or path_column not in frame.columns:
        return
    options: list[tuple[str, Path]] = []
    for _, row in frame.iterrows():
        rel = str(row.get(path_column) or "").strip()
        if not rel:
            continue
        file_path = Path(rel)
        if not file_path.is_absolute():
            file_path = channel / rel
        if file_path.is_file():
            label = str(row.get(label_column) or file_path.name).strip()
            options.append((f"{label} — {file_path.name}", file_path))
    if not options:
        st.caption(f"В колонке {path_column} пока нет готовых mp3.")
        return
    labels = [item[0] for item in options]
    picked = st.selectbox(f"Прослушать ({path_column})", labels, key=f"db_audio_{key}_{path_column}")
    st.audio(str(options[labels.index(picked)][1]))


def episode_staged_video_build(episode_dir: Path) -> bool:
    """Длинная FFmpeg-сборка (сюжет / хвост / финал), не только sleep-канал."""
    try:
        from isekai_video import inspect_long_episode

        return inspect_long_episode(episode_dir) is not None
    except (FileNotFoundError, RuntimeError, OSError):
        return False


def page_studio(channel_slug: str) -> None:
    st.header("🎬 Студия производства и доработки")
    if not channel_slug:
        st.info("Выберите канал.")
        return
    ch = channel_dir(channel_slug)
    isekai_mode = bool(load_isekai_settings(ch).get("TARGET_LANG"))
    if isekai_mode:
        st.info(
            "Формат «язык во сне». Сценарий — блоки `[ID|роль|скорость]`, "
            "озвучка — только новые фразы в одну дорожку `voiceover.mp3`."
        )
    episodes_root = ch / "episodes"
    episodes_root.mkdir(exist_ok=True)
    episode_by_name = {path.name: path for path in list_episode_dirs(ch)}
    episode_names = sorted(episode_by_name)
    backlog = load_channel_backlog(ch / "backlog.csv")

    if "episode_name" not in st.session_state:
        st.session_state.episode_name = episode_names[0] if episode_names else ""

    pin_running = False
    ep_key = ""
    episode_dir: Path | None = None

    # —— Блок 1: эпизод, идеи, full pipeline ——
    st.subheader("Эпизод и запуск")
    col_ep, col_ideas = st.columns([1, 1])
    playlist_key = f"studio_playlist_{channel_slug}"
    if playlist_key not in st.session_state:
        st.session_state[playlist_key] = STANDALONE_ID
    url_pl = st.session_state.pop("_url_playlist", None)
    ep_from_url = str(st.session_state.get("episode_name") or "").strip()
    if ep_from_url:
        for ep_path in list_episode_dirs(ch):
            if ep_path.name == ep_from_url:
                inferred = playlist_id_from_episode_dir(ep_path, ch)
                if inferred and not url_pl:
                    st.session_state[playlist_key] = inferred
                break
    playlists = load_playlists(ch)
    playlist_ids = [STANDALONE_ID, *[str(p["id"]) for p in playlists]]
    if url_pl:
        match = next((pid for pid in playlist_ids if pid.lower() == str(url_pl).lower()), None)
        if match:
            st.session_state[playlist_key] = match
    playlist_labels = [STANDALONE_UI_LABEL, *[playlist_label(p) for p in playlists]]
    cur_pl = st.session_state[playlist_key]
    if cur_pl not in playlist_ids:
        cur_pl = STANDALONE_ID
        st.session_state[playlist_key] = cur_pl

    with col_ep:
        from episode_launch_ui import render_episode_launch_controls
        selected_playlist, episode_dir = render_episode_launch_controls(
            channel_slug=channel_slug,
            ch=ch,
            episodes_root=episodes_root,
            episode_by_name=episode_by_name,
            episode_names=episode_names,
            playlist_key=playlist_key,
            playlist_ids=playlist_ids,
            playlist_labels=playlist_labels,
            write_nav_to_url=write_nav_to_url,
            remember_episode_title=_remember_episode_title,
            rename_playlist=_rename_playlist,
            next_manual_standalone_id=_next_manual_standalone_id,
            ensure_episode_files=ensure_episode_files,
            load_channel_backlog=load_channel_backlog,
        )

        if selected_playlist != STANDALONE_ID:
            pdir = playlist_dir_for_series(ch, selected_playlist)
            bible = series_bible_path(ch, selected_playlist)
            _BIBLE_RU_LABELS = {
                "world_and_atmosphere": "🌍 Мир и атмосфера",
                "characters_visual_lock": "🧙‍♂️ Герои и внешность (Visual Lock)",
                "lore_and_rules": "🔮 Правила и лор",
            }
            with st.expander("📖 Этап 1 — Bible плейлиста (НЕ трогает сценарий эпизода)", expanded=False):
                if pdir is None:
                    st.caption("Плейлист не найден.")
                else:
                    mig_key = f"bible_migrated_{channel_slug}_{selected_playlist}"
                    if not st.session_state.get(mig_key):
                        with st.spinner("Подготовка series_bible.json…"):
                            mig_err = ensure_series_bible_artifacts(ch, selected_playlist)
                        if mig_err:
                            st.error(mig_err)
                        else:
                            st.session_state[mig_key] = True
                    bible_data = load_series_bible_json(pdir)
                    brief_key = f"series_bible_brief_{channel_slug}_{selected_playlist}"
                    if brief_key not in st.session_state:
                        st.session_state[brief_key] = read_producer_brief(ch, selected_playlist)
                    with st.form(
                        f"bible_regen_form_{channel_slug}_{selected_playlist}",
                        clear_on_submit=False,
                    ):
                        st.caption(
                            "Только плейлист: `producer_brief.md` + секции bible. "
                            "Генерация/синхронизация сценария эпизода сюда не пишет."
                        )
                        reload_disk = st.form_submit_button("↻ С диска")
                        st.text_area(
                            "Примерное описание произведения (producer_brief.md)",
                            height=100,
                            placeholder="Мир, герои, тон, арка на 5 серий…",
                            key=brief_key,
                        )
                        regen_submit = st.form_submit_button(
                            "🔄 Перегенерировать из описания",
                            use_container_width=True,
                        )
                    if reload_disk:
                        st.session_state[brief_key] = read_producer_brief(ch, selected_playlist)
                        st.rerun()
                    if regen_submit:
                        brief = (st.session_state.get(brief_key) or "").strip()
                        if not brief:
                            st.warning("Сначала заполните описание.")
                        else:
                            save_producer_brief(ch, selected_playlist, brief)
                            _llm = load_ai_settings()
                            with st.spinner(
                                f"{_llm.get('text_provider')} — bible из brief…"
                            ):
                                err = regenerate_series_bible_from_brief(
                                    ch,
                                    selected_playlist,
                                    creator_brief=brief,
                                )
                            if err:
                                st.error(err)
                            else:
                                st.success("Bible перегенерирован (json + EN md).")
                                st.rerun()

                    for section_key, title in _BIBLE_RU_LABELS.items():
                        field_key = f"bible_ru_{channel_slug}_{selected_playlist}_{section_key}"
                        if field_key not in st.session_state:
                            st.session_state[field_key] = str(
                                bible_data.get(section_key, {}).get("ru", "")
                            )
                        st.text_area(
                            title,
                            height=130,
                            key=field_key,
                        )

                    st.markdown("#### 📜 Хроника серий (Recap)")
                    del_key = f"bible_recap_hidden_{channel_slug}_{selected_playlist}"
                    if del_key not in st.session_state:
                        st.session_state[del_key] = set()
                    hidden_eps = st.session_state[del_key]
                    recaps = [
                        r
                        for r in (bible_data.get("recaps") or [])
                        if str(r.get("episode", "")).strip().upper() not in hidden_eps
                    ]
                    for idx, row in enumerate(recaps):
                        ep = str(row.get("episode", ""))
                        recap_key = f"bible_recap_{channel_slug}_{selected_playlist}_{ep}"
                        if recap_key not in st.session_state:
                            st.session_state[recap_key] = str(row.get("summary_ru", ""))
                        c1, c2 = st.columns([5, 1])
                        with c1:
                            st.text_area(
                                f"{ep} — {row.get('title', '')}",
                                height=90,
                                key=recap_key,
                            )
                        with c2:
                            if st.button(
                                "Удалить",
                                key=f"del_recap_{channel_slug}_{selected_playlist}_{ep}",
                            ):
                                hidden_eps.add(ep.strip().upper())
                                st.session_state[del_key] = hidden_eps
                                st.rerun()

                    col_save, col_ai = st.columns(2)
                    config_path = ch / "channel_config.md"
                    config_excerpt = (
                        config_path.read_text(encoding="utf-8-sig")[:6500]
                        if config_path.is_file()
                        else ""
                    )
                    with col_save:
                        if st.button(
                            "💾 Сохранить мои правки (обновить EN-канон)",
                            key=f"bible_save_{channel_slug}_{selected_playlist}",
                            use_container_width=True,
                        ):
                            updated_ru = {
                                k: str(
                                    st.session_state.get(
                                        f"bible_ru_{channel_slug}_{selected_playlist}_{k}",
                                        "",
                                    )
                                ).strip()
                                for k in BIBLE_SECTION_KEYS
                            }
                            with st.spinner("Перевод RU→EN и пересборка series_bible.md…"):
                                err = sync_and_regenerate_bible(
                                    pdir,
                                    updated_ru,
                                    mode="translate_only",
                                )
                            if err:
                                st.error(err)
                            else:
                                fresh = load_series_bible_json(pdir)
                                fresh["recaps"] = [
                                    r
                                    for r in (fresh.get("recaps") or [])
                                    if str(r.get("episode", "")).strip().upper() not in hidden_eps
                                ]
                                for row in fresh["recaps"]:
                                    ep = str(row.get("episode", ""))
                                    rk = f"bible_recap_{channel_slug}_{selected_playlist}_{ep}"
                                    ru = str(st.session_state.get(rk, row.get("summary_ru", ""))).strip()
                                    row["summary_ru"] = ru
                                    if ru:
                                        en_text, tr_err = generate_text(
                                            [
                                                {
                                                    "role": "system",
                                                    "content": "Translate episode recap RU→EN. Output only English text.",
                                                },
                                                {"role": "user", "content": ru},
                                            ],
                                            temperature=0.2,
                                            max_tokens=400,
                                        )
                                        if en_text and not tr_err:
                                            row["summary_en"] = en_text.strip()
                                save_series_bible_data(pdir, fresh)
                                st.session_state[del_key] = set()
                                st.success("Сохранено: series_bible.json + EN series_bible.md")
                                st.rerun()
                    with col_ai:
                        if st.button(
                            "✨ Перегенерировать Библию с учётом моих правок",
                            key=f"bible_regen_ai_{channel_slug}_{selected_playlist}",
                            use_container_width=True,
                        ):
                            updated_ru = {
                                k: str(
                                    st.session_state.get(
                                        f"bible_ru_{channel_slug}_{selected_playlist}_{k}",
                                        "",
                                    )
                                ).strip()
                                for k in BIBLE_SECTION_KEYS
                            }
                            brief = (st.session_state.get(brief_key) or "").strip()
                            _llm = load_ai_settings()
                            with st.spinner(
                                f"{_llm.get('text_provider')} — согласование bible…"
                            ):
                                err = sync_and_regenerate_bible(
                                    pdir,
                                    updated_ru,
                                    mode="regenerate_around_edits",
                                    config_excerpt=config_excerpt,
                                    producer_brief=brief,
                                )
                            if err:
                                st.error(err)
                            else:
                                st.success("Bible обновлена вокруг ваших правок (recaps сохранены).")
                                st.rerun()

                    if bible and bible.is_file():
                        with st.expander("EN-компилят series_bible.md (только чтение)"):
                            st.caption(read_series_bible(ch, selected_playlist)[:4000])

    with col_ideas:
        st.markdown("**💡 Идеи в бэклоге**")
        prefs_key = f"idea_prefs_kw_{channel_slug}"
        brief_key = f"idea_prefs_brief_{channel_slug}"
        if prefs_key not in st.session_state:
            st.session_state[prefs_key] = ""
        if brief_key not in st.session_state:
            st.session_state[brief_key] = ""
        idea_keywords = st.text_input(
            "Ключевые слова (необязательно, можно по-русски)",
            key=prefs_key,
            placeholder="например: магия, ученик, таверна",
            help="Topic — на английском; пожелания можно по-русски.",
        )
        idea_brief = st.text_area(
            "Описание / пожелания (необязательно)",
            key=brief_key,
            height=68,
            placeholder="для Standalone — разные герои; для сериала — уточните тон арки",
        )
        if selected_playlist == STANDALONE_ID:
            if st.button("✨ Сгенерировать 5 новых идей", key=f"studio_gen_ideas_{channel_slug}"):
                with st.spinner("Workers AI (Llama 3.3)…"):
                    added, err = generate_backlog_ideas(
                        ch,
                        count=5,
                        preference_keywords=idea_keywords or "",
                        preference_brief=idea_brief or "",
                    )
                if err:
                    st.error(err)
                elif added:
                    st.success(f"Добавлено standalone-идей: {added}")
                    backlog = load_channel_backlog(ch / "backlog.csv")
                    st.rerun()
        else:
            st.caption(
                f"Плейлист **{selected_playlist}**: новый выпуск — popover «Следующее видео»; "
                "сериал из **standalone-идеи** — «Развернуть в многосерийный плейлист»."
            )
        idea_options = filter_backlog_by_playlist(
            backlog[backlog["Status"].astype(str).isin(["Idea", "research"])]
            if not backlog.empty
            else backlog,
            STANDALONE_ID,
        )
        if idea_options.empty:
            st.caption("Нет идей со статусом Idea/research в бэклоге.")
        else:
            labels = [format_backlog_row_label(row, ch) for _, row in idea_options.iterrows()]
            idea_pick = st.selectbox(
                "Идея из бэклога",
                labels,
                key=f"studio_idea_pick_{channel_slug}_{selected_playlist}",
            )
            picked_row = idea_options.iloc[labels.index(idea_pick)]
            mk_standalone, mk_series = st.columns(2)
            with mk_standalone:
                if st.button(
                    "🎬 Создать одиночное видео (1 эпизод)",
                    key=f"studio_mk_standalone_{channel_slug}",
                    use_container_width=True,
                ):
                    try:
                        folder = create_episode_folder(channel_slug, picked_row)
                        st.session_state[f"studio_playlist_{channel_slug}"] = STANDALONE_ID
                        st.session_state.episode_name = folder.name
                        st.success(f"Standalone: {folder.relative_to(ROOT)}")
                        st.rerun()
                    except Exception as exc:
                        st.error(str(exc))
            with mk_series:
                if st.button(
                    "📚 Развернуть в многосерийный плейлист (Сериал)",
                    key=f"studio_expand_series_{channel_slug}",
                    use_container_width=True,
                    disabled=not row_can_expand_to_series(picked_row),
                ):
                    with st.spinner("Плейлист, bible, пилот S01E01…"):
                        playlist, folder, err = expand_backlog_idea_to_series(
                            ch,
                            backlog_id=str(picked_row.get("ID", "")).strip(),
                        )
                    if err:
                        st.error(err)
                    elif folder:
                        ensure_episode_files(
                            folder,
                            topic_en=str(picked_row.get("Topic", "Episode")),
                            topic_ru=str(picked_row.get("Topic_RU", "")),
                        )
                        st.session_state[f"studio_playlist_{channel_slug}"] = str(playlist.get("id", ""))
                        st.session_state.episode_name = folder.name
                        st.success(
                            f"Сериал {playlist.get('id')}: пилот {folder.name}, bible без E02–E05."
                        )
                        st.rerun()
            if not row_can_expand_to_series(picked_row):
                st.caption("Эта строка уже сериал — развёртывание недоступно.")

    if episode_dir is not None:
        ep_key = episode_dir.name
        pin_running = episode_has_running_job(episode_dir)
    else:
        pin_running = False

    if not episode_dir:
        return

    epi_cfg_live = load_episode_config(episode_dir, channel_slug, ch)
    from tail_controls import sync_tail_session_from_disk

    sync_tail_session_from_disk(ep_key, epi_cfg_live)

    if isekai_mode:
        render_phrase_database(ch, channel_slug)

    review = load_review(episode_dir)
    images_dir_early = episode_dir / "images"
    early_names = []
    if images_dir_early.is_dir():
        early_names = [
            path.name
            for path in images_dir_early.iterdir()
            if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES
        ]
    frame_stem_options = (
        sorted({stem_from_filename(name) for name in early_names})
        if early_names
        else [f"{index:02d}" for index in range(1, 25)]
    )

    ep_backlog_id = episode_dir.name.split("_", 1)[0]
    ep_row = backlog_row_by_episode_id(backlog, ep_backlog_id)
    if ep_row is not None and row_can_expand_to_series(ep_row) and not isekai_mode:
        if st.button(
            "🔄 Превратить этот выпуск в Серию 1 нового плейлиста",
            key=f"convert_ep_series_{ep_key}",
            help="Создаст S0N, bible по сценарию, S0NE01 и идеи серий 2–5",
        ):
            script_path = episode_dir / "script.md"
            script_seed = ""
            if script_path.is_file():
                script_seed = script_path.read_text(encoding="utf-8-sig")[:15000]
            with st.spinner("Конвертация в сериал…"):
                playlist, folder, err = expand_backlog_idea_to_series(
                    ch,
                    backlog_id=str(ep_row.get("ID", ep_backlog_id)).strip(),
                    script_seed=script_seed,
                    existing_episode_dir=episode_dir,
                )
            if err:
                st.error(err)
            elif folder:
                ensure_episode_files(
                    folder,
                    topic_en=str(ep_row.get("Topic", "Episode")),
                    topic_ru=str(ep_row.get("Topic_RU", "")),
                )
                st.session_state[f"studio_playlist_{channel_slug}"] = str(playlist.get("id", ""))
                st.session_state.episode_name = folder.name
                st.success(f"Готово: {playlist.get('id')} · {folder.name}")
                st.rerun()

    pipe_l, pipe_r = st.columns([3, 1])
    with pipe_l:
        st.caption(f"Конвейер для `{episode_dir.relative_to(ROOT)}`")
    with pipe_r:
        pipe_label = (
            "🚀 Новые фразы, кадры и ночное видео"
            if isekai_mode
            else "🚀 Полная генерация эпизода (Full Pipeline)"
        )
        if st.button(
            pipe_label,
            key=f"full_pipe_{ep_key}",
            use_container_width=True,
        ):
            review_save = load_review(episode_dir)
            if not review_save.get("animate_frames"):
                review_save["animate_frames"] = normalize_animate_frames(None)
            save_review(episode_dir, review_save)
            launch_background_job("full_pipeline", episode_dir, "run_full_pipeline.py")
    files = {
        "script_ru.md (RU)": episode_dir / "script_ru.md",
        "script.md (EN)": episode_dir / "script.md",
        "visual_prompts.md": episode_dir / "visual_prompts.md",
        "metadata.md": episode_dir / "metadata.md",
    }

    if isekai_mode:
        import isekai_panel

        importlib.reload(isekai_panel)
        from isekai_panel import render_isekai_production

        _isekai_kw = dict(
            episode_dir=episode_dir,
            channel_slug=channel_slug,
            ep_key=ep_key,
            editor_key=editor_key,
            save_texts=save_episode_text_files,
            launch_job=launch_background_job,
            acknowledge=acknowledge_episode_job_outputs,
            sync_editors=sync_editors_from_disk_if_newer,
            show_section_headings=False,
        )
        with st.expander("1 — Сценарий блоками", expanded=False):
            render_isekai_production(**_isekai_kw, blocks=("script",))
        with st.expander("2 — Озвучка", expanded=False):
            render_isekai_production(**_isekai_kw, blocks=("voice",))
    if not isekai_mode:
        with st.expander("1 — Сценарий и промпты", expanded=False):
            st.caption("Документация: `docs/episode-stage-2-script.md` · Bible — отдельно (этап 1).")

            epi_cfg = load_episode_config(episode_dir, channel_slug, ch)
            plot_on_disk = load_plot_brief(episode_dir)
            plan_key = f"plot_brief_{ep_key}"
            if plan_key not in st.session_state and plot_on_disk:
                st.session_state[plan_key] = plot_on_disk

            dcol1, dcol2, dgen = st.columns([1, 1, 2])
            with dcol1:
                st.number_input(
                    "Длительность (мин) — от",
                    min_value=3.0,
                    max_value=90.0,
                    value=float(epi_cfg["target_min_minutes"]),
                    step=1.0,
                    key=f"ep_min_min_{ep_key}",
                )
            with dcol2:
                st.number_input(
                    "Длительность (мин) — до",
                    min_value=3.0,
                    max_value=120.0,
                    value=float(epi_cfg["target_max_minutes"]),
                    step=1.0,
                    key=f"ep_max_min_{ep_key}",
                )
            st.caption(episode_plan_summary(episode_dir, channel_slug))
            tcol1, tcol2, tcol3, tcol4, tcol5 = st.columns([1, 1, 1, 1, 1])
            manual_frames = epi_cfg.get("target_frame_count")
            frame_default = int(manual_frames) if manual_frames else 0
            with tcol1:
                st.number_input(
                    "Кадров (0 = авто)",
                    min_value=0,
                    max_value=80,
                    value=frame_default,
                    step=1,
                    key=f"ep_frame_count_{ep_key}",
                    help="Влияет на число PROMPT при ИИ-генерации. generate_images.py рисует все строки в файле.",
                )
            frame_ui = int(st.session_state.get(f"ep_frame_count_{ep_key}", frame_default))
            min_v_ui = float(st.session_state.get(f"ep_min_min_{ep_key}", epi_cfg["target_min_minutes"]))
            max_v_ui = float(st.session_state.get(f"ep_max_min_{ep_key}", epi_cfg["target_max_minutes"]))
            spf_ui = float(st.session_state.get(f"ep_spf_{ep_key}", epi_cfg.get("seconds_per_frame", 40)))
            auto_hint = auto_frame_count((min_v_ui + max_v_ui) / 2.0, spf_ui)
            with tcol2:
                st.number_input(
                    "Сек/кадр (авто)",
                    min_value=25.0,
                    max_value=60.0,
                    value=float(epi_cfg.get("seconds_per_frame", 40)),
                    step=1.0,
                    key=f"ep_spf_{ep_key}",
                    disabled=frame_ui > 0,
                )
                if frame_ui <= 0:
                    st.caption(f"При авто ≈ **{auto_hint}** кадров")
            with tcol3:
                st.number_input(
                    "Затухание голоса (с)",
                    min_value=0.0,
                    max_value=120.0,
                    value=float(epi_cfg.get("voice_fade_out_sec", 12)),
                    step=1.0,
                    key=f"ep_voice_fade_{ep_key}",
                )
            with tcol4:
                st.number_input(
                    "Хвост ambient (с)",
                    min_value=0.0,
                    max_value=600.0,
                    value=float(epi_cfg.get("ambient_tail_sec", 0)),
                    step=5.0,
                    key=f"ep_ambient_tail_{ep_key}",
                    help="Короткий хвост MoviePy: последний кадр, если длинный эмбиент-хвост выключен.",
                )
            with tcol5:
                st.write("")
                if st.button(
                    "💾 Сохранить настройки эпизода",
                    key=f"ep_cfg_save_{ep_key}",
                    use_container_width=True,
                ):
                    persist_episode_config_before_job(episode_dir, ep_key, epi_cfg)
                    st.toast("episode_config.json сохранён")
                    st.rerun()
            with dgen:
                st.text_area(
                    "Сюжет / бриф выпуска (RU → plot_brief.txt)",
                    height=100,
                    key=plan_key,
                    placeholder="Рамка выпуска: начало, финал, запреты.",
                )
                st.caption("Только для **генерации с нуля** — в переработку не подмешивается.")
                if st.button("✍️ Сгенерировать сценарий с нуля", key=f"ai_pack_{ep_key}", type="primary"):
                    persist_episode_plan_before_job(episode_dir, ep_key, epi_cfg, plan_key)
                    launch_background_job("generation", episode_dir, "generate_episode_pack.py")

            ed_col, tool_col = st.columns([4, 1])
            with ed_col:
                acknowledge_episode_job_outputs(episode_dir, ep_key)
                sync_editors_from_disk_if_newer(channel_slug, files)
                tabs = st.tabs(list(files.keys()))
                for tab, (label, path) in zip(tabs, files.items()):
                    with tab:
                        content = path.read_text(encoding="utf-8-sig") if path.is_file() else ""
                        size = path.stat().st_size if path.is_file() else 0
                        st.caption(f"`{path.name}` · {size} байт")
                        st.text_area(label, content, height=300, key=editor_key(path, channel_slug))
                if st.button("💾 Сохранить тексты", key=f"save_all_txt_{ep_key}"):
                    n = save_episode_text_files(channel_slug, files)
                    st.success(f"Сохранено файлов: {n}")
                    st.rerun()

            with tool_col:
                st.markdown("**🛠️ ИИ-переработка**")
                truth_key = f"truth_source_{ep_key}"
                if truth_key not in st.session_state:
                    st.session_state[truth_key] = "ru"
                truth = st.radio(
                    "Источник истины",
                    options=("ru", "en"),
                    format_func=lambda v: (
                        "🇷🇺 Русский (script_ru.md)" if v == "ru" else "🇬🇧 Английский (script.md)"
                    ),
                    key=truth_key,
                )
                st.text_area(
                    "Комментарий / инструкция для ИИ",
                    height=100,
                    key=f"sync_instruction_{ep_key}",
                    placeholder="Напр.: смягчи финал, убери алхимию во 2-й главе.",
                )
                st.caption(
                    "Берёт **script_ru.md** или **script.md** (источник истины), правит текст **только по комментарию**, "
                    "синхронизирует второй язык, заново пишет **visual_prompts** и **metadata**. "
                    "`plot_brief` — только у кнопки «Сгенерировать с нуля»."
                )
                if st.button("✨ Переработать и синхронизировать", key=f"sync_scripts_{ep_key}", use_container_width=True):
                    instruction = str(st.session_state.get(f"sync_instruction_{ep_key}", "")).strip()
                    if not instruction:
                        st.error("Заполните комментарий — переработка без plot_brief не запускается.")
                    else:
                        save_episode_text_files(channel_slug, files)
                        persist_episode_config_before_job(episode_dir, ep_key, epi_cfg)
                        (episode_dir / ".sync_instruction.txt").write_text(instruction, encoding="utf-8")
                        (episode_dir / "sync_instruction_last.txt").write_text(instruction, encoding="utf-8")
                        launch_background_job(
                            "generation",
                            episode_dir,
                            "sync_episode_pack.py",
                            "--primary",
                            truth,
                        )

                st.markdown("**🕰️ История версий**")
                snaps = list_episode_snapshots(episode_dir)
                if snaps:
                    snap_ids = [str(s["id"]) for s in snaps]
                    snap_labels = {str(s["id"]): snapshot_display_line(s) for s in snaps}
                    pick = st.selectbox(
                        "Версия",
                        options=snap_ids,
                        format_func=lambda sid: snap_labels.get(sid, sid),
                        key=f"history_pick_{ep_key}",
                    )
                    if st.button("⏪ Восстановить", key=f"history_restore_{ep_key}", use_container_width=True):
                        err = restore_episode_snapshot(episode_dir, pick)
                        if err:
                            st.error(err)
                        else:
                            bump_episode_text_editors(episode_dir)
                            st.success("Версия восстановлена (текущая сохранена в history/)")
                            st.rerun()
                else:
                    st.caption("Пока нет снапшотов — появятся после первой ИИ-генерации/переработки.")

        with st.expander("2 — Озвучка (Audio)", expanded=False):
            b_play, b_act = st.columns([3, 1])
            with b_play:
                for name, label in (("voiceover.mp3", "EN"), ("voiceover_ru.mp3", "RU")):
                    audio = episode_dir / name
                    if audio.is_file():
                        st.markdown(f"**{label}**")
                        st.audio(audio.read_bytes(), format="audio/mp3")
                    else:
                        st.caption(f"{label}: файла нет ({name})")
            with b_act:
                if st.button(
                    "🎙️ Сгенерировать / Обновить озвучку (EN + RU)",
                    key=f"voice_{ep_key}",
                    use_container_width=True,
                ):
                    launch_background_job("generation", episode_dir, "generate_voice.py")

    with st.expander("3 — Кадры и обложка (Visuals)", expanded=False):
        images_dir = episode_dir / "images"
        images: list[Path] = []
        image_names: list[str] = []
        if images_dir.is_dir():
            images = sorted(
                path for path in images_dir.iterdir() if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES
            )
            image_names = [path.name for path in images]
        if images:
            review = load_review(episode_dir)
            flagged_on_disk = disk_names_for_flagged(review, image_names)
            inject_flagged_image_css(flagged_on_disk, ep_key, image_names)
            st.caption("Проблемные кадры — красная обводка; статус **open** / **ok**")
            columns = st.columns(min(4, len(images)))
            for index, image_path in enumerate(images):
                name = image_path.name
                is_flagged = is_image_path_flagged(review, name)
                status = "⚑ open" if is_flagged else "ok"
                stem = stem_from_filename(name)
                anim_on = is_animate_frame(review, stem)
                anim_label = "🎞️ anim" if anim_on else " "
                with columns[index % len(columns)]:
                    with st.container(border=True, key=image_cell_key(ep_key, name)):
                        mp4_path = images_dir / f"{stem}.mp4"
                        if mp4_path.is_file():
                            st.video(str(mp4_path))
                        else:
                            st.image(str(image_path), width="stretch")
                        st.caption(f"{status} · {anim_label}")
                        b_flag, b_anim, b_open = st.columns(3)
                        flag_help = "Снять пометку" if is_flagged else "Пометить как проблему"
                        if b_flag.button("⚑", key=f"img_flag_{ep_key}_{name}", help=flag_help, use_container_width=True):
                            review = load_review(episode_dir)
                            toggle_problem_image(review, name)
                            save_review(episode_dir, review)
                            st.rerun()
                        anim_help = "Снять с анимации" if anim_on else "Анимировать через ComfyUI"
                        if b_anim.button("🎞️", key=f"img_anim_{ep_key}_{name}", help=anim_help, use_container_width=True):
                            review = load_review(episode_dir)
                            toggle_animate_frame(review, stem)
                            save_review(episode_dir, review)
                            st.rerun()
                        b_open.link_button(
                            "⛶",
                            str(image_path.resolve().as_uri()),
                            help="Открыть в полном размере",
                            use_container_width=True,
                        )
            with st.expander("Пометить несколько кадров одной проблемой"):
                default_pick = sorted(flagged_on_disk) if flagged_on_disk else image_names[:1]
                with st.form(key=f"bulk_form_{ep_key}", clear_on_submit=False):
                    bulk_pick = st.multiselect("Кадры", image_names, default=default_pick)
                    bulk_note = st.text_area("Комментарий для PROMPT при регенерации", height=80)
                    submitted = st.form_submit_button("Применить")
                if submitted and bulk_pick:
                    review = load_review(episode_dir)
                    flag_images_batch(review, bulk_pick, bulk_note)
                    save_review(episode_dir, review)
                    st.rerun()
        else:
            st.caption("Папка images/ пуста — запустите генерацию кадров.")

        thumb = episode_dir / "thumbnail.jpg"
        if thumb.is_file():
            st.markdown("**Обложка**")
            st.image(str(thumb), width=360)

        c1, c2, c3, c4 = st.columns(4)
        if c1.button("🖼️ Сгенерировать недостающие кадры", key=f"img_miss_{ep_key}"):
            launch_background_job("generation", episode_dir, "generate_images.py")
        flagged = disk_names_for_flagged(load_review(episode_dir), image_names)
        if c2.button("🔄 Перегенерировать только помеченные", key=f"img_flagged_{ep_key}"):
            if not flagged:
                st.warning("Нет помеченных кадров.")
            else:
                launch_background_job("regeneration", episode_dir, "generate_images.py", "--flagged-only")
        if c3.button("🎨 Обновить обложку", key=f"thumb_{ep_key}"):
            launch_background_job("generation", episode_dir, "generate_thumbnail.py")
        with c4:
            st.caption("Оживление / сборка — блок **4**")

    frame_stems = frame_stem_options
    staged_build = episode_staged_video_build(episode_dir)

    with st.expander("4 — Сюжет (видео + звук)", expanded=False):
        from story_build_controls import render_story_build_block

        render_story_build_block(
            episode_dir,
            ep_key,
            launch_job=launch_background_job,
            persist_config=persist_episode_config_before_job,
            channel_slug=channel_slug,
            channel_dir=ch,
            render_animation=render_animate_i2v_panel,
            frame_stem_options=frame_stems,
        )
        if not staged_build:
            from animation_settings import load_episode_animation, resolve_video_build_script

            build_script = resolve_video_build_script(load_episode_animation(episode_dir))
            st.caption(f"Короткая сборка (без плейлиста): `{build_script}`.")
            if st.button("🎞️ Собрать / Пересобрать видео", key=f"build_vid_{ep_key}", use_container_width=True):
                epi_save = load_episode_config(episode_dir, channel_slug, ch)
                persist_episode_config_before_job(episode_dir, ep_key, epi_save)
                launch_background_job("build_video", episode_dir, build_script)

    with st.expander("5 — Хвост (картинка + звук)", expanded=False):
        if staged_build:
            from tail_build_controls import render_tail_build_block

            render_tail_build_block(
                episode_dir,
                ep_key,
                launch_job=launch_background_job,
                channel_slug=channel_slug,
            )
        else:
            from tail_controls import render_ambient_tail_controls

            render_ambient_tail_controls(episode_dir, channel_slug, ep_key)

    with st.expander("6 — Финал и просмотр", expanded=False):
        if staged_build:
            from final_mux_controls import render_final_mux_block

            render_final_mux_block(
                episode_dir,
                ep_key,
                launch_job=launch_background_job,
                persist_config=persist_episode_config_before_job,
                channel_slug=channel_slug,
                channel_dir=ch,
            )
        video = episode_dir / "final_video.mp4"
        d_v, d_btn = st.columns([3, 1])
        with d_v:
            if video.is_file():
                st.video(str(video))
            else:
                st.caption("final_video.mp4 ещё нет.")
            marker_event = video_marker(key=f"video_marker_{ep_key}")
            if isinstance(marker_event, dict) and marker_event.get("action") == "save_segment":
                sig = (
                    round(float(marker_event.get("start", 0)), 2),
                    round(float(marker_event.get("end", 0)), 2),
                    str(marker_event.get("note", "")).strip(),
                )
                dedupe_key = f"last_video_seg_{ep_key}"
                if st.session_state.get(dedupe_key) != sig:
                    st.session_state[dedupe_key] = sig
                    review = load_review(episode_dir)
                    segments = review.get("video_segments", [])
                    segments.append({"start": sig[0], "end": sig[1], "note": sig[2]})
                    review["video_segments"] = segments
                    save_review(episode_dir, review)
                    st.toast("Участок сохранён")
                    st.rerun()

            review = load_review(episode_dir)
            st.markdown("**Проблемные участки**")
            segments = review.get("video_segments", [])
            if segments:
                for index, segment in enumerate(segments):
                    start = float(segment.get("start", 0))
                    end = float(segment.get("end", 0))
                    c_time, c_note, c_save, c_del = st.columns([2, 4, 1, 1])
                    c_time.markdown(
                        f"**{index + 1}.** {format_media_time(start)} — {format_media_time(end)}"
                    )
                    note_val = c_note.text_input(
                        "Комментарий",
                        value=str(segment.get("note", "")),
                        key=f"seg_note_{ep_key}_{index}",
                        label_visibility="collapsed",
                    )
                    if c_save.button("💾", key=f"seg_save_{ep_key}_{index}", use_container_width=True):
                        update_video_segment_note(review, index, note_val)
                        save_review(episode_dir, review)
                        st.rerun()
                    if c_del.button("✕", key=f"seg_del_{ep_key}_{index}", use_container_width=True):
                        remove_video_segment(review, index)
                        save_review(episode_dir, review)
                        st.rerun()
            else:
                st.caption("Отметьте участки в плеере или добавьте вручную ниже.")
            with st.expander("Добавить участок вручную (таймкод)", expanded=False):
                with st.form(f"video_mark_form_{ep_key}"):
                    start = st.text_input("Начало (сек или ММ:СС)", value="0:00")
                    end = st.text_input("Конец (сек или ММ:СС)", value="0:30")
                    note = st.text_input("Комментарий")
                    add = st.form_submit_button("Добавить участок")
                if add:
                    try:
                        review = load_review(episode_dir)
                        segments = review.get("video_segments", [])
                        segments.append(
                            {
                                "start": round(parse_timecode(start), 2),
                                "end": round(parse_timecode(end), 2),
                                "note": note.strip(),
                            }
                        )
                        review["video_segments"] = segments
                        save_review(episode_dir, review)
                        st.rerun()
                    except ValueError as exc:
                        st.error(str(exc))

        with d_btn:
            if st.button(
                "🚀 Применить правки по участкам и пересобрать",
                key=f"apply_review_{ep_key}",
                use_container_width=True,
            ):
                launch_background_job("regeneration", episode_dir, "apply_review.py")
            if st.button("🎯 Участки → пометить кадры", key=f"map_seg_{ep_key}", use_container_width=True):
                added, updated = apply_video_segments_to_images(episode_dir)
                if added or updated:
                    st.success(f"Пометок: +{added}, обновлено: {updated}")
                    st.rerun()
                else:
                    st.info("Нет участков с комментариями.")

        with st.expander("📋 История фоновых задач"):
            render_jobs_monitor(episode_dir, ep_key, auto_refresh=False)

    if pin_running:
        import time

        time.sleep(2)
        st.rerun()


def page_radar(channel_slug: str) -> None:
    st.header("Радар и публикация")
    MARKET_DIR.mkdir(exist_ok=True)
    reports = sorted(MARKET_DIR.glob("new_niches_*.csv"), reverse=True)
    if st.button("Запустить discover_niches.py"):
        with st.spinner("Поиск ниш..."):
            show_process_result(run_script("discover_niches.py", timeout=3600), "discover_niches.py")
        st.rerun()
    if reports:
        choice = st.selectbox("Отчёт", reports, format_func=lambda p: p.name)
        st.dataframe(pd.read_csv(choice, encoding="utf-8-sig"), use_container_width=True)
    else:
        st.caption("В `market_radar/` пока нет отчётов.")

    st.header("📤 Автопубликация (заглушка)")
    st.caption("OAuth для YouTube API: загрузите client_secrets.json в папку канала.")
    if channel_slug:
        ch = channel_dir(channel_slug)
        secrets_path = ch / "client_secrets.json"
        uploaded = st.file_uploader("client_secrets.json", type=["json"])
        if uploaded is not None:
            secrets_path.write_bytes(uploaded.getvalue())
            st.success(f"Сохранено: {secrets_path.relative_to(ROOT)}")
        st.text_input("Время публикации (EST)", value="18:00", disabled=False)
        connected = secrets_path.is_file()
        st.metric("Статус OAuth", "Подключено (файл найден)" if connected else "Не подключено")

        episode_by_name = {path.name: path for path in list_episode_dirs(ch)}
        episode_names = sorted(episode_by_name)
        if episode_names:
            meta_episode = st.selectbox("Эпизод для копирования метаданных", episode_names, key="meta_copy")
            meta_path = episode_by_name[meta_episode] / "metadata.md"
            if meta_path.is_file():
                meta = meta_path.read_text(encoding="utf-8-sig")
                en_block = extract_section(meta, "English Metadata") or meta
                ru_block = extract_section(meta, "Russian Metadata")
                st.text_area("English Metadata", en_block, height=180)
                if ru_block:
                    st.text_area("Russian Metadata", ru_block, height=180)
                st.caption("Скопируйте текст вручную или выделите из полей выше для YouTube Studio.")


def main() -> None:
    st.set_page_config(page_title="YouTube Panel", layout="wide")
    channels = list_channels()
    if "nav_section" not in st.session_state:
        st.session_state.nav_section = "analytics"
    read_nav_from_url(channels)
    section = sidebar()
    write_nav_to_url()
    active_jobs = render_global_jobs_toolbar()

    if section == "new_channel":
        page_new_channel()
        maybe_auto_refresh_jobs(active_jobs)
        return

    channel_slug = render_channel_tabs(channels)

    if section == "analytics":
        page_analytics(channel_slug)
    elif section == "studio":
        page_studio(channel_slug)
    elif section == "channel_settings":
        page_channel_settings(channel_slug)
    elif section == "capacity":
        page_capacity(channel_slug)
    elif section == "jobs":
        page_jobs(channel_slug)
    else:
        page_radar(channel_slug)

    maybe_auto_refresh_jobs(active_jobs)


if __name__ == "__main__":
    main()