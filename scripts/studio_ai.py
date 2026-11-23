"""Cloudflare Workers AI: идеи бэклога и пакет файлов эпизода для студии."""

from __future__ import annotations

import ast
import json
import os
import re
from difflib import SequenceMatcher
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
import requests
from dotenv import load_dotenv
from playlist_manager import (
    BACKLOG_COLUMNS,
    BIBLE_SECTION_KEYS,
    STANDALONE_ID,
    append_recap_block,
    backlog_row_index,
    compile_series_bible_md,
    create_playlist,
    episode_dir_for_backlog_id,
    extract_recap_section,
    load_series_bible_json,
    backlog_row_for_episode_dir,
    next_series_episode_from_folders,
    prior_series_episode_lines,
    read_episode_idea,
    write_episode_idea,
    next_standalone_episode_id,
    normalize_backlog_frame,
    normalize_series_bible_data,
    playlist_by_id,
    SERIES_EP_ID_RE,
    load_series_bible_json,
    format_series_bible_context,
    format_series_bible_context_data,
    format_series_bible_episode_tone_data,
    playlist_dir_for_series,
    read_producer_brief,
    read_series_bible,
    relocate_episode_folder,
    save_producer_brief,
    save_series_bible_data,
    row_can_expand_to_series,
    series_bible_path,
    write_series_bible,
)
from playlist_manager import backlog_row_by_episode_id as pm_backlog_row

from ai_router import generate_text, load_ai_settings, unload_ollama_model

ROOT = Path(__file__).resolve().parents[1]
SLEEP_CHANNEL_SLUG = "channel_02_sleep_history"
PROMPT_LINE_RE = re.compile(r"^\s*PROMPT\s*:", re.IGNORECASE)
WORD_RE = re.compile(r"\w+", flags=re.UNICODE)

FILE_SCRIPT = "script.md"
FILE_SCRIPT_RU = "script_ru.md"
FILE_VISUAL = "visual_prompts.md"
FILE_METADATA = "metadata.md"
EPISODE_CONFIG_JSON = "episode_config.json"
PLOT_BRIEF_TXT = "plot_brief.txt"
DEFAULT_MINUTES: dict[str, tuple[float, float]] = {
    "channel_02_sleep_history": (15, 20),
    "channel_03_enchanted_realms": (20, 25),
    "channel_01_gaming_analysis": (8, 12),
}
DEFAULT_WPM: dict[str, int] = {
    "channel_02_sleep_history": 125,
    "channel_03_enchanted_realms": 145,
    "channel_01_gaming_analysis": 145,
}
WORDS_PER_CHAPTER = 500
SECONDS_PER_FRAME = 40


def _load_env() -> None:
    load_dotenv(ROOT / ".env")


def cf_chat(messages: list[dict[str, str]], *, max_tokens: int = 8192, temperature: float = 0.45) -> tuple[str, str | None]:
    """Обратная совместимость: все LLM-вызовы идут через ai_router."""
    return generate_text(messages, temperature=temperature, max_tokens=max_tokens)


def _ollama_in_text_stack() -> bool:
    settings = load_ai_settings()
    if str(settings.get("text_provider")) == "ollama":
        return True
    if not settings.get("text_fallback_on_quota", True):
        return False
    chain = settings.get("text_fallback_chain") or []
    return "ollama" in chain


def _llm_prompt_caps() -> dict[str, int]:
    """Сжимаем промпт, если Ollama среди текстовых провайдеров (узкое окно контекста)."""
    if not _ollama_in_text_stack():
        return {
            "series_bible": 12000,
            "producer": 4500,
            "series_outline": 7000,
            "series_chapter": 12000,
            "plot_outline": 6000,
            "plot_chapter": 4000,
            "config_outline": 6000,
            "config_chapter": 8000,
        }
    _load_env()
    return {
        "series_bible": int(os.getenv("OLLAMA_PROMPT_SERIES_CHARS", "3500")),
        "producer": int(os.getenv("OLLAMA_PROMPT_PRODUCER_CHARS", "2000")),
        "series_outline": int(os.getenv("OLLAMA_PROMPT_SERIES_OUTLINE_CHARS", "3000")),
        "series_chapter": int(os.getenv("OLLAMA_PROMPT_SERIES_CHAPTER_CHARS", "3000")),
        "plot_outline": int(os.getenv("OLLAMA_PROMPT_PLOT_OUTLINE_CHARS", "2000")),
        "plot_chapter": int(os.getenv("OLLAMA_PROMPT_PLOT_CHAPTER_CHARS", "2000")),
        "config_outline": int(os.getenv("OLLAMA_PROMPT_CONFIG_OUTLINE_CHARS", "2500")),
        "config_chapter": int(os.getenv("OLLAMA_PROMPT_CONFIG_CHAPTER_CHARS", "2500")),
    }


def _strip_fence(text: str) -> str:
    fence = re.search(r"```(?:json|markdown|md)?\s*([\s\S]*?)```", text)
    if fence:
        return fence.group(1).strip()
    return text.strip()


def _is_idea_row(obj: dict[str, Any]) -> bool:
    return bool(
        obj.get("Topic")
        or obj.get("topic")
        or obj.get("title")
        or obj.get("Title")
    )


def _coerce_idea_rows(parsed: Any) -> list[dict[str, Any]]:
    if isinstance(parsed, dict):
        for key in ("ideas", "items", "topics", "results", "data"):
            if key in parsed and isinstance(parsed[key], list):
                parsed = parsed[key]
                break
        if isinstance(parsed, dict) and _is_idea_row(parsed):
            return [parsed]
    if not isinstance(parsed, list):
        return []
    return [x for x in parsed if isinstance(x, dict) and _is_idea_row(x)]


def _normalize_idea_row(item: dict[str, Any]) -> dict[str, str]:
    topic = (
        item.get("Topic")
        or item.get("topic")
        or item.get("Title")
        or item.get("title")
        or ""
    )
    topic_ru = (
        item.get("Topic_RU")
        or item.get("topic_ru")
        or item.get("TopicRu")
        or item.get("title_ru")
        or item.get("titleRu")
        or ""
    )
    angle = (
        item.get("Angle")
        or item.get("angle")
        or item.get("hook")
        or item.get("Hook")
        or item.get("description")
        or ""
    )
    series = (
        item.get("Series")
        or item.get("series")
        or item.get("Series_Title")
        or item.get("series_title")
        or ""
    )
    series_ru = (
        item.get("Series_RU")
        or item.get("series_ru")
        or item.get("SeriesRu")
        or ""
    )
    return {
        "Topic": str(topic).strip(),
        "Topic_RU": str(topic_ru).strip(),
        "Series": str(series).strip(),
        "Series_RU": str(series_ru).strip(),
        "Angle": str(angle).strip(),
    }


def _extract_json_objects(text: str) -> list[dict[str, Any]]:
    decoder = json.JSONDecoder()
    idx = 0
    length = len(text)
    out: list[dict[str, Any]] = []
    while idx < length:
        while idx < length and text[idx] in " \n\r\t,":
            idx += 1
        if idx >= length:
            break
        if text[idx] not in "{[":
            next_brace = text.find("{", idx)
            if next_brace < 0:
                break
            idx = next_brace
        try:
            parsed, end = decoder.raw_decode(text, idx)
            if isinstance(parsed, dict) and _is_idea_row(parsed):
                out.append(_normalize_idea_row(parsed))
            elif isinstance(parsed, list):
                for row in parsed:
                    if isinstance(row, dict) and _is_idea_row(row):
                        out.append(_normalize_idea_row(row))
            idx = end
        except json.JSONDecodeError:
            next_brace = text.find("{", idx + 1)
            if next_brace < 0:
                break
            idx = next_brace
    return out


def _try_literal_object(text: str, start: int) -> tuple[dict[str, Any] | None, int]:
    """Один dict с одинарными кавычками (Python literal) — в т.ч. обрезанный массив."""
    if start >= len(text) or text[start] != "{":
        return None, start + 1
    for end in range(start + 2, len(text) + 1):
        if text[end - 1] != "}":
            continue
        chunk = text[start:end]
        if chunk.count("{") != chunk.count("}"):
            continue
        try:
            val = ast.literal_eval(chunk)
            if isinstance(val, dict):
                return val, end
        except (SyntaxError, ValueError):
            continue
    return None, start + 1


def _parse_python_literal_ideas(text: str) -> list[dict[str, str]]:
    """Llama часто шлёт [{'Topic': '...'}, ...] — это не JSON, но literal_eval."""
    start = text.find("[")
    if start >= 0:
        end = text.rfind("]")
        if end > start:
            try:
                val = ast.literal_eval(text[start : end + 1])
                rows = _coerce_idea_rows(val)
                if rows:
                    return [_normalize_idea_row(r) for r in rows]
            except (SyntaxError, ValueError):
                pass

    out: list[dict[str, str]] = []
    idx = 0
    seen_topics: set[str] = set()
    while idx < len(text):
        pos = text.find("{", idx)
        if pos < 0:
            break
        obj, nxt = _try_literal_object(text, pos)
        if obj and _is_idea_row(obj):
            row = _normalize_idea_row(obj)
            key = row["Topic"].lower()
            if key and key not in seen_topics:
                seen_topics.add(key)
                out.append(row)
            idx = nxt
        else:
            idx = pos + 1
    return out


def _parse_idea_rows(text: str) -> list[dict[str, str]]:
    text = _strip_fence(text).strip()
    if not text:
        return []

    try:
        rows = _coerce_idea_rows(json.loads(text))
        if rows:
            return [_normalize_idea_row(r) for r in rows]
    except json.JSONDecodeError:
        pass

    start = text.find("[")
    end = text.rfind("]")
    if start >= 0 and end > start:
        try:
            rows = _coerce_idea_rows(json.loads(text[start : end + 1]))
            if rows:
                return [_normalize_idea_row(r) for r in rows]
        except json.JSONDecodeError:
            pass

    py_rows = _parse_python_literal_ideas(text)
    if py_rows:
        return py_rows

    objects = _extract_json_objects(text)
    if objects:
        return objects

    if text.startswith("{"):
        try:
            rows = _coerce_idea_rows(json.loads(text))
            if rows:
                return [_normalize_idea_row(r) for r in rows]
        except json.JSONDecodeError:
            pass

    return []


def _preference_block(keywords: str, brief: str) -> str:
    keywords = keywords.strip()
    brief = brief.strip()
    if not keywords and not brief:
        return ""
    parts = [
        "CREATOR PREFERENCES (may be in Russian — interpret and apply to every idea):",
    ]
    if keywords:
        parts.append(f"Keywords / themes: {keywords}")
    if brief:
        parts.append(f"Creative brief: {brief}")
    parts.append(
        "Every Topic must clearly reflect these preferences. "
        "Do not ignore them when present."
    )
    return "\n".join(parts)


STANDALONE_IDEA_RULES = (
    "STANDALONE (1 episode each): every idea is a fully independent magical/fantasy sleep story — "
    "a NEW protagonist and NEW setting per idea. No cliffhangers, no 'to be continued', "
    "no references to other backlog topics."
)


