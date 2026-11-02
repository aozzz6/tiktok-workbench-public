# -*- coding: utf-8 -*-
"""overview_kpi.test.py — 总览「今日」聚合与新 KPI 数据源（2026-10-24 诊断 P0/P1）

锁定四件事（对应 总览看板诊断_20261024.html）：
  1. 「今日执行」统计窗口是**全部今日记录**，不是 runs 字段的最近 20 条；
  2. repair 子任务与单测桩账号一律排除（旧实现把它们算进今日执行）；
  3. 新鲜度不用 last_success_date（主链路不写），改看产物 mtime；
  4. 抓取通道按 items/(items+errors) 判级 —— 出口被 WAF 拦时必须能被看见。
跑法：webapp/.venv/Scripts/python.exe tests/overview_kpi.test.py
"""
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

WEBAPP = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WEBAPP))

import config  # noqa: E402
import pipeline_ops  # noqa: E402

_ok = 0
_fail = 0


def check(name, fn):
    global _ok, _fail
    try:
        fn()
        _ok += 1
        print("PASS", name)
    except AssertionError as e:
        _fail += 1
        print("FAIL", name, "->", e)
    except Exception as e:                                    # noqa: BLE001
        _fail += 1
        print("ERROR", name, "->", type(e).__name__, e)


def assert_eq(got, want, msg=""):
    assert got == want, f"{msg} 期望 {want!r}，实际 {got!r}"


NOW = time.time()
TODAY = time.strftime("%Y-%m-%d", time.localtime(NOW))
YDAY = time.strftime("%Y-%m-%d", time.localtime(NOW - 86400))

import app  # noqa: E402  （导入即注册路由，无副作用）

_orig_runs = config.RUNS_JSONL


def _write_runs(rows, name="runs"):
    """写一份临时 journal 并让 config 指向它。"""
    d = Path(tempfile.mkdtemp(prefix=f"ovkpi_{name}_"))
    p = d / "runs.jsonl"
    p.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
                 encoding="utf-8")
    config.RUNS_JSONL = p
    app._day_cache["key"] = None
    return p


def _run(account, kind="pipeline", status="done", ts=None, rid="x1", **kw):
    return {"id": rid, "kind": kind, "account": account, "status": status,
            "started_at": ts if ts is not None else NOW - 60,
            "title": f"{kind} · {account}", "exit_code": 0, "duration_s": 12.0, **kw}


# ── 1. 排除 repair 子任务与单测桩账号 ────────────────────────────────
def _filter():
    rows = [
        _run("alpha", "pipeline", "done", rid="a"),
        _run("beta", "daily", "failed", rid="b"),
        _run("alpha", "repair", "done", rid="c"),          # 一键修复内部子任务
        _run("acc1", "pipeline", "done", rid="d"),        # 单测桩账号
        _run("acc3,alpha", "pipeline", "done", rid="e"),   # 混了真账号 → 保留
    ]
    real = app._real_runs(rows)
    assert_eq([r["id"] for r in real], ["a", "b", "e"], "只保留真实写台账的执行")


# ── 2. 统计窗口：超过 20 条也要全算 ─────────────────────────────────
def _window():
    rows = [_run("alpha", "pipeline", "done", rid=f"r{i}", ts=NOW - 60 - i) for i in range(30)]
    rows += [_run("alpha", "pipeline", "failed", rid=f"f{i}", ts=NOW - 90 - i) for i in range(5)]
    rows.append(_run("alpha", "pipeline", "done", rid="old", ts=NOW - 3 * 86400))
    _write_runs(rows)
    d = app._day_stats()
    assert_eq(d["total"], 35, "今日 35 条（不是最近 20 条）")
    assert_eq(d["success"], 30)
    assert_eq(d["failed"], 5)


# ── 3. 今日边界：昨天的记录不算 ─────────────────────────────────────
def _boundary():
    _write_runs([_run("alpha", ts=NOW - 60, rid="t"),
                 _run("alpha", ts=NOW - 86400 * 1.2, rid="y", status="failed")])
    d = app._day_stats()
    assert_eq(d["total"], 1, "只算今天")
    assert_eq(d["failed"], 0)
    assert_eq(d["date"], TODAY)


# ── 4. 覆盖账号：失败/中断不算「跑过」，all 不算账号 ────────────────
def _covered():
    _write_runs([
        _run("alpha", "pipeline", "done", rid="1"),
        _run("beta", "pipeline", "failed", rid="2"),
        _run("gamma", "pipeline", "interrupted", rid="3"),
        _run("delta", "daily", "done", rid="4"),
        _run("epsilon,zeta", "daily", "done", rid="5"),
        _run("all", "daily", "done", rid="6"),
    ])
    d = app._day_stats()
    assert_eq(d["accounts_covered"], 4, "alpha + delta + epsilon + zeta（失败/中断的不算，all 不算）")
    assert_eq(d["stopped"], 1)


