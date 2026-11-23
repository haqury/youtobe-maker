"""Ищет недавние видео небольших каналов и считает Outlier_Score = Views / Subscribers."""

from __future__ import annotations

import argparse
import csv
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
from quota_tracker import estimate_youtube_scout_units, record_limit_hit, record_use
API = "https://www.googleapis.com/youtube/v3"
LOOKBACK_DAYS = 60
MAX_SUBSCRIBERS = 25_000
MIN_VIEWS = 15_000
MAX_RESULTS = 50
DURATIONS = ("medium", "long")
ORDERS = ("viewCount", "date")
COLUMNS = (
    "Video_Title",
    "Channel_Title",
    "Subscribers",
    "Views",
    "Outlier_Score",
    "Publish_Date",
    "URL",
)


class YouTubeError(RuntimeError):
    pass


def load_keywords(config_path: Path) -> list[str]:
    """Берёт маркированный список из секции ## Search Keywords до следующего заголовка."""
    lines = config_path.read_text(encoding="utf-8-sig").splitlines()
    keywords: list[str] = []
    in_section = False
    for line in lines:
        if line.startswith("## Search Keywords"):
            in_section = True
            continue
        if in_section and line.startswith("## "):
            break
        if not in_section:
            continue
        stripped = line.strip()
        if stripped.startswith("- "):
            keyword = stripped[2:].strip()
            if keyword:
                keywords.append(keyword)
    return keywords


def resolve_channel(raw: Path) -> Path:
    if raw.is_absolute():
        return raw
    from_cwd = (Path.cwd() / raw).resolve()
    from_root = (ROOT / raw).resolve()
    if (from_cwd / "channel_config.md").is_file():
        return from_cwd
    if (from_root / "channel_config.md").is_file():
        return from_root
    return from_cwd


def api_get(session: requests.Session, method: str, params: dict) -> dict:
    response = session.get(f"{API}/{method}", params=params, timeout=30)
    if response.status_code != 200:
        message = response.reason
        try:
            message = response.json()["error"]["message"]
        except (ValueError, KeyError, TypeError):
            pass
        if response.status_code in (403, 429):
            record_limit_hit("youtube_data_api", response.status_code, message)
        raise YouTubeError(f"YouTube API {response.status_code}: {message}")
    return response.json()


def search_video_ids(session: requests.Session, key: str, keyword: str, published_after: str) -> set[str]:
    found: set[str] = set()
    for duration in DURATIONS:
        for order in ORDERS:
            payload = api_get(
                session,
                "search",
                {
                    "part": "snippet",
                    "q": keyword,
                    "type": "video",
                    "publishedAfter": published_after,
                    "videoDuration": duration,
                    "order": order,
                    "maxResults": MAX_RESULTS,
                    "relevanceLanguage": "en",
                    "key": key,
                },
            )
            for item in payload.get("items", []):
                video_id = (item.get("id") or {}).get("videoId")
                if video_id:
                    found.add(video_id)
    return found


def fetch_videos(session: requests.Session, key: str, video_ids: list[str]) -> dict[str, dict]:
    videos: dict[str, dict] = {}
    if not video_ids:
        return videos
    for offset in range(0, len(video_ids), MAX_RESULTS):
        batch = video_ids[offset : offset + MAX_RESULTS]
        payload = api_get(
            session,
            "videos",
            {"part": "snippet,statistics", "id": ",".join(batch), "key": key},
        )
        for item in payload.get("items", []):
            stats = item.get("statistics") or {}
            snippet = item.get("snippet") or {}
            if "viewCount" not in stats or not snippet.get("channelId"):
                continue
            videos[item["id"]] = {
                "title": snippet.get("title", ""),
                "channel_title": snippet.get("channelTitle", ""),
                "channel_id": snippet["channelId"],
                "published_at": snippet.get("publishedAt", ""),
                "views": int(stats["viewCount"]),
            }
    return videos