def _idea_messages(
    config_excerpt: str,
    forbidden: str,
    count: int,
    *,
    compact: bool,
    keywords: str = "",
    brief: str = "",
    standalone: bool = True,
) -> list[dict[str, str]]:
    prefs = _preference_block(keywords, brief)
    prefs_block = f"\n\n{prefs}" if prefs else ""
    ru_rules = (
        "Topic_RU is mandatory: natural Russian title for the producer (not a copy of Topic, not English)."
    )
    mode_block = f"\n\n{STANDALONE_IDEA_RULES}\n" if standalone else ""
    if compact:
        system = (
            "Output ONLY a minified JSON array. First char [, last char ]. "
            f"Exactly {count} objects. Keys: Topic, Topic_RU, Angle. "
            f"{ru_rules} "
            "No markdown, no text outside JSON."
        )
        user = (
            f"Channel niche (sleep-friendly faceless YouTube, English titles):\n"
            f"{config_excerpt[:2800]}\n\n"
            f"{mode_block}"
            f"Do NOT repeat these topics:\n{forbidden or '(none)'}"
            f"{prefs_block}\n\n"
            f'Example shape: [{{"Topic":"...","Topic_RU":"...","Angle":"..."}}]\n'
            f"Generate {count} new ideas now."
        )
    else:
        system = (
            "You are a YouTube backlog producer for a faceless channel. "
            "Output MUST be exactly ONE JSON array starting with [ and ending with ]. "
            "No markdown fences, no prose, no single object only. "
            f"Exactly {count} objects. Use strict JSON: double quotes only, not Python. "
            'Each object: "Topic" (English YouTube title), '
            f'"Topic_RU" (Russian translation/title for producer — required, {ru_rules}), '
            '"Angle" (one English line, unique hook).'
        )
        user = (
            f"Channel config:\n{config_excerpt}\n\n"
            f"{mode_block}"
            f"FORBIDDEN (already in backlog or published):\n{forbidden or '(none)'}"
            f"{prefs_block}\n\n"
            f"Return only the JSON array of {count} fresh ideas."
        )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _load_backlog(channel_dir: Path) -> pd.DataFrame:
    path = channel_dir / "backlog.csv"
    if not path.is_file():
        return pd.DataFrame(columns=BACKLOG_COLUMNS)
    return normalize_backlog_frame(pd.read_csv(path, encoding="utf-8-sig"))


def _save_backlog(channel_dir: Path, frame: pd.DataFrame) -> None:
    path = channel_dir / "backlog.csv"
    frame = frame.reindex(columns=BACKLOG_COLUMNS, fill_value="")
    frame.to_csv(path, index=False, encoding="utf-8-sig")


def _topic_set(channel_dir: Path) -> set[str]:
    topics: set[str] = set()
    for name in ("backlog.csv", "published.csv"):
        path = channel_dir / name
        if not path.is_file():
            continue
        frame = pd.read_csv(path, encoding="utf-8-sig")
        for col in ("Topic", "Topic_RU"):
            if col in frame.columns:
                for value in frame[col].astype(str):
                    text = value.strip().lower()
                    if text and text != "nan":
                        topics.add(text)
    episodes = channel_dir / "episodes"
    if episodes.is_dir():
        for ep_dir in episodes.iterdir():
            if not ep_dir.is_dir():
                continue
            idea = read_episode_idea(ep_dir)
            for key in ("topic", "topic_ru"):
                text = str(idea.get(key, "")).strip().lower()
                if text:
                    topics.add(text)
    return topics


def _expected_views(channel_dir: Path) -> str:
    path = channel_dir / "published.csv"
    if not path.is_file():
        return "1000-5000 (Тест)"
    frame = pd.read_csv(path, encoding="utf-8-sig")
    if "Views_7d" not in frame.columns:
        return "1000-5000 (Тест)"
    values = pd.to_numeric(frame["Views_7d"], errors="coerce").dropna()
    if values.empty:
        return "1000-5000 (Тест)"
    med = int(statistics.median(values))
    return f"{med} (медиана {len(values)} рол.)"


def _next_backlog_id(backlog: pd.DataFrame, channel_slug: str) -> str:
    if channel_slug.startswith("channel_02"):
        prefix = "SH"
    elif channel_slug.startswith("channel_01"):
        prefix = "GM"
    else:
        prefix = "EP"
    numbers: list[int] = []
    for value in backlog.get("ID", pd.Series(dtype=str)).astype(str):
        match = re.fullmatch(rf"{prefix}(\d+)", value.strip(), flags=re.IGNORECASE)
        if match:
            numbers.append(int(match.group(1)))
    n = max(numbers, default=0) + 1
    return f"{prefix}{n:02d}"


def _channel_config(channel_dir: Path) -> str:
    path = channel_dir / "channel_config.md"
    if path.is_file():
        return path.read_text(encoding="utf-8-sig")
    return ""


def generate_backlog_ideas(
    channel_dir: Path,
    count: int = 5,
    *,
    preference_keywords: str = "",
    preference_brief: str = "",
) -> tuple[int, str | None]:
    """Сгенерировать идеи и дописать backlog.csv. Возвращает (число добавленных, ошибка)."""
    try:
        return _generate_backlog_ideas_inner(
            channel_dir,
            count,
            preference_keywords=preference_keywords,
            preference_brief=preference_brief,
        )
    finally:
        if str(load_ai_settings().get("text_provider")) == "ollama":
            unload_ollama_model()


def _generate_backlog_ideas_inner(
    channel_dir: Path,
    count: int,
    *,
    preference_keywords: str = "",
    preference_brief: str = "",
) -> tuple[int, str | None]:
    channel_slug = channel_dir.name
    backlog = _load_backlog(channel_dir)
    taken = _topic_set(channel_dir)
    expected = _expected_views(channel_dir)
    config = _channel_config(channel_dir)
    taken_sample = "\n".join(f"- {t[:120]}" for t in sorted(taken)[:40])
    config_excerpt = config[:6500] if config else "(no channel_config.md)"

    raw = ""
    rows: list[dict[str, str]] = []
    for _attempt, compact in enumerate((False, True)):
        messages = _idea_messages(
            config_excerpt,
            taken_sample,
            count,
            compact=compact,
            keywords=preference_keywords,
            brief=preference_brief,
        )
        raw, err = cf_chat(messages, temperature=0.35 if compact else 0.45)
        if err:
            return 0, err
        rows = _parse_idea_rows(raw)
        if rows:
            break

    if not rows:
        snippet = (raw or "").replace("\n", " ")[:320]
        return 0, (
            "Не удалось разобрать JSON идей (нужен массив с Topic / Topic_RU / Angle). "
            f"Начало ответа модели: {snippet!r}"
        )[:520]

    added = 0
    for item in rows:
        topic = item.get("Topic", "").strip()
        if not topic:
            continue
        key = topic.lower()
        if key in taken:
            continue
        topic_ru = item.get("Topic_RU", "").strip()
        if not topic_ru or topic_ru.lower() == topic.lower():
            continue
        angle = item.get("Angle", "").strip() or "AI suggestion"
        new_id = next_standalone_episode_id(backlog)
        backlog.loc[len(backlog)] = {
            "ID": new_id,
            "Topic": topic,
            "Topic_RU": topic_ru,
            "Series_ID": STANDALONE_ID,
            "Episode_Num": "1",
            "Series": "",
            "Series_RU": "",
            "Angle": angle,
            "Competitor_Ref": "",
            "Competitor_Views": "",
            "Outlier_Score": "",
            "Expected_Views": expected,
            "Status": "Idea",
            "Priority": "P1",
        }
        taken.add(key)
        added += 1
        if added >= count:
            break

    if added == 0:
        return 0, (
            "Не добавлено ни одной темы: дубли в бэклоге/публикациях, пустой Topic "
            "или отсутствует Topic_RU (нужен осмысленный русский перевод, не копия Topic)."
        )
    _save_backlog(channel_dir, backlog)
    return added, None


def _has_cyrillic(text: str) -> bool:
    return bool(re.search(r"[\u0400-\u04FF]", text))


def _series_messages(
    config_excerpt: str,
    forbidden: str,
    count: int,
    *,
    compact: bool,
    series_en: str,
    series_ru: str,
    keywords: str = "",
    brief: str = "",
) -> list[dict[str, str]]:
    prefs = _preference_block(keywords, brief)
    prefs_block = f"\n\n{prefs}" if prefs else ""
    ru_rules = (
        "Topic_RU is mandatory: natural Russian title for the producer (not a copy of Topic, not English)."
    )
    series_block = (
        f'Series umbrella title (English, same on every row): "{series_en}".\n'
        f'Series_RU (Russian, same on every row): "{series_ru}".\n'
        f"Plan exactly {count} episodes forming one coherent series arc. "
        "Each episode must work as a standalone sleep video but has clear order. "
        'In Angle, start with "Part X/N —" (N = episode count).'
    )
    keys = "Topic, Topic_RU, Series, Series_RU, Angle"
    if compact:
        system = (
            "Output ONLY a minified JSON array. First char [, last char ]. "
            f"Exactly {count} objects. Keys: {keys}. "
            f"{ru_rules} "
            "No markdown, no text outside JSON."
        )
        user = (
            f"Channel niche (sleep-friendly faceless YouTube, English titles):\n"
            f"{config_excerpt[:2800]}\n\n"
            f"{series_block}\n\n"
            f"Do NOT repeat these topics:\n{forbidden or '(none)'}"
            f"{prefs_block}\n\n"
            f'Example: [{{"Topic":"...","Topic_RU":"...","Series":"...","Series_RU":"...","Angle":"Part 1/{count} — ..."}}]\n'
            f"Generate {count} series episodes now."
        )
    else:
        system = (
            "You are a YouTube backlog producer for a faceless channel. "
            "Output MUST be exactly ONE JSON array starting with [ and ending with ]. "
            "No markdown fences, no prose. "
            f"Exactly {count} objects. Strict JSON, double quotes only. "
            f'Each object keys: {keys}. {ru_rules}'
        )
        user = (
            f"Channel config:\n{config_excerpt}\n\n"
            f"{series_block}\n\n"
            f"FORBIDDEN (already in backlog or published):\n{forbidden or '(none)'}"
            f"{prefs_block}\n\n"
            f"Return only the JSON array of {count} series episodes."
        )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _append_series_rows(
    channel_dir: Path,
    backlog: pd.DataFrame,
    *,
    series_id: str,
    series_en: str,
    series_ru: str,
    rows: list[dict[str, str]],
    episode_count: int,
    taken: set[str],
    expected: str,
) -> int:
    added = 0
    for idx, item in enumerate(rows):
        topic = item.get("Topic", "").strip()
        if not topic:
            continue
        key = topic.lower()
        if key in taken:
            continue
        topic_ru = item.get("Topic_RU", "").strip()
        if not topic_ru or topic_ru.lower() == topic.lower():
            continue
        new_id, ep_num = next_series_episode_id(backlog, series_id)
        angle = (
            item.get("Angle", "").strip()
            or f"Part {ep_num} — continuing the serial"
        )
        priority = "P0" if added == 0 else "P1"
        backlog.loc[len(backlog)] = {
            "ID": new_id,
            "Topic": topic,
            "Topic_RU": topic_ru,
            "Series_ID": series_id,
            "Episode_Num": str(ep_num),
            "Series": series_en,
            "Series_RU": series_ru,
            "Angle": angle,
            "Competitor_Ref": "",
            "Competitor_Views": "",
            "Outlier_Score": "",
            "Expected_Views": expected,
            "Status": "Idea",
            "Priority": priority,
        }
        taken.add(key)
        added += 1
        if added >= episode_count:
            break
    return added


