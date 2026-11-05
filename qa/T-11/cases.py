# -*- coding: utf-8 -*-
r"""qa/T-11/cases.py —— T-11「对标分析页空数据不再白屏」的 QA 用例（C-01 ~ C-06）

用例清单：`.dsh-crew/jobs/fix-check-findings/T-11-plan.md`，一条一条照写，不增不减。
DoD 来源：本作业 docs/tasks/tasks.md 里 T-11 的 DoD 第 1~3 条（含 2026-11-05 的追加条：
bench.js 的 trend()/topvideos()、两串 TypeError、10 个区块）。

被测实现：
  - webapp/static/bench2/derive.js
  - webapp/static/bench2/bench.js
工程师的回归用例：webapp/tests/bench_empty_data.test.js。

独立性说明（重要）：
  - C-01 是 DoD 第 1 条明写的那条命令，只有这一条调用工程师的用例；
  - C-03 / C-04 是**独立探针**：自己构造空的 /api/benchmark 载荷，自己在 jsdom 里加载
    仓库里真实的 derive.js / bench.js，直接对这两个产物断言，不吃工程师用例的断言；
  - C-05 用 playwright-core 打开真浏览器打真实服务；
  - C-06 跑仓库 QA 总跑器 qa\run-all.ps1。

跑法：powershell -NoProfile -ExecutionPolicy Bypass -File qa\T-11\run.ps1
（直接调用本文件时用 webapp\.venv\Scripts\python.exe）
"""
from __future__ import annotations

import contextlib
import io as _io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

# 控制台/管道编码可能不是 UTF-8：直连真控制台时交给 Python 默认处理，被管道接走时改 UTF-8。
for _stream in (sys.stdout, sys.stderr):
    try:
        if not _stream.isatty():
            _stream.reconfigure(encoding="utf-8", errors="replace")
        else:
            _stream.reconfigure(errors="replace")
    except Exception:
        pass

_REPO = Path(__file__).resolve().parents[2]
_WEBAPP = _REPO / "webapp"
_TEST = _WEBAPP / "tests" / "bench_empty_data.test.js"
_DERIVE = _WEBAPP / "static" / "bench2" / "derive.js"
_BENCH = _WEBAPP / "static" / "bench2" / "bench.js"
_JSDOM = _WEBAPP / "node_modules" / "jsdom"
_PLAYWRIGHT = _WEBAPP / "node_modules" / "playwright-core"
_JOB = _REPO / ".dsh-crew" / "jobs" / "fix-check-findings"
_DOCS_HOME = Path(tempfile.gettempdir()) / "dsh-crew" / "d-tiktok-workbench" / "fix-check-findings"
_BASE = "http://127.0.0.1:8789"
_TEST_REL = "tests\\bench_empty_data.test.js"
_QA_RUNALL = _REPO / "qa" / "run-all.ps1"

_T11_FILES = ["derive.js", "bench.js", "bench_empty_data.test.js"]   # T-11 任务行 Owns（归因用）

# 共享的「嵌套 run-all」标记（三个 QA 统一用，PM 2026-11-05 要求）：
# 任何一次由 C-06 发起的 qa\run-all.ps1 都在子进程环境里带 QA_RUNALL_NESTED=1，
# 于是那一层里 T-11/T-12/T-13 的回归用例都不再各自再起一个 run-all —— 否则三层套四层，
# 一轮要跑十几分钟。带这个变量的进程里，C-06 只打印一行说明并通过。
_NESTED_RUNALL = bool((os.environ.get("QA_RUNALL_NESTED") or "").strip())

# 页面 bench_s096.js 的 renderAll() 依次填 10 个功能单元；data-unit 名字来自各 U.xxx() 的 key
_EXPECTED_UNITS = ["kpi", "accbars", "trend", "patterns", "acctable", "videos",
                   "products", "topvideos", "takeaways", "foot"]

_TIMEOUT = 1800

_TMP = None
_ok = 0
_fail = 0


def _emit(text):
    for line in text.splitlines():
        if line.strip():
            print("      " + line.rstrip())


def _run_case(cid, name, fn):
    global _ok, _fail
    buf = _io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            fn()
        print("ok " + cid + " " + name)
        _emit(buf.getvalue())
        _ok += 1
    except AssertionError as e:
        print("FAIL " + cid + " " + name + " -> " + str(e))
        _emit(buf.getvalue())
        _fail += 1
    except Exception as e:  # noqa: BLE001 - 用例自己必须说清失败原因，不能整段崩掉
        print("FAIL " + cid + " " + name + " -> " + type(e).__name__ + ": " + str(e))
        _emit(buf.getvalue())
        _fail += 1


def assert_(cond, msg="assert failed"):
    if not cond:
        raise AssertionError(msg)


def _node_exe():
    exe = shutil.which("node") or shutil.which("node.exe")
    assert_(exe is not None, "PATH 上找不到 node（项目测试命令需要 node），无法跑本任务的用例")
    return exe


def _powershell():
    for cand in (shutil.which("powershell"), shutil.which("powershell.exe")):
        if cand:
            return cand
    assert_(False, "找不到 powershell（qa\\run-all.ps1 需要它）")


def _child_env(**extra):
    env = dict(os.environ)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.update({k: str(v) for k, v in extra.items() if v is not None})
    return env