# ── 5. 失败清单：失败与中断都列，按时间倒序，最多 6 ─────────────────
def _failed_runs():
    rows = [_run("alpha", "pipeline", "failed", rid=f"f{i}", ts=NOW - 10 * i) for i in range(8)]
    rows.append(_run("beta", "pipeline", "interrupted", rid="i1", ts=NOW - 5, exit_code=None))
    _write_runs(rows)
    d = app._day_stats()
    assert_eq(len(d["failed_runs"]), 6, "最多 6 条")
    assert_eq(d["failed_runs"][0]["id"], "f0", "最新的排最前")
    assert "i1" in [r["id"] for r in d["failed_runs"]], "中断的也要进失败清单"


# ── 6. 7 日序列 ────────────────────────────────────────────────────
def _series():
    _write_runs([_run("alpha", ts=NOW - 60, rid="a"),
                 _run("alpha", ts=NOW - 86400 - 60, rid="b"),
                 _run("alpha", ts=NOW - 86400 - 120, rid="c", status="failed")])
    d = app._day_stats()
    assert_eq(len(d["series"]), 7, "固定 7 天")
    assert_eq(d["series"][-1]["success"], 1, "今天 1 条成功")
    assert_eq(d["series"][-2]["success"], 1, "昨天 1 条成功")
    assert_eq(d["series"][-2]["failed"], 1, "昨天 1 条失败")


# ── 7. 新鲜度：不用 last_success_date，按产物 mtime ────────────────
def _fresh():
    fresh = app._freshness([
        {"account": "A", "enabled": True, "art_mtime": NOW - 3600},
        {"account": "B", "enabled": True, "art_mtime": NOW - 30 * 3600},
        {"account": "C", "enabled": True, "art_mtime": 0},
        {"account": "OFF", "enabled": False, "art_mtime": 0},     # 停用不参与
    ], now=NOW, stale_h=24)
    assert_eq(fresh["stale_count"], 2, "B 超 24h、C 没产物；停用账号不计")
    assert_eq(len(fresh["stale_accounts"]), 2)
    assert fresh["as_of_text"], "要有可读的 as_of 文案"


# ── 8. 抓取通道分级 ────────────────────────────────────────────────
def _health():
    ok_ = app._scrape_health([{"enabled": True, "scrape_items": 19, "scrape_errors": 1}])
    assert_eq(ok_["level"], "ok", "95% 应判 ok")
    warn = app._scrape_health([{"enabled": True, "scrape_items": 8, "scrape_errors": 4}])
    assert_eq(warn["level"], "warn", "67% 应判 warn")
    bad = app._scrape_health([{"enabled": True, "scrape_items": 1, "scrape_errors": 65}])
    assert_eq(bad["level"], "bad", "WAF 拦停（1/66）必须判 bad")
    none = app._scrape_health([{"enabled": True, "scrape_items": 0, "scrape_errors": 0}])
    assert_eq(none["level"], "unknown", "没有采集过 → unknown，不误报")
    off = app._scrape_health([{"enabled": False, "scrape_items": 0, "scrape_errors": 9}])
    assert_eq(off["items"] + off["errors"], 0, "停用账号不计入通道健康度")


# ── 9. 批次摘要：state/partial/round_id 透传给前端 ─────────────────
def _batch():
    summary = {"date": TODAY, "state": "done", "level": "WARN", "total": 45,
               "counts": {"ok": 44, "warn": 0, "failed": 1}, "duration_s": 750.0,
               "started_at": NOW - 900, "ended_at": NOW - 150,
               "round_id": "20261024T080000_000_1", "partial": True,
               "stages": {}, "failed_stages": ["ledger_verify"], "alerts": []}
    orig = pipeline_ops.latest_summary
    pipeline_ops.latest_summary = lambda: summary
    try:
        b = pipeline_ops.batch_brief(now=NOW)
    finally:
        pipeline_ops.latest_summary = orig
    assert_eq(b["state"], "done")
    assert_eq(b["round_id"], "20261024T080000_000_1")
    assert_eq(b["counts"]["ok"], 44)
    assert_eq(b["partial"], True)
    assert_eq(b["failed_stages"], ["ledger_verify"])
    assert b["age_s"] is not None, "要有距今时长，前端据此判过期"


