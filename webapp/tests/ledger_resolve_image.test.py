# -*- coding: utf-8 -*-
"""ledger_resolve_image.test.py — 图文账号点「表格」必须优先打开图文表（P1-1）

背景（2026-11-01 全功能体检 P1-1）
    视频主簿里残留了图文账号的同名 `对标记录-<账号>` 旧表，而 `ledger_resolve`
    里 `hit = exact_hit or row_hit` 让精确名命中优先，于是 15 个图文账号里 14 个
    打开的是旧视频表（example_account_019 只显示 9 行，真实图文 26 条）。

本用例覆盖任务 T-02 的 DoD 1-5
    1. media_type=image 且图文表里有该账号行 → 优先图文表（不被同名视频 sheet 抢走）
    2. 只有图文行、没有同名视频 sheet（example_account_051 型）→ 仍返回图文表
    3. 视频账号（media_type=video 或无该字段）→ 仍返回 `对标记录-<账号>`
    4. 图文账号在图文表里没有行、但有同名视频 sheet → 回退视频 sheet（不返回 None）
    5. 未知账号 → 404「账号不存在」（P2 起，CRD 0002 / Q-01 方案 A；原 DoD 5 的
       sheet=None + candidates 已废弃，账号存在但没台账仍返回 sheet=None）

隔离：全部数据在临时目录现造（openpyxl 两本 xlsx + 临时 accounts.json），
只 patch config.LEDGER_DIR / config.ACCOUNTS_JSON，不读也不写生产台账。
运行：webapp/.venv/Scripts/python.exe tests/ledger_resolve_image.test.py
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

IMG_SHEET = "图文账号记录"          # 与 pipeline 的图文表名一致（下文凡以「图文」开头的 sheet 都算图文表）
VIDEO_BOOK = "视频主簿_对标.xlsx"
IMAGE_BOOK = "图文账号_对标.xlsx"


class LedgerResolveImageTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

        self.video = self.root / VIDEO_BOOK
        self.image = self.root / IMAGE_BOOK
        # 视频主簿：残留的图文账号同名旧表（9 行）+ 真正的视频账号表
        self.book(self.video, {
            "对标记录-imgacc": 9,
            "对标记录-vidacc": 2,
            "对标记录-plainacc": 3,
            "对标记录-noacc": 4,       # 图文账号，但图文表里没有它的行 → 兜底用这张
        })
        # 图文簿：共享的「图文账号记录」，B 列逐行放 @账号
        rows = [("2026-11-01", "@imgacc")] * 26           # 真实图文 26 条
        rows += [("2026-11-01", "@example_account_051")] * 3          # 没有同名视频 sheet 的图文号
        rows += [("2026-11-01", "@vidacc")] * 1           # 视频号在图文表里的残留行（不该反抢视频表）
        rows += [("2026-11-01", "@someotherimg")] * 2
        self.book(self.image, {IMG_SHEET: rows})

        self.accounts = self.root / "accounts.json"
        self.accounts.write_text(json.dumps({
            "xlsx": str(self.video),
            "img_xlsx": str(self.image),
            "accounts": [
                {"account": "imgacc", "media_type": "image"},
                {"account": "example_account_051", "media_type": "image"},
                {"account": "noacc", "media_type": "image"},
                {"account": "vidacc", "media_type": "video"},
                {"account": "plainacc"},                  # 没有 media_type 字段 = 视频号
            ],
        }, ensure_ascii=False), encoding="utf-8")

        for name, val in (("LEDGER_DIR", self.root), ("ACCOUNTS_JSON", self.accounts)):
            p = patch.object(config, name, val)
            p.start()
            self.addCleanup(p.stop)

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
                for i in range(spec):
                    ws.append(["2026-11-01", name.split("-", 1)[1]] + ["fixture"] * 11)
            else:
                for date, who in spec:
                    ws.append([date, who] + [""] * 10)
        wb.save(path)
        wb.close()

    def test_image_account_prefers_image_sheet_over_same_name_video_sheet(self):
        """DoD 1：同名视频旧表存在时，图文账号仍打开图文表（example_account_019 型）。"""
        r = app.ledger_resolve(account="imgacc")
        self.assertEqual(r["sheet"], IMG_SHEET)
        self.assertEqual(r["file"], IMAGE_BOOK)
        self.assertEqual(Path(r["file_path"]), self.image.resolve())
        self.assertIn(IMG_SHEET, r["candidates"])

    def test_image_account_without_video_sheet_still_opens_image_sheet(self):
        """DoD 2：example_account_051 型（只有图文行、没有同名视频 sheet）仍返回图文表。"""
        r = app.ledger_resolve(account="example_account_051")
        self.assertEqual((r["sheet"], r["file"]), (IMG_SHEET, IMAGE_BOOK))

    def test_video_account_keeps_video_sheet(self):
        """DoD 3：video 账号行为不变（即使图文表里有它的残留行）。"""
        r = app.ledger_resolve(account="vidacc")
        self.assertEqual((r["sheet"], r["file"]), ("对标记录-vidacc", VIDEO_BOOK))

    def test_video_account_without_media_type_keeps_video_sheet(self):
        """DoD 3：没有 media_type 字段的账号按视频口径处理。"""
        r = app.ledger_resolve(account="plainacc")
        self.assertEqual((r["sheet"], r["file"]), ("对标记录-plainacc", VIDEO_BOOK))

    def test_image_account_without_row_falls_back_to_video_sheet(self):
        """DoD 4：图文表里没有该账号行、但有同名视频 sheet → 回退视频表，不返回 None。"""
        r = app.ledger_resolve(account="noacc")
        self.assertIsNotNone(r["sheet"])
        self.assertEqual((r["sheet"], r["file"]), ("对标记录-noacc", VIDEO_BOOK))

    def test_unknown_account_returns_none_and_candidates(self):
        """未知账号 → 404（P2 起；CRD 0002 / Q-01 方案 A，兼容语义变动）。

        账号不在 accounts.json 时直接 404「账号不存在：<名>」，不打开台账、
        不再把目录内真实 sheet 名当 candidates 回吐。账号存在但没台账仍返回 sheet=None。
        """
        with self.assertRaises(HTTPException) as cm:
            app.ledger_resolve(account="ghost_zz")
        self.assertEqual(cm.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main(verbosity=2)