def _run(cmd, cwd=None, timeout=_TIMEOUT, env=None):
    """跑一个子进程，返回 (退出码, stdout+stderr 合并文本, stdout, stderr)。"""
    p = subprocess.run([str(c) for c in cmd], cwd=str(cwd or _REPO), capture_output=True,
                       env=env or _child_env(), timeout=timeout)
    out = p.stdout.decode("utf-8", "replace")
    err = p.stderr.decode("utf-8", "replace")
    return p.returncode, out + "\n" + err, out, err


def _tmpdir():
    global _TMP
    if _TMP is None:
        _TMP = Path(tempfile.mkdtemp(prefix="qa_t11_"))
    return _TMP


def _write_child(name, text):
    p = _tmpdir() / name
    p.write_text(text, encoding="utf-8")
    return p


def _tail(text, n=800):
    text = (text or "").strip()
    return text[-n:] if len(text) > n else text


# ================================================================ C-01
# DoD 1 前半：新用例存在且当前为绿 —— 就这一条引用工程师的用例。
def case_01_new_test_exists_and_green():
    assert_(_TEST.exists(), "新用例不存在：" + str(_TEST))
    node = _node_exe()
    rc, text, out, err = _run([node, _TEST_REL], cwd=_WEBAPP, timeout=300)
    assert_(rc == 0, "期望 `node " + _TEST_REL + "` 退出码 0，实得 " + str(rc) +
            "；输出尾部：" + _tail(text, 500))
    # 注意：用例自己的 PASS 文案里就写着「不抛 RangeError: Invalid time value」，
    # 所以不能对整段文本做子串判断；只统计 **不在 PASS 行里** 的出现，那才是真的崩了。
    bad_lines = []
    for line in text.splitlines():
        if "PASS" in line:
            continue
        if ("RangeError" in line) or ("Invalid time value" in line):
            bad_lines.append(line.strip())
    assert_(not bad_lines,
            "输出里出现真正的 RangeError / Invalid time value（不在 PASS 行里）：" +
            json.dumps(bad_lines[:4], ensure_ascii=False))
    fail_lines = [ln.strip() for ln in text.splitlines() if ln.strip().startswith("FAIL")]
    assert_(not fail_lines, "用例输出里有 FAIL 行（退出码却是 0，自相矛盾）：" +
            json.dumps(fail_lines[:4], ensure_ascii=False))
    print("        证据：`cd webapp && node " + _TEST_REL + "` 退出码 0；输出尾部：")
    for line in _tail(text, 400).splitlines()[-4:]:
        print("          " + line)


# ================================================================ C-02
# DoD 1 后半：报告 / 提交信息里「改前真实红 + 改后真实绿」两段都在。
# 文档核对，不跑命令；也不拿当前绿当作改前红的替代。
_EXIT_ZERO = re.compile(r"(?i)(exit(?:\s*code)?|退出码)\s*[:=]?\s*0\b")
_EXIT_NONZERO = re.compile(r"(?i)(exit(?:\s*code)?|退出码)\s*[:=]?\s*[1-9]\d*")
_SUM_EQ = re.compile(r"通过\s*(\d+)\s*/\s*(\d+)")


def _report_candidates():
    cands = [
        _REPO / "qa" / "T-11" / "engineer-report.md",
        _JOB / "T-11-report.md",
        _JOB / "T-11-report.txt",
        _JOB / "reviews" / "T-11-report.md",
        _JOB / "reviews" / "T-11.md",
        _DOCS_HOME / "T-11-report.md",
        _DOCS_HOME / "T-11-report.txt",
        _DOCS_HOME / "docs" / "reports" / "T-11.md",
    ]
    hits = [p for p in cands if p.exists()]
    for root in (_JOB, _DOCS_HOME):
        if root.exists():
            for p in sorted(root.rglob("*")):
                if not p.is_file():
                    continue
                nm = p.name.lower()
                if ("t-11" in nm or "t11" in nm) and ("report" in nm or "报告" in p.name or "summary" in nm):
                    if p not in hits:
                        hits.append(p)
    return hits


def _git_t11_messages():
    rc, text, out, err = _run(["git", "-C", str(_REPO), "log", "--all", "--format=%H%n%B%x00"],
                              cwd=_REPO, timeout=120)
    if rc != 0:
        return [], "git log 失败（退出码 " + str(rc) + "）：" + _tail(err, 300)
    blocks = [b.strip() for b in out.split("\x00") if b.strip()]
    picked = []
    for b in blocks:
        if "T-11" in b or "bench_empty_data" in b or "crew T-11" in b:
            picked.append(b)
    return picked, ""


def _red_evidence(text):
    """改前真实红：报错文本 + 退出码非 0，两样都要。返回 (有没有报错, 有没有非零退出码)。"""
    has_err = ("Invalid time value" in text) or ("Invalid Date" in text)
    has_exit = bool(_EXIT_NONZERO.search(text)) or ("FAILED" in text) or ("失败" in text)
    return has_err, has_exit


def _green_evidence(text):
    """改后真实绿：退出码 0，或「通过 N / N」且两数相等。"""
    if _EXIT_ZERO.search(text):
        return True
    for a, b in _SUM_EQ.findall(text):
        if int(a) == int(b):
            return True
    return False


