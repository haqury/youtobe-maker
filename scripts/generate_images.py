"""Скачать кадры 1920x1080 по строкам PROMPT: из visual_prompts.md."""

from __future__ import annotations

import argparse
import ast
import base64
import io
import json
import os
import random
import re
import statistics
import sys
import time
from pathlib import Path
from typing import Callable
from urllib.parse import quote

import requests
from dotenv import load_dotenv

_SCRIPTS = Path(__file__).resolve().parent
ROOT = _SCRIPTS.parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
from PIL import Image

from ai_router import fetch_image_for_prompt, load_ai_settings
from local_sd import generate_local_sd_batch, local_sd_status
from episode_review import image_note_map, load_review, stem_from_filename
from job_progress import emit_progress
from quota_tracker import record_use, register_http_failure

_QUOTA_KEY = {
    "cloudflare": "cloudflare_workers_ai",
    "together_flux": "together_flux",
    "gemini_imagen": "gemini_imagen",
    "pollinations": "pollinations_fallback",
}

WIDTH = 1920
HEIGHT = 1080
HF_WIDTH = 1280
HF_HEIGHT = 720
HF_MODEL = "black-forest-labs/FLUX.1-schnell"
HF_PAUSE_RANGE = (2.0, 3.0)
CF_PAUSE_RANGE = (1.5, 2.5)
PAUSE_RANGE = (3.0, 5.0)
CF_MODEL_PATH = "@cf/black-forest-labs/flux-1-schnell"
CF_PROMPT_MAX_LEN = 2048
CF_STEPS = 4
ATTEMPTS = 3
_HF_PAYMENT_BLOCKED = False
PLACEHOLDER_MAX_BYTES = 50 * 1024
POLLINATIONS_WATERMARK_CROP = 45
PROMPT_RE = re.compile(r"^PROMPT:\s*(.+)$", re.IGNORECASE)
NUMBERED_RE = re.compile(r"^\d+[\.\)\:]\s+(.+)$")
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}


def load_hf_token() -> str | None:
    load_dotenv(ROOT / ".env")
    token = os.getenv("HF_TOKEN", "").strip()
    return token or None


def load_cf_credentials() -> tuple[str, str] | None:
    load_dotenv(ROOT / ".env")
    account_id = os.getenv("CF_ACCOUNT_ID", "").strip()
    api_token = os.getenv("CF_API_TOKEN", "").strip()
    if account_id and api_token:
        return account_id, api_token
    return None


def _prompt_from_token(item: str) -> str | None:
    text = item.strip().strip(",").strip()
    if not text:
        return None
    tagged = PROMPT_RE.match(text)
    if tagged:
        return tagged.group(1).strip()
    if text.lower().startswith("prompt:"):
        return text.split(":", 1)[1].strip()
    return text


def extract_prompts_from_blob(text: str) -> list[str]:
    """LLM иногда пишет visual_prompts как Python/JSON массив в одну строку."""
    raw = text.strip()
    if not raw:
        return []
    if raw.startswith("["):
        try:
            parsed = ast.literal_eval(raw)
            if isinstance(parsed, list):
                out: list[str] = []
                for item in parsed:
                    if not isinstance(item, str):
                        item = str(item)
                    prompt = _prompt_from_token(item)
                    if prompt:
                        out.append(prompt)
                if out:
                    return out
        except (SyntaxError, ValueError):
            pass
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                out = []
                for item in parsed:
                    prompt = _prompt_from_token(str(item))
                    if prompt:
                        out.append(prompt)
                if out:
                    return out
        except json.JSONDecodeError:
            pass
    return []


def format_visual_prompts_markdown(prompts: list[str]) -> str:
    lines = [f"PROMPT: {p.strip()}" for p in prompts if p and p.strip()]
    return "\n".join(lines) + ("\n" if lines else "")


