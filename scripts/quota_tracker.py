"""Учёт квот провайдеров генерации и API."""

from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
SPECS_PATH = ROOT / "config" / "providers_specs.json"
USAGE_PATH = ROOT / "logs" / "quota_usage.json"
LOGS_DIR = ROOT / "logs"
MAX_EVENTS = 40
CF_FREE_NEURONS_DAILY = 10_000
CF_NEURONS_PER_IMAGE = 173
CF_GRAPHQL_URL = "https://api.cloudflare.com/client/v4/graphql"

_lock = threading.Lock()
LIMIT_HIT_CODES = {"402", "429"}

Status = str  # "green" | "yellow" | "red" | "unlimited" | "inactive"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _period_key(period: str, moment: datetime | None = None) -> str:
    moment = moment or _utc_now()
    if period == "daily":
        return moment.strftime("%Y-%m-%d")
    if period == "hourly":
        return moment.strftime("%Y-%m-%dT%H")
    if period == "minute":
        return moment.strftime("%Y-%m-%dT%H:%M")
    return moment.strftime("%Y-%m-%d")


def load_specs() -> dict[str, Any]:
    if not SPECS_PATH.is_file():
        return {"providers": {}}
    try:
        data = json.loads(SPECS_PATH.read_text(encoding="utf-8-sig"))
        return data if isinstance(data, dict) else {"providers": {}}
    except json.JSONDecodeError:
        return {"providers": {}}


def save_specs(data: dict[str, Any]) -> None:
    SPECS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SPECS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def provider_spec(provider_id: str) -> dict[str, Any] | None:
    return (load_specs().get("providers") or {}).get(provider_id)


def _default_usage_row(provider_id: str) -> dict[str, Any]:
    spec = provider_spec(provider_id) or {}
    period = spec.get("cap_period", "daily")
    day_key = _period_key("daily")
    return {
        "provider_id": provider_id,
        "day_key": day_key,
        "daily_count": 0,
        "period_key": _period_key(period),
        "period_count": 0,
        "count": 0,
        "failed_attempts": 0,
        "limit_exhausted": False,
        "limit_exhausted_at": None,
        "last_error_code": None,
        "last_success_at": None,
        "events": [],
    }


def _migrate_row(row: dict[str, Any]) -> dict[str, Any]:
    if "daily_count" not in row:
        row["daily_count"] = int(row.get("count", 0))
    if "period_count" not in row:
        row["period_count"] = int(row.get("count", 0))
    row["count"] = int(row.get("period_count", row.get("count", 0)))
    row.setdefault("failed_attempts", 0)
    row.setdefault("events", [])
    row.setdefault("day_key", _period_key("daily"))
    return row


def _append_event(row: dict[str, Any], event_type: str, amount: int = 1, note: str = "") -> None:
    events: list[dict[str, Any]] = list(row.get("events") or [])
    events.append(
        {
            "at": _utc_now().isoformat(timespec="seconds"),
            "type": event_type,
            "amount": amount,
            "note": note[:200],
        }
    )
    row["events"] = events[-MAX_EVENTS:]


def _load_usage() -> dict[str, Any]:
    if not USAGE_PATH.is_file():
        return {"updated_at": None, "providers": {}}
    try:
        data = json.loads(USAGE_PATH.read_text(encoding="utf-8-sig"))
        if not isinstance(data, dict):
            return {"updated_at": None, "providers": {}}
        data.setdefault("providers", {})
        return data
    except json.JSONDecodeError:
        return {"updated_at": None, "providers": {}}


def _save_usage(data: dict[str, Any]) -> None:
    USAGE_PATH.parent.mkdir(parents=True, exist_ok=True)
    data["updated_at"] = _utc_now().isoformat(timespec="seconds")
    USAGE_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _rollover_row(provider_id: str, row: dict[str, Any]) -> dict[str, Any]:
    spec = provider_spec(provider_id) or {}
    period = spec.get("cap_period", "daily")
    row = _migrate_row(row)
    day_key = _period_key("daily")
    if row.get("day_key") != day_key:
        row["day_key"] = day_key
        row["daily_count"] = 0
        row["failed_attempts"] = 0
        row["limit_exhausted"] = False
        row["limit_exhausted_at"] = None
    current_key = _period_key(period)
    if row.get("period_key") != current_key:
        row["period_key"] = current_key
        row["period_count"] = 0
        row["count"] = 0
    cap = _cap_for(provider_id)
    used = int(row.get("period_count", row.get("count", 0)))
    if cap is not None and used < cap:
        row["limit_exhausted"] = False
        row["limit_exhausted_at"] = None
    return row