def case_02_before_red_and_after_green_in_report():
    cands = _report_candidates()
    msgs, git_err = _git_t11_messages()
    sources = [(str(p), p.read_text(encoding="utf-8", errors="replace")) for p in cands]
    sources += [("git 提交信息 #" + str(i + 1), m) for i, m in enumerate(msgs)]
    if not sources:
        where = "、".join(str(p) for p in _report_candidates()[:4])
        raise AssertionError(
            "没有找到 T-11 工程师报告（查过 " + where + " 等候选路径，都不存在），"
            "git 里也没有 T-11 的提交信息（git log --all 查过 T-11 / bench_empty_data / crew T-11，0 条）"
            " -> 改前真实红与改后真实绿这**两段**在仓库里都没有落盘证据" +
            ("；" + git_err if git_err else ""))

    good = []
    why = []
    for label, text in sources:
        has_err, has_exit = _red_evidence(text)
        green = _green_evidence(text)
        if has_err and has_exit and green:
            good.append(label)
        else:
            lack = []
            if not has_err:
                lack.append("改前一段缺失败报错文本（Invalid time value / Invalid Date）")
            if not has_exit:
                lack.append("改前一段缺『退出码非 0』")
            if not green:
                lack.append("改后一段缺『退出码 0』或『通过 N / N』")
            why.append(label + " 缺：" + "、".join(lack))
    assert_(good, "找到了 " + str(len(sources)) + " 份来源，但没有一份同时含改前红与改后绿。" +
            "；".join(why))
    print("        证据：" + good[0] + " 同时含改前真实红（失败报错文本 + 非零退出码）与改后真实绿")


# ================================================================ C-03 / C-04 共用：探针骨架
_PROBE_PRELUDE = r"""
const path = require('path');
const fs = require('fs');
const { JSDOM } = require(process.env.QA_JSDOM);
const root = process.env.QA_WEBAPP;
const readStatic = (p) => fs.readFileSync(path.join(root, 'static', 'bench2', p), 'utf8');
const out = { inputs: {}, result: {}, threw: null };
function makeEmptyPayload() {
  // 与 2026-11-05 实测 /api/benchmark（空库）同一形状：accounts / videos 都是空数组。
  return {
    ts: 1793869384.08,
    kpis: {
      accounts: 0, videos: 0, total_plays: 0, total_likes: 0, avg_engagement: 0,
      with_products: 0, product_coverage: 0, top_accounts_by_plays: [],
      ledger_rows: 0, ledger_only: 0
    },
    accounts: [],
    account_options: [],
    videos: [],
    patterns: {
      play_buckets: [
        { label: '0-1k', count: 0, pct: 0 },
        { label: '1k-10k', count: 0, pct: 0 },
        { label: '10k-100k', count: 0, pct: 0 },
        { label: '100k+', count: 0, pct: 0 }
      ],
      top_tags: [], hook_words: [], top_videos: [], products_top: [],
      n_videos_in_sample: 0
    },
    takeaways: [],
    data_sources: { scrape_dir: '', products_dir: '', ledger_rows: 0, ledger_only: 0, gmv_status: '' }
  };
}
const clone = (x) => JSON.parse(JSON.stringify(x));
"""

_PROBE_DERIVE = _PROBE_PRELUDE + r"""
try {
  const emptyPayload = makeEmptyPayload();
  out.inputs = { accountsLen: emptyPayload.accounts.length, videosLen: emptyPayload.videos.length };
  const dom = new JSDOM('<!doctype html><html><body></body></html>', { runScripts: 'outside-only' });
  const w = dom.window;
  w.eval(readStatic('derive.js'));
  out.result.deriveType = typeof w.BENCH_DERIVE;
  const D = w.BENCH_DERIVE(clone(emptyPayload));
  Object.assign(out.result, {
    kpisType: typeof D.kpis,
    kpisAccounts: D.kpis && D.kpis.accounts,
    kpisVideos: D.kpis && D.kpis.videos,
    kpisTotalPlays: D.kpis && D.kpis.total_plays,
    accountsIsArray: Array.isArray(D.accounts),
    accountsLen: D.accounts && D.accounts.length,
    seriesIsArray: Array.isArray(D.series),
    seriesLen: D.series && D.series.length,
    videosIsArray: Array.isArray(D.videos),
    videosLen: D.videos && D.videos.length,
    metaType: typeof D.meta,
    metaFrom: D.meta && D.meta.from,
    metaTo: D.meta && D.meta.to,
    metaDays: D.meta && D.meta.days,
    metaNVideos: D.meta && D.meta.nVideos,
    metaNAccounts: D.meta && D.meta.nAccounts,
    asOf: D.meta && D.meta.asOf,
    patternsType: typeof D.patterns,
    takeawaysIsArray: Array.isArray(D.takeaways),
    baselinesType: typeof D.baselines
  });
  dom.window.close();
} catch (e) {
  out.threw = String((e && (e.stack || e.message)) || e);
}
console.log(JSON.stringify(out));
"""