def generate_series_backlog_ideas(
    channel_dir: Path,
    *,
    series_title: str,
    episode_count: int = 1,
    preference_keywords: str = "",
    preference_brief: str = "",
) -> tuple[int, str | None]:
    """Создать плейлист (S01) и один пилотный выпуск S01E01 в backlog."""
    series_title = series_title.strip()
    if not series_title:
        return 0, "Укажите название плейлиста."
    episode_count = 1
    try:
        playlist = create_playlist(channel_dir, title=series_title)
    except ValueError as exc:
        return 0, str(exc)

    series_id = str(playlist["id"]).strip()
    series_en = str(playlist.get("title_en", series_title)).strip()
    series_ru = str(playlist.get("title_ru", series_title)).strip()

    backlog = _load_backlog(channel_dir)
    taken = _topic_set(channel_dir)
    expected = _expected_views(channel_dir)
    config = _channel_config(channel_dir)
    taken_sample = "\n".join(f"- {t[:120]}" for t in sorted(taken)[:40])
    config_excerpt = config[:6500] if config else "(no channel_config.md)"

    raw = ""
    rows: list[dict[str, str]] = []
    for _attempt, compact in enumerate((False, True)):
        messages = _series_messages(
            config_excerpt,
            taken_sample,
            episode_count,
            compact=compact,
            series_en=series_en,
            series_ru=series_ru,
            keywords=preference_keywords,
            brief=preference_brief,
        )
        raw, err = cf_chat(messages, temperature=0.4 if compact else 0.5)
        if err:
            return 0, err
        rows = _parse_idea_rows(raw)
        if rows:
            break

    if not rows:
        snippet = (raw or "").replace("\n", " ")[:320]
        return 0, (
            "Не удалось разобрать JSON серии (нужен массив с Topic / Topic_RU / Angle). "
            f"Начало ответа: {snippet!r}"
        )[:520]

    added = _append_series_rows(
        channel_dir,
        backlog,
        series_id=series_id,
        series_en=series_en,
        series_ru=series_ru,
        rows=rows,
        episode_count=episode_count,
        taken=taken,
        expected=expected,
    )
    if added == 0:
        return 0, (
            "Не добавлено ни одного выпуска серии: дубли, пустой Topic "
            "или нет нормального Topic_RU."
        )
    _save_backlog(channel_dir, backlog)
    return added, None


def _next_episode_messages(
    config_excerpt: str,
    forbidden: str,
    *,
    compact: bool,
    series_en: str,
    series_ru: str,
    episode_num: int,
    prior_episodes: str,
    bible_context: str = "",
    user_hint: str = "",
    keywords: str = "",
    brief: str = "",
) -> list[dict[str, str]]:
    prefs = _preference_block(keywords, brief)
    prefs_block = f"\n\n{prefs}" if prefs else ""
    hint_block = ""
    if user_hint.strip():
        hint_block = (
            f"\n\nProducer hint for episode {episode_num} (Russian, optional — treat as soft canon):\n"
            f"{user_hint.strip()[:2500]}"
        )
    bible_block = ""
    if bible_context.strip():
        bible_block = (
            f"\n\n--- series_bible (world, characters, lore, full chronicle) ---\n"
            f"{bible_context[:14000]}"
        )
    series_block = (
        f'Series (English): "{series_en}". Series_RU: "{series_ru}".\n'
        f"Propose episode {episode_num} of this continuing serial.\n"
        f"Backlog topics already planned:\n{prior_episodes or '(none)'}\n"
        "Continue organically after the chronicle; same cast and visual lock."
        f"{bible_block}"
        f"{hint_block}"
    )
    keys = "Topic, Topic_RU, Angle"
    if compact:
        system = (
            "Output ONLY one minified JSON object (not array). Keys: "
            f"{keys}. Topic_RU required (Russian, not copy of Topic)."
        )
        user = (
            f"{config_excerpt[:2800]}\n\n{series_block}{prefs_block}\n\n"
            f'Forbidden topics:\n{forbidden or "(none)"}\n'
            f'Return {{"Topic":"...","Topic_RU":"...","Angle":"Part {episode_num} — ..."}}'
        )
    else:
        system = (
            "You are a YouTube backlog producer. Output ONE JSON object only. "
            f"Keys: {keys}. Topic_RU mandatory."
        )
        user = (
            f"Channel config:\n{config_excerpt}\n\n{series_block}{prefs_block}\n\n"
            f"FORBIDDEN:\n{forbidden or '(none)'}"
        )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _parse_single_idea_object(text: str) -> dict[str, str] | None:
    rows = _parse_idea_rows(text)
    if rows:
        return rows[0]
    text = _strip_fence(text).strip()
    if text.startswith("{"):
        try:
            obj = json.loads(text)
            if isinstance(obj, dict):
                return _normalize_idea_row(obj)
        except json.JSONDecodeError:
            pass
    return None


def generate_next_series_episode(
    channel_dir: Path,
    *,
    series_id: str,
    preference_keywords: str = "",
    preference_brief: str = "",
    user_hint: str = "",
) -> tuple[str, str | None]:
    """Следующий выпуск: папка episodes/ + episode_idea.json (backlog не трогаем)."""
    series_id = series_id.strip().upper()
    playlist = playlist_by_id(channel_dir, series_id)
    if not playlist:
        return "", f"Плейлист {series_id} не найден."
    series_en = str(playlist.get("title_en", "")).strip()
    series_ru = str(playlist.get("title_ru", "")).strip()

    taken = _topic_set(channel_dir)
    config = _channel_config(channel_dir)
    taken_sample = "\n".join(f"- {t[:120]}" for t in sorted(taken)[:40])
    config_excerpt = config[:6500] if config else "(no channel_config.md)"
    prior = prior_series_episode_lines(channel_dir, series_id)
    new_id, next_num = next_series_episode_from_folders(channel_dir, series_id)

    pdir = playlist_dir_for_series(channel_dir, series_id)
    bible_context = format_series_bible_context(pdir) if pdir else ""

    item: dict[str, str] | None = None
    for compact in (False, True):
        messages = _next_episode_messages(
            config_excerpt,
            taken_sample,
            compact=compact,
            series_en=series_en,
            series_ru=series_ru,
            episode_num=next_num,
            prior_episodes=prior,
            bible_context=bible_context,
            user_hint=user_hint,
            keywords=preference_keywords,
            brief=preference_brief,
        )
        raw, err = cf_chat(messages, temperature=0.45)
        if err:
            return "", err
        item = _parse_single_idea_object(raw)
        if item:
            break

    if not item:
        return "", "Не удалось разобрать идею следующего выпуска."
    topic = str(item.get("Topic", "")).strip()
    topic_ru = str(item.get("Topic_RU", "")).strip()
    if not topic_ru or topic_ru.lower() == topic.lower():
        return "", "Выпуск не добавлен: нужен Topic_RU."
    if topic.lower() in taken:
        return "", "Выпуск не добавлен: дубль темы."
    angle = (
        str(item.get("Angle", "")).strip()
        or f"Part {next_num} — continuing the serial"
    )
    try:
        folder = relocate_episode_folder(channel_dir, new_id, new_id, topic)
    except OSError as exc:
        return "", f"Папка не создана: {exc}"
    write_episode_idea(
        folder,
        {
            "id": new_id,
            "topic": topic,
            "topic_ru": topic_ru,
            "angle": angle,
            "series_id": series_id,
            "episode_num": str(next_num),
            "series_en": series_en,
            "series_ru": series_ru,
        },
    )
    return new_id, None


_SERIES_BIBLE_JSON_SHAPE = (
    '{"title_en":"...","title_ru":"...","world_and_atmosphere":{"ru":"...","en":"..."},'
    '"characters_visual_lock":{"ru":"...","en":"..."},"lore_and_rules":{"ru":"...","en":"..."},'
    '"recaps":[]}'
)


def _strip_json_fence(text: str) -> str:
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if fence:
        return fence.group(1).strip()
    return text.strip()


def _parse_series_bible_json(raw: str, *, title_en: str, title_ru: str) -> tuple[dict[str, Any], str | None]:
    try:
        data = json.loads(_strip_json_fence(raw))
    except json.JSONDecodeError as exc:
        return {}, f"JSON bible: {exc}"
    if not isinstance(data, dict):
        return {}, "JSON bible: ожидался объект"
    data.setdefault("title_en", title_en)
    data.setdefault("title_ru", title_ru)
    data.setdefault("recaps", [])
    return normalize_series_bible_data(data), None


def _generate_series_bible_data(
    config: str,
    *,
    topic: str,
    topic_ru: str,
    angle: str,
    series_en: str,
    series_ru: str,
    script_seed: str = "",
    creator_brief: str = "",
    existing_recaps: list[dict[str, str]] | None = None,
) -> tuple[dict[str, Any], str | None]:
    seed_block = ""
    if script_seed.strip():
        seed_block = (
            "\n\nExisting narration draft (use as canon for characters and tone):\n"
            f"{script_seed[:12000]}"
        )
    has_brief = bool(creator_brief.strip())
    brief_block = ""
    if has_brief:
        brief_block = (
            "\n\n=== PRODUCER BRIEF (MANDATORY — shape the entire bible from this) ===\n"
            f"{creator_brief.strip()[:8000]}\n"
            "=== END PRODUCER BRIEF ==="
        )
    system = (
        "You write a producer series bible for a calm faceless YouTube serial. "
        "Output ONLY one minified JSON object, no markdown. "
        f"Exact shape: {_SERIES_BIBLE_JSON_SHAPE}. "
        "Each section has parallel Russian (ru) and English (en) prose. "
        "characters_visual_lock: concrete looks for image prompts (names + visual locks). "
        "No full-season arc — only series foundation. recaps must be empty array []."
    )
    if has_brief:
        system += (
            " When PRODUCER BRIEF is present, it overrides generic channel defaults — every section must reflect it."
        )
    user = (
        f"Channel config:\n{config[:7000]}\n\n"
        f"Series title EN: {series_en}\nSeries title RU: {series_ru}\n"
        f"Pilot topic EN: {topic}\nPilot RU: {topic_ru}\n"
        f"Pitch angle: {angle or '—'}\n"
        f"Foundation for an open-ended serial; pilot is episode 1 only — no multi-episode arc text."
        f"{seed_block}"
        f"{brief_block}"
    )
    raw, err = cf_chat(
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        temperature=0.42,
        max_tokens=4096,
    )
    if err:
        return {}, err
    data, parse_err = _parse_series_bible_json(raw, title_en=series_en, title_ru=series_ru)
    if parse_err:
        return {}, parse_err
    if existing_recaps is not None:
        data["recaps"] = existing_recaps
    return data, None


def _generate_series_bible_markdown(
    config: str,
    *,
    topic: str,
    topic_ru: str,
    angle: str,
    series_en: str,
    series_ru: str,
    script_seed: str = "",
    creator_brief: str = "",
) -> tuple[str, str | None]:
    """Legacy: сгенерировать EN-markdown (через JSON + compile)."""
    data, err = _generate_series_bible_data(
        config,
        topic=topic,
        topic_ru=topic_ru,
        angle=angle,
        series_en=series_en,
        series_ru=series_ru,
        script_seed=script_seed,
        creator_brief=creator_brief,
        existing_recaps=[],
    )
    if err:
        return "", err
    return compile_series_bible_md(Path("."), data), None


