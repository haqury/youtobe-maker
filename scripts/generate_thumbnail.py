"""YouTube-обложка 1280x720: фон без текста + крупный заголовок."""

from __future__ import annotations

import argparse
import csv
import io
import re
import sys
from pathlib import Path
from urllib.parse import quote

import requests
from PIL import Image, ImageDraw, ImageEnhance, ImageFont

ROOT = Path(__file__).resolve().parents[1]
WIDTH = 1280
HEIGHT = 720
SLEEP_CHANNEL = "channel_02_sleep_history"
GAMING_CHANNEL = "channel_01_gaming_mysteries"
TITLE_LINE_RE = re.compile(r"^\d+\.\s+(.+)$")
SUBTITLE_RE = re.compile(r"^###\s+Titles\s*$", re.I)
QUOTED_RE = re.compile(r'"([^"]{3,40})"')
PROMPT_RE = re.compile(r"^PROMPT:\s*(.+)$", re.IGNORECASE)
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
FONT_CANDIDATES = (
    Path(r"C:\Windows\Fonts\arialbd.ttf"),
    Path(r"C:\Windows\Fonts\impact.ttf"),
    Path(r"C:\Windows\Fonts\segoeuib.ttf"),
)


def resolve_episode(raw: Path) -> Path:
    candidate = raw.expanduser()
    options = [candidate.resolve()] if candidate.is_absolute() else []
    options.extend([(Path.cwd() / candidate).resolve(), (ROOT / candidate).resolve()])
    for option in options:
        if option.is_dir():
            return option
    token = str(raw).strip().replace("\\", "/").rstrip("/").split("/")[-1].casefold()
    matches = [
        path.resolve()
        for path in ROOT.glob("channels/*/episodes/*")
        if path.is_dir() and (path.name.casefold() == token or path.name.casefold().startswith(token + "_"))
    ]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise FileNotFoundError(f"ID {token} подходит нескольким папкам.")
    raise FileNotFoundError(f"Эпизод не найден: {raw}")


def metadata_text(episode_dir: Path) -> str:
    path = episode_dir / "metadata.md"
    return path.read_text(encoding="utf-8-sig") if path.is_file() else ""


def first_visual_prompt(episode_dir: Path) -> str:
    path = episode_dir / "visual_prompts.md"
    if not path.is_file():
        return ""
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        match = PROMPT_RE.match(line.strip())
        if match:
            return match.group(1).strip()
    return ""


def title_from_backlog(episode_dir: Path) -> str | None:
    episode_id = episode_dir.name.split("_", 1)[0].upper()
    channel_slug = episode_dir.parent.parent.name
    backlog = ROOT / "channels" / channel_slug / "backlog.csv"
    if not backlog.is_file():
        return None
    for line in backlog.read_text(encoding="utf-8-sig").splitlines()[1:]:
        if not line.startswith(f"{episode_id},"):
            continue
        cells = next(csv.reader([line]))
        return cells[1].strip() if len(cells) > 1 else None
    return None


def pick_title(episode_dir: Path) -> str:
    text = metadata_text(episode_dir)
    has_dual = "## English Metadata" in text
    in_english = not has_dual
    in_titles = not has_dual
    for line in text.splitlines():
        stripped = line.strip()
        lower = stripped.lower()
        if lower == "## english metadata":
            in_english = True
            in_titles = False
            continue
        if stripped.startswith("## ") and in_english and lower != "## english metadata":
            in_english = False
            in_titles = False
        if lower in {"## titles", "### titles"} or SUBTITLE_RE.match(stripped):
            in_titles = in_english or not has_dual
            continue
        if stripped.startswith("### ") and in_english and lower not in {"### titles", "## titles"}:
            in_titles = False
        match = TITLE_LINE_RE.match(stripped)
        if match and in_titles:
            return match.group(1).strip()
    backlog_title = title_from_backlog(episode_dir)
    if backlog_title:
        return backlog_title
    return episode_dir.name.replace("_", " ").title()


def headline_words(full_title: str, metadata: str) -> str:
    for match in QUOTED_RE.findall(metadata):
        words = match.split()
        if 2 <= len(words) <= 4:
            return match
    cleaned = re.split(r"\s*\|\s*", full_title, maxsplit=1)[0]
    cleaned = re.sub(r"[^\w\s:'-]", "", cleaned)
    words = cleaned.split()
    if len(words) <= 4:
        return cleaned
    stop = {"the", "a", "an", "in", "for", "to", "of", "and", "at", "from", "with"}
    picked: list[str] = []
    for word in words:
        if word.lower() in stop and picked:
            continue
        picked.append(word)
        if len(picked) >= 4:
            break
    return " ".join(picked[:4] if picked else words[:4])


