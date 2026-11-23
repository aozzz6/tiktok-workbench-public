"""Check the dated play deltas and local transcript artifacts used in the video view."""
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "pipeline" / "scripts"))

import benchmark
import config
import daily_insights
import video_snapshots


class BenchmarkWorkbenchTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.output = Path(tmp.name)
        self.audio = self.output / "custom_audio"
        self.transcripts = self.audio / "transcripts"
        self.transcripts.mkdir(parents=True)
        self.enterContext(patch.object(config, "OUTPUT_DIR", self.output))
        self.enterContext(patch.object(config, "load_accounts", return_value={
            "accounts": [{"account": "maker", "audio_dir": str(self.audio)}]
        }))

    def test_growth_requires_today_and_yesterday_for_same_video_and_account(self):
        now = datetime(2026, 11, 24, 17, tzinfo=daily_insights.GMT8)
        yesterday = now - timedelta(days=1)
        old = now - timedelta(days=8)
        db = self.output / "video_snapshots.sqlite3"

        for account, vid, published, observed, plays in [
            ("maker", "101", old, yesterday, 120), ("maker", "101", old, now, 100),
            ("maker", "102", old, yesterday, 90), ("maker", "102", old, now, 90),
            ("studio", "103", old, yesterday, 25), ("maker", "103", old, now, 35),
            ("maker", "104", now, now, 10),
            ("maker", "105", old, yesterday, 100), ("maker", "105", old, now + timedelta(hours=1), 999),
            ("maker", "106", old, yesterday, 100), ("maker", "106", old, now, 120),
        ]:
            video_snapshots.record_scrape(db, account, {"account": account, "items": [{
                "id": vid, "publish_time_gmt8": published.isoformat(),
                "observed_at": observed.isoformat(), "stats": {"playCount": plays},
            }]})

        rows = [{"account": "maker", "vid": str(vid)} for vid in range(101, 107)]
        rows[-1]["media_type"] = "image"
        self.assertEqual(daily_insights.benchmark_growth(rows, now), {
            ("maker", "101"): {"delta": -20, "status": "comparable"},
            ("maker", "102"): {"delta": 0, "status": "comparable"},
            ("maker", "104"): {"delta": None, "status": "baseline"},
        })

    def test_scan_and_read_use_local_account_transcripts(self):
        (self.transcripts / "maker_101_transcript.txt").write_text("[00:00] 口播", encoding="utf-8-sig")
        (self.transcripts / "102_transcript.txt").write_text("", encoding="utf-8")
        (self.transcripts / "103_transcript.txt").write_bytes(b"\xff")
        rows = [{"account": "maker", "vid": str(vid)} for vid in range(101, 105)]
        rows[-1]["url"] = "https://www.tiktok.com/@maker/photo/104"

        benchmark.add_transcript_status(rows)
        self.assertEqual([row["transcript_status"] for row in rows],
                         ["available", "missing", "available", "not_applicable"])
        self.assertEqual(benchmark.read_video_transcript("maker", "101"),
                         {"status": "available", "text": "[00:00] 口播", "truncated": False})
        self.assertEqual(benchmark.read_video_transcript("maker", "103"), {"status": "unavailable"})
        self.assertEqual(benchmark.read_video_transcript("studio", "101"), {"status": "missing"})
        self.assertEqual(benchmark.read_video_transcript("../maker", "101"), {"status": "missing"})


if __name__ == "__main__":
    unittest.main()
