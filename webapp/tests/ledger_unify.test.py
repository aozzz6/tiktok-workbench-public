# -*- coding: utf-8 -*-
"""回归测试：build_ledger_15col v2 统一收口（2026-10-10 第 2 批优化）。

关键保障：
  1) 账号清单 accounts.json 驱动 —— active 过滤 + ledger_skip 排除
  2) F 列去重按 video_id —— 三种历史格式（裸 url / {url}（ID: vid）/ 纯数字）互认
  3) 新行 F 列写规范格式 {url}（ID: {vid}）
  4) lang 从 accounts.json 读取
  5) 幂等 —— 重跑不产生重复行；M 列补写不覆盖已有值
  6) 新 sheet 从模板复制 + 冻结窗格
运行：python tests/ledger_unify.test.py（全部使用临时目录 fixture，不碰真实台账）
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

PIPE = Path(__file__).resolve().parent.parent.parent / "pipeline"
sys.path.insert(0, str(PIPE / "scripts"))
sys.path.insert(0, str(PIPE))

import build_ledger_15col as bl  # noqa: E402
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


def make_fixture(tmp: Path, accounts: list[dict], with_template=True):
    """构造临时 accounts.json + 台账 + scrape/转写产物，返回 (cfg, ledger_path)。"""
    out = tmp / "output"
    out.mkdir(parents=True, exist_ok=True)
    for a in accounts:
        acc = a["account"]
        scrape = {"items": [
            {"id": "1111111111111111111", "url": f"https://www.tiktok.com/@{acc}/video/1111111111111111111",
             "desc": "first video", "publish_time_gmt8": "2026-10-09 10:00:00",
             "stats": {"playCount": 100, "diggCount": 10, "commentCount": 1, "shareCount": 0, "collectCount": 2}},
            {"id": "2222222222222222222", "url": f"https://www.tiktok.com/@{acc}/video/2222222222222222222",
             "desc": "second video", "publish_time_gmt8": "2026-10-10 09:00:00",
             "stats": {"playCount": 200, "diggCount": 20, "commentCount": 2, "shareCount": 1, "collectCount": 3}},
        ]}
        (out / f"scrape_{acc}.json").write_text(json.dumps(scrape), encoding="utf-8")
        tdir = out / f"{acc}_audio" / "transcripts"
        tdir.mkdir(parents=True, exist_ok=True)
        (tdir / f"{acc}_1111111111111111111_transcript.txt").write_text(
            "[00:00:00,000 -> 00:00:02,000] hello world this is a spoken line one\n"
            "[00:00:02,000 -> 00:00:04,000] and this is the second spoken line here",
            encoding="utf-8")
        # 222 无转写文件（未标注）
    ledger = tmp / "ledger.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "说明"
    ws["A1"] = "do not touch"
    if with_template:
        # 真实台账里 gylwwcxb 模板 = 表头 + 数据行（带样式/冻结）；
        # fixture 简化为表头 + 1 行占位数据，验证复制后能清干净
        t = wb.create_sheet("对标记录-gylwwcxb")
        t.append(bl.HEADER)
        t.append(["2026-01-01", "@gylwwcxb"] + ["x"] * 13)
        t.freeze_panes = "C2"
    wb.save(ledger)
    wb.close()
    cfg = {"xlsx": str(ledger), "accounts": accounts}
    return cfg, ledger


CFG_V2 = [
    {"account": "acc_a", "lang": "en", "out_dir": "", "audio_dir": "", "active": True},
    {"account": "acc_b", "lang": "es", "out_dir": "", "audio_dir": "", "active": True},
    {"account": "acc_off", "lang": "en", "out_dir": "", "audio_dir": "", "active": False},
    {"account": "gylwwcxb", "lang": "en", "out_dir": "", "audio_dir": "", "active": True,
     "ledger_skip": True},
]


def test_scope():
    names = [a["account"] for a in bl.ledger_accounts({"accounts": CFG_V2})]
    assert_(names == ["acc_a", "acc_b"], f"应为 [acc_a, acc_b]，实得 {names}")


def test_vid_of():
    assert_(bl.vid_of("https://www.tiktok.com/@x/video/7689821342610361630") == "7689821342610361630")
    assert_(bl.vid_of("https://www.tiktok.com/@x/video/7689821342610361630（ID: 7689821342610361630）")
            == "7689821342610361630")
    assert_(bl.vid_of("7689821342610361630") == "7689821342610361630")
    assert_(bl.vid_of(None) is None)
    assert_(bl.vid_of("") is None)


def test_lang():
    cfg = {"accounts": CFG_V2}
    assert_(bl.account_lang("acc_a", cfg) == "en")
    assert_(bl.account_lang("acc_b", cfg) == "es")
    assert_(bl.account_lang("ghost", cfg) == "en")


def test_write_and_format():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        # out_dir 指向 fixture 的 output
        accs = [dict(a, out_dir=str(tmp / "output")) for a in CFG_V2]
        cfg, ledger = make_fixture(tmp, accs)
        rc = bl.main_write(cfg=cfg)
        assert_(rc == 0, f"main_write 应 rc=0，实得 {rc}")
        wb = openpyxl.load_workbook(ledger)
        ws = wb["对标记录-acc_a"]
        # 新 sheet 应含表头 + 2 数据行
        assert_(ws.max_row == 3, f"应有 3 行（表头+2），实得 {ws.max_row}")
        assert_(ws.max_column >= bl.COL_COUNT, f"应至少 {bl.COL_COUNT} 列")
        # 新行 F 列 = 规范格式（含 ID 后缀）
        f2 = str(ws.cell(2, 6).value)
        assert_("（ID: " in f2, f"F 列应为规范格式，实得 {f2}")
        assert_(bl.vid_of(f2) == "1111111111111111111" or bl.vid_of(f2) == "2222222222222222222")
        # M 列：111 有转写（2 句），222 无
        vals = {bl.vid_of(ws.cell(r, 6).value): r for r in (2, 3)}
        m1 = str(ws.cell(vals["1111111111111111111"], 13).value or "")
        assert_("hello world" in m1, "M 列应含转写全文")
        assert_(ws.cell(1, bl.COL_COUNT).value == "翻译", "N 列表头应为翻译")
        # stats
        assert_(ws.cell(vals["1111111111111111111"], 8).value == 100, "H 列播放量")
        # freeze
        assert_(ws.freeze_panes == "C2", f"应冻结 C2，实得 {ws.freeze_panes}")
        # 模板 sheet 未被破坏
        assert_(wb["对标记录-gylwwcxb"].max_row >= 1)
        assert_(wb["说明"]["A1"].value == "do not touch")


def test_dedupe_mixed_formats():
    """关键回归：sheet 里已有裸 url 旧行，新抓取的 url 带 ID 后缀 → 不应产生重复行。"""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        accs = [dict(a, out_dir=str(tmp / "output")) for a in CFG_V2]
        cfg, ledger = make_fixture(tmp, accs)
        # 预置旧格式行（裸 url，v1 风格）
        wb = openpyxl.load_workbook(ledger)
        ws = wb.create_sheet("对标记录-acc_a")
        ws.append(bl.HEADER)
        old = ["2026-10-09", "@acc_a", "2026-10-09 10:00:00", "first", "first",
               "https://www.tiktok.com/@acc_a/video/1111111111111111111", "",
               90, 9, 0, 0, 1, "old_transcript", None, "旧格式行"]
        ws.append(old)
        wb.save(ledger)
        rc = bl.main_write(cfg=cfg)
        assert_(rc == 0)
        wb2 = openpyxl.load_workbook(ledger)
        ws2 = wb2["对标记录-acc_a"]
        vids = [bl.vid_of(ws2.cell(r, 6).value) for r in range(2, ws2.max_row + 1)]
        vids = [v for v in vids if v]
        assert_(len(vids) == len(set(vids)), f"不应有重复 video_id: {vids}")
        assert_(set(vids) == {"1111111111111111111", "2222222222222222222"}, f"应恰 2 条: {vids}")
        # 旧行 M 不被覆盖（保留 old_transcript）
        assert_(str(ws2.cell(2, 13).value) == "old_transcript", "已有 M 不应被补写覆盖")


def test_idempotent_rerun():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        accs = [dict(a, out_dir=str(tmp / "output")) for a in CFG_V2]
        cfg, ledger = make_fixture(tmp, accs)
        assert_(bl.main_write(cfg=cfg) == 0)
        assert_(bl.main_write(cfg=cfg) == 0)
        wb = openpyxl.load_workbook(ledger)
        ws = wb["对标记录-acc_a"]
        assert_(ws.max_row == 3, f"重跑后仍应 3 行，实得 {ws.max_row}")


def test_backup_created():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        accs = [dict(a, out_dir=str(tmp / "output")) for a in CFG_V2]
        cfg, ledger = make_fixture(tmp, accs)
        bl.main_write(cfg=cfg)
        baks = list(tmp.glob("ledger.xlsx.bak_*"))
        assert_(len(baks) == 1, f"应产生 1 个备份，实得 {len(baks)}")


def test_scope_filter():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        accs = [dict(a, out_dir=str(tmp / "output")) for a in CFG_V2]
        cfg, ledger = make_fixture(tmp, accs)
        assert_(bl.main_write(scope=["acc_b"], cfg=cfg) == 0)
        wb = openpyxl.load_workbook(ledger)
        assert_("对标记录-acc_b" in wb.sheetnames, "scope 内账号应有 sheet")
        assert_("对标记录-acc_a" not in wb.sheetnames, "scope 外账号不应建 sheet")


def test_unknown_scope():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        accs = [dict(a, out_dir=str(tmp / "output")) for a in CFG_V2]
        cfg, ledger = make_fixture(tmp, accs)
        rc = bl.main_write(scope=["no_such"], cfg=cfg)
        assert_(rc == 1, "未知账号应 rc=1")


def test_export_trans():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        accs = [dict(a, out_dir=str(tmp / "output")) for a in CFG_V2]
        cfg, ledger = make_fixture(tmp, accs)
        bl.main_write(cfg=cfg)
        # 手动填一条 N 列（111 已译，222 的 M 为空）→ 导出应为空
        wb = openpyxl.load_workbook(ledger)
        wb["对标记录-acc_a"].cell(2, 14).value = "已翻译"
        wb.save(ledger)
        import io, contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            bl.main_export_trans(cfg=cfg)
        out_txt = buf.getvalue()
        # 111 有 N → 不导出；acc_a 无其他待译 → pending 不含 acc_a 或为空
        pf = tmp / "output" / "pending_trans.json"
        assert_(pf.exists(), f"应产出 pending_trans.json，stdout={out_txt[:200]}")
        p = json.loads(pf.read_text(encoding="utf-8"))
        assert_("acc_a" not in p or p["acc_a"] == [], f"已译不应再导出: {p}")
        # 把 N 清空 → 应导出 1 条且 lang=en
        wb = openpyxl.load_workbook(ledger)
        wb["对标记录-acc_a"].cell(2, 14).value = None
        wb.save(ledger)
        with contextlib.redirect_stdout(io.StringIO()):
            bl.main_export_trans(cfg=cfg)
        p = json.loads(pf.read_text(encoding="utf-8"))
        assert_(len(p.get("acc_a", [])) == 1, f"应导出 1 条: {p}")
        assert_(p["acc_a"][0]["lang"] == "en")
        assert_(p["acc_a"][0]["vid"] == "1111111111111111111", f"vid 应从 F 列提取: {p['acc_a'][0]}")


def test_real_accounts_scope():
    """真实 accounts.json：参与范围必须等于启用且非 ledger_skip 的账号。"""
    cfg = bl.load_cfg()
    names = [a["account"] for a in bl.ledger_accounts(cfg)]
    expected = [a["account"] for a in cfg.get("accounts", [])
                if a.get("active", True) and not a.get("ledger_skip", False)]
    assert_(names == expected, f"台账账号范围应由 active/ledger_skip 决定，实得 {names}")


if __name__ == "__main__":
    print("== build_ledger_15col v2 统一收口 ==")
    check("账号清单 active+ledger_skip 过滤", test_scope)
    check("vid_of 三格式兼容", test_vid_of)
    check("lang 从配置读取", test_lang)
    check("写入 + 14 列格式 + 冻结", test_write_and_format)
    check("混合格式去重（裸 url vs ID 后缀）", test_dedupe_mixed_formats)
    check("幂等重跑", test_idempotent_rerun)
    check("写前备份", test_backup_created)
    check("scope 过滤", test_scope_filter)
    check("未知账号 rc=1", test_unknown_scope)
    check("export-trans 导出/已译跳过/lang 注入", test_export_trans)
    check("真实 accounts.json 范围", test_real_accounts_scope)
    print(f"结果: {pass_n} 通过 / {fail_n} 失败")
    sys.exit(1 if fail_n else 0)
