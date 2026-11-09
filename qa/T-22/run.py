# -*- coding: utf-8 -*-
r"""qa/T-22/run.py —— T-22「一条命令产出两个 ZIP」独立验收（11 条：10 实现 + 1 入口）。

用例清单：`.dsh-crew/jobs/portable-package/M3-qa-plan.md` 的 T-22 段。
判据以 `docs/tasks/tasks.md` T-22 DoD 与 `docs/design/api/builder-portable.md` §9 为准。

模式：默认用 `--skip-build` 复用 `build\portable\{slim,full}` 现有 stage，
两个 ZIP 由本次 `make_zips.py` 重新生成（体积取本次 `Path.stat()` 的真实值）；
stage 不完整时自动退回真构建。脚本开头打印实际模式。

不碰 8789：包级用例只用 879x（默认 8792）；只停自己起的进程。
M3 DoD 4 因 slim 无模型，按裁决只验「启动 + 台账」，转写未验 → 记 PARTIAL（单列，不冒充 PASS）。

跑法（仓库根）：`pipeline\.venv\Scripts\python.exe qa\T-22\run.py`
全过（含 PARTIAL）退出 0；任一条 FAIL 退出 1；无 SKIP。
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    try:
        if not _stream.isatty():
            _stream.reconfigure(encoding="utf-8", errors="replace")
        else:
            _stream.reconfigure(errors="replace")
    except Exception:                                              # noqa: BLE001
        pass

REPO = Path(__file__).resolve().parents[2]
PIPELINE_PY = sys.executable
WEBAPP_PY = REPO / "webapp" / ".venv" / "Scripts" / "python.exe"
MAKE_ZIPS = REPO / "packaging" / "make_zips.py"
ZIPS_TEST = REPO / "packaging" / "tests" / "make_zips.test.py"
BUILD = REPO / "build" / "portable"
DIST = REPO / "dist"
VERSION = (REPO / "VERSION").read_text(encoding="utf-8").strip()
BUDGET = {"full": 2_040_109_466, "slim": 524_288_000}
FIELDS = ("flavor", "zip_name", "zip_bytes", "unpacked_bytes", "file_count",
          "sha256", "budget_bytes", "over_budget")
REQUIRED = ("启动工作台.bat", "_setenv.bat", "VERSION", "python/python.exe",
            "ffmpeg/bin/ffmpeg.exe", "webapp/app.py", "pipeline/pipeline.py")
PORT_CANDIDATES = (8791, 8792, 8793)

WORK = Path(tempfile.mkdtemp(prefix="qa_t22_", dir="D:\\"))
PKG_PARENT = Path(r"D:\测试 目录")
PKG_ROOT = PKG_PARENT / "工作台"
PKG_LOCK = PKG_PARENT / ".qa-t22.lock"
RUN_LOCK = PKG_PARENT / ".qa-t22-run.lock"
PARTIAL = "PARTIAL"
RESULTS = []


def assert_(cond, msg="assert failed"):
    if not cond:
        raise AssertionError(msg)


def _run(cmd, cwd=None, timeout=1800, env=None):
    child_env = dict(os.environ if env is None else env)
    child_env["PYTHONIOENCODING"] = "utf-8"          # 子进程输出统一 UTF-8，避免 GBK 乱码
    child_env["PYTHONUTF8"] = "1"
    p = subprocess.run([str(c) for c in cmd], cwd=str(cwd or REPO), timeout=timeout,
                       capture_output=True, env=child_env, text=True,
                       encoding="utf-8", errors="replace")
    return p.returncode, (p.stdout or "") + "\n" + (p.stderr or "")


def _busy_builders() -> list:
    """返回正在跑的 build_portable / make_zips 进程号（排除自己）。"""
    script = ("Get-CimInstance Win32_Process -Filter \"Name='python.exe' or Name='pythonw.exe'\" | "
              "Where-Object { $_.CommandLine -match 'build_portable|make_zips' } | "
              "Select-Object -ExpandProperty ProcessId")
    try:
        proc = subprocess.run(["powershell", "-NoProfile", "-Command", script],
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=60)
    except Exception:                                              # noqa: BLE001
        return []
    return [int(x) for x in proc.stdout.split()
            if x.strip().isdigit() and int(x) != os.getpid()]


def _wait_builders_quiet(ev, timeout: float = 1800.0) -> None:
    deadline = time.time() + timeout
    noted = False
    while time.time() < deadline:
        pids = _busy_builders()
        if not pids:
            return
        if not noted:
            ev.append("另一个构建/压包进程在跑（pid %r），等它结束" % pids)
            noted = True
        time.sleep(10.0)
    raise AssertionError("等 %.0f 秒仍有构建进程在跑：%r" % (timeout, _busy_builders()))


def _stage_ready(stage: Path) -> bool:
    return ((stage / ".build-portable-stage").is_file()
            and (stage / "build-info.json").is_file()
            and all((stage / rel).is_file() for rel in REQUIRED))


def _wait_stages_ready(ev, timeout: float = 1800.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if all(_stage_ready(BUILD / f) for f in ("full", "slim")):
            ev.append("stage 完整（marker + build-info + 7 个必含文件）")
            return
        time.sleep(10.0)
    missing = [f for f in ("full", "slim") if not _stage_ready(BUILD / f)]
    raise AssertionError("等 %.0f 秒 stage 仍不完整：%r（另一个构建可能还在跑）" % (timeout, missing))


def _acquire_run_lock(wait: float = 1800.0) -> None:
    """同一个 QA 脚本的并发实例串行化（旧实例不认这个锁，只能靠调用方别并发）。"""
    deadline = time.time() + wait
    while True:
        try:
            fd = os.open(str(RUN_LOCK), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode("ascii"))
            os.close(fd)
            return
        except FileExistsError:
            try:
                if time.time() - RUN_LOCK.stat().st_mtime > 1800:
                    RUN_LOCK.unlink(missing_ok=True)
                    continue
            except OSError:
                pass
            if time.time() > deadline:
                raise AssertionError("等 T-22 运行锁 %.0f 秒超时：%s" % (wait, RUN_LOCK))
            time.sleep(5.0)


def _acquire_pkg_lock(wait: float = 900.0) -> None:
    """串行化『解压到 D:\\测试 目录\\工作台』这一步，避免并发跑互相清目录。"""
    deadline = time.time() + wait
    while True:
        try:
            fd = os.open(str(PKG_LOCK), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode("ascii"))
            os.close(fd)
            return
        except FileExistsError:
            try:
                if time.time() - PKG_LOCK.stat().st_mtime > 1800:   # 30 分钟视为陈旧锁
                    PKG_LOCK.unlink(missing_ok=True)
                    continue
            except OSError:
                pass
            if time.time() > deadline:
                raise AssertionError("等 %s 超过 %.0f 秒，另一个 T-22 实例还占着" % (PKG_LOCK, wait))
            time.sleep(2.0)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _zip_names(path: Path) -> list:
    with zipfile.ZipFile(path) as zf:
        return zf.namelist()


def _http_get(port: int, path: str, timeout=10):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open("http://127.0.0.1:%d%s" % (port, path), timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:                          # noqa: F821
        return exc.code, exc.read()
    except Exception:                                              # noqa: BLE001
        return None, b""


def _free_port() -> int:
    for port in PORT_CANDIDATES:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise AssertionError("候选端口 %r 全被占用，起不了包级自测实例" % (PORT_CANDIDATES,))


def _stripped_env() -> dict:
    env = dict(os.environ)
    for key in list(env):
        if key.startswith("WB_") or key.startswith("HF_") or key.startswith("PYTHON"):
            env.pop(key, None)
        elif key in ("HUGGINGFACE_HUB_CACHE", "XDG_CACHE_HOME"):
            env.pop(key, None)
    system_root = env.get("SystemRoot") or r"C:\Windows"
    env["PATH"] = system_root + r"\system32;" + system_root
    empty = WORK / "_empty_temp"
    empty.mkdir(parents=True, exist_ok=True)
    env["TEMP"] = str(empty)
    env["TMP"] = str(empty)
    return env


def _wait_health(port: int, timeout: float = 120.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        status, _raw = _http_get(port, "/api/health", timeout=3)
        if status == 200:
            return True
        time.sleep(1.0)
    return False


def _make_xlsx(path: Path) -> None:
    from openpyxl import Workbook

    path.parent.mkdir(parents=True, exist_ok=True)
    wb = Workbook()
    ws = wb.active
    ws.append(["列%d" % i for i in range(1, 15)])                    # 14 列
    ws.append(["acc1"] + [""] * 13)
    wb.save(str(path))


def _ensure_extracted(force: bool = False) -> Path:
    """解压 slim ZIP 到 D:\\测试 目录\\工作台\\，返回包根（ZIP 内唯一顶层目录）。"""
    if PKG_ROOT.exists() and not force:
        return PKG_ROOT / ("tiktok-workbench-%s-slim" % VERSION)
    if PKG_ROOT.exists():
        shutil.rmtree(PKG_ROOT)
    PKG_PARENT.mkdir(parents=True, exist_ok=True)
    PKG_ROOT.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(DIST / ("tiktok-workbench-%s-slim.zip" % VERSION)) as zf:
        zf.extractall(PKG_ROOT)
    roots = [p for p in PKG_ROOT.iterdir() if p.is_dir()]
    assert_(len(roots) == 1, "解压后顶层目录不是唯一一个：%r" % [p.name for p in roots])
    return roots[0]


# ────────────────────────────── 用例 ──────────────────────────────
def _clean_stage_junk(ev) -> None:
    """清掉 stage 里别的测试运行后留下的运行产物（不是构建内容；build_portable 本来就不产出它们）。

    并发 QA 可能从 `build/portable/<flavor>` 直接起包，写出 logs/*.log、reverse/_uploads、
    __pycache__ —— 不清掉的话 `--skip-build` 会把它们打进用户的 ZIP（DoD 4 会红）。
    """
    removed = []
    for flavor in ("full", "slim"):
        stage = BUILD / flavor
        if not stage.is_dir():
            continue
        for root, dirs, files in os.walk(stage, topdown=True):
            root_path = Path(root)
            rel = root_path.relative_to(stage).as_posix()
            if rel == "python" or rel.startswith("python/"):
                continue                                          # 随包 Python 的 __pycache__ 是构建内容，不动
            if "__pycache__" in dirs:
                shutil.rmtree(root_path / "__pycache__", ignore_errors=True)
                removed.append(rel + "/__pycache__")
                dirs.remove("__pycache__")
            if rel == "reverse" and "_uploads" in dirs:
                shutil.rmtree(root_path / "_uploads", ignore_errors=True)
                removed.append(rel + "/_uploads")
                dirs.remove("_uploads")
            for name in list(files):
                if name == "README-data.txt":
                    continue
                if name.endswith((".pyc", ".pyo", ".log", ".tmp")):
                    (root_path / name).unlink(missing_ok=True)
                    removed.append(rel + "/" + name)
    if removed:
        ev.append("清掉 stage 里并发测试留下的运行产物 %d 项：%r" % (len(removed), removed[:8]))
    else:
        ev.append("stage 干净：没有运行产物")


def c01(ev):
    """1. 一条命令产出四个产物；文件名版本与根 VERSION 逐字相同。"""
    _wait_builders_quiet(ev)
    _wait_stages_ready(ev)
    _clean_stage_junk(ev)
    for stale in DIST.glob("*.tmp"):
        stale.unlink(missing_ok=True)
    stages_ok = all((BUILD / f / ".build-portable-stage").is_file() and
                    (BUILD / f / "build-info.json").is_file() for f in ("full", "slim"))
    cmd = [WEBAPP_PY, MAKE_ZIPS, "--flavor", "both"]
    if stages_ok:
        cmd.append("--skip-build")
    started = time.time()
    attempts = 0
    for attempt in range(1, 5):
        attempts = attempt
        rc, out = _run(cmd, REPO, timeout=1800)
        if rc == 0:
            break
        # 并发构建/杀软扫描可能临时锁住 .tmp；等几秒重试，不掩盖真正的失败。
        if "WinError 32" in out or "另一个程序正在使用此文件" in out:
            time.sleep(10)
            continue
        break
    elapsed = time.time() - started
    mode = "--skip-build（复用现有 stage；ZIP 本次重新生成）" if stages_ok else "真构建"
    assert_(rc == 0, "make_zips 退出码 %s（模式 %s，尝试 %d 次）；输出尾部：%s"
            % (rc, mode, attempts, out[-800:]))
    assert_("[OK] 产出" in out, "make_zips 没有打印 [OK] 产出：%s" % out[-500:])
    expected = sorted(["tiktok-workbench-%s-full.zip" % VERSION,
                       "tiktok-workbench-%s-slim.zip" % VERSION,
                       "SHA256SUMS.txt", "build-report.json"])
    # 并发跑 T-22 时另一个 make_zips 可能正在写 *.tmp：等它落定（最多 30 秒），不把别人的中间态算成失败。
    deadline = time.time() + 30
    while True:
        names = sorted(p.name for p in DIST.iterdir() if p.is_file())
        if names == expected or time.time() > deadline:
            break
        time.sleep(1)
    assert_(names == expected,
            "dist 产物不是恰好这四个：%r（期望 %r）" % (names, expected))
    ev.append("命令：%s（%s，%.1f 秒）" % (" ".join(str(c) for c in cmd), mode, elapsed))
    ev.append("VERSION=%r；dist 四个产物：%r" % (VERSION, names))


def c02(ev):
    """2. 体积预算、报告字段、报告与实测一致。"""
    report = json.loads((DIST / "build-report.json").read_text(encoding="utf-8"))
    assert_(report.get("version") == VERSION,
            "报告 version=%r，期望 %r" % (report.get("version"), VERSION))
    flavors = report.get("flavors") or []
    assert_(len(flavors) == 2, "报告 flavors 不是 2 项：%r" % (flavors,))
    by_flavor = {item.get("flavor"): item for item in flavors}
    assert_(set(by_flavor) == {"full", "slim"}, "报告 flavor 不是 full/slim：%r" % (set(by_flavor),))
    for flavor in ("full", "slim"):
        item = by_flavor[flavor]
        missing = [name for name in FIELDS if name not in item]
        assert_(not missing, "%s 报告缺字段：%r" % (flavor, missing))
        zip_path = DIST / item["zip_name"]
        assert_(zip_path.is_file(), "%s ZIP 不存在：%s" % (flavor, zip_path))
        actual = zip_path.stat().st_size
        assert_(item["zip_bytes"] == actual,
                "%s 报告 zip_bytes=%r 与实测 %d 不一致" % (flavor, item["zip_bytes"], actual))
        assert_(item["budget_bytes"] == BUDGET[flavor],
                "%s budget_bytes=%r，期望 %d" % (flavor, item["budget_bytes"], BUDGET[flavor]))
        assert_(actual <= BUDGET[flavor],
                "%s 实测 %d B 超预算 %d B" % (flavor, actual, BUDGET[flavor]))
        assert_(item["over_budget"] is False, "%s over_budget 不是 false" % flavor)
        assert_(item["unpacked_bytes"] > 0 and item["file_count"] > 0,
                "%s unpacked_bytes/file_count 不是正数" % flavor)
        assert_(re.fullmatch(r"[0-9a-f]{64}", item["sha256"] or ""),
                "%s 报告 sha256 格式不对" % flavor)
        ev.append("%s: zip_bytes=%d（上限 %d）、unpacked_bytes=%d、file_count=%d、sha256=%s…"
                  % (flavor, actual, BUDGET[flavor], item["unpacked_bytes"],
                     item["file_count"], item["sha256"][:12]))


def c03(ev):
    """3. 超限分支：退出码 2，且同时打印实际字节与上限 524288000。

    新护栏：`--out` / `--stage-root` 只允许 <repo>\\build、系统 temp（`--out` 另允许 <repo>\\dist）。
    所以假 stage 和假 out 都放 `tempfile.mkdtemp()`（默认在 %TEMP% 下），不碰 D: 盘根。
    """
    fake_root = Path(tempfile.mkdtemp(prefix="qa_t22_c03_"))
    stage = fake_root / "slim"
    stage.mkdir(parents=True, exist_ok=True)
    # 故意写成非 build_portable 的 marker：产品对"测试用假 stage"不做仓库 drift 比对
    # （_stage_was_built 只认以 "built by packaging/build_portable.py" 开头的 marker）。
    (stage / ".build-portable-stage").write_text(
        "fake stage for QA over-budget test\n", encoding="utf-8")
    shutil.copy2(BUILD / "slim" / "build-info.json", stage / "build-info.json")
    for rel in REQUIRED:
        target = stage / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"")
    padding = stage / "padding.bin"
    with open(padding, "wb") as fh:
        for _ in range(600):                                       # 600 MiB 不可压缩
            fh.write(os.urandom(1024 * 1024))
    fake_out = fake_root / "fake_dist"
    rc, out = _run([WEBAPP_PY, MAKE_ZIPS, "--flavor", "slim",
                    "--stage-root", str(fake_root), "--skip-build", "--out", str(fake_out)],
                   REPO, timeout=900)
    assert_(rc == 2, "超预算时退出码 %r，期望 2；输出尾部：%s" % (rc, out[-600:]))
    assert_("524288000" in out, "超限输出没有打印上限 524288000：%s" % out[-400:])
    match = re.search(r"超过预算：(\d+) B > 524288000 B", out)
    assert_(match and int(match.group(1)) > 524288000,
            "超限输出没有同时打印『实际值 > 上限』：%s" % out[-400:])
    ev.append("桩 stage 600 MiB 随机数据（系统临时目录下）；退出码 2；输出：%s" % match.group(0))
    shutil.rmtree(fake_root, ignore_errors=True)


def c04(ev):
    """4. ZIP 结构 (a)–(e)（取 slim）。"""
    zip_path = DIST / ("tiktok-workbench-%s-slim.zip" % VERSION)
    names = _zip_names(zip_path)
    nameset = set(names)
    top = "tiktok-workbench-%s-slim/" % VERSION
    tops = {name.split("/")[0] for name in names}
    assert_(tops == {top.rstrip("/")}, "顶层目录不唯一：%r" % (tops,))
    assert_(all(name.startswith(top) for name in names), "有条目不在顶层目录下")
    missing = [rel for rel in REQUIRED if top + rel not in nameset]
    assert_(not missing, "ZIP 缺必含文件：%r" % (missing,))
    assert_(top + "pipeline/accounts.json" not in nameset, "ZIP 含 pipeline/accounts.json")
    assert_(top + "webapp/data/runs.jsonl" not in nameset, "ZIP 含 webapp/data/runs.jsonl")
    xlsx = [name for name in names if name.lower().endswith(".xlsx")]
    assert_(not xlsx, "ZIP 含 xlsx：%r" % (xlsx[:3],))
    venv = [name for name in names if ".venv" in name.split("/")]
    assert_(not venv, "ZIP 含 .venv：%r" % (venv[:3],))
    ledger_dir = top + "ledger/"
    ledger_files = [name for name in names if name.startswith(ledger_dir) and name != ledger_dir]
    assert_(ledger_dir in nameset or ledger_files, "ZIP 里没有 ledger/ 目录条目")
    assert_(all(name == ledger_dir + "README-data.txt" for name in ledger_files),
            "ledger/ 里不是只有 README-data.txt：%r" % (ledger_files[:5],))
    marker = [name for name in names if name.endswith("/.build-portable-stage")]
    assert_(not marker, "ZIP 含 .build-portable-stage marker：%r" % (marker[:2],))
    ev.append("条目总数 %d；顶层=%r" % (len(names), sorted(tops)))
    ev.append("必含 7 项齐；不含 accounts.json/runs.jsonl/*.xlsx/.venv/marker；"
              "ledger/ 文件=%r" % (ledger_files,))


def c05(ev):
    """5. 两个包的差异只有模型：差集全在 models/ 下；full 有 model.bin，slim 没有。"""
    def rel_names(flavor):
        top = "tiktok-workbench-%s-%s/" % (VERSION, flavor)
        names = _zip_names(DIST / ("tiktok-workbench-%s-%s.zip" % (VERSION, flavor)))
        assert_(all(name.startswith(top) for name in names), "%s 有条目不在顶层目录下" % flavor)
        return {name[len(top):] for name in names}

    full = rel_names("full")
    slim = rel_names("slim")
    diff = full - slim
    back = slim - full
    assert_(diff, "full 与 slim 没有任何差异")
    assert_(not back, "slim 有 full 没有的条目（差异不只模型）：%r" % (sorted(back)[:5],))
    outside = [name for name in diff if not name.startswith("models/")]
    assert_(not outside, "差集不在 models/ 下：%r" % (outside[:5],))
    assert_(any(name.endswith("model.bin") for name in full), "full 里没有 model.bin")
    assert_(not any(name.endswith("model.bin") for name in slim), "slim 里不该有 model.bin")
    ev.append("差集 %d 条（全部在 models/ 下）；两侧 model.bin：full=%r slim=%r"
              % (len(diff), any(n.endswith("model.bin") for n in full),
                 any(n.endswith("model.bin") for n in slim)))
    ev.append("差集前 5 条：%r" % (sorted(diff)[:5],))


def c06(ev):
    """6. SHA256SUMS.txt 两行格式正确，且与两个 ZIP 的实测哈希一致。"""
    lines = [ln for ln in (DIST / "SHA256SUMS.txt").read_text(encoding="ascii").splitlines() if ln.strip()]
    assert_(len(lines) == 2, "SHA256SUMS.txt 不是 2 行：%r" % (lines,))
    recorded = {}
    for line in lines:
        match = re.fullmatch(r"([0-9a-f]{64})  (\S+)", line)
        assert_(match, "SHA256SUMS 行格式不对：%r" % line)
        recorded[match.group(2)] = match.group(1)
    expected = {"tiktok-workbench-%s-full.zip" % VERSION, "tiktok-workbench-%s-slim.zip" % VERSION}
    assert_(set(recorded) == expected, "SHA256SUMS 文件名不对：%r" % set(recorded))
    for name, digest in recorded.items():
        actual = _sha256(DIST / name)
        assert_(actual == digest, "%s 实测哈希 %s 与记录 %s 不一致" % (name, actual, digest))
        ev.append("%s  %s" % (digest, name))


def c07(ev):
    """7. 覆盖升级不丢数据（M3 DoD (c)）。"""
    pkg_root = _ensure_extracted(force=True)
    old = WORK / "old_pkg"
    if old.exists():
        shutil.rmtree(old)
    shutil.copytree(pkg_root, old)
    # 老包数据（4 个点位）
    accounts = {"accounts": [{"account": "acc1", "lang": "en", "active": True,
                             "out_dir": "pipeline/output", "audio_dir": "pipeline/output/acc1_audio"}]}
    accounts_path = old / "pipeline" / "accounts.json"
    accounts_path.parent.mkdir(parents=True, exist_ok=True)
    accounts_path.write_text(json.dumps(accounts, ensure_ascii=False, indent=2), encoding="utf-8")
    ledger_path = old / "ledger" / "台账-acc1.xlsx"
    _make_xlsx(ledger_path)
    scrape = old / "pipeline" / "output" / "scrape_acc1.json"
    scrape.parent.mkdir(parents=True, exist_ok=True)
    scrape.write_text(json.dumps({"account": "acc1", "items": []}), encoding="utf-8")
    runs = old / "webapp" / "data" / "runs.jsonl"
    runs.parent.mkdir(parents=True, exist_ok=True)
    runs.write_text("".join(json.dumps({"run": i}) + "\n" for i in (1, 2, 3)), encoding="utf-8")
    points = [accounts_path, ledger_path, scrape, runs]
    before = {str(p): _sha256(p) for p in points}
    (old / "VERSION").write_text("0.0.1\n", encoding="utf-8")
    old_python = old / "python" / "python.exe"
    old_stamp = 946684800                                          # 2000-01-01
    os.utime(old_python, (old_stamp, old_stamp))
    # 新包全部内容覆盖（等价 Copy-Item -Recurse -Force）
    shutil.copytree(pkg_root, old, dirs_exist_ok=True)
    after = {str(p): _sha256(p) for p in points}
    for path in points:
        assert_(before[str(path)] == after[str(path)],
                "覆盖后数据变了：%s（%s -> %s）" % (path, before[str(path)], after[str(path)]))
    version_now = (old / "VERSION").read_text(encoding="utf-8").strip()
    assert_(version_now == VERSION, "覆盖后 VERSION=%r，期望 %r" % (version_now, VERSION))
    new_mtime = old_python.stat().st_mtime
    assert_(abs(new_mtime - old_stamp) > 1,
            "覆盖后 python.exe 的 mtime 没变（还是 2000-01-01 的老值）")
    ev.append("4 个数据点位 sha256 前后一致：%r" % [p.name for p in points])
    ev.append("VERSION 0.0.1 -> %r；python.exe mtime 2000-01-01 -> %r"
              % (version_now, new_mtime))


def c08(ev):
    """8. M3 DoD 4（PARTIAL）：解压 slim → 剥环境启动 → 台账可用；转写因 slim 无模型未验。"""
    pkg_root = _ensure_extracted()
    env = _stripped_env()
    pkg_python = pkg_root / "python" / "python.exe"
    assert_(pkg_python.is_file(), "slim 包里没有 python\\python.exe")
    rc, out = _run([pkg_python, "first_run.py", "--portable", "--init-only"],
                   pkg_root, timeout=180, env=env)
    assert_(rc == 0, "first_run --init-only 退出码 %s：%s" % (rc, out[-500:]))
    port = _free_port()
    log = pkg_root / "logs" / "_server_run.log"
    server_env = dict(env)
    server_env["WB_LOG_DIR"] = str(pkg_root / "logs")
    proc = subprocess.Popen([str(pkg_python), "webapp/_boot.py", "--port", str(port)],
                            cwd=str(pkg_root), env=server_env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        healthy = _wait_health(port, timeout=120)
        if not healthy:
            tail = ""
            try:
                tail = log.read_text(encoding="utf-8", errors="replace")[-800:]
            except OSError:
                pass
            raise AssertionError("包在端口 %d 上 120 秒内没起来；日志尾部：%s" % (port, tail))
        status, raw = _http_get(port, "/api/health")
        health = json.loads(raw.decode("utf-8", "replace"))
        assert_(status == 200 and health.get("ok") is True,
                "/api/health 不是 200/ok=true：%r %r" % (status, health))
        status, raw = _http_get(port, "/api/config")
        config = json.loads(raw.decode("utf-8", "replace"))
        assert_(status == 200, "/api/config 状态码 %r" % (status,))
        detail = config.get("accounts_detail")
        assert_(isinstance(detail, list) and len(detail) >= 1,
                "/api/config 的 accounts_detail 不是非空列表：%r" % (detail,))
        status, raw = _http_get(port, "/api/ledger")
        assert_(status == 200, "/api/ledger 状态码 %r（期望 200）" % (status,))
        ledger = json.loads(raw.decode("utf-8", "replace"))
        ev.append("端口 %d /api/health 200 ok=true；/api/config accounts_detail=%d 个账号；"
                  "/api/ledger 200（%s）"
                  % (port, len(detail), type(ledger).__name__))
        ev.append("转写未验：slim 包不带模型（本用例只验『启动 + 台账』两项）")
        return PARTIAL
    finally:
        try:
            proc.kill()
            proc.wait(timeout=15)
        except Exception:                                          # noqa: BLE001
            pass


def c09(ev):
    """9. 构建产物不进 git：git status 里没有 dist/。"""
    rc, out = _run(["git", "-C", REPO, "status", "--short"], REPO, timeout=120)
    assert_(rc == 0, "git status 退出码 %r" % (rc,))
    lines = [ln for ln in out.splitlines() if ln.strip()]
    dist_lines = [ln for ln in lines if re.match(r"^\s*[AM?]{1,2}\s+dist[/\\]", ln)]
    assert_(not dist_lines, "git status 里有 dist/：%r" % (dist_lines[:5],))
    ev.append("git status 行数 %d；dist/ 命中 0" % len(lines))


def c10(ev):
    """10. make_zips.test.py 退出 0。"""
    rc, out = _run([WEBAPP_PY, ZIPS_TEST], REPO, timeout=900)
    assert_(rc == 0, "make_zips.test.py 退出码 %r：%s" % (rc, out[-800:]))
    assert_("Traceback" not in out, "make_zips.test.py 输出有 Traceback")
    ev.extend([ln for ln in out.splitlines() if ln.strip()][-3:])


CASES = [
    ("C-01", "一条命令产出四个产物（版本号文件名）", c01),
    ("C-02", "体积预算、报告字段、报告与实测一致", c02),
    ("C-03", "超限分支退出 2 并打印实际值与上限", c03),
    ("C-04", "ZIP 结构 (a)–(e)", c04),
    ("C-05", "两个包的差异只有模型", c05),
    ("C-06", "SHA256 复核", c06),
    ("C-07", "覆盖升级不丢数据（M3 DoD (c)）", c07),
    ("C-08", "M3 DoD 4：剥环境启动 + 台账（PARTIAL：转写未验）", c08),
    ("C-09", "构建产物不进 git", c09),
    ("C-10", "make_zips.test.py 通过", c10),
]


def _print_ev(ev):
    for line in ev:
        for sub in str(line).splitlines():
            if sub.strip():
                print("      " + sub.rstrip())


def main() -> int:
    stages_ok = all((BUILD / f / ".build-portable-stage").is_file() for f in ("full", "slim"))
    mode = ("--skip-build（复用 build\\portable\\{slim,full} 现有 stage；"
            "两个 ZIP 由本次 make_zips.py 重新生成）") if stages_ok else "真构建（stage 不完整）"
    print("qa/T-22/run.py —— T-22 两个 ZIP 独立验收（用例清单：M3-qa-plan.md T-22 1~11）")
    print("VERSION=%s；dist=%s" % (VERSION, DIST))
    print("模式：%s" % mode)
    try:
        _acquire_run_lock()
    except AssertionError as exc:
        print("FAIL 无法取得 T-22 运行锁：%s" % exc)
        return 1
    ok = fail = partial = 0
    try:
        for cid, name, fn in CASES:
            ev = []
            try:
                status = fn(ev)
            except AssertionError as exc:
                print("FAIL %s %s -> %s" % (cid, name, exc))
                _print_ev(ev)
                fail += 1
            except Exception as exc:                               # noqa: BLE001
                print("FAIL %s %s -> %s: %s" % (cid, name, type(exc).__name__, exc))
                _print_ev(ev)
                fail += 1
            else:
                if status == PARTIAL:
                    print("PARTIAL %s %s（转写未验：slim 无模型）" % (cid, name))
                    _print_ev(ev)
                    partial += 1
                else:
                    print("ok %s %s" % (cid, name))
                    _print_ev(ev)
                    ok += 1
        if fail == 0:
            print("ok C-11 QA 入口（退出 0；PARTIAL %d 条单列）" % partial)
            ok += 1
        else:
            print("FAIL C-11 QA 入口 -> %d 条用例失败" % fail)
            fail += 1
        total = ok + fail + partial
        print("")
        print("通过 %d / %d（PARTIAL %d）" % (ok, total, partial))
        print("T-22 RESULT: %d/%d ok, partial %d, exit %d"
              % (ok, total, partial, 0 if fail == 0 else 1))
        return 0 if fail == 0 else 1
    finally:
        shutil.rmtree(WORK, ignore_errors=True)
        shutil.rmtree(PKG_ROOT, ignore_errors=True)
        for lock in (RUN_LOCK, PKG_LOCK):
            lock.unlink(missing_ok=True)


if __name__ == "__main__":
    sys.exit(main())
