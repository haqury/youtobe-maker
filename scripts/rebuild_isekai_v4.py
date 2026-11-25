"""Привести isekai script.md к 4-фазному v4: TARGET/_translate, RECALL, PLAYLIST.

Использование:
  python scripts/rebuild_isekai_v4.py <папка эпизода> --recall
  python scripts/rebuild_isekai_v4.py <папка эпизода> --story-ai   # дописать 73–108 через LLM
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from ai_router import generate_text
from isekai_voice import build_plan, is_isekai_script, parse_script

TARGET_SLOTS: dict[int, list[int]] = {
    1: [1, 7, 11, 17],
    2: [20, 25, 30, 35],
    3: [38, 43, 49, 54],
    4: [57, 60, 63, 66, 69, 72],
    5: [75, 78, 81, 84, 87, 90],
    6: [93, 96, 99, 102, 105, 108],
}

ACT_NAMES: dict[int, str] = {}

LINE_RE = re.compile(
    r"^\[(\d{3})\|(NARRATOR_NATIVE_en(?:_translate|_scene|_lantern)?|NARRATOR_TARGET_es(?:_repeat)?)\|([\d.]+)\]\s*(.*)$"
)
PAUSE_RE = re.compile(r"^\[PAUSE\|([\d.]+)\]$")
BLOCK_RE = re.compile(r"^## BLOCK: (ACT_\d+_\S+)$")


@dataclass
class Scene:
    scene_id: int
    native: str = ""
    target: str = ""
    act_block: str = ""
    target_en: str = ""


def act_for_id(scene_id: int) -> int:
    if scene_id <= 0:
        return 1
    return min(6, (scene_id - 1) // 18 + 1)


def parse_scenes_001_108(text: str) -> dict[int, Scene]:
    current_block = ""
    scenes: dict[int, Scene] = {}
    current_id: int | None = None
    for raw in text.splitlines():
        line = raw.strip()
        m_block = BLOCK_RE.match(line)
        if m_block:
            current_block = m_block.group(1)
            if m_block.group(1).startswith("ACT_"):
                num = int(re.search(r"ACT_(\d)", m_block.group(1)).group(1))
                ACT_NAMES[num] = m_block.group(1).split("_", 2)[-1]
            continue
        if line.startswith("## PLAYLIST"):
            break
        if line.startswith("## BLOCK: RECALL"):
            continue
        m = LINE_RE.match(line)
        if not m:
            continue
        sid = int(m.group(1))
        role = m_group = m.group(2)
        body = m.group(4).strip()
        if sid == 0:
            continue
        if sid > 108:
            continue
        if sid not in scenes:
            scenes[sid] = Scene(sid, act_block=current_block)
        if role == "NARRATOR_NATIVE_en":
            scenes[sid].native = body
        elif role == "NARRATOR_TARGET_es":
            scenes[sid].target = body
    return scenes


def format_story_bundle(sid: int, scene: Scene, *, with_target: bool) -> list[str]:
    lines: list[str] = []
    lines.append(f"[{sid:03d}|NARRATOR_NATIVE_en|0.84] {scene.native}")
    lines.append("[PAUSE|1.5]")
    if with_target and scene.target:
        lines.append(f"[{sid:03d}|NARRATOR_TARGET_es|0.78] {scene.target}")
        lines.append("[PAUSE|1.5]")
        en = scene.target_en or scene.target
        lines.append(f"[{sid:03d}|NARRATOR_NATIVE_en_translate|0.84] {en}")
        lines.append("[PAUSE|2.0]")
    return lines


def _quick_en_gloss(es: str) -> str:
    """Грубый fallback, если нет перевода — LLM должен заменить в recall."""
    return es


def build_story_body(scenes: dict[int, Scene], header_lines: list[str]) -> str:
    out: list[str] = header_lines + [""]
    for act in range(1, 7):
        block = f"ACT_{act}_{ACT_NAMES.get(act, 'SCENE')}"
        out.append(f"## BLOCK: {block}")
        if act == 1:
            out.extend(_hook_block(scenes))
        start = (act - 1) * 18 + 1
        end = act * 18
        slots = set(TARGET_SLOTS[act])
        for sid in range(start, end + 1):
            scene = scenes.get(sid)
            if not scene or not scene.native:
                continue
            out.extend(format_story_bundle(sid, scene, with_target=sid in slots))
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def _hook_block(scenes: dict[int, Scene]) -> list[str]:
    s1 = scenes.get(1)
    native_hint = (s1.native[:120] + "…") if s1 and s1.native else "The night is slow and safe."
    return [
        "[000|NARRATOR_NATIVE_en|0.88] Let the day slip from your shoulders, and let your hands rest open. Nothing you did today needs you now. The night can be slow.",
        "[PAUSE|3.5]",
        "[000|NARRATOR_NATIVE_en_scene|0.88] The air of this place is close and mild. A small sound repeats, and the dark around you stays kind.",
        "[PAUSE|4.0]",
        "[000|NARRATOR_NATIVE_en_lantern|0.88] A low warm light touches what is nearest. The rest of the path can wait.",
        "[PAUSE|3.5]",
    ]


def recall_prompt(batch: list[Scene], target_lang: str) -> list[dict[str, str]]:
    items = []
    for sc in batch:
        items.append(f"ID {sc.scene_id:03d} EN scene:\n{sc.native}")
    user = (
        "For each ID, output exactly one block in this format (no markdown fences):\n"
        "[ID|NARRATOR_NATIVE_en|0.88] 2-3 sentences, 25-35 English words, second person.\n"
        "[PAUSE|2.0]\n"
        f"[ID|NARRATOR_TARGET_{target_lang}|0.78] 4-5 simple A1 Spanish sentences, 30-40 words, full meaning of the EN scene.\n"
        "[PAUSE|2.0]\n"
        f"[ID|NARRATOR_TARGET_{target_lang}_repeat|0.74] exact repeat of TARGET line.\n"
        "[PAUSE|3.0]\n\n"
        "IDs must start at "
        + f"{batch[0].scene_id:03d} and be consecutive.\n\n"
        + "\n\n".join(items)
    )
    return [
        {
            "role": "system",
            "content": "You write sleep-language recall blocks. Output only the tagged lines.",
        },
        {"role": "user", "content": user},
    ]


def _trim_words(text: str, n: int) -> str:
    words = text.split()
    if len(words) <= n:
        return text.strip()
    return " ".join(words[:n]).strip() + "…"


def _spanish_recall_from_story(story_id: int, native: str, target_hint: str) -> str:
    short = _trim_words(target_hint or native, 8).rstrip("…")
    parts = [
        "Tú estás aquí.",
        short if short and not short.startswith("Tú") else "La noche es tranquila.",
        "El aire es suave.",
        "El sonido es lento.",
        "Tú puedes descansar.",
    ]
    return " ".join(parts[:5])


def generate_recall_mechanical(scenes: dict[int, Scene]) -> str:
    blocks: list[str] = []
    for recall_num, (lo, hi) in enumerate([(109, 144), (145, 180), (181, 216)], start=1):
        blocks.append(f"## BLOCK: RECALL_{recall_num}")
        for recall_id in range(lo, hi + 1):
            story_id = recall_id - 108
            src = scenes.get(story_id)
            if not src or not src.native:
                continue
            en = _trim_words(src.native, 32)
            es = _spanish_recall_from_story(story_id, src.native, src.target)
            blocks.append(f"[{recall_id:03d}|NARRATOR_NATIVE_en|0.88] {en}")
            blocks.append("[PAUSE|2.0]")
            blocks.append(f"[{recall_id:03d}|NARRATOR_TARGET_es|0.78] {es}")
            blocks.append("[PAUSE|2.0]")
            blocks.append(f"[{recall_id:03d}|NARRATOR_TARGET_es_repeat|0.78] {es}")
            blocks.append("[PAUSE|3.0]")
        blocks.append("")
    return "\n".join(blocks)


def generate_recall(scenes: dict[int, Scene], target_lang: str = "es") -> str:
    blocks: list[str] = []
    for recall_num, (lo, hi) in enumerate([(109, 144), (145, 180), (181, 216)], start=1):
        blocks.append(f"## BLOCK: RECALL_{recall_num}")
        chunk: list[Scene] = []
        for recall_id in range(lo, hi + 1):
            story_id = recall_id - 108
            src = scenes.get(story_id)
            if not src or not src.native:
                continue
            chunk.append(
                Scene(recall_id, native=src.native, target=src.target, act_block=src.act_block)
            )
        for i in range(0, len(chunk), 6):
            batch = chunk[i : i + 6]
            text, err = generate_text(recall_prompt(batch, target_lang), temperature=0.35, max_tokens=8000)
            if err or not text.strip():
                raise SystemExit(err or "Пустой recall от модели")
            blocks.append(text.strip())
        blocks.append("")
    return "\n".join(blocks)


def playlist_block(act_names: dict[int, str]) -> str:
    a1 = f"ACT_1_{act_names.get(1, 'SCENE')}"
    a2 = f"ACT_2_{act_names.get(2, 'SCENE')}"
    a3 = f"ACT_3_{act_names.get(3, 'SCENE')}"
    a4 = f"ACT_4_{act_names.get(4, 'SCENE')}"
    a5 = f"ACT_5_{act_names.get(5, 'SCENE')}"
    a6 = f"ACT_6_{act_names.get(6, 'SCENE')}"
    return "\n".join(
        [
            "## PLAYLIST_SEQUENCE",
            f"PLAY: {a1}, {a2}, {a3} (mode=full, repeat=1)",
            f"PLAY: {a4}, {a5}, {a6} (mode=full, repeat=1)",
            "PLAY: RECALL_1, RECALL_2, RECALL_3 (mode=full, repeat=1)",
            "PLAY: RECALL_1, RECALL_2, RECALL_3 (mode=target_only, speed_mult=0.88, extra_pause=4.0, repeat=1)",
            "",
        ]
    )


def story_extend_prompt(missing: list[int], brief: str, last_native: str) -> list[dict[str, str]]:
    ids = ", ".join(f"{i:03d}" for i in missing)
    return [
        {
            "role": "system",
            "content": "Write isekai sleep script lines. Output ONLY [ID|NARRATOR_NATIVE_en|0.84] lines with [PAUSE|1.5] after each.",
        },
        {
            "role": "user",
            "content": f"BRIEF: {brief}\nContinue story after:\n{last_native}\n\nWrite native English for IDs {ids}. Second person, sensory, calm. One [001|...] block per ID.",
        },
    ]


def extend_story_ai(scenes: dict[int, Scene], brief: str) -> None:
    missing = [i for i in range(1, 109) if i not in scenes or not scenes[i].native]
    if not missing:
        return
    last_id = max(i for i in scenes if scenes[i].native)
    text, err = generate_text(
        story_extend_prompt(missing, brief, scenes[last_id].native),
        temperature=0.4,
        max_tokens=12000,
    )
    if err:
        raise SystemExit(err)
    for m in LINE_RE.finditer(text):
        sid = int(m.group(1))
        if m.group(2) != "NARRATOR_NATIVE_en" or sid > 108:
            continue
        if sid not in scenes:
            scenes[sid] = Scene(sid)
        scenes[sid].native = m.group(4).strip()
    for sid in missing:
        if sid not in scenes or not scenes[sid].native:
            raise SystemExit(f"Модель не дописала сцену {sid:03d}")
        act = act_for_id(sid)
        scenes[sid].act_block = f"ACT_{act}_{ACT_NAMES.get(act, 'SCENE')}"
        if sid in {n for a in TARGET_SLOTS for n in TARGET_SLOTS[a]}:
            scenes[sid].target = _default_target_for_act(sid, scenes[sid].native)


def _default_target_for_act(sid: int, native: str) -> str:
    """Короткая A1-заготовка — QA/автор может поправить позже."""
    seeds = [
        "La lluvia es suave.",
        "El aire es cálido.",
        "La piedra es fría.",
        "La noche es larga.",
        "Tú caminas despacio.",
        "El fuego es pequeño.",
    ]
    return seeds[sid % len(seeds)]


def extract_header(text: str) -> list[str]:
    lines: list[str] = []
    for raw in text.splitlines():
        if raw.startswith("## BLOCK:"):
            break
        if raw.strip().startswith("# MIGRATION:"):
            continue
        lines.append(raw)
    if not any("Оценка хронометража" in x for x in lines):
        lines.append(
            "# Оценка хронометража: фаза 1 ~28 мин, фаза 2 ~29 мин, фаза 3 ~105 мин, "
            "фаза 4 ~44 мин чистого испанского без повтора, речь ~206 мин. Остаток до 600 минут — тихий дождь на сборке."
        )
    return lines


def translate_targets_batch(scenes: dict[int, Scene]) -> None:
    """Заполняет target_en для выбранных слотов одним запросом к LLM."""
    need = [
        sc
        for sid in range(1, 109)
        for sc in [scenes.get(sid)]
        if sc and sc.target and sid in TARGET_SLOTS[act_for_id(sid)]
    ]
    if not need:
        return
    pairs = "\n".join(f"{sc.scene_id:03d}|ES: {sc.target}" for sc in need)
    text, err = generate_text(
        [
            {
                "role": "system",
                "content": "Translate each Spanish line to one short English sentence. Format: 001|EN: ...",
            },
            {"role": "user", "content": pairs},
        ],
        temperature=0.2,
        max_tokens=4000,
    )
    if err:
        return
    for line in text.splitlines():
        m = re.match(r"(\d{3})\|EN:\s*(.+)", line.strip())
        if not m:
            continue
        sid = int(m.group(1))
        if sid in scenes:
            scenes[sid].target_en = m.group(2).strip()


def pad_scenes_to_108(scenes: dict[int, Scene]) -> None:
    for sid in range(1, 109):
        if scenes.get(sid) and scenes[sid].native:
            continue
        src_id = ((sid - 1) % 48) + 1
        src = scenes.get(src_id)
        if not src or not src.native:
            src_id = max(scenes)
            src = scenes[src_id]
        suffix = " The night does not hurry." if sid > 72 else ""
        act = act_for_id(sid)
        target = ""
        if sid in TARGET_SLOTS[act]:
            target = src.target or _default_target_for_act(sid, src.native)
        scenes[sid] = Scene(
            sid,
            native=(src.native + suffix).strip(),
            target=target,
            act_block=f"ACT_{act}_{ACT_NAMES.get(act, 'SCENE')}",
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Пересборка isekai script.md под v4")
    parser.add_argument("episode_dir", type=Path)
    parser.add_argument("--recall", action="store_true", help="Добавить RECALL_1–3")
    parser.add_argument(
        "--recall-llm",
        action="store_true",
        help="RECALL через LLM (иначе механическая черновая сборка)",
    )
    parser.add_argument("--story-ai", action="store_true", help="Дописать пропущенные 001-108")
    parser.add_argument("--skip-llm", action="store_true", help="Без вызовов LLM (перевод _translate = первая фраза native)")
    parser.add_argument("--no-write", action="store_true")
    args = parser.parse_args(argv)
    episode_dir = args.episode_dir.resolve()
    script_path = episode_dir / "script.md"
    if not script_path.is_file():
        raise SystemExit(f"Нет {script_path}")
    text = script_path.read_text(encoding="utf-8-sig")
    brief_m = re.search(r"^# BRIEF:\s*(.+)$", text, re.M)
    brief = brief_m.group(1).strip() if brief_m else ""

    ACT_NAMES.clear()
    for m in BLOCK_RE.finditer(text):
        if m.group(1).startswith("ACT_"):
            num = int(re.search(r"ACT_(\d)", m.group(1)).group(1))
            if num <= 6:
                ACT_NAMES[num] = m.group(1).split("_", 2)[-1]

    scenes = parse_scenes_001_108(text)
    pad_scenes_to_108(scenes)
    if args.story_ai:
        extend_story_ai(scenes, brief)
    for sid in range(1, 109):
        act = act_for_id(sid)
        if sid in TARGET_SLOTS[act] and scenes.get(sid) and not scenes[sid].target:
            scenes[sid].target = _default_target_for_act(sid, scenes[sid].native)

    if args.skip_llm:
        for sid in range(1, 109):
            sc = scenes.get(sid)
            if sc and sc.target and sid in TARGET_SLOTS[act_for_id(sid)]:
                first = sc.native.split(".")[0].strip()
                sc.target_en = first + "." if first and not first.endswith(".") else first
    else:
        translate_targets_batch(scenes)

    header = extract_header(text)
    body = build_story_body(scenes, header)
    if args.recall:
        if args.recall_llm:
            body += "\n" + generate_recall(scenes, "es")
        else:
            body += "\n" + generate_recall_mechanical(scenes)
    body += playlist_block(ACT_NAMES)

    if not args.no_write:
        script_path.write_text(body, encoding="utf-8")
        print(f"Записан {script_path}")
    if args.recall:
        if not is_isekai_script(body):
            raise SystemExit("Результат не прошёл is_isekai_script")
        parse_script(body)
        build_plan(body)
        print("Проверка parse_script / build_plan: OK")
    else:
        print("Сюжет готов. Для полного v4 добавь флаг --recall.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())