def migrate_legacy_series_bible_md_to_json(
    playlist_dir: Path,
    md_text: str,
    *,
    title_en: str,
    title_ru: str,
) -> tuple[dict[str, Any], str | None]:
    system = (
        "Convert a series_bible.md into structured JSON. Output ONLY one JSON object. "
        f"Shape: {_SERIES_BIBLE_JSON_SHAPE}. "
        "Map: World and tone → world_and_atmosphere; Characters → characters_visual_lock; "
        "Ongoing threads / Lore / Visual style / Season arc (if present) → lore_and_rules. "
        "Parse ## Recap / ### S01E01 blocks into recaps array with episode, title, summary_en; "
        "if only English recap, copy meaning to summary_ru. Empty recaps → []."
    )
    user = f"Series EN: {title_en}\nSeries RU: {title_ru}\n\n--- markdown ---\n{md_text[:20000]}"
    raw, err = cf_chat(
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        temperature=0.25,
        max_tokens=4096,
    )
    if err:
        return {}, err
    data, parse_err = _parse_series_bible_json(raw, title_en=title_en, title_ru=title_ru)
    return data, parse_err


def sync_and_regenerate_bible(
    playlist_dir: Path,
    updated_ru_fields: dict[str, str],
    *,
    mode: str,
    config_excerpt: str = "",
    producer_brief: str = "",
) -> str | None:
    """
    mode=translate_only | regenerate_around_edits — обновить JSON и пересобрать .md.
    recaps не трогаем.
    """
    mode = (mode or "translate_only").strip()
    data = load_series_bible_json(playlist_dir)
    recaps = list(data.get("recaps") or [])
    for key in BIBLE_SECTION_KEYS:
        if key in updated_ru_fields:
            data.setdefault(key, {"ru": "", "en": ""})
            data[key]["ru"] = str(updated_ru_fields[key]).strip()

    if mode == "translate_only":
        blocks: dict[str, dict[str, str]] = {}
        for key in BIBLE_SECTION_KEYS:
            ru = str(data.get(key, {}).get("ru", "")).strip()
            if not ru:
                blocks[key] = {"ru": "", "en": ""}
                continue
            system = (
                "Translate producer series-bible section RU→EN. "
                "Output ONLY JSON: "
                '{"ru":"...", "en":"..."} — ru unchanged copy, en literary translation.'
            )
            user = f"Section: {key}\n\nRussian:\n{ru[:8000]}"
            raw, err = cf_chat(
                [{"role": "system", "content": system}, {"role": "user", "content": user}],
                temperature=0.2,
                max_tokens=2048,
            )
            if err:
                return err
            try:
                block = json.loads(_strip_json_fence(raw))
            except json.JSONDecodeError:
                return f"Не разобран перевод для {key}"
            data[key] = {
                "ru": ru,
                "en": str(block.get("en", "")).strip(),
            }
        data["recaps"] = recaps
        save_series_bible_data(playlist_dir, data)
        return None

    if mode == "regenerate_around_edits":
        ru_canon = "\n\n".join(
            f"### {key}\n{str(data.get(key, {}).get('ru', '')).strip()}" for key in BIBLE_SECTION_KEYS
        )
        brief_block = f"\n\nProducer brief:\n{producer_brief[:4000]}" if producer_brief.strip() else ""
        system = (
            "You refine a bilingual series bible. Output ONLY JSON with keys: "
            "world_and_atmosphere, characters_visual_lock, lore_and_rules — "
            "each {\"ru\":\"...\",\"en\":\"...\"}. "
            "User's Russian edits are HARD canon; expand and harmonize all sections in RU and EN. "
            "Do not output recaps."
        )
        user = (
            f"Channel config:\n{config_excerpt[:6000]}\n\n"
            f"Series EN: {data.get('title_en','')}\nSeries RU: {data.get('title_ru','')}\n"
            f"{brief_block}\n\n--- Russian canon (editable fields) ---\n{ru_canon[:12000]}"
        )
        raw, err = cf_chat(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            temperature=0.38,
            max_tokens=4096,
        )
        if err:
            return err
        try:
            patch = json.loads(_strip_json_fence(raw))
        except json.JSONDecodeError as exc:
            return f"JSON bible regen: {exc}"
        if not isinstance(patch, dict):
            return "JSON bible regen: ожидался объект"
        for key in BIBLE_SECTION_KEYS:
            block = patch.get(key)
            if isinstance(block, dict):
                data[key] = {
                    "ru": str(block.get("ru", data.get(key, {}).get("ru", ""))).strip(),
                    "en": str(block.get("en", "")).strip(),
                }
        data["recaps"] = recaps
        save_series_bible_data(playlist_dir, data)
        return None

    return f"Неизвестный mode: {mode}"


def _pilot_row_for_series(backlog: pd.DataFrame, series_id: str) -> pd.Series | None:
    if backlog.empty:
        return None
    sid = series_id.strip().upper()
    mask = backlog["Series_ID"].astype(str).str.strip().str.upper().eq(sid)
    subset = backlog.loc[mask]
    if subset.empty:
        return None
    subset = subset.copy()
    subset["_ord"] = pd.to_numeric(subset["Episode_Num"], errors="coerce").fillna(99)
    subset = subset.sort_values("_ord")
    row = subset.iloc[0]
    return row


def regenerate_series_bible_from_brief(
    channel_dir: Path,
    series_id: str,
    *,
    creator_brief: str,
    preserve_recap: bool = True,
) -> str | None:
    """Перегенерировать series_bible.md по описанию продюсера. Ошибка — строка, иначе None."""
    series_id = series_id.strip().upper()
    playlist = playlist_by_id(channel_dir, series_id)
    if not playlist:
        return f"Плейлист {series_id} не найден."

    series_en = str(playlist.get("title_en", "")).strip()
    series_ru = str(playlist.get("title_ru", "")).strip()
    backlog = _load_backlog(channel_dir)
    pilot = _pilot_row_for_series(backlog, series_id)
    if pilot is not None:
        topic = str(pilot.get("Topic", series_en)).strip() or series_en
        topic_ru = str(pilot.get("Topic_RU", series_ru)).strip() or series_ru
        angle = str(pilot.get("Angle", "")).strip()
    else:
        topic = series_en or series_id
        topic_ru = series_ru or series_id
        angle = ""

    pdir = playlist_dir_for_series(channel_dir, series_id)
    if pdir is None:
        return f"Папка плейлиста {series_id} не найдена."
    keep_recaps: list[dict[str, str]] = []
    if preserve_recap:
        keep_recaps = list(load_series_bible_json(pdir).get("recaps") or [])

    config = _channel_config(channel_dir)
    brief_text = creator_brief.strip()
    if not brief_text:
        brief_text = read_producer_brief(channel_dir, series_id)
    print(
        f"[series_bible] regen {series_id}: producer brief {len(brief_text)} chars",
        flush=True,
    )
    bible_data, err = _generate_series_bible_data(
        config,
        topic=topic,
        topic_ru=topic_ru,
        angle=angle,
        series_en=series_en,
        series_ru=series_ru,
        creator_brief=brief_text,
        script_seed="",
        existing_recaps=keep_recaps if preserve_recap else [],
    )
    if err:
        return err

    try:
        save_series_bible_data(pdir, bible_data)
        if brief_text:
            save_producer_brief(channel_dir, series_id, brief_text)
        md_path = pdir / "series_bible.md"
        print(
            f"[series_bible] saved {series_id} → {md_path} (json + md)",
            flush=True,
        )
    except OSError as exc:
        return str(exc)
    return None


def _generate_sequel_ideas(
    config_excerpt: str,
    *,
    bible_text: str,
    series_en: str,
    series_ru: str,
    pilot_topic: str,
    pilot_ru: str,
    pilot_angle: str,
    count: int,
    forbidden: str,
) -> tuple[list[dict[str, str]], str | None]:
    system = (
        "Output ONLY a JSON array. Each object: Topic (English), Topic_RU (Russian), Angle. "
        f"Exactly {count} objects for episodes 2 through {count + 1} of the SAME series. "
        "Topic_RU must be Russian, not a copy of Topic. "
        'Angle starts with "Part X/5 —". No markdown outside JSON.'
    )
    user = (
        f"Channel:\n{config_excerpt[:4000]}\n\n"
        f'Series: "{series_en}" / "{series_ru}"\n\n'
        f"series_bible.md:\n{bible_text[:10000]}\n\n"
        f"Episode 1 (fixed pilot): {pilot_topic} / {pilot_ru}\nAngle: {pilot_angle}\n\n"
        f"FORBIDDEN topics:\n{forbidden or '(none)'}\n\n"
        f"Generate {count} sequel episode ideas continuing this arc."
    )
    raw, err = cf_chat(
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        temperature=0.48,
    )
    if err:
        return [], err
    rows = _parse_idea_rows(raw)
    if not rows:
        return [], "Не удалось разобрать идеи серий 2–5"
    return rows[:count], None


def expand_backlog_idea_to_series(
    channel_dir: Path,
    *,
    backlog_id: str,
    script_seed: str = "",
    existing_episode_dir: Path | None = None,
    follow_up_count: int = 0,
) -> tuple[dict[str, Any], Path | None, str | None]:
    """
    Пилот-идея → плейлист S0N, AI series_bible.json и один выпуск S0NE01 + папка.
    Возвращает (playlist entry, episode folder, error).
    """
    backlog_id = backlog_id.strip()
    backlog = _load_backlog(channel_dir)
    idx = backlog_row_index(backlog, backlog_id)
    if idx is None:
        return {}, None, f"Строка backlog с ID {backlog_id!r} не найдена."
    row = backlog.loc[idx]
    if not row_can_expand_to_series(row):
        return {}, None, "Эта строка уже привязана к сериалу — выберите standalone-идею."
    topic = str(row.get("Topic", "")).strip()
    topic_ru = str(row.get("Topic_RU", "")).strip()
    angle = str(row.get("Angle", "")).strip()
    if not topic:
        return {}, None, "Пустой Topic в бэклоге."
    if not topic_ru or topic_ru.lower() == topic.lower():
        return {}, None, "Нужен Topic_RU (русский перевод)."

    try:
        playlist = create_playlist(
            channel_dir,
            title=topic,
            title_en=topic,
            title_ru=topic_ru,
        )
    except ValueError as exc:
        return {}, None, str(exc)

    series_id = str(playlist["id"]).strip().upper()
    series_en = str(playlist.get("title_en", topic)).strip()
    series_ru = str(playlist.get("title_ru", topic_ru)).strip()
    config = _channel_config(channel_dir)

    pdir = playlist_dir_for_series(channel_dir, series_id)
    bible_data, err = _generate_series_bible_data(
        config,
        topic=topic,
        topic_ru=topic_ru,
        angle=angle,
        series_en=series_en,
        series_ru=series_ru,
        script_seed=script_seed,
    )
    if err:
        return playlist, None, f"series_bible: {err}"
    if pdir is None:
        return playlist, None, "Папка плейлиста не найдена."
    try:
        save_series_bible_data(pdir, bible_data)
    except OSError as exc:
        return playlist, None, str(exc)
    old_id = str(row.get("ID", "")).strip()
    new_id = f"{series_id}E01"

    backlog = backlog.drop(index=idx).reset_index(drop=True)

    src_dir = existing_episode_dir
    if src_dir is None:
        src_dir = episode_dir_for_backlog_id(channel_dir, old_id)
    if src_dir is not None:
        old_from_folder = src_dir.name.split("_", 1)[0]
    else:
        old_from_folder = old_id
    try:
        folder = relocate_episode_folder(channel_dir, old_from_folder, new_id, topic)
    except OSError as exc:
        _save_backlog(channel_dir, backlog)
        return playlist, None, str(exc)

    write_episode_idea(
        folder,
        {
            "id": new_id,
            "topic": topic,
            "topic_ru": topic_ru,
            "angle": angle,
            "series_id": series_id,
            "episode_num": "1",
            "series_en": series_en,
            "series_ru": series_ru,
        },
    )
    _save_backlog(channel_dir, backlog)
    return playlist, folder, None


