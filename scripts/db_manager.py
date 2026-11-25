"""SQLite канала: банк переходов, фразы плейлиста и словарь (legacy).

Isekai-озвучка (`isekai_voice.py`) **не** читает и не пишет БД: только `script.md` + `audio_cache/` эпизода.

Файл: channels/<slug>/database/channel.db
Общие mp3: channels/<slug>/shared_audio/ (legacy)

Команды:
  python scripts/db_manager.py migrate <channel_path>
  python scripts/db_manager.py check <channel_path> --playlist "..."
  python scripts/db_manager.py sync_episode <channel_path> <episode_id> --playlist "..."
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

DB_NAME = "channel.db"
SHARED_DIR = "shared_audio"
JSON_NAMES = ("transitions_bank.json", "playlist_registry.json")

_STOP = {
    "el", "la", "los", "las", "un", "una", "es", "está", "estan", "están",
    "hay", "yo", "tu", "tú", "mi", "mis", "su", "sus", "de", "a", "y", "e", "o",
    "en", "con", "por", "para", "que", "esto", "esta", "son", "soy", "eres",
    "estamos", "del", "al", "se", "lo", "le", "les", "me", "te", "nos", "os",
    "como", "muy", "ya", "no", "si", "sí", "pero", "porque",
}
_GLOSS = {
    "hola": "hello",
    "buenas": "good",
    "noches": "night",
    "amigo": "friend",
    "gato": "cat",
    "guía": "guide",
    "guia": "guide",
    "mateo": "Mateo",
    "aquí": "here",
    "tranquilo": "calm",
    "tranquila": "calm",
    "noche": "night",
    "lluvia": "rain",
    "agua": "water",
    "fuego": "fire",
    "calor": "heat",
    "bosque": "forest",
    "árbol": "tree",
    "grande": "big",
    "antiguo": "ancient",
    "antigua": "ancient",
    "piedra": "stone",
    "templo": "temple",
    "cielo": "sky",
    "luna": "moon",
    "estrella": "star",
    "mirar": "to look",
    "miro": "I look",
    "mira": "look",
    "escuchar": "to listen",
    "escucho": "I listen",
    "escucha": "listen",
    "descansar": "to rest",
    "descansa": "rest",
    "descansas": "you rest",
}


def resolve_channel(path: Path) -> Path:
    """Папка канала: есть channel_config.md. От эпизода поднимаемся вверх."""
    current = path.resolve()
    for folder in (current, *current.parents):
        if (folder / "channel_config.md").is_file():
            return folder
    raise SystemExit(f"Не найден channel_config.md от {path}")


def db_path(channel: Path) -> Path:
    return channel / "database" / DB_NAME


def shared_audio_dir(channel: Path) -> Path:
    return channel / SHARED_DIR


def connect(channel: Path) -> sqlite3.Connection:
    path = db_path(channel)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    _ensure_schema(conn)
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS transitions (
            id INTEGER PRIMARY KEY,
            category TEXT NOT NULL,
            text_en TEXT NOT NULL,
            audio_path TEXT,
            usage_count INTEGER NOT NULL DEFAULT 0,
            UNIQUE(category, text_en)
        );
        CREATE TABLE IF NOT EXISTS phrases (
            id INTEGER PRIMARY KEY,
            playlist_name TEXT NOT NULL,
            episode_id TEXT NOT NULL,
            semantic_id TEXT NOT NULL,
            role TEXT NOT NULL,
            text_target TEXT,
            text_native TEXT,
            audio_target_path TEXT,
            audio_native_path TEXT,
            created_at TEXT NOT NULL,
            UNIQUE(playlist_name, text_target)
        );
        CREATE TABLE IF NOT EXISTS vocab (
            id INTEGER PRIMARY KEY,
            playlist_name TEXT NOT NULL,
            word_target TEXT NOT NULL,
            word_native TEXT,
            first_episode_id TEXT,
            UNIQUE(playlist_name, word_target)
        );
        """
    )
    conn.commit()


def is_target_slot(slot: str) -> bool:
    low = slot.lower()
    if low.startswith("transitions_bank") or "_slow" in low:
        return False
    if "_target_" in low:
        return True
    return low in {"mentor_target", "npc_target"}


