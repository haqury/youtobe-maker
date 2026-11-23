"""Подбор и оценка YouTube-ниш: пресеты, market_radar, LLM Cloudflare, проба YouTube API."""

from __future__ import annotations

import json
import os
import re
import statistics
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in __import__("sys").path:
    __import__("sys").path.insert(0, str(_SCRIPTS))
from ai_router import generate_text

PRESETS_PATH = ROOT / "config" / "niche_presets.json"
MARKET_RADAR_DIR = ROOT / "market_radar"
CHANNELS_DIR = ROOT / "channels"
YOUTUBE_API = "https://www.googleapis.com/youtube/v3"
YOUNG_SUBS_MAX = 15_000
BREAKOUT_VIEWS_MIN = 50_000
LLM_NICHE_COUNT = 6


def _as_str(value: Any, default: str = "") -> str:
    if value is None:
        return default
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, list):
        parts = [_as_str(item) for item in value if item is not None]
        return "\n".join(part for part in parts if part) or default
    if isinstance(value, dict):
        for key in ("response", "text", "content", "message"):
            if key in value:
                return _as_str(value[key], default)
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _as_int(value: Any, default: int = 5) -> int:
    if value is None:
        return default
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        match = re.search(r"-?\d+", value)
        return int(match.group(0)) if match else default
    if isinstance(value, list) and value:
        return _as_int(value[0], default)
    return default


def _as_keyword_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [line.strip() for line in value.replace(",", "\n").splitlines() if line.strip()]
    if isinstance(value, list):
        out: list[str] = []
        for item in value:
            out.extend(_as_keyword_list(item))
        return out[:12]
    return [_as_str(value)]


def _rpm_midpoint(rpm: str) -> float:
    text = str(rpm).strip()
    nums = [float(x) for x in re.findall(r"\d+\.?\d*", text)]
    if not nums:
        return 4.0
    if len(nums) == 1:
        return nums[0]
    return (nums[0] + nums[-1]) / 2.0


def profitability_index(demand: int | float, rpm: str, difficulty: int | float) -> float:
    difficulty = max(1.0, float(difficulty or 5))
    return round(float(demand) * _rpm_midpoint(rpm) / difficulty, 2)


def income_100k_views(rpm: str) -> str:
    low, high = re.findall(r"\d+\.?\d*", str(rpm))[:2] or ["3", "6"]
    lo, hi = float(low), float(high or low)
    return f"${lo * 100:.0f} – ${hi * 100:.0f}"


def load_presets() -> list[dict[str, Any]]:
    if not PRESETS_PATH.is_file():
        return []
    data = json.loads(PRESETS_PATH.read_text(encoding="utf-8-sig"))
    items = data.get("presets") if isinstance(data, dict) else data
    if not isinstance(items, list):
        return []
    out: list[dict[str, Any]] = []
    for row in items:
        if not isinstance(row, dict):
            continue
        item = dict(row)
        item.setdefault("source", "preset")
        item["profitability_index"] = profitability_index(
            item.get("demand_score", 5),
            item.get("estimated_rpm_usd", "3 - 6"),
            item.get("production_difficulty", 5),
        )
        note = item.get("production_difficulty_note") or ""
        if not note and item.get("production_difficulty") is not None:
            note = f"{item['production_difficulty']}/10"
        item["production_difficulty_label"] = note
        out.append(item)
    return out


def list_existing_channel_slugs() -> set[str]:
    if not CHANNELS_DIR.is_dir():
        return set()
    return {
        p.name.lower()
        for p in CHANNELS_DIR.iterdir()
        if p.is_dir() and not p.name.startswith(".")
    }


def existing_channels_prompt_block() -> str:
    lines: list[str] = []
    for slug in sorted(list_existing_channel_slugs()):
        cfg = CHANNELS_DIR / slug / "channel_config.md"
        excerpt = ""
        if cfg.is_file():
            text = cfg.read_text(encoding="utf-8-sig", errors="replace")
            for raw in text.splitlines():
                if raw.strip().startswith("- Тема:"):
                    excerpt = raw.strip()
                    break
            if not excerpt:
                excerpt = " ".join(text.split())[:120]
        lines.append(f"- `{slug}` {excerpt}".strip())
    if not lines:
        return "No existing channels yet."
    return "\n".join(lines)


def _tokenize(text: str) -> set[str]:
    return {w.lower() for w in re.findall(r"[a-zа-яё0-9]{3,}", text.lower())}


