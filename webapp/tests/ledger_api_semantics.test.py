# -*- coding: utf-8 -*-
"""ledger_api_semantics.test.py — 台账接口语义（P2-1 / P2-8 / P2-新增 resolve）

覆盖任务 T-02 的 DoD：
  1. `/api/ledger/sheet`：openpyxl 抛 `PermissionError`（Excel 独占打开）→ 423 + 中文提示；正常 200
  2. `/api/ledger`：枚举目录内**全部** xlsx（含图文那本），sheets 数与 `/api/ledger/overview` 一致，
     `files` 与 `sheets` 来自同一枚举
  3. `/api/ledger/resolve`：accounts.json 里没有的账号 → 404「账号不存在：<名>」，不回吐 sheet 名；
     账号存在但没台账 → 200 `sheet=None` + candidates；`example_account_019`（图文号）仍回图文表（P1-1 不回归）

隔离：两本 xlsx + accounts.json 全在临时目录现造（openpyxl），只 patch
`config.LEDGER_DIR` / `config.ACCOUNTS_JSON`；锁用 patch 模拟；不读生产台账、不写 data。
运行：webapp/.venv/Scripts/python.exe tests/ledger_api_semantics.test.py
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

WEBAPP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WEBAPP))
import _isolate  # noqa: F401  运行 journal 落到临时文件，不污染生产 runs.jsonl
import openpyxl  # noqa: E402
import app  # noqa: E402
import config  # noqa: E402
from fastapi import HTTPException  # noqa: E402

VIDEO_BOOK = "视频主簿_对标.xlsx"
IMAGE_BOOK = "图文账号_对标.xlsx"
IMG_SHEET = "图文账号记录"
LOCKED_MSG = "台账被占用，请关闭 Excel 后重试"


class LedgerApiSemanticsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.video = self.root / VIDEO_BOOK
        self.image = self.root / IMAGE_BOOK
        # 视频主簿：两个视频号 + 一个「图文号残留的同名旧表」（P1-1 场景）
        self.book(self.video, {
            "对标记录-vidacc": 2,
            "对标记录-example_account_019": 9,
        })
        # 图文簿：共享图文表，B 列逐行 @账号
        self.book(self.image, {"图文账号记录": [
            ("2026-11-01", "@example_account_019")] * 26
            + [("2026-11-01", "@example_account_051")] * 3})
        self.accounts = self.root / "accounts.json"
        self.accounts.write_text(json.dumps({
            "xlsx": str(self.video),
            "img_xlsx": str(self.image),
            "accounts": [
                {"account": "vidacc", "media_type": "video"},
                {"account": "example_account_019", "media_type": "image"},
                {"account": "example_account_051", "media_type": "image"},
                {"account": "noacc", "media_type": "video"},   # 在账号表里，但台账里没有它
            ],
        }, ensure_ascii=False), encoding="utf-8")
        for name, val in (("LEDGER_DIR", self.root), ("ACCOUNTS_JSON", self.accounts)):
            p = patch.object(config, name, val)
            p.start()
            self.addCleanup(p.stop)
        cache = patch.dict(app._ledger_cache, {"ts": 0.0, "data": None})
        cache.start()
        self.addCleanup(cache.stop)

    @staticmethod
    def book(path: Path, sheets: dict) -> None:
        """sheets: {sheet 名: 数据行数} 或 {sheet 名: [(记录日期, @账号), ...]}。"""
        wb = openpyxl.Workbook()
        wb.remove(wb.active)
        for name, spec in sheets.items():
            ws = wb.create_sheet(name)
            ws.append(config.IMAGE_EXPECTED_COLS if name.startswith("图文")
                      else config.EXPECTED_COLS)
            if isinstance(spec, int):
                for _ in range(spec):
                    ws.append(["2026-11-01", "fixture"] + ["x"] * 11)
            else:
                for date, who in spec:
                    ws.append([date, who] + [""] * 10)
        wb.save(path)
        wb.close()

    # ── DoD 1：Excel 锁 → 423，正常 → 200 ────────────────────────────
    def test_locked_ledger_returns_423_with_chinese_detail(self):
        with patch.object(openpyxl, "load_workbook",
                          side_effect=PermissionError(13, "Permission denied")):
            with self.assertRaises(HTTPException) as exc:
                app.ledger_sheet(name="对标记录-vidacc")
        self.assertEqual(exc.exception.status_code, 423)
        self.assertIn(LOCKED_MSG, str(exc.exception.detail))

    def test_normal_ledger_sheet_still_returns_rows(self):
        result = app.ledger_sheet(name="对标记录-vidacc")
        self.assertEqual(result["sheet"], "对标记录-vidacc")
        self.assertEqual(result["file"], VIDEO_BOOK)
        self.assertEqual(len(result["rows"]), 2)

    # ── DoD 2：/api/ledger 与 overview 同一枚举 ──────────────────────
    def test_ledger_lists_every_book_and_matches_overview(self):
        info = app.ledger_info()
        overview = app.ledger_overview(True)
        self.assertTrue(info["exists"])
        self.assertEqual(set(info["files"]), {VIDEO_BOOK, IMAGE_BOOK})
        self.assertEqual(len(info["sheets"]), len(overview["sheets"]))
        self.assertEqual(len(info["sheets"]), 3)          # 2 视频 sheet + 1 图文 sheet
        self.assertIn(IMG_SHEET, [s["name"] for s in info["sheets"]])
        # files 与 sheets 来自同一枚举：每个 sheet 的 file 都在 files 里，两本书都有 sheet
        self.assertEqual({s["file"] for s in info["sheets"]}, set(info["files"]))

    # ── DoD 3：resolve 未知账号 404，不回吐 sheet 名 ─────────────────
    def test_resolve_unknown_account_is_404_without_sheet_names(self):
        with self.assertRaises(HTTPException) as exc:
            app.ledger_resolve(account="ghost_zz")
        self.assertEqual(exc.exception.status_code, 404)
        self.assertEqual(str(exc.exception.detail), "账号不存在：ghost_zz")
        self.assertNotIn(IMG_SHEET, str(exc.exception.detail))
        self.assertNotIn("对标记录-", str(exc.exception.detail))

    def test_resolve_known_account_without_ledger_still_returns_none(self):
        result = app.ledger_resolve(account="noacc")
        self.assertIsNone(result["sheet"])
        self.assertEqual(result["account"], "noacc")
        self.assertIsInstance(result["candidates"], list)

    # ── DoD 4：图文号仍回图文表（P1-1 不回归）；视频号行为不变 ──────
    def test_resolve_image_account_still_prefers_image_sheet(self):
        result = app.ledger_resolve(account="example_account_019")
        self.assertEqual((result["sheet"], result["file"]), (IMG_SHEET, IMAGE_BOOK))

    def test_resolve_video_account_keeps_video_sheet(self):
        result = app.ledger_resolve(account="vidacc")
        self.assertEqual((result["sheet"], result["file"]), ("对标记录-vidacc", VIDEO_BOOK))


if __name__ == "__main__":
    unittest.main(verbosity=2)