def extract_prompts(text: str) -> list[str]:
    tagged: list[str] = []
    numbered: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        tagged_match = PROMPT_RE.match(line)
        if tagged_match:
            prompt = tagged_match.group(1).strip()
            if prompt:
                tagged.append(prompt)
            continue
        numbered_match = NUMBERED_RE.match(line)
        if not numbered_match:
            continue
        prompt = numbered_match.group(1).strip()
        nested = PROMPT_RE.match(prompt)
        if nested:
            prompt = nested.group(1).strip()
        if prompt:
            numbered.append(prompt)
    if tagged:
        return tagged
    if numbered:
        return numbered
    return extract_prompts_from_blob(text)


def load_script_image_prompts(script_path: Path) -> list[str]:
    """Описания [IMAGE|NN] из script.md. Позиция в списке = номер кадра минус 1."""
    if not script_path.is_file():
        return []
    found: dict[int, str] = {}
    for raw in script_path.read_text(encoding="utf-8-sig").splitlines():
        match = re.match(r"^\[IMAGE\|(\d+)\]\s+(\S.*?)\s*$", raw.strip(), re.IGNORECASE)
        if not match:
            continue
        number = int(match.group(1))
        if number > 0 and number not in found:
            found[number] = match.group(2).strip()
    if not found:
        return []
    prompts = [""] * max(found)
    for number, text in found.items():
        prompts[number - 1] = text
    return prompts


def load_visual_prompts(prompts_path: Path, *, rewrite: bool = True) -> list[str]:
    raw = prompts_path.read_text(encoding="utf-8-sig")
    prompts = extract_prompts(raw)
    if not prompts:
        return []
    if rewrite:
        canonical = "# Visual prompts\n\n" + format_visual_prompts_markdown(prompts)
        line_tagged = any(
            PROMPT_RE.match(line.strip())
            for line in raw.splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        )
        if raw.strip() != canonical.strip() and (raw.strip().startswith("[") or not line_tagged):
            prompts_path.write_text(canonical, encoding="utf-8")
    return prompts


def image_exists(folder: Path, number: int) -> bool:
    stem = f"{number:02d}"
    return any((folder / f"{stem}{suffix}").is_file() for suffix in IMAGE_SUFFIXES)


def is_likely_placeholder(path: Path) -> bool:
    if not path.is_file():
        return False
    if path.stat().st_size < PLACEHOLDER_MAX_BYTES:
        return True
    try:
        with Image.open(path) as image:
            sample = image.convert("RGB").resize((96, 54))
        pixels = list(sample.getdata())
        if len(pixels) < 2:
            return True
        luminance = [sum(channel) / 3 for channel in pixels]
        spread = statistics.pstdev(luminance)
        unique = len({pixels[index] for index in range(0, len(pixels), 97)})
        if spread < 6.0:
            return True
        if unique <= 12 and spread < 22.0:
            return True
    except OSError:
        return True
    return False


def remove_stale_placeholders(image_dir: Path) -> list[str]:
    removed: list[str] = []
    if not image_dir.is_dir():
        return removed
    for path in sorted(image_dir.iterdir()):
        if not path.is_file() or path.suffix.lower() not in IMAGE_SUFFIXES:
            continue
        if is_likely_placeholder(path):
            path.unlink(missing_ok=True)
            removed.append(path.name)
    return removed


def pollinations_urls(prompt: str) -> list[str]:
    encoded = quote(prompt, safe="")
    base = f"https://image.pollinations.ai/prompt/{encoded}"
    params = "&".join(
        [
            "width=1920",
            "height=1080",
            "nologo=true",
            "enhance=false",
        ]
    )
    return [
        f"{base}?{params}",
        f"{base}?{params}&model=turbo",
        f"{base}?{params}&model=flux",
    ]


def pause_pollinations() -> None:
    delay = random.uniform(*PAUSE_RANGE)
    print(f"Пауза {delay:.1f} с.")
    time.sleep(delay)


