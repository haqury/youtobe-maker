"""Единый роутер LLM и генерации картинок по config/ai_settings.json."""

from __future__ import annotations

import base64
import io
import json
import os
import subprocess
import sys
import time
from time import monotonic
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests
from dotenv import load_dotenv
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SETTINGS_PATH = ROOT / "config" / "ai_settings.json"

TEXT_PROVIDERS = ("cloudflare", "gemini", "groq", "openrouter", "ollama")
IMAGE_PROVIDERS = ("cloudflare", "gemini_imagen", "together_flux", "pollinations", "local_sd")

TEXT_HTTP_CONNECT_TIMEOUT = 20.0
TEXT_HTTP_READ_TIMEOUT = 120.0
TEXT_HTTP_READ_TIMEOUT_LONG = 600.0

# Снятые/битые id — подменяем при load_ai_settings (и не предлагаем в UI первыми).
GROQ_MODEL_ALIASES: dict[str, str] = {
    "llama-3.3-70b-versatile": "openai/gpt-oss-120b",
    "llama-3.1-70b-versatile": "openai/gpt-oss-120b",
    "llama3-70b-8192": "openai/gpt-oss-120b",
    "llama-3.1-8b-instant": "openai/gpt-oss-20b",
    "mixtral-8x7b-32768": "openai/gpt-oss-120b",
}

GEMINI_MODEL_ALIASES: dict[str, str] = {
    "gemini-2.0-flash": "gemini-3-flash-preview",
    "gemini-2.5-flash": "gemini-3-flash-preview",
    "gemini-2.0-flash-lite": "gemini-3-flash-preview",
    "gemini-2.5-flash-lite": "gemini-3-flash-preview",
    "gemini-1.5-flash": "gemini-3-flash-preview",
}

TEXT_MODELS: dict[str, list[str]] = {
    "cloudflare": ["@cf/meta/llama-3.3-70b-instruct-fp8-fast"],
    "gemini": [
        "gemini-3-flash-preview",
        "gemini-3.5-flash",
        "gemini-flash-latest",
    ],
    "groq": [
        "openai/gpt-oss-120b",
        "openai/gpt-oss-20b",
        "qwen/qwen3.8-27b",
    ],
    "openrouter": [
        "deepseek/deepseek-chat:free",
        "meta-llama/llama-3.3-70b-instruct:free",
        "google/gemma-2-9b-it:free",
    ],
    "ollama": [],
}

IMAGE_MODELS: dict[str, list[str]] = {
    "cloudflare": ["@cf/black-forest-labs/flux-1-schnell"],
    "gemini_imagen": ["imagen-3.0-generate-002", "imagen-3.0-fast-generate-001"],
    "together_flux": [
        "black-forest-labs/FLUX.1-schnell-Free",
        "black-forest-labs/FLUX.1-schnell",
    ],
    "pollinations": ["flux", "turbo"],
    "local_sd": [],
}

TEXT_ENV_KEYS: dict[str, list[str]] = {
    "cloudflare": ["CF_ACCOUNT_ID", "CF_API_TOKEN"],
    "gemini": ["GEMINI_API_KEY"],
    "groq": ["GROQ_API_KEY"],
    "openrouter": ["OPENROUTER_API_KEY"],
    "ollama": [],
}

IMAGE_ENV_KEYS: dict[str, list[str]] = {
    "cloudflare": ["CF_ACCOUNT_ID", "CF_API_TOKEN"],
    "gemini_imagen": ["GEMINI_API_KEY"],
    "together_flux": ["TOGETHER_API_KEY"],
    "pollinations": [],
    "local_sd": [],
}

_ollama_last_model: str | None = None
_groq_disabled_models: set[str] = set()
_groq_rate_limited_until: float = 0.0
_gemini_text_blocked_until: float = 0.0

DEFAULT_SETTINGS: dict[str, Any] = {
    "text_provider": "cloudflare",
    "text_model": TEXT_MODELS["cloudflare"][0],
    "image_provider": "cloudflare",
    "image_model": IMAGE_MODELS["cloudflare"][0],
    "text_fallback_on_quota": True,
    "text_fallback_include_gemini": False,
    "text_fallback_chain": ["cloudflare", "ollama", "openrouter"],
    "text_fallback_models": {},
    "image_fallback_on_error": True,
}

