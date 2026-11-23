"""Isolated editorial read-model fixtures. Run with python webapp/tests/test_daily_insights.py."""
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "pipeline" / "scripts"))
import daily_insights as insights
import video_snapshots


NOW = datetime(2026, 11, 23, 20, tzinfo=insights.GMT8)


class DailyInsightsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.output = Path(self.tmp.name)
        self.path = self.output / "video_snapshots.sqlite3"
        self.accounts = [{"account": "maker"}, {"account": "studio"},
                         {"account": "dormant", "active": False}, {"account": "photo", "media_type": "image"},
                         {"account": "skip", "ledger_skip": True}, {"account": "acc1"}]
        for key, value in [("OUTPUT_DIR", self.output), ("load_accounts", lambda: {"accounts": self.accounts})]:
            mock = patch.object(insights.config, key, value)
            mock.start()
            self.addCleanup(mock.stop)

    def put(self, db, account, vid, published, observed=None, plays=None, caption="示例", tags=(), url="javascript:bad()"):
        publication = published.isoformat() if isinstance(published, datetime) else published
        item = {"id": str(vid), "publish_time_gmt8": publication, "desc": caption, "hashtags": list(tags),
                "url": url, "stats": {"playCount": plays}, "observed_at": observed.isoformat() if observed else None}
        video_snapshots.record_scrape(db, account, {"account": account, "items": [item]})

    def test_scrape_only_and_local_midnight(self):
        rows = [{"id": str(vid), "publish_time_gmt8": pub, "desc": "<img> #cups",
                 "stats": {"playCount": 800}, "author": author, "url": "javascript:bad()"}
                for vid, pub, author in [(100, "2026-11-22T16:00:00Z", "maker"),
                                         (101, "2026-11-22T15:59:59Z", "maker"),
                                         (102, "2026-11-23", "maker"),
                                         (103, "2026-11-23 19:00:01", "someone_else"),
                                         (104, "2026-11-23 23:00:00", "maker")]]
        for account in ("maker", "photo", "dormant"):
            (self.output / f"scrape_{account}.json").write_text(
                json.dumps({"scraped_at": NOW.isoformat(), "items": rows}), encoding="utf-8")
        result = insights.get_daily_insights(NOW.astimezone(timezone.utc))
        self.assertEqual(result["coverage"]["tracked_accounts"], 2)
        self.assertEqual(result["date"], "2026-11-23")
        self.assertEqual(result["window_start"], "2026-11-21")
        self.assertEqual(result["new_videos"]["count"], 2)
        midnight, date_only = result["new_videos"]["items"]
        self.assertEqual(midnight["url"], "https://www.tiktok.com/@maker/video/100")
        self.assertEqual(midnight["published_at"], "2026-11-23T00:00:00+08:00")
        self.assertEqual(date_only["published_at"], "2026-11-23")
        self.assertIsNone(midnight["plays"])
        self.assertEqual(result["growing_videos"]["count"], 0)
        self.assertEqual(result["coverage"]["observed_today_accounts"], 0)
        self.assertFalse(self.path.exists())

    def test_exact_growth_zero_missing_counts_and_account_filter(self):
        db = self.path
        old = NOW - timedelta(days=9)
        yesterday = NOW - timedelta(days=1)
        self.put(db, "maker", 1, old, yesterday, 0)
        self.put(db, "maker", 1, old, NOW, 60)
        self.put(db, "maker", 2, old, yesterday - timedelta(days=1), 10)
        self.put(db, "maker", 2, old, NOW, 500)
        self.put(db, "maker", 3, old, yesterday, 19)
        self.put(db, "maker", 3, old, NOW, 16)
        self.put(db, "maker", 4, old, NOW, None)
        self.put(db, "maker", 5, "2026-11-20", yesterday, 30)
        self.put(db, "maker", 5, "2026-11-20", NOW, 31)
        self.put(db, "maker", 6, yesterday, yesterday - timedelta(hours=1), 0)
        self.put(db, "maker", 6, yesterday, NOW, 900)
        self.put(db, "studio", 7, NOW, NOW, 0)
        self.put(db, "studio", 8, NOW - timedelta(hours=1))
        self.put(db, "studio", 9, NOW + timedelta(hours=1))
        self.put(db, "studio", 10, NOW, NOW, 500, url="https://www.tiktok.com/@studio/photo/10")
        for account in ("dormant", "photo", "skip", "acc1", "untracked"):
            self.put(db, account, 44, old, yesterday, 0)
            self.put(db, account, 44, old, NOW, 1000)
            self.put(db, account, 45, NOW, NOW, 1000)
        result = insights.get_daily_insights(NOW)
        self.assertEqual(result["coverage"]["comparable_videos"], 3)
        self.assertEqual(result["coverage"]["observed_today_accounts"], 2)
        self.assertEqual(result["coverage"]["observed_yesterday_accounts"], 1)
        self.assertEqual([(v["id"], v["previous_plays"], v["growth"]) for v in result["growing_videos"]["items"]],
                         [("1", 0, 60), ("5", 30, 1)])
        self.assertEqual([(v["id"], v["plays"]) for v in result["new_videos"]["items"]], [("7", 0), ("8", None)])

    def peers(self, db, account, start_id, values, age_hours=10):
        for i, plays in enumerate(values):
            published = NOW - timedelta(days=5 + i, hours=10)
            self.put(db, account, start_id + i, published, published + timedelta(hours=age_hours), plays)

    def test_topics_require_two_accounts_and_own_age_matched_peer_medians(self):
        db = self.path
        self.peers(db, "maker", 100, [80, 100, 120])
        self.peers(db, "studio", 200, [400, 500, 600])
        self.peers(db, "maker", 300, [9000, 9000, 9000], age_hours=48)
        published = NOW - timedelta(hours=10)
        desc = "a" * 280 + " #MugArt #fyp #Ad"
        self.put(db, "maker", 901, published, NOW, 175, caption=desc)
        self.put(db, "maker", 902, published, NOW, 150, tags=("MugArt", "onlyhere"))
        self.put(db, "studio", 903, published, NOW, 1000, tags=("mugart", "fyp", "ad"))
        self.put(db, "studio", 904, published, NOW, 740, tags=("onlyhere",))
        result = insights.get_daily_insights(NOW)
        self.assertEqual(result["coverage"]["baseline_accounts"], 2)
        self.assertEqual(result["topics"]["count"], 1)
        topic = result["topics"]["items"][0]
        self.assertEqual((topic["tag"], topic["account_count"], topic["video_count"]), ("mugart", 2, 3))
        self.assertEqual([(v["id"], v["baseline_plays"], v["baseline_size"], v["multiplier"])
                          for v in topic["examples"]], [("903", 500, 3, 2), ("901", 100, 3, 1.75)])
        self.assertEqual(len(set(v["account"] for v in topic["examples"])), 2)

    def test_repeated_handles_ids_and_sparse_peers_cannot_validate_a_topic(self):
        db = self.path
        self.accounts.append({"account": "@MAKER"})
        self.peers(db, "maker", 100, [100, 120, 90])
        self.peers(db, "studio", 200, [100, 120])
        published = NOW - timedelta(hours=10)
        for account in ("maker", "studio"):
            self.put(db, account, 900, published, NOW, 9000, tags=("duplicate",))
        self.put(db, "maker", 901, published, NOW, 9000, tags=("cups",))
        self.put(db, "studio", 902, published, NOW, 9000, tags=("cups",))
        # Rereading the same reference post cannot add a third distinct reference video.
        first = NOW - timedelta(days=5, hours=10)
        self.put(db, "studio", 200, first, first + timedelta(hours=11), 105)
        result = insights.get_daily_insights(NOW)
        self.assertEqual(result["coverage"]["tracked_accounts"], 2)
        self.assertEqual(result["coverage"]["baseline_accounts"], 1)
        self.assertEqual(result["topics"]["items"], [])
        self.peers(db, "studio", 300, [110])
        topics = insights.get_daily_insights(NOW)["topics"]["items"]
        self.assertEqual([t["tag"] for t in topics], ["cups"])

    def test_multiple_matching_observations_choose_nearest_once_per_video(self):
        db = self.path
        published = NOW - timedelta(days=1, hours=4)
        # Near midnight, observations on adjacent dates can match the same candidate age.
        for account, first_id in (("maker", 100), ("studio", 200)):
            for i in range(3):
                peer_published = NOW.replace(hour=20) - timedelta(days=5 + i)
                self.put(db, account, first_id+i, peer_published, peer_published + timedelta(hours=27), 100)
                self.put(db, account, first_id+i, peer_published, peer_published + timedelta(hours=29.5), 10000)
            self.put(db, account, first_id+9, published, NOW, 200, tags=("ceramics",))
        examples = insights.get_daily_insights(NOW)["topics"]["items"][0]["examples"]
        self.assertEqual([(v["baseline_size"], v["baseline_plays"]) for v in examples], [(3, 100), (3, 100)])

    def test_result_caps_and_corrupt_old_files(self):
        db = self.path
        for i in range(25):
            self.put(db, "maker", i+1, NOW-timedelta(minutes=i), NOW, i)
        (self.output / "scrape_studio.json").write_text("broken!", encoding="utf-8")
        result = insights.get_daily_insights(NOW)
        self.assertEqual(result["new_videos"]["count"], 25)
        self.assertEqual(len(result["new_videos"]["items"]), 20)
        self.assertTrue(any("抓取文件" in note for note in result["coverage"]["notes"]))


if __name__ == "__main__":
    unittest.main()