def pause_hf() -> None:
    delay = random.uniform(*HF_PAUSE_RANGE)
    print(f"Пауза HF {delay:.1f} с.")
    time.sleep(delay)


def pause_cf() -> None:
    delay = random.uniform(*CF_PAUSE_RANGE)
    print(f"Пауза CF {delay:.1f} с.")
    time.sleep(delay)


def bytes_to_jpeg(image: Image.Image, *, pollinations: bool) -> bytes:
    if pollinations:
        crop_height = image.height - POLLINATIONS_WATERMARK_CROP
        if crop_height > 32:
            image = image.crop((0, 0, image.width, crop_height))
    if image.size != (WIDTH, HEIGHT):
        image = image.resize((WIDTH, HEIGHT), Image.Resampling.LANCZOS)
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=92)
    return buffer.getvalue()


def _http_error_detail(response: requests.Response) -> str:
    try:
        payload = response.json()
    except json.JSONDecodeError:
        return response.text[:300]
    errors = payload.get("errors") or payload.get("messages") or []
    if errors:
        return str(errors)[:400]
    return response.text[:300]


def _cf_image_from_response(response: requests.Response) -> Image.Image:
    content_type = (response.headers.get("Content-Type") or "").lower()
    if "application/json" in content_type or response.text[:1] in ("{", "["):
        body = response.json()
        if not body.get("success", True) and body.get("errors"):
            raise RuntimeError(f"Cloudflare AI: {body.get('errors')}")
        result = body.get("result") or body
        if isinstance(result, list):
            result = result[0] if result else {}
        image_b64 = result.get("image") if isinstance(result, dict) else None
        if not image_b64:
            raise RuntimeError(f"Cloudflare AI: нет image в ответе: {str(body)[:200]}")
        if isinstance(image_b64, str) and image_b64.startswith("data:") and "," in image_b64:
            image_b64 = image_b64.split(",", 1)[1]
        raw = base64.b64decode(image_b64)
        return validate_image_bytes(raw, "image/jpeg")
    return validate_image_bytes(response.content, content_type)


def should_try_hf() -> bool:
    global _HF_PAYMENT_BLOCKED
    if _HF_PAYMENT_BLOCKED or not load_hf_token():
        return False
    try:
        from quota_tracker import get_provider_status

        row = get_provider_status("huggingface_flux")
        if row.get("status") == "inactive":
            return False
        if row.get("status") == "red" and str(row.get("last_error_code")) == "402":
            return False
    except Exception:
        pass
    return True


def validate_image_bytes(data: bytes, content_type: str = "") -> Image.Image:
    if not data:
        raise RuntimeError("пустой ответ")
    is_image_bytes = (
        data.startswith(b"\xff\xd8")
        or data.startswith(b"\x89PNG")
        or data.startswith(b"RIFF")
        or data.startswith(b"GIF8")
    )
    if "image" not in content_type.lower() and not is_image_bytes:
        snippet = data[:200].decode("utf-8", errors="replace")
        raise RuntimeError(f"ответ не похож на картинку: {snippet[:120]}")
    image = Image.open(io.BytesIO(data)).convert("RGB")
    return image