_PROBE_BENCH = _PROBE_PRELUDE + r"""
try {
  const emptyPayload = makeEmptyPayload();
  out.inputs = { accountsLen: emptyPayload.accounts.length, videosLen: emptyPayload.videos.length };
  const dom = new JSDOM('<!doctype html><html><body></body></html>', { runScripts: 'outside-only' });
  const w = dom.window;
  // 与 static/index.html 的加载顺序一致：derive.js -> bcore.js -> bench.js
  w.eval(readStatic('derive.js'));
  w.eval(readStatic('bcore.js'));
  w.eval(readStatic('bench.js'));
  const D = w.BENCH_DERIVE(clone(emptyPayload));
  out.result.emptyInput = { seriesLen: D.series.length, videosLen: D.videos.length };
  out.result.buType = typeof w.BU;
  function probe(label, fn) {
    try {
      const html = fn();
      return {
        ok: true, type: typeof html, len: typeof html === 'string' ? html.length : -1,
        hasUnit: typeof html === 'string' && html.indexOf('data-unit="' + label + '"') >= 0,
        emptyText: typeof html === 'string' && /暂无/.test(html)
      };
    } catch (e) {
      return { ok: false, err: String((e && (e.stack || e.message)) || e) };
    }
  }
  out.result.trendN0 = probe('trend', () => w.BU.trend(D, { n: 0, kind: 'bars' }));
  out.result.trendN20 = probe('trend', () => w.BU.trend(D, { n: 20, kind: 'bars' }));
  out.result.topvideosN3 = probe('topvideos', () => w.BU.topvideos(D, { n: 3 }));
  dom.window.close();
} catch (e) {
  out.threw = String((e && (e.stack || e.message)) || e);
}
console.log(JSON.stringify(out));
"""


def _slashes(p):
    return str(p).replace("\\", "/")


def _probe_result(script):
    node = _node_exe()
    rc, text, out, err = _run([node, str(script)], cwd=_WEBAPP, timeout=120,
                              env=_child_env(QA_WEBAPP=_slashes(_WEBAPP), QA_JSDOM=_slashes(_JSDOM)))
    assert_(rc == 0, "探针子进程退出码 " + str(rc) + "（期望 0）：" + _tail(err or text, 600))
    try:
        payload = json.loads(out.strip().splitlines()[-1])
    except Exception:
        raise AssertionError("探针没有输出可解析的 JSON；stdout 尾部：" + _tail(out, 600) +
                             "；stderr 尾部：" + _tail(err, 300))
    return payload


# ================================================================ C-03
# DoD 2 前段：空载荷 derive() 不抛异常，KPI / 账号行 / 趋势桶 / 7 日切点四类字段各至少一条。
def case_03_empty_payload_derive_fields():
    payload = _probe_result(_write_child("probe_derive_empty.js", _PROBE_DERIVE))
    assert_(payload.get("threw") in (None, ""),
            "空载荷 derive() 抛异常了： " + str(payload.get("threw"))[:600])
    r = payload["result"]
    inp = payload["inputs"]
    assert_(inp.get("accountsLen") == 0 and inp.get("videosLen") == 0,
            "探针喂入的不是空载荷（accounts/videos 应为空数组），实得 " + json.dumps(inp, ensure_ascii=False))
    assert_(r.get("deriveType") == "function", "derive.js 没把 derive() 挂到 window.BENCH_DERIVE（实得 " +
            str(r.get("deriveType")) + "）")

    # 1) KPI
    assert_(r.get("kpisType") == "object", "返回对象缺 KPI（kpis 不是对象）")
    assert_(r.get("kpisAccounts") == 0 and r.get("kpisVideos") == 0,
            "空载荷下 KPI 没有原样带出：accounts=" + str(r.get("kpisAccounts")) +
            " videos=" + str(r.get("kpisVideos")))
    # 2) 账号行
    assert_(r.get("accountsIsArray") is True, "返回对象的 accounts 不是数组")
    assert_(r.get("accountsLen") == 0, "空载荷下 accounts 应为空数组，实得长度 " + str(r.get("accountsLen"))) 
    # 3) 趋势桶
    assert_(r.get("seriesIsArray") is True, "返回对象的 series 不是数组")
    assert_(r.get("seriesLen") == 0, "空载荷下 series 应为空数组，实得长度 " + str(r.get("seriesLen")))
    # 4) 7 日切点（meta.from/to/days + 视频列表）
    assert_(r.get("metaType") == "object", "返回对象缺 meta（7 日切点所在字段）")
    assert_(r.get("metaFrom") == "", "空载荷下 meta.from 应为空串，实得 " + json.dumps(r.get("metaFrom"), ensure_ascii=False))
    assert_(r.get("metaTo") == "", "空载荷下 meta.to 应为空串，实得 " + json.dumps(r.get("metaTo"), ensure_ascii=False))
    assert_(r.get("metaDays") == 0, "空载荷下 meta.days 应为 0，实得 " + str(r.get("metaDays")))
    assert_(r.get("videosIsArray") is True and r.get("videosLen") == 0,
            "返回对象的 videos 不是空数组（长度 " + str(r.get("videosLen")) + "）")
    assert_(r.get("asOf") is not None and re.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$", str(r.get("asOf"))),
            "空载荷下 meta.asOf 不是合法时间串（Invalid Date 会在这里露头）：" +
            json.dumps(r.get("asOf"), ensure_ascii=False))

    print("        证据：独立 jsdom 探针加载真实 derive.js；输入 accounts=[] videos=[]；"
          "KPI/账号行/趋势桶/7 日切点四类字段逐个断言通过（asOf=" + str(r.get("asOf")) + "）")


