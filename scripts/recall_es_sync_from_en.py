"""Пересинхронизировать RECALL NARRATOR_TARGET_es с EN native той же связки (1:1, меньше Tú)."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from ai_router import generate_text
from polish_isekai_v4_standards import soften_tu

BUNDLE_START = re.compile(
    r"^\[(\d{3})\|NARRATOR_NATIVE_en\|([\d.]+)\]\s*(.*)$"
)
LINE_RE = re.compile(r"^\[(\d{3})\|(NARRATOR_[^\|]+)\|([\d.]+)\]\s*(.*)$")


def parse_recall_bundles(text: str) -> list[tuple[int, str]]:
    bundles: list[tuple[int, str]] = []
    in_recall = False
    for raw in text.splitlines():
        if raw.startswith("## BLOCK: RECALL"):
            in_recall = True
            continue
        if raw.startswith("## PLAYLIST"):
            break
        if not in_recall:
            continue
        m = BUNDLE_START.match(raw)
        if m:
            sid = int(m.group(1))
            if 109 <= sid <= 216:
                bundles.append((sid, m.group(3).strip()))
    return bundles


def rebuild_recall_targets(script: Path, *, batch: int = 8, dry_run: bool = False) -> None:
    text = script.read_text(encoding="utf-8")
    bundles = parse_recall_bundles(text)
    if not bundles:
        raise SystemExit("No RECALL bundles found")
    new_es: dict[int, str] = {}
    for i in range(0, len(bundles), batch):
        chunk = bundles[i : i + batch]
        items = "\n\n".join(f"ID {sid:03d}:\n{en}" for sid, en in chunk)
        prompt = [
            {
                "role": "system",
                "content": (
                    "You write A1 European Spanish for sleep audio. Rules: present tense; "
                    "4-5 short sentences, 30-40 words; translate ONLY what is in the English "
                    "(no new objects or words); do NOT start every sentence with Tú — use tú "
                    "at most once per block; otherwise drop the pronoun. Output ONLY lines:\n"
                    "[ID|ES] Spanish text\none per ID, no markdown."
                ),
            },
            {"role": "user", "content": items},
        ]
        out, err = generate_text(prompt, temperature=0.25, max_tokens=6000)
        if err or not out.strip():
            raise SystemExit(err or "empty LLM")
        for line in out.splitlines():
            line = line.strip()
            m = re.match(r"^\[(\d{3})\|ES\]\s*(.*)$", line)
            if not m:
                m = re.match(r"^\[(\d{3})\|.*\]\s*(.*)$", line)
            if m:
                sid = int(m.group(1))
                es = soften_tu(m.group(2).strip(), max_tu=1)
                new_es[sid] = es

    if dry_run:
        for k in sorted(new_es)[:5]:
            print(k, new_es[k][:80])
        return

    lines = text.splitlines()
    out: list[str] = []
    for raw in lines:
        m = LINE_RE.match(raw)
        if m:
            sid = int(m.group(1))
            role = m.group(2)
            if sid >= 109 and role in ("NARRATOR_TARGET_es", "NARRATOR_TARGET_es_repeat"):
                if sid in new_es:
                    spd = m.group(3)
                    body = new_es[sid]
                    out.append(f"[{sid:03d}|{role}|{spd}] {body}")
                    continue
        out.append(raw)
    script.write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"Updated {len(new_es)} recall ES lines in {script}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("script", type=Path)
    ap.add_argument("--batch", type=int, default=8)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    rebuild_recall_targets(args.script, batch=args.batch, dry_run=args.dry_run)


if __name__ == "__main__":
    main()