def fetch_from_cloudflare(
    session: requests.Session,
    prompt: str,
    account_id: str,
    api_token: str,
) -> bytes | None:
    url = (
        f"https://api.cloudflare.com/client/v4/accounts/{account_id}"
        f"/ai/run/{CF_MODEL_PATH}"
    )
    headers = {
        "Authorization": f"Bearer {api_token}",
        "Content-Type": "application/json",
    }
    prompt_use = prompt.strip()[:CF_PROMPT_MAX_LEN]
    payload = {"prompt": prompt_use, "steps": CF_STEPS}
    last_error: Exception | None = None
    for attempt in range(1, ATTEMPTS + 1):
        try:
            response = session.post(url, headers=headers, json=payload, timeout=(20, 180))
            if not response.ok:
                detail = _http_error_detail(response)
                raise RuntimeError(
                    f"{response.status_code} {response.reason}: {detail}"
                )
            image = _cf_image_from_response(response)
            neurons = response.headers.get("cf-ai-neurons")
            extra = f", neurons={neurons}" if neurons else ""
            print(f"CF: кадр получен ({image.width}x{image.height}{extra}).")
            record_use("cloudflare_workers_ai", 1)
            return bytes_to_jpeg(image, pollinations=False)
        except (requests.RequestException, RuntimeError, OSError, json.JSONDecodeError, ValueError) as exc:
            register_http_failure("cloudflare_workers_ai", exc)
            last_error = exc
            print(f"CF попытка {attempt}/{ATTEMPTS} не удалась: {exc}")
            if attempt < ATTEMPTS:
                pause_cf()
    print(f"CF: все попытки исчерпаны: {last_error}", file=sys.stderr)
    return None


def fetch_from_hf(session: requests.Session, prompt: str, token: str) -> bytes | None:
    """Inference Providers (FLUX.1-schnell), не устаревший hf-inference URL."""
    global _HF_PAYMENT_BLOCKED
    from huggingface_hub import InferenceClient

    _ = session
    if not should_try_hf():
        return None
    last_error: Exception | None = None
    for attempt in range(1, ATTEMPTS + 1):
        try:
            client = InferenceClient(token=token, timeout=300)
            image = client.text_to_image(
                prompt,
                model=HF_MODEL,
                width=HF_WIDTH,
                height=HF_HEIGHT,
            )
            if isinstance(image, Image.Image):
                rgb = image.convert("RGB")
            else:
                rgb = Image.open(io.BytesIO(image)).convert("RGB")
            print(f"HF: кадр получен ({rgb.width}x{rgb.height}).")
            record_use("huggingface_flux", 1)
            return bytes_to_jpeg(rgb, pollinations=False)
        except Exception as exc:
            register_http_failure("huggingface_flux", exc)
            last_error = exc
            text = str(exc)
            if "402" in text or "Payment Required" in text:
                _HF_PAYMENT_BLOCKED = True
                print(
                    "HF: баланс prepaid $0 / 402 — дальше только CF → Pollinations.",
                    file=sys.stderr,
                )
                break
            print(f"HF попытка {attempt}/{ATTEMPTS} не удалась: {exc}")
            if attempt < ATTEMPTS:
                pause_hf()
    print(f"HF: все попытки исчерпаны: {last_error}", file=sys.stderr)
    return None


def fetch_from_pollinations(session: requests.Session, prompt: str) -> bytes | None:
    last_error: Exception | None = None
    urls = pollinations_urls(prompt)
    for attempt in range(1, ATTEMPTS + 1):
        url = urls[(attempt - 1) % len(urls)]
        try:
            response = session.get(url, timeout=(20, 180))
            response.raise_for_status()
            image = validate_image_bytes(response.content, response.headers.get("Content-Type", ""))
            print("Pollinations: кадр получен, обрезка водяного знака.")
            record_use("pollinations_fallback", 1)
            return bytes_to_jpeg(image, pollinations=True)
        except (requests.RequestException, RuntimeError, OSError) as exc:
            register_http_failure("pollinations_fallback", exc)
            last_error = exc
            print(f"Pollinations попытка {attempt}/{ATTEMPTS} не удалась: {exc}")
            if attempt < ATTEMPTS:
                pause_pollinations()
    print(f"Pollinations: все попытки исчерпаны: {last_error}", file=sys.stderr)
    return None


