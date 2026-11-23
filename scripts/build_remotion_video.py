"""Сборка final_video.mp4 через Remotion (React)."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
ROOT = _SCRIPTS.parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from animation_settings import load_episode_animation
from job_progress import emit_progress
from remotion_env import (
    REMOTION_DIR,
    ensure_npm_install,
    node_status,
    remotion_settings_from_animation,
    stage_episode_assets,
)
from stdio_utf8 import configure_utf8_stdio, subprocess_env

RENDERED_RE = re.compile(r"Rendered\s+(\d+)\s*/\s*(\d+)", re.IGNORECASE)
ENCODE_RE = re.compile(r"(\d+)\s*%", re.IGNORECASE)


def write_props(episode_dir: Path, props_path: Path) -> None:
    anim = load_episode_animation(episode_dir)
    staged = stage_episode_assets(episode_dir)
    payload = {
        **staged,
        "settings": remotion_settings_from_animation(anim),
    }
    props_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def parse_progress_line(line: str, total_hint: int) -> tuple[int, int, str] | None:
    match = RENDERED_RE.search(line)
    if match:
        current = int(match.group(1))
        total = int(match.group(2))
        return current, max(total, 1), "Remotion: кадры"
    match = ENCODE_RE.search(line)
    if match and "%" in line:
        pct = int(match.group(1))
        return pct, 100, "Remotion: кодирование"
    if "Bundling" in line or "bundl" in line.lower():
        return 0, total_hint, "Remotion: bundle"
    return None


def run_render(output: Path, props_path: Path) -> int:
    ok, msg = ensure_npm_install()
    if not ok:
        print(f"[ERROR] {msg}", file=sys.stderr)
        return 1

    cmd = [
        "npx",
        "remotion",
        "render",
        "src/index.ts",
        "SleepStoryComposition",
        str(output),
        f"--props={props_path}",
    ]
    total_hint = 100
    emit_progress(0, total_hint, "Remotion: старт рендера")
    proc = subprocess.Popen(
        cmd,
        cwd=str(REMOTION_DIR),
        env=subprocess_env(),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    last_emit = (-1, "")
    assert proc.stdout is not None
    for line in proc.stdout:
        line = line.rstrip()
        if line:
            print(line, flush=True)
        parsed = parse_progress_line(line, total_hint)
        if parsed:
            current, total, text = parsed
            if total != total_hint:
                total_hint = total
            key = (current, text)
            if key != last_emit:
                emit_progress(current, total, text)
                last_emit = key
    return proc.wait()


def main(argv: list[str] | None = None) -> int:
    configure_utf8_stdio()
    parser = argparse.ArgumentParser(description="Remotion → final_video.mp4")
    parser.add_argument("episode", type=Path)
    args = parser.parse_args(argv)
    episode_dir = args.episode.expanduser().resolve()
    if not episode_dir.is_dir():
        print(f"Папка не найдена: {episode_dir}", file=sys.stderr)
        return 1

    status = node_status()
    if not status["ready"]:
        print(
            "[ERROR] Remotion не готов. Нужны node, npm и npm install в remotion_studio/. "
            f"Статус: {status}",
            file=sys.stderr,
        )
        return 1

    output = episode_dir / "final_video.mp4"
    props_path = REMOTION_DIR / "props.episode.json"
    try:
        write_props(episode_dir, props_path)
    except (FileNotFoundError, OSError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1

    code = run_render(output, props_path)
    if code != 0:
        print(f"[ERROR] Remotion завершился с кодом {code}", file=sys.stderr)
        return code
    emit_progress(100, 100, "Remotion: готово")
    print(f"Готово: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())