def get_usage_snapshot(*, persist: bool = True) -> dict[str, dict[str, Any]]:
    with _lock:
        data = _load_usage()
        providers = data.get("providers", {})
        out: dict[str, dict[str, Any]] = {}
        for provider_id in (load_specs().get("providers") or {}):
            row = _rollover_row(provider_id, providers.get(provider_id, _default_usage_row(provider_id)))
            row["count"] = int(row.get("period_count", 0))
            providers[provider_id] = row
            out[provider_id] = dict(row)
        data["providers"] = providers
        if persist:
            _save_usage(data)
        return out


def record_use(provider_id: str, amount: int = 1) -> dict[str, Any]:
    if amount <= 0:
        return get_provider_status(provider_id)
    with _lock:
        data = _load_usage()
        providers = data.setdefault("providers", {})
        row = _rollover_row(provider_id, providers.get(provider_id, _default_usage_row(provider_id)))
        row["period_count"] = int(row.get("period_count", 0)) + amount
        row["daily_count"] = int(row.get("daily_count", 0)) + amount
        row["count"] = row["period_count"]
        row["last_success_at"] = _utc_now().isoformat(timespec="seconds")
        _append_event(row, "use", amount)
        providers[provider_id] = row
        _save_usage(data)
        return row


def record_failed_attempt(provider_id: str, error_code: str | int | None = None, message: str = "") -> dict[str, Any]:
    with _lock:
        data = _load_usage()
        providers = data.setdefault("providers", {})
        row = _rollover_row(provider_id, providers.get(provider_id, _default_usage_row(provider_id)))
        row["failed_attempts"] = int(row.get("failed_attempts", 0)) + 1
        code = str(error_code).strip() if error_code is not None else ""
        if code:
            row["last_error_code"] = code
        if message:
            row["last_error_message"] = message[:500]
        _append_event(row, "fail", 1, message[:200] if message else code)
        providers[provider_id] = row
        _save_usage(data)
        return row


def record_limit_hit(provider_id: str, error_code: str | int | None = None, message: str = "") -> dict[str, Any]:
    code = str(error_code).strip() if error_code is not None else ""
    with _lock:
        data = _load_usage()
        providers = data.setdefault("providers", {})
        row = _rollover_row(provider_id, providers.get(provider_id, _default_usage_row(provider_id)))
        cap = _cap_for(provider_id)
        used = int(row.get("period_count", row.get("count", 0)))
        if cap is not None and used >= cap:
            row["limit_exhausted"] = True
            row["limit_exhausted_at"] = _utc_now().isoformat(timespec="seconds")
        row["last_error_code"] = code or row.get("last_error_code")
        row["failed_attempts"] = int(row.get("failed_attempts", 0)) + 1
        if message:
            row["last_error_message"] = message[:500]
        _append_event(row, "limit", 1, message[:200] if message else f"HTTP {code}")
        providers[provider_id] = row
        _save_usage(data)
        return row


def clear_limit_flag(provider_id: str) -> None:
    with _lock:
        data = _load_usage()
        row = data.get("providers", {}).get(provider_id)
        if not row:
            return
        row["limit_exhausted"] = False
        row["limit_exhausted_at"] = None
        _save_usage(data)


def _cap_for(provider_id: str) -> int | None:
    spec = provider_spec(provider_id) or {}
    cap = spec.get("daily_or_hourly_cap")
    if cap is None or spec.get("unit") == "unlimited":
        return None
    try:
        return int(cap)
    except (TypeError, ValueError):
        return None


def usage_ratio(provider_id: str) -> float | None:
    cap = _cap_for(provider_id)
    if cap is None or cap <= 0:
        return None
    snap = get_usage_snapshot(persist=False)
    count = int(snap.get(provider_id, {}).get("period_count", snap.get(provider_id, {}).get("count", 0)))
    return min(1.0, count / cap)


def _period_label(period: str) -> str:
    labels = {"daily": "за сутки", "hourly": "за текущий час", "minute": "за текущую минуту"}
    return labels.get(period, period)


def parse_job_log_stats(text: str) -> dict[str, int]:
    cf_ok = text.count("CF: кадр получен")
    hf_ok = text.count("HF: кадр получен")
    pol_ok = text.count("Pollinations: кадр получен")
    yt_units = sum(int(value) for value in re.findall(r"YouTube API quota: \+(\d+)", text))
    hf_fail = sum(
        1
        for line in text.splitlines()
        if "HF попытка" in line and "не удалась" in line
    )
    pol_fail = sum(
        1
        for line in text.splitlines()
        if "pollinations.ai" in line.lower() and "402" in line
    )
    pol_fail += sum(
        1
        for line in text.splitlines()
        if "Pollinations:" in line and "исчерпаны" in line
    )
    cf_fail = sum(
        1 for line in text.splitlines() if "CF попытка" in line and "не удалась" in line
    )
    cf_limit = any(
        "402" in line or "429" in line for line in text.splitlines() if "CF попытка" in line
    )
    hf_limit = any(
        "402" in line and ("Payment Required" in line or "huggingface" in line.lower() or "HF" in line)
        for line in text.splitlines()
    )
    pol_limit = pol_fail >= 3 and pol_ok == 0
    return {
        "cloudflare_workers_ai_ok": cf_ok,
        "cloudflare_workers_ai_fail": cf_fail,
        "cloudflare_workers_ai_limit": int(cf_limit),
        "huggingface_flux_ok": hf_ok,
        "pollinations_fallback_ok": pol_ok,
        "youtube_data_api_units": yt_units,
        "huggingface_flux_fail": hf_fail,
        "pollinations_fallback_fail": pol_fail,
        "huggingface_flux_limit": int(hf_limit),
        "pollinations_fallback_limit": int(pol_limit),
    }


