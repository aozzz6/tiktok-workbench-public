# -*- coding: utf-8 -*-
"""回归测试：post_update.py（第 3 批优化，2026-10-10）。

关键保障：
  1) check_ledger：行数对账（rows < scrape_items → error）、N 列空缺识别、
     ~$ 锁文件拦截、MISSING sheet 识别、停用账号跳过
  2) main_verify rc 语义：全对齐 → 0；有 ERROR → 1
  3) CLI 委托：write/export-trans/apply-trans 透传 build_ledger_15col
运行：python tests/post_update.test.py（临时 fixture，不碰真实台账）
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PIPE = Path(__file__).resolve().parent.parent.parent / "pipeline"
sys.path.insert(0, str(PIPE / "scripts"))
sys.path.insert(0, str(PIPE))

import post_update as pu  # noqa: E402
import openpyxl  # noqa: E402

pass_n = 0
fail_n = 0


def check(name, fn):
    global pass_n, fail_n
    try:
        fn()
        print("  \u2713 " + name)
        pass_n += 1
    except Exception as e:  # noqa: BLE001
        print("  \u2717 " + name + " -> " + repr(e))
        fail_n += 1


def assert_(cond, msg=""):
    if not cond:
        raise AssertionError(msg or "assertion failed")


HEADER = ["记录日期", "对标账号", "视频发布时间", "视频标题", "话题标签",
          "视频链接/ID", "商品链接", "播放量", "点赞数", "评论数",
          "转发数", "收藏数", "音频", "翻译"]


def make_fixture(tmp: Path, accounts: list[dict], sheets: dict):
    """sheets: {acc: [(M, N), ...]}；scrape items 数 = 行数（除非 items_override）"""
    out = tmp / "output"
    out.mkdir(parents=True, exist_ok=True)
    accounts = [dict(a, out_dir=str(out)) for a in accounts]
    ledger = tmp / "ledger.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "说明"
    for acc, rows in sheets.items():
        s = wb.create_sheet(f"对标记录-{acc}")
        s.append(HEADER)
        for idx, (m, n) in enumerate(rows):
            s.append(["2026-10-10", f"@{acc}", "", "d", "d",
                      f"https://www.tiktok.com/@{acc}/video/{idx:019d}",
                      "", 1, 1, 1, 1, 1, m, n])
        # scrape 产物：items 数 = 数据行数
        items = [{"id": f"{i:019d}", "url": "u"} for i in range(len(rows))]
        (out / f"scrape_{acc}.json").write_text(json.dumps({"items": items}), encoding="utf-8")
    wb.save(ledger)
    wb.close()
    cfg = {"xlsx": str(ledger), "accounts": accounts}
    return cfg


ACC_A = {"account": "acc_a", "lang": "en", "out_dir": "", "audio_dir": "", "active": True}
ACC_B = {"account": "acc_b", "lang": "en", "out_dir": "", "audio_dir": "", "active": True}
ACC_OFF = {"account": "acc_off", "lang": "en", "out_dir": "", "audio_dir": "", "active": False}


def test_all_aligned():
    with tempfile.TemporaryDirectory() as td:
        cfg = make_fixture(Path(td), [ACC_A, ACC_OFF], {
            "acc_a": [("text1", "译1"), ("text2", "译2")]})
        r = pu.check_ledger(cfg)
        assert_(r["status"] == "ok", f"应对齐: {r}")
        d = r["accounts"][0]
        assert_(d["rows"] == 2 and d["scrape_items"] == 2)
        assert_(d["n_empty_count"] == 0)
        assert_(all(x["account"] != "acc_off" for x in r["accounts"]), "停用账号应跳过")


def test_rows_short():
    with tempfile.TemporaryDirectory() as td:
        cfg = make_fixture(Path(td), [ACC_A], {"acc_a": [("t", "n")]})
        # 手动把 scrape 加到 3 条（台账只有 1 行）
        out = Path(td) / "output"
        items = [{"id": f"{i:019d}"} for i in range(3)]
        (out / "scrape_acc_a.json").write_text(json.dumps({"items": items}), encoding="utf-8")
        r = pu.check_ledger(cfg)
        assert_(r["status"] == "error", "行数不足应 error")
        assert_(r["bad"] == ["acc_a"])
        assert_(r["accounts"][0]["rows_short"] is True)


def test_n_empty():
    with tempfile.TemporaryDirectory() as td:
        cfg = make_fixture(Path(td), [ACC_A], {"acc_a": [("有文无译", ""), ("全空", "x")]})
        r = pu.check_ledger(cfg)
        d = r["accounts"][0]
        assert_(d["n_empty_count"] == 1, f"仅 1 条待译: {d}")
        assert_(d["n_empty_rows"] == [2], f"行号应为 2: {d}")


def test_lock_file():
    with tempfile.TemporaryDirectory() as td:
        cfg = make_fixture(Path(td), [ACC_A], {"acc_a": [("t", "n")]})
        (Path(td) / "~$ledger.xlsx").write_text("lock", encoding="utf-8")
        r = pu.check_ledger(cfg)
        assert_(r["status"] == "error" and "占用" in r["error"], f"锁文件应拦截: {r}")


def test_missing_sheet():
    with tempfile.TemporaryDirectory() as td:
        cfg = make_fixture(Path(td), [ACC_A, ACC_B], {"acc_a": [("t", "n")]})
        r = pu.check_ledger(cfg)
        assert_(r["status"] == "error")
        assert_("acc_b" in r["bad"], f"缺 sheet 应 error: {r}")


def test_verify_rc():
    with tempfile.TemporaryDirectory() as td:
        cfg = make_fixture(Path(td), [ACC_A], {"acc_a": [("t", "n")]})
        # 注入 cfg：monkeypatch load_cfg
        orig = pu.load_cfg
        pu.load_cfg = lambda: cfg
        try:
            assert_(pu.main_verify(skip_api=True) == 0, "全对齐应 rc=0")
            # 弄坏：删掉 sheet
            wb = openpyxl.load_workbook(cfg["xlsx"])
            del wb["对标记录-acc_a"]
            wb.save(cfg["xlsx"])
            wb.close()
            assert_(pu.main_verify(skip_api=True) == 1, "缺 sheet 应 rc=1")
        finally:
            pu.load_cfg = orig


def test_cli_delegation():
    """真实CLI委托链仅使用临时pipeline、配置、台账和解释器路径。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        cfg = make_fixture(root, [ACC_A], {"acc_a": [("t", "n")]})
        from desc_split import HEADER_13
        wb = openpyxl.load_workbook(cfg["xlsx"])
        ws = wb["对标记录-acc_a"]
        ws.delete_cols(len(HEADER_13) + 1)
        for col, value in enumerate(HEADER_13, 1):
            ws.cell(1, col).value = value
        wb.save(cfg["xlsx"])
        wb.close()
        (root / "accounts.json").write_text(json.dumps(cfg), encoding="utf-8")
        scripts = root / "scripts"
        scripts.mkdir()
        for name in ("post_update.py", "build_ledger_15col.py", "desc_split.py"):
            shutil.copy2(PIPE / "scripts" / name, scripts / name)
        # run_builder 使用当前测试解释器，避免临时目录依赖真实pipeline路径。
        wrapper = ("import runpy,sys; sys.path.insert(0,sys.argv[1]); "
                   "import post_update; post_update.PY=sys.executable; "
                   "sys.argv=['post_update.py','write']; sys.exit(post_update.main())")
        r = subprocess.run([sys.executable, "-c", wrapper, str(scripts)],
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=30)
        out = r.stdout + r.stderr
        assert_(r.returncode == 0, f"write 应 rc=0: {out[-300:]}")
        assert_("终验" in out, f"应含终验输出: {out[-300:]}")


if __name__ == "__main__":
    print("== post_update.py ==")
    check("全对齐 + 停用跳过", test_all_aligned)
    check("行数不足 → error", test_rows_short)
    check("N 列空缺识别", test_n_empty)
    check("~$ 锁文件拦截", test_lock_file)
    check("缺 sheet 识别", test_missing_sheet)
    check("verify rc 语义", test_verify_rc)
    check("CLI 委托 write（隔离临时台账）", test_cli_delegation)
    print(f"结果: {pass_n} 通过 / {fail_n} 失败")
    sys.exit(1 if fail_n else 0)