def thumbnail_prompt(episode_dir: Path, full_title: str) -> str:
    visual = first_visual_prompt(episode_dir)
    if SLEEP_CHANNEL in episode_dir.parts:
        style = (
            "dark cozy sleep atmosphere, muted blues and warm amber, cinematic still, fog, soft glow, "
            "no people, no face"
        )
    elif GAMING_CHANNEL in episode_dir.parts:
        style = (
            "high contrast detective gaming mystery mood, deep shadows, neon hints, dramatic scene, "
            "no people, no face"
        )
    else:
        style = "cinematic faceless youtube mood, high contrast, no people, no face"
    scene = visual or full_title
    return (
        f"{scene}, {style}, negative space for title, "
        "no typography, no letters, no words, no text, no watermark"
    )


def pollinations_urls(prompt: str) -> list[str]:
    encoded = quote(prompt, safe="")
    base = f"https://image.pollinations.ai/prompt/{encoded}"
    common = f"width={WIDTH}&height={HEIGHT}&nologo=true&enhance=false"
    return [
        f"{base}?{common}&model=flux",
        f"{base}?{common}&model=turbo",
        f"{base}?{common}",
    ]


def fetch_background(prompt: str, episode_dir: Path) -> Image.Image:
    _scripts = Path(__file__).resolve().parent
    if str(_scripts) not in sys.path:
        sys.path.insert(0, str(_scripts))
    from ai_router import fetch_image_for_prompt

    session = requests.Session()
    session.headers["User-Agent"] = "youtobe-maker/generate_thumbnail"
    try:
        jpeg = fetch_image_for_prompt(session, prompt)
        if jpeg:
            image = Image.open(io.BytesIO(jpeg)).convert("RGB")
            if image.size != (WIDTH, HEIGHT):
                image = image.resize((WIDTH, HEIGHT), Image.Resampling.LANCZOS)
            session.close()
            return image
    except OSError:
        pass
    last_error: Exception | None = None
    for url in pollinations_urls(prompt):
        try:
            response = session.get(url, timeout=(20, 180))
            response.raise_for_status()
            image = Image.open(io.BytesIO(response.content)).convert("RGB")
            if image.size != (WIDTH, HEIGHT):
                image = image.resize((WIDTH, HEIGHT), Image.Resampling.LANCZOS)
            session.close()
            return image
        except (requests.RequestException, OSError) as exc:
            last_error = exc
    session.close()
    images_dir = episode_dir / "images"
    if images_dir.is_dir():
        for path in sorted(images_dir.iterdir()):
            if path.suffix.lower() in IMAGE_SUFFIXES:
                return Image.open(path).convert("RGB").resize((WIDTH, HEIGHT), Image.Resampling.LANCZOS)
    print(f"Pollinations недоступен ({last_error}), тёмная заглушка.", file=sys.stderr)
    return Image.new("RGB", (WIDTH, HEIGHT), (18, 20, 28))


def load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in FONT_CANDIDATES:
        if path.is_file():
            return ImageFont.truetype(str(path), size=size)
    return ImageFont.load_default()


def apply_vignette(image: Image.Image) -> Image.Image:
    shaded = ImageEnhance.Brightness(image).enhance(0.72)
    overlay = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    draw.rectangle((0, 0, WIDTH, HEIGHT), fill=(0, 0, 0, 80))
    draw.ellipse((-140, -90, WIDTH + 140, HEIGHT + 120), fill=(0, 0, 0, 120))
    base = shaded.convert("RGBA")
    return Image.alpha_composite(base, overlay).convert("RGB")


def draw_headline(image: Image.Image, text: str) -> Image.Image:
    canvas = image.copy()
    draw = ImageDraw.Draw(canvas)
    size = 112
    while size >= 64:
        font = load_font(size)
        box = draw.textbbox((0, 0), text, font=font, stroke_width=6)
        if box[2] - box[0] <= WIDTH - 120:
            break
        size -= 6
    else:
        font = load_font(size)
    box = draw.textbbox((0, 0), text, font=font, stroke_width=6)
    x = (WIDTH - (box[2] - box[0])) // 2
    y = HEIGHT - (box[3] - box[1]) - 70
    for dx, dy in ((4, 4), (3, 3), (2, 2)):
        draw.text((x + dx, y + dy), text, font=font, fill=(0, 0, 0))
    draw.text((x, y), text, font=font, fill=(244, 244, 244), stroke_width=6, stroke_fill=(0, 0, 0))
    return canvas


def build_thumbnail(episode_dir: Path) -> Path:
    full_title = pick_title(episode_dir)
    meta = metadata_text(episode_dir)
    headline = headline_words(full_title, meta)
    prompt = thumbnail_prompt(episode_dir, full_title)
    print(f"Заголовок: {headline}")
    print(f"Фон: {prompt[:140]}...")
    image = draw_headline(apply_vignette(fetch_background(prompt, episode_dir)), headline.upper())
    output = episode_dir / "thumbnail.jpg"
    image.save(output, "JPEG", quality=92, optimize=True)
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Собрать thumbnail.jpg для эпизода.")
    parser.add_argument("episode", type=Path, help="Папка эпизода")
    args = parser.parse_args(argv)
    try:
        output = build_thumbnail(resolve_episode(args.episode))
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except OSError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"Готово: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())