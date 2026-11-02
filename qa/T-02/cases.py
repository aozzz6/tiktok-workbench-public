# -*- coding: utf-8 -*-
r"""qa/T-02/cases.py — T-02 的 QA 用例（crew QA 第 2 步：把清单写成真实用例）

任务：T-02 抓取失败记下真实 statusCode/statusMsg；100001 归可重试
用例清单：.dsh-crew/jobs/s2-scrape-failure-fix/T-02-plan.md（C-01 ~ C-13，编号与那个文件一致）
DoD 来源：docs/tasks/tasks.md v1 · T-02 的 DoD 第 1-6 条

跑法：powershell -NoProfile -ExecutionPolicy Bypass -File qa\T-02\run.ps1   （等价：pipeline\.venv\Scripts\python.exe qa\T-02\cases.py）
退出码：0 = 全部 ok；1 = 有用例 FAIL。

纪律（这几条决定用例有没有价值，改用例前先读一遍）：
- 不联网。fetch_page 一律 patch 成返回固定 HTML；浏览器兜底 browser_batch_fallback
  一律 patch 成 {}（它会起 playwright 子进程真上网），反假绿用例改成「一调用就抛异常」。
- C-02 / C-03 把真正的网络入口（tiktok_fetch.fetch_html、socket.socket.connect、
  socket.create_connection）换成「一调用就抛异常」，并且**先自检 tripwire 真的会炸**，
  防止 patch 没挂上导致的假绿。
- C-05 的计数 mock 挂在 scrape_tiktok_video.fetch_page —— 即 main() 每抓一次就调用一次的
  那一层（parse_item_detail(fetch_page(url))）。挂在 parse_item_detail 上多半也能数到 3，
  但那不是「按 URL 取数」的动作；挂在 fetch_page 的内部 fetch_html 上会数到 9（每层自带
  3 次重试），必然假绿。
- C-13 是文档证据（报告里的「先红后绿」两段输出），无法由代码判定，所以打印 SKIP 并
  不计入通过数 —— 宁可显式不判，也不写一条永远绿的假用例。

断言信息一律写「期望什么、实得什么」，方便别人不复现就能判断。
"""
from __future__ import annotations

import contextlib
import importlib
import io
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

CASES = Path(__file__).resolve()
REPO = CASES.parents[2]                       # qa\T-02\cases.py -> qa\T-02 -> qa -> 仓库根
SCRIPTS = REPO / "pipeline" / "scripts"
TEST_FILE = REPO / "pipeline" / "tests" / "test_scrape_failure_reason.py"

sys.path.insert(0, str(SCRIPTS))
import scrape_tiktok_video as sc          # noqa: E402
import tiktok_fetch                       # noqa: E402

# 控制台是 GBK：遇到编不进去的字符时用 \uXXXX 转义，绝不让 print 把用例炸掉
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="backslashreplace")
    except Exception:      # pragma: no cover - 老解释器/被重定向的流
        pass

# 子进程（被检查的测试文件）一律强制写 UTF-8，父进程按键解码，避免 GBK/UTF-8 互猜
_CHILD_ENV = dict(os.environ, PYTHONIOENCODING="utf-8")

_ok = 0
_fail = 0
_skip = 0


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


def skip(name, why):
    global _skip
    print(f"  SKIP {name} -> {why}")
    _skip += 1


# ---------- 固定 HTML 样例（写死在本文件，不联网、不读文件） ----------

VID_10204 = "7696068031566318881"
VID_100001 = "7696777637192027425"
VID_OK = "7701872052117982495"
ACCOUNT = "probe"


def _url(vid: str) -> str:
    return f"https://www.tiktok.com/@{ACCOUNT}/video/{vid}"


def _universal_html(detail: dict) -> str:
    """构造一个带 __UNIVERSAL_DATA_FOR_REHYDRATION__ 的固定页面。"""
    payload = {"__DEFAULT_SCOPE__": {"webapp.video-detail": detail}}
    return (
        '<html><head></head><body><div id="app"></div>'
        '<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__" type="application/json">'
        + json.dumps(payload, ensure_ascii=False)
        + "</script></body></html>"
    )


