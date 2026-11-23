"""Плейлисты (сериалы) и standalone-эпизоды: ID, бэклог, series_bible."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pandas as pd

STANDALONE_ID = "standalone"
STANDALONE_UI_LABEL = "📦 Одиночные истории (Standalone — 1 эпизод)"
SERIES_ID_RE = re.compile(r"^(S\d+)", re.IGNORECASE)
SERIES_EP_ID_RE = re.compile(r"^(S\d+)E(\d+)$", re.IGNORECASE)
STANDALONE_EP_RE = re.compile(r"^(?:EP|SH|GM)(\d+)$", re.IGNORECASE)

BACKLOG_COLUMNS = [
    "ID",
    "Topic",
    "Topic_RU",
    "Series_ID",
    "Episode_Num",
    "Series",
    "Series_RU",
    "Angle",
    "Competitor_Ref",
    "Competitor_Views",
    "Outlier_Score",
    "Expected_Views",
    "Status",
    "Priority",
]


def playlists_path(channel_dir: Path) -> Path:
    return channel_dir / "playlists.json"


def load_playlists(channel_dir: Path) -> list[dict[str, Any]]:
    path = playlists_path(channel_dir)
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError:
        return []
    rows = data.get("playlists", data if isinstance(data, list) else [])
    return [dict(row) for row in rows if isinstance(row, dict) and row.get("id")]


def save_playlists(channel_dir: Path, playlists: list[dict[str, Any]]) -> None:
    path = playlists_path(channel_dir)
    path.write_text(
        json.dumps({"playlists": playlists}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def playlist_label(entry: dict[str, Any]) -> str:
    title = str(entry.get("title_ru") or entry.get("title_en") or entry.get("id", "")).strip()
    return f"📚 Сериал: {title}"


def playlist_by_id(channel_dir: Path, series_id: str) -> dict[str, Any] | None:
    sid = series_id.strip().upper()
    for row in load_playlists(channel_dir):
        if str(row.get("id", "")).strip().upper() == sid:
            return row
    return None


def slugify_playlist(title: str) -> str:
    ascii_text = re.sub(r"[^\w\s-]", "", title, flags=re.ASCII)
    words = re.sub(r"\s+", "_", ascii_text.strip().lower()).strip("_")
    return words[:40] or "series"


def next_playlist_id(channel_dir: Path) -> str:
    numbers: list[int] = []
    for row in load_playlists(channel_dir):
        match = re.fullmatch(r"S(\d+)", str(row.get("id", "")).strip(), flags=re.IGNORECASE)
        if match:
            numbers.append(int(match.group(1)))
    for value in _backlog_ids(channel_dir):
        match = SERIES_ID_RE.fullmatch(value.split("E")[0].upper()) if "E" in value.upper() else None
        if match:
            numbers.append(int(match.group(1)))
    n = max(numbers, default=0) + 1
    return f"S{n:02d}"


def playlist_dir(channel_dir: Path, playlist: dict[str, Any]) -> Path:
    pid = str(playlist.get("id", "S00")).strip()
    slug = str(playlist.get("slug", "series")).strip()
    return channel_dir / "playlists" / f"{pid}_{slug}"


SERIES_BIBLE_JSON = "series_bible.json"
BIBLE_SECTION_KEYS = (
    "world_and_atmosphere",
    "characters_visual_lock",
    "lore_and_rules",
)


def playlist_dir_for_series(channel_dir: Path, series_id: str) -> Path | None:
    row = playlist_by_id(channel_dir, series_id)
    if not row:
        return None
    return playlist_dir(channel_dir, row)


def series_bible_path(channel_dir: Path, series_id: str) -> Path | None:
    pdir = playlist_dir_for_series(channel_dir, series_id)
    if not pdir:
        return None
    return pdir / "series_bible.md"


def series_bible_json_path(channel_dir: Path, series_id: str) -> Path | None:
    pdir = playlist_dir_for_series(channel_dir, series_id)
    if not pdir:
        return None
    return pdir / SERIES_BIBLE_JSON


def producer_brief_path(channel_dir: Path, series_id: str) -> Path | None:
    row = playlist_by_id(channel_dir, series_id)
    if not row:
        return None
    return playlist_dir(channel_dir, row) / "producer_brief.md"


def read_producer_brief(channel_dir: Path, series_id: str) -> str:
    path = producer_brief_path(channel_dir, series_id)
    if path and path.is_file():
        return path.read_text(encoding="utf-8-sig").strip()
    return ""


def save_producer_brief(channel_dir: Path, series_id: str, text: str) -> Path | None:
    path = producer_brief_path(channel_dir, series_id)
    if path is None:
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.strip() + "\n", encoding="utf-8-sig")
    return path


def _bilingual_field(ru: str = "", en: str = "") -> dict[str, str]:
    return {"ru": ru.strip(), "en": en.strip()}


def empty_series_bible_dict(*, title_en: str = "", title_ru: str = "") -> dict[str, Any]:
    return {
        "version": 1,
        "title_en": title_en.strip(),
        "title_ru": title_ru.strip(),
        "world_and_atmosphere": _bilingual_field(),
        "characters_visual_lock": _bilingual_field(),
        "lore_and_rules": _bilingual_field(),
        "recaps": [],
    }


def normalize_series_bible_data(data: dict[str, Any]) -> dict[str, Any]:
    base = empty_series_bible_dict(
        title_en=str(data.get("title_en", "")),
        title_ru=str(data.get("title_ru", "")),
    )
    base["version"] = int(data.get("version", 1) or 1)
    for key in BIBLE_SECTION_KEYS:
        block = data.get(key) if isinstance(data.get(key), dict) else {}
        base[key] = _bilingual_field(
            str(block.get("ru", "")),
            str(block.get("en", "")),
        )
    recaps: list[dict[str, str]] = []
    for item in data.get("recaps") or []:
        if not isinstance(item, dict):
            continue
        ep = str(item.get("episode", "")).strip()
        if not ep:
            continue
        recaps.append(
            {
                "episode": ep,
                "title": str(item.get("title", "")).strip(),
                "summary_ru": str(item.get("summary_ru", "")).strip(),
                "summary_en": str(item.get("summary_en", "")).strip(),
            }
        )
    base["recaps"] = recaps
    base.pop("season_arc", None)
    return base


def format_series_bible_episode_tone_data(data: dict[str, Any]) -> str:
    """Тон/мир/герои/правила для генерации эпизода — без хроники и сюжетных арок."""
    data = normalize_series_bible_data(data)
    blocks: list[str] = []
    labels = {
        "world_and_atmosphere": "World and atmosphere",
        "characters_visual_lock": "Characters (visual lock)",
        "lore_and_rules": "Lore and rules",
    }
    for key, label in labels.items():
        ru = str(data.get(key, {}).get("ru", "")).strip()
        en = str(data.get(key, {}).get("en", "")).strip()
        if ru or en:
            blocks.append(f"### {label}\nRU:\n{ru}\n\nEN:\n{en}")
    return "\n\n".join(blocks) if blocks else "_No bible sections yet._"


def format_series_bible_context_data(data: dict[str, Any]) -> str:
    """Контекст bible для LLM из dict (без перечитывания JSON с диска)."""
    data = normalize_series_bible_data(data)
    blocks: list[str] = []
    tone = format_series_bible_episode_tone_data(data)
    if tone and not tone.startswith("_No"):
        blocks.append(tone)
    recaps = data.get("recaps") or []
    if recaps:
        blocks.append("### Chronicle (all released episodes)")
        for row in recaps:
            ep = row.get("episode", "")
            title = row.get("title", "")
            blocks.append(
                f"#### {ep} — {title}\n"
                f"RU: {row.get('summary_ru', '')}\n"
                f"EN: {row.get('summary_en', '')}"
            )
    else:
        blocks.append("### Chronicle\n_No episodes released yet — this may be episode 1 or a direct sequel idea._")
    return "\n\n".join(blocks)


def format_series_bible_context(playlist_dir: Path) -> str:
    """Контекст bible для LLM: мир, герои, лор и полная хроника выпусков."""
    return format_series_bible_context_data(load_series_bible_json(playlist_dir))


def compile_series_bible_md(playlist_dir: Path, data: dict[str, Any] | None = None) -> str:
    """Собрать EN-markdown для конвейера сценариев и картинок."""
    if data is None:
        data = load_series_bible_json(playlist_dir)
    data = normalize_series_bible_data(data)
    title_en = data.get("title_en") or "Series"
    title_ru = data.get("title_ru") or title_en
    parts = [f"# {title_en}", "", f"_RU: {title_ru}_", ""]

    section_titles = {
        "world_and_atmosphere": "World and tone",
        "characters_visual_lock": "Characters (visual lock)",
        "lore_and_rules": "Lore and rules",
    }
    for key, heading in section_titles.items():
        body = str(data[key].get("en", "")).strip()
        parts.append(f"## {heading}")
        parts.append(body or "_TBD_")
        parts.append("")

    parts.append("## Recap")
    recaps = data.get("recaps") or []
    if not recaps:
        parts.append("_None yet._")
    else:
        for row in recaps:
            ep = row.get("episode", "")
            title = row.get("title", "")
            summary = row.get("summary_en", "")
            parts.append(f"\n### {ep} — {title}")
            parts.append(summary)
    return "\n".join(parts).strip() + "\n"


def load_series_bible_json(playlist_dir: Path) -> dict[str, Any]:
    path = playlist_dir / SERIES_BIBLE_JSON
    if not path.is_file():
        return empty_series_bible_dict()
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError:
        return empty_series_bible_dict()
    if not isinstance(raw, dict):
        return empty_series_bible_dict()
    return normalize_series_bible_data(raw)


def save_series_bible_data(playlist_dir: Path, data: dict[str, Any]) -> Path:
    playlist_dir.mkdir(parents=True, exist_ok=True)
    normalized = normalize_series_bible_data(data)
    json_path = playlist_dir / SERIES_BIBLE_JSON
    json_path.write_text(
        json.dumps(normalized, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8-sig",
    )
    md_path = playlist_dir / "series_bible.md"
    md_path.write_text(compile_series_bible_md(playlist_dir, normalized), encoding="utf-8-sig")
    return json_path


def _legacy_md_has_content(md_text: str) -> bool:
    text = md_text.strip()
    if len(text) < 80:
        return False
    stripped = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    stripped = re.sub(r"\s+", " ", stripped)
    return "## " in text or len(stripped) > 120


def ensure_series_bible_artifacts(channel_dir: Path, series_id: str) -> str | None:
    """
    Убедиться, что есть series_bible.json и актуальный series_bible.md.
    Старый только-.md конвертируется через studio_ai при первом обращении.
    """
    row = playlist_by_id(channel_dir, series_id)
    if not row:
        return f"Плейлист {series_id} не найден."
    root = playlist_dir(channel_dir, row)
    root.mkdir(parents=True, exist_ok=True)
    json_path = root / SERIES_BIBLE_JSON
    md_path = root / "series_bible.md"
    title_en = str(row.get("title_en", "")).strip()
    title_ru = str(row.get("title_ru", "")).strip()

    if json_path.is_file():
        save_series_bible_data(root, load_series_bible_json(root))
        return None

    if md_path.is_file() and _legacy_md_has_content(md_path.read_text(encoding="utf-8-sig")):
        from studio_ai import migrate_legacy_series_bible_md_to_json

        md_text = md_path.read_text(encoding="utf-8-sig")
        data, err = migrate_legacy_series_bible_md_to_json(
            root,
            md_text,
            title_en=title_en,
            title_ru=title_ru,
        )
        if err:
            return err
        save_series_bible_data(root, data)
        return None

    save_series_bible_data(
        root,
        empty_series_bible_dict(title_en=title_en, title_ru=title_ru),
    )
    return None


def ensure_series_bible(channel_dir: Path, playlist: dict[str, Any], *, title_en: str, title_ru: str) -> Path:
    root = playlist_dir(channel_dir, playlist)
    root.mkdir(parents=True, exist_ok=True)
    json_path = root / SERIES_BIBLE_JSON
    if json_path.is_file():
        save_series_bible_data(root, load_series_bible_json(root))
        return root / "series_bible.md"
    if (root / "series_bible.md").is_file():
        ensure_series_bible_artifacts(channel_dir, str(playlist.get("id", "")))
        return root / "series_bible.md"
    save_series_bible_data(root, empty_series_bible_dict(title_en=title_en, title_ru=title_ru))
    return root / "series_bible.md"


def create_playlist(
    channel_dir: Path,
    *,
    title: str,
    title_ru: str = "",
    title_en: str = "",
) -> dict[str, Any]:
    title = title.strip()
    if not title:
        raise ValueError("Укажите название плейлиста.")
    has_cyrillic = bool(re.search(r"[\u0400-\u04FF]", title))
    if has_cyrillic:
        title_ru = title_ru.strip() or title
        title_en = title_en.strip() or title
    else:
        title_en = title_en.strip() or title
        title_ru = title_ru.strip() or title
    pid = next_playlist_id(channel_dir)
    slug = slugify_playlist(title_en)
    entry = {
        "id": pid,
        "title_en": title_en,
        "title_ru": title_ru,
        "slug": slug,
    }
    playlists = load_playlists(channel_dir)
    playlists.append(entry)
    save_playlists(channel_dir, playlists)
    ensure_series_bible(channel_dir, entry, title_en=title_en, title_ru=title_ru)
    return entry


def normalize_backlog_frame(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame(columns=BACKLOG_COLUMNS)
    frame = frame.copy()
    for col in BACKLOG_COLUMNS:
        if col not in frame.columns:
            frame[col] = ""
    if "Series_ID" not in frame.columns and "Series" in frame.columns:
        for idx in frame.index:
            series = str(frame.at[idx, "Series"]).strip()
            series_ru = str(frame.get("Series_RU", pd.Series(dtype=str)).get(idx, "")).strip()
            if series or series_ru:
                frame.at[idx, "Series_ID"] = "_legacy_series_"
            else:
                frame.at[idx, "Series_ID"] = STANDALONE_ID
            frame.at[idx, "Episode_Num"] = frame.at[idx, "Episode_Num"] if str(frame.at[idx, "Episode_Num"]).strip() else "1"
    frame["Series_ID"] = frame["Series_ID"].astype(str).replace({"nan": "", "": STANDALONE_ID})
    empty_mask = frame["Series_ID"].astype(str).str.strip().eq("")
    frame.loc[empty_mask, "Series_ID"] = STANDALONE_ID
    legacy = frame["Series_ID"].astype(str).str.strip().eq("_legacy_series_")
    if legacy.any():
        for idx in frame.index[legacy]:
            frame.at[idx, "Series_ID"] = STANDALONE_ID
            if not str(frame.at[idx, "Episode_Num"]).strip():
                frame.at[idx, "Episode_Num"] = "1"
    return frame.reindex(columns=BACKLOG_COLUMNS, fill_value="")


def _backlog_ids(channel_dir: Path) -> list[str]:
    path = channel_dir / "backlog.csv"
    if not path.is_file():
        return []
    frame = pd.read_csv(path, encoding="utf-8-sig")
    if "ID" not in frame.columns:
        return []
    return [str(v).strip() for v in frame["ID"].astype(str) if str(v).strip() and str(v).lower() != "nan"]


def next_standalone_episode_id(backlog: pd.DataFrame) -> str:
    numbers: list[int] = []
    for value in backlog.get("ID", pd.Series(dtype=str)).astype(str):
        match = STANDALONE_EP_RE.fullmatch(value.strip())
        if match:
            numbers.append(int(match.group(1)))
    n = max(numbers, default=0) + 1
    return f"EP{n:02d}"


def next_series_episode_id(backlog: pd.DataFrame, series_id: str) -> tuple[str, int]:
    """Legacy: max(ID)+1 по backlog (без учёта папок). Предпочтительно: find_next_series_episode_slot."""
    sid = series_id.strip().upper()
    numbers: list[int] = []
    for value in backlog.get("ID", pd.Series(dtype=str)).astype(str):
        match = SERIES_EP_ID_RE.fullmatch(value.strip())
        if match and match.group(1).upper() == sid:
            numbers.append(int(match.group(2)))
    if not numbers and "Series_ID" in backlog.columns and "Episode_Num" in backlog.columns:
        subset = backlog[backlog["Series_ID"].astype(str).str.strip().str.upper().eq(sid)]
        for num in subset.get("Episode_Num", pd.Series(dtype=str)).astype(str):
            if num.strip().isdigit():
                numbers.append(int(float(num.strip())))
    ep_num = max(numbers, default=0) + 1
    return f"{sid}E{ep_num:02d}", ep_num


def backlog_row_by_episode_id(backlog: pd.DataFrame, ep_id: str) -> pd.Series | None:
    if backlog.empty or "ID" not in backlog.columns:
        return None
    match = backlog[backlog["ID"].astype(str).str.strip().eq(ep_id.strip())]
    if match.empty:
        return None
    return match.iloc[0]


def episode_id_from_folder_name(folder_name: str) -> str:
    return folder_name.split("_", 1)[0].strip()


def infer_series_id_from_episode_id(ep_id: str) -> str:
    ep_id = ep_id.strip().upper()
    match = SERIES_EP_ID_RE.fullmatch(ep_id)
    if match:
        return match.group(1).upper()
    return STANDALONE_ID


def row_series_id(row: pd.Series) -> str:
    sid = str(row.get("Series_ID", "") or "").strip()
    if sid and sid.lower() != "nan":
        return sid if sid != "_legacy_series_" else STANDALONE_ID
    ep_id = str(row.get("ID", "")).strip().upper()
    return infer_series_id_from_episode_id(ep_id)


def folder_matches_playlist(folder_name: str, backlog: pd.DataFrame, playlist_id: str) -> bool:
    ep_id = episode_id_from_folder_name(folder_name)
    row = backlog_row_by_episode_id(normalize_backlog_frame(backlog), ep_id) if not backlog.empty else None
    if row is not None:
        rsid = row_series_id(row)
        if playlist_id == STANDALONE_ID:
            return rsid == STANDALONE_ID
        return rsid.upper() == playlist_id.upper()
    if playlist_id == STANDALONE_ID:
        return bool(STANDALONE_EP_RE.fullmatch(ep_id)) or not SERIES_EP_ID_RE.fullmatch(ep_id)
    match = SERIES_EP_ID_RE.fullmatch(ep_id)
    return bool(match and match.group(1).upper() == playlist_id.upper())


def playlist_title_for_id(channel_dir: Path, series_id: str, row: pd.Series | None = None) -> str:
    if series_id == STANDALONE_ID:
        return ""
    pl = playlist_by_id(channel_dir, series_id)
    if pl:
        return str(pl.get("title_ru") or pl.get("title_en") or series_id)
    if row is not None:
        return str(row.get("Series_RU") or row.get("Series") or series_id)
    return series_id


def format_episode_folder_label(
    folder_name: str,
    channel_dir: Path,
    backlog: pd.DataFrame,
) -> str:
    ep_id = episode_id_from_folder_name(folder_name)
    backlog = normalize_backlog_frame(backlog) if not backlog.empty else backlog
    row = backlog_row_by_episode_id(backlog, ep_id) if not backlog.empty else None
    sid = row_series_id(row) if row is not None else infer_series_id_from_episode_id(ep_id)
    if sid == STANDALONE_ID:
        return f"[Одиночный] {folder_name}"
    title = playlist_title_for_id(channel_dir, sid, row)
    ep_num = ""
    if row is not None:
        ep_num = str(row.get("Episode_Num", "") or "").strip()
    if not ep_num and SERIES_EP_ID_RE.fullmatch(ep_id.upper()):
        ep_num = SERIES_EP_ID_RE.fullmatch(ep_id.upper()).group(2).lstrip("0") or "1"
    short_title = title or sid
    part = f" · Серия {ep_num}" if ep_num else ""
    return f"[Сериал: {short_title}{part}] {folder_name}"


def format_backlog_row_label(
    row: pd.Series,
    channel_dir: Path,
) -> str:
    sid = row_series_id(row)
    ep_id = str(row.get("ID", ""))
    topic = str(row.get("Topic", "") or "")
    topic_ru = str(row.get("Topic_RU", "") or "").strip()
    if sid == STANDALONE_ID:
        badge = "[Одиночный]"
    else:
        title = playlist_title_for_id(channel_dir, sid, row)
        ep_num = str(row.get("Episode_Num", "") or "").strip()
        part = f" · Серия {ep_num}" if ep_num else ""
        badge = f"[Сериал: {title}{part}]"
    if topic_ru and topic_ru.lower() != "nan":
        return f"{badge} {ep_id}: {topic} — {topic_ru}"
    return f"{badge} {ep_id}: {topic}"


def read_series_bible(channel_dir: Path, series_id: str) -> str:
    err = ensure_series_bible_artifacts(channel_dir, series_id)
    if err:
        print(f"[series_bible] ensure: {err}", flush=True)
    path = series_bible_path(channel_dir, series_id)
    if path and path.is_file():
        return path.read_text(encoding="utf-8-sig")
    pdir = playlist_dir_for_series(channel_dir, series_id)
    if pdir:
        return compile_series_bible_md(pdir)
    return ""


def extract_recap_section(bible_text: str) -> str:
    if not bible_text.strip():
        return ""
    lower = bible_text.lower()
    marker = "## recap"
    idx = lower.find(marker)
    if idx < 0:
        return ""
    return bible_text[idx:].strip()


def append_recap_block(
    bible_path: Path,
    episode_id: str,
    episode_title: str,
    recap_text: str,
    *,
    recap_ru: str = "",
) -> None:
    """Добавить recap в JSON (+ пересборка .md). recap_text — EN; recap_ru — RU."""
    playlist_dir = bible_path.parent
    data = load_series_bible_json(playlist_dir)
    ep = episode_id.strip().upper()
    recaps = [r for r in data.get("recaps", []) if str(r.get("episode", "")).strip().upper() != ep]
    recaps.append(
        {
            "episode": ep,
            "title": episode_title.strip(),
            "summary_en": recap_text.strip(),
            "summary_ru": (recap_ru or recap_text).strip(),
        }
    )
    data["recaps"] = recaps
    save_series_bible_data(playlist_dir, data)


def slugify_episode_slug(topic: str) -> str:
    ascii_text = re.sub(r"[^\w\s-]", "", topic, flags=re.ASCII)
    words = re.sub(r"\s+", "_", ascii_text.strip().lower()).strip("_")
    return words[:48] or "episode"


def relocate_episode_folder(
    channel_dir: Path,
    old_episode_id: str,
    new_episode_id: str,
    topic: str,
) -> Path:
    """Переименовать episodes/OLD_* → NEW_slug или создать пустую папку NEW."""
    root = channel_dir / "episodes"
    root.mkdir(parents=True, exist_ok=True)
    slug = slugify_episode_slug(topic)
    dest_name = f"{new_episode_id}_{slug}"
    dest = root / dest_name
    old_episode_id = old_episode_id.strip()
    matches = sorted(p for p in root.glob(f"{old_episode_id}_*") if p.is_dir())
    if matches:
        src = matches[0]
        if src.name == dest_name:
            return src
        if dest.exists():
            raise FileExistsError(f"Целевая папка уже есть: {dest_name}")
        src.rename(dest)
        return dest
    if dest.exists():
        raise FileExistsError(f"Папка уже существует: {dest_name}")
    dest.mkdir(parents=True, exist_ok=False)
    return dest


def write_series_bible(channel_dir: Path, series_id: str, content: str | dict[str, Any]) -> Path:
    """Сохранить bible: dict → JSON+MD; str (legacy) → только .md без JSON."""
    pdir = playlist_dir_for_series(channel_dir, series_id)
    if pdir is None:
        raise FileNotFoundError(f"Плейлист {series_id} не найден.")
    if isinstance(content, dict):
        save_series_bible_data(pdir, content)
        return pdir / SERIES_BIBLE_JSON
    path = series_bible_path(channel_dir, series_id)
    if path is None:
        raise FileNotFoundError(f"Плейлист {series_id} не найден.")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(content).strip() + "\n", encoding="utf-8-sig")
    return path


def backlog_row_index(backlog: pd.DataFrame, backlog_id: str) -> int | None:
    if backlog.empty or "ID" not in backlog.columns:
        return None
    match = backlog.index[backlog["ID"].astype(str).str.strip().eq(backlog_id.strip())]
    if match.empty:
        return None
    return int(match[0])


def row_can_expand_to_series(row: pd.Series) -> bool:
    sid = str(row.get("Series_ID", "") or "").strip()
    if not sid or sid.lower() == "nan":
        return True
    return sid == STANDALONE_ID


def episode_dir_for_backlog_id(channel_dir: Path, backlog_id: str) -> Path | None:
    root = channel_dir / "episodes"
    if not root.is_dir():
        return None
    matches = sorted(p for p in root.glob(f"{backlog_id.strip()}_*") if p.is_dir())
    return matches[0] if matches else None


EPISODE_IDEA_FILE = "episode_idea.json"


def write_episode_idea(episode_dir: Path, payload: dict[str, Any]) -> Path:
    episode_dir.mkdir(parents=True, exist_ok=True)
    path = episode_dir / EPISODE_IDEA_FILE
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8-sig",
    )
    return path


def read_episode_idea(episode_dir: Path) -> dict[str, Any]:
    path = episode_dir / EPISODE_IDEA_FILE
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def episode_idea_as_backlog_row(idea: dict[str, Any]) -> pd.Series:
    return pd.Series(
        {
            "ID": str(idea.get("id", "")),
            "Topic": str(idea.get("topic", "")),
            "Topic_RU": str(idea.get("topic_ru", "")),
            "Series_ID": str(idea.get("series_id", STANDALONE_ID)),
            "Episode_Num": str(idea.get("episode_num", "1")),
            "Series": str(idea.get("series_en", "")),
            "Series_RU": str(idea.get("series_ru", "")),
            "Angle": str(idea.get("angle", "")),
        }
    )


def backlog_row_for_episode_dir(
    channel_dir: Path,
    episode_dir: Path,
    backlog: pd.DataFrame | None = None,
) -> pd.Series | None:
    ep_id = episode_id_from_folder_name(episode_dir.name)
    frame = normalize_backlog_frame(backlog) if backlog is not None else pd.DataFrame()
    row = backlog_row_by_episode_id(frame, ep_id) if not frame.empty else None
    if row is not None and row_series_id(row) == STANDALONE_ID:
        return row
    idea = read_episode_idea(episode_dir)
    if idea:
        return episode_idea_as_backlog_row(idea)
    if row is not None:
        return row
    return None


def list_series_episode_dirs(channel_dir: Path, series_id: str) -> list[Path]:
    sid = series_id.strip().upper()
    root = channel_dir / "episodes"
    if not root.is_dir():
        return []
    found: list[tuple[int, Path]] = []
    for path in root.iterdir():
        if not path.is_dir():
            continue
        ep_id = path.name.split("_", 1)[0].upper()
        match = SERIES_EP_ID_RE.fullmatch(ep_id)
        if match and match.group(1).upper() == sid:
            found.append((int(match.group(2)), path))
    found.sort(key=lambda x: x[0])
    return [p for _, p in found]


def next_series_episode_from_folders(channel_dir: Path, series_id: str) -> tuple[str, int]:
    """Следующий ID серии только по папкам episodes/ (backlog не участвует)."""
    sid = series_id.strip().upper()
    nums: list[int] = []
    for path in list_series_episode_dirs(channel_dir, series_id):
        ep_id = path.name.split("_", 1)[0].upper()
        match = SERIES_EP_ID_RE.fullmatch(ep_id)
        if match:
            nums.append(int(match.group(2)))
    ep_num = max(nums, default=0) + 1
    return f"{sid}E{ep_num:02d}", ep_num


def prior_series_episode_lines(channel_dir: Path, series_id: str) -> str:
    lines: list[str] = []
    for path in list_series_episode_dirs(channel_dir, series_id):
        ep_id = path.name.split("_", 1)[0]
        idea = read_episode_idea(path)
        topic = idea.get("topic") or path.name.replace("_", " ")
        topic_ru = idea.get("topic_ru", "")
        angle = idea.get("angle", "")
        lines.append(f"- {ep_id}: {topic} / {topic_ru} ({angle})")
    return "\n".join(lines)


def purge_series_from_backlog(channel_dir: Path, backlog: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """
    Backlog — только идеи для одиночных роликов. Серийные строки снимаем;
    темы с папкой → episode_idea.json.
    """
    backlog = normalize_backlog_frame(backlog)
    if backlog.empty:
        return backlog, 0
    drop_idx: list[int] = []
    for idx, row in backlog.iterrows():
        if row_series_id(row) == STANDALONE_ID:
            continue
        ep_id = str(row.get("ID", "")).strip()
        folder = episode_dir_for_backlog_id(channel_dir, ep_id)
        if folder:
            write_episode_idea(
                folder,
                {
                    "id": ep_id,
                    "topic": str(row.get("Topic", "")),
                    "topic_ru": str(row.get("Topic_RU", "")),
                    "angle": str(row.get("Angle", "")),
                    "series_id": row_series_id(row),
                    "episode_num": str(row.get("Episode_Num", "1")),
                    "series_en": str(row.get("Series", "")),
                    "series_ru": str(row.get("Series_RU", "")),
                },
            )
        drop_idx.append(idx)
    if not drop_idx:
        return backlog, 0
    cleaned = backlog.drop(index=drop_idx).reset_index(drop=True)
    return cleaned, len(drop_idx)


def filter_backlog_by_playlist(backlog: pd.DataFrame, playlist_id: str) -> pd.DataFrame:
    if backlog.empty:
        return backlog
    backlog = normalize_backlog_frame(backlog)
    pid = playlist_id.strip()
    if pid == STANDALONE_ID:
        mask = backlog.apply(lambda r: row_series_id(r) == STANDALONE_ID, axis=1)
        return backlog.loc[mask]
    # В backlog нет серийных идей — эпизоды сериала только в episodes/
    return backlog.iloc[0:0].copy()