def fetch_jpeg(
    session: requests.Session,
    prompt: str,
    number: int,
    cf_creds: tuple[str, str] | None,
    hf_token: str | None,
    *,
    skip_local_sd: bool = False,
    channel_dir: Path | None = None,
) -> bytes | None:
    if channel_dir is not None:
        from channel_settings import apply_style_suffix
        from isekai_config import base_style_prompt

        base = base_style_prompt(channel_dir)
        if base and base.lower() not in prompt.lower():
            prompt = f"{base}. {prompt}"
        prompt = apply_style_suffix(prompt, channel_dir)
    settings = load_ai_settings()
    provider = str(settings.get("image_provider", "cloudflare"))
    data: bytes | None = None
    if not (provider == "local_sd" and skip_local_sd):
        data = fetch_image_for_prompt(session, prompt)
    if data:
        record_use(_QUOTA_KEY.get(provider, "cloudflare_workers_ai"), 1)
        return data

    if provider != "cloudflare" and cf_creds:
        account_id, api_token = cf_creds
        data = fetch_from_cloudflare(session, prompt, account_id, api_token)
        if data:
            return data

    if hf_token and should_try_hf():
        data = fetch_from_hf(session, prompt, hf_token)
        if data:
            return data

    data = fetch_from_pollinations(session, prompt)
    if data:
        return data

    print(f"[ERROR] Не удалось сгенерировать кадр {number:02d}", file=sys.stderr)
    return None


def note_for_number(notes: dict[str, str], number: int) -> str:
    stem = f"{number:02d}"
    for key, value in notes.items():
        if stem_from_filename(key) == stem:
            return value.strip()
    return notes.get(f"{stem}.jpg", "").strip()


def _collect_local_sd_batch(
    *,
    provider: str,
    model: str,
    image_dir: Path,
    prompts: list[str],
    notes: dict[str, str],
    flagged_stems: set[str] | None,
    only_numbers: list[int] | None = None,
    channel_dir: Path | None = None,
) -> tuple[list[tuple[int, str]], dict[int, bytes]]:
    """Список недостающих кадров и результат одной пачки local_sd."""
    batch_items: list[tuple[int, str]] = []
    for number, prompt in enumerate(prompts, start=1):
        if only_numbers is not None and number not in only_numbers:
            continue
        stem = f"{number:02d}"
        flagged_set = flagged_stems or set()
        if stem in flagged_set:
            pass
        elif image_exists(image_dir, number):
            continue
        prompt_use = prompt
        extra = note_for_number(notes, number)
        if extra:
            prompt_use = f"{prompt}. Fix and improve: {extra}"
        if channel_dir is not None:
            from channel_settings import apply_style_suffix
            from isekai_config import base_style_prompt

            base = base_style_prompt(channel_dir)
            if base and base.lower() not in prompt_use.lower():
                prompt_use = f"{base}. {prompt_use}"
            prompt_use = apply_style_suffix(prompt_use, channel_dir)
        batch_items.append((number, prompt_use))
    batch_done: dict[int, bytes] = {}
    if provider == "local_sd" and batch_items:
        status = local_sd_status(model_name=model)
        if status.get("ready"):
            batch_done = generate_local_sd_batch(batch_items, image_dir, model_name=model)
        else:
            from local_sd import print_local_sd_setup_help

            print_local_sd_setup_help(model)
    return batch_items, batch_done


def print_failed_summary(failed: list[int]) -> None:
    if not failed:
        return
    labels = ", ".join(f"{number:02d}" for number in failed)
    print(f"Кадры без файла (докачайте при повторе): {labels}", file=sys.stderr)


