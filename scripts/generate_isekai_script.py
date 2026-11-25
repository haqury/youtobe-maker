"""Сценарий isekai-сна: блоки [ID|ROLE|speed] и PLAYLIST_SEQUENCE. Прозу не пишет."""

from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
_ROOT = _SCRIPTS.parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from ai_router import generate_text
from isekai_config import load_isekai_settings
from isekai_voice import build_plan, is_isekai_script, parse_script


def _strip_fence(text: str) -> str:
    body = text.strip()
    if body.startswith("```"):
        lines = body.splitlines()
        lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        body = "\n".join(lines).strip()
    return body


def _split_prompts(raw: str) -> tuple[str, str]:
    marker = "---PROMPTS---"
    if marker not in raw:
        return raw.strip(), ""
    script, prompts = raw.split(marker, 1)
    lines = []
    for line in prompts.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.upper().startswith("PROMPT:"):
            lines.append(stripped)
        else:
            lines.append(f"PROMPT: {stripped}")
    return script.strip(), "\n".join(lines) + ("\n" if lines else "")


def build_messages(settings: dict[str, str], brief: str, episode_name: str) -> list[dict[str, str]]:
    target = settings.get("TARGET_LANG", "es")
    native = settings.get("NATIVE_LANG", "en")
    system = (
        "You write cozy night language-learning scripts for sleep. "
        "Output ONLY the script, then a line ---PROMPTS---, then PROMPT lines. "
        "No markdown fences, no commentary."
    )
    user = f"""Episode folder: {episode_name}
TARGET_LANG: {target}
NATIVE_LANG: {native}
Brief: {brief or "A quiet night. The gray cat guide walks with the listener through a safe place."}

Rules:
- Comprehensible Input, level A1. One new {target} word at a time, then the {native} gloss on the next line.
- Second person, already safe. No questions, no horror, no suddenly, no grammar lecture.
- Roles only MENTOR_NATIVE (about 0.90), MENTOR_TARGET (about 0.82), NPC_TARGET (about 0.84).
- Two blocks, ## BLOCK: ACT_1_... and ## BLOCK: ACT_2_...
- Each spoken line: [001|MENTOR_NATIVE|0.90] text
- After every spoken line: [PAUSE|2.0] or [PAUSE|2.5] or [PAUSE|3.0]
- IDs unique, zero-padded, increasing.
- Header:
# EPISODE: {episode_name}
# TARGET_LANG: {target}
# NATIVE_LANG: {native}
- End with ## PLAYLIST_SEQUENCE and for EACH block these three lines:
PLAY: BLOCK_NAME (mode=full, repeat=1)
PLAY: BLOCK_NAME (mode=target_only, speed_mult=0.92, extra_pause=2.0, repeat=2)
PLAY: BLOCK_NAME (mode=shuffle_target, speed_mult=0.85, extra_pause=7.0, repeat=4)
- About 8 to 12 spoken lines per block. Not a 3-hour unique text. The playlist stretches it.
- {target} sentences must be real A1, short, correct.
- After ---PROMPTS--- write 6 lines starting with PROMPT: 
  Scene only: night forest, cart, or tavern, gray cat in a cloak seen from behind, dim lantern.
  Do not describe a human face. End each line with 16:9, no text.
"""
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def generate(episode_dir: Path) -> int:
    episode_dir = episode_dir.resolve()
    settings = load_isekai_settings(episode_dir)
    if not settings.get("TARGET_LANG"):
        print("В channel_config.md нет TARGET_LANG. Это не канал языка во сне.", file=sys.stderr)
        return 1
    brief_path = episode_dir / "plot_brief.txt"
    brief = brief_path.read_text(encoding="utf-8-sig").strip() if brief_path.is_file() else ""
    text, err = generate_text(build_messages(settings, brief, episode_dir.name), temperature=0.4, max_tokens=4000)
    if err or not text.strip():
        print(err or "Пустой ответ модели.", file=sys.stderr)
        return 1
    script_body, prompts = _split_prompts(_strip_fence(text))
    if not is_isekai_script(script_body):
        draft = episode_dir / "script.isekai.draft.md"
        draft.write_text(script_body + "\n", encoding="utf-8")
        print(f"Ответ не прошёл разметку. Черновик: {draft.name}. script.md не тронут.", file=sys.stderr)
        return 1
    try:
        parse_script(script_body)
        build_plan(script_body)
    except SystemExit as exc:
        draft = episode_dir / "script.isekai.draft.md"
        draft.write_text(script_body + "\n", encoding="utf-8")
        print(f"{exc}. Черновик: {draft.name}. script.md не тронут.", file=sys.stderr)
        return 1
    script_path = episode_dir / "script.md"
    script_path.write_text(script_body.rstrip() + "\n", encoding="utf-8")
    print(f"Записан {script_path.name}")
    if prompts:
        (episode_dir / "visual_prompts.md").write_text(prompts, encoding="utf-8")
        print("Записан visual_prompts.md")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("Использование: python scripts/generate_isekai_script.py <папка эпизода>", file=sys.stderr)
        return 2
    return generate(Path(args[0]))


if __name__ == "__main__":
    raise SystemExit(main())
