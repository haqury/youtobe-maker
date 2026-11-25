"""Применить темпы/паузы v4 и смягчить «Tú» в NARRATOR_TARGET_es (isekai script.md)."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

LINE_RE = re.compile(
    r"^(\[(\d{3})\|(NARRATOR_[^\|]+)\|)([\d.]+)(\]\s*)(.*)$"
)
PAUSE_RE = re.compile(r"^\[PAUSE\|([\d.]+)\]$")

HOOK_LANTERN = (
    "[000|NARRATOR_NATIVE_en_lantern|0.88] Most of the night stays in English, and now and then "
    "a short Spanish phrase passes through, easy and slow. You can let the words arrive without holding them."
)


def story_act(sid: int) -> int | None:
    if sid == 0:
        return 0
    if 1 <= sid <= 108:
        return min(6, (sid - 1) // 18 + 1)
    return None


def speed_for(role: str, sid: int) -> float | None:
    if sid == 0 and role.startswith("NARRATOR_NATIVE"):
        return 0.88
    if 109 <= sid <= 216:
        if role == "NARRATOR_NATIVE_en":
            return 0.80
        if role == "NARRATOR_TARGET_es":
            return 0.75
        if role == "NARRATOR_TARGET_es_repeat":
            return 0.70
        return None
    act = story_act(sid)
    if act is None:
        return None
    if role == "NARRATOR_NATIVE_en":
        return 0.85 if act <= 3 else 0.82
    if role == "NARRATOR_NATIVE_en_translate":
        return 0.85 if act <= 3 else 0.82
    if role == "NARRATOR_TARGET_es":
        return 0.78 if act <= 3 else 0.75
    return None


def soften_tu(text: str, *, max_tu: int = 1) -> str:
    """Убрать лишние «Tú» в начале предложений; оставить не больше max_tu на всю реплику."""
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    if not sentences:
        return text
    tu_count = 0
    out: list[str] = []
    for sent in sentences:
        s = sent.strip()
        if re.match(r"^Tú\b", s, re.I):
            if tu_count < max_tu:
                tu_count += 1
                out.append(s)
            else:
                s2 = re.sub(r"^Tú\s+", "", s, flags=re.I)
                if s2:
                    s2 = s2[0].upper() + s2[1:] if len(s2) > 1 else s2.upper()
                out.append(s2)
        else:
            out.append(s)
    return " ".join(out)


def sync_target_repeats(lines: list[str]) -> list[str]:
    last_target: dict[int, str] = {}
    out: list[str] = []
    for raw in lines:
        m = LINE_RE.match(raw)
        if m:
            sid = int(m.group(2))
            role = m.group(3)
            body = m.group(6)
            if role == "NARRATOR_TARGET_es":
                last_target[sid] = body
            if role == "NARRATOR_TARGET_es_repeat" and sid in last_target:
                raw = f"{m.group(1)}{m.group(4)}{m.group(5)}{last_target[sid]}"
        out.append(raw)
    return out


def polish_lines(lines: list[str]) -> list[str]:
    out: list[str] = []
    i = 0
    in_recall = False
    while i < len(lines):
        raw = lines[i]
        if raw.startswith("## BLOCK: RECALL"):
            in_recall = True
        if raw.startswith("## PLAYLIST_SEQUENCE"):
            break

        if raw.startswith("[000|NARRATOR_NATIVE_en_lantern|"):
            out.append(HOOK_LANTERN)
            i += 1
            continue

        m = LINE_RE.match(raw)
        if m:
            sid = int(m.group(2))
            role = m.group(3)
            body = m.group(6)
            spd = speed_for(role, sid)
            if spd is not None:
                raw = f"{m.group(1)}{spd:.2f}{m.group(5)}{body}"

            if role == "NARRATOR_TARGET_es":
                max_tu = 1 if ("." in body or "?" in body or "!" in body) else 0
                if in_recall:
                    max_tu = 1
                body = soften_tu(body, max_tu=max_tu)
                raw = f"{m.group(1)}{spd:.2f}{m.group(5)}{body}" if spd else raw

        out.append(raw)

        # Bundle-end pauses after translate or recall repeat
        if m:
            sid = int(m.group(2))
            role = m.group(3)
            if i + 1 < len(lines):
                pm = PAUSE_RE.match(lines[i + 1].strip())
                if pm:
                    act = story_act(sid)
                    new_pause = None
                    if role == "NARRATOR_NATIVE_en_translate" and act:
                        new_pause = 2.0 if act <= 3 else 2.5
                    elif role == "NARRATOR_TARGET_es_repeat" and sid >= 109:
                        new_pause = 4.0
                    if new_pause is not None:
                        out.append(f"[PAUSE|{new_pause:.1f}]")
                        i += 2
                        continue
        i += 1

    # Playlist tail
    while i < len(lines):
        line = lines[i]
        if "target_only" in line:
            line = re.sub(
                r"speed_mult=[\d.]+,\s*extra_pause=[\d.]+",
                "speed_mult=0.85, extra_pause=5.0",
                line,
            )
        out.append(line)
        i += 1
    return sync_target_repeats(out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("script", type=Path)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    text = args.script.read_text(encoding="utf-8")
    new_text = "\n".join(polish_lines(text.splitlines())) + ("\n" if text.endswith("\n") else "")
    if args.dry_run:
        print(new_text[:2000])
        return
    args.script.write_text(new_text, encoding="utf-8")
    print(f"Updated {args.script}")


if __name__ == "__main__":
    main()