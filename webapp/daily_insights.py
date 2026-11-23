"""Read the editorial homepage from the local video catalog and dated play observations."""
from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
from bisect import bisect_left, bisect_right
from collections import defaultdict
from contextlib import closing
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from statistics import median

import config


GMT8 = timezone(timedelta(hours=8))
BASELINE_DAYS = 30
MIN_PEERS = 3
MIN_MULTIPLIER = 1.5
VIDEO_LIMIT = 20
TOPIC_LIMIT = 8
SCRAPE_BYTE_LIMIT = 16 * 1024 * 1024
HANDLE = re.compile(r"[a-z0-9_.]{1,32}", re.ASCII)
VIDEO_ID = re.compile(r"[0-9]{1,25}", re.ASCII)
HASHTAG = re.compile(r"#(\w+)", re.UNICODE)
GENERIC_TAGS = frozenset("""
    ad ads advertisement advertising sponsored partner partnership promotion promo sale
    fyp fy fypp fypage foryou foryoupage foryourpage parati pourtoi fürdich viral
    viralvideo viralvideos trending trend trends tiktok tiktoker tiktokviral tiktokvideo
    tiktokshop tiktokshopus tiktokshopuk tiktokshopde tiktokshopfinds tiktokmademebuyit
    shop shopping shoppable shoppingfinds musthave musthaves explore explorepage shorts
    reel reels capcut affiliate werbung 推荐 热门 广告 带货 好物推荐
""".split())


def _timestamp(value):
    if not isinstance(value, str) or len(value) < 16:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=GMT8) if parsed.tzinfo is None else parsed.astimezone(GMT8)
    except (ValueError, OverflowError):
        return None


def _publication(value, local_date=None):
    precise = _timestamp(value)
    if precise:
        return precise, precise.isoformat()
    try:
        return None, date.fromisoformat(local_date or value).isoformat()
    except (TypeError, ValueError):
        return None, ""


def _handle(value):
    return value.strip().lstrip("@").lower() if isinstance(value, str) else ""


def _eligible_accounts():
    accounts = {}
    test_accounts = {_handle(a) for a in config.TEST_ACCOUNTS}
    for account in config.load_accounts().get("accounts") or []:
        if not isinstance(account, dict) or not account.get("active", True):
            continue
        name = _handle(account.get("account"))
        if (HANDLE.fullmatch(name) and name not in test_accounts and
                not account.get("ledger_skip") and account.get("media_type") != "image"):
            accounts[name] = Path(account.get("out_dir") or config.OUTPUT_DIR)
    return accounts


def _video(account, video_id, published, caption, plays):
    title = " ".join(str(caption or "").split())[:220]
    return {"id": video_id, "account": account, "published_at": published,
            "title": title or f"@{account} 的视频",
            "url": f"https://www.tiktok.com/@{account}/video/{video_id}", "plays": plays}


@lru_cache(maxsize=256)
def _scrape_publications(path, name, day, mtime_ns, size):
    """Read older installations once per file version. File-level scrape time can cover merged rows."""
    if size > SCRAPE_BYTE_LIMIT:
        return [], False
    with path.open("rb") as source:
        raw = source.read(SCRAPE_BYTE_LIMIT + 1)
    if len(raw) > SCRAPE_BYTE_LIMIT:
        return [], False
    data = json.loads(raw)
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        return [], False
    if data.get("account") and _handle(data["account"]) != name:
        return [], False
    videos = []
    for item in data["items"]:
        if not isinstance(item, dict) or (item.get("author") and _handle(item["author"]) != name):
            continue
        video_id = str(item.get("id") or "")
        if f"/photo/{video_id}" in str(item.get("url") or ""):
            continue
        _, published = _publication(item.get("publish_time_gmt8"))
        if VIDEO_ID.fullmatch(video_id) and published[:10] == day.isoformat():
            videos.append(_video(name, video_id, published, item.get("desc"), None))
    return videos, True