def is_native_slot(slot: str) -> bool:
    low = slot.lower()
    return low.startswith("mentor_native") or low.startswith("narrator_native")


def transition_category(slot: str) -> str:
    prefix = "transitions_bank_"
    low = slot.lower()
    if low.startswith(prefix):
        return slot[len(prefix):]
    return ""


def mp3_name(semantic_id: str, slot: str, voice: str, speed: float, text: str) -> str:
    digest = hashlib.md5(f"{voice}\n{format(speed, 'g')}\n{text}".encode("utf-8")).hexdigest()
    return f"{semantic_id}_{slot}_{digest}.mp3"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _rel(channel: Path, file_path: Path) -> str:
    return file_path.resolve().relative_to(channel.resolve()).as_posix()


def _abs(channel: Path, rel: str | None) -> Path | None:
    if not rel:
        return None
    path = Path(rel)
    if not path.is_absolute():
        path = channel / rel
    return path


def find_episode_dir(channel: Path, episode_id: str) -> Path:
    from playlist_manager import list_episode_dirs

    episode_id = episode_id.strip()
    named = [
        path
        for path in list_episode_dirs(channel)
        if path.name == episode_id or path.name.startswith(episode_id)
    ]
    exact = [path for path in named if path.name == episode_id]
    if len(exact) == 1:
        return exact[0]
    if len(named) == 1:
        return named[0]
    if not named:
        raise SystemExit(f"Эпизод не найден: {episode_id}")
    names = ", ".join(path.name for path in named)
    raise SystemExit(f"Неоднозначный эпизод {episode_id}: {names}")