def channel_conflict(slug_suggestion: Any, title: Any) -> str | None:
    slugs = list_existing_channel_slugs()
    slug_raw = _as_str(slug_suggestion)
    slug = slug_raw.strip().lower()
    if slug and slug in slugs:
        return f"Папка `{slug_raw}` уже есть в channels/"
    title_l = _as_str(title).lower()
    for existing in slugs:
        if existing.replace("channel_", "").replace("_", " ") in title_l:
            return f"Похоже на существующий канал: `{existing}`"
    return None


def load_market_radar_niches(limit: int = 12) -> list[dict[str, Any]]:
    if not MARKET_RADAR_DIR.is_dir():
        return []
    files = sorted(MARKET_RADAR_DIR.glob("new_niches_*.csv"), reverse=True)
    if not files:
        files = sorted(MARKET_RADAR_DIR.glob("*.csv"), reverse=True)
    if not files:
        return []
    path = files[0]
    import csv

    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for i, row in enumerate(reader):
            if i >= limit:
                break
            title = (row.get("Video_Title") or row.get("Channel_Title") or "").strip()
            if not title:
                continue
            views = row.get("Views") or row.get("views") or "0"
            try:
                views_n = int(str(views).replace(",", "").replace(" ", ""))
            except ValueError:
                views_n = 0
            demand = min(10, max(4, 5 + (views_n // 100_000)))
            slug = "channel_radar_" + re.sub(r"[^a-z0-9]+", "_", title.lower())[:28].strip("_")
            item = {
                "id": f"radar_{i}",
                "title": title[:80],
                "slug_suggestion": slug,
                "category": "Market radar",
                "demand_score": demand,
                "estimated_rpm_usd": "3.0 - 6.0",
                "production_difficulty": 5,
                "production_difficulty_note": "5/10 — оценка по radar, уточните формат",
                "recommended_duration": "10-18 минут",
                "description": f"Находка из {path.name}: Outlier/просмотры из CSV.",
                "keywords": _guess_keywords(title),
                "source": "market_radar",
                "radar_file": path.name,
            }
            item["profitability_index"] = profitability_index(
                demand, item["estimated_rpm_usd"], item["production_difficulty"]
            )
            item["production_difficulty_label"] = item["production_difficulty_note"]
            rows.append(item)
    return rows


def _guess_keywords(title: str) -> list[str]:
    base = re.sub(r"[^a-zA-Z0-9 ]", " ", title).lower()
    words = [w for w in base.split() if len(w) > 3][:4]
    if not words:
        return ["documentary explained", "faceless youtube niche"]
    primary = " ".join(words)
    return [
        primary,
        f"{primary} explained",
        f"{primary} documentary",
        f"what is {primary}",
        f"{words[0]} story",
        f"{words[0]} for sleep" if "sleep" in base else f"{words[0]} mystery",
    ]


def filter_presets(query: str, presets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    q = query.strip().lower()
    if not q:
        return presets
    tokens = _tokenize(q)
    scored: list[tuple[float, dict[str, Any]]] = []
    for item in presets:
        hay = " ".join(
            [
                str(item.get("title", "")),
                str(item.get("description", "")),
                str(item.get("category", "")),
                " ".join(item.get("keywords") or []),
            ]
        ).lower()
        hay_tokens = _tokenize(hay)
        overlap = len(tokens & hay_tokens)
        if q in hay:
            overlap += 3
        if overlap > 0:
            scored.append((overlap + item.get("profitability_index", 0) * 0.1, item))
    if not scored:
        return presets[:6]
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [pair[1] for pair in scored]


def _cf_credentials() -> tuple[str, str] | None:
    load_dotenv(ROOT / ".env")
    account = os.getenv("CF_ACCOUNT_ID", "").strip()
    token = os.getenv("CF_API_TOKEN", "").strip()
    if account and token:
        return account, token
    return None


def _is_niche_row(obj: dict[str, Any]) -> bool:
    return bool(obj.get("title") or obj.get("slug_suggestion"))


def _coerce_niche_rows(parsed: Any) -> list[dict[str, Any]]:
    if isinstance(parsed, dict) and "niches" in parsed:
        parsed = parsed["niches"]
    if isinstance(parsed, dict) and "items" in parsed:
        parsed = parsed["items"]
    if isinstance(parsed, dict) and _is_niche_row(parsed):
        return [parsed]
    if not isinstance(parsed, list):
        return []
    return [x for x in parsed if isinstance(x, dict) and _is_niche_row(x)]


def _extract_json_objects(text: str) -> list[dict[str, Any]]:
    """Llama часто шлёт `{...}{...}` или обрезанный поток — вытаскиваем все целые объекты."""
    decoder = json.JSONDecoder()
    idx = 0
    length = len(text)
    out: list[dict[str, Any]] = []
    while idx < length:
        while idx < length and text[idx] in " \n\r\t,":
            idx += 1
        if idx >= length:
            break
        if text[idx] not in "{[":
            next_brace = text.find("{", idx)
            if next_brace < 0:
                break
            idx = next_brace
        try:
            parsed, end = decoder.raw_decode(text, idx)
            out.extend(_coerce_niche_rows(parsed))
            idx = end
        except json.JSONDecodeError:
            next_brace = text.find("{", idx + 1)
            if next_brace < 0:
                break
            idx = next_brace
    return out


def _parse_llm_json(text: Any) -> list[dict[str, Any]]:
    text = _as_str(text).strip()
    if not text:
        return []
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if fence:
        text = fence.group(1).strip()

    try:
        rows = _coerce_niche_rows(json.loads(text))
        if rows:
            return rows
    except json.JSONDecodeError:
        pass

    start = text.find("[")
    end = text.rfind("]")
    if start >= 0 and end > start:
        try:
            rows = _coerce_niche_rows(json.loads(text[start : end + 1]))
            if rows:
                return rows
        except json.JSONDecodeError:
            pass

    objects = _extract_json_objects(text)
    if objects:
        return objects

    if text.startswith("{"):
        try:
            rows = _coerce_niche_rows(json.loads(text))
            if rows:
                return rows
        except json.JSONDecodeError:
            pass

    return []


def llm_suggest_niches(
    query: str,
    count: int = LLM_NICHE_COUNT,
) -> tuple[list[dict[str, Any]], str | None]:
    excluded = existing_channels_prompt_block()
    system = (
        "You are a YouTube faceless-channel strategist for the US English market. "
        "Output MUST be one JSON array starting with [ and ending with ]. "
        "No markdown, no prose, no single standalone objects. "
        "Exactly 5-6 objects, compact JSON (minified). "
        "Each object distinct angle. Keys per object: "
        "title, slug_suggestion (channel_XX_topic), category, demand_score (int 1-10), "
        "estimated_rpm_usd (e.g. \"4.0 - 7.0\"), production_difficulty (int), "
        "production_difficulty_note (max 40 chars), recommended_duration (short), "
        "description (max 100 chars EN), keywords (4-6 English search strings)."
    )
    user = (
        f"User theme / brief: {query}\n\n"
        f"Generate {count} concrete, DIFFERENT sub-niches (unique angles). "
        f"Production: Python faceless stack — static images + TTS + MoviePy OR gameplay clips.\n\n"
        f"FORBIDDEN — do NOT propose duplicates or near-duplicates of these existing channels:\n"
        f"{excluded}\n\n"
        f"Each slug_suggestion must be new and not match excluded slugs."
    )
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    raw_text, err = generate_text(messages, max_tokens=8192, temperature=0.45)
    if err:
        return [], err[:500]
    raw_items = _parse_llm_json(raw_text)
    if not raw_items:
        snippet = raw_text[:280]
        return [], (
            "LLM: не удалось разобрать JSON (нужен массив из 5–6 объектов). "
            f"Начало ответа: {snippet!r}"
        )[:500]

    out: list[dict[str, Any]] = []
    for i, row in enumerate(raw_items[:count]):
        difficulty = _as_int(row.get("production_difficulty"), 5)
        item = {
            "id": f"llm_{i}",
            "title": _as_str(row.get("title"), f"Niche {i+1}")[:120],
            "slug_suggestion": _as_str(row.get("slug_suggestion"), f"channel_llm_{i:02d}")[:48],
            "category": _as_str(row.get("category"), "LLM suggestion"),
            "demand_score": _as_int(row.get("demand_score"), 6),
            "estimated_rpm_usd": _as_str(row.get("estimated_rpm_usd"), "3.0 - 6.0"),
            "production_difficulty": difficulty,
            "production_difficulty_note": _as_str(
                row.get("production_difficulty_note"),
                f"{difficulty}/10",
            ),
            "recommended_duration": _as_str(row.get("recommended_duration"), "10-15 minutes"),
            "description": _as_str(row.get("description")),
            "keywords": _as_keyword_list(row.get("keywords"))[:8],
            "source": "ai_router",
        }
        item["profitability_index"] = profitability_index(
            item["demand_score"],
            item["estimated_rpm_usd"],
            item["production_difficulty"],
        )
        item["production_difficulty_label"] = item["production_difficulty_note"]
        out.append(item)
    if len(out) < 3:
        return out, f"Модель вернула только {len(out)} идей (ожидалось 5–6). Можно повторить запрос."
    return out, None


def _record_youtube_units(units: int) -> None:
    try:
        from quota_tracker import record_use

        record_use("youtube_data_api", units)
    except Exception:
        pass


def youtube_market_stats(keyword: str, api_key: str) -> dict[str, Any]:
    """Проба ниши: топ видео за 30д, средние просмотры, молодые каналы-«выстрелы»."""
    published_after = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    session = requests.Session()
    search_params = {
        "part": "snippet",
        "q": keyword,
        "type": "video",
        "order": "viewCount",
        "publishedAfter": published_after,
        "maxResults": 10,
        "key": api_key,
    }
    try:
        search = session.get(f"{YOUTUBE_API}/search", params=search_params, timeout=25)
        _record_youtube_units(100)
        if search.status_code != 200:
            return {"ok": False, "error": search.text[:220], "keyword": keyword}
        search_items = search.json().get("items") or []
        video_ids: list[str] = []
        video_channel: dict[str, str] = {}
        for row in search_items:
            vid = row.get("id", {}).get("videoId")
            ch = row.get("snippet", {}).get("channelId")
            if vid and ch:
                video_ids.append(vid)
                video_channel[vid] = ch
        if not video_ids:
            return {
                "ok": True,
                "keyword": keyword,
                "videos_sampled": 0,
                "avg_views_top": 0,
                "young_breakouts": 0,
                "note": "Нет видео за последние 30 дней по этому ключу.",
            }
        stats = session.get(
            f"{YOUTUBE_API}/videos",
            params={"part": "statistics", "id": ",".join(video_ids), "key": api_key},
            timeout=25,
        )
        _record_youtube_units(1)
        views_by_video: dict[str, int] = {}
        if stats.ok:
            for row in stats.json().get("items") or []:
                views_by_video[row["id"]] = int(row.get("statistics", {}).get("viewCount") or 0)
        channel_ids = list({video_channel[vid] for vid in video_ids if vid in video_channel})
        subs_by_channel: dict[str, int] = {}
        if channel_ids:
            ch_resp = session.get(
                f"{YOUTUBE_API}/channels",
                params={
                    "part": "statistics",
                    "id": ",".join(channel_ids[:50]),
                    "key": api_key,
                },
                timeout=25,
            )
            _record_youtube_units(1)
            if ch_resp.ok:
                for row in ch_resp.json().get("items") or []:
                    subs_by_channel[row["id"]] = int(row.get("statistics", {}).get("subscriberCount") or 0)
        views_list = [views_by_video.get(vid, 0) for vid in video_ids if views_by_video.get(vid, 0)]
        young_breakouts = 0
        for vid in video_ids:
            views = views_by_video.get(vid, 0)
            ch = video_channel.get(vid)
            subs = subs_by_channel.get(ch or "", 0)
            if subs <= YOUNG_SUBS_MAX and views >= BREAKOUT_VIEWS_MIN:
                young_breakouts += 1
        avg_views = int(statistics.mean(views_list)) if views_list else 0
        return {
            "ok": True,
            "keyword": keyword,
            "videos_sampled": len(views_list),
            "avg_views_top": avg_views,
            "total_views_top": sum(views_list),
            "young_breakouts": young_breakouts,
            "young_subs_threshold": YOUNG_SUBS_MAX,
            "breakout_views_min": BREAKOUT_VIEWS_MIN,
        }
    except requests.RequestException as exc:
        return {"ok": False, "error": str(exc)[:220], "keyword": keyword}


def youtube_probe_keyword(keyword: str, api_key: str) -> dict[str, Any]:
    published_after = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    session = requests.Session()
    params = {
        "part": "snippet",
        "q": keyword,
        "type": "video",
        "order": "viewCount",
        "publishedAfter": published_after,
        "maxResults": 8,
        "key": api_key,
    }
    try:
        search = session.get(f"{YOUTUBE_API}/search", params=params, timeout=25)
        if search.status_code != 200:
            return {"ok": False, "error": search.text[:200]}
        items = search.json().get("items") or []
        video_ids = [
            item["id"]["videoId"]
            for item in items
            if item.get("id", {}).get("videoId")
        ]
        if not video_ids:
            return {"ok": True, "keyword": keyword, "videos": 0, "total_views_30d": 0}
        stats_params = {
            "part": "statistics",
            "id": ",".join(video_ids),
            "key": api_key,
        }
        stats = session.get(f"{YOUTUBE_API}/videos", params=stats_params, timeout=25)
        total = 0
        if stats.ok:
            for row in stats.json().get("items") or []:
                total += int(row.get("statistics", {}).get("viewCount") or 0)
        try:
            from quota_tracker import record_use

            record_use("youtube_data_api", 102)
        except Exception:
            pass
        return {
            "ok": True,
            "keyword": keyword,
            "videos": len(video_ids),
            "total_views_30d": total,
        }
    except requests.RequestException as exc:
        return {"ok": False, "error": str(exc)[:200]}


def enrich_item_youtube_market(item: dict[str, Any], api_key: str | None = None) -> None:
    load_dotenv(ROOT / ".env")
    api_key = (api_key or os.getenv("YOUTUBE_API_KEY", "")).strip()
    kws = _as_keyword_list(item.get("keywords"))
    if not api_key:
        item["youtube_market"] = {"ok": False, "error": "YOUTUBE_API_KEY не задан"}
        return
    if not kws:
        item["youtube_market"] = {"ok": False, "error": "Нет keywords для пробы"}
        return
    market = youtube_market_stats(kws[0], api_key)
    item["youtube_market"] = market
    if market.get("ok") and market.get("avg_views_top"):
        avg = int(market["avg_views_top"])
        boost = min(3, avg // 200_000)
        item["demand_score"] = min(10, _as_int(item.get("demand_score"), 6) + boost)
        item["profitability_index"] = profitability_index(
            item["demand_score"],
            item.get("estimated_rpm_usd", "3 - 6"),
            item.get("production_difficulty", 5),
        )


def _normalize_item(item: dict[str, Any]) -> dict[str, Any]:
    item = dict(item)
    item["income_100k_views"] = income_100k_views(item.get("estimated_rpm_usd", "3 - 6"))
    conflict = channel_conflict(
        str(item.get("slug_suggestion", "")),
        str(item.get("title", "")),
    )
    item["channel_conflict"] = conflict
    return item


def suggest_niches_catalog(query_text: str = "") -> list[dict[str, Any]]:
    """Только готовые пресеты (вкладка «Быстрый выбор»)."""
    query = _as_str(query_text).strip()
    items = [_normalize_item(dict(row)) for row in filter_presets(query, load_presets())]
    items.sort(key=lambda x: x.get("profitability_index", 0), reverse=True)
    return items


def suggest_niches_ai(query_text: str) -> dict[str, Any]:
    """
    Живой ИИ-подбор: всегда Cloudflare LLM (5–6 свежих подниш) + YouTube по каждой.
    """
    query = _as_str(query_text).strip()
    if not query:
        return {
            "ok": False,
            "error": "Введите тему для анализа (например: ai it sleep history).",
            "items": [],
            "warning": None,
        }
    items, llm_warn = llm_suggest_niches(query, count=LLM_NICHE_COUNT)
    if not items:
        return {"ok": False, "error": llm_warn or "Cloudflare LLM не вернул идеи.", "items": [], "warning": None}
    load_dotenv(ROOT / ".env")
    api_key = os.getenv("YOUTUBE_API_KEY", "").strip()
    for index, item in enumerate(items):
        enrich_item_youtube_market(item, api_key or None)
        items[index] = _normalize_item(item)
    items.sort(key=lambda x: x.get("profitability_index", 0), reverse=True)
    return {
        "ok": True,
        "error": None,
        "warning": llm_warn,
        "items": items,
    }


def suggest_niches(query_text: str = "") -> list[dict[str, Any]]:
    """Совместимость: только ИИ-подбор; при ошибке — исключение с текстом."""
    result = suggest_niches_ai(query_text)
    if not result.get("ok"):
        raise RuntimeError(result.get("error") or "Ошибка подбора ниш")
    return list(result.get("items") or [])


def niche_to_form_fields(item: dict[str, Any]) -> dict[str, str]:
    keywords = _as_keyword_list(item.get("keywords"))
    title = _as_str(item.get("title"))
    desc = _as_str(item.get("description"))
    niche = f"{title} — {desc}".strip(" —") if desc else title
    return {
        "slug": _as_str(item.get("slug_suggestion")).strip(),
        "niche": niche,
        "duration": _as_str(item.get("recommended_duration"), "10-15 минут"),
        "keywords": "\n".join(keywords),
    }