def _fallback_today(accounts, now, notes):
    known = {}
    unreadable = 0
    for name, directory in accounts.items():
        path = directory / f"scrape_{name}.json"
        try:
            if not path.is_file():
                path = config.OUTPUT_DIR / path.name
            stat = path.stat()
            videos, valid = _scrape_publications(path, name, now.date(), stat.st_mtime_ns, stat.st_size)
            unreadable += int(not valid)
        except FileNotFoundError:
            continue
        except (OSError, ValueError):
            unreadable += 1
            continue
        for video in videos:
            if video["published_at"] <= now.isoformat():
                known[(name, video["id"])] = video.copy()
    if unreadable:
        notes.append(f"{unreadable} 个账号的旧抓取文件过大或无法读取，今日发布列表可能不完整。")
    return known


def _snapshot_rows(db, accounts, today, yesterday, cutoff):
    """Use indexed date windows. Read each account separately to bound working memory."""
    for name in accounts:
        catalog = db.execute("""
            SELECT video_id, published_at, published_date, caption FROM tracked_videos
            WHERE account = ? AND published_date = ? AND instr(url, '/photo/') = 0""", (name, today))
        # Old publications only need two readings. For recent publications the first
        # three days after release supply the age-matched reference observations.
        rows = db.execute("""
            SELECT video_id, observed_date, observed_at, published_at, published_date, plays, caption, hashtags
            FROM video_snapshots
            WHERE account = ? AND observed_date BETWEEN ? AND ? AND instr(url, '/photo/') = 0
            UNION ALL
            SELECT video_id, observed_date, observed_at, published_at, published_date, plays, caption, hashtags
            FROM video_snapshots
            WHERE account = ? AND published_date BETWEEN ? AND ? AND instr(url, '/photo/') = 0
              AND observed_date BETWEEN published_date AND date(published_date, '+3 days') AND observed_date < ?
            """, (name, yesterday, today, name, cutoff, today, yesterday))
        yield name, catalog, rows


def benchmark_growth(videos: list[dict], now: datetime | None = None) -> dict:
    """Return same-account comparisons for the requested benchmark videos."""
    now = now or datetime.now(GMT8)
    now = now.replace(tzinfo=GMT8) if now.tzinfo is None else now.astimezone(GMT8)
    today = now.date().isoformat()
    yesterday = (now.date() - timedelta(days=1)).isoformat()
    wanted = defaultdict(set)
    for video in videos:
        if video.get("media_type") != "image" and "/photo/" not in (video.get("url") or ""):
            wanted[_handle(video.get("account"))].add(video.get("vid"))
    path = config.OUTPUT_DIR / "video_snapshots.sqlite3"
    if not wanted or not path.is_file():
        return {}

    results = {}
    try:
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=1)) as db:
            db.execute("BEGIN")
            for account, ids in wanted.items():
                by_video = defaultdict(dict)
                for vid, day, taken, plays, published, published_date in db.execute("""
                    SELECT video_id, observed_date, observed_at, plays, published_at, published_date
                    FROM video_snapshots
                    WHERE account = ? AND observed_date IN (?, ?) AND instr(url, '/photo/') = 0
                    """, (account, yesterday, today)):
                    observed = _timestamp(taken)
                    precise, publication = _publication(published, published_date)
                    if (vid in ids and type(plays) is int and plays >= 0 and observed and observed <= now
                            and observed.date().isoformat() == day and publication and publication[:10] <= day
                            and (not precise or precise <= observed)):
                        by_video[vid][day] = (plays, publication[:10])
                for vid, readings in by_video.items():
                    current = readings.get(today)
                    if not current:
                        continue
                    if yesterday in readings and current[1] < today:
                        results[account, vid] = {"delta": current[0] - readings[yesterday][0], "status": "comparable"}
                    elif current[1] == today:
                        results[account, vid] = {"delta": None, "status": "baseline"}
    except sqlite3.Error:
        return {}
    return results


