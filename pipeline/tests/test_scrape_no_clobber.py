# -*- coding: utf-8 -*-
r"""test_scrape_no_clobber.py — s2 抓取：全失败不覆盖历史产物（crew T-03）

覆盖 docs/tasks/tasks.md 里 T-03 的 DoD 1~5：
  1. 本次 items 为空且 errors 非空 → 目标文件字节级不变（改前 read_bytes() 比对）
  2. 这次运行的退出码仍为 0（失败由 gate 判，不由本脚本判）
  3. stdout 末行 JSON 照常输出本次结果（items []、errors 条数与本次一致）；
     stderr 摘要行出现「未写盘」字样
  4. 不回退正常路径：items 非空时照常写盘、文件内容被更新
  5. --merge 模式不受影响：预置 2 条历史 items + 本次 0 成功
     → 合并后 items 仍是 2 条，文件正常写盘（历史保住，而不是拒绝写盘）

事故背景（handoff_s2_scrape_20261102.md 第 3 节）：2026-11-02 全量抓取
example_account_022 时 49 条全失败（0 items + 49 errors），整体覆盖把上一次的
历史条目冲掉——抓得越失败毁得越多。

不联网：fetch_page / parse_item_detail 全部 mock；
browser_batch_fallback 一律 patch 成 {}（绝不真起 playwright 子进程）。

运行：D:\workbuddy_project\tiktok\pipeline\.venv\Scripts\python.exe pipeline\tests\test_scrape_no_clobber.py
"""
from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

PIPE = Path(__file__).resolve().parent.parent
SCRIPTS = PIPE / "scripts"
sys.path.insert(0, str(SCRIPTS))
import scrape_tiktok_video as sc  # noqa: E402

_ok = 0
_fail = 0


def check(name, fn):
    global _ok, _fail
    try:
        fn()
        print(f"  ok {name}")
        _ok += 1
    except AssertionError as e:
        print(f"  FAIL {name} -> {e}")
        _fail += 1
    except Exception as e:
        print(f"  FAIL {name} -> {e.__class__.__name__}: {e}")
        _fail += 1


def assert_(c, m="assert failed"):
    if not c:
        raise AssertionError(m)


# ---------- 固定数据（不联网） ----------

ACC = "probe"
VID_A = "7696068031566318881"
VID_B = "7696080593674603809"
HIST_A = "7691000000000000001"
HIST_B = "7691000000000000002"

# 本次抓取用的 item（成功路径）
ITEM_A = {
    "desc": "本次抓到的文案 #tail",
    "createTime": 1761900000,
    "stats": {"playCount": 9, "diggCount": 1, "commentCount": 2,
              "shareCount": 3, "collectCount": 4},
    "challenges": [{"title": "tail"}],
    "music": {"title": "bgm"},
    "author": {"uniqueId": ACC},
}


def _fail_detail(html):
    """本次全部失败：10204 作者审核中（归类仍 removed，不重试、不触兜底）。"""
    return None, "removed", 10204, "author_status,status_reviewing"


def _ok_detail(html):
    return dict(ITEM_A), "", 0, ""


# ---------- 小工具 ----------

def _tmp_out(prefix: str) -> Path:
    return Path(tempfile.mkdtemp(prefix=prefix))