def fetch_channels(session: requests.Session, key: str, channel_ids: list[str]) -> dict[str, dict]:
    channels: dict[str, dict] = {}
    if not channel_ids:
        return channels
    for offset in range(0, len(channel_ids), MAX_RESULTS):
        batch = channel_ids[offset : offset + MAX_RESULTS]
        payload = api_get(
            session,
            "channels",
            {"part": "statistics", "id": ",".join(batch), "key": key},
        )
        for item in payload.get("items", []):
            stats = item.get("statistics") or {}
            hidden = bool(stats.get("hiddenSubscriberCount"))
            subscribers = int(stats.get("subscriberCount") or 0)
            channels[item["id"]] = {"hidden": hidden, "subscribers": subscribers}
    return channels


def build_rows(videos: dict[str, dict], channels: dict[str, dict]) -> list[dict]:
    rows: list[dict] = []
    for video_id, video in videos.items():
        channel = channels.get(video["channel_id"])
        if channel is None or channel["hidden"]:
            continue
        subscribers = channel["subscribers"]
        views = video["views"]
        if subscribers > MAX_SUBSCRIBERS or views < MIN_VIEWS:
            continue
        denominator = subscribers if subscribers > 0 else 1
        published = video["published_at"][:10]
        rows.append(
            {
                "Video_Title": video["title"],
                "Channel_Title": video["channel_title"],
                "Subscribers": subscribers,
                "Views": views,
                "Outlier_Score": round(views / denominator, 2),
                "Publish_Date": published,
                "URL": f"https://www.youtube.com/watch?v={video_id}",
            }
        )
    rows.sort(key=lambda row: row["Outlier_Score"], reverse=True)
    return rows


def write_report(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def estimate_scout_units(channel_dir: Path) -> int:
    keywords = load_keywords(channel_dir / "channel_config.md")
    return estimate_youtube_scout_units(len(keywords))


def scout(channel_dir: Path, key: str) -> tuple[Path, int, int]:
    published_after = (
        datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    keywords = load_keywords(channel_dir / "channel_config.md")
    video_ids: set[str] = set()
    with requests.Session() as session:
        for keyword in keywords:
            video_ids.update(search_video_ids(session, key, keyword, published_after))
        videos = fetch_videos(session, key, sorted(video_ids))
        channel_ids = sorted({video["channel_id"] for video in videos.values()})
        channels = fetch_channels(session, key, channel_ids)
    rows = build_rows(videos, channels)
    report = channel_dir / "analytics_dump" / "latest_scout.csv"
    write_report(report, rows)
    search_units = len(keywords) * len(DURATIONS) * len(ORDERS) * 100
    video_batches = (len(video_ids) + MAX_RESULTS - 1) // MAX_RESULTS if video_ids else 0
    channel_batches = (len(channel_ids) + MAX_RESULTS - 1) // MAX_RESULTS if channel_ids else 0
    units_used = search_units + video_batches + channel_batches
    return report, len(video_ids), len(rows), units_used


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Найти выбросы YouTube за 60 дней и сохранить latest_scout.csv."
    )
    parser.add_argument(
        "channel",
        type=Path,
        help="Папка канала, например channels/channel_01_gaming_mysteries",
    )
    args = parser.parse_args(argv)

    channel_dir = resolve_channel(args.channel)
    config_path = channel_dir / "channel_config.md"
    if not config_path.is_file():
        print(f"Нет channel_config.md: {channel_dir}", file=sys.stderr)
        return 1

    keywords = load_keywords(config_path)
    if not keywords:
        print("В channel_config.md нет секции ## Search Keywords.", file=sys.stderr)
        return 1

    load_dotenv(ROOT / ".env")
    key = os.environ.get("YOUTUBE_API_KEY", "").strip()
    if not key or key == "твой_ключ":
        print("Впиши YOUTUBE_API_KEY в .env (образец — .env.example).", file=sys.stderr)
        return 1

    calls = len(keywords) * len(DURATIONS) * len(ORDERS)
    print(f"Канал: {channel_dir.name}")
    print(f"Запросы: {len(keywords)}. Вызовов search.list: {calls}.")
    try:
        report, scanned, kept, units_used = scout(channel_dir, key)
        record_use("youtube_data_api", units_used)
        print(f"YouTube API quota: +{units_used} units (оценка фактического scout).")
    except YouTubeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except requests.RequestException as exc:
        print(f"Сеть: {exc}", file=sys.stderr)
        return 1

    print(f"Просмотрено видео: {scanned}. Прошли фильтр: {kept}.")
    print(f"Отчёт: {report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