def regenerate_flagged_images(
    episode_dir: Path,
    on_frame: Callable[[int, int, str], None] | None = None,
) -> int:
    """Перегенерировать только помеченные в review кадры."""
    prompts_path = episode_dir / "visual_prompts.md"
    if not prompts_path.is_file():
        raise FileNotFoundError(f"Нет файла: {prompts_path}")
    prompts = load_visual_prompts(prompts_path)
    if not prompts:
        raise RuntimeError("В visual_prompts.md нет промптов. Нужна строка вида: PROMPT: ...")

    image_dir = episode_dir / "images"
    image_dir.mkdir(parents=True, exist_ok=True)
    purged = remove_stale_placeholders(image_dir)
    if purged:
        print(f"Удалены заглушки: {', '.join(purged)}")

    review = load_review(episode_dir)
    notes = image_note_map(review)
    flagged_stems = {stem_from_filename(key) for key in notes}
    if not flagged_stems:
        print("Нет помеченных кадров.")
        return 0

    targets = [number for number in range(1, len(prompts) + 1) if f"{number:02d}" in flagged_stems]
    channel_dir = episode_dir.parents[1]
    total = len(targets)
    created = 0
    failed: list[int] = []
    img_settings = load_ai_settings()
    provider = str(img_settings.get("image_provider", "cloudflare"))
    image_model = str(img_settings.get("image_model", ""))
    _, batch_done = _collect_local_sd_batch(
        provider=provider,
        model=image_model,
        image_dir=image_dir,
        prompts=prompts,
        notes=notes,
        flagged_stems=flagged_stems,
        only_numbers=targets,
        channel_dir=channel_dir,
    )
    cf_creds = load_cf_credentials()
    hf_token = load_hf_token()
    session = requests.Session()
    session.headers["User-Agent"] = "youtobe-maker/generate_images"
    pending_request = False

    def pause_between_frames() -> None:
        if cf_creds:
            pause_cf()
        elif hf_token:
            pause_hf()
        else:
            pause_pollinations()

    try:
        for index, number in enumerate(targets, start=1):
            prompt = prompts[number - 1]
            destination = image_dir / f"{number:02d}.jpg"
            label = f"Перегенерация кадра {number:02d}.jpg"
            if on_frame:
                on_frame(index, total, label)
            emit_progress(index, total, label)
            for suffix in IMAGE_SUFFIXES:
                path = image_dir / f"{number:02d}{suffix}"
                if path.is_file():
                    path.unlink()
            if pending_request:
                pause_between_frames()
            prompt_use = prompt
            extra = note_for_number(notes, number)
            if extra:
                prompt_use = f"{prompt}. Fix and improve: {extra}"
                print(f"{number:02d}: учёт замечания — {extra[:120]}{'…' if len(extra) > 120 else ''}")
            print(f"{number:02d}: {prompt_use}")
            if number in batch_done:
                jpeg = batch_done[number]
            else:
                jpeg = fetch_jpeg(
                    session,
                    prompt_use,
                    number,
                    cf_creds,
                    hf_token,
                    skip_local_sd=(provider == "local_sd"),
                    channel_dir=channel_dir,
                )
            if jpeg is None:
                failed.append(number)
                continue
            destination.write_bytes(jpeg)
            created += 1
            pending_request = True
            print(f"Сохранено: {destination}")
    finally:
        session.close()

    print_failed_summary(failed)
    print(f"Перегенерировано кадров: {created}.")
    if failed:
        raise RuntimeError(f"Не сгенерированы кадры: {', '.join(f'{n:02d}' for n in failed)}")
    return created