# ================================================================ C-04
# DoD 2 追加（2026-11-05）：bench.js 的 trend() / topvideos() 在空 series / 空列表下不抛异常。
def case_04_bench_units_empty_data():
    payload = _probe_result(_write_child("probe_bench_empty.js", _PROBE_BENCH))
    assert_(payload.get("threw") in (None, ""),
            "加载 bench.js 或空数据渲染抛异常了： " + str(payload.get("threw"))[:600])
    r = payload["result"]
    assert_(r.get("emptyInput") == {"seriesLen": 0, "videosLen": 0},
            "探针的输入不是空 series / 空视频列表：" + json.dumps(r.get("emptyInput"), ensure_ascii=False))
    assert_(r.get("buType") == "object", "bench.js 没把渲染单元挂到 window.BU（实得 " + str(r.get("buType")) + "）")
    for key, label in (("trendN0", "trend(n=0)"), ("trendN20", "trend(n=20)"), ("topvideosN3", "topvideos(n=3)")):
        item = r.get(key) or {}
        assert_(item.get("ok") is True,
                label + " 在空数据下抛异常了：" + str(item.get("err"))[:400])
        assert_(item.get("hasUnit") is True and item.get("len", -1) > 0,
                label + " 没产出对应区块（data-unit 缺失或空串）：len=" + str(item.get("len")) +
                " hasUnit=" + str(item.get("hasUnit")))
    print("        证据：独立 jsdom 探针加载真实 derive.js/bcore.js/bench.js，空 series / 空 videos 下："
          "trend(n=0) / trend(n=20) / topvideos(n=3) 均未抛异常，且各自产出 data-unit 区块"
          "（空态文案命中=" + str(r.get("trendN0", {}).get("emptyText")) + "/" +
          str(r.get("topvideosN3", {}).get("emptyText")) + "）")


# ================================================================ C-05
_BROWSER_PROBE = r"""
const fs = require('fs');
const path = require('path');
const { chromium } = require(process.env.QA_PLAYWRIGHT);
const out = { launchError: null, navError: null, pageErrors: [], consoleErrors: [], badResponses: [] };
const NEEDLES = [
  '\u8bfb\u53d6 /api/benchmark \u5931\u8d25\uff1aInvalid time value',
  "Cannot read properties of undefined (reading 'date')",
  "Cannot read properties of undefined (reading 'plays')"
];
const EMPTY_TEXTS = ['\u6682\u65e0\u53d1\u5e03\u6570\u636e', '\u6682\u65e0\u89c6\u9891\u6570\u636e'];
const EXE = path.join(process.env.LOCALAPPDATA || '', 'ms-playwright', 'chromium-1248', 'chrome-win64', 'chrome.exe');
out.exe = EXE;
out.exeExists = fs.existsSync(EXE);
(async () => {
  if (!out.exeExists) { console.log(JSON.stringify(out)); return; }
  let browser = null;
  try {
    browser = await chromium.launch({ executablePath: EXE, headless: true });
    const page = await browser.newPage();
    page.on('pageerror', (e) => out.pageErrors.push(String((e && (e.stack || e.message)) || e)));
    page.on('console', (m) => { if (m.type() === 'error') out.consoleErrors.push(String(m.text()).slice(0, 400)); });
    page.on('response', (r) => { if (r.status() >= 400) out.badResponses.push({ status: r.status(), url: r.url() }); });
    try {
      await page.goto(process.env.QA_URL, { waitUntil: 'load', timeout: 40000 });
    } catch (e) {
      out.navError = String((e && e.message) || e);
    }
    if (!out.navError) {
      try {
        await page.waitForFunction(() => {
          const h = document.getElementById('bench-s096');
          return !!(h && h.shadowRoot && h.shadowRoot.querySelectorAll('[data-unit]').length >= 10);
        }, null, { timeout: 25000 });
        await page.waitForFunction(() => {
          const h = document.getElementById('bench-s096');
          const l = h && h.shadowRoot && h.shadowRoot.querySelector('#b96-load');
          return !l || l.hidden === true;
        }, null, { timeout: 20000 });
      } catch (e) {
        out.navError = 'page did not finish rendering: ' + String((e && e.message) || e);
      }
      await page.waitForTimeout(1200);
      out.page = await page.evaluate((needles) => {
        const host = document.getElementById('bench-s096');
        const info = {
          url: location.href, title: document.title,
          host: !!host, shadow: !!(host && host.shadowRoot),
          units: [], unitTextLen: {}, unitEmpty: {},
          loadHidden: null, loadErr: null, loadText: null,
          shadowTextLen: 0, appTextLen: 0,
          needles: {}, empty: {},
          bodyVisible: (document.body.innerText || '').trim().length
        };
        if (host && host.shadowRoot) {
          const sr = host.shadowRoot;
          const units = Array.from(sr.querySelectorAll('[data-unit]'));
          info.units = units.map((el) => el.getAttribute('data-unit'));
          units.forEach((el) => {
            const u = el.getAttribute('data-unit');
            const t = (el.textContent || '').trim();
            info.unitTextLen[u] = t.length;
            info.unitEmpty[u] = t.length === 0;
          });
          const load = sr.querySelector('#b96-load');
          if (load) { info.loadHidden = load.hidden; info.loadErr = load.classList.contains('err'); info.loadText = (load.textContent || '').trim().slice(0, 200); }
          const st = (sr.textContent || '');
          info.shadowTextLen = st.length;
          const at = (document.body.innerText || '');
          info.appTextLen = at.length;
          needles.forEach((n) => { info.needles[n] = { shadow: st.indexOf(n) >= 0, app: at.indexOf(n) >= 0 }; });
          ['\u6682\u65e0\u53d1\u5e03\u6570\u636e', '\u6682\u65e0\u89c6\u9891\u6570\u636e', '\u6682\u65e0\u6570\u636e'].forEach((n) => {
            info.empty[n] = st.indexOf(n) >= 0;
          });
          // 「读取 /api/benchmark 失败：」与 Invalid time value 是否同页共现（哪怕拼法不同）
          info.errorPrefix = st.indexOf('\u8bfb\u53d6 /api/benchmark \u5931\u8d25') >= 0 || at.indexOf('\u8bfb\u53d6 /api/benchmark \u5931\u8d25') >= 0;
          info.invalidTime = st.indexOf('Invalid time value') >= 0 || at.indexOf('Invalid time value') >= 0;
        }
        return info;
      }, NEEDLES);
    }
    await browser.close();
  } catch (e) {
    out.launchError = String((e && (e.stack || e.message)) || e);
    try { if (browser) await browser.close(); } catch (e2) {}
  }
  console.log(JSON.stringify(out));
})();
"""