def _episode_id(episode_dir: Path) -> str:
    return episode_dir.name.split("_")[0]


@dataclass(frozen=True)
class ScriptProfile:
    word_target_min: int
    word_target_max: int
    extra_chapter_floor: int
    chapter_word_min: int
    chapter_word_max: int
    base_chapters: int
    sleep_style: bool
    prompts_per_chapter: int = 4
    target_min_minutes: float = 15.0
    target_max_minutes: float = 20.0
    words_per_minute: int = 125
    target_frame_count: int = 20
    target_frame_count_manual: bool = False
    seconds_per_frame: float = 40.0


def _sleep_style_channel(channel_slug: str) -> bool:
    low = channel_slug.lower()
    return (
        channel_slug.startswith("channel_02")
        or "sleep_history" in low
        or channel_slug.startswith("channel_03")
        or "enchanted" in low
    )


def _parse_duration_from_channel_md(config_text: str) -> tuple[float, float] | None:
    for pat in (
        r"Длина\s+ролика:\s*(\d+)\s*[-–]\s*(\d+)\s*min",
        r"(\d+)\s*[-–]\s*(\d+)\s*min",
    ):
        m = re.search(pat, config_text, re.IGNORECASE)
        if m:
            return float(m.group(1)), float(m.group(2))
    return None


def default_minutes_for_channel(channel_slug: str, channel_dir: Path) -> tuple[float, float]:
    cfg_path = channel_dir / "channel_config.md"
    if cfg_path.is_file():
        parsed = _parse_duration_from_channel_md(cfg_path.read_text(encoding="utf-8-sig"))
        if parsed:
            return parsed
    return DEFAULT_MINUTES.get(channel_slug, (15, 20))


def load_episode_config(episode_dir: Path, channel_slug: str, channel_dir: Path) -> dict[str, float | int | bool | None]:
    from episode_timing import apply_timing_defaults

    path = episode_dir / EPISODE_CONFIG_JSON
    mn, mx = default_minutes_for_channel(channel_slug, channel_dir)
    wpm = DEFAULT_WPM.get(channel_slug, 125)
    base: dict[str, float | int | bool | None] = {
        "target_min_minutes": mn,
        "target_max_minutes": mx,
        "words_per_minute": wpm,
        "update_bible_recap": False,
        "target_frame_count": None,
        "seconds_per_frame": SECONDS_PER_FRAME,
        "voice_fade_out_sec": 12.0,
        "ambient_tail_sec": 0.0,
    }
    if not path.is_file():
        return apply_timing_defaults(base)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return apply_timing_defaults(base)
    if not isinstance(data, dict):
        return apply_timing_defaults(base)
    for key in base:
        if key in data:
            base[key] = data[key]
    try:
        base["target_min_minutes"] = float(base["target_min_minutes"])
        base["target_max_minutes"] = float(base["target_max_minutes"])
        base["words_per_minute"] = int(base["words_per_minute"])
    except (TypeError, ValueError):
        mn, mx = default_minutes_for_channel(channel_slug, channel_dir)
        wpm = DEFAULT_WPM.get(channel_slug, 125)
        base = {
            "target_min_minutes": mn,
            "target_max_minutes": mx,
            "words_per_minute": wpm,
            "update_bible_recap": False,
            "target_frame_count": None,
            "seconds_per_frame": SECONDS_PER_FRAME,
            "voice_fade_out_sec": 12.0,
            "ambient_tail_sec": 0.0,
        }
    if float(base["target_max_minutes"]) < float(base["target_min_minutes"]):
        base["target_max_minutes"] = base["target_min_minutes"]
    base["update_bible_recap"] = bool(data.get("update_bible_recap", False)) if isinstance(data, dict) else False
    return apply_timing_defaults(base)


def save_episode_config(episode_dir: Path, data: dict[str, float | int]) -> None:
    episode_dir.mkdir(parents=True, exist_ok=True)
    (episode_dir / EPISODE_CONFIG_JSON).write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def load_plot_brief(episode_dir: Path) -> str:
    path = episode_dir / PLOT_BRIEF_TXT
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8").strip()


def save_plot_brief(episode_dir: Path, text: str) -> None:
    episode_dir.mkdir(parents=True, exist_ok=True)
    (episode_dir / PLOT_BRIEF_TXT).write_text(text.strip(), encoding="utf-8")


def profile_from_episode_settings(episode_dir: Path, channel_slug: str) -> ScriptProfile:
    from episode_timing import resolve_target_frame_count

    channel_dir = episode_dir.parents[1] if len(episode_dir.parents) > 1 else ROOT / "channels" / channel_slug
    epi = load_episode_config(episode_dir, channel_slug, channel_dir)
    mn = float(epi["target_min_minutes"])
    mx = float(epi["target_max_minutes"])
    wpm = int(epi["words_per_minute"])
    spf = float(epi["seconds_per_frame"])
    word_min = int(mn * wpm)
    word_max = int(mx * wpm)
    sleep_style = _sleep_style_channel(channel_slug)
    if sleep_style:
        chapter_word_min, chapter_word_max = 480, 550
    else:
        chapter_word_min, chapter_word_max = 320, 400
    avg_words = (word_min + word_max) // 2
    base_chapters = max(3, min(7, round(avg_words / WORDS_PER_CHAPTER)))
    target_frame_count, frame_manual = resolve_target_frame_count(epi)
    prompts_per_chapter = max(3, min(6, round(target_frame_count / max(base_chapters, 1))))
    extra_chapter_floor = word_min if sleep_style else int(word_min * 0.85)
    return ScriptProfile(
        word_target_min=word_min,
        word_target_max=word_max,
        extra_chapter_floor=extra_chapter_floor,
        chapter_word_min=chapter_word_min,
        chapter_word_max=chapter_word_max,
        base_chapters=base_chapters,
        sleep_style=sleep_style,
        prompts_per_chapter=prompts_per_chapter,
        target_min_minutes=mn,
        target_max_minutes=mx,
        words_per_minute=wpm,
        target_frame_count=target_frame_count,
        target_frame_count_manual=frame_manual,
        seconds_per_frame=spf,
    )


def episode_plan_summary(episode_dir: Path, channel_slug: str) -> str:
    prof = profile_from_episode_settings(episode_dir, channel_slug)
    if prof.target_frame_count_manual:
        frames_label = f"{prof.target_frame_count} кадров (вручную)"
    else:
        frames_label = (
            f"≈{prof.target_frame_count} кадров (авто, {prof.seconds_per_frame:.0f} с/кадр)"
        )
    return (
        f"~{prof.target_min_minutes:.0f}–{prof.target_max_minutes:.0f} мин · "
        f"{prof.words_per_minute} wpm · {prof.word_target_min:,}–{prof.word_target_max:,} слов · "
        f"{prof.base_chapters} глав · ~{prof.prompts_per_chapter} кадров/глава · "
        f"{frames_label}"
    )


def script_profile(channel_slug: str) -> ScriptProfile:
    placeholder = ROOT / "channels" / channel_slug / "episodes" / "_profile_placeholder"
    return profile_from_episode_settings(placeholder, channel_slug)


def _split_script_chapters(text: str) -> list[tuple[str, str]]:
    chunks = re.split(r"^##\s+", text.strip(), flags=re.MULTILINE)
    chapters: list[tuple[str, str]] = []
    if not text.strip():
        return chapters
    if not re.search(r"^##\s+", text, flags=re.MULTILINE):
        return [("Full script", text.strip())]
    first = chunks[0].strip()
    if first:
        chapters.append(("Opening", first))
    for block in chunks[1:]:
        lines = block.split("\n", 1)
        title = lines[0].strip() or "Chapter"
        body = lines[1].strip() if len(lines) > 1 else ""
        if body:
            chapters.append((title, body))
    return chapters


def count_words(text: str) -> int:
    return len(WORD_RE.findall(text))


def _emit_job_progress(step: int, total: int, text: str) -> None:
    try:
        from job_progress import emit_progress

        emit_progress(step, total, text)
    except ImportError:
        print(f"[PROGRESS] {step}/{total} | {text}", flush=True)
    print(f"[words] {text}", flush=True)


def sanitize_narration_text(text: str) -> str:
    """Убрать PROMPT: и прочий не-дикторский мусор из сценария."""
    cleaned: list[str] = []
    for line in text.splitlines():
        if PROMPT_LINE_RE.match(line):
            continue
        stripped = line.strip()
        if stripped.lower().startswith("prompt:"):
            continue
        cleaned.append(line)
    body = "\n".join(cleaned)
    body = re.sub(r"\n{3,}", "\n\n", body).strip()
    return body


def _parse_chapter_outline(text: str) -> list[dict[str, str]]:
    text = _strip_fence(text).strip()
    items: list[Any] = []
    if text:
        try:
            start = text.find("[")
            end = text.rfind("]")
            chunk = text[start : end + 1] if start >= 0 and end > start else text
            parsed = json.loads(chunk)
            if isinstance(parsed, list):
                items = parsed
        except json.JSONDecodeError:
            pass
        if not items and text.startswith("["):
            try:
                parsed = ast.literal_eval(text[text.find("[") : text.rfind("]") + 1])
                if isinstance(parsed, list):
                    items = parsed
            except (SyntaxError, ValueError):
                pass
    chapters: list[dict[str, str]] = []
    for index, row in enumerate(items, start=1):
        if not isinstance(row, dict):
            continue
        title = str(row.get("title") or row.get("Title") or f"Chapter {index}").strip()
        beats = str(row.get("beats") or row.get("thesis") or row.get("summary") or "").strip()
        if title:
            chapters.append({"title": title, "beats": beats})
    return chapters[:7]


STANDALONE_SCRIPT_RULES = (
    "STANDALONE: one complete sleep story in a single video. "
    "Gentle opening, no prior episodes, soothing resolution and goodnight fade in the final chapter. "
    "Do not rely on series_bible or cliffhangers for a sequel."
)


def _generate_outline(
    profile: ScriptProfile,
    config: str,
    topic: str,
    angle: str,
    *,
    series_context: str = "",
    plot_brief: str = "",
) -> tuple[list[dict[str, str]], str | None]:
    system = (
        "You plan a YouTube narration outline. Output ONLY a JSON array of exactly "
        f"{profile.base_chapters} objects. Keys: title (English), beats (1-2 sentences). "
        "No markdown, no prose outside JSON."
    )
    style = (
        "Calm sleep history; soft immersion, no hook, no battle."
        if profile.sleep_style
        else "Gaming mystery essay; detective arc, hook in chapter 1 only."
    )
    caps = _llm_prompt_caps()
    ctx = (
        f"\n\nSeries context (world/characters, not plot beats):\n{series_context[: caps['series_outline']]}\n"
        if series_context.strip()
        else ""
    )
    plot_block = ""
    if plot_brief.strip():
        plot_block = (
            "\n\nPRIMARY plot direction (overrides producer brief, Topic/Angle, bible beats if conflict):\n"
            f"{plot_brief.strip()[: caps['plot_outline']]}\n"
        )
    user = (
        f"Channel config:\n{config[: caps['config_outline']]}\n\n"
        f"Title hint only — Topic: {topic}\nAngle (ignore if conflicts plot): {angle or '—'}\nStyle: {style}\n"
        f"{ctx}{plot_block}\n"
        f"Return {profile.base_chapters} chapter titles + beats as minified JSON array."
    )
    raw, err = cf_chat(
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        temperature=0.35,
    )
    if err:
        return [], err
    chapters = _parse_chapter_outline(raw)
    if len(chapters) < profile.base_chapters:
        while len(chapters) < profile.base_chapters:
            n = len(chapters) + 1
            chapters.append({"title": f"Chapter {n}", "beats": f"Continue the story beat {n} for: {topic}"})
    return chapters[: profile.base_chapters], None


