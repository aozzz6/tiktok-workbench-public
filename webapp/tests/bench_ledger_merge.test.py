# -*- coding: utf-8 -*-
"""bench_ledger_merge.test.py — 台账行并入视频明细（隔离临时目录）

覆盖：
  · 台账有、抓取没有 → 以 source="ledger" 补进 videos
  · 同一视频两处都有 → 只保留 scrape 版本（不重复）
  · 台账 12 列（图文簿）也能读，缺音频/翻译列不报错
  · KPI / 爆款规律口径仍只算 scrape
  · account_options 收录只在台账出现的账号
运行：webapp/.venv/Scripts/python.exe tests/bench_ledger_merge.test.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import openpyxl

WEBAPP = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WEBAPP))
import config  # noqa: E402
import benchmark  # noqa: E402

_ok = 0
_fail = 0


def check(name, fn):
    global _ok, _fail
    try:
        fn()
        print(f"  ✓ {name}")
        _ok += 1
    except AssertionError as e:
        print(f"  ✗ {name} -> {e}")
        _fail += 1
    except Exception as e:
        print(f"  ✗ {name} -> {e.__class__.__name__}: {e}")
        _fail += 1


def assert_(c, m="assert failed"):
    if not c:
        raise AssertionError(m)


VIDEO_HDR = ["记录日期", "对标账号", "视频发布时间", "视频标题", "话题标签", "视频链接/ID",
             "商品链接", "播放量", "点赞数", "评论数", "转发数", "收藏数", "音频", "翻译"]
IMAGE_HDR = ["记录日期", "对标账号", "图文发布时间", "图文标题", "话题标签", "视频链接/ID",
             "商品链接", "播放量", "点赞数", "评论数", "转发数", "收藏数"]


def make_video_book(p: Path, sheets: dict):
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for sn, rows in sheets.items():
        ws = wb.create_sheet(sn)
        ws.append(VIDEO_HDR)
        for r in rows:
            ws.append(r)
    wb.save(p)


def make_image_book(p: Path, rows: list):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "图文账号记录"
    ws.append(IMAGE_HDR)
    for r in rows:
        ws.append(r)
    wb.save(p)


def scrape_item(vid, desc, plays):
    return {"id": vid, "desc": desc, "publish_time_gmt8": "2026-10-18 10:00:00",
            "url": f"https://www.tiktok.com/@example_account_051/video/{vid}",
            "stats": {"playCount": plays, "diggCount": 5, "commentCount": 1,
                      "shareCount": 0, "collectCount": 0}}


def build_env(sheet_rows, image_rows=None):
    tmp = Path(tempfile.mkdtemp(prefix="bl_"))
    out = tmp / "out"
    ledger = tmp / "ledger"
    out.mkdir()
    ledger.mkdir()
    vp = ledger / "tiktok_对标.xlsx"
    make_video_book(vp, {"对标记录-example_account_051": sheet_rows})
    ip = None
    if image_rows is not None:
        ip = ledger / "图文账号_对标.xlsx"
        make_image_book(ip, image_rows)
    # 抓取产物：只有 vid=111
    (out / "scrape_example_account_051.json").write_text(
        json.dumps({"items": [scrape_item("1111111111111111111", "抓取版文案", 5000)]},
                   ensure_ascii=False), encoding="utf-8")
    return tmp, out, ledger, vp, ip


def run_case(fn):
    saved = (config.OUTPUT_DIR, config.LEDGER_DIR, config.ledger_path, config.image_ledger_path)
    try:
        fn()
    finally:
        config.OUTPUT_DIR, config.LEDGER_DIR, config.ledger_path, config.image_ledger_path = saved
        benchmark._LEDGER_CACHE.update(key=None, data={})


def main() -> int:
    def case_merge():
        tmp, out, ledger, vp, _ = build_env([
            ["2026-10-19", "@example_account_051", "2026-10-18 10:00:00", "抓取版文案", "#a", 
             "https://www.tiktok.com/@example_account_051/video/1111111111111111111（ID: 1111111111111111111）",
             None, "5000", "5", "1", "0", "0", "英文音频", "中文翻译"],
            ["2026-10-19", "@example_account_051", "2026-10-17 09:00:00", "台账独有文案", "#b",
             "https://www.tiktok.com/@example_account_051/video/2222222222222222222（ID: 2222222222222222222）",
             None, "128", "3", "0", "0", "0", "", ""],
        ])
        config.OUTPUT_DIR, config.LEDGER_DIR = out, ledger
        config.ledger_path = lambda: vp
        config.image_ledger_path = lambda: None
        benchmark._LEDGER_CACHE.update(key=None, data={})
        d = benchmark.build(refresh=True)
        assert_(d["kpis"]["videos"] == 1, f"KPI 应只算抓取 1 条，实得 {d['kpis']['videos']}")
        assert_(d["kpis"]["ledger_rows"] == 2, f"台账行数应为 2，实得 {d['kpis']['ledger_rows']}")
        assert_(d["kpis"]["ledger_only"] == 1, f"补录应为 1，实得 {d['kpis']['ledger_only']}")
        assert_(len(d["videos"]) == 2, f"明细应为 2 条，实得 {len(d['videos'])}")
        extra = [v for v in d["videos"] if v["vid"].startswith("2222")]
        assert_(len(extra) == 1, "缺台账独有行")
        e = extra[0]
        assert_(e["source"] == "ledger", "来源标记不对")
        assert_(e["plays"] == 128 and e["likes"] == 3, f"数值解析错: {e['plays']}/{e['likes']}")
        assert_(e["account"] == "example_account_051", "账号归属错")
        assert_(e["has_audio"] is False and e["has_translation"] is False, "空音频/翻译应为 False")
        same = [v for v in d["videos"] if v["vid"].startswith("1111")]
        assert_(len(same) == 1 and same[0]["source"] == "scrape", "同视频应只保留抓取版")
        assert_(same[0]["plays"] == 5000, "抓取版播放量应优先")
        opts = [o["account"] for o in d["account_options"]]
        assert_("example_account_051" in opts, "账号未进下拉")

    def case_image_book():
        tmp, out, ledger, vp, ip = build_env(
            [["2026-10-19", "@example_account_051", "2026-10-18 10:00:00", "抓取版文案", "#a",
              "https://www.tiktok.com/@example_account_051/video/1111111111111111111", None,
              "5000", "5", "1", "0", "0", "音频", "译文"]],
            image_rows=[["2026-10-17", "@imgacc", "2026-10-16 12:00:00", "图文文案", "#c",
                         "https://www.tiktok.com/@imgacc/video/3333333333333333333", None,
                         "837", "3", "0", "0", "0"]])
        config.OUTPUT_DIR, config.LEDGER_DIR = out, ledger
        config.ledger_path = lambda: vp
        config.image_ledger_path = lambda: ip
        benchmark._LEDGER_CACHE.update(key=None, data={})
        d = benchmark.build(refresh=True)
        img = [v for v in d["videos"] if v["account"] == "imgacc"]
        assert_(len(img) == 1, f"图文台账行未并进来: {len(img)}")
        assert_(img[0]["media_type"] == "image", "media_type 应为 image")
        assert_(img[0]["plays"] == 837, "播放量解析错")
        assert_(img[0].get("has_audio") is False, "12 列簿无音频列，应为 False")
        assert_("imgacc" in [o["account"] for o in d["account_options"]], "图文账号未进下拉")

    def case_cross_book_dedupe():
        tmp, out, ledger, vp, ip = build_env(
            [["2026-10-19", "@example_account_051", "2026-10-18 10:00:00", "视频簿", "#a",
              "https://www.tiktok.com/@example_account_051/video/1111111111111111111", None,
              "5000", "5", "1", "0", "0", "有", "有"]],
            image_rows=[["2026-10-17", "@example_account_051", "2026-10-18 10:00:00", "图文簿同一条", "#a",
                         "https://www.tiktok.com/@example_account_051/video/1111111111111111111", None,
                         "5000", "5", "1", "0", "0"]])
        config.OUTPUT_DIR, config.LEDGER_DIR = out, ledger
        config.ledger_path = lambda: vp
        config.image_ledger_path = lambda: ip
        benchmark._LEDGER_CACHE.update(key=None, data={})
        d = benchmark.build(refresh=True)
        assert_(d["kpis"]["ledger_rows"] == 1, f"跨簿同一条应去重为 1，实得 {d['kpis']['ledger_rows']}")

    print("台账并入视频明细：")
    for name, fn in [("抓取 + 台账并集 / KPI 口径不变", case_merge),
                     ("图文 12 列簿并入", case_image_book),
                     ("跨工作簿同一条去重", case_cross_book_dedupe)]:
        check(name, lambda f=fn: run_case(f))
    print(f"\n通过 {_ok} / {_ok + _fail}")
    return 0 if _fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