def _history_file(out: Path) -> Path:
    """放一份含 2 条历史 items 的 scrape_probe.json（模拟上一次全量抓取的产物）。"""
    p = out / f"scrape_{ACC}.json"
    rows = [
        {"id": HIST_A, "url": f"https://www.tiktok.com/@{ACC}/video/{HIST_A}",
         "desc": "历史条目一 #tag", "hashtags": ["tag"],
         "publish_time_gmt8": "2026-10-30 09:00:00",
         "stats": {"playCount": 100}, "music": "bgm", "author": ACC},
        {"id": HIST_B, "url": f"https://www.tiktok.com/@{ACC}/video/{HIST_B}",
         "desc": "历史条目二 #tag", "hashtags": ["tag"],
         "publish_time_gmt8": "2026-10-31 10:00:00",
         "stats": {"playCount": 200}, "music": "bgm", "author": ACC},
    ]
    p.write_text(json.dumps({
        "account": ACC, "policy": "strict", "scraped_at": "2026-11-01T09:00:00",
        "reasons": {"blocked": 0, "removed": 0}, "interval_s": 5.0,
        "errors": [], "items": rows,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def _audio_dir(out: Path, vids: list[str]) -> Path:
    """建一个假音频目录：collect_ids() 从文件名取视频 ID（全量抓取的真实入口）。"""
    d = out / f"{ACC}_audio"
    d.mkdir(parents=True, exist_ok=True)
    for v in vids:
        (d / f"{ACC}_{v}.mp3").write_bytes(b"")
    return d


def _run_main(argv_extra, detail_fn=_fail_detail):
    """跑一次 sc.main()；返回 (rc, stdout, stderr)。

    fetch_page（返回值被 parse_item_detail 吃掉）与 browser_batch_fallback
    都被 patch，测试永不联网、永不起 playwright 子进程。
    """
    out_buf = io.StringIO()
    err_buf = io.StringIO()
    with patch.object(sys, "argv", ["scrape_tiktok_video.py", *argv_extra]), \
            patch.object(sc, "fetch_page", return_value="<html></html>"), \
            patch.object(sc, "parse_item_detail", side_effect=detail_fn), \
            patch.object(sc, "browser_batch_fallback", return_value={}), \
            patch.object(sc.time, "sleep", side_effect=lambda s: None), \
            contextlib.redirect_stdout(out_buf), \
            contextlib.redirect_stderr(err_buf):
        rc = sc.main()
    return rc, out_buf.getvalue(), err_buf.getvalue()


def _summary_of(stdout: str) -> dict:
    """取 stdout 末行 JSON 摘要（gate 用的那份）。"""
    for line in reversed(stdout.strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
    raise AssertionError(f"stdout 里找不到摘要 JSON，尾部：{stdout[-300:]!r}")


def _full_run_argv(out: Path, vids: list[str]) -> list[str]:
    """全量抓取形态（真实事故的入口）：不带 --ids、不带 --merge。"""
    return ["--account", ACC, "--out-dir", str(out),
            "--audio-dir", str(_audio_dir(out, vids)),
            "--interval", "0", "--blocked-retry", "0"]


# ---------- DoD 1：全失败时文件字节级不变 ----------

def case_dod1_full_failure_keeps_bytes():
    out = _tmp_out("noclog1_")
    path = _history_file(out)
    before = path.read_bytes()
    rc, out_s, err_s = _run_main(_full_run_argv(out, [VID_A, VID_B]))
    assert_(rc == 0, f"退出码应为 0，实得 {rc}；stderr={err_s[-300:]}")
    after = path.read_bytes()
    assert_(after == before,
            f"本次 0 条有效时不得改动文件：改前 {len(before)} 字节，"
            f"改后 {len(after)} 字节")
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert_(len(saved.get("items") or []) == 2,
            f"历史 2 条 items 必须还在，实得 {len(saved.get('items') or [])} 条")


# ---------- DoD 2：退出码仍为 0 ----------

def case_dod2_exit_code_zero():
    out = _tmp_out("noclog2_")
    _history_file(out)
    rc, out_s, err_s = _run_main(_full_run_argv(out, [VID_A, VID_B]))
    assert_(rc == 0,
            f"全失败时脚本退出码仍须为 0（失败由 gate 判），实得 {rc}；"
            f"stderr={err_s[-300:]}")


# ---------- DoD 3：摘要照常输出本次结果 + 标注未写盘 ----------

def case_dod3_summary_marks_not_written():
    out = _tmp_out("noclog3_")
    _history_file(out)
    rc, out_s, err_s = _run_main(_full_run_argv(out, [VID_A, VID_B]))
    assert_(rc == 0, f"退出码应为 0，实得 {rc}；stderr={err_s[-300:]}")
    s = _summary_of(out_s)
    assert_(s["items"] == [], f"末行 JSON 的 items 应为本次结果 []，实得 {s['items']}")
    assert_(len(s["errors"]) == 2,
            f"末行 JSON 的 errors 条数应与本次一致（2），实得 {len(s['errors'])}")
    assert_({e["id"] for e in s["errors"]} == {VID_A, VID_B},
            f"errors 应是本次两个 ID，实得 {[e['id'] for e in s['errors']]}")
    assert_("未写盘" in err_s,
            f"stderr 摘要行应标注「未写盘」，实得 stderr={err_s[-300:]!r}")
    assert_("[saved]" not in err_s,
            f"未写盘时不该出现 [saved]（会让人以为文件已更新）：{err_s[-300:]!r}")


# ---------- DoD 4：有 items 时照常写盘 ----------

def case_dod4_success_still_writes():
    out = _tmp_out("noclog4_")
    path = _history_file(out)
    before = path.read_bytes()
    rc, out_s, err_s = _run_main(_full_run_argv(out, [VID_A, VID_B]), detail_fn=_ok_detail)
    assert_(rc == 0, f"退出码应为 0，实得 {rc}；stderr={err_s[-300:]}")
    after = path.read_bytes()
    assert_(after != before, "items 非空时必须照常写盘（不能把写盘整个关掉）")
    saved = json.loads(path.read_text(encoding="utf-8"))
    got = [str(i["id"]) for i in saved.get("items") or []]
    assert_(got == [VID_A, VID_B], f"文件里应是本次抓到的 2 条，实得 {got}")
    assert_("[saved]" in err_s, f"正常写盘应打印 [saved]，实得 {err_s[-300:]!r}")


# ---------- DoD 5：--merge 模式历史保住且正常写盘 ----------

def case_dod5_merge_keeps_history():
    out = _tmp_out("noclog5_")
    path = _history_file(out)
    before = path.read_bytes()
    rc, _out_s, err_s = _run_main(
        ["--account", ACC, "--out-dir", str(out), "--ids", VID_A,
         "--merge", "--interval", "0", "--blocked-retry", "0"])
    assert_(rc == 0, f"退出码应为 0，实得 {rc}；stderr={err_s[-300:]}")
    # 注意：--merge 的 stdout 摘要本来就是「合并后」的视图（既有行为，本任务不动），
    # 所以这里用 stderr 的 [merge] 行证明「本次 0 成功」，而不是要求摘要 items==[]。
    assert_("本次抓 0 条" in err_s,
            f"stderr 的 [merge] 行应显示本次抓 0 条，实得 {err_s[-300:]!r}")
    saved = json.loads(path.read_text(encoding="utf-8"))
    got = sorted(str(i["id"]) for i in saved.get("items") or [])
    assert_(got == sorted([HIST_A, HIST_B]),
            f"--merge 后历史 2 条必须还在（而不是拒绝写盘），实得 {got}")
    err_ids = [str(e["id"]) for e in saved.get("errors") or []]
    assert_(VID_A in err_ids, f"--merge 应记下本次失败的 {VID_A}，实得 {err_ids}")
    assert_(path.read_bytes() != before,
            "--merge 是正常写盘路径，文件应被更新（历史+本次错误）")
    assert_("未写盘" not in err_s,
            f"--merge 不该走「未写盘」分支：{err_s[-300:]!r}")


def main() -> int:
    print("s2 抓取：全失败不覆盖历史产物：")
    for name, fn in [
        ("DoD1 全失败时目标文件字节级不变", case_dod1_full_failure_keeps_bytes),
        ("DoD2 全失败时退出码仍为 0", case_dod2_exit_code_zero),
        ("DoD3 摘要照常输出本次结果并标注未写盘", case_dod3_summary_marks_not_written),
        ("DoD4 有 items 时照常写盘", case_dod4_success_still_writes),
        ("DoD5 --merge 合并后历史 2 条仍在且正常写盘", case_dod5_merge_keeps_history),
    ]:
        check(name, fn)
    print(f"\n通过 {_ok} / {_ok + _fail}")
    return 0 if _fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