def _generate_chapter_en(
    profile: ScriptProfile,
    config: str,
    topic: str,
    angle: str,
    chapter: dict[str, str],
    chapter_index: int,
    chapter_count: int,
    prior_titles: list[str],
    *,
    allow_farewell: bool,
    extra_immersion: bool = False,
    series_context: str = "",
    plot_brief: str = "",
) -> tuple[str, str | None]:
    farewell_rule = (
        "Include only a very soft goodnight fade in the closing sentences."
        if allow_farewell and profile.sleep_style
        else (
            "No goodbyes, no 'good night', no closing the story yet."
            if profile.sleep_style
            else "No recap trailer; end this chapter on the next open clue."
        )
    )
    if extra_immersion:
        farewell_rule = (
            "Deep immersion chapter before the end: extend atmosphere, still calm. "
            + ("Soft fade only at the very last paragraph." if profile.sleep_style else "")
        )
    system = (
        "Write YouTube voiceover prose in English. Output ONLY narration text for this chapter — "
        "no JSON, no PROMPT: lines, no image directions, no bullet lists. "
        f"Target length: {profile.chapter_word_min}-{profile.chapter_word_max} words. "
        "Use vivid sensory detail, no filler. "
        f"{farewell_rule} "
        "Mark disputed facts with [FACT-CHECK] only when needed."
    )
    if not profile.sleep_style:
        system += " Mark fan theories with [THEORY]."
    caps = _llm_prompt_caps()
    ctx = (
        f"\n\nSeries / format:\n{series_context[: caps['series_chapter']]}\n"
        if series_context.strip()
        else ""
    )
    plot_block = ""
    if plot_brief.strip():
        plot_block = (
            "\nPRIMARY plot direction (must follow; overrides producer brief and Topic/Angle):\n"
            f"{plot_brief.strip()[: caps['plot_chapter']]}\n"
        )
    user = (
        f"Channel config:\n{config[: caps['config_chapter']]}\n\n"
        f"Title hint — topic: {topic}\nAngle (ignore if conflicts plot): {angle or '—'}\n"
        f"{ctx}{plot_block}\n"
        f"Chapter {chapter_index}/{chapter_count}: {chapter['title']}\n"
        f"Beats: {chapter.get('beats', '')}\n\n"
        f"Prior chapters (titles only): {', '.join(prior_titles) or 'none'}\n\n"
        "Write this chapter now."
    )
    raw, err = cf_chat(
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        max_tokens=4096,
        temperature=0.42,
    )
    if err:
        return "", err
    text = sanitize_narration_text(raw)
    if count_words(text) < profile.chapter_word_min // 2:
        return "", f"Глава {chapter_index} слишком короткая ({count_words(text)} слов)."
    return text, None


def _combined_plot_direction(
    producer_brief: str,
    episode_plot: str,
    *,
    series_id: str,
) -> str:
    """Сюжет эпизода — только plot_brief (+ optional instruction). Producer — фон, если plot пуст."""
    producer = producer_brief.strip()
    extra = episode_plot.strip()
    if series_id == STANDALONE_ID:
        return extra
    if extra:
        return f"EPISODE PLOT (plot_brief.txt — обязательно, этот выпуск):\n{extra}"
    if producer:
        return (
            "EPISODE PLOT: plot_brief пуст — ориентир только общий логлайн серии (не развивай арку дальше одного эпизода):\n"
            f"{producer[:1200]}"
        )
    return ""


def _sanitize_recap_summary(text: str) -> str:
    text = (text or "").strip()
    if not text:
        return ""
    if text.startswith("{") and "summary_" in text:
        try:
            block = json.loads(_strip_json_fence(text))
            if isinstance(block, dict):
                return str(block.get("summary_en") or block.get("summary_ru") or "").strip()
        except json.JSONDecodeError:
            pass
    return text[:2500]


def _episode_num_for_context(episode_dir: Path | None, idea: dict[str, Any]) -> int:
    if idea:
        raw = str(idea.get("episode_num", "") or "").strip()
        try:
            return max(1, int(float(raw)))
        except ValueError:
            pass
    if episode_dir is not None:
        m = SERIES_EP_ID_RE.fullmatch(_episode_id(episode_dir).upper())
        if m:
            return int(m.group(2))
    return 1


def _filtered_bible_recaps(data: dict[str, Any], episode_num: int) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for row in data.get("recaps") or []:
        if not isinstance(row, dict):
            continue
        ep = str(row.get("episode", "")).strip().upper()
        match = SERIES_EP_ID_RE.fullmatch(ep)
        if not match or int(match.group(2)) >= episode_num:
            continue
        en = _sanitize_recap_summary(str(row.get("summary_en", "")))
        ru = _sanitize_recap_summary(str(row.get("summary_ru", "")))
        if len(en) < 40 or en.startswith("{"):
            continue
        rows.append(
            {
                "episode": ep,
                "title": str(row.get("title", "")).strip(),
                "summary_en": en,
                "summary_ru": ru or en,
            }
        )
    return rows


def _build_series_script_context(
    channel_dir: Path,
    row: pd.Series | None,
    *,
    episode_dir: Path | None = None,
) -> tuple[str, str, Path | None]:
    if row is None:
        return STANDALONE_SCRIPT_RULES, STANDALONE_ID, None
    series_id = str(row.get("Series_ID", "") or "").strip() or STANDALONE_ID
    if series_id == STANDALONE_ID:
        return STANDALONE_SCRIPT_RULES, series_id, None
    idea = read_episode_idea(episode_dir) if episode_dir else {}
    ep_num = _episode_num_for_context(episode_dir, idea)
    bible_path = series_bible_path(channel_dir, series_id)
    pdir = playlist_dir_for_series(channel_dir, series_id)
    data = load_series_bible_json(pdir) if pdir else {}
    caps = _llm_prompt_caps()
    prior_recaps = _filtered_bible_recaps(data, ep_num)
    bible_tone = format_series_bible_episode_tone_data(normalize_series_bible_data(data))[: caps["series_bible"]]
    recap_lines: list[str] = []
    for row in prior_recaps:
        recap_lines.append(
            f"- {row.get('episode')}: RU {row.get('summary_ru', '')[:400]}"
        )
    recap_block = ""
    if recap_lines:
        recap_block = (
            "\n--- Prior episodes (continuity hints only — do not replace plot_brief) ---\n"
            + "\n".join(recap_lines[:6])
            + "\n"
        )
    if ep_num <= 1:
        chronicle_rule = (
            "CHRONICLE: Episode 1 — series pilot. No prior episodes. "
            "Follow plot_brief.txt scope for where this episode starts and MUST end."
        )
    elif prior_recaps:
        chronicle_rule = (
            "CHRONICLE: Prior recaps — только намёк в начале; сюжет эпизода = plot_brief."
        )
    else:
        chronicle_rule = "CHRONICLE: Нет recap — сюжет только из plot_brief."
    context = (
        f"SERIAL episode #{ep_num}. Series_ID={series_id}.\n"
        "PLOT: только plot_brief.txt (+ editor mandate). "
        "Bible ниже — тон, внешность, правила мира; НЕ источник сюжетных поворотов. "
        "Producer brief / playlist arc НЕ в этом блоке — не подставляй шаги будущих серий.\n"
        "Topic/Angle — заголовки; игнорировать при конфликте с plot_brief.\n"
        "Second-person cozy fantasy sleep tone; slow pacing.\n"
        f"--- Bible (tone & visuals only) ---\n{bible_tone}\n"
        f"{recap_block}"
        f"--- {chronicle_rule}"
    )
    return context, series_id, bible_path


def _generate_episode_recap(topic: str, script_excerpt: str) -> tuple[dict[str, str], str | None]:
    system = (
        "Write a concise producer episode recap for series_bible.json. "
        "Output ONLY JSON: "
        '{"summary_en":"3–6 sentences","summary_ru":"same meaning, literary Russian"} '
        "— plot beats, tone, where characters are now. No bullet lists."
    )
    user = (
        f"Episode topic: {topic}\n\n"
        f"Script excerpt (start):\n{script_excerpt[:8000]}"
    )
    raw, err = cf_chat(
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        temperature=0.35,
        max_tokens=768,
    )
    if err:
        return {}, err
    try:
        block = json.loads(_strip_json_fence(raw))
    except json.JSONDecodeError:
        return {
            "summary_en": raw.strip()[:2500],
            "summary_ru": raw.strip()[:2500],
        }, None
    en = _sanitize_recap_summary(str(block.get("summary_en", "")))
    ru = _sanitize_recap_summary(str(block.get("summary_ru", "")))
    if not en and raw.strip():
        en = raw.strip()[:2500]
    if not ru:
        ru = en
    return {"summary_en": en, "summary_ru": ru}, None


def _generate_chapter_visual_prompts(
    profile: ScriptProfile,
    config: str,
    chapter_title: str,
    chapter_text: str,
    *,
    count: int | None = None,
) -> tuple[list[str], str | None]:
    n = count or profile.prompts_per_chapter
    suffix = (
        "detailed textures, realistic oil painting style, crisp focus on subject, "
        "deep shadows, chiaroscuro lighting, 16:9, no text"
    )
    system = (
        f"Create exactly {n} image prompts for AI art. Output ONLY {n} lines. "
        "Each line must start with PROMPT: then English description. "
        "No narration, no Russian, no JSON array. "
        f"Each prompt must include a clear subject and warm local light; end with: {suffix}"
    )
    user = (
        f"Channel visual rules:\n{config[:6000]}\n\n"
        f"Chapter: {chapter_title}\n"
        f"Narration excerpt:\n{chapter_text[:3500]}\n\n"
        f"Write {n} PROMPT: lines for this chapter."
    )
    raw, err = cf_chat(
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        max_tokens=2048,
        temperature=0.38,
    )
    if err:
        return [], err
    from generate_images import extract_prompts, extract_prompts_from_blob

    lines = extract_prompts(raw)
    if not lines:
        lines = extract_prompts_from_blob(raw)
    if not lines:
        lines = [ln.split(":", 1)[1].strip() for ln in raw.splitlines() if PROMPT_LINE_RE.match(ln.strip())]
    return lines[: n + 1], None


def _visual_prompt_bounds(profile: ScriptProfile, chapter_count: int) -> tuple[int, int]:
    """Минимум кадров с учётом реального числа глав (не только target_frame_count)."""
    target_floor = max(12, profile.target_frame_count - 3)
    planned = max(12, chapter_count * profile.prompts_per_chapter - 2)
    prompt_floor = min(target_floor, planned)
    prompt_cap = max(
        prompt_floor + 3,
        profile.target_frame_count + 2,
        chapter_count * profile.prompts_per_chapter + 2,
    )
    return prompt_floor, prompt_cap


def _top_up_visual_prompts(
    profile: ScriptProfile,
    config: str,
    titles: list[str],
    chapters_en: list[str],
    all_prompts: list[str],
    prompt_floor: int,
) -> tuple[list[str], str | None]:
    """Догенерировать PROMPT: если LLM вернул чуть меньше строк, чем план."""
    prompts = list(all_prompts)
    if len(prompts) >= prompt_floor or not titles:
        return prompts, None
    shortage = prompt_floor - len(prompts)
    attempts = 0
    while len(prompts) < prompt_floor and attempts < 3:
        attempts += 1
        idx = min(len(titles) - 1, attempts - 1)
        title = titles[idx]
        body = chapters_en[idx] if idx < len(chapters_en) else chapters_en[-1]
        need = prompt_floor - len(prompts)
        extra, err = _generate_chapter_visual_prompts(
            profile,
            config,
            title,
            body,
            count=need + 2,
        )
        if err:
            return prompts, err
        for line in extra:
            if line not in prompts:
                prompts.append(line)
            if len(prompts) >= prompt_floor:
                break
    return prompts, None