# 10204：账号被判「作者审核中」；有内嵌数据，但 itemInfo.itemStruct 为空
SAMPLE_10204 = _universal_html({
    "statusCode": 10204,
    "statusMsg": "author_status,status_reviewing",
    "itemInfo": {"itemStruct": {}},
})

# 100001：RPC 网络错误（瞬断），可重试
SAMPLE_100001 = _universal_html({
    "statusCode": 100001,
    "statusMsg": "RPC error",
    "itemInfo": {"itemStruct": {}},
})

# 风控页 / 挑战页：整页没有 __UNIVERSAL_DATA_FOR_REHYDRATION__
SAMPLE_NO_UNIVERSAL = ("<html><body><h1>verify you are human</h1>"
                       "<p>please complete the challenge</p></body></html>")

# 正常视频详情页：完整 itemStruct
SAMPLE_OK = _universal_html({
    "statusCode": 0,
    "statusMsg": "",
    "itemInfo": {"itemStruct": {
        "id": VID_OK,
        "desc": "QA 固定样例文案 #qa",
        "createTime": 1761900000,
        "stats": {"playCount": 321, "diggCount": 12, "commentCount": 3,
                  "shareCount": 4, "collectCount": 5},
        "challenges": [{"title": "qa"}],
        "music": {"title": "qa-bgm"},
        "author": {"uniqueId": ACCOUNT},
    }},
})


# ---------- 跑一次 main() 的小工具 ----------

def _tmp_out(prefix: str) -> Path:
    """临时输出目录。用 mkdtemp，绝不往仓库里写临时文件。"""
    return Path(tempfile.mkdtemp(prefix=prefix))


def _run(argv_extra, fetch, *, fallback_raises: bool = False):
    """跑一次 sc.main()，返回 (rc, stdout, stderr, fallback_mock, sleeps)。

    fetch_page 被换成用例给的 fetch（不联网）；browser_batch_fallback 默认换成 {}，
    fallback_raises=True 时换成「一调用就抛异常」（反假绿用）；
    time.sleep 被换成记账，跑用例不真的等。
    """
    out_buf, err_buf = io.StringIO(), io.StringIO()
    sleeps: list = []
    fb_kwargs = ({"side_effect": RuntimeError("tripwire: 浏览器兜底被调用")}
                 if fallback_raises else {"return_value": {}})
    with patch.object(sys, "argv", ["scrape_tiktok_video.py", *argv_extra]), \
            patch.object(sc, "fetch_page", side_effect=fetch), \
            patch.object(sc, "browser_batch_fallback", **fb_kwargs) as fb, \
            patch.object(sc.time, "sleep", side_effect=lambda s: sleeps.append(s)), \
            contextlib.redirect_stdout(out_buf), \
            contextlib.redirect_stderr(err_buf):
        rc = sc.main()
    return rc, out_buf.getvalue(), err_buf.getvalue(), fb, sleeps


