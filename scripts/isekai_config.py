"""Машинные ключи isekai-канала из channel_config.md (TARGET_LANG, голоса, BASE_STYLE_PROMPT)."""

from __future__ import annotations

import re
from pathlib import Path

CONFIG_NAME = "channel_config.md"
ISEKAI_KEYS = (
    "TARGET_LANG",
    "NATIVE_LANG",
    "BASE_STYLE_PROMPT",
    "MENTOR_NATIVE",
    "MENTOR_TARGET",
    "NPC_TARGET",
    "SPEECH_SPEED_BIAS",
    "SPEECH_PITCH",
)

# Короткие списки для студии. Первый мужской es-ES — самый мягкий из доступных в edge-tts.
SLEEP_VOICES: dict[str, tuple[tuple[str, str], ...]] = {
    "es": (
        ("es-ES-AlvaroNeural", "Альваро — мужской, Испания, мягкий"),
        ("es-MX-JorgeNeural", "Хорхе — мужской, Мексика"),
        ("es-US-AlonsoNeural", "Алонсо — мужской, США"),
        ("es-ES-XimenaNeural", "Химена — женский, Испания"),
        ("es-ES-ElviraNeural", "Эльвира — женский, Испания"),
    ),
    "en": (
        ("en-US-BrianNeural", "Брайан — мужской, спокойный"),
        ("en-US-AndrewMultilingualNeural", "Эндрю — мужской"),
        ("en-US-GuyNeural", "Гай — мужской"),
        ("en-GB-RyanNeural", "Райан — мужской, британский"),
        ("en-US-JennyNeural", "Дженни — женский"),
        ("en-US-AriaNeural", "Ария — женский"),
    ),
}


def find_channel_config(start: Path) -> Path | None:
    """Идём от папки вверх: эпизод channels/<slug>/episodes/<id>/ -> channels/<slug>/channel_config.md."""
    try:
        current = start.resolve()
    except OSError:
        return None
    for folder in (current, *current.parents):
        candidate = folder / CONFIG_NAME
        if candidate.is_file():
            return candidate
    return None


def load_isekai_settings(channel_dir: Path) -> dict[str, str]:
    """Первые строки `KEY: value` или `KEY = value`. Пусто, если файла или ключей нет."""
    try:
        config = find_channel_config(Path(channel_dir))
        if config is None:
            return {}
        text = config.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError):
        return {}
    settings: dict[str, str] = {}
    for key in ISEKAI_KEYS:
        match = re.search(rf"^[ \t]*{key}[ \t]*[:=][ \t]*(\S.*?)[ \t]*$", text, flags=re.MULTILINE)
        if match:
            settings[key] = match.group(1).strip()
    return settings


def base_style_prompt(channel_dir: Path) -> str:
    return load_isekai_settings(channel_dir).get("BASE_STYLE_PROMPT", "")


def write_isekai_settings(channel_dir: Path, updates: dict[str, str]) -> Path:
    """Меняет только переданные строки KEY: value в channel_config.md."""
    config = find_channel_config(Path(channel_dir))
    if config is None:
        raise FileNotFoundError(f"Нет channel_config.md рядом с {channel_dir}")
    text = config.read_text(encoding="utf-8-sig")
    for key, value in updates.items():
        if key not in ISEKAI_KEYS:
            continue
        clean = " ".join(str(value).split())
        pattern = rf"^[ \t]*{key}[ \t]*[:=][ \t]*\S.*?[ \t]*$"
        line = f"{key}: {clean}"
        if re.search(pattern, text, flags=re.MULTILINE):
            text = re.sub(pattern, line, text, count=1, flags=re.MULTILINE)
        else:
            if not text.endswith("\n"):
                text += "\n"
            text += line + "\n"
    config.write_text(text, encoding="utf-8")
    return config


def speech_speed_bias(settings: dict[str, str]) -> float:
    """Множитель темпа TTS по channel_config (1.0 = как в script.md). Только isekai render."""
    raw = (settings.get("SPEECH_SPEED_BIAS") or "1").strip()
    try:
        value = float(raw)
    except ValueError:
        return 1.0
    if not 0.5 <= value <= 1.0:
        return 1.0
    return value


def speech_pitch(settings: dict[str, str]) -> str:
    """Тон edge-tts. Ниже нуля — мягче. По умолчанию -4 Гц."""
    raw = (settings.get("SPEECH_PITCH") or "-4Hz").strip()
    match = re.fullmatch(r"([+-]?\d+)Hz", raw)
    if not match:
        return "-4Hz"
    hz = max(-16, min(4, int(match.group(1))))
    if hz > 0:
        return f"+{hz}Hz"
    return f"{hz}Hz"