def _tags(raw, caption):
    try:
        explicit = json.loads(raw or "[]")
    except (TypeError, ValueError):
        explicit = []
    tags = set()
    for tag in (explicit if isinstance(explicit, list) else []) + HASHTAG.findall(caption or ""):
        if not isinstance(tag, str):
            continue
        tag = unicodedata.normalize("NFKC", tag).strip().lstrip("#").casefold()
        if (2 <= len(tag) <= 64 and re.fullmatch(r"\w+", tag) and not tag.isdecimal()
                and tag not in GENERIC_TAGS and not tag.startswith("fyp")):
            tags.add(tag)
    return sorted(tags)


@dataclass
class Observation:
    video: dict
    published: datetime
    observed: datetime
    age_hours: float
    tags: list[str]


def _topics_for_account(readings, window_start):
    latest = {}
    for item in readings:
        key = item.video["id"]
        if item.published.date() >= window_start and (key not in latest or item.observed > latest[key].observed):
            latest[key] = item
    by_age = sorted(readings, key=lambda r: r.age_hours)
    ages = [r.age_hours for r in by_age]
    hits = []
    assessed = False
    for candidate in latest.values():
        if candidate.age_hours < 1:
            continue
        tags = candidate.tags
        if not tags:
            continue
        tolerance = min(6, candidate.age_hours / 4)
        left = bisect_left(ages, candidate.age_hours - tolerance)
        right = bisect_right(ages, candidate.age_hours + tolerance)
        earliest = candidate.published - timedelta(days=BASELINE_DAYS)
        peers = {}
        for peer in by_age[left:right]:
            if (not earliest <= peer.published < candidate.published or peer.observed > candidate.observed
                    or peer.video["id"] == candidate.video["id"]):
                continue
            distance = abs(peer.age_hours - candidate.age_hours)
            old = peers.get(peer.video["id"])
            if old is None or distance < old[0]:
                peers[peer.video["id"]] = (distance, peer.video["plays"])
        if len(peers) < MIN_PEERS:
            continue
        baseline = median(p[1] for p in peers.values())
        if baseline <= 0:
            continue
        assessed = True
        ratio = candidate.video["plays"] / baseline
        if ratio >= MIN_MULTIPLIER:
            example = dict(candidate.video, baseline_plays=baseline, baseline_size=len(peers),
                           multiplier=round(ratio, 2), age_hours=round(candidate.age_hours, 1),
                           observed_at=candidate.observed.isoformat())
            hits.extend((tag, example) for tag in tags)
    return hits, assessed


def _account_readings(name, catalog, rows, now, window_start, known, coverage, owners):
    today = now.date()
    yesterday = today - timedelta(days=1)
    for row in catalog:
        vid = str(row["video_id"])
        precise, published = _publication(row["published_at"], row["published_date"])
        if (VIDEO_ID.fullmatch(vid) and published[:10] == today.isoformat()
                and (not precise or precise <= now)):
            known[name, vid] = _video(name, vid, published, row["caption"], None)

    current, previous, recent = {}, {}, []
    observed_days = set()
    for row in rows:
        vid, plays = str(row["video_id"]), row["plays"]
        observed = _timestamp(row["observed_at"])
        if (not VIDEO_ID.fullmatch(vid) or type(plays) is not int or plays < 0 or not observed
                or observed.date().isoformat() != row["observed_date"] or observed > now):
            continue
        observed_days.add(observed.date())
        owners[vid].add(name)
        precise, published = _publication(row["published_at"], row["published_date"])
        if not published or published[:10] > row["observed_date"] or (precise and precise > observed):
            continue
        if observed.date() == yesterday:
            previous[vid] = plays
        video = _video(name, vid, published, row["caption"], plays)
        if observed.date() == today:
            current[vid] = video
            if published[:10] == today.isoformat():
                known[name, vid] = video
        if not precise:
            continue
        age = (observed - precise).total_seconds() / 3600
        if age <= 78:  # A 72-hour candidate allows a peer up to six hours older.
            tags = _tags(row["hashtags"], row["caption"]) if precise.date() >= window_start else []
            recent.append(Observation(video, precise, observed, age, tags))
    coverage["observed_today_accounts"] += int(today in observed_days)
    coverage["observed_yesterday_accounts"] += int(yesterday in observed_days)
    growing, comparable = [], set()
    for vid, video in current.items():
        if video["published_at"][:10] >= today.isoformat() or vid not in previous:
            continue
        comparable.add(vid)
        growth = video["plays"] - previous[vid]
        if growth > 0:
            growing.append(dict(video, previous_plays=previous[vid], growth=growth))
    hits, assessed = _topics_for_account(recent, window_start)
    coverage["baseline_accounts"] += int(assessed)
    return growing, comparable, hits


