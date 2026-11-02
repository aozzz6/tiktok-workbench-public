# -*- coding: utf-8 -*-
r"""test_scrape_failure_reason.py — s2 抓取：失败原因说真话 + 100001 归可重试（crew T-02）

覆盖 docs/tasks/tasks.md 里 T-02 的 DoD 1~5：
  1. statusCode 10204 / statusMsg author_status,status_reviewing → errors[0].error 同时含两者（归类仍 removed）
  2. statusCode 100001（RPC 网络错误，瞬断）→ reason blocked，且 --blocked-retry 2 时抓取函数被调用 3 次
  3. 既有语义不回退：无 __UNIVERSAL_DATA_FOR_REHYDRATION__ → blocked；有 itemStruct → 成功
  4. parse_item_ex() 仍是 (item, reason) 二元组：10204 → (None, "removed")
  5. --blocked-retry / --blocked-sleep 默认值仍来自 WB_SCRAPE_BLOCKED_RETRY=1 / WB_SCRAPE_BLOCKED_SLEEP=8

不联网：HTML 样例写死在本文件，fetch_page 全部 mock；
browser_batch_fallback 一律 patch 成 {}，绝不真的起 playwright 子进程。

运行：D:\workbuddy_project\tiktok\pipeline\.venv\Scripts\python.exe pipeline\tests\test_scrape_failure_reason.py
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
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


# ---------- 固定 HTML 样例（写死，不联网） ----------

VID_10204 = "7696068031566318881"
VID_100001 = "7696777637192027425"
VID_OK = "7701872052117982495"


def _universal_html(detail: dict) -> str:
    payload = {"__DEFAULT_SCOPE__": {"webapp.video-detail": detail}}
    return (
        '<html><head></head><body><div id="app"></div>'
        '<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__" '
        'type="application/json">'
        + json.dumps(payload, ensure_ascii=False)
        + "</script></body></html>"
    )


# 10204：账号被判「作者审核中」，有内嵌数据但 itemInfo.itemStruct 为空
SAMPLE_10204 = _universal_html({
    "statusCode": 10204,
    "statusMsg": "author_status,status_reviewing",
    "itemInfo": {},
})

# 100001：RPC 网络错误（瞬断），可重试
SAMPLE_100001 = _universal_html({
    "statusCode": 100001,
    "statusMsg": "RPC 网络错误（瞬断）",
    "itemInfo": {},
})

# 风控页/挑战页：没有 __UNIVERSAL_DATA_FOR_REHYDRATION__
SAMPLE_BLOCKED = "<html><body><h1>verify you are human</h1></body></html>"

# 正常视频详情页：完整 itemStruct
SAMPLE_OK = _universal_html({
    "statusCode": 0,
    "statusMsg": "",
    "itemInfo": {
        "itemStruct": {
            "id": VID_OK,
            "desc": "样例视频文案 #tail",
            "createTime": 1761900000,
            "stats": {"playCount": 123, "diggCount": 4, "commentCount": 5,
                      "shareCount": 6, "collectCount": 7},
            "challenges": [{"title": "tail"}],
            "music": {"title": "bgm"},
            "author": {"uniqueId": "probe"},
        },
    },
})


# ---------- 跑 main() 的小工具 ----------

def _run_main(argv_extra, fetch_side_effect):
    """跑一次 sc.main()；返回 (rc, stdout, stderr, fallback_mock)。

    fetch_page 与 browser_batch_fallback 都被 patch，测试永不联网、永不起子进程。
    """
    out_buf = io.StringIO()
    err_buf = io.StringIO()
    with patch.object(sys, "argv", ["scrape_tiktok_video.py", *argv_extra]), \
            patch.object(sc, "fetch_page", side_effect=fetch_side_effect), \
            patch.object(sc, "browser_batch_fallback", return_value={}) as fb, \
            contextlib.redirect_stdout(out_buf), \
            contextlib.redirect_stderr(err_buf):
        rc = sc.main()
    return rc, out_buf.getvalue(), err_buf.getvalue(), fb


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


def _tmp_out(prefix: str) -> Path:
    return Path(tempfile.mkdtemp(prefix=prefix))


# ---------- DoD 1：10204 的错误文本带真实 statusCode + statusMsg ----------

def case_10204_error_has_status():
    out = _tmp_out("scrape_10204_")
    calls = []

    def fetch(url):
        calls.append(url)
        return SAMPLE_10204

    rc, out_s, err_s, fb = _run_main(
        ["--account", "probe", "--out-dir", str(out), "--ids", VID_10204,
         "--interval", "0", "--blocked-retry", "0", "--allow-overwrite"], fetch)
    assert_(rc == 0, f"退出码应为 0，实得 {rc}；stderr={err_s[-300:]}")
    s = _summary_of(out_s)
    assert_(len(s["errors"]) == 1, f"应有 1 条 error，实得 {s['errors']}")
    e = s["errors"][0]
    assert_("10204" in e["error"], f"error 应含 statusCode 10204，实得 {e['error']!r}")
    assert_("author_status,status_reviewing" in e["error"],
            f"error 应含 statusMsg author_status,status_reviewing，实得 {e['error']!r}")
    assert_(e["reason"] == "removed", f"10204 归类不变，应仍为 removed，实得 {e['reason']!r}")
    assert_(len(calls) == 1, f"removed 不该重试，fetch 被调 {len(calls)} 次")
    assert_(fb.call_count == 0, "removed 不该触发浏览器兜底")


# ---------- DoD 2：100001 → blocked，并按 --blocked-retry 真重试 ----------

def case_100001_blocked_and_retried():
    out = _tmp_out("scrape_100001_")
    calls = []
    sleeps = []

    def fetch(url):
        calls.append(url)
        return SAMPLE_100001

    with patch.object(sc.time, "sleep", side_effect=lambda s: sleeps.append(s)):
        rc, out_s, err_s, fb = _run_main(
            ["--account", "probe", "--out-dir", str(out), "--ids", VID_100001,
             "--interval", "0", "--blocked-retry", "2", "--blocked-sleep", "0.25",
             "--allow-overwrite"], fetch)
    assert_(rc == 0, f"退出码应为 0，实得 {rc}；stderr={err_s[-300:]}")
    s = _summary_of(out_s)
    assert_(len(calls) == 3,
            f"--blocked-retry 2 应对同一 URL 抓 1+2=3 次，实得 {len(calls)} 次")
    assert_(len(s["errors"]) == 1, f"应有 1 条 error，实得 {s['errors']}")
    e = s["errors"][0]
    assert_(e["reason"] == "blocked", f"100001 应归 blocked，实得 {e['reason']!r}")
    assert_("100001" in e["error"], f"error 应含 statusCode 100001，实得 {e['error']!r}")
    assert_(sleeps == [0.25, 0.25],
            f"应按 --blocked-sleep 0.25 等待两次，实得 {sleeps}")
    assert_(fb.call_count == 1, "blocked 应进入既有浏览器兜底入口（本测试已 patch 成空结果）")


# ---------- DoD 3：既有语义不回退 ----------

def case_no_universal_data_is_blocked():
    out = _tmp_out("scrape_blocked_")
    rc, out_s, err_s, fb = _run_main(
        ["--account", "probe", "--out-dir", str(out), "--ids", VID_10204,
         "--interval", "0", "--blocked-retry", "0", "--allow-overwrite"],
        lambda url: SAMPLE_BLOCKED)
    assert_(rc == 0, f"退出码应为 0，实得 {rc}；stderr={err_s[-300:]}")
    s = _summary_of(out_s)
    assert_(s["items"] == [], f"风控页不该产出 items，实得 {s['items']}")
    assert_(len(s["errors"]) == 1 and s["errors"][0]["reason"] == "blocked",
            f"无内嵌数据应归 blocked，实得 {s['errors']}")
    assert_(fb.call_count == 1, "blocked 应进入浏览器兜底入口（已 patch）")


def case_full_item_struct_succeeds():
    out = _tmp_out("scrape_ok_")
    rc, out_s, err_s, fb = _run_main(
        ["--account", "probe", "--out-dir", str(out), "--ids", VID_OK,
         "--interval", "0", "--blocked-retry", "0", "--allow-overwrite"],
        lambda url: SAMPLE_OK)
    assert_(rc == 0, f"退出码应为 0，实得 {rc}；stderr={err_s[-300:]}")
    s = _summary_of(out_s)
    assert_(len(s["items"]) == 1, f"应抓到 1 条，实得 {s['items']}")
    assert_(s["errors"] == [], f"成功不该有 error，实得 {s['errors']}")
    it = s["items"][0]
    assert_(it["id"] == VID_OK, f"item id 不对：{it['id']}")
    assert_(it["hashtags"] == ["tail"], f"hashtags 解析不对：{it['hashtags']}")
    assert_(it["stats"]["playCount"] == 123, f"stats 解析不对：{it['stats']}")
    assert_(fb.call_count == 0, "成功路径不该触发浏览器兜底")


# ---------- DoD 4：parse_item_ex 的二元组返回形状不变 ----------

def case_parse_item_ex_shape_kept():
    item, reason = sc.parse_item_ex(SAMPLE_10204)
    assert_(item is None, f"10204 的 item 应为 None，实得 {item!r}")
    assert_(reason == "removed", f"10204 仍归 removed（PRD 关键决定 1），实得 {reason!r}")

    item2, reason2 = sc.parse_item_ex(SAMPLE_100001)
    assert_(item2 is None, f"100001 的 item 应为 None，实得 {item2!r}")
    assert_(reason2 == "blocked", f"100001 应归 blocked，实得 {reason2!r}")

    # 新函数承载 statusCode/statusMsg；旧函数只是它的前两项，形状不变
    detail = sc.parse_item_detail(SAMPLE_10204)
    assert_(isinstance(detail, tuple) and len(detail) == 4,
            f"parse_item_detail 应返回 4 元组，实得 {detail!r}")
    _, _, code, msg = detail
    assert_(str(code) == "10204", f"statusCode 应为 10204，实得 {code!r}")
    assert_(msg == "author_status,status_reviewing", f"statusMsg 不对，实得 {msg!r}")

    # 旧兼容入口 parse_item() 仍然可用（只取 item）
    assert_(sc.parse_item(SAMPLE_10204) is None, "parse_item 兼容入口应仍可用")
    assert_(sc.parse_item(SAMPLE_OK)["id"] == VID_OK, "parse_item 应能取到完整 item")


# ---------- DoD 5：--blocked-retry / --blocked-sleep 默认值与传参行为不变 ----------

def case_blocked_defaults_from_env():
    code = (
        "import json, sys\n"
        f"sys.path.insert(0, {str(SCRIPTS)!r})\n"
        "import scrape_tiktok_video as sc\n"
        "print(json.dumps([sc.BLOCKED_RETRY, sc.BLOCKED_SLEEP]))\n"
    )
    base_env = {k: v for k, v in os.environ.items()
                if not k.startswith("WB_SCRAPE_BLOCKED_")}

    def _run(env):
        r = subprocess.run([sys.executable, "-c", code], capture_output=True,
                           text=True, encoding="utf-8", errors="replace", env=env)
        assert_(r.returncode == 0, f"子进程失败：{r.stderr[-300:]}")
        return json.loads(r.stdout.strip().splitlines()[-1])

    got = _run(base_env)
    assert_(got == [1, 8.0], f"默认值应为 1 / 8.0，实得 {got}")

    env2 = dict(base_env, WB_SCRAPE_BLOCKED_RETRY="4", WB_SCRAPE_BLOCKED_SLEEP="2.5")
    got2 = _run(env2)
    assert_(got2 == [4, 2.5], f"环境变量覆盖应生效（4 / 2.5），实得 {got2}")


def main() -> int:
    print("抓取失败原因（T-02 DoD）：")
    for name, fn in [
        ("10204 错误文本带真实 statusCode+statusMsg（归类仍 removed）",
         case_10204_error_has_status),
        ("100001 归 blocked，--blocked-retry 2 时真抓 3 次",
         case_100001_blocked_and_retried),
        ("无内嵌数据仍归 blocked（不回退）", case_no_universal_data_is_blocked),
        ("完整 itemStruct 仍成功、items 非空、errors 为空", case_full_item_struct_succeeds),
        ("parse_item_ex 仍是 (item, reason) 二元组", case_parse_item_ex_shape_kept),
        ("--blocked-retry/--blocked-sleep 默认值与环境覆盖不变",
         case_blocked_defaults_from_env),
    ]:
        check(name, fn)
    print(f"\n通过 {_ok} / {_ok + _fail}")
    return 0 if _fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