def _batch_interrupted():
    """★ 2026-10-30：state 仍是 running，但 pid 已退出 → 卡片必须拿到 interrupted。

    事故：10-29 08:15 的整轮进程 49468 中途死掉，回执停在 running，总览「今日批次」
    一直显示「运行中 106m」——没有任何一次运行能把它翻成 done。
    """
    summary = {"date": "2026-10-29", "state": "running", "level": "WARN", "total": 45,
               "counts": {"ok": 30, "warn": 15, "failed": 0}, "duration_s": 6391.5,
               "started_at": NOW - 90000, "ended_at": None, "pid": 999999,
               "round_id": "20261029T081546_982_49468", "partial": True,
               "stages": {}, "failed_stages": [], "alerts": []}
    orig = pipeline_ops.latest_summary
    pipeline_ops.latest_summary = lambda: summary
    try:
        b = pipeline_ops.batch_brief(now=NOW)
    finally:
        pipeline_ops.latest_summary = orig
    assert_eq(b["state"], "running", "契约状态不改盘，仍是 running")
    assert b["interrupted"] is True, f"进程已退出必须标 interrupted：{b}"
    assert b["ended_at"] is None, "ended_at 仍为 None（不改盘）"


def _batch_running_live():
    summary = {"date": TODAY, "state": "running", "level": "INFO", "total": 3,
               "counts": {"ok": 1, "warn": 0, "failed": 0}, "duration_s": 120.0,
               "started_at": NOW - 120, "ended_at": None, "pid": os.getpid(),
               "round_id": "r_live", "partial": True,
               "stages": {}, "failed_stages": [], "alerts": []}
    orig = pipeline_ops.latest_summary
    pipeline_ops.latest_summary = lambda: summary
    try:
        b = pipeline_ops.batch_brief(now=NOW)
    finally:
        pipeline_ops.latest_summary = orig
    assert b["interrupted"] is False, f"进程还活着不能标中断：{b}"


def _batch_none():
    orig = pipeline_ops.latest_summary
    pipeline_ops.latest_summary = lambda: None
    try:
        b = pipeline_ops.batch_brief(now=NOW)
    finally:
        pipeline_ops.latest_summary = orig
    assert_eq(b["state"], "none", "没有回执 → none（不是 done）")
    assert_eq(b["total"], 0)


# ── 10. 子进程隔离：WB_RUNS_FILE 生效，别写生产 ────────────────────
def _isolate_env():
    tmp = Path(tempfile.mkdtemp(prefix="ovkpi_iso_")) / "runs.jsonl"
    env = dict(os.environ, WB_RUNS_FILE=str(tmp))
    code = "import config;print(config.RUNS_JSONL)"
    out = subprocess.run([config.python_exe(), "-c", code], cwd=str(WEBAPP), env=env,
                         capture_output=True, text=True, encoding="utf-8")
    got = (out.stdout or "").strip().replace("\\", "/")
    assert got.endswith("runs.jsonl"), f"子进程没拿到临时路径：{got} {out.stderr[:200]}"
    assert "wb-test-runs" not in got and str(tmp).replace("\\", "/") in got, got


# ── 11. 待译分布（下钻用）──────────────────────────────────────────
def _pending():
    import app as A
    p = Path(tempfile.mkdtemp(prefix="ovkpi_pend_")) / "p.json"
    p.write_text(json.dumps({"total": 3, "ts": NOW, "items": [
        {"account": "A"}, {"account": "A"}, {"account": "B"}]}), encoding="utf-8")
    orig = A.PENDING_TRANS_FILE
    A.PENDING_TRANS_FILE = p
    try:
        d = A._read_pending_translation()
    finally:
        A.PENDING_TRANS_FILE = orig
    assert_eq(d["total"], 3)
    assert_eq(d["by_account"][0], {"account": "A", "count": 2})
    assert_eq(d["by_account"][1], {"account": "B", "count": 1})