def sync_usage_from_logs() -> dict[str, Any]:
    """Подтянуть факт из logs/job_*.log (сегодня по дате изменения файла)."""
    today = _utc_now().date()
    totals = {
        "cloudflare_workers_ai_ok": 0,
        "cloudflare_workers_ai_fail": 0,
        "huggingface_flux_ok": 0,
        "pollinations_fallback_ok": 0,
        "youtube_data_api_units": 0,
        "huggingface_flux_fail": 0,
        "pollinations_fallback_fail": 0,
        "files_scanned": 0,
    }
    limits: dict[str, tuple[str, str]] = {}
    if not LOGS_DIR.is_dir():
        return totals
    for path in sorted(LOGS_DIR.glob("job_*.log")):
        try:
            mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).date()
        except OSError:
            continue
        if mtime != today:
            continue
        totals["files_scanned"] += 1
        text = path.read_text(encoding="utf-8", errors="replace")
        parsed = parse_job_log_stats(text)
        totals["cloudflare_workers_ai_ok"] += parsed.get("cloudflare_workers_ai_ok", 0)
        totals["cloudflare_workers_ai_fail"] += parsed.get("cloudflare_workers_ai_fail", 0)
        totals["huggingface_flux_ok"] += parsed["huggingface_flux_ok"]
        totals["pollinations_fallback_ok"] += parsed["pollinations_fallback_ok"]
        totals["youtube_data_api_units"] += parsed["youtube_data_api_units"]
        totals["huggingface_flux_fail"] += parsed["huggingface_flux_fail"]
        totals["pollinations_fallback_fail"] += parsed["pollinations_fallback_fail"]
        if parsed.get("cloudflare_workers_ai_limit"):
            limits["cloudflare_workers_ai"] = ("429", path.name)
        if parsed["huggingface_flux_limit"]:
            limits["huggingface_flux"] = ("402", path.name)
        if parsed["pollinations_fallback_limit"]:
            limits["pollinations_fallback"] = ("402", path.name)

    with _lock:
        data = _load_usage()
        providers = data.setdefault("providers", {})

        def merge(provider_id: str, ok: int, fail: int) -> None:
            if ok <= 0 and fail <= 0 and provider_id not in limits:
                return
            row = _rollover_row(provider_id, providers.get(provider_id, _default_usage_row(provider_id)))
            if ok > 0:
                row["daily_count"] = max(int(row.get("daily_count", 0)), ok)
                row["period_count"] = max(int(row.get("period_count", 0)), ok)
                row["count"] = row["period_count"]
                row["last_success_at"] = _utc_now().isoformat(timespec="seconds")
                _append_event(row, "sync_log", ok, "из logs/job_*.log")
            if fail > 0:
                row["failed_attempts"] = max(int(row.get("failed_attempts", 0)), fail)
            providers[provider_id] = row

        merge(
            "cloudflare_workers_ai",
            totals["cloudflare_workers_ai_ok"],
            totals["cloudflare_workers_ai_fail"],
        )
        merge("huggingface_flux", totals["huggingface_flux_ok"], totals["huggingface_flux_fail"])
        merge("pollinations_fallback", totals["pollinations_fallback_ok"], totals["pollinations_fallback_fail"])

        if totals["youtube_data_api_units"] > 0:
            row = _rollover_row(
                "youtube_data_api",
                providers.get("youtube_data_api", _default_usage_row("youtube_data_api")),
            )
            row["daily_count"] = max(int(row.get("daily_count", 0)), totals["youtube_data_api_units"])
            row["period_count"] = row["daily_count"]
            row["count"] = row["period_count"]
            providers["youtube_data_api"] = row

        for provider_id, (code, note) in limits.items():
            row = _rollover_row(provider_id, providers.get(provider_id, _default_usage_row(provider_id)))
            used = int(row.get("period_count", row.get("count", 0)))
            cap = _cap_for(provider_id)
            if cap is not None and used >= cap:
                row["limit_exhausted"] = True
                row["limit_exhausted_at"] = _utc_now().isoformat(timespec="seconds")
            row["last_error_code"] = code
            _append_event(row, "limit", 1, note)
            providers[provider_id] = row

        data["providers"] = providers
        data["last_log_sync"] = _utc_now().isoformat(timespec="seconds")
        _save_usage(data)
    return totals