def _shared_topics(hits, owners):
    grouped = defaultdict(list)
    for tag, video in hits:
        if len(owners[video["id"]]) == 1:
            grouped[tag].append(video)
    ranked = []
    for tag, videos in grouped.items():
        examples = {}
        for video in sorted(videos, key=lambda v: (-v["multiplier"], -v["plays"], v["id"])):
            examples.setdefault(video["account"], video)
        if len(examples) >= 2:
            score = median(v["multiplier"] for v in examples.values())
            topic = {"tag": tag, "account_count": len(examples), "video_count": len(videos),
                     "examples": list(examples.values())[:3]}
            ranked.append((-len(examples), -score, tag, topic))
    return [entry[-1] for entry in sorted(ranked)]


def _section(items, limit):
    return {"count": len(items), "items": items[:limit]}


def get_daily_insights(now: datetime | None = None) -> dict:
    now = now or datetime.now(GMT8)
    now = now.replace(tzinfo=GMT8) if now.tzinfo is None else now.astimezone(GMT8)
    today = now.date()
    yesterday = today - timedelta(days=1)
    window_start = today - timedelta(days=2)
    cutoff = window_start - timedelta(days=BASELINE_DAYS)
    accounts = _eligible_accounts()
    notes = []
    coverage = {"tracked_accounts": len(accounts), "observed_today_accounts": 0,
                "observed_yesterday_accounts": 0, "comparable_videos": 0,
                "baseline_accounts": 0, "notes": notes}
    known = _fallback_today(accounts, now, notes)
    growing, comparable, hits = [], set(), []
    owners = defaultdict(set)
    path = config.OUTPUT_DIR / "video_snapshots.sqlite3"
    if accounts and path.is_file():
        try:
            with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=1)) as db:
                db.row_factory = sqlite3.Row
                db.execute("BEGIN")
                for name, catalog, rows in _snapshot_rows(db, accounts, today.isoformat(), yesterday.isoformat(),
                                                          cutoff.isoformat()):
                    gains, pairs, candidates = _account_readings(name, catalog, rows, now, window_start,
                                                                 known, coverage, owners)
                    growing.extend(gains)
                    comparable.update(pairs)
                    hits.extend(candidates)
        except sqlite3.Error:
            notes.append("暂时无法完整读取播放快照，当前仅展示已读取的记录。")
    if accounts and not coverage["observed_today_accounts"]:
        notes.append("今日尚无带采集时间的播放快照。")
    if accounts and not coverage["observed_yesterday_accounts"]:
        notes.append("昨日缺少播放快照，无法计算旧视频的日增量。")
    for name, vid in known:
        owners[vid].add(name)
    coverage["comparable_videos"] = sum(len(owners[vid]) == 1 for vid in comparable)
    new_videos = sorted((v for v in known.values() if len(owners[v["id"]]) == 1),
                        key=lambda v: (v["published_at"], v["id"]), reverse=True)
    growing = sorted((v for v in growing if len(owners[v["id"]]) == 1),
                     key=lambda v: (-v["growth"], -v["plays"], v["id"]))
    return {"date": today.isoformat(), "previous_date": yesterday.isoformat(), "window_start": window_start.isoformat(),
            "coverage": coverage, "new_videos": _section(new_videos, VIDEO_LIMIT),
            "growing_videos": _section(growing, VIDEO_LIMIT), "topics": _section(_shared_topics(hits, owners), TOPIC_LIMIT)}