TEXT_FALLBACK_ORDER = ("cloudflare", "groq", "openrouter", "ollama", "gemini")
DEFAULT_TEXT_FALLBACK_CHAIN = ["cloudflare", "ollama", "openrouter"]


def normalize_text_fallback_chain(settings: dict[str, Any], primary: str) -> list[str]:
    """Пользовательский порядок резерва из ai_settings (без дублей и без primary)."""
    raw = settings.get("text_fallback_chain")
    if not isinstance(raw, list) or not raw:
        raw = list(DEFAULT_TEXT_FALLBACK_CHAIN)
    include_gemini = bool(settings.get("text_fallback_include_gemini", False)) or primary == "gemini"
    out: list[str] = []
    for item in raw:
        prov = str(item).strip()
        if prov not in TEXT_PROVIDERS or prov == primary or prov in out:
            continue
        if prov == "gemini" and not include_gemini:
            continue
        out.append(prov)
    return out


def text_fallback_chain(settings: dict[str, Any], primary: str) -> tuple[str, ...]:
    return tuple(normalize_text_fallback_chain(settings, primary))


def text_model_for_provider(settings: dict[str, Any], provider: str) -> str:
    overrides = settings.get("text_fallback_models")
    if isinstance(overrides, dict):
        custom = str(overrides.get(provider, "")).strip()
        if custom and not custom.startswith("("):
            return custom
    if provider == "ollama":
        models = get_ollama_models(start_if_needed=False)
        return models[0] if models else ""
    models = TEXT_MODELS.get(provider, [])
    return models[0] if models else ""


def _load_env() -> None:
    load_dotenv(ROOT / ".env")


def load_ai_settings() -> dict[str, Any]:
    if not SETTINGS_PATH.is_file():
        save_ai_settings(DEFAULT_SETTINGS.copy())
        return DEFAULT_SETTINGS.copy()
    try:
        data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError:
        data = {}
    merged = {**DEFAULT_SETTINGS, **data}
    tp = str(merged.get("text_provider", "cloudflare"))
    if tp not in TEXT_PROVIDERS:
        tp = "cloudflare"
    merged["text_provider"] = tp
    models = list(TEXT_MODELS.get(tp, TEXT_MODELS["cloudflare"]))
    if tp == "ollama":
        models = get_ollama_models(start_if_needed=False) or models
    tm = str(merged.get("text_model", ""))
    if tp == "gemini" and tm in GEMINI_MODEL_ALIASES:
        tm = GEMINI_MODEL_ALIASES[tm]
    if tp == "groq":
        if tm in GROQ_MODEL_ALIASES:
            tm = GROQ_MODEL_ALIASES[tm]
        if tm not in models:
            tm = GROQ_MODEL_ALIASES.get(tm, models[0] if models else tm)
    if tp == "ollama" and models:
        if tm not in models:
            merged["text_model"] = models[0]
        else:
            merged["text_model"] = tm
    elif tm not in models and models:
        merged["text_model"] = models[0]
    elif models:
        merged["text_model"] = tm
    ip = str(merged.get("image_provider", "cloudflare"))
    if ip not in IMAGE_PROVIDERS:
        ip = "cloudflare"
    merged["image_provider"] = ip
    imodels = list(IMAGE_MODELS.get(ip, IMAGE_MODELS["cloudflare"]))
    if ip == "local_sd":
        from local_sd import list_local_sd_models

        imodels = list_local_sd_models()
    if str(merged.get("image_model", "")) not in imodels and imodels:
        merged["image_model"] = imodels[0]
    elif imodels:
        merged["image_model"] = str(merged.get("image_model", imodels[0]))
    chain = merged.get("text_fallback_chain")
    if not isinstance(chain, list):
        merged["text_fallback_chain"] = list(DEFAULT_TEXT_FALLBACK_CHAIN)
    fb_models = merged.get("text_fallback_models")
    if not isinstance(fb_models, dict):
        merged["text_fallback_models"] = {}
    return merged