def _translate_chapter_ru(chapter_title: str, chapter_en: str, topic_ru_hint: str) -> tuple[str, str | None]:
    system = (
        "Literary Russian adaptation of an English YouTube chapter. "
        "Output ONLY Russian prose with the same structure. "
        "No [FACT-CHECK], no [THEORY], no PROMPT: lines, no commentary."
    )
    user = (
        f"Russian title hint for the video: {topic_ru_hint or '—'}\n\n"
        f"Chapter title (EN): {chapter_title}\n\n"
        f"English chapter:\n{chapter_en[:12000]}\n\n"
        "Russian chapter:"
    )
    raw, err = cf_chat(
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        max_tokens=4096,
        temperature=0.35,
    )
    if err:
        return "", err
    return sanitize_narration_text(raw), None


def _generate_metadata_md(
    config: str,
    meta_template: str,
    topic: str,
    topic_ru: str,
    script_en: str,
) -> tuple[str, str | None]:
    system = (
        "Write episode metadata markdown. Two sections exactly: "
        "## English Metadata and ## Russian Metadata. "
        "Follow the template fields. No JSON."
    )
    user = (
        f"Template:\n{meta_template[:4000]}\n\n"
        f"Config excerpt:\n{config[:4000]}\n\n"
        f"Topic EN: {topic}\nTopic RU: {topic_ru}\n\n"
        f"Script excerpt:\n{script_en[:8000]}\n\n"
        "Write metadata.md content."
    )
    raw, err = cf_chat(
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        max_tokens=4096,
        temperature=0.35,
    )
    return (raw.strip(), err) if not err else ("", err)


def _topic_for_episode(channel_dir: Path, episode_dir: Path) -> tuple[str, str, str]:
    idea = read_episode_idea(episode_dir)
    if idea.get("topic"):
        return (
            str(idea.get("topic", "Episode")),
            str(idea.get("topic_ru", "")),
            str(idea.get("angle", "")),
        )
    ep_id = _episode_id(episode_dir)
    backlog = _load_backlog(channel_dir)
    if not backlog.empty and "ID" in backlog.columns:
        match = backlog[backlog["ID"].astype(str).str.strip() == ep_id]
        if not match.empty:
            row = match.iloc[0]
            return (
                str(row.get("Topic", "Episode")),
                str(row.get("Topic_RU", "")),
                str(row.get("Angle", "")),
            )
    title = episode_dir.name.replace("_", " ")
    return title, "", ""


def _adapt_full_script_language(
    source_text: str,
  *,
    to_english: bool,
    topic_hint: str,
    instruction: str,
    config: str,
) -> tuple[str, str | None]:
    if not source_text.strip():
        return "", "Пустой исходный сценарий."
    extra = f"\nEditor instruction:\n{instruction.strip()[:3000]}" if instruction.strip() else ""
    rewrite = bool(instruction.strip()) and any(
        w in instruction.lower()
        for w in (
            "перепис",
            "заново",
            "не должно",
            "убери",
            "начал",
            "конец",
            "эпизод",
            "rewrite",
            "remove",
        )
    )
    if to_english:
        system = (
            "Adapt a Russian YouTube narration into English. "
            + (
                "Follow Editor instruction even if it changes plot beats and chapter content; "
                "you may rename ## headings if needed. "
                if rewrite
                else "Keep the same ## chapter headings and paragraph structure. "
            )
            + "Output ONLY the English script markdown — no commentary."
        )
        user = (
            f"Channel config excerpt:\n{config[:5000]}\n\n"
            f"Topic hint: {topic_hint or '—'}\n{extra}\n\n"
            f"Russian script:\n{source_text[:30000]}\n\nEnglish script:"
        )
    else:
        system = (
            "Literary Russian adaptation of an English YouTube script. "
            + (
                "Follow Editor instruction even if plot and headings change. "
                if rewrite
                else "Keep the same ## chapter headings and structure. "
            )
            + "No [FACT-CHECK], [THEORY], PROMPT: lines. Output ONLY Russian markdown."
        )
        user = (
            f"Channel config excerpt:\n{config[:5000]}\n\n"
            f"Topic hint: {topic_hint or '—'}\n{extra}\n\n"
            f"English script:\n{source_text[:30000]}\n\nRussian script:"
        )
    raw, err = cf_chat(
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        max_tokens=8192,
        temperature=0.35,
    )
    if err:
        return "", err
    return sanitize_narration_text(raw) if not to_english else re.sub(
        r"\n{3,}", "\n\n", _strip_fence(raw).strip()
    ), None


def _script_similarity(left: str, right: str) -> float:
    a = re.sub(r"\s+", " ", (left or "").strip().lower())
    b = re.sub(r"\s+", " ", (right or "").strip().lower())
    if not a or not b:
        return 0.0
    sample = 50_000
    return SequenceMatcher(None, a[:sample], b[:sample]).ratio()


def _rework_script_with_instruction(
    source_text: str,
    *,
    lang: str,
    instruction: str,
    profile: ScriptProfile,
    config: str,
    topic_hint: str,
    strict_retry: bool = False,
) -> tuple[str, str | None]:
    """Правка сценария только по комментарию продюсера (без plot_brief)."""
    if not source_text.strip():
        return "", "Пустой исходный сценарий."
    comment = instruction.strip()
    if not comment:
        return "", "Пустой комментарий — переработка только по инструкции в UI."

    lang_note = "Russian" if lang.lower().startswith("ru") else "English"
    system = (
        f"You edit a YouTube sleep-style narration script in {lang_note}. "
        "Follow the producer comment strictly — apply real content changes, not proofreading only. "
        f"Keep length near {profile.word_target_min}-{profile.word_target_max} words total. "
        "Keep ## chapter headings unless the comment requires restructuring. "
        "Output ONLY the script markdown — no commentary."
    )
    user_parts = [
        f"Channel config excerpt:\n{config[:5000]}",
        f"Topic hint: {topic_hint or '—'}",
        f"Producer comment:\n{comment[:3000]}",
    ]
    if strict_retry:
        user_parts.append(
            "CRITICAL: The previous attempt was rejected as nearly identical to the source. "
            "Apply the producer comment with substantive scene and wording changes."
        )
    user_parts.append(f"Current {lang_note} script:\n{source_text[:30000]}")
    user_parts.append(f"Revised {lang_note} script:")
    user = "\n\n".join(user_parts)

    raw, err = cf_chat(
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        max_tokens=8192,
        temperature=0.48 if strict_retry else 0.38,
    )
    if err:
        return "", err
    if lang.lower().startswith("ru"):
        out = sanitize_narration_text(raw)
    else:
        out = re.sub(r"\n{3,}", "\n\n", _strip_fence(raw).strip())

    if _script_similarity(source_text, out) >= 0.88:
        if not strict_retry:
            return _rework_script_with_instruction(
                source_text,
                lang=lang,
                instruction=instruction,
                profile=profile,
                config=config,
                topic_hint=topic_hint,
                strict_retry=True,
            )
        return (
            "",
            "Сценарий почти не изменился после переработки (модель вернула тот же текст). "
            "Уточните комментарий или смените модель в резервной цепочке.",
        )
    return out, None


def rework_episode_from_truth_source(
    episode_dir: Path,
    *,
    primary_lang: str = "en",
    instruction: str = "",
) -> str | None:
    """Источник истины RU/EN → (опц.) переработка по комментарию → второй язык → кадры + metadata."""
    from episode_snapshot import save_episode_snapshot

    channel_dir = episode_dir.parents[1]
    channel_slug = channel_dir.name
    profile = profile_from_episode_settings(episode_dir, channel_slug)
    config = _channel_config(channel_dir)
    topic_en, topic_ru_hint, _angle = _topic_for_episode(channel_dir, episode_dir)
    en_path = episode_dir / FILE_SCRIPT
    ru_path = episode_dir / FILE_SCRIPT_RU
    chapters_count = max(profile.base_chapters, 1)
    progress_total = 4 + chapters_count + 3
    progress_step = 0

    def tick(text: str) -> None:
        nonlocal progress_step
        progress_step += 1
        _emit_job_progress(progress_step, progress_total, text)

    tick("Снапшот текущих файлов")
    save_episode_snapshot(episode_dir, "before_rework")

    en_text = en_path.read_text(encoding="utf-8-sig").strip() if en_path.is_file() else ""
    ru_text = ru_path.read_text(encoding="utf-8-sig").strip() if ru_path.is_file() else ""
    primary = primary_lang.strip().lower()
    use_ru_primary = primary in {"ru", "russian", "рус", "русский"}
    comment = instruction.strip()
    if not comment:
        return "Укажите комментарий в UI — переработка только по комментарию (plot_brief не используется)."

    if use_ru_primary:
        if not ru_text:
            return "Нет текста в script_ru.md — сохраните RU или выберите EN как источник."
        tick("Переработка RU по комментарию")
        ru_text, err = _rework_script_with_instruction(
            ru_text,
            lang="ru",
            instruction=comment,
            profile=profile,
            config=config,
            topic_hint=topic_ru_hint,
        )
        if err:
            return err
        tick("Синхронизация RU → EN")
        en_text, err = _adapt_full_script_language(
            ru_text,
            to_english=True,
            topic_hint=topic_en,
            instruction="",
            config=config,
        )
        if err:
            return err
    else:
        if not en_text:
            return "Нет текста в script.md — сохраните EN или выберите RU как источник."
        tick("Переработка EN по комментарию")
        en_text, err = _rework_script_with_instruction(
            en_text,
            lang="en",
            instruction=comment,
            profile=profile,
            config=config,
            topic_hint=topic_en,
        )
        if err:
            return err
        tick("Синхронизация EN → RU")
        ru_text, err = _adapt_full_script_language(
            en_text,
            to_english=False,
            topic_hint=topic_ru_hint,
            instruction="",
            config=config,
        )
        if err:
            return err

    chapters = _split_script_chapters(en_text)
    if not chapters:
        return "Не удалось разобрать главы в EN сценарии."

    _write_if_content(episode_dir, FILE_SCRIPT, en_text)
    _write_if_content(episode_dir, FILE_SCRIPT_RU, ru_text)
    print("Сценарии сохранены на диск (до генерации кадров).", flush=True)

    from generate_images import format_visual_prompts_markdown

    all_prompts: list[str] = []
    for idx, (title, body) in enumerate(chapters, start=1):
        prompts, err = _generate_chapter_visual_prompts(
            profile, config, title, body, count=profile.prompts_per_chapter
        )
        if err:
            return err
        all_prompts.extend(prompts)
        tick(f"Промпты кадров: глава {idx}/{len(chapters)} ({len(prompts)} шт.)")

    prompt_floor, prompt_cap = _visual_prompt_bounds(profile, len(chapters))
    all_prompts, err = _top_up_visual_prompts(
        profile, config, [t for t, _ in chapters], [b for _, b in chapters], all_prompts, prompt_floor
    )
    if err:
        return err
    if len(all_prompts) < prompt_floor:
        return f"Мало кадров после синхронизации: {len(all_prompts)} (<{prompt_floor})."
    tick(f"Доведение кадров до минимума ({len(all_prompts)}/{prompt_floor})")

    meta_template_path = ROOT / "templates" / "episode_metadata.md"
    meta_template = ""
    if meta_template_path.is_file():
        meta_template = meta_template_path.read_text(encoding="utf-8-sig")
    metadata, err = _generate_metadata_md(config, meta_template, topic_en, topic_ru_hint, en_text)
    if err:
        return err
    tick("metadata.md")

    visual_md = "# Visual prompts\n\n" + format_visual_prompts_markdown(all_prompts[:prompt_cap])
    _write_if_content(episode_dir, FILE_SCRIPT, en_text)
    _write_if_content(episode_dir, FILE_SCRIPT_RU, ru_text)
    _write_if_content(episode_dir, FILE_VISUAL, visual_md)
    _write_if_content(episode_dir, FILE_METADATA, metadata)
    _emit_job_progress(progress_total, progress_total, "Готово: запись файлов")
    print(
        f"Переработка готова: EN {count_words(en_text)} слов, "
        f"кадров {len(all_prompts[:prompt_cap])}, источник={primary_lang}.",
        flush=True,
    )
    return None