def _summary_of(stdout: str) -> dict:
    """取 stdout 末行的 JSON 摘要（就是 gate 用的那份、也是本次结果的原文）。"""
    for line in reversed(stdout.strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
    raise AssertionError(f"stdout 里找不到摘要 JSON；尾部：{stdout[-400:]!r}")


def _base_argv(out: Path, vid: str, *extra: str) -> list[str]:
    """一次最小抓取的参数：单条 --ids、不真的限流、允许覆盖（本次写入临时目录）。"""
    return ["--account", ACCOUNT, "--out-dir", str(out), "--ids", vid,
            "--interval", "0", "--allow-overwrite", *extra]


def _only_error(summary: dict) -> dict:
    errs = summary.get("errors") or []
    assert_(len(errs) == 1,
            f"期望 errors 长度 1，实得 {len(errs)}：{errs}")
    return errs[0]


# ---------- C-01：10204 的错误文本同时带真实 statusCode 与 statusMsg ----------

def case_c01_10204_error_text():
    rc, out_s, err_s, fb, _sleeps = _run(
        _base_argv(_tmp_out("t02_c01_"), VID_10204, "--blocked-retry", "0"),
        lambda url: SAMPLE_10204)
    assert_(rc == 0, f"期望退出码 0，实得 {rc}；stderr 尾部：{err_s[-300:]!r}")
    e = _only_error(_summary_of(out_s))
    text = e["error"]
    assert_("10204" in text,
            f"期望 errors[0]['error'] 含真实 statusCode '10204'，实得 {text!r}")
    assert_("author_status,status_reviewing" in text,
            f"期望 errors[0]['error'] 含真实 statusMsg 'author_status,status_reviewing'，实得 {text!r}")
    assert_(fb.call_count == 0, f"10204 归类不变（removed），不该进浏览器兜底，实得 {fb.call_count} 次")


# ---------- C-02：上面那类抓取全程没有发起真实网络请求 ----------

def case_c02_no_real_network():
    out = _tmp_out("t02_c02_")
    calls: list = []
    hits: list = []

    def fetch(url):
        calls.append(url)
        return SAMPLE_10204

    def boom(*a, **k):
        hits.append(a)
        raise RuntimeError("tripwire: 检测到真实网络请求")

    with patch.object(tiktok_fetch, "fetch_html", side_effect=boom), \
            patch.object(socket.socket, "connect", boom), \
            patch.object(socket, "create_connection", boom):
        # 先自检：tripwire 真的会炸，否则本用例必然假绿
        fired = []
        try:
            tiktok_fetch.fetch_html("https://www.tiktok.com/@probe/video/1")
        except RuntimeError as exc:
            fired.append(str(exc))
        try:
            socket.create_connection(("127.0.0.1", 9), timeout=0.01)
        except RuntimeError as exc:
            fired.append(str(exc))
        assert_(len(fired) == 2 and all("tripwire" in f for f in fired),
                f"tripwire 没挂上（fetch_html/socket 未被替换），本用例会假绿；实得 {fired!r}")
        hits.clear()

        rc, out_s, err_s, fb, _sleeps = _run(
            _base_argv(out, VID_10204, "--blocked-retry", "0"),
            fetch, fallback_raises=True)

    assert_(hits == [], f"期望 0 次真实网络调用，实得 {len(hits)} 次：{hits!r}")
    assert_(rc == 0,
            f"网络入口全部换成抛异常后，期望抓取仍能完成（退出码 0），实得 {rc}；"
            f"stderr 尾部：{err_s[-300:]!r}")
    assert_(len(calls) == 1, f"期望固定样例只被取 1 次，实得 {len(calls)} 次：{calls!r}")
    assert_(fb.call_count == 0, f"10204 不该进浏览器兜底，实得 {fb.call_count} 次；"
                                "兜底已被换成抛异常，叫了就会炸")
    e = _only_error(_summary_of(out_s))   # C-01 的结论在这条用例里同样必须成立
    assert_("10204" in e["error"] and "author_status,status_reviewing" in e["error"],
            f"期望 error 同时含 10204 与 author_status,status_reviewing（不靠真实网络），实得 {e['error']!r}")


# ---------- C-03：工程师的单元测试自带固定样例、不依赖网络 ----------

# 静态扫描用的嫌疑写法：样例若是从文件/网络读进来的，这里必然命中其一
_SUSPECT_SAMPLE_SOURCES = (
    "urlopen(", "requests.get(", "requests.post(", "curl_cffi", "httpx",
    "fetch_html(", "Fetcher.get(", "urlretrieve(", "read_text(",
)

_NETGUARD_DRIVER = r'''# -*- coding: utf-8 -*-
"""QA 临时驱动（写在临时目录，不落仓库）：把网络入口换成抛异常后跑目标文件。"""
import runpy
import socket
import sys

target, scripts = sys.argv[1], sys.argv[2]
sys.path.insert(0, scripts)
import tiktok_fetch


def _boom(*a, **k):
    raise RuntimeError("tripwire: QA 用例检测到真实网络请求")


tiktok_fetch.fetch_html = _boom
socket.socket.connect = _boom
socket.socket.connect_ex = _boom
socket.create_connection = _boom

sys.argv = [target]
runpy.run_path(target, run_name="__main__")
'''

_DRIVER_PATH = None


def _netguard_driver() -> Path:
    global _DRIVER_PATH
    if _DRIVER_PATH is None or not _DRIVER_PATH.exists():
        d = Path(tempfile.mkdtemp(prefix="t02_driver_"))
        p = d / "_t02_netguard.py"
        p.write_text(_NETGUARD_DRIVER, encoding="utf-8")
        _DRIVER_PATH = p
    return _DRIVER_PATH


def _run_py_file(path: Path, *, poison_network: bool = False):
    if poison_network:
        cmd = [sys.executable, str(_netguard_driver()), str(path), str(SCRIPTS)]
    else:
        cmd = [sys.executable, str(path)]
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", cwd=str(REPO), env=_CHILD_ENV)


def _totals_of(stdout: str) -> tuple[int, int]:
    hits = re.findall(r"通过\s*(\d+)\s*/\s*(\d+)", stdout)
    assert_(hits, f"输出里找不到「通过 N/M」，实得尾部：{stdout[-400:]!r}")
    n, m = hits[-1]
    return int(n), int(m)


def case_c03_unit_test_offline():
    assert_(TEST_FILE.is_file(), f"期望单元测试文件存在：{TEST_FILE}")
    text = TEST_FILE.read_text(encoding="utf-8")
    assert_("10204" in text,
            f"期望 {TEST_FILE.name} 里硬编码 10204 字面量（固定样例），没找到")
    assert_("author_status,status_reviewing" in text,
            f"期望 {TEST_FILE.name} 里硬编码 author_status,status_reviewing 字面量（固定样例），没找到")
    suspect = [p for p in _SUSPECT_SAMPLE_SOURCES if p in text]
    assert_(not suspect,
            f"期望样例写死在文件里、不从文件/网络读，实得命中嫌疑写法 {suspect}")

    r = _run_py_file(TEST_FILE, poison_network=True)
    tail = (r.stdout or "")[-400:]
    assert_(r.returncode == 0,
            f"网络入口换成抛异常后，期望该测试文件仍退出码 0，实得 {r.returncode}；"
            f"stdout 尾部 {tail!r}；stderr 尾部 {(r.stderr or '')[-300:]!r}")
    n, m = _totals_of(r.stdout or "")
    assert_(m > 0 and n == m, f"期望「通过 N/N」且 N>0，实得「通过 {n}/{m}」")


# ---------- C-04：100001 判为可重试 blocked ----------

def case_c04_100001_blocked():
    rc, out_s, err_s, fb, _sleeps = _run(
        _base_argv(_tmp_out("t02_c04_"), VID_100001, "--blocked-retry", "0"),
        lambda url: SAMPLE_100001)
    assert_(rc == 0, f"期望退出码 0，实得 {rc}；stderr 尾部：{err_s[-300:]!r}")
    e = _only_error(_summary_of(out_s))
    assert_(e["reason"] == "blocked",
            f"期望 100001（RPC 瞬断）的 reason == 'blocked'，实得 {e['reason']!r}")
    assert_("100001" in e["error"],
            f"期望 error 文本也带上 100001，实得 {e['error']!r}")


# ---------- C-05：--blocked-retry 2 时同一 URL 恰好抓 3 次 ----------
# mock 挂在 sc.fetch_page：main() 的每次尝试都是 parse_item_detail(fetch_page(url))，
# 所以「fetch_page 被调几次」=「真去按 URL 取数几次」= 1 次原始 + N 次重试。

def case_c05_retry_two_means_three_fetches():
    calls: list = []

    def fetch(url):
        calls.append(url)
        return SAMPLE_100001

    rc, out_s, err_s, fb, sleeps = _run(
        _base_argv(_tmp_out("t02_c05_"), VID_100001,
                   "--blocked-retry", "2", "--blocked-sleep", "0.01"),
        fetch)
    assert_(rc == 0, f"期望退出码 0，实得 {rc}；stderr 尾部：{err_s[-300:]!r}")
    assert_(len(calls) == 3,
            f"期望 fetch_page 被调用恰好 3 次（1 次原始 + 2 次重试），实得 {len(calls)} 次：{calls!r}")
    assert_(len(set(calls)) == 1 and calls[0] == _url(VID_100001),
            f"期望 3 次抓的是同一个 URL {_url(VID_100001)!r}，实得 {calls!r}")
    assert_(len(sleeps) == 2,
            f"期望两次重试各等一次（sleep 2 次），实得 {len(sleeps)} 次：{sleeps!r}")
    assert_(out_s.count("[retry]") == 2,
            f"期望 stdout 出现 2 行 [retry]（每条重试一行），实得 {out_s.count('[retry]')} 行；"
            f"stdout 尾部：{out_s[-400:]!r}")
    e = _only_error(_summary_of(out_s))
    assert_(e["reason"] == "blocked", f"期望最终仍归 blocked，实得 {e['reason']!r}")


# ---------- C-06：既有语义不回退——缺 __UNIVERSAL_DATA_FOR_REHYDRATION__ 仍判 blocked ----------

def case_c06_missing_universal_blocked():
    rc, out_s, err_s, fb, _sleeps = _run(
        _base_argv(_tmp_out("t02_c06_"), VID_10204, "--blocked-retry", "0"),
        lambda url: SAMPLE_NO_UNIVERSAL)
    assert_(rc == 0, f"期望退出码 0，实得 {rc}；stderr 尾部：{err_s[-300:]!r}")
    s = _summary_of(out_s)
    assert_(s["items"] == [], f"期望风控页不产出 items，实得 {s['items']!r}")
    e = _only_error(s)
    assert_(e["reason"] == "blocked", f"期望无内嵌数据仍归 'blocked'，实得 {e['reason']!r}")


# ---------- C-07：既有语义不回退——完整 itemStruct 仍成功 ----------

def case_c07_full_item_succeeds():
    rc, out_s, err_s, fb, _sleeps = _run(
        _base_argv(_tmp_out("t02_c07_"), VID_OK, "--blocked-retry", "0"),
        lambda url: SAMPLE_OK)
    assert_(rc == 0, f"期望退出码 0，实得 {rc}；stderr 尾部：{err_s[-300:]!r}")
    s = _summary_of(out_s)
    assert_(len(s["items"]) >= 1, f"期望 items 长度 >= 1，实得 {len(s['items'])}：{s['items']!r}")
    assert_(s["errors"] == [], f"期望 errors == []，实得 {s['errors']!r}")
    assert_(s["items"][0]["id"] == VID_OK,
            f"期望 items[0]['id'] == {VID_OK}，实得 {s['items'][0].get('id')!r}")
    assert_(s["items"][0]["hashtags"] == ["qa"],
            f"期望 hashtags == ['qa']，实得 {s['items'][0].get('hashtags')!r}")
    assert_(fb.call_count == 0, f"成功路径不该进浏览器兜底，实得 {fb.call_count} 次")


# ---------- C-08：parse_item_ex() 的返回形状仍可二元组解包 ----------

def case_c08_parse_item_ex_two_tuple():
    r = sc.parse_item_ex(SAMPLE_10204)
    try:
        n = len(r)
    except TypeError as exc:
        raise AssertionError(
            f"期望 parse_item_ex 返回可解包的二元组，实得不可求长度："
            f"{type(r).__name__} {r!r}（{exc}）")
    assert_(n == 2, f"期望二元组（len == 2），实得 {type(r).__name__} 长度 {n}：{r!r}")
    try:
        item, reason = r            # DoD 第 4 条：两个既有调用方的解包写法仍成立
    except (ValueError, TypeError) as exc:
        raise AssertionError(
            f"期望 parse_item_ex 的返回值可 `item, reason = r` 解包，实得抛 "
            f"{type(exc).__name__}: {exc}；返回值 {r!r}")
    assert_(item is None or isinstance(item, dict),
            f"期望解包出的 item 为 None 或 dict，实得 {type(item).__name__}")


# ---------- C-09：10204 的分类不变（item is None 且 reason == removed） ----------

def case_c09_10204_stays_removed():
    item, reason = sc.parse_item_ex(SAMPLE_10204)
    assert_(item is None, f"期望 10204 的 item 严格为 None，实得 {item!r}（{type(item).__name__}）")
    assert_(reason == "removed",
            f"期望 10204 的 reason 字符串等于 'removed'（PRD 关键决定 1：分类不变），实得 {reason!r}")


# ---------- C-10：--blocked-retry / --blocked-sleep 的默认值仍来自环境变量 ----------

_ENV_KEYS = ("WB_SCRAPE_BLOCKED_RETRY", "WB_SCRAPE_BLOCKED_SLEEP")


def _reload_with_env(env_set: dict) -> None:
    for k in _ENV_KEYS:
        os.environ.pop(k, None)
    os.environ.update(env_set)
    importlib.reload(sc)


def case_c10_blocked_defaults_from_env():
    saved = {k: os.environ.get(k) for k in _ENV_KEYS}
    try:
        _reload_with_env({})
        assert_(sc.BLOCKED_RETRY == 1,
                f"清掉 WB_SCRAPE_BLOCKED_RETRY 后期望默认 1，实得 {sc.BLOCKED_RETRY!r}")
        assert_(float(sc.BLOCKED_SLEEP) == 8.0,
                f"清掉 WB_SCRAPE_BLOCKED_SLEEP 后期望默认 8，实得 {sc.BLOCKED_SLEEP!r}")

        # 行为级：不带 --blocked-retry / --blocked-sleep 跑一次 100001，应恰好 1 次重试、等 8s
        calls1: list = []

        def fetch1(url):
            calls1.append(url)
            return SAMPLE_100001

        rc1, _so1, err1, _fb1, sleeps1 = _run(
            _base_argv(_tmp_out("t02_c10a_"), VID_100001), fetch1)
        assert_(rc1 == 0, f"期望退出码 0，实得 {rc1}；stderr 尾部：{err1[-300:]!r}")
        assert_(len(calls1) == 2,
                f"默认 retry=1：期望抓 1+1=2 次，实得 {len(calls1)} 次：{calls1!r}")
        assert_(sleeps1 == [8.0],
                f"默认 sleep=8：期望重试前等 8.0 一次，实得 {sleeps1!r}")

        # 环境变量仍然覆盖默认值
        _reload_with_env({"WB_SCRAPE_BLOCKED_RETRY": "4", "WB_SCRAPE_BLOCKED_SLEEP": "2.5"})
        calls2: list = []

        def fetch2(url):
            calls2.append(url)
            return SAMPLE_100001

        rc2, _so2, err2, _fb2, sleeps2 = _run(
            _base_argv(_tmp_out("t02_c10b_"), VID_100001), fetch2)
        assert_(rc2 == 0, f"期望退出码 0，实得 {rc2}；stderr 尾部：{err2[-300:]!r}")
        assert_(len(calls2) == 5,
                f"WB_SCRAPE_BLOCKED_RETRY=4：期望抓 1+4=5 次，实得 {len(calls2)} 次：{calls2!r}")
        assert_(sleeps2 == [2.5, 2.5, 2.5, 2.5],
                f"WB_SCRAPE_BLOCKED_SLEEP=2.5：期望等 2.5 四次，实得 {sleeps2!r}")
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        importlib.reload(sc)


# ---------- C-11：命令行传参仍然生效（行为级，不读 help 文本） ----------

def case_c11_cli_args_take_effect():
    # a) --blocked-retry 0 → 只抓 1 次，不重试、不 sleep
    calls_a: list = []

    def fetch_a(url):
        calls_a.append(url)
        return SAMPLE_100001

    rc_a, so_a, err_a, _fb_a, sleeps_a = _run(
        _base_argv(_tmp_out("t02_c11a_"), VID_100001, "--blocked-retry", "0"), fetch_a)
    assert_(rc_a == 0, f"期望退出码 0，实得 {rc_a}；stderr 尾部：{err_a[-300:]!r}")
    assert_(len(calls_a) == 1,
            f"--blocked-retry 0：期望只抓 1 次，实得 {len(calls_a)} 次：{calls_a!r}")
    assert_(sleeps_a == [],
            f"--blocked-retry 0：期望 sleep 调用次数为 0，实得 {len(sleeps_a)} 次：{sleeps_a!r}")
    assert_(_only_error(_summary_of(so_a))["reason"] == "blocked",
            "--blocked-retry 0：期望仍归 blocked")

    # b) --blocked-sleep 0 → 2 次重试、每次都等 0（不是默认的 8）
    calls_b: list = []

    def fetch_b(url):
        calls_b.append(url)
        return SAMPLE_100001

    rc_b, _so_b, err_b, _fb_b, sleeps_b = _run(
        _base_argv(_tmp_out("t02_c11b_"), VID_100001,
                   "--blocked-retry", "2", "--blocked-sleep", "0"), fetch_b)
    assert_(rc_b == 0, f"期望退出码 0，实得 {rc_b}；stderr 尾部：{err_b[-300:]!r}")
    assert_(len(calls_b) == 3,
            f"--blocked-retry 2：期望抓 3 次，实得 {len(calls_b)} 次：{calls_b!r}")
    assert_(len(sleeps_b) == 2,
            f"--blocked-sleep 0：期望 sleep 被调用 2 次（每次重试一次），实得 {len(sleeps_b)} 次：{sleeps_b!r}")
    assert_(set(sleeps_b) == {0.0},
            f"--blocked-sleep 0：期望 sleep 参数全为 0.0，实得 {sleeps_b!r}")


# ---------- C-12：新增单元测试文件现在跑是绿的 ----------

def case_c12_unit_test_green():
    assert_(TEST_FILE.is_file(), f"期望单元测试文件存在：{TEST_FILE}")
    r = _run_py_file(TEST_FILE)
    assert_(r.returncode == 0,
            f"期望退出码 0，实得 {r.returncode}；stdout 尾部 {(r.stdout or '')[-400:]!r}；"
            f"stderr 尾部 {(r.stderr or '')[-300:]!r}")
    n, m = _totals_of(r.stdout or "")
    assert_(n > 0, f"期望用例数 N > 0，实得「通过 {n}/{m}」")
    assert_(n == m, f"期望「通过 N/N」，实得「通过 {n}/{m}」")


def main() -> int:
    print("T-02 QA 用例（清单：.dsh-crew/jobs/s2-scrape-failure-fix/T-02-plan.md）")
    print("仓库：" + str(REPO))
    print("解释器：" + sys.executable)

    for name, fn in [
        ("C-01 10204 的 errors[0].error 同时含 statusCode 10204 与 statusMsg", case_c01_10204_error_text),
        ("C-02 同一抓取全程零真实网络请求（网络入口换成抛异常）", case_c02_no_real_network),
        ("C-03 单元测试自带固定样例、毒化网络后仍绿", case_c03_unit_test_offline),
        ("C-04 100001 判为可重试 blocked", case_c04_100001_blocked),
        ("C-05 --blocked-retry 2 时同一 URL 恰好抓 3 次", case_c05_retry_two_means_three_fetches),
        ("C-06 缺 __UNIVERSAL_DATA_FOR_REHYDRATION__ 仍判 blocked", case_c06_missing_universal_blocked),
        ("C-07 完整 itemStruct 仍成功、items 非空、errors 为空", case_c07_full_item_succeeds),
        ("C-08 parse_item_ex 仍可二元组解包", case_c08_parse_item_ex_two_tuple),
        ("C-09 10204 分类不变：item is None 且 reason == removed", case_c09_10204_stays_removed),
        ("C-10 --blocked-retry/--blocked-sleep 默认值仍来自环境变量", case_c10_blocked_defaults_from_env),
        ("C-11 命令行传参仍然生效（retry 0 不抓第二次、sleep 0 真等 0）", case_c11_cli_args_take_effect),
        ("C-12 新增单元测试文件现在跑是绿的", case_c12_unit_test_green),
    ]:
        check(name, fn)

    # C-13 是文档证据：改动前的红输出无法在今天重跑再现，只能按工程师报告核对。
    # 代码判不了它，所以不给它一条永远绿的断言（那正是假绿）。
    skip("C-13 报告给出「先红后绿」两段真实输出",
         "文档证据，代码无法判定；须按工程师报告里改动前/后的两段输出现场核对（PM 记入 qa/gaps.md）")

    print(f"\n通过 {_ok} / {_ok + _fail}")
    if _skip:
        print(f"（另有 {_skip} 条 SKIP，不计入通过数：C-13）")
    return 0 if _fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