def _http_status(path, timeout=20):
    # 显式关掉代理：本机 127.0.0.1 不应该走系统代理，免得 README 里那句「服务在 8789」被代理搅局。
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    url = _BASE + path
    with opener.open(url, timeout=timeout) as r:
        return r.status, r.read()


def case_05_browser_empty_state_and_units():
    # 第 1 步：服务在 8789 且返回 200（这一步同时给出「空数据」条件是否成立）
    status, raw = _http_status("/api/benchmark")
    assert_(status == 200, "/api/benchmark 期望 200，实得 " + str(status))
    payload = json.loads(raw.decode("utf-8", "replace"))
    n_videos = len(payload.get("videos") or [])
    n_accounts = len(payload.get("accounts") or [])
    assert_(n_videos == 0 and n_accounts == 0,
            "本条要求空数据下的页面，但 /api/benchmark 返回 videos=" + str(n_videos) +
            " accounts=" + str(n_accounts) + " -> 空数据条件不成立（DoD 没写怎么把数据置空，需 Lead 澄清）")
    print("        证据：GET " + _BASE + "/api/benchmark -> HTTP 200，videos=0 accounts=0（空数据条件成立）")

    # 第 2 步：真浏览器打开 #bench
    probe = _write_child("probe_browser.js", _BROWSER_PROBE)
    node = _node_exe()
    rc, text, out, err = _run([node, str(probe)], cwd=_WEBAPP, timeout=180,
                              env=_child_env(QA_URL=_BASE + "/#bench", QA_PLAYWRIGHT=_slashes(_PLAYWRIGHT)))
    assert_(rc == 0, "浏览器探针子进程退出码 " + str(rc) + "（期望 0）：" + _tail(err or text, 800))
    try:
        info = json.loads(out.strip().splitlines()[-1])
    except Exception:
        raise AssertionError("浏览器探针没有输出可解析 JSON；stdout 尾部：" + _tail(out, 800))
    if info.get("launchError"):
        raise AssertionError("Chromium 启动失败：" + str(info["launchError"])[:600])
    if info.get("exeExists") is False:
        raise AssertionError("找不到 Chromium：" + str(info.get("exe")))
    assert_(not info.get("navError"), "页面没有正常打开/渲染完成：" + str(info.get("navError"))[:600])
    page = info.get("page") or {}
    assert_(page.get("shadow") is True, "没有找到 #bench-s096 的 Shadow DOM（页面没按 S096 渲染）")
    assert_("#bench" in str(page.get("url")), "地址不是 /#bench，实得 " + str(page.get("url")))

    # 三串报错一个都不能出现（页面正文 + Shadow DOM 都查）
    for needle, hit in (page.get("needles") or {}).items():
        assert_(not hit.get("shadow"), "页面上出现了 DoD 明令不许出现的字符串（Shadow DOM）：" + needle)
        assert_(not hit.get("app"), "页面上出现了 DoD 明令不许出现的字符串（正文）：" + needle)
    assert_(not (page.get("errorPrefix") and page.get("invalidTime")),
            "同一页同时出现「读取 /api/benchmark 失败」与「Invalid time value」")

    # Console：没有未捕获的 TypeError（那两串 TypeError 都属此类）
    for msg in info.get("pageErrors") or []:
        assert_("TypeError" not in msg, "Console 出现未捕获的 TypeError：" + msg[:400])
        for needle in page.get("needles") or {}:
            assert_(needle not in msg, "Console 出现 DoD 明令不许出现的报错：" + msg[:400])
    for msg in info.get("consoleErrors") or []:
        assert_("Uncaught TypeError" not in msg, "Console 出现未捕获的 TypeError：" + msg[:400])

    # 空态 + 10 个区块（清单来自 bench_s096.js renderAll() 的 10 个功能单元）
    empty = page.get("empty") or {}
    assert_(empty.get("暂无发布数据") and empty.get("暂无视频数据"),
            "空数据下没有显示空态：trend/topvideos 的空态文案命中情况 " +
            json.dumps({k: v for k, v in empty.items()}, ensure_ascii=False))
    assert_(page.get("loadHidden") is True, "页面加载/错误卡片仍在显示（loadHidden=" +
            str(page.get("loadHidden")) + "，err=" + str(page.get("loadErr")) + "）")
    units = page.get("units") or []
    lens = page.get("unitTextLen") or {}
    assert_(sorted(units) == sorted(_EXPECTED_UNITS),
            "期望 10 个区块 " + json.dumps(_EXPECTED_UNITS, ensure_ascii=False) +
            "，实得 " + str(len(units)) + " 个：" + json.dumps(units, ensure_ascii=False))
    empty_units = [u for u in units if not lens.get(u)]
    assert_(not empty_units, "这些区块内容区是空的（白屏/半截）：" + "、".join(empty_units) +
            "；内容长度 " + json.dumps(lens, ensure_ascii=False))
    assert_((page.get("shadowTextLen") or 0) > 300, "页面整体内容过少（疑似白屏），Shadow DOM 文本长度 " +
            str(page.get("shadowTextLen")))

    print("        证据：10 个区块齐全且有内容：" + json.dumps(lens, ensure_ascii=False))
    print("        证据：空态命中 " + json.dumps({k: v for k, v in empty.items() if v}, ensure_ascii=False) +
          "；未捕获 pageError 数=" + str(len(info.get("pageErrors") or [])))
    if info.get("consoleErrors"):
        print("        [note] Console 里另有非 TypeError 的报错（不计入本条判据，原样记录）：" +
              " | ".join(info["consoleErrors"][:6]))
    if info.get("badResponses"):
        print("        [note] 有非 2xx 的静态请求（原样记录，不计入本条判据）：" +
              json.dumps(info["badResponses"][:6], ensure_ascii=False))