# ── 11b. 待译自愈（2026-10-24 A 方案：台账 mtime 变 → 队列自动重算）──────
def _pending_selfheal():
    """回归「总览常驻 9 条待译」事故：快照必须能自己烂掉、也必须能自己回来。"""
    import app as A
    import openpyxl
    from unittest.mock import patch
    tmp = Path(tempfile.mkdtemp(prefix="ovkpi_heal_"))
    led, q = tmp / "ledger.xlsx", tmp / "p.json"
    hdr = ["记录日期", "对标账号", "视频发布时间", "视频标题", "话题标签", "视频链接/ID",
           "商品链接", "播放量", "点赞数", "评论数", "转发数", "收藏数", "音频", "翻译"]
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "对标记录-accX"
    ws.append(hdr)
    ws.append(["2026-10-24", "@accX", "", "", "", "https://t.tiktok/video/7699071015602752800",
               "", 0, 0, 0, 0, 0, "hello world", None])
    wb.save(led)
    wb.close()
    # 预置过期快照：9 条幽灵待译（线上那次事故的原样）
    q.write_text(json.dumps({"ts": NOW - 4000, "total": 9,
                             "items": [{"account": "ghost"} for _ in range(9)]}),
                 encoding="utf-8")
    orig_file, orig_sig = A.PENDING_TRANS_FILE, dict(A._PENDING_SIG)
    A.PENDING_TRANS_FILE = q
    A._PENDING_SIG["mtime"] = -1.0
    try:
        with patch.object(config, "ledger_path", return_value=str(led)):
            d = A._pending_snapshot()
            assert_eq(d["total"], 1, "台账只有 1 条真待译：幽灵 9 条必须被覆盖")
            assert d["age_s"] is not None and d["age_s"] < 60, f"重算后龄期应接近 0：{d}"
            assert_eq(A._pending_snapshot()["total"], 1, "mtime 未变时结果应一致（走缓存）")

            def _set_n(val):
                b = openpyxl.load_workbook(led)
                b["对标记录-accX"]["N2"] = val
                b.save(led)
                b.close()
                time.sleep(0.05)   # mtime 精度兜底

            _set_n("你好世界")      # 模拟巡检 automation 回填译文（外部写台账）
            assert_eq(A._pending_snapshot()["total"], 0, "已回填 → 必须归零")
            assert not q.exists(), "清零后队列文件应被删除"
            _set_n(None)           # 反向：N 被清空 → 计数必须回升，不能只降不升
            assert_eq(A._pending_snapshot()["total"], 1, "N 清空后应重新计入待译")
    finally:
        A.PENDING_TRANS_FILE = orig_file
        A._PENDING_SIG.update(orig_sig)


# ── 11c. 队列摘要字段（下钻/报警要用的）────────────────────────────
def _pending_fields():
    import app as A
    tmp = Path(tempfile.mkdtemp(prefix="ovkpi_pf_"))
    p = tmp / "p.json"
    p.write_text(json.dumps({"ts": time.time() - 4000, "total": 11,
                             "items": [{"account": c} for c in "ABCDEFGHIJK"]}),
                 encoding="utf-8")
    orig = A.PENDING_TRANS_FILE
    A.PENDING_TRANS_FILE = p
    try:
        d = A._read_pending_translation()
        assert d["ok"] is True, d
        assert 3900 < d["age_s"] < 4200, f"age_s 应≈4000：{d}"
        assert_eq(len(d["by_account"]), 8, "下钻最多列 8 个账号")
        assert d["truncated"] is True and d["by_account_total"] == 11, d
        A.PENDING_TRANS_FILE = tmp / "missing.json"        # 队列文件不存在 = 无待译（正常态）
        assert_eq(A._read_pending_translation(), {"total": 0, "ok": True})
        (tmp / "broken.json").write_text("{not json", encoding="utf-8")
        A.PENDING_TRANS_FILE = tmp / "broken.json"         # 读失败必须能被区分，不能冒充 0
        assert_eq(A._read_pending_translation(), {"total": 0, "ok": False})
    finally:
        A.PENDING_TRANS_FILE = orig


check("repair 子任务与单测桩账号不进「今日执行」", _filter)
check("统计窗口是全部今日记录（35 条，不是最近 20 条）", _window)
check("今日边界：昨天的不算", _boundary)
check("覆盖账号：失败/中断不算跑过，all 不算账号", _covered)
check("失败清单：失败+中断、倒序、最多 6 条", _failed_runs)
check("7 日序列（末位=今天）", _series)
check("新鲜度：按产物 mtime，停用账号不参与", _fresh)
check("抓取通道：95% ok / 67% warn / WAF 拦停 bad / 未采集 unknown", _health)
check("批次摘要：state/round_id/counts/partial 透传", _batch)
check("批次摘要：running+进程已退 → interrupted（不再显示运行中）", _batch_interrupted)
check("批次摘要：running+进程活着 → 不算中断", _batch_running_live)
check("批次摘要：无回执 → none（不是 done）", _batch_none)
check("WB_RUNS_FILE 子进程隔离生效（不写生产 journal）", _isolate_env)
check("待译自愈：台账 mtime 变 → 幽灵快照被覆盖（回填归零、清空回升）", _pending_selfheal)
check("待译摘要：age_s / truncated / 缺文件=0 / 损坏=ok:false", _pending_fields)
check("待译分布 by_account（下钻用）", _pending)

print()
print(f"结果: {_ok} 通过 / {_fail} 失败")
config.RUNS_JSONL = _orig_runs
sys.exit(1 if _fail else 0)
