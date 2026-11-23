"""Daily editorial observations. All dates and ISO timestamps are normalized to GMT+8.

Read the video_snapshots view for the latest measured play count per account,
video and calendar date. tracked_videos also lists publications without counts.
Neither scrape file mtimes nor batch-level timestamps establish an observation.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import sqlite3
from contextlib import closing
from pathlib import Path

GMT8 = dt.timezone(dt.timedelta(hours=8))
DB_NAME = "video_snapshots.sqlite3"
ENUM_SAMPLE_LIMIT = 80

SCHEMA = """
BEGIN IMMEDIATE;
CREATE TABLE IF NOT EXISTS tracked_videos (
    account TEXT NOT NULL, video_id TEXT NOT NULL,
    published_at TEXT, published_date TEXT, caption TEXT NOT NULL,
    url TEXT NOT NULL, hashtags TEXT NOT NULL, metadata_at TEXT NOT NULL,
    PRIMARY KEY (account, video_id)
);
CREATE TABLE IF NOT EXISTS video_observations (
    account TEXT NOT NULL, video_id TEXT NOT NULL, observed_date TEXT NOT NULL,
    observed_at TEXT NOT NULL, plays INTEGER NOT NULL CHECK (plays >= 0),
    PRIMARY KEY (account, video_id, observed_date)
);
CREATE INDEX IF NOT EXISTS observations_by_date
    ON video_observations(observed_date, account);
CREATE INDEX IF NOT EXISTS observations_by_account_date
    ON video_observations(account, observed_date);
CREATE INDEX IF NOT EXISTS videos_by_date ON tracked_videos(published_date, account);
CREATE INDEX IF NOT EXISTS account_publications ON tracked_videos(account, published_date);
CREATE VIEW IF NOT EXISTS video_snapshots AS
    SELECT o.account, o.video_id, o.observed_date, o.observed_at, o.plays,
           v.published_date, v.published_at, v.caption, v.url, v.hashtags
    FROM video_observations AS o JOIN tracked_videos AS v USING (account, video_id);
"""

UPSERT_VIDEO = """
INSERT INTO tracked_videos(account, video_id, published_at, published_date, caption,
                           url, hashtags, metadata_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(account, video_id) DO UPDATE SET
    published_at = CASE WHEN excluded.published_at IS NOT NULL AND
        (tracked_videos.published_at IS NULL OR excluded.metadata_at > tracked_videos.metadata_at)
        THEN excluded.published_at ELSE tracked_videos.published_at END,
    published_date = CASE WHEN excluded.published_at IS NOT NULL AND
        (tracked_videos.published_at IS NULL OR excluded.metadata_at > tracked_videos.metadata_at)
        THEN excluded.published_date ELSE COALESCE(tracked_videos.published_date, excluded.published_date) END,
    caption = CASE WHEN tracked_videos.caption = '' OR
        (excluded.caption <> '' AND excluded.metadata_at >= tracked_videos.metadata_at)
        THEN excluded.caption ELSE tracked_videos.caption END,
    hashtags = CASE WHEN tracked_videos.hashtags = '[]' OR
        (excluded.hashtags <> '[]' AND excluded.metadata_at >= tracked_videos.metadata_at)
        THEN excluded.hashtags ELSE tracked_videos.hashtags END,
    metadata_at = MAX(tracked_videos.metadata_at, excluded.metadata_at)
"""

UPSERT_COUNT = """
INSERT INTO video_observations(account, video_id, observed_date, observed_at, plays)
VALUES (?, ?, ?, ?, ?)
ON CONFLICT(account, video_id, observed_date) DO UPDATE SET
    observed_at = excluded.observed_at, plays = excluded.plays
WHERE excluded.observed_at > video_observations.observed_at
"""


def _iso_time(value, *, naive_gmt8=False) -> str | None:
    if not isinstance(value, str) or len(value) <= 10:
        return None
    try:
        stamp = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            if not naive_gmt8:
                return None
            stamp = stamp.replace(tzinfo=GMT8)
        return stamp.astimezone(GMT8).isoformat(timespec="microseconds")
    except (ValueError, OverflowError):
        return None


def _plays(value) -> int | None:
    if isinstance(value, str) and re.fullmatch(r"[0-9]{1,19}", value):
        value = int(value)
    return value if type(value) is int and 0 <= value <= 2**63 - 1 else None


def _account(value) -> str:
    return str(value or "").strip().lstrip("@").lower()


def record_scrape(db_path: Path, account: str, scrape: dict) -> None:
    """Catalog known items. Only successful, individually timestamped counts enter history."""
    account = _account(account)
    if (not isinstance(scrape, dict) or not re.fullmatch(r"[a-z0-9._]+", account)
            or _account(scrape.get("account")) != account):
        return
    items = scrape.get("items", [])
    errors = scrape.get("errors", [])
    if not isinstance(items, list) or not isinstance(errors, list):
        return
    failed = {str(row.get("id")) for row in errors if isinstance(row, dict)}
    videos, observations = [], []
    for item in items:
        if not isinstance(item, dict) or (item.get("author") and _account(item["author"]) != account):
            continue
        vid = str(item.get("id") or "")
        if not re.fullmatch(r"[0-9]{1,25}", vid):
            continue
        stamp = _iso_time(item.get("observed_at"))
        raw_publication = item.get("publish_time_gmt8")
        published = _iso_time(raw_publication, naive_gmt8=True)
        pub_date = published[:10] if published else None
        if isinstance(raw_publication, str) and len(raw_publication) == 10:
            try:
                pub_date = dt.date.fromisoformat(raw_publication).isoformat()
            except ValueError:
                pass
        tags = item.get("hashtags")
        tags = [t for t in tags if isinstance(t, str) and t] if isinstance(tags, list) else []
        caption = item.get("desc") if isinstance(item.get("desc"), str) else ""
        route = "photo" if f"/photo/{vid}" in str(item.get("url") or "") else "video"
        url = f"https://www.tiktok.com/@{account}/{route}/{vid}"
        videos.append((account, vid, published, pub_date,
                       caption, url, json.dumps(tags, ensure_ascii=False), stamp or ""))
        stats = item.get("stats")
        count = _plays(stats.get("playCount")) if isinstance(stats, dict) else None
        if stamp and count is not None and vid not in failed:
            observations.append((account, vid, stamp[:10], stamp, count))
    if not videos:
        return
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(db_path, timeout=15)) as con, con:
        con.executescript(SCHEMA)
        con.executemany(UPSERT_VIDEO, videos)
        con.executemany(UPSERT_COUNT, observations)


def record_enumeration(db_path: Path, account: str, entries: list, observed_at: dt.datetime) -> None:
    """Use counts already returned by the ordinary account listing, before date filtering."""
    items = []
    for entry in entries[:ENUM_SAMPLE_LIMIT]:
        if not isinstance(entry, dict):
            continue
        published = None
        timestamp = entry.get("timestamp")
        if type(timestamp) in (int, float) and timestamp > 0:
            try:
                published = dt.datetime.fromtimestamp(timestamp, GMT8).isoformat()
            except (ValueError, OSError, OverflowError):
                pass
        items.append({
            "id": entry.get("id"), "author": entry.get("uploader"),
            "publish_time_gmt8": published, "observed_at": observed_at.isoformat(),
            "url": entry.get("webpage_url"), "desc": entry.get("description") or "",
            "hashtags": entry.get("tags"), "stats": {"playCount": entry.get("view_count")},
        })
    record_scrape(db_path, account, {"account": account, "items": items})