# ================================================================ C-06
# 回归（非 DoD 条目）：本任务没把既有测试弄红。
#
# 判据改动（PM 裁决 2026-11-05，原委见 qa/gaps.md 第 21 条）：
#   计划原文要求 `qa\run-all.ps1` 退出码 0，但本仓库里这个字面判据**不可达** ——
#   qa/T-04 的 C-08 把「改动前」钉在提交 d58d9ed 上，而本仓库历史里没有这个提交；
#   C-10 又要求 pipeline/tests/test_publish_artifacts.py 全绿（实测 8/13，改前就这样）。
#   PM 因此把 C-06 改成**按归因判**（与 qa/T-12 的 C-07 同一口径）：
#     · 全套退出码照实打印，但不再要求 0；
#     · 失败输出里出现 T-11 拥有的文件/符号 -> 判红（这才是「本任务把别处弄红」）；
#     · 基线里就有的失败目录（T-04，见 gaps #21）不算 T-11 的；
#     · 比基线新增的失败目录打印出来做证据，只有失败行归因到 T-11 才算红；
#     · T-11 自己的入口在全套里被判红/缺入口 -> 判红。
#   基线：qa/T-11 目录建起来之前（2026-11-05）实测的一遍 run-all = 通过 2 / 失败 1，只有 T-04 红。
_BASELINE_FAILING = ("T-04",)
_BASELINE_NOTE = "qa/T-11 建目录前实测：通过 2 / 失败 1（T-04(exit=1)，T-04 自己的 C-08/C-10 红，见 qa/gaps.md #21）"
_T11_ATTRIB = ("derive.js", "bench.js", "bench_empty_data.test.js",
               "BENCH_DERIVE", "BU.trend", "BU.topvideos", "bench_empty_data")