def save_ai_settings(settings: dict[str, Any]) -> None:
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    frame = {**DEFAULT_SETTINGS, **settings}
    SETTINGS_PATH.write_text(json.dumps(frame, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def provider_has_key(provider: str, *, kind: str = "text") -> bool:
    _load_env()
    if provider == "ollama":
        return ollama_server_up()
    if provider == "local_sd":
        from local_sd import local_sd_status

        settings = load_ai_settings()
        return bool(local_sd_status(model_name=str(settings.get("image_model", ""))).get("ready"))
    keys = TEXT_ENV_KEYS if kind == "text" else IMAGE_ENV_KEYS
    names = keys.get(provider, [])
    if not names:
        return True
    return all(os.getenv(name, "").strip() for name in names)


def ollama_base_url() -> str:
    _load_env()
    return os.getenv("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")


def ollama_server_up() -> bool:
    try:
        response = requests.get(f"{ollama_base_url()}/api/tags", timeout=(2, 8))
        return response.ok
    except requests.RequestException:
        return False


def ensure_ollama_server() -> bool:
    if ollama_server_up():
        return True
    try:
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
        subprocess.Popen(
            ["ollama", "serve"],
            cwd=str(ROOT),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
        )
    except OSError as exc:
        print(f"[ai_router] ollama serve: {exc}", file=sys.stderr)
        return False
    time.sleep(3)
    return ollama_server_up()


def get_ollama_models(*, start_if_needed: bool = False) -> list[str]:
    if start_if_needed:
        if not ensure_ollama_server():
            return []
    elif not ollama_server_up():
        return []
    try:
        response = requests.get(f"{ollama_base_url()}/api/tags", timeout=(2, 15))
        if not response.ok:
            return []
        data = response.json()
        models = data.get("models") if isinstance(data, dict) else []
        names: list[str] = []
        for row in models or []:
            if isinstance(row, dict):
                name = str(row.get("name", "")).strip()
                if name:
                    names.append(name)
        return sorted(set(names))
    except (requests.RequestException, json.JSONDecodeError):
        return []


def unload_ollama_model(model: str | None = None) -> None:
    """Выгрузить модель из ОЗУ (keep_alive=0)."""
    global _ollama_last_model
    settings = load_ai_settings()
    if str(settings.get("text_provider")) != "ollama":
        return
    name = (model or _ollama_last_model or str(settings.get("text_model", ""))).strip()
    if not name:
        return
    if not ollama_server_up():
        return
    body = {
        "model": name,
        "messages": [{"role": "user", "content": " "}],
        "stream": False,
        "keep_alive": 0,
    }
    try:
        requests.post(f"{ollama_base_url()}/api/chat", json=body, timeout=(5, 30))
        print(f"[ai_router] ollama unload: {name}", flush=True)
    except requests.RequestException as exc:
        print(f"[ai_router] ollama unload failed: {exc}", file=sys.stderr)
    _ollama_last_model = None


def _http_timeout() -> tuple[float, float]:
    _load_env()
    connect = float(os.getenv("TEXT_HTTP_CONNECT_TIMEOUT", str(TEXT_HTTP_CONNECT_TIMEOUT)))
    read = float(os.getenv("TEXT_HTTP_READ_TIMEOUT", str(TEXT_HTTP_READ_TIMEOUT)))
    return (connect, read)


def _gemini_http_timeout() -> tuple[float, float]:
    """Между SSE-чанками Gemini; длинные ответы (перевод сценария)."""
    _load_env()
    connect = float(os.getenv("GEMINI_CONNECT_TIMEOUT", "60"))
    read = float(os.getenv("GEMINI_READ_TIMEOUT", "900"))
    return (connect, read)


def _apply_socket_read_timeout(response: requests.Response, read_sec: float) -> None:
    """У requests stream=True иначе между чанками часто остаётся короткий socket timeout."""
    raw = getattr(response, "raw", None)
    if not raw:
        return
    sock = getattr(raw, "sock", None)
    if sock is not None:
        try:
            sock.settimeout(read_sec)
        except OSError:
            pass


def _ollama_http_timeout() -> tuple[float, float]:
    """Ollama: долгая генерация; read — пауза между чанками при stream=True."""
    _load_env()
    connect = float(os.getenv("OLLAMA_CONNECT_TIMEOUT", str(TEXT_HTTP_CONNECT_TIMEOUT)))
    read = float(os.getenv("OLLAMA_READ_TIMEOUT", "600"))
    return (connect, read)


def _ollama_num_ctx() -> int:
    _load_env()
    try:
        return max(2048, int(os.getenv("OLLAMA_NUM_CTX", "16384")))
    except ValueError:
        return 16384


def _ollama_clamp_predict(messages: list[dict[str, str]], max_tokens: int, num_ctx: int) -> int:
    """prompt + num_predict должны помещаться в num_ctx."""
    chars = sum(len(str(m.get("content", ""))) for m in messages)
    est_in = max(1, chars // 3)
    headroom = num_ctx - est_in - 128
    if headroom < 256:
        return min(max_tokens, max(128, headroom))
    return min(max_tokens, headroom)


def _is_quota_error(status: int, message: str) -> bool:
    if status in (402, 429):
        return True
    lower = message.lower()
    return any(x in lower for x in ("quota", "rate limit", "insufficient", "429", "402", "exceeded"))


def _should_fallback_text(status: int, message: str) -> bool:
    """Резервный провайдер при квоте, таймауте, 404 модели и типичных сбоях сети."""
    if _is_quota_error(status, message):
        return True
    if status in (404, 408, 502, 503, 504):
        return True
    lower = message.lower()
    if status == 0:
        return True
    if status == 400 and any(
        x in lower
        for x in (
            "decommission",
            "does not exist",
            "invalid_request",
            "model_decommissioned",
            "no longer supported",
            "context length",
            "longer than the context",
        )
    ):
        return True
    return any(
        x in lower
        for x in (
            "timed out",
            "timeout",
            "read timeout",
            "connectionpool",
            "connection aborted",
            "connection reset",
            "no longer available",
            "not found",
            "decommissioned",
            "temporarily unavailable",
        )
    )


def _messages_to_openai(messages: list[dict[str, str]]) -> list[dict[str, str]]:
    return [{"role": m.get("role", "user"), "content": str(m.get("content", ""))} for m in messages]


def _messages_to_gemini(messages: list[dict[str, str]]) -> tuple[str | None, list[dict[str, Any]]]:
    system_parts: list[str] = []
    contents: list[dict[str, Any]] = []
    for msg in messages:
        role = msg.get("role", "user")
        text = str(msg.get("content", ""))
        if role == "system":
            system_parts.append(text)
            continue
        g_role = "user" if role == "user" else "model"
        contents.append({"role": g_role, "parts": [{"text": text}]})
    system = "\n\n".join(system_parts).strip() or None
    if not contents:
        contents = [{"role": "user", "parts": [{"text": " "}]}]
    return system, contents


def _generate_text_cloudflare(
    messages: list[dict[str, str]],
    *,
    model: str,
    max_tokens: int,
    temperature: float,
) -> tuple[str, str | None, int]:
    account = os.getenv("CF_ACCOUNT_ID", "").strip()
    token = os.getenv("CF_API_TOKEN", "").strip()
    if not account or not token:
        return "", "Cloudflare: задайте CF_ACCOUNT_ID и CF_API_TOKEN в .env", 0
    url = f"https://api.cloudflare.com/client/v4/accounts/{account}/ai/run/{model}"
    payload = {"messages": messages, "max_tokens": max_tokens, "temperature": temperature}
    try:
        response = requests.post(
            url,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json=payload,
            timeout=_http_timeout(),
        )
        body = response.json() if response.content else {}
        if not response.ok:
            detail = body.get("errors") or body
            return "", f"Workers AI {response.status_code}: {detail}", response.status_code
        result = body.get("result") or {}
        text = result.get("response") if isinstance(result, dict) else result
        if not text and isinstance(result, dict):
            text = result.get("text") or result.get("output") or ""
        return str(text).strip(), None, response.status_code
    except requests.RequestException as exc:
        return "", str(exc), 0


def _generate_text_groq(
    messages: list[dict[str, str]],
    *,
    model: str,
    max_tokens: int,
    temperature: float,
) -> tuple[str, str | None, int]:
    global _groq_rate_limited_until
    resolved = GROQ_MODEL_ALIASES.get(model, model)
    if monotonic() < _groq_rate_limited_until:
        return "", "Groq: rate limit (кэш)", 429
    if resolved in _groq_disabled_models:
        return "", f"Groq: модель {resolved} недоступна", 404
    key = os.getenv("GROQ_API_KEY", "").strip()
    if not key:
        return "", "Groq: задайте GROQ_API_KEY в .env", 0
    model = resolved
    payload = {
        "model": model,
        "messages": _messages_to_openai(messages),
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    try:
        response = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json=payload,
            timeout=_http_timeout(),
        )
        if not response.ok:
            body_low = response.text.lower()
            if response.status_code == 429:
                _groq_rate_limited_until = monotonic() + 90.0
            if response.status_code in (404, 400) and any(
                x in body_low for x in ("does not exist", "decommission", "no longer supported")
            ):
                _groq_disabled_models.add(model)
            return "", f"Groq {response.status_code}: {response.text[:400]}", response.status_code
        data = response.json()
        choices = data.get("choices") or []
        if not choices:
            return "", "Groq: пустой ответ", response.status_code
        text = choices[0].get("message", {}).get("content", "")
        return str(text).strip(), None, response.status_code
    except requests.RequestException as exc:
        return "", str(exc), 0


def _generate_text_openrouter(
    messages: list[dict[str, str]],
    *,
    model: str,
    max_tokens: int,
    temperature: float,
) -> tuple[str, str | None, int]:
    key = os.getenv("OPENROUTER_API_KEY", "").strip()
    if not key:
        return "", "OpenRouter: задайте OPENROUTER_API_KEY в .env", 0
    payload = {
        "model": model,
        "messages": _messages_to_openai(messages),
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    try:
        response = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://github.com/youtobe-maker",
                "X-Title": "youtobe-maker",
            },
            json=payload,
            timeout=_http_timeout(),
        )
        if not response.ok:
            return "", f"OpenRouter {response.status_code}: {response.text[:400]}", response.status_code
        data = response.json()
        choices = data.get("choices") or []
        if not choices:
            return "", "OpenRouter: пустой ответ", response.status_code
        text = choices[0].get("message", {}).get("content", "")
        return str(text).strip(), None, response.status_code
    except requests.RequestException as exc:
        return "", str(exc), 0


def _parse_gemini_sse_chunks(raw_line: str) -> str:
    line = raw_line.strip()
    if not line or line == "[DONE]":
        return ""
    if line.startswith("data:"):
        line = line[5:].strip()
    if not line.startswith("{"):
        return ""
    try:
        data = json.loads(line)
    except json.JSONDecodeError:
        return ""
    candidates = data.get("candidates") or []
    if not candidates:
        return ""
    parts = candidates[0].get("content", {}).get("parts") or []
    return "".join(str(p.get("text", "")) for p in parts)


def _generate_text_gemini(
    messages: list[dict[str, str]],
    *,
    model: str,
    max_tokens: int,
    temperature: float,
) -> tuple[str, str | None, int]:
    global _gemini_text_blocked_until
    if monotonic() < _gemini_text_blocked_until:
        return "", "Gemini: квота free tier (кэш)", 429
    key = os.getenv("GEMINI_API_KEY", "").strip()
    if not key:
        return "", "Gemini: задайте GEMINI_API_KEY в .env", 0
    system, contents = _messages_to_gemini(messages)
    _load_env()
    use_stream = os.getenv("GEMINI_STREAM", "1").strip().lower() not in {"0", "false", "no"}
    body: dict[str, Any] = {
        "contents": contents,
        "generationConfig": {
            "temperature": temperature,
            "maxOutputTokens": max_tokens,
        },
    }
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}
    try:
        if use_stream:
            connect_t, read_t = _gemini_http_timeout()
            url = (
                f"https://generativelanguage.googleapis.com/v1beta/models/{model}:streamGenerateContent"
                f"?alt=sse&key={key}"
            )
            response = requests.post(
                url,
                json=body,
                stream=True,
                timeout=(connect_t, read_t),
            )
            if not response.ok:
                if response.status_code == 429:
                    _gemini_text_blocked_until = monotonic() + 3600.0
                err_body = response.text[:400] if hasattr(response, "text") else ""
                return "", f"Gemini {response.status_code}: {err_body}", response.status_code
            _apply_socket_read_timeout(response, read_t)
            chunks: list[str] = []
            for raw_line in response.iter_lines(decode_unicode=True):
                if not raw_line:
                    continue
                piece = _parse_gemini_sse_chunks(raw_line)
                if piece:
                    chunks.append(piece)
            text = "".join(chunks).strip()
            status = response.status_code
        else:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
            response = requests.post(url, json=body, timeout=_http_timeout())
            status = response.status_code
            if not response.ok:
                if status == 429:
                    _gemini_text_blocked_until = monotonic() + 3600.0
                return "", f"Gemini {status}: {response.text[:400]}", status
            data = response.json()
            candidates = data.get("candidates") or []
            if not candidates:
                return "", f"Gemini: нет candidates — {data!s}"[:400], status
            parts = candidates[0].get("content", {}).get("parts") or []
            text = "".join(str(p.get("text", "")) for p in parts).strip()
        if not text:
            return "", "Gemini: пустой текст", status if use_stream else response.status_code
        return text, None, status if use_stream else response.status_code
    except requests.RequestException as exc:
        return "", str(exc), 0


def _generate_text_ollama(
    messages: list[dict[str, str]],
    *,
    model: str,
    max_tokens: int,
    temperature: float,
) -> tuple[str, str | None, int]:
    global _ollama_last_model
    if not ensure_ollama_server():
        return "", "Ollama: сервер недоступен (запустите ollama serve)", 0
    ollama_messages = [
        {"role": str(m.get("role", "user")), "content": str(m.get("content", ""))}
        for m in messages
    ]
    num_ctx = _ollama_num_ctx()
    num_predict = _ollama_clamp_predict(ollama_messages, max_tokens, num_ctx)
    body: dict[str, Any] = {
        "model": model,
        "messages": ollama_messages,
        "stream": True,
        "keep_alive": "5m",
        "options": {
            "temperature": temperature,
            "num_ctx": num_ctx,
            "num_predict": num_predict,
        },
    }
    _ollama_last_model = model
    try:
        response = requests.post(
            f"{ollama_base_url()}/api/chat",
            json=body,
            stream=True,
            timeout=_ollama_http_timeout(),
        )
        if not response.ok:
            err_body = response.text[:400] if hasattr(response, "text") else ""
            return "", f"Ollama {response.status_code}: {err_body}", response.status_code
        chunks: list[str] = []
        for raw_line in response.iter_lines(decode_unicode=True):
            if not raw_line:
                continue
            try:
                data = json.loads(raw_line)
            except json.JSONDecodeError:
                continue
            if not isinstance(data, dict):
                continue
            msg = data.get("message") or {}
            part = str(msg.get("content", ""))
            if part:
                chunks.append(part)
            if data.get("done"):
                break
        text = "".join(chunks).strip()
        if not text:
            return "", "Ollama: пустой ответ", response.status_code
        return text, None, response.status_code
    except requests.RequestException as exc:
        return "", str(exc), 0


def _generate_text_one(
    provider: str,
    model: str,
    messages: list[dict[str, str]],
    *,
    max_tokens: int,
    temperature: float,
) -> tuple[str, str | None, int]:
    if provider == "cloudflare":
        return _generate_text_cloudflare(messages, model=model, max_tokens=max_tokens, temperature=temperature)
    if provider == "groq":
        return _generate_text_groq(messages, model=model, max_tokens=max_tokens, temperature=temperature)
    if provider == "openrouter":
        return _generate_text_openrouter(messages, model=model, max_tokens=max_tokens, temperature=temperature)
    if provider == "gemini":
        return _generate_text_gemini(messages, model=model, max_tokens=max_tokens, temperature=temperature)
    if provider == "ollama":
        return _generate_text_ollama(messages, model=model, max_tokens=max_tokens, temperature=temperature)
    return "", f"Неизвестный text_provider: {provider}", 0


def generate_text(
    messages: list[dict[str, str]],
    *,
    temperature: float = 0.7,
    max_tokens: int = 2000,
) -> tuple[str, str | None]:
    """Текст по ai_settings.json; при ошибке — резерв (квота, таймаут, 404 модели, сеть)."""
    _load_env()
    settings = load_ai_settings()
    primary = str(settings.get("text_provider", "cloudflare"))
    model = str(settings.get("text_model", TEXT_MODELS.get(primary, [""])[0]))
    fallback = bool(settings.get("text_fallback_on_quota", True))
    fallback_order = text_fallback_chain(settings, primary)

    providers_try: list[str] = []
    if primary == "groq" and monotonic() < _groq_rate_limited_until:
        providers_try = []
    elif provider_has_key(primary, kind="text") or primary == "ollama":
        providers_try = [primary]
    elif primary:
        providers_try = [primary]

    if fallback:
        for candidate in fallback_order:
            if candidate == primary or candidate in providers_try:
                continue
            if candidate == "groq" and monotonic() < _groq_rate_limited_until:
                continue
            if candidate == "gemini" and monotonic() < _gemini_text_blocked_until:
                continue
            if candidate == "ollama":
                if ollama_server_up() or get_ollama_models(start_if_needed=False):
                    providers_try.append(candidate)
                continue
            if provider_has_key(candidate, kind="text"):
                providers_try.append(candidate)

    last_err = ""
    for prov in providers_try:
        if prov == primary:
            use_model = model
        else:
            use_model = text_model_for_provider(settings, prov)
            if prov == "groq" and use_model in GROQ_MODEL_ALIASES:
                use_model = GROQ_MODEL_ALIASES[use_model]
            if not use_model:
                continue
        text, err, status = _generate_text_one(
            prov, use_model, messages, max_tokens=max_tokens, temperature=temperature
        )
        if not err and text:
            if prov != primary:
                print(f"[ai_router] текст: резерв {prov} / {use_model}", flush=True)
            return text, None
        last_err = err or "пустой ответ"
        if (
            not text
            and prov == primary
            and _should_fallback_text(status, last_err)
            and "timed out" in last_err.lower()
        ):
            print(f"[ai_router] текст {prov}: таймаут, повтор…", flush=True)
            text2, err2, status2 = _generate_text_one(
                prov, use_model, messages, max_tokens=max_tokens, temperature=temperature
            )
            if not err2 and text2:
                return text2, None
            last_err = err2 or last_err
            status = status2
        if not fallback or not _should_fallback_text(status, last_err):
            break
        print(f"[ai_router] текст {prov} отказ ({status}): {last_err[:120]} → другой провайдер", flush=True)

    return "", last_err or "Генерация текста не удалась"


# --- Images ---

POLLINATIONS_WATERMARK_CROP = 45
IMAGE_WIDTH = 1920
IMAGE_HEIGHT = 1080


def _bytes_to_jpeg(image: Image.Image, *, pollinations: bool = False) -> bytes:
    if pollinations and image.height > POLLINATIONS_WATERMARK_CROP + 100:
        image = image.crop((0, 0, image.width, image.height - POLLINATIONS_WATERMARK_CROP))
    if image.size != (IMAGE_WIDTH, IMAGE_HEIGHT):
        image = image.resize((IMAGE_WIDTH, IMAGE_HEIGHT), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    image.save(buf, format="JPEG", quality=92, optimize=True)
    return buf.getvalue()


def _pollinations_urls(prompt: str, model: str) -> list[str]:
    encoded = quote(prompt, safe="")
    base = f"https://image.pollinations.ai/prompt/{encoded}"
    common = f"width={IMAGE_WIDTH}&height={IMAGE_HEIGHT}&nologo=true&enhance=false"
    urls = [f"{base}?{common}&model={model}"]
    if model != "turbo":
        urls.append(f"{base}?{common}&model=turbo")
    urls.append(f"{base}?{common}")
    return urls


def _fetch_image_cloudflare(session: requests.Session, prompt: str, model: str) -> bytes | None:
    account = os.getenv("CF_ACCOUNT_ID", "").strip()
    token = os.getenv("CF_API_TOKEN", "").strip()
    if not account or not token:
        return None
    url = f"https://api.cloudflare.com/client/v4/accounts/{account}/ai/run/{model}"
    payload = {"prompt": prompt.strip()[:2048], "steps": 4}
    response = session.post(
        url,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json=payload,
        timeout=(20, 180),
    )
    if not response.ok:
        raise RuntimeError(f"CF image {response.status_code}: {response.text[:200]}")
    content_type = (response.headers.get("Content-Type") or "").lower()
    if "application/json" in content_type or (response.text or "")[:1] in ("{", "["):
        body = response.json()
        result = body.get("result") or body
        if isinstance(result, list):
            result = result[0] if result else {}
        image_b64 = result.get("image") if isinstance(result, dict) else None
        if not image_b64:
            raise RuntimeError("CF: нет image в JSON")
        if isinstance(image_b64, str) and image_b64.startswith("data:") and "," in image_b64:
            image_b64 = image_b64.split(",", 1)[1]
        raw = base64.b64decode(image_b64)
        img = Image.open(io.BytesIO(raw)).convert("RGB")
    else:
        img = Image.open(io.BytesIO(response.content)).convert("RGB")
    return _bytes_to_jpeg(img)


def _fetch_image_together(session: requests.Session, prompt: str, model: str) -> bytes | None:
    key = os.getenv("TOGETHER_API_KEY", "").strip()
    if not key:
        return None
    response = session.post(
        "https://api.together.xyz/v1/images/generations",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={
            "model": model,
            "prompt": prompt[:2048],
            "width": 1280,
            "height": 720,
            "steps": 4,
            "n": 1,
        },
        timeout=(20, 180),
    )
    if not response.ok:
        raise RuntimeError(f"Together {response.status_code}: {response.text[:200]}")
    data = response.json()
    row = (data.get("data") or [{}])[0]
    b64 = row.get("b64_json")
    url = row.get("url")
    if b64:
        raw = base64.b64decode(b64)
        img = Image.open(io.BytesIO(raw)).convert("RGB")
        return _bytes_to_jpeg(img)
    if url:
        img_resp = session.get(url, timeout=(20, 180))
        img_resp.raise_for_status()
        img = Image.open(io.BytesIO(img_resp.content)).convert("RGB")
        return _bytes_to_jpeg(img)
    raise RuntimeError("Together: нет b64_json/url")


def _fetch_image_gemini(session: requests.Session, prompt: str, model: str) -> bytes | None:
    key = os.getenv("GEMINI_API_KEY", "").strip()
    if not key:
        return None
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:predict?key={key}"
    body = {
        "instances": [{"prompt": prompt[:2048]}],
        "parameters": {"sampleCount": 1, "aspectRatio": "16:9"},
    }
    response = session.post(url, json=body, timeout=(20, 180))
    if not response.ok:
        raise RuntimeError(f"Gemini Imagen {response.status_code}: {response.text[:200]}")
    data = response.json()
    predictions = data.get("predictions") or data.get("generatedImages") or []
    if not predictions:
        raise RuntimeError(f"Gemini Imagen: пустой ответ {str(data)[:200]}")
    row = predictions[0]
    b64 = row.get("bytesBase64Encoded") or row.get("image", {}).get("imageBytes")
    if not b64:
        raise RuntimeError("Gemini Imagen: нет base64")
    raw = base64.b64decode(b64)
    img = Image.open(io.BytesIO(raw)).convert("RGB")
    return _bytes_to_jpeg(img)


def _fetch_image_pollinations(session: requests.Session, prompt: str, model: str) -> bytes | None:
    last_exc: Exception | None = None
    for url in _pollinations_urls(prompt, model):
        try:
            response = session.get(url, timeout=(20, 180))
            response.raise_for_status()
            img = Image.open(io.BytesIO(response.content)).convert("RGB")
            return _bytes_to_jpeg(img, pollinations=True)
        except (requests.RequestException, OSError) as exc:
            last_exc = exc
    if last_exc:
        raise RuntimeError(str(last_exc))
    return None


def fetch_image_for_prompt(session: requests.Session, prompt: str) -> bytes | None:
    """Картинка по image_provider / image_model из ai_settings.json."""
    _load_env()
    settings = load_ai_settings()
    provider = str(settings.get("image_provider", "cloudflare"))
    model = str(settings.get("image_model", IMAGE_MODELS["cloudflare"][0]))
    fallback = bool(settings.get("image_fallback_on_error", True))

    providers = [provider]
    if fallback and provider != "pollinations":
        providers.append("pollinations")
    if fallback and provider == "local_sd" and "cloudflare" not in providers:
        providers.insert(1, "cloudflare")

    last_err: Exception | None = None
    for prov in providers:
        if prov == provider:
            use_model = model
        elif prov == "local_sd":
            from local_sd import list_local_sd_models

            lm = list_local_sd_models()
            use_model = lm[0] if lm else model
        else:
            use_model = IMAGE_MODELS.get(prov, IMAGE_MODELS["pollinations"])[0]
        try:
            if prov == "cloudflare":
                data = _fetch_image_cloudflare(session, prompt, use_model)
            elif prov == "together_flux":
                data = _fetch_image_together(session, prompt, use_model)
            elif prov == "gemini_imagen":
                data = _fetch_image_gemini(session, prompt, use_model)
            elif prov == "pollinations":
                data = _fetch_image_pollinations(session, prompt, use_model)
            elif prov == "local_sd":
                from local_sd import fetch_one_local_sd

                data = fetch_one_local_sd(prompt, use_model)
            else:
                continue
            if data:
                if prov != provider:
                    print(f"[ai_router] картинка: резерв {prov}", flush=True)
                return data
        except (requests.RequestException, RuntimeError, OSError, ValueError) as exc:
            last_err = exc
            print(f"[ai_router] image {prov} failed: {exc}", flush=True)
    if last_err:
        print(f"[ai_router] image failed: {last_err}", file=sys.stderr)
    return None