def playlist_name_for_episode(channel: Path, episode_dir: Path) -> str:
    idea = episode_dir / "episode_idea.json"
    if idea.is_file():
        try:
            data = json.loads(idea.read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError:
            data = {}
        title = str(data.get("series_en") or data.get("series") or "").strip()
        if title:
            return title
    return ""


def _content_words(text: str) -> list[str]:
    words: list[str] = []
    for raw in re.findall(r"[^\W\d_]+", text, flags=re.UNICODE):
        word = raw.casefold()
        if len(word) < 2 or word in _STOP:
            continue
        words.append(word)
    return words


# Старая строка банка → мягкая, с вводной связкой в конце.
# «What I just said was:» / «In English, she said:» живут здесь, не в MENTOR_NATIVE.
TRANSITION_REWORD = {
    "Oh, sorry if you didn't understand me.": "Oh, pardon me. I went a little ahead. What I just said was:",
    "Forgive me, I forgot you are new to this world.": "Oh, pardon me, I forgot you are new to this place. What I just said was:",
    "Sorry for slipping back into Spanish again.": "Excuse me, I slipped into Spanish again. In English, I said:",
    "I went ahead in Spanish. Let me come back.": "Excuse me, old habits. I went ahead in Spanish. What I just said was:",
    "That was Spanish. I should have waited.": "Ah, my apologies, traveler. The words came out in Spanish. What I just said was:",
    "Forgive the Spanish. You are still new here.": "Ah, my apologies. You are still new here. In English, I said:",
    "I spoke too quickly in the other tongue.": "Oh, pardon me, I spoke a little too quickly. What I just said was:",
    "Pardon me. The words of this place came out first.": "Excuse me. The words of this place came first. In English, I said:",
    "I forgot to stay with your language for a moment.": "Ah, my apologies, traveler. I stepped away from your language for a moment. What I just said was:",
    "Sorry. I will walk that sentence back for you.": "Oh, pardon me. Let me walk that sentence back. In English, I said:",
    "Don't worry, she is friendly. She said:": "Don't worry, she is friendly. What she just told us is:",
    "She is calm. She told us:": "Her voice is calm. In English, she said:",
    "There is no trouble. Her words were:": "There is no trouble. What she just told us is:",
    "She only greeted the fire. She said:": "She only greeted the fire. In English, she said:",
    "Her voice is quiet. She said:": "Her voice is quiet. What she just told us is:",
    "She means us well. What she said was:": "She means us well. In English, she said:",
    "The guard is kind. She said:": "The guard is kind. What she just told us is:",
    "Nothing to fear. She said:": "She is at ease. In English, she said:",
    "She spoke softly. The words were:": "She spoke softly. What she just told us is:",
    "She is a friend of the temple. She said:": "She is a friend of the temple. In English, she said:",
}

_LEAD_IN_PREFIXES = ("i just said:", "i said:", "she said:", "she told us:")


def _has_lead_in(text: str) -> bool:
    low = text.lower().strip()
    return any(low.startswith(prefix) for prefix in _LEAD_IN_PREFIXES)


def apply_transition_reword(conn: sqlite3.Connection) -> int:
    """Меняет текст существующих строк банка. Новая фраза не должна уже лежать в той же категории."""
    changed = 0
    rows = conn.execute("SELECT id, category, text_en FROM transitions").fetchall()
    for row in rows:
        new = TRANSITION_REWORD.get(row["text_en"])
        if not new or new == row["text_en"]:
            continue
        taken = conn.execute(
            "SELECT id FROM transitions WHERE category = ? AND text_en = ? AND id != ?",
            (row["category"], new, row["id"]),
        ).fetchone()
        if taken is not None:
            continue
        conn.execute("UPDATE transitions SET text_en = ? WHERE id = ?", (new, row["id"]))
        changed += 1
    return changed


def upsert_transition(conn: sqlite3.Connection, category: str, text_en: str) -> None:
    conn.execute(
        """
        INSERT INTO transitions (category, text_en, usage_count)
        VALUES (?, ?, 0)
        ON CONFLICT(category, text_en) DO NOTHING
        """,
        (category, text_en),
    )


def upsert_phrase(
    conn: sqlite3.Connection,
    *,
    playlist_name: str,
    episode_id: str,
    semantic_id: str,
    role: str,
    text_target: str,
    text_native: str,
) -> str:
    """Вставка фразы. Повтор text_target в том же плейлисте не создаёт вторую строку."""
    text_target = text_target.strip()
    if not text_target:
        return "skip"
    row = conn.execute(
        "SELECT id, semantic_id, text_native FROM phrases WHERE playlist_name = ? AND text_target = ?",
        (playlist_name, text_target),
    ).fetchone()
    if row is not None:
        old_native = str(row["text_native"] or "").strip()
        new_native = text_native.strip()
        same_line = str(row["semantic_id"]) == str(semantic_id)
        if new_native and (not old_native or _has_lead_in(old_native) or (same_line and old_native != new_native)):
            conn.execute(
                "UPDATE phrases SET text_native = ? WHERE id = ?",
                (new_native, row["id"]),
            )
        return "duplicate"
    conn.execute(
        """
        INSERT INTO phrases (
            playlist_name, episode_id, semantic_id, role,
            text_target, text_native, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            playlist_name,
            episode_id,
            semantic_id,
            role,
            text_target,
            text_native.strip(),
            _now(),
        ),
    )
    return "inserted"


def upsert_vocab(
    conn: sqlite3.Connection,
    playlist_name: str,
    word_target: str,
    episode_id: str,
) -> None:
    conn.execute(
        """
        INSERT INTO vocab (playlist_name, word_target, word_native, first_episode_id)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(playlist_name, word_target) DO NOTHING
        """,
        (playlist_name, word_target, _GLOSS.get(word_target, ""), episode_id),
    )


def _bundles_from_script(script_text: str) -> tuple[dict[str, dict[str, str]], list[tuple[str, str, str]]]:
    """semantic_id -> {slot: text}; список (semantic_id, slot, text) переходных строк."""
    from isekai_voice import parse_script

    blocks, _plays = parse_script(script_text)
    bundles: dict[str, dict[str, str]] = {}
    transitions: list[tuple[str, str, str]] = []
    for items in blocks.values():
        for item in items:
            if not hasattr(item, "role"):
                continue
            slot_map = bundles.setdefault(item.chunk_id, {})
            slot_map.setdefault(item.role, item.text)
            if transition_category(item.role):
                transitions.append((item.chunk_id, item.role, item.text))
    return bundles, transitions


def sync_episode(channel: Path, episode_id: str, playlist_name: str) -> dict[str, int]:
    channel = resolve_channel(channel)
    playlist_name = playlist_name.strip()
    if not playlist_name:
        raise SystemExit("Нужно имя плейлиста: --playlist")
    episode_dir = find_episode_dir(channel, episode_id)
    script = episode_dir / "script.md"
    if not script.is_file():
        raise SystemExit(f"Нет script.md в {episode_dir}")
    bundles, transition_lines = _bundles_from_script(script.read_text(encoding="utf-8-sig"))
    conn = connect(channel)
    inserted = duplicates = 0
    try:
        apply_transition_reword(conn)
        for semantic_id, slots in bundles.items():
            target_slot = next((slot for slot in slots if is_target_slot(slot)), "")
            if not target_slot:
                continue
            native = next(
                (
                    text
                    for slot, text in slots.items()
                    if is_native_slot(slot) and slot.lower().endswith("_translate")
                ),
                "",
            ) or next((text for slot, text in slots.items() if is_native_slot(slot)), "")
            status = upsert_phrase(
                conn,
                playlist_name=playlist_name,
                episode_id=episode_dir.name,
                semantic_id=semantic_id,
                role=target_slot,
                text_target=slots[target_slot],
                text_native=native,
            )
            if status == "inserted":
                inserted += 1
                for word in _content_words(slots[target_slot]):
                    upsert_vocab(conn, playlist_name, word, episode_dir.name)
            elif status == "duplicate":
                duplicates += 1
        for _semantic_id, slot, text in transition_lines:
            category = transition_category(slot)
            if category and text.strip():
                upsert_transition(conn, category, text.strip())
        _recount_transitions(conn, channel)
        linked = _link_audio(conn, channel, episode_dir)
        conn.commit()
    finally:
        conn.close()
    return {
        "inserted": inserted,
        "duplicates": duplicates,
        "audio_linked": linked,
        "episode": episode_dir.name,
    }


def _recount_transitions(conn: sqlite3.Connection, channel: Path) -> None:
    from playlist_manager import list_episode_dirs

    counts: dict[tuple[str, str], int] = {}
    for episode_dir in list_episode_dirs(channel):
        script = episode_dir / "script.md"
        if script.is_file():
            try:
                _bundles, lines = _bundles_from_script(script.read_text(encoding="utf-8-sig"))
            except SystemExit:
                continue
            for _semantic_id, slot, text in lines:
                category = transition_category(slot)
                if category:
                    key = (category, text.strip())
                    counts[key] = counts.get(key, 0) + 1
    conn.execute("UPDATE transitions SET usage_count = 0")
    for (category, text), count in counts.items():
        conn.execute(
            """
            UPDATE transitions SET usage_count = ?
            WHERE category = ? AND text_en = ?
            """,
            (count, category, text),
        )


def _candidate_files(channel: Path, episode_dir: Path | None, semantic_id: str, slot: str) -> list[Path]:
    found: list[Path] = []
    folders = [shared_audio_dir(channel)]
    if episode_dir is not None:
        folders.append(episode_dir / "audio_cache")
    prefix = f"{semantic_id}_{slot}_"
    for folder in folders:
        if not folder.is_dir():
            continue
        found.extend(path for path in folder.glob(f"{prefix}*.mp3") if path.stat().st_size > 0)
    return found


def _voice_for_slot(channel: Path, slot: str) -> str:
    from isekai_config import load_isekai_settings

    settings = load_isekai_settings(channel)
    low = slot.lower()
    if low.startswith("transitions_bank") or low.startswith("mentor_native") or low.startswith("narrator_native"):
        key = "MENTOR_NATIVE"
    elif low.startswith("mentor_target") or low.startswith("narrator_target"):
        key = "MENTOR_TARGET"
    elif low.startswith("npc_target"):
        key = "NPC_TARGET"
    else:
        return ""
    return settings.get(key, "").strip()


def exact_mp3(
    channel: Path,
    episode_dir: Path | None,
    semantic_id: str,
    slot: str,
    text: str,
    speeds: tuple[float, ...],
) -> Path | None:
    """Ищет уже готовый mp3 по тому же имени, что и кэш озвучки. Файл не создаёт."""
    voice = _voice_for_slot(channel, slot)
    if not voice or not text.strip():
        return None
    folders = [shared_audio_dir(channel)]
    if episode_dir is not None:
        folders.append(episode_dir / "audio_cache")
    for speed in speeds:
        name = mp3_name(semantic_id, slot, voice, speed, text.strip())
        for folder in folders:
            candidate = folder / name
            if candidate.is_file() and candidate.stat().st_size > 0:
                return candidate
    return None


def _link_audio(conn: sqlite3.Connection, channel: Path, episode_dir: Path) -> int:
    linked = 0
    episode_id = episode_dir.name
    rows = conn.execute(
        """
        SELECT id, semantic_id, role, text_target, audio_target_path, audio_native_path
        FROM phrases WHERE episode_id = ?
        """,
        (episode_id,),
    ).fetchall()
    for row in rows:
        if not row["audio_target_path"]:
            target = exact_mp3(
                channel, episode_dir, row["semantic_id"], row["role"], row["text_target"] or "", (0.82, 0.85)
            )
            if target is not None:
                conn.execute(
                    "UPDATE phrases SET audio_target_path = ? WHERE id = ?",
                    (_rel(channel, target), row["id"]),
                )
                linked += 1
        native_slot = _native_slot_in_bundle(episode_dir, row["semantic_id"])
        if native_slot and not row["audio_native_path"]:
            bundles, _lines = _bundles_from_script((episode_dir / "script.md").read_text(encoding="utf-8-sig"))
            native_text = bundles.get(row["semantic_id"], {}).get(native_slot, "")
            native = exact_mp3(channel, episode_dir, row["semantic_id"], native_slot, native_text, (0.90,))
            if native is not None:
                conn.execute(
                    "UPDATE phrases SET audio_native_path = ? WHERE id = ?",
                    (_rel(channel, native), row["id"]),
                )
                linked += 1
    script = episode_dir / "script.md"
    if script.is_file():
        _bundles, lines = _bundles_from_script(script.read_text(encoding="utf-8-sig"))
        for semantic_id, slot, text in lines:
            match = exact_mp3(channel, episode_dir, semantic_id, slot, text, (0.90,))
            if match is None:
                continue
            cur = conn.execute(
                "UPDATE transitions SET audio_path = ? WHERE text_en = ? AND (audio_path IS NULL OR audio_path = '')",
                (_rel(channel, match), text.strip()),
            )
            linked += cur.rowcount
    return linked


def _native_slot_in_bundle(episode_dir: Path, semantic_id: str) -> str:
    script = episode_dir / "script.md"
    if not script.is_file():
        return ""
    try:
        bundles, _lines = _bundles_from_script(script.read_text(encoding="utf-8-sig"))
    except SystemExit:
        return ""
    for slot in bundles.get(semantic_id, {}):
        if is_native_slot(slot):
            return slot
    return ""


def locate_line_audio(
    channel: Path,
    episode_dir: Path,
    *,
    semantic_id: str,
    slot: str,
    text: str,
    voice: str,
    speed: float,
    playlist_name: str = "",
) -> Path | None:
    """Ищет mp3: сначала путь в channel.db, затем shared_audio и audio_cache."""
    channel = resolve_channel(channel)
    name = mp3_name(semantic_id, slot, voice, speed, text) if voice else ""
    if db_path(channel).is_file():
        conn = connect(channel)
        try:
            rel = _lookup_db_audio(
                conn,
                playlist_name=playlist_name,
                episode_id=episode_dir.name,
                semantic_id=semantic_id,
                slot=slot,
                text=text,
            )
        finally:
            conn.close()
        found = _abs(channel, rel)
        if found is not None and found.is_file() and found.stat().st_size > 0:
            if not name or found.name == name or slot.startswith("transitions_bank"):
                return found
            if is_native_slot(slot) or (is_target_slot(slot)):
                return found
    if name:
        for folder in (shared_audio_dir(channel), episode_dir / "audio_cache"):
            candidate = folder / name
            if candidate.is_file() and candidate.stat().st_size > 0:
                return candidate
    hits = _candidate_files(channel, episode_dir, semantic_id, slot)
    return hits[0] if hits else None


def _lookup_db_audio(
    conn: sqlite3.Connection,
    *,
    playlist_name: str,
    episode_id: str,
    semantic_id: str,
    slot: str,
    text: str,
) -> str | None:
    if transition_category(slot):
        row = conn.execute(
            "SELECT audio_path FROM transitions WHERE text_en = ? AND audio_path IS NOT NULL AND audio_path != ''",
            (text.strip(),),
        ).fetchone()
        return str(row["audio_path"]) if row else None
    if is_target_slot(slot) and playlist_name:
        row = conn.execute(
            """
            SELECT audio_target_path FROM phrases
            WHERE playlist_name = ? AND text_target = ?
              AND audio_target_path IS NOT NULL AND audio_target_path != ''
            """,
            (playlist_name, text.strip()),
        ).fetchone()
        return str(row["audio_target_path"]) if row else None
    if is_native_slot(slot):
        row = conn.execute(
            """
            SELECT audio_native_path FROM phrases
            WHERE episode_id = ? AND semantic_id = ?
              AND audio_native_path IS NOT NULL AND audio_native_path != ''
            """,
            (episode_id, semantic_id),
        ).fetchone()
        return str(row["audio_native_path"]) if row else None
    return None


def remember_line_audio(
    channel: Path,
    episode_dir: Path,
    *,
    playlist_name: str,
    semantic_id: str,
    slot: str,
    text: str,
    file_path: Path,
) -> None:
    """Пишет относительный путь mp3 в transitions или phrases. Файл не перемещает."""
    channel = resolve_channel(channel)
    if not file_path.is_file():
        return
    rel = _rel(channel, file_path)
    conn = connect(channel)
    try:
        if transition_category(slot):
            category = transition_category(slot)
            upsert_transition(conn, category, text.strip())
            conn.execute(
                "UPDATE transitions SET audio_path = ? WHERE category = ? AND text_en = ?",
                (rel, category, text.strip()),
            )
        elif is_target_slot(slot) and playlist_name:
            conn.execute(
                """
                UPDATE phrases SET audio_target_path = ?
                WHERE playlist_name = ? AND text_target = ?
                """,
                (rel, playlist_name, text.strip()),
            )
        elif is_native_slot(slot):
            conn.execute(
                """
                UPDATE phrases SET audio_native_path = ?
                WHERE episode_id = ? AND semantic_id = ?
                """,
                (rel, episode_dir.name, semantic_id),
            )
        conn.commit()
    finally:
        conn.close()


def _import_transitions(conn: sqlite3.Connection, payload: dict) -> int:
    categories = payload.get("categories") or {}
    count = 0
    for category, rows in categories.items():
        if not isinstance(rows, list):
            continue
        for text in rows:
            line = str(text).strip()
            if not line:
                continue
            upsert_transition(conn, str(category), line)
            count += 1
    return count


def _import_registry(conn: sqlite3.Connection, payload: dict) -> tuple[str, int]:
    playlists = payload.get("playlists") or {}
    title = ""
    added = 0
    for _key, block in playlists.items():
        if not isinstance(block, dict):
            continue
        title = str(block.get("title") or title).strip() or title
        episodes = block.get("episodes") or {}
        for episode_id, rows in episodes.items():
            if not isinstance(rows, list) or not title:
                continue
            for row in rows:
                if not isinstance(row, dict):
                    continue
                slot = str(row.get("slot") or "")
                text = str(row.get("text") or "")
                if not is_target_slot(slot):
                    continue
                status = upsert_phrase(
                    conn,
                    playlist_name=title,
                    episode_id=str(episode_id),
                    semantic_id=str(row.get("id") or ""),
                    role=slot,
                    text_target=text,
                    text_native="",
                )
                if status == "inserted":
                    added += 1
                    for word in _content_words(text):
                        upsert_vocab(conn, title, word, str(episode_id))
    return title, added


def _backup_json(channel: Path) -> list[str]:
    database = channel / "database"
    backup = database / "backup"
    moved: list[str] = []
    for name in JSON_NAMES:
        source = database / name
        if not source.is_file():
            continue
        backup.mkdir(parents=True, exist_ok=True)
        target = backup / name
        if target.exists():
            target.unlink()
        shutil.move(str(source), str(target))
        moved.append(name)
    return moved


def migrate(channel_path: Path) -> dict:
    channel = resolve_channel(channel_path)
    conn = connect(channel)
    transition_rows = 0
    registry_title = ""
    registry_added = 0
    try:
        bank = channel / "database" / "transitions_bank.json"
        if bank.is_file():
            transition_rows = _import_transitions(conn, json.loads(bank.read_text(encoding="utf-8-sig")))
        registry = channel / "database" / "playlist_registry.json"
        if registry.is_file():
            registry_title, registry_added = _import_registry(
                conn, json.loads(registry.read_text(encoding="utf-8-sig"))
            )
        conn.commit()
    finally:
        conn.close()

    synced: list[dict[str, int | str]] = []
    if registry_title:
        registry_file = channel / "database" / "playlist_registry.json"
        episode_ids: list[str] = []
        if registry_file.is_file():
            payload = json.loads(registry_file.read_text(encoding="utf-8-sig"))
            for block in (payload.get("playlists") or {}).values():
                if isinstance(block, dict):
                    episode_ids.extend(str(key) for key in (block.get("episodes") or {}))
        try:
            maya = find_episode_dir(channel, "EP01_maya_jungle_rain")
        except SystemExit:
            maya = None
        if maya is not None and maya.is_dir() and "EP01_maya_jungle_rain" not in episode_ids:
            episode_ids.append("EP01_maya_jungle_rain")
        for episode_id in episode_ids:
            synced.append(sync_episode(channel, episode_id, registry_title))

    conn = connect(channel)
    try:
        _recount_transitions(conn, channel)
        conn.commit()
    finally:
        conn.close()

    moved = _backup_json(channel)
    summary = {
        "channel": str(channel),
        "db": str(db_path(channel)),
        "transitions_imported": transition_rows,
        "registry_phrases": registry_added,
        "synced": synced,
        "json_backup": moved,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary


def check(channel_path: Path, playlist_name: str) -> dict:
    channel = resolve_channel(channel_path)
    playlist_name = playlist_name.strip()
    conn = connect(channel)
    try:
        phrases = [
            str(row["text_target"])
            for row in conn.execute(
                """
                SELECT text_target FROM phrases
                WHERE playlist_name = ? AND text_target IS NOT NULL AND text_target != ''
                ORDER BY episode_id, semantic_id
                """,
                (playlist_name,),
            )
        ]
        vocab = [
            {"word_target": row["word_target"], "word_native": row["word_native"] or ""}
            for row in conn.execute(
                """
                SELECT word_target, word_native FROM vocab
                WHERE playlist_name = ?
                ORDER BY word_target
                """,
                (playlist_name,),
            )
        ]
        transitions = [
            {
                "id": row["id"],
                "category": row["category"],
                "text_en": row["text_en"],
                "usage_count": row["usage_count"],
            }
            for row in conn.execute(
                """
                SELECT id, category, text_en, usage_count FROM (
                    SELECT id, category, text_en, usage_count,
                           ROW_NUMBER() OVER (
                               PARTITION BY category ORDER BY usage_count ASC, id ASC
                           ) AS rank
                    FROM transitions
                ) WHERE rank <= 3
                ORDER BY category, usage_count, id
                """
            )
        ]
    finally:
        conn.close()
    payload = {
        "playlist": playlist_name,
        "phrases": phrases,
        "vocab": vocab,
        "transitions": transitions,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return payload


def _cmd_sync(channel_path: Path, episode_id: str, playlist_name: str) -> None:
    summary = sync_episode(channel_path, episode_id, playlist_name)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SQLite банка фраз канала")
    sub = parser.add_subparsers(dest="cmd", required=True)

    migrate_cmd = sub.add_parser("migrate", help="JSON и script.md -> channel.db")
    migrate_cmd.add_argument("channel_path")

    check_cmd = sub.add_parser("check", help="Занятые фразы и редкие переходы")
    check_cmd.add_argument("channel_path")
    check_cmd.add_argument("--playlist", required=True)

    sync_cmd = sub.add_parser("sync_episode", help="Записать эпизод в channel.db")
    sync_cmd.add_argument("channel_path")
    sync_cmd.add_argument("episode_id")
    sync_cmd.add_argument("--playlist", required=True)

    args = parser.parse_args(argv)
    if args.cmd == "migrate":
        migrate(Path(args.channel_path))
    elif args.cmd == "check":
        check(Path(args.channel_path), args.playlist)
    elif args.cmd == "sync_episode":
        _cmd_sync(Path(args.channel_path), args.episode_id, args.playlist)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