def generate(episode_dir: Path) -> int:
    script_prompts = load_script_image_prompts(episode_dir / "script.md")
    prompts_path = episode_dir / "visual_prompts.md"
    if script_prompts and any(item.strip() for item in script_prompts):
        prompts = script_prompts
        print(f"Кадры из [IMAGE] в script.md: {sum(1 for item in prompts if item.strip())} описаний.")
    else:
        if not prompts_path.is_file():
            raise FileNotFoundError(f"Нет файла: {prompts_path}")
        prompts = load_visual_prompts(prompts_path)
        if not prompts:
            raise RuntimeError(
                "В visual_prompts.md нет промптов. Нужна строка вида: PROMPT: ..."
            )

    image_dir = episode_dir / "images"
    image_dir.mkdir(parents=True, exist_ok=True)
    purged = remove_stale_placeholders(image_dir)
    if purged:
        print(f"Удалены заглушки: {', '.join(purged)}")

    review = load_review(episode_dir)
    notes = image_note_map(review)
    flagged_stems_set = {stem_from_filename(key) for key in notes}
    total = len(prompts)
    created = 0
    failed: list[int] = []
    cf_creds = load_cf_credentials()
    hf_token = load_hf_token()
    img_settings = load_ai_settings()
    provider = str(img_settings.get("image_provider", "cloudflare"))
    image_model = str(img_settings.get("image_model", ""))
    print(
        f"Генератор кадров: {img_settings.get('image_provider')} / {img_settings.get('image_model')} "
        f"(ai_settings.json; резерв HF/Pollinations при сбое)."
    )
    channel_dir = episode_dir.parents[1]

    batch_items, batch_done = _collect_local_sd_batch(
        provider=provider,
        model=image_model,
        image_dir=image_dir,
        prompts=prompts,
        notes=notes,
        flagged_stems=flagged_stems_set,
        channel_dir=channel_dir,
    )
    if provider == "local_sd" and batch_items:
        print(f"local_sd: пачка {len(batch_items)} кадров, готово {len(batch_done)}", flush=True)

    session = requests.Session()
    session.headers["User-Agent"] = "youtobe-maker/generate_images"
    pending_request = False

    def pause_between_frames() -> None:
        if cf_creds:
            pause_cf()
        elif hf_token:
            pause_hf()
        else:
            pause_pollinations()

    emit_progress(0, total, "Подготовка генерации кадров")
    try:
        for number, prompt in enumerate(prompts, start=1):
            if not str(prompt).strip():
                continue
            destination = image_dir / f"{number:02d}.jpg"
            stem = f"{number:02d}"
            emit_progress(number, total, f"Кадр {stem}.jpg")
            if stem in flagged_stems_set:
                for suffix in IMAGE_SUFFIXES:
                    path = image_dir / f"{number:02d}{suffix}"
                    if path.is_file():
                        path.unlink()
                print(f"{number:02d}: помечен как проблемный, перегенерирую.")
            elif image_exists(image_dir, number):
                print(f"{number:02d}: уже есть, пропускаю.")
                continue
            prompt_use = prompt
            extra = note_for_number(notes, number)
            if extra:
                prompt_use = f"{prompt}. Fix and improve: {extra}"
                print(f"{number:02d}: учёт замечания — {extra[:120]}{'…' if len(extra) > 120 else ''}")
            if pending_request:
                pause_between_frames()
            print(f"{number:02d}: {prompt_use}")
            if number in batch_done:
                jpeg = batch_done[number]
            else:
                jpeg = fetch_jpeg(
                    session,
                    prompt_use,
                    number,
                    cf_creds,
                    hf_token,
                    skip_local_sd=(provider == "local_sd"),
                    channel_dir=channel_dir,
                )
            if jpeg is None:
                failed.append(number)
                continue
            destination.write_bytes(jpeg)
            created += 1
            pending_request = True
            print(f"Сохранено: {destination}")
    finally:
        session.close()

    emit_progress(total, total, "Генерация кадров завершена")
    print_failed_summary(failed)
    print(f"Новых кадров: {created}. Всего промптов: {len(prompts)}.")
    if failed:
        raise RuntimeError(f"Не сгенерированы кадры: {', '.join(f'{n:02d}' for n in failed)}")
    return created


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Сгенерировать images/NN.jpg из visual_prompts.md.")
    parser.add_argument("episode", type=Path, help="Папка эпизода")
    parser.add_argument(
        "--flagged-only",
        action="store_true",
        help="Перегенерировать только кадры из review.json",
    )
    args = parser.parse_args(argv)
    episode_dir = args.episode.expanduser().resolve()
    if not episode_dir.is_dir():
        print(f"Папка не найдена: {episode_dir}", file=sys.stderr)
        return 1
    try:
        if args.flagged_only:
            regenerate_flagged_images(episode_dir)
        else:
            generate(episode_dir)
    except (FileNotFoundError, RuntimeError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())