def _parse_iso(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def _quota_error_within_last_hour(row: dict[str, Any]) -> bool:
    """402/429 в событиях за последний час (реальный отказ API)."""
    now = _utc_now()
    markers = ("402", "429", "Payment Required", "Too Many Requests")
    for event in reversed(row.get("events") or []):
        if event.get("type") not in ("fail", "limit"):
            continue
        at = _parse_iso(str(event.get("at", "")))
        if at is None or (now - at).total_seconds() > 3600:
            continue
        note = str(event.get("note", ""))
        if any(marker in note for marker in markers):
            return True
    code = str(row.get("last_error_code") or "")
    if code not in LIMIT_HIT_CODES:
        return False
    at = _parse_iso(str(row.get("limit_exhausted_at") or ""))
    return bool(at and (now - at).total_seconds() <= 3600)


def _hf_prepaid_empty(provider_id: str) -> bool:
    if provider_id != "huggingface_flux":
        return False
    remote = get_remote_quota(provider_id)
    if not remote or not remote.get("ok"):
        return False
    balance = float(remote.get("balance_usd") or 0)
    mode = str(remote.get("billing_mode") or "").lower()
    if balance <= 0.0 and ("prepaid" in mode or mode == "prepaid"):
        return True
    if balance <= 0.0:
        return True
    return False


def _credentials_configured(provider_id: str) -> bool:
    if provider_id == "cloudflare_workers_ai":
        return bool(_load_env_token("CF_ACCOUNT_ID") and _load_env_token("CF_API_TOKEN"))
    if provider_id == "huggingface_flux":
        return bool(_load_env_token("HF_TOKEN"))
    if provider_id == "youtube_data_api":
        return bool(_load_env_token("YOUTUBE_API_KEY"))
    if provider_id == "pollinations_fallback":
        remote = get_remote_quota(provider_id)
        if remote is None:
            return True
        return bool(remote.get("ok"))
    return True


def status_for(provider_id: str) -> tuple[Status, str]:
    spec = provider_spec(provider_id) or {}
    if spec.get("unit") == "unlimited" or _cap_for(provider_id) is None:
        return "unlimited", "∞"

    if provider_id == "huggingface_flux":
        if not _load_env_token("HF_TOKEN"):
            return "inactive", "⚪ HF_TOKEN не задан"
        if _hf_prepaid_empty(provider_id):
            return "inactive", "⚪ Не пополнен (Prepaid $0)"

    if provider_id == "cloudflare_workers_ai":
        if not _credentials_configured(provider_id):
            return "inactive", "⚪ CF ключи не заданы"

    snap = get_usage_snapshot(persist=False)
    row = snap.get(provider_id, {})
    cap = _cap_for(provider_id) or 0
    count = int(row.get("period_count", row.get("count", 0)))
    ratio = (count / cap) if cap > 0 else 0.0

    if cap > 0 and count >= cap:
        return "red", "🔴 Лимит исчерпан"
    if _quota_error_within_last_hour(row):
        return "red", "🔴 Лимит исчерпан"

    if ratio >= 0.8:
        return "yellow", "🟡 Близко к лимиту"

    if provider_id == "pollinations_fallback":
        remote = get_remote_quota(provider_id)
        if remote is not None and not remote.get("ok"):
            return "yellow", "🟡 Сервис недоступен (проба ≠ 200)"

    if not _credentials_configured(provider_id):
        return "inactive", "⚪ Ключи не заданы"

    return "green", "🟢 Норма"


def get_provider_status(provider_id: str) -> dict[str, Any]:
    spec = provider_spec(provider_id) or {}
    snap = get_usage_snapshot(persist=False)
    row = snap.get(provider_id, {})
    cap = _cap_for(provider_id)
    period_count = int(row.get("period_count", row.get("count", 0)))
    daily_count = int(row.get("daily_count", period_count))
    status, label = status_for(provider_id)
    remaining = None if cap is None else max(0, cap - period_count)
    return {
        "provider_id": provider_id,
        "name": spec.get("name", provider_id),
        "count": period_count,
        "daily_count": daily_count,
        "failed_attempts": int(row.get("failed_attempts", 0)),
        "cap": cap,
        "remaining": remaining,
        "unit": spec.get("unit", "units"),
        "period_key": row.get("period_key"),
        "day_key": row.get("day_key"),
        "cap_period": spec.get("cap_period", "daily"),
        "period_label": _period_label(str(spec.get("cap_period", "daily"))),
        "status": status,
        "status_label": label,
        "limit_exhausted": bool(row.get("limit_exhausted")),
        "last_error_code": row.get("last_error_code"),
        "last_success_at": row.get("last_success_at"),
        "events": list(row.get("events") or [])[-8:],
        "remote": get_remote_quota(provider_id),
    }


def _load_env_token(name: str) -> str:
    load_dotenv(ROOT / ".env")
    return os.getenv(name, "").strip()


def fetch_huggingface_remote(token: str) -> dict[str, Any]:
    """Баланс Inference credits с Hugging Face (нужен scope read-billing)."""
    if not token:
        return {
            "source": "huggingface_api",
            "ok": False,
            "error": "HF_TOKEN не задан",
        }
    try:
        response = requests.get(
            "https://huggingface.co/api/settings/billing/credits",
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
        )
        if response.status_code == 403:
            whoami = requests.get(
                "https://huggingface.co/api/whoami-v2",
                headers={"Authorization": f"Bearer {token}"},
                timeout=20,
            )
            user = whoami.json().get("name", "?") if whoami.ok else "?"
            return {
                "source": "huggingface_api",
                "ok": False,
                "error": "Токен без read-billing (403). Создайте fine-grained token с Billing → Read.",
                "user": user,
                "billing_url": "https://huggingface.co/settings/billing",
            }
        response.raise_for_status()
        payload = response.json()
        balances = payload.get("balances") or []
        included_usd = sum(
            float(row.get("balanceUsd") or 0)
            for row in balances
            if str(row.get("type", "")).lower() == "included"
        )
        return {
            "source": "huggingface_api",
            "ok": True,
            "fetched_at": _utc_now().isoformat(timespec="seconds"),
            "balance_usd": float(payload.get("balanceUsd") or 0),
            "billing_mode": payload.get("billingMode"),
            "included_monthly_usd": included_usd,
            "balances": balances,
        }
    except requests.RequestException as exc:
        return {"source": "huggingface_api", "ok": False, "error": str(exc)[:300]}


def fetch_youtube_remote() -> dict[str, Any]:
    """Google не отдаёт остаток YouTube Data API quota по простому REST — только консоль."""
    key = _load_env_token("YOUTUBE_API_KEY")
    return {
        "source": "google_cloud_console",
        "ok": bool(key),
        "error": None if key else "YOUTUBE_API_KEY не задан",
        "message": (
            "Остаток суточной квоты (10 000 units) смотрите в Google Cloud Console. "
            "Ниже — только наш расход (scout_trends)."
        ),
        "console_url": "https://console.cloud.google.com/apis/api/youtube.googleapis.com/quotas",
        "fetched_at": _utc_now().isoformat(timespec="seconds"),
    }


def _local_cf_neurons_estimate() -> tuple[int, int]:
    snap = get_usage_snapshot(persist=False).get("cloudflare_workers_ai", {})
    images = int(snap.get("daily_count", snap.get("period_count", 0)))
    return images * CF_NEURONS_PER_IMAGE, images


def _parse_cf_neurons_graphql(body: dict[str, Any]) -> int | None:
    errors = body.get("errors")
    if errors:
        return None
    data = body.get("data")
    if not isinstance(data, dict):
        return None
    viewer = data.get("viewer") or {}
    accounts = viewer.get("accounts") or []
    if not accounts:
        return None
    groups = accounts[0].get("aiInferenceAdaptiveGroups") or []
    total = 0.0
    for group in groups:
        if not isinstance(group, dict):
            continue
        summary = group.get("sum") or {}
        value = summary.get("totalNeurons")
        if value is None:
            value = summary.get("neurons")
        if value is not None:
            total += float(value)
    return int(total)


def _cloudflare_graphql_neurons(
    account_id: str,
    api_token: str,
    utc_date: str,
    utc_day_start: str,
) -> tuple[int | None, str | None]:
    headers = {
        "Authorization": f"Bearer {api_token}",
        "Content-Type": "application/json",
    }
    queries: list[tuple[str, dict[str, Any]]] = [
        (
            """
            query CfNeuronsDay($accountTag: String!, $date: Date!, $datetime: DateTime!) {
              viewer {
                accounts(filter: { accountTag: $accountTag }) {
                  aiInferenceAdaptiveGroups(
                    limit: 10000
                    filter: { date_geq: $date, datetime_geq: $datetime }
                  ) {
                    sum { totalNeurons }
                  }
                }
              }
            }
            """,
            {"accountTag": account_id, "date": utc_date, "datetime": utc_day_start},
        ),
        (
            """
            query CfNeuronsDate($accountTag: String!, $date: Date!) {
              viewer {
                accounts(filter: { accountTag: $accountTag }) {
                  aiInferenceAdaptiveGroups(limit: 10000, filter: { date: $date }) {
                    sum { totalNeurons }
                  }
                }
              }
            }
            """,
            {"accountTag": account_id, "date": utc_date},
        ),
    ]
    last_error: str | None = None
    for query, variables in queries:
        try:
            response = requests.post(
                CF_GRAPHQL_URL,
                headers=headers,
                json={"query": query, "variables": variables},
                timeout=30,
            )
            body = response.json() if response.content else {}
            if not response.ok:
                last_error = f"HTTP {response.status_code}: {str(body)[:200]}"
                continue
            parsed = _parse_cf_neurons_graphql(body)
            if parsed is not None:
                return parsed, None
            graph_errors = body.get("errors") or []
            if graph_errors:
                last_error = "; ".join(
                    str(item.get("message", item)) for item in graph_errors[:3]
                )[:300]
        except requests.RequestException as exc:
            last_error = str(exc)[:300]
    return None, last_error or "GraphQL: пустой ответ или нет прав Account Analytics Read"


def fetch_cloudflare_neurons(
    account_id: str | None = None,
    api_token: str | None = None,
) -> dict[str, Any]:
    """Расход free neurons за текущие UTC-сутки (GraphQL Analytics)."""
    account_id = (account_id or _load_env_token("CF_ACCOUNT_ID")).strip()
    api_token = (api_token or _load_env_token("CF_API_TOKEN")).strip()
    fetched_at = _utc_now().isoformat(timespec="seconds")
    utc_date = _utc_now().strftime("%Y-%m-%d")
    utc_day_start = f"{utc_date}T00:00:00Z"
    local_neurons, local_images = _local_cf_neurons_estimate()

    if not account_id or not api_token:
        return {
            "ok": False,
            "source": "cloudflare_graphql",
            "error": "CF_ACCOUNT_ID или CF_API_TOKEN не заданы",
            "utc_date": utc_date,
            "fetched_at": fetched_at,
            "neurons_cap": CF_FREE_NEURONS_DAILY,
            "neurons_used": local_neurons,
            "neurons_meter_source": "local",
            "local_images_today": local_images,
        }

    neurons_used, error = _cloudflare_graphql_neurons(
        account_id, api_token, utc_date, utc_day_start
    )
    if neurons_used is None:
        return {
            "ok": False,
            "source": "cloudflare_graphql",
            "error": error,
            "neurons_graphql_error": error,
            "utc_date": utc_date,
            "fetched_at": fetched_at,
            "neurons_cap": CF_FREE_NEURONS_DAILY,
            "neurons_used": local_neurons,
            "neurons_remaining": max(0, CF_FREE_NEURONS_DAILY - local_neurons),
            "images_estimated_remaining": max(0, CF_FREE_NEURONS_DAILY // CF_NEURONS_PER_IMAGE - local_images),
            "neurons_meter_source": "local",
            "neurons_meter_label": "💻 Локальный счётчик",
            "local_images_today": local_images,
        }

    remaining = max(0, CF_FREE_NEURONS_DAILY - neurons_used)
    images_used_est = neurons_used // CF_NEURONS_PER_IMAGE
    images_remaining = max(0, remaining // CF_NEURONS_PER_IMAGE)
    return {
        "ok": True,
        "source": "cloudflare_graphql",
        "utc_date": utc_date,
        "fetched_at": fetched_at,
        "neurons_cap": CF_FREE_NEURONS_DAILY,
        "neurons_used": neurons_used,
        "neurons_remaining": remaining,
        "images_estimated_used": images_used_est,
        "images_estimated_remaining": images_remaining,
        "neurons_per_image_estimate": CF_NEURONS_PER_IMAGE,
        "neurons_meter_source": "graphql",
        "neurons_meter_label": "🌐 Cloudflare GraphQL API",
        "local_images_today": local_images,
    }


def fetch_cloudflare_remote() -> dict[str, Any]:
    account_id = _load_env_token("CF_ACCOUNT_ID")
    api_token = _load_env_token("CF_API_TOKEN")
    neurons = fetch_cloudflare_neurons(account_id, api_token)
    if not account_id or not api_token:
        return {
            "source": "cloudflare_api",
            "ok": False,
            "error": "CF_ACCOUNT_ID или CF_API_TOKEN не заданы",
            **neurons,
        }
    try:
        response = requests.get(
            "https://api.cloudflare.com/client/v4/user/tokens/verify",
            headers={"Authorization": f"Bearer {api_token}"},
            timeout=20,
        )
        payload = response.json() if response.content else {}
        token_ok = bool(payload.get("success")) and response.status_code == 200
        meter_label = neurons.get("neurons_meter_label", "💻 Локальный счётчик")
        if neurons.get("ok"):
            message = (
                f"Neurons сегодня (UTC): {neurons['neurons_used']:,} / {CF_FREE_NEURONS_DAILY:,} "
                f"· ≈{neurons.get('images_estimated_remaining', 0)} кадров осталось."
            )
        else:
            message = (
                f"GraphQL недоступен ({neurons.get('error', '—')[:120]}). "
                f"Локальная оценка: {neurons.get('neurons_used', 0)} neurons."
            )
        return {
            "source": "cloudflare_api",
            "ok": token_ok,
            "http_status": response.status_code,
            "fetched_at": _utc_now().isoformat(timespec="seconds"),
            "account_id_set": bool(account_id),
            "message": message,
            "error": None if token_ok else str(payload.get("errors") or response.text)[:200],
            "neurons_ok": bool(neurons.get("ok")),
            **{k: v for k, v in neurons.items() if k not in {"ok", "source"}},
        }
    except requests.RequestException as exc:
        return {
            "source": "cloudflare_api",
            "ok": False,
            "error": str(exc)[:200],
            **neurons,
            "neurons_ok": bool(neurons.get("ok")),
        }


def fetch_pollinations_remote() -> dict[str, Any]:
    """Публичного API остатка квоты у Pollinations нет — только проба доступности."""
    probe_url = "https://image.pollinations.ai/prompt/test?width=64&height=64&nologo=true"
    try:
        response = requests.get(probe_url, timeout=(5, 25))
        return {
            "source": "pollinations_probe",
            "ok": response.status_code == 200,
            "http_status": response.status_code,
            "fetched_at": _utc_now().isoformat(timespec="seconds"),
            "message": (
                "Официального счётчика квоты нет. Проба HTTP + локальный учёт запросов из наших скриптов."
            ),
        }
    except requests.RequestException as exc:
        return {
            "source": "pollinations_probe",
            "ok": False,
            "error": str(exc)[:200],
            "fetched_at": _utc_now().isoformat(timespec="seconds"),
            "message": "Сервис недоступен или rate-limit.",
        }


def refresh_cloudflare_remote() -> dict[str, Any]:
    """Обновить только Cloudflare (token verify + GraphQL neurons) в кэше remote."""
    cf_remote = fetch_cloudflare_remote()
    with _lock:
        data = _load_usage()
        remote = data.get("remote")
        if not isinstance(remote, dict):
            remote = {}
        remote["cloudflare_workers_ai"] = cf_remote
        remote["cloudflare_refreshed_at"] = _utc_now().isoformat(timespec="seconds")
        data["remote"] = remote
        _save_usage(data)
    return cf_remote


def refresh_remote_quotas() -> dict[str, Any]:
    """Опрос внешних API провайдеров, кэш в logs/quota_usage.json → remote."""
    remote = {
        "cloudflare_workers_ai": fetch_cloudflare_remote(),
        "huggingface_flux": fetch_huggingface_remote(_load_env_token("HF_TOKEN")),
        "youtube_data_api": fetch_youtube_remote(),
        "pollinations_fallback": fetch_pollinations_remote(),
        "refreshed_at": _utc_now().isoformat(timespec="seconds"),
    }
    with _lock:
        data = _load_usage()
        data["remote"] = remote
        _save_usage(data)
    return remote


def get_remote_quota(provider_id: str) -> dict[str, Any] | None:
    data = _load_usage()
    remote = data.get("remote")
    if not isinstance(remote, dict):
        return None
    value = remote.get(provider_id)
    return value if isinstance(value, dict) else None


def _source_badge(row: dict[str, Any]) -> str:
    remote = row.get("remote")
    if not isinstance(remote, dict):
        return "📟 только локальный учёт"
    source = remote.get("source", "local")
    if source == "huggingface_api" and remote.get("ok"):
        return "🌐 Hugging Face API"
    if source == "google_cloud_console":
        return "🌐 Google Console + локальный расход"
    if source == "cloudflare_api":
        if remote.get("neurons_ok"):
            return "🌐 Cloudflare GraphQL API"
        if remote.get("ok"):
            return "💻 Локальный счётчик"
    if source == "pollinations_probe":
        return "🌐 проба Pollinations + локальный расход"
    if source and source != "local":
        return f"🌐 {source}"
    return "📟 только локальный учёт"


def all_provider_statuses(*, refresh_remote: bool = False) -> list[dict[str, Any]]:
    if refresh_remote:
        refresh_remote_quotas()
    specs = load_specs().get("providers") or {}
    rows = [get_provider_status(pid) for pid in specs]
    for row in rows:
        remote = get_remote_quota(row["provider_id"])
        if remote:
            row["remote"] = remote
        row["source_badge"] = _source_badge(row)
    return rows


def estimate_youtube_scout_units(keyword_count: int) -> int:
    """Согласовано с scout_trends: search × duration × order."""
    search_calls = keyword_count * 2 * 2  # DURATIONS, ORDERS
    return search_calls * 100 + 50


def parse_http_error_code(exc: Exception) -> str | None:
    text = str(exc)
    match = re.search(r"'(\d{3})'", text)
    if match:
        return match.group(1)
    match = re.search(r"\b(402|429|403|401)\b", text)
    return match.group(1) if match else None


def register_http_failure(provider_id: str, exc: Exception) -> None:
    code = parse_http_error_code(exc)
    record_failed_attempt(provider_id, code, str(exc)[:300])
    if code in LIMIT_HIT_CODES:
        record_limit_hit(provider_id, code, str(exc)[:300])


def can_consume(provider_id: str, amount: int) -> tuple[bool, str]:
    status, label = status_for(provider_id)
    if status == "unlimited":
        return True, label
    if status == "inactive":
        return False, label
    if status == "red":
        return False, label
    cap = _cap_for(provider_id)
    if cap is None:
        return True, label
    snap = get_usage_snapshot()
    count = int(snap.get(provider_id, {}).get("count", 0))
    if count + amount > cap:
        return False, "🟡 Недостаточно остатка в текущем периоде"
    return True, label


def estimate_episode_workload(episode_dir: Path) -> dict[str, Any]:
    """Оценка задач для pre-flight (без запуска генерации)."""
    from generate_images import extract_prompts, image_exists

    episode_dir = episode_dir.resolve()
    prompts_path = episode_dir / "visual_prompts.md"
    image_dir = episode_dir / "images"
    prompt_count = 0
    missing_images = 0
    if prompts_path.is_file():
        prompts = extract_prompts(prompts_path.read_text(encoding="utf-8-sig"))
        prompt_count = len(prompts)
        for number in range(1, prompt_count + 1):
            if not image_exists(image_dir, number):
                missing_images += 1

    regen_count = 0
    flagged: set[str] = set()
    try:
        from episode_review import flagged_stems, load_review

        flagged = flagged_stems(load_review(episode_dir))
        regen_count = len(flagged)
    except Exception:
        flagged = set()

    voice_sec = 900.0
    voice_path = episode_dir / "voiceover.mp3"
    if voice_path.is_file():
        try:
            from moviepy.audio.io.AudioFileClip import AudioFileClip

            clip = AudioFileClip(str(voice_path))
            try:
                voice_sec = float(clip.duration or 900)
            finally:
                clip.close()
        except Exception:
            pass

    specs = load_specs().get("providers") or {}
    cf = specs.get("cloudflare_workers_ai", {})
    hf = specs.get("huggingface_flux", {})
    pol = specs.get("pollinations_fallback", {})
    tts = specs.get("edge_tts", {})
    render = specs.get("moviepy_render", {})

    frames: set[int] = set()
    if prompt_count:
        for number in range(1, prompt_count + 1):
            stem = f"{number:02d}"
            if not image_exists(image_dir, number) or stem in flagged:
                frames.add(number)
    images_needed = len(frames)
    from dotenv import load_dotenv
    import os

    load_dotenv(ROOT / ".env")
    has_cf = bool(os.getenv("CF_ACCOUNT_ID", "").strip() and os.getenv("CF_API_TOKEN", "").strip())
    has_hf = bool(os.getenv("HF_TOKEN", "").strip())
    cf_status = get_provider_status("cloudflare_workers_ai")
    hf_status = get_provider_status("huggingface_flux")
    if has_cf and cf_status.get("status") != "red":
        image_provider = "cloudflare_workers_ai"
        sec_per_image = float(cf.get("avg_seconds_per_item", 6))
        delay = float(cf.get("safe_delay_sec", 2.0))
    elif has_hf and hf_status.get("status") not in ("red", "inactive"):
        image_provider = "huggingface_flux"
        sec_per_image = float(hf.get("avg_seconds_per_item", 8))
        delay = float(hf.get("safe_delay_sec", 2.5))
    else:
        image_provider = "pollinations_fallback"
        sec_per_image = float(pol.get("avg_seconds_per_item", 12))
        delay = float(pol.get("safe_delay_sec", 4))

    images_time = images_needed * (sec_per_image + delay)
    voice_time = float(tts.get("avg_seconds_per_item", 15))
    render_time = (voice_sec / 60.0) * float(render.get("avg_seconds_per_item", 30))
    total_sec = images_time + voice_time + render_time

    img_ok, img_label = can_consume(image_provider, images_needed)
    return {
        "episode": str(episode_dir),
        "prompt_count": prompt_count,
        "missing_images": missing_images,
        "regen_flagged": regen_count,
        "images_to_generate": images_needed,
        "image_provider": image_provider,
        "voice_minutes": round(voice_sec / 60, 1),
        "eta_images_sec": round(images_time),
        "eta_voice_sec": round(voice_time),
        "eta_render_sec": round(render_time),
        "eta_total_sec": round(total_sec),
        "eta_total_min": round(total_sec / 60, 1),
        "quota_images_ok": img_ok,
        "quota_images_label": img_label,
    }


__all__ = [
    "all_provider_statuses",
    "can_consume",
    "estimate_episode_workload",
    "estimate_youtube_scout_units",
    "fetch_cloudflare_neurons",
    "fetch_cloudflare_remote",
    "get_provider_status",
    "get_remote_quota",
    "load_specs",
    "record_use",
    "refresh_cloudflare_remote",
    "refresh_remote_quotas",
    "register_http_failure",
    "save_specs",
    "status_for",
    "sync_usage_from_logs",
]