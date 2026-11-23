"""Ищет молодые faceless-каналы с аномально высокими просмотрами."""

from __future__ import annotations

import argparse
import csv
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
API = "https://www.googleapis.com/youtube/v3"
LOOKBACK_DAYS = 30
MIN_VIEWS = 50_000
MAX_SUBSCRIBERS = 15_000
MIN_OUTLIER = 5.0
MIN_DURATION_SEC = 8 * 60
MAX_RESULTS = 50
QUERIES = (
    "documentary",
    "explained",
    "iceberg",
    "sleep story",
    "untold story",
    "history of",
    "what happens if",
    "dark side of",
    "for sleep",
    "mystery documentary",
    "urban legend explained",
    "science documentary",
)
COLUMNS = (
    "Channel_Title",
    "Channel_Created_Date",
    "Subscribers",
    "Video_Title",
    "Views",
    "Outlier_Score",
    "Duration_Min",
    "URL",
)


class YouTubeError(RuntimeError):
    pass


def api_get(session: requests.Session, method: str, params: dict) -> dict:
    response = session.get(f"{API}/{method}", params=params, timeout=30)
    if response.status_code != 200:
        message = response.reason
        try:
            message = response.json()["error"]["message"]
        except (ValueError, KeyError, TypeError):
            pass
        raise YouTubeError(f"YouTube API {response.status_code}: {message}")
    return response.json()


def parse_duration_seconds(raw: str) -> int:
    if not raw:
        return 0
    hours = int(re.search(r"(\d+)H", raw).group(1)) if re.search(r"(\d+)H", raw) else 0
    minutes = int(re.search(r"(\d+)M", raw).group(1)) if re.search(r"(\d+)M", raw) else 0
    seconds = int(re.search(r"(\d+)S", raw).group(1)) if re.search(r"(\d+)S", raw) else 0
    return hours * 3600 + minutes * 60 + seconds


def search_video_ids(session: requests.Session, key: str, keyword: str, published_after: str) -> set[str]:
    found: set[str] = set()
    for duration in ("medium", "long"):
        for order in ("viewCount", "date"):
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
                    "regionCode": "US",
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
            {"part": "snippet,statistics,contentDetails", "id": ",".join(batch), "key": key},
        )
        for item in payload.get("items", []):
            stats = item.get("statistics") or {}
            snippet = item.get("snippet") or {}
            content = item.get("contentDetails") or {}
            if "viewCount" not in stats or not snippet.get("channelId"):
                continue
            duration_sec = parse_duration_seconds(content.get("duration", ""))
            videos[item["id"]] = {
                "title": snippet.get("title", ""),
                "channel_title": snippet.get("channelTitle", ""),
                "channel_id": snippet["channelId"],
                "views": int(stats["viewCount"]),
                "duration_sec": duration_sec,
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
            {"part": "snippet,statistics", "id": ",".join(batch), "key": key},
        )
        for item in payload.get("items", []):
            stats = item.get("statistics") or {}
            snippet = item.get("snippet") or {}
            channels[item["id"]] = {
                "title": snippet.get("title", ""),
                "created": (snippet.get("publishedAt") or "")[:10],
                "hidden": bool(stats.get("hiddenSubscriberCount")),
                "subscribers": int(stats.get("subscriberCount") or 0),
            }
    return channels


def build_rows(videos: dict[str, dict], channels: dict[str, dict]) -> list[dict]:
    rows: list[dict] = []
    for video_id, video in videos.items():
        if video["duration_sec"] < MIN_DURATION_SEC:
            continue
        channel = channels.get(video["channel_id"])
        if channel is None or channel["hidden"]:
            continue
        subscribers = channel["subscribers"]
        views = video["views"]
        denominator = subscribers if subscribers > 0 else 1
        outlier = views / denominator
        if views < MIN_VIEWS or subscribers >= MAX_SUBSCRIBERS or outlier < MIN_OUTLIER:
            continue
        rows.append(
            {
                "Channel_Title": channel["title"] or video["channel_title"],
                "Channel_Created_Date": channel["created"],
                "Subscribers": subscribers,
                "Video_Title": video["title"],
                "Views": views,
                "Outlier_Score": round(outlier, 2),
                "Duration_Min": round(video["duration_sec"] / 60, 1),
                "URL": f"https://www.youtube.com/watch?v={video_id}",
            }
        )
    rows.sort(key=lambda row: row["Outlier_Score"], reverse=True)
    deduped: list[dict] = []
    seen: set[str] = set()
    for row in rows:
        if row["URL"] in seen:
            continue
        seen.add(row["URL"])
        deduped.append(row)
    return deduped


def discover(key: str) -> Path:
    published_after = (
        datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    video_ids: set[str] = set()
    with requests.Session() as session:
        for keyword in QUERIES:
            video_ids.update(search_video_ids(session, key, keyword, published_after))
        videos = fetch_videos(session, key, sorted(video_ids))
        channel_ids = sorted({video["channel_id"] for video in videos.values()})
        channels = fetch_channels(session, key, channel_ids)
    rows = build_rows(videos, channels)
    output_dir = ROOT / "market_radar"
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / f"new_niches_{datetime.now(timezone.utc).date().isoformat()}.csv"
    with output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    return output


def main(argv: list[str] | None = None) -> int:
    load_dotenv(ROOT / ".env")
    key = os.environ.get("YOUTUBE_API_KEY", "").strip()
    if not key or key == "твой_ключ":
        print("Впиши YOUTUBE_API_KEY в .env (образец — .env.example).", file=sys.stderr)
        return 1
    print(f"Запросов: {len(QUERIES)}. Окно: {LOOKBACK_DAYS} дней.")
    try:
        output = discover(key)
    except YouTubeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except requests.RequestException as exc:
        print(f"Сеть: {exc}", file=sys.stderr)
        return 1
    count = max(sum(1 for _ in output.open(encoding="utf-8")) - 1, 0)
    print(f"Найдено строк: {count}. Отчёт: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())