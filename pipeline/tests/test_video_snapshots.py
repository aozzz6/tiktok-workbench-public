"""Synthetic daily observations across listing, merged detail output and batch replay."""
import contextlib
from concurrent.futures import ThreadPoolExecutor
import datetime as dt
import io
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import scrape_tiktok_video as detail
import tk_audio_download as audio
import video_snapshots as store

with patch.dict(os.environ, {"WB_BASE": "http://127.0.0.1:1"}):
    import daily_batch


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.output = self.root / "output"
        self.output.mkdir()
        self.db = self.output / store.DB_NAME

    def save(self, *items, errors=()):
        store.record_scrape(self.db, "ALICE", {
            "account": "Alice", "items": list(items), "errors": list(errors),
            "scraped_at": "2050-01-01T12:00:00",
        })

    def rows(self, sql):
        with contextlib.closing(sqlite3.connect(self.db)) as con:
            return con.execute(sql).fetchall()

    @staticmethod
    def item(vid="1", count=100, stamp="2026-11-21T15:59:59Z", **extra):
        return {"id": vid, "author": "@Alice", "stats": {"playCount": count},
                "observed_at": stamp, **extra}

    def test_midnight_retry_and_metadata_recovery(self):
        self.save(self.item(count=125, stamp="2026-11-21T16:05:00Z"),
                  self.item(count=200, stamp="2026-11-21T16:10:00Z"))
        self.save(self.item(count=220, stamp="2026-11-21T16:08:00Z"))
        self.save(self.item(count=190, stamp="2026-11-21T16:11:00Z"), self.item())
        legacy = self.item(stamp=None, count=999, publish_time_gmt8="2026-11-01",
                           hashtags=["spice"], desc="original")
        self.save(legacy)
        self.assertEqual(self.rows("select published_at, published_date from tracked_videos"),
                         [(None, "2026-11-01")])
        legacy["publish_time_gmt8"] = "2026-11-01 08:20:00"
        self.save(legacy)
        self.assertEqual(self.rows("select observed_date, plays, caption, hashtags "
                                   "from video_snapshots order by observed_date"),
                         [("2026-11-21", 100, "original", '["spice"]'),
                          ("2026-11-22", 190, "original", '["spice"]')])
        self.save(self.item(stamp="2026-11-23T08:00:00+08:00", publish_time_gmt8="2026-11-02 13:10:00"))
        self.save(legacy)  # replaying older metadata must not undo a verified correction
        self.assertEqual(self.rows("select distinct published_date from video_snapshots"), [("2026-11-02",)])

    def test_missing_invalid_failed_and_wrong_owner(self):
        rows = [self.item(str(index), count) for index, count in
                enumerate([0, "0", None, False, -1, "N/A", 2**63], 1)]
        self.save(*rows, self.item("8", author="bob"), self.item("9", stamp=None),
                  self.item("10", stamp="2026-11-22T10:00:00"), errors=[{"id": "2"}])
        self.assertEqual(self.rows("select video_id, plays from video_snapshots"), [("1", 0)])
        self.assertEqual(self.rows("select count(*) from tracked_videos"), [(9,)])

    def test_overlapping_writers_keep_latest_measurement(self):
        def write(second):
            self.save(self.item(count=100 - second, stamp=f"2026-11-21T18:01:{second:02d}+08:00"))

        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(write, [9, 1, 8, 3, 4, 2]))
        self.assertEqual(self.rows("select plays, observed_at from video_snapshots"),
                         [(91, "2026-11-21T18:01:09.000000+08:00")])

    def test_listing_in_daily_workflow_with_empty_download_window(self):
        timestamp = dt.datetime(2026, 10, 1, tzinfo=store.GMT8).timestamp()
        entries = [{"id": str(i), "timestamp": timestamp, "view_count": i,
                    "uploader": "alice", "description": "fall"}
                   for i in range(1, 90)]
        entries[0].pop("view_count")
        entries[1]["uploader"] = "someone_else"
        argv = ["audio", "--account", "alice", "--from", "2026-11-23", "--to", "2026-11-23",
                "--out", str(self.root / "sound"), "--snapshot-dir", str(self.output)]
        with patch.object(sys, "argv", argv), patch.object(audio, "enumerate_videos", return_value=entries), \
                patch.object(audio, "download_audio") as download, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(audio.main(), 0)
            download.assert_not_called()
        self.assertEqual(self.rows("select count(*), max(plays) from video_snapshots"), [(78, 80)])
        self.assertEqual(self.rows("select distinct published_date from tracked_videos"), [("2026-10-01",)])

    def test_partial_scrape_merge_and_later_failed_account_replay(self):
        path = self.output / "scrape_alice.json"
        history = self.item("7777777777777777701", count=540, stamp=None)
        path.write_text(json.dumps({"account": "alice", "items": [history]}), encoding="utf-8")
        target = "7777777777777777702"
        fetched = {"desc": "fresh", "stats": {"playCount": 42}, "author": {"uniqueId": "Alice"}}
        args = ["detail", "--account", "alice", "--ids", target, "--merge",
                "--out-dir", str(self.output), "--blocked-retry", "0"]
        with patch.object(sys, "argv", args), patch.object(detail, "fetch_page", return_value="ok"), \
                patch.object(detail, "parse_item_detail", return_value=(fetched, "", 0, "")), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(detail.main(), 0)
        rows = {row["id"]: row for row in json.loads(path.read_text(encoding="utf-8"))["items"]}
        self.assertIsNone(rows[history["id"]]["observed_at"])
        self.assertTrue(rows[target]["observed_at"].endswith("+08:00"))
        self.assertEqual(self.rows("select video_id, plays from video_snapshots"), [(target, 42)])

        self.db.unlink()  # simulate recovery from an unavailable database during stage 2
        with patch.object(daily_batch, "PIPE_DIR", self.root), \
                patch.object(daily_batch, "_post_run_when_ledger_free", return_value={"id": "fixture"}), \
                patch.object(daily_batch, "wait_run", return_value={"status": "failed", "exit_code": 5}):
            report = daily_batch.run_account({"account": "alice"}, "2026-11-23", "2026-11-23",
                                             1, 1, 1, lambda _: None)
        self.assertEqual(report["status"], "failed")
        self.assertEqual(self.rows("select video_id, plays from video_snapshots"), [(target, 42)])

    def test_malformed_scrape_does_not_abort_daily_batch(self):
        (self.output / "scrape_alice.json").write_text("[]", encoding="utf-8")
        with patch.object(daily_batch, "PIPE_DIR", self.root), \
                patch.object(daily_batch, "_post_run_when_ledger_free", return_value={"id": "fixture"}), \
                patch.object(daily_batch, "wait_run", return_value={"status": "done", "duration_s": 20}):
            report = daily_batch.run_account({"account": "alice"}, "2026-11-23", "2026-11-23",
                                             1, 1, 1, lambda _: None)
        self.assertEqual(report["status"], "warn")
        self.assertIn("scrape_alice.json", report["anomalies"][0])
        self.assertFalse(self.db.exists())


if __name__ == "__main__":
    unittest.main()