# Совместимость со старыми вызовами
def sync_episode_scripts_after_edit(
    episode_dir: Path,
    *,
    primary_lang: str = "en",
    instruction: str = "",
    mode: str = "translate",
) -> str | None:
    _ = mode
    return rework_episode_from_truth_source(
        episode_dir,
        primary_lang=primary_lang,
        instruction=instruction,
    )


def script_needs_generation(episode_dir: Path) -> bool:
    channel_slug = episode_dir.parents[1].name
    profile = profile_from_episode_settings(episode_dir, channel_slug)
    path = episode_dir / "script.md"
    if not path.is_file():
        return True
    text = path.read_text(encoding="utf-8-sig").strip()
    words = count_words(text)
    threshold = max(450, profile.extra_chapter_floor - 150)
    return words < threshold


def _write_if_content(episode_dir: Path, name: str, body: str) -> None:
    body = body.strip()
    if not body:
        return
    (episode_dir / name).write_text(body + ("\n" if not body.endswith("\n") else ""), encoding="utf-8")


def _visual_prompts_to_markdown(raw: Any) -> str:
    from generate_images import extract_prompts, format_visual_prompts_markdown

    if isinstance(raw, list):
        text = "\n".join(str(item) for item in raw)
    else:
        text = str(raw or "")
    prompts = extract_prompts(text)
    if not prompts:
        return text
    return "# Visual prompts\n\n" + format_visual_prompts_markdown(prompts)


def generate_episode_script_pack(
    episode_dir: Path,
    *,
    extra_plot_instruction: str = "",
) -> str | None:
    """Поглавная генерация: script / script_ru / visual_prompts / metadata."""
    try:
        return _generate_episode_script_pack_inner(
            episode_dir,
            extra_plot_instruction=extra_plot_instruction,
        )
    finally:
        if str(load_ai_settings().get("text_provider")) == "ollama":
            unload_ollama_model()


def _generate_episode_script_pack_inner(
    episode_dir: Path,
    *,
    extra_plot_instruction: str = "",
) -> str | None:
    from episode_snapshot import save_episode_snapshot

    save_episode_snapshot(episode_dir, "before_generate")
    channel_dir = episode_dir.parents[1]
    channel_slug = channel_dir.name
    profile = profile_from_episode_settings(episode_dir, channel_slug)
    plot_brief_raw = load_plot_brief(episode_dir)
    config = _channel_config(channel_dir)
    topic_en, topic_ru_hint, angle = _topic_for_episode(channel_dir, episode_dir)
    backlog_row = backlog_row_for_episode_dir(
        channel_dir, episode_dir, _load_backlog(channel_dir)
    )
    series_context, series_id, bible_path = _build_series_script_context(
        channel_dir, backlog_row, episode_dir=episode_dir
    )
    producer_brief = read_producer_brief(channel_dir, series_id) if series_id != STANDALONE_ID else ""
    plot_brief = _combined_plot_direction(
        producer_brief,
        plot_brief_raw,
        series_id=series_id,
    )
    extra = extra_plot_instruction.strip()
    if extra:
        plot_brief = (
            f"{plot_brief}\n\n"
            "--- ОБЯЗАТЕЛЬНАЯ рамка ЭТОГО эпизода (важнее producer brief по START/END и запретам) ---\n"
            f"{extra}"
        ).strip()
    meta_template_path = ROOT / "templates" / "episode_metadata.md"
    meta_template = ""
    if meta_template_path.is_file():
        meta_template = meta_template_path.read_text(encoding="utf-8-sig")

    # outline + (en + prompts + ru) * chapters + optional ch6 + metadata
    progress_total = 1 + profile.base_chapters * 3 + 1 + 3
    progress_step = 0

    def tick(text: str) -> None:
        nonlocal progress_step
        progress_step += 1
        _emit_job_progress(progress_step, progress_total, text)

    tick(f"План: {profile.base_chapters} глав (outline) · {episode_plan_summary(episode_dir, channel_slug)}")
    outline, err = _generate_outline(
        profile,
        config,
        topic_en,
        angle,
        series_context=series_context,
        plot_brief=plot_brief,
    )
    if err:
        return err

    chapters_en: list[str] = []
    chapters_ru: list[str] = []
    titles: list[str] = []
    all_prompts: list[str] = []

    for index, chapter in enumerate(outline, start=1):
        prior = titles[:]
        # Сон: прощание только в 6-й доп. главе или в 5-й, если объём уже набран.
        allow_farewell = (
            not profile.sleep_style and index == profile.base_chapters
        )
        body, err = _generate_chapter_en(
            profile,
            config,
            topic_en,
            angle,
            chapter,
            index,
            profile.base_chapters,
            prior,
            allow_farewell=allow_farewell,
            series_context=series_context,
            plot_brief=plot_brief,
        )
        if err:
            return err
        titles.append(chapter["title"])
        chapters_en.append(body)
        total_words = count_words("\n\n".join(chapters_en))
        tick(f"Сгенерирована глава {index} (всего слов: {total_words})")

        prompts, err = _generate_chapter_visual_prompts(
            profile, config, chapter["title"], body, count=profile.prompts_per_chapter
        )
        if err:
            return err
        all_prompts.extend(prompts)
        tick(f"Промпты кадров: глава {index} ({len(prompts)} шт.)")

        ru_body, err = _translate_chapter_ru(chapter["title"], body, topic_ru_hint)
        if err:
            return err
        chapters_ru.append(ru_body)
        tick(f"Перевод RU: глава {index}")

    full_script = "\n\n".join(
        f"## {title}\n\n{sanitize_narration_text(text)}" for title, text in zip(titles, chapters_en)
    )
    total_words = count_words(full_script)

    needs_extra = profile.sleep_style and total_words < profile.extra_chapter_floor
    if needs_extra:
        progress_total += 3
        extra_spec = {
            "title": "Deeper night",
            "beats": "Extra immersion: slower pace, more sensory detail, no new plot.",
        }
        body, err = _generate_chapter_en(
            profile,
            config,
            topic_en,
            angle,
            extra_spec,
            len(titles) + 1,
            len(titles) + 1,
            titles,
            allow_farewell=True,
            extra_immersion=True,
            series_context=series_context,
            plot_brief=plot_brief,
        )
        if err:
            return err
        titles.append(extra_spec["title"])
        chapters_en.append(body)
        chapters_ru_body, err = _translate_chapter_ru(extra_spec["title"], body, topic_ru_hint)
        if err:
            return err
        chapters_ru.append(chapters_ru_body)
        total_words = count_words("\n\n".join(chapters_en))
        tick(f"Доп. глава 6 — погружение (всего слов: {total_words})")

        extra_prompts, err = _generate_chapter_visual_prompts(
            profile, config, extra_spec["title"], body, count=profile.prompts_per_chapter
        )
        if err:
            return err
        all_prompts.extend(extra_prompts)
        tick("Промпты кадров: глава 6")

        full_script = "\n\n".join(
            f"## {title}\n\n{sanitize_narration_text(text)}" for title, text in zip(titles, chapters_en)
        )
        total_words = count_words(full_script)
    elif profile.sleep_style and outline:
        ch5 = outline[-1]
        prior = titles[:-1]
        body, err = _generate_chapter_en(
            profile,
            config,
            topic_en,
            angle,
            ch5,
            profile.base_chapters,
            profile.base_chapters,
            prior,
            allow_farewell=True,
            series_context=series_context,
            plot_brief=plot_brief,
        )
        if err:
            return err
        chapters_en[-1] = body
        ru_body, err = _translate_chapter_ru(ch5["title"], body, topic_ru_hint)
        if err:
            return err
        chapters_ru[-1] = ru_body
        full_script = "\n\n".join(
            f"## {title}\n\n{sanitize_narration_text(text)}" for title, text in zip(titles, chapters_en)
        )
        total_words = count_words(full_script)
        tick(f"Глава 5 — финал с прощанием (всего слов: {total_words})")

    if total_words < profile.word_target_min - 200:
        return (
            f"Сценарий слишком короткий: {total_words} слов "
            f"(нужно {profile.word_target_min}-{profile.word_target_max})."
        )

    script_ru_full = "\n\n".join(
        f"## {title}\n\n{sanitize_narration_text(text)}" for title, text in zip(titles, chapters_ru)
    )

    from generate_images import format_visual_prompts_markdown

    prompt_floor, prompt_cap = _visual_prompt_bounds(profile, len(titles))
    all_prompts, err = _top_up_visual_prompts(
        profile, config, titles, chapters_en, all_prompts, prompt_floor
    )
    if err:
        return err
    if len(all_prompts) < prompt_floor:
        return f"Мало промптов кадров: {len(all_prompts)} (нужно ≥{prompt_floor})."
    visual_md = "# Visual prompts\n\n" + format_visual_prompts_markdown(all_prompts[:prompt_cap])

    metadata, err = _generate_metadata_md(config, meta_template, topic_en, topic_ru_hint, full_script)
    if err:
        return err
    tick(f"metadata.md готово (итого слов EN: {total_words})")

    episode_dir.mkdir(parents=True, exist_ok=True)
    (episode_dir / "images").mkdir(exist_ok=True)
    _write_if_content(episode_dir, "script.md", full_script)
    _write_if_content(episode_dir, "script_ru.md", script_ru_full)
    _write_if_content(episode_dir, "visual_prompts.md", visual_md)
    _write_if_content(episode_dir, "metadata.md", metadata)
    epi_cfg = load_episode_config(episode_dir, channel_slug, channel_dir)
    if (
        series_id != STANDALONE_ID
        and bible_path is not None
        and bool(epi_cfg.get("update_bible_recap"))
    ):
        ep_code = _episode_id(episode_dir)
        recap, err_recap = _generate_episode_recap(topic_en, full_script)
        if err_recap:
            print(f"Recap не записан: {err_recap}", flush=True)
        elif recap.get("summary_en"):
            append_recap_block(
                bible_path,
                ep_code,
                topic_en,
                recap["summary_en"],
                recap_ru=recap.get("summary_ru", ""),
            )
            tick("series_bible: Recap обновлён (вручную включено)")
    elif series_id != STANDALONE_ID:
        print(
            "[episode] Recap в bible не трогали (update_bible_recap=false). Обновите в Bible или включите галочку.",
            flush=True,
        )
    print(
        f"Сценарий готов: {total_words} слов, глав {len(titles)}, промптов {len(all_prompts[:25])}.",
        flush=True,
    )
    return None