def case_06_repo_qa_runner_attribution():
    ps = _powershell()
    # QA_RUNALL_NESTED=1 是给 T-12/T-13 的共享标记：那一层里它们的回归用例不再各自再起 run-all。
    # QA_T11_NESTED 保留，给 run.ps1 自己那道护栏用（整目录跳过，避免重复跑本目录）。
    env = _child_env(QA_T11_NESTED="1", QA_RUNALL_NESTED="1")
    rc, text, out, err = _run([ps, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(_QA_RUNALL)],
                              cwd=_REPO, timeout=_TIMEOUT, env=env)
    # run-all 自己的失败行锚定行首：别的用例在失败原因里**引用**「[FAIL] T-04 exit=1」这种字符串不算。
    failing_ids = [m.group(1) for m in re.finditer(
        r"(?m)^\[FAIL\]\s+([A-Za-z0-9_.\-]+)\s+exit=(\d+)", text)]
    no_entry = []
    m = re.search(r"(?m)^\s*没有入口：(.+)$", text)
    if m:
        no_entry = [x.strip() for x in m.group(1).split(",") if x.strip()]
    totals = [ln.strip() for ln in text.splitlines() if "QA 用例" in ln]
    summary = totals[-1] if totals else "（没有汇总行）"
    new_ids = [x for x in failing_ids if x not in _BASELINE_FAILING]

    print("        证据：qa\\run-all.ps1 退出码 " + str(rc) + "；" + summary)
    print("        证据：失败目录=" + (json.dumps(failing_ids, ensure_ascii=False) if failing_ids else "无") +
          "；缺入口目录=" + (json.dumps(no_entry, ensure_ascii=False) if no_entry else "无"))
    print("        证据：基线（" + _BASELINE_NOTE + "）；比基线新增的失败目录=" +
          (json.dumps(new_ids, ensure_ascii=False) if new_ids else "无"))

    assert_(failing_ids or no_entry or rc == 0,
            "全套退出码 " + str(rc) + "，但输出里既解析不到 [FAIL] 行也解析不到『没有入口』"
            "（不认识的失败形状，不能当通过）：" + _tail(text, 600))
    assert_(totals, "输出里没有 qa\\run-all.ps1 的汇总行，无法确认总跑器真的跑完")
    assert_("T-11" not in failing_ids,
            "T-11 自己的入口在全套里被判红（exit!=0）：" + json.dumps(failing_ids, ensure_ascii=False))
    assert_("T-11" not in no_entry,
            "T-11 自己的入口在全套里被判成『没有入口』：" + json.dumps(no_entry, ensure_ascii=False))

    # 归因：只认「出现在失败形态行里」的 T-11 文件/符号；别的用例在通过行里提到不算。
    hits = []
    for ln in text.splitlines():
        if any(t in ln for t in _T11_ATTRIB) and re.search(
                r"\[FAIL\]|\bFAIL\b|\u2717|Traceback|Error|exit\s*=\s*[1-9]", ln):
            hits.append(ln.strip()[:200])
    print("        证据：失败行里提到 T-11 拥有的文件/符号=" +
          (json.dumps(hits, ensure_ascii=False) if hits else "无"))
    assert_(not hits, "失败输出里出现了 T-11 拥有的文件/符号，说明本任务的改动让别处变红了：" +
            json.dumps(hits, ensure_ascii=False))

    if new_ids:
        # 新增的失败目录不归因到 T-11 就不算 T-11 的红（同 qa/T-12 的 C-07）。
        detail = []
        for d in new_ids:
            m2 = re.search(r"(?m)^\[FAIL\]\s+" + re.escape(d) + r"\s+exit=(\d+)", text)
            code = m2.group(1) if m2 else "?"
            tail = ""
            if d == "T-13":
                vm = re.search(r"ValueError: .+", text)
                if vm:
                    tail = "（其自身 cases.py 报 " + vm.group(0).strip()[:80] + "）"
            detail.append(d + "(exit=" + code + ")" + tail)
        print("        [note] 比基线新增的失败目录不是 T-11 造成的，原样记录：" + "、".join(detail))
    if rc != 0:
        print("        [note] 全套退出码非 0 属已知基线（" + _BASELINE_NOTE + "）与别的任务的目录；"
              "C-06 按 PM 裁决只看归因，不按字面 exit 0。")
    print("        证据：T-11 拥有的文件/符号在失败行里 0 命中，T-11 自身入口未被判红 -> 归因通过")


def case_06_nested_skip():
    """嵌套调用（QA_RUNALL_NESTED 已设）里不再起 run-all：回归检查由外层那一轮负责。"""
    print("        证据：本次进程带 QA_RUNALL_NESTED=1（外层 run-all 的嵌套调用），"
          "不再重复发起 qa\\run-all.ps1；回归检查由外层负责")


C06 = (("C-06", "(nested run-all: 跳过自身回归检查，由外层负责)", case_06_nested_skip)
       if _NESTED_RUNALL else
       ("C-06", "qa\\run-all.ps1 回归按归因判：没有失败行指向 T-11 的文件/符号",
        case_06_repo_qa_runner_attribution))

CASES = [
    ("C-01", "新用例存在且当前为绿", case_01_new_test_exists_and_green),
    ("C-02", "报告/提交信息里有改前红、改后绿两段真实输出", case_02_before_red_and_after_green_in_report),
    ("C-03", "空载荷 derive() 不抛异常且返回字段齐全（独立探针）", case_03_empty_payload_derive_fields),
    ("C-04", "bench.js 的 trend()/topvideos() 空数据不抛异常（独立探针）", case_04_bench_units_empty_data),
    ("C-05", "浏览器实测空数据空态、三串报错不出现、10 个区块渲染", case_05_browser_empty_state_and_units),
    C06,
]


def main():
    print("T-11 用例（qa/T-11/cases.py，清单见 .dsh-crew/jobs/fix-check-findings/T-11-plan.md）：")
    start = time.time()
    try:
        for cid, name, fn in CASES:
            _run_case(cid, name, fn)
    finally:
        if _TMP is not None:
            shutil.rmtree(_TMP, ignore_errors=True)
    total = _ok + _fail
    print("")
    print("T-11 RESULT: " + str(_ok) + "/" + str(total) + " ok, exit " + str(0 if _fail == 0 else 1) +
          "（用时 " + str(round(time.time() - start)) + " 秒）")
    return 0 if _fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
