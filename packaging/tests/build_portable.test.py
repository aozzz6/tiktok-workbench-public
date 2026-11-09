# -*- coding: utf-8 -*-
"""T-15 build_portable.py 单测（离线、可重复、不联网）。

覆盖 T-15 DoD 17 与 builder-portable.md 的 §3/§4/§5/§6/§8：
  (a) 假 `--python-home` + 假 `--ffmpeg-dir` + 假 `--vcruntime-dir` + `--skip-deps`
      构建退出码 0，且 §4 的布局齐、§6 的排除清单成立
  (b) 数据目录形状：8 个数据目录各只有 README-data.txt；`ledger\\` 里没有模板 xlsx
  (c) `build-info.json` 字段齐、`version` == `VERSION`、不含构建机绝对路径
  (d) 包内没有 venv（无 pyvenv.cfg / .venv），标准库剔除生效（Lib\\test 等不在）
  (e) 缺一个 VC DLL → 退出码 1、输出点名该 DLL、且不写 build-info.json
  (f) `--flavor full` 缺 `--hf-cache` → 退出码 1、消息含 hf-cache
  (g) `--hf-cache` 存在但没有模型 → 退出码 1、消息含 model.bin
  (h) 重复构建：stage 整个重建（旧文件不残留）
  (i) `packaging\\*.py` 不含 `X:\\` / `C:\\Users` / `/home/` 字面量（DoD 17 扫描）

跑法（仓库根，本文件即 T-15 的测试文件）：
    webapp\\.venv\\Scripts\\python.exe packaging\\tests\\build_portable.test.py
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# 中文 Windows 控制台是 GBK：子进程输出里可能有替换字符（\ufffd），print 会抛
# UnicodeEncodeError。把两个流改成 errors="replace"，用例在任何控制台都能跑完。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="replace")       # type: ignore[attr-defined]
    except (AttributeError, ValueError):
        pass

HERE = Path(__file__).resolve().parent          # packaging/tests
PKG = HERE.parent                                # packaging
REPO = PKG.parent                                # 仓库根
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))
import build_portable as bp                      # noqa: E402  被测模块（纯函数级安全用例）
BUILDER = PKG / "build_portable.py"
VERSION_FILE = REPO / "VERSION"

VC_DLLS = [
    "msvcp140.dll", "msvcp140_1.dll", "msvcp140_2.dll",
    "concrt140.dll", "vcruntime140.dll", "vcruntime140_1.dll",
]

ABS_PATH_RE = re.compile(r"[A-Za-z]:\\")
FORBIDDEN_IN_JSON = ("D:\\", "C:\\Users", "/home/")

# §4 布局（相对 stage 根，POSIX 写法）
REQUIRED_FILES = [
    "python/python.exe",
    "python/Lib/site-packages",
    "ffmpeg/bin/ffmpeg.exe",
    "ffmpeg/bin/ffprobe.exe",
    "webapp/app.py",
    "webapp/static/index.html",
    "pipeline/pipeline.py",
    "pipeline/accounts.example.json",
    "pipeline/scripts/batch_transcribe_fw.py",
    "VERSION",
    "build-info.json",
    "_setenv.bat",
    "启动工作台.bat",
    "start.bat",
    "first_run.py",
    "LICENSE",
    "THIRD_PARTY_NOTICES.md",
    "licenses/PYTHON-PACKAGES.txt",
]

# §4 数据目录（每个目录里只允许 README-data.txt）
DATA_DIRS = [
    "ledger", "reverse", "logs", "workbuddy",
    "webapp/data", "pipeline/output", "pipeline/reports",
    "models/hf/hub",
]

# §6 排除清单（相对 stage 根）
FORBIDDEN_PATHS = [
    "webapp/.venv", "webapp/tests", "webapp/data/runs.jsonl",
    "webapp/package.json", "webapp/install.bat", "webapp/_serve.bat",
    "pipeline/.venv", "pipeline/tests", "pipeline/accounts.json",
    "pipeline/cc_cookie.txt", "pipeline/ffmpeg_bin", ".git", "qa",
]

FORBIDDEN_GLOBS = [
    "webapp/start_workbench.*", "webapp/_serve*.bat", "*.xlsx",
    "*.zip", "*.log", "**/__pycache__", "webapp/*.diff",
]

_ok = 0
_fail = 0


def check(name, fn):
    global _ok, _fail
    try:
        fn()
        print(f"  PASS {name}")
        _ok += 1
    except AssertionError as e:
        print(f"  FAIL {name} -> {e}")
        _fail += 1
    except Exception as e:                                   # noqa: BLE001
        print(f"  FAIL {name} -> {e.__class__.__name__}: {e}")
        _fail += 1


def assert_(cond, msg="assert failed"):
    if not cond:
        raise AssertionError(msg)


# ── 临时目录与假输入 ────────────────────────────────────────────────
def new_tmp() -> Path:
    """系统临时目录下（--out 的允许范围之一；安全评审后 build 只接受 build/ 或 temp/）。"""
    return Path(tempfile.mkdtemp(prefix="t15_build_"))


def write(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, bytes):
        path.write_bytes(data)
    else:
        path.write_text(data, encoding="utf-8", newline="")


def make_fake_python_home(root: Path) -> Path:
    """假解释器目录：只有布局，不可执行（--skip-deps 下不会被 run）。"""
    home = root / "pyhome"
    for name in ("python.exe", "pythonw.exe", "python3.dll", "python313.dll",
                 "vcruntime140.dll", "vcruntime140_1.dll"):
        write(home / name, b"MZ-stub")
    write(home / "DLLs" / "_ssl.pyd", b"stub")
    write(home / "DLLs" / "_socket.pyd", b"stub")
    lib = home / "Lib"
    for d in ("test", "idlelib", "tkinter", "turtledemo", "ensurepip",
              "pydoc_data", "venv", "site-packages"):
        (lib / d).mkdir(parents=True, exist_ok=True)
    write(lib / "os.py", "# stub\n")
    write(lib / "zipfile.py", "# stub\n")
    # 解释器自带的预编译文件：必须保留（CRD-0005，稳态启动 ≤3 秒靠它）
    write(lib / "__pycache__" / "os.cpython-313.pyc", b"\x00pyc-stub")
    write(lib / "test" / "test_os.py", "# stub\n")
    write(lib / "idlelib" / "idle.py", "# stub\n")
    write(lib / "site-packages" / "junk.py", "# 必须被清掉\n")
    for d in ("Scripts", "include", "libs", "tcl"):
        (home / d).mkdir(parents=True, exist_ok=True)
    write(home / "Scripts" / "pip.exe", b"stub")
    return home


def make_fake_ffmpeg(root: Path) -> Path:
    """假 ffmpeg 目录：用系统真正的 tar.exe 当桩（`--version` 退出 0 且有版本行）。

    构建脚本对 `-version` / `--version` 两种写法都要试（真 ffmpeg 两种都认）。
    """
    bin_dir = root / "ffbin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    src = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "tar.exe"
    assert_(src.is_file(), f"找不到可用的桩 exe：{src}")
    for name in ("ffmpeg.exe", "ffprobe.exe"):
        shutil.copyfile(src, bin_dir / name)
    return bin_dir


def make_fake_vcruntime(root: Path, omit: str | None = None) -> Path:
    d = root / "vcruntime"
    d.mkdir(parents=True, exist_ok=True)
    for name in VC_DLLS:
        if name == omit:
            continue
        write(d / name, b"stub")
    return d


def run_build(out: Path, home: Path, ffbin: Path, vc: Path, *,
              flavor: str = "slim", hf_cache: Path | None = None,
              skip_deps: bool = True, extra=(), timeout: int = 900):
    cmd = [sys.executable, str(BUILDER), "--flavor", flavor, "--out", str(out),
           "--python-home", str(home), "--ffmpeg-dir", str(ffbin),
           "--vcruntime-dir", str(vc)]
    if hf_cache is not None:
        cmd += ["--hf-cache", str(hf_cache)]
    if skip_deps:
        cmd += ["--skip-deps"]
    cmd += list(extra)
    env = child_env()
    return subprocess.run(cmd, cwd=str(REPO), capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout,
                          env=env)


def child_env() -> dict:
    """Environment for a child that we decode as UTF-8.

    The console here is GBK; without an explicit PYTHONIOENCODING the build
    script writes GBK bytes that this file would then decode as UTF-8 with
    errors="replace" - the Chinese assertions then fail for no real reason
    (QA hit exactly this).  Never depend on the console code page.
    """
    env = dict(os.environ)
    env.pop("WB_FFMPEG_DIR", None)          # 断言 --ffmpeg-dir 真的被用上
    # 模型缓存的四级回退（契约 §1）必须清干净，否则"缺 --hf-cache"这一支
    # 在本机会被 HF_HOME 兜住，测不到失败分支。
    for name in ("HF_HUB_CACHE", "HUGGINGFACE_HUB_CACHE", "HF_HOME",
                 "XDG_CACHE_HOME", "HF_HUB_OFFLINE"):
        env.pop(name, None)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    return env


def run_raw(args, timeout: int = 900):
    """裸跑构建脚本（自己给全套参数），用于 --out 范围与 marker 的拒绝分支。"""
    return subprocess.run([sys.executable, str(BUILDER)] + [str(a) for a in args],
                          cwd=str(REPO), capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout,
                          env=child_env())


def rel_files(stage: Path):
    return sorted(p.relative_to(stage).as_posix()
                  for p in stage.rglob("*") if p.is_file())


def build_ok(tmp: Path, flavor: str = "slim"):
    """构建一次 slim 并返回 (stage, proc)，供多个断言复用。"""
    out = tmp / "out"
    home = make_fake_python_home(tmp)
    ffbin = make_fake_ffmpeg(tmp)
    vc = make_fake_vcruntime(tmp)
    proc = run_build(out, home, ffbin, vc, flavor=flavor)
    stage = out / flavor
    return stage, proc


print("T-15 构建脚本（build_portable.py）契约单测")

# 单次成功构建，后面一半断言都基于它
_TMP = new_tmp()
try:
    STAGE, PROC = build_ok(_TMP)

    print("[1] 假输入 + --skip-deps → 退出码 0")
    print("    " + (PROC.stdout or "").strip().replace("\n", "\n    ")[:1500])
    if PROC.stderr.strip():
        print("    stderr: " + PROC.stderr.strip()[:800].replace("\n", "\n    "))

    def _exit_zero():
        assert_(BUILDER.is_file(), f"还没有实现：{BUILDER}")
        assert_(PROC.returncode == 0,
                f"退出码 {PROC.returncode}；stdout 末行={PROC.stdout.strip().splitlines()[-1:]!r}")
    check("构建退出码 0", _exit_zero)

    def _layout():
        assert_(STAGE.is_dir(), f"没有 stage：{STAGE}")
        missing = [p for p in REQUIRED_FILES if not (STAGE / p).exists()]
        assert_(not missing, f"§4 布局缺 {missing}")
    check("(a) §4 布局：解释器/依赖/ffmpeg/webapp/pipeline/模板/许可证/版本 都在", _layout)

    def _exclusions():
        present = [p for p in FORBIDDEN_PATHS if (STAGE / p).exists()]
        import glob as _glob
        for pat in FORBIDDEN_GLOBS:
            hits = [str(Path(h).relative_to(STAGE).as_posix())
                    for h in _glob.glob(str(STAGE / pat), recursive=True)]
            present += hits
        assert_(not present, f"§6 排除清单里这些还在包里：{present}")
    check("(a) §6 排除清单：无 venv/tests/数据/个人文档/缓存/压缩包", _exclusions)

    def _data_dirs():
        bad = []
        for d in DATA_DIRS:
            path = STAGE / d
            if not path.is_dir():
                bad.append(f"{d} 不存在")
                continue
            files = sorted(p.name for p in path.iterdir())
            if files != ["README-data.txt"]:
                bad.append(f"{d} 里有 {files}")
        assert_(not bad, "；".join(bad))
        xlsx = list((STAGE / "ledger").rglob("*.xlsx"))
        assert_(not xlsx, f"ledger 里出现了模板 xlsx：{xlsx}")
    check("(b) 8 个数据目录各只有 README-data.txt，ledger 无模板 xlsx", _data_dirs)

    def _build_info():
        info = json.loads((STAGE / "build-info.json").read_text(encoding="utf-8"))
        raw = (STAGE / "build-info.json").read_text(encoding="utf-8")
        assert_(info.get("app") == "tiktok-workbench", f"app={info.get('app')!r}")
        want = VERSION_FILE.read_text(encoding="utf-8").strip()
        assert_(info.get("version") == want, f"version={info.get('version')!r}，VERSION={want!r}")
        assert_(info.get("flavor") == "slim", f"flavor={info.get('flavor')!r}")
        assert_(str(info.get("built_at", "")).strip(), "缺 built_at")
        py = info.get("python") or {}
        assert_(str(py.get("version", "")).startswith("3.13"), f"python.version={py.get('version')!r}")
        ff = info.get("ffmpeg") or {}
        assert_(ff.get("build") in ("static", "shared"), f"ffmpeg.build={ff.get('build')!r}")
        assert_(ff.get("license") in ("GPLv3", "LGPL"), f"ffmpeg.license={ff.get('license')!r}")
        assert_(str(ff.get("version_line", "")).strip(), "缺 ffmpeg.version_line")
        model = info.get("model") or {}
        assert_(model.get("present") is False, f"slim 的 model.present 应为 false：{model!r}")
        deps = info.get("deps") or {}
        assert_(deps.get("source") in ("pip", "deps-from"), f"deps.source={deps.get('source')!r}")
        assert_(isinstance(deps.get("site_packages_bytes"), int), "deps.site_packages_bytes 不是整数")
        assert_(isinstance(deps.get("unpacked_bytes"), int) and deps["unpacked_bytes"] > 0,
                f"deps.unpacked_bytes={deps.get('unpacked_bytes')!r}")
        req = info.get("requirements") or {}
        assert_(req.get("webapp"), "requirements.webapp 为空")
        assert_(req.get("pipeline"), "requirements.pipeline 为空")
        leaked = [tok for tok in FORBIDDEN_IN_JSON if tok in raw]
        assert_(not leaked, f"build-info.json 含构建机绝对路径字面量 {leaked}")
    check("(c) build-info.json 字段齐、version==VERSION、无绝对路径", _build_info)

    def _version_copied():
        assert_((STAGE / "VERSION").read_bytes() == VERSION_FILE.read_bytes(),
                "包内 VERSION 与仓库 VERSION 不是同一份字节")
    check("(c) 包内 VERSION 与仓库 VERSION 逐字节相同", _version_copied)

    def _no_venv():
        pv = list(STAGE.rglob("pyvenv.cfg"))
        venv = [p for p in STAGE.rglob(".venv") if p.is_dir()]
        assert_(not pv, f"包内有 pyvenv.cfg：{pv[:3]}")
        assert_(not venv, f"包内有 .venv 目录：{venv[:3]}")
    check("(d) 包内没有 venv（无 pyvenv.cfg、无 .venv）", _no_venv)

    def _stdlib_trimmed():
        py = STAGE / "python"
        must_exist = ["python.exe", "pythonw.exe", "python3.dll", "python313.dll",
                      "DLLs/_ssl.pyd", "Lib/os.py", "Lib/zipfile.py", "vcruntime140.dll"]
        must_not = ["Lib/test", "Lib/idlelib", "Lib/tkinter", "Lib/turtledemo",
                    "Lib/ensurepip", "Lib/pydoc_data", "Lib/venv", "Scripts",
                    "include", "libs", "tcl"]
        missing = [p for p in must_exist if not (py / p).exists()]
        present = [p for p in must_not if (py / p).exists()]
        assert_(not missing, f"python\\ 里缺 {missing}")
        assert_(not present, f"python\\ 里该剔除的还在：{present}")
        leftovers = list((py / "Lib" / "site-packages").glob("junk.py"))
        assert_(not leftovers, "旧的 site-packages 内容没被清掉")
    check("(d) 标准库按清单剔除，site-packages 由构建重新生成", _stdlib_trimmed)

    def _vc_dlls():
        py = STAGE / "python"
        missing = [n for n in VC_DLLS if not (py / n).is_file()]
        assert_(not missing, f"python\\ 里缺 VC DLL：{missing}")
    check("(d) 6 个 VC 运行库 DLL 都在 python\\ 下", _vc_dlls)

    def _readme_data():
        text = (STAGE / "ledger" / "README-data.txt").read_text(encoding="utf-8")
        assert_("升级" in text and "数据" in text, f"README-data.txt 内容不像说明书：{text!r}")
    check("(b) README-data.txt 是给用户看的中文说明", _readme_data)

    def _templates_copied():
        for name in ("_setenv.bat", "启动工作台.bat", "start.bat"):
            a = (PKG / "templates" / name).read_bytes()
            b = (STAGE / name).read_bytes()
            assert_(a == b, f"{name} 与 packaging\\templates 不是逐字节相同")
    check("(a) 三个入口 .bat 从 packaging\\templates 逐字节复制", _templates_copied)

    # ── 代码评审 1/2：顶层名单不递归 + 静态资源完整性 ───────────────
    print("[1d] 代码评审：顶层名单只作用于顶层、static 完整性断言、子进程超时")

    def _deep_names_kept():
        tmp2 = new_tmp()
        try:
            src = tmp2 / "src"
            write(src / "app.py", "x = 1\n")
            write(src / "README.md", "顶层的开发者文档，要排\n")
            write(src / "static" / "sub" / "README.md", "子目录里的说明，要留\n")
            write(src / "static" / "sub" / "data" / "x.js", "keep()\n")
            write(src / "data" / "runs.jsonl", "{}\n")
            write(src / "tests" / "t.js", "1\n")
            dst = tmp2 / "dst"
            bp.copy_webapp(src, dst)
            got = sorted(p.relative_to(dst).as_posix() for p in dst.rglob("*") if p.is_file())
            want = ["app.py", "static/sub/README.md", "static/sub/data/x.js"]
            assert_(got == want, f"递归层用错了名单：期望 {want}，实得 {got}")
        finally:
            shutil.rmtree(tmp2, ignore_errors=True)
    check("(c1) 顶层 README.md/data/tests 排除；static/sub 下的同名文件与 data 目录保留",
          _deep_names_kept)

    def _nested_pycache_dropped():
        tmp2 = new_tmp()
        try:
            psrc = tmp2 / "pipeline"
            write(psrc / "pipeline.py", "print(1)\n")
            write(psrc / "scripts" / "ok.py", "x = 1\n")
            write(psrc / "scripts" / "__pycache__" / "x.cpython-313.pyc", b"\x00pyc")
            write(psrc / "scripts" / "deeper" / "__pycache__" / "y.pyc", b"\x00pyc")
            pdst = tmp2 / "pdst"
            bp.copy_pipeline(psrc, pdst)
            pgot = sorted(p.relative_to(pdst).as_posix() for p in pdst.rglob("*") if p.is_file())
            assert_(pgot == ["pipeline.py", "scripts/ok.py"],
                    f"pipeline 任意层级的 __pycache__ 都必须排除，实际：{pgot}")
            wsrc = tmp2 / "webapp"
            write(wsrc / "app.py", "print(1)\n")
            write(wsrc / "static" / "a.js", "1\n")
            write(wsrc / "static" / "__pycache__" / "b.pyc", b"\x00pyc")
            wdst = tmp2 / "wdst"
            bp.copy_webapp(wsrc, wdst)
            wgot = sorted(p.relative_to(wdst).as_posix() for p in wdst.rglob("*") if p.is_file())
            assert_(wgot == ["app.py", "static/a.js"],
                    f"webapp 任意层级的 __pycache__ 都必须排除，实际：{wgot}")
        finally:
            shutil.rmtree(tmp2, ignore_errors=True)
    check("(c1b) pipeline\\scripts\\__pycache__（含更深一层）与 webapp 的 __pycache__ 都不进包",
          _nested_pycache_dropped)

    def _real_static_kept():
        repo_file = REPO / "webapp" / "static" / "prompt_v2" / "README.md"
        assert_(repo_file.is_file(), "前提变了：仓库里没有 webapp/static/prompt_v2/README.md")
        assert_((STAGE / "webapp" / "static" / "prompt_v2" / "README.md").is_file(),
                "回归实例：static/prompt_v2/README.md 又被递归规则吃掉了")
        # 顶层 webapp\data 是要保留的"空数据目录"（运行时才写），但仓库里的运行时文件不许进包
        assert_((STAGE / "webapp" / "data").is_dir(), "包内应有空的 webapp\\data 数据目录")
        data_names = sorted(p.name for p in (STAGE / "webapp" / "data").iterdir())
        assert_(data_names == ["README-data.txt"], f"webapp\\data 里有仓库文件：{data_names}")
        assert_(not (STAGE / "webapp" / "tests").exists(), "顶层 webapp\\tests 应被排除")
        expected = len(bp.expected_static_files(REPO))
        got = sum(1 for p in (STAGE / "webapp" / "static").rglob("*") if p.is_file())
        assert_(got == expected, f"static 文件数 {got} ≠ 规则应保留的 {expected}")
    check("(c2) 真实包保留 static/prompt_v2/README.md，static 文件数与规则一致", _real_static_kept)

    def _static_parity_guard():
        tmp2 = new_tmp()
        try:
            fake_repo = tmp2 / "repo"
            fake_stage = tmp2 / "stage"
            for root in (fake_repo, fake_stage):
                write(root / "webapp" / "static" / "a.js", "1\n")
                write(root / "webapp" / "static" / "sub" / "README.md", "doc\n")
            assert_(bp.check_static_parity(fake_repo, fake_stage) == 2,
                    "完整时 check_static_parity 应返回 2")
            (fake_stage / "webapp" / "static" / "sub" / "README.md").unlink()
            try:
                bp.check_static_parity(fake_repo, fake_stage)
            except bp.BuildError as exc:
                assert_("README.md" in str(exc), f"报错没点名缺的文件：{exc}")
            else:
                raise AssertionError("static 少了文件，构建却没有报错")
        finally:
            shutil.rmtree(tmp2, ignore_errors=True)
    check("(c3) 静态资源缺失 → 构建期断言报错并点名文件", _static_parity_guard)

    def _timeout_reports_fail_line():
        tmp2 = new_tmp()
        orig = bp.install_deps

        def boom(*_a, **_k):
            raise subprocess.TimeoutExpired(cmd=["pip", "install", "--target", "..."], timeout=7200)

        bp.install_deps = boom
        try:
            home = make_fake_python_home(tmp2)
            ffbin = make_fake_ffmpeg(tmp2)
            vc = make_fake_vcruntime(tmp2)
            out = tmp2 / "out"
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = bp.main(["--flavor", "slim", "--out", str(out),
                              "--python-home", str(home), "--ffmpeg-dir", str(ffbin),
                              "--vcruntime-dir", str(vc)])          # 不给 --skip-deps → 走 install_deps
            text = buf.getvalue()
            assert_(rc == 1, f"子进程超时应退 1，实得 {rc}")
            last = [ln for ln in text.strip().splitlines() if ln.strip()][-1]
            assert_(last.startswith("[FAIL]") and "超时" in last,
                    f"最后一行不是 '[FAIL] 超时：…'：{last!r}")
            assert_("Traceback" not in text, "超时时不应打印 traceback")
        finally:
            bp.install_deps = orig
            shutil.rmtree(tmp2, ignore_errors=True)
    check("(c4) pip/ffmpeg 超时 → 最后一行 [FAIL] 超时、退出 1、无 traceback",
          _timeout_reports_fail_line)

    # ── --deps-from：离线验证裁剪与许可证清单 ───────────────────────
    print("[1b] --deps-from：*.zip 清掉 / pip* 删掉 / .dist-info 保留 / 生成 PYTHON-PACKAGES.txt")

    def _deps_from_cleanup():
        tmp2 = new_tmp()
        try:
            out = tmp2 / "out"
            home = make_fake_python_home(tmp2)
            ffbin = make_fake_ffmpeg(tmp2)
            vc = make_fake_vcruntime(tmp2)
            deps = tmp2 / "deps"
            write(deps / "somedist" / "__init__.py", "X = 1\n")
            write(deps / "somedist" / "tests" / "data" / "fixture.zip", b"PK-stub")
            write(deps / "somedist-1.0.dist-info" / "METADATA",
                  "Metadata-Version: 2.1\nName: somedist\nVersion: 1.0\n")
            write(deps / "pip" / "__init__.py", "# pip\n")
            write(deps / "pip-26.2.2.dist-info" / "METADATA",
                  "Metadata-Version: 2.1\nName: pip\nVersion: 26.2.2\n")
            proc = run_build(out, home, ffbin, vc, extra=("--deps-from", str(deps)))
            stage = out / "slim"
            printed = (proc.stdout or "") + (proc.stderr or "")
            assert_(proc.returncode == 0, f"退出码 {proc.returncode}：{printed[-600:]}")
            sp = stage / "python" / "Lib" / "site-packages"
            assert_((sp / "somedist" / "__init__.py").is_file(), "--deps-from 的依赖没被复制进来")
            assert_(not list(sp.rglob("*.zip")), "site-packages 里的 *.zip 没被清掉")
            assert_(not list(sp.glob("pip*")), "pip* 没被删掉（ADR-0001：包里不该有 pip）")
            assert_((sp / "somedist-1.0.dist-info" / "METADATA").is_file(),
                    ".dist-info 被误删（ADR-0003 的许可证合规要靠它）")
            lines = (stage / "licenses" / "PYTHON-PACKAGES.txt").read_text(
                encoding="utf-8").splitlines()
            assert_("somedist==1.0" in lines,
                    f"PYTHON-PACKAGES.txt 里没有 somedist==1.0：{lines[:5]}")
            info = json.loads((stage / "build-info.json").read_text(encoding="utf-8"))
            assert_(info["deps"]["source"] == "deps-from",
                    f"deps.source={info['deps']['source']!r}，期望 deps-from")
        finally:
            shutil.rmtree(tmp2, ignore_errors=True)

    check("(1b) --deps-from：*.zip 清掉、pip* 删掉、.dist-info 保留、PYTHON-PACKAGES 生成",
          _deps_from_cleanup)

    # ── 安全评审 1/2：凭据黑名单 + 产物扫描 ─────────────────────────
    print("[1c] 安全：文件名黑名单、产物凭据扫描、命中即拒绝出包")

    def _forbidden_names_dropped():
        tmp2 = new_tmp()
        try:
            src = tmp2 / "src"
            write(src / ".env", "API_KEY=sk-" + "B" * 24 + "\n")
            write(src / ".env.local", "X=1\n")
            write(src / ".env.production", "X=1\n")
            write(src / "id_rsa.pem", "-----BEGIN PRIVATE KEY-----\n")
            write(src / "server.key", "k\n")
            write(src / "cc_cookie.txt", "c\n")
            write(src / "user_token.json", "{}")
            write(src / "secret_notes.txt", "s\n")
            write(src / "app.py", "print(1)\n")
            write(src / "sub" / ".env.example", "X=1\n")
            write(src / "sub" / "ok.js", "1\n")
            dst = tmp2 / "dst"
            bp.copy_webapp(src, dst)
            got = sorted(p.relative_to(dst).as_posix() for p in dst.rglob("*") if p.is_file())
            assert_(got == ["app.py", "sub/ok.js"], f"凭据类文件没被排除，实际复制了 {got}")
        finally:
            shutil.rmtree(tmp2, ignore_errors=True)
    check("(安全 1) .env*/*.pem/*.key/*cookie*/*token*/*secret* 都不进包", _forbidden_names_dropped)

    def _secret_scan_units():
        tmp2 = new_tmp()
        try:
            clean = tmp2 / "clean"
            write(clean / "a.py", "s = 'task-abc'; t = 'risk-based'\n")
            write(clean / "ui.js", 'x = "sk-..."\n')          # UI 占位符，不能命中
            assert_(bp.scan_stage_secrets(clean) == [], "干净目录被误判成有凭据")
            dirty = tmp2 / "dirty"
            write(dirty / "conf.py", "KEY = 'sk-" + "a" * 24 + "'\n")
            hits = bp.scan_stage_secrets(dirty)
            assert_(hits and hits[0][0] == "conf.py", f"没扫出 sk- 形态的 key：{hits}")
            for name, text in (("aws.py", 'K = "AKIA' + "A" * 16 + '"'),
                               ("bot.py", 'T = "BOT_' + "b" * 12 + '"'),
                               ("slack.py", 'T = "xoxb-1234567890-abc"')):
                d = tmp2 / ("d_" + name)
                write(d / name, text + "\n")
                assert_(bp.scan_stage_secrets(d), f"{name} 里的凭据没扫出来")
            skipped = tmp2 / "skipped"
            write(skipped / "python" / "Lib" / "x.py", "sk-" + "a" * 24)
            assert_(bp.scan_stage_secrets(skipped) == [], "python\\ 树应当跳过扫描")
        finally:
            shutil.rmtree(tmp2, ignore_errors=True)
    check("(安全 2) sk-/AKIA/BOT_/xoxb- 能扫出，task-/risk-/sk-... 不误报", _secret_scan_units)

    def _secret_fails_build():
        tmp2 = new_tmp()
        orig = bp.copy_templates

        def leaking(repo, stage):
            (stage / "leak-note.md").write_text(
                "token: sk-" + "C" * 24 + "\n", encoding="utf-8")
            return orig(repo, stage)

        bp.copy_templates = leaking
        try:
            out = tmp2 / "out"
            home = make_fake_python_home(tmp2)
            ffbin = make_fake_ffmpeg(tmp2)
            vc = make_fake_vcruntime(tmp2)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = bp.main(["--flavor", "slim", "--out", str(out),
                              "--python-home", str(home), "--ffmpeg-dir", str(ffbin),
                              "--vcruntime-dir", str(vc), "--skip-deps"])
            text = buf.getvalue()
            assert_(rc == 1, f"带凭据的包应退出 1，实得 {rc}；输出尾：{text[-400:]}")
            assert_("疑似凭据" in text and "leak-note.md" in text,
                    f"失败信息没点名文件：{text[-400:]}")
            assert_(not (out / "slim" / "build-info.json").exists(),
                    "扫出凭据时不许写 build-info.json")
        finally:
            bp.copy_templates = orig
            shutil.rmtree(tmp2, ignore_errors=True)
    check("(安全 3) 产物里出现 sk- 形态凭据 → 构建退出 1 并点名文件", _secret_fails_build)

    # ── 失败分支 ────────────────────────────────────────────────────
    print("[2] 失败分支")

    def _missing_vc_dll():
        tmp2 = new_tmp()
        try:
            out = tmp2 / "out"
            home = make_fake_python_home(tmp2)
            ffbin = make_fake_ffmpeg(tmp2)
            vc = make_fake_vcruntime(tmp2, omit="msvcp140_2.dll")
            proc = run_build(out, home, ffbin, vc)
            printed = (proc.stdout or "") + (proc.stderr or "")
            assert_(proc.returncode == 1, f"缺 DLL 时应退出 1，实得 {proc.returncode}")
            assert_("msvcp140_2.dll" in printed, f"输出没有点名缺的 DLL：{printed[-500:]!r}")
            assert_("[FAIL]" in proc.stdout, "失败时 stdout 末行应是 [FAIL] …")
            assert_(not (out / "slim" / "build-info.json").exists(),
                    "失败时不许写 build-info.json")
        finally:
            shutil.rmtree(tmp2, ignore_errors=True)
    check("(e) 缺一个 VC DLL → 退出 1、点名该 DLL、不写 build-info.json", _missing_vc_dll)

    def _full_needs_hf_cache():
        tmp2 = new_tmp()
        try:
            out = tmp2 / "out"
            home = make_fake_python_home(tmp2)
            ffbin = make_fake_ffmpeg(tmp2)
            vc = make_fake_vcruntime(tmp2)
            proc = run_build(out, home, ffbin, vc, flavor="full")
            printed = (proc.stdout or "") + (proc.stderr or "")
            assert_(proc.returncode == 1, f"full 缺 --hf-cache 应退出 1，实得 {proc.returncode}")
            assert_("hf-cache" in printed.lower(), f"消息里没有 hf-cache：{printed[-500:]!r}")
        finally:
            shutil.rmtree(tmp2, ignore_errors=True)
    check("(f) --flavor full 缺 --hf-cache → 退出 1，消息含 hf-cache", _full_needs_hf_cache)

    def _hf_cache_without_model():
        tmp2 = new_tmp()
        try:
            out = tmp2 / "out"
            home = make_fake_python_home(tmp2)
            ffbin = make_fake_ffmpeg(tmp2)
            vc = make_fake_vcruntime(tmp2)
            empty = tmp2 / "emptyhub"
            empty.mkdir(parents=True, exist_ok=True)
            (empty / "models--Systran--faster-whisper-medium").mkdir(exist_ok=True)
            proc = run_build(out, home, ffbin, vc, flavor="full", hf_cache=empty)
            printed = (proc.stdout or "") + (proc.stderr or "")
            assert_(proc.returncode == 1,
                    f"--hf-cache 存在但没模型应退出 1，实得 {proc.returncode}")
            assert_("model.bin" in printed, f"消息里没有 model.bin：{printed[-500:]!r}")
            assert_(not (out / "full" / "build-info.json").exists(),
                    "不许静默产出没有模型的 full 包")
        finally:
            shutil.rmtree(tmp2, ignore_errors=True)
    check("(g) --hf-cache 存在但没有模型 → 退出 1，消息含 model.bin", _hf_cache_without_model)

    def _rebuild_wipes():
        assert_(STAGE.is_dir(), "stage 不存在，无法验证重建")
        marker = STAGE / "stale-probe.txt"
        write(marker, "上一轮的残留\n")
        proc = run_build(STAGE.parent, make_fake_python_home(_TMP),
                         make_fake_ffmpeg(_TMP), make_fake_vcruntime(_TMP))
        assert_(proc.returncode == 0, f"重建退出码 {proc.returncode}")
        assert_(not marker.exists(), "stage 没有整个重建，上一轮的文件还在")
    check("(h) 重复构建：stage 整个删掉重建，旧文件不残留", _rebuild_wipes)

    # ── 安全评审 2/2：--out 范围与 stage marker（防误删）────────────
    print("[3] --out 范围与 stage marker（防误删别人的目录）")

    def _out_range():
        tmp2 = new_tmp()
        try:
            home = make_fake_python_home(tmp2)
            ffbin = make_fake_ffmpeg(tmp2)
            vc = make_fake_vcruntime(tmp2)
            base = ["--flavor", "slim", "--python-home", str(home),
                    "--ffmpeg-dir", str(ffbin), "--vcruntime-dir", str(vc), "--skip-deps"]
            refused = (("盘符根", Path(REPO).anchor), ("仓库根", REPO), ("用户主目录", Path.home()))
            for label, target in refused:
                proc = run_raw(base + ["--out", str(target)])
                printed = (proc.stdout or "") + (proc.stderr or "")
                assert_(proc.returncode == 2,
                        f"{label} --out {target} 应退 2，实得 {proc.returncode}：{printed[-300:]}")
                assert_("拒绝" in printed, f"{label} 的拒绝理由没打印：{printed[-300:]}")
            # 允许的两处：<repo>\build 之下、系统临时目录之下
            in_build = REPO / "build" / "t15-test-out"
            try:
                proc = run_raw(base + ["--out", str(in_build)])
                assert_(proc.returncode == 0,
                        f"<repo>\\build 之下应允许，实得 {proc.returncode}：{proc.stdout[-300:]}")
            finally:
                shutil.rmtree(in_build, ignore_errors=True)
            proc = run_raw(base + ["--out", str(tmp2 / "out")])
            assert_(proc.returncode == 0,
                    f"系统临时目录之下应允许，实得 {proc.returncode}：{proc.stdout[-300:]}")
        finally:
            shutil.rmtree(tmp2, ignore_errors=True)
    check("(安全 4) --out 盘符根 / 仓库根 / 用户主目录 → 退 2；build\\ 与 temp\\ 之下通过", _out_range)

    def _marker_guard():
        tmp2 = new_tmp()
        try:
            home = make_fake_python_home(tmp2)
            ffbin = make_fake_ffmpeg(tmp2)
            vc = make_fake_vcruntime(tmp2)
            base = ["--flavor", "slim", "--python-home", str(home),
                    "--ffmpeg-dir", str(ffbin), "--vcruntime-dir", str(vc), "--skip-deps"]
            out = tmp2 / "out"
            proc = run_raw(base + ["--out", str(out)])
            assert_(proc.returncode == 0, f"首次构建应成功：{proc.stdout[-300:]}")
            stage = out / "slim"
            assert_((stage / bp.STAGE_MARKER).is_file(), "stage 根上没有 marker")
            write(stage / "stale.txt", "上一轮\n")
            proc = run_raw(base + ["--out", str(out)])
            assert_(proc.returncode == 0, f"有 marker 时应允许重建：{proc.stdout[-300:]}")
            assert_(not (stage / "stale.txt").exists(), "有 marker 时旧文件没被清掉")
            # 去掉 marker：拒绝，且一个字节都不许删
            write(stage / "precious.txt", "别删我\n")
            (stage / bp.STAGE_MARKER).unlink()
            proc = run_raw(base + ["--out", str(out)])
            printed = (proc.stdout or "") + (proc.stderr or "")
            assert_(proc.returncode == 2, f"没有 marker 应退 2，实得 {proc.returncode}")
            assert_(bp.STAGE_MARKER in printed, f"拒绝理由没点名 marker：{printed[-300:]}")
            assert_((stage / "precious.txt").is_file(), "没有 marker 时仍然删了目录内容！")
            # 一个从来不是 stage 的同名目录：同样拒绝、同样保留
            out2 = tmp2 / "out2"
            (out2 / "slim").mkdir(parents=True)
            write(out2 / "slim" / "keep.txt", "别人的数据\n")
            proc = run_raw(base + ["--out", str(out2)])
            assert_(proc.returncode == 2, f"外来目录应退 2，实得 {proc.returncode}")
            assert_((out2 / "slim" / "keep.txt").is_file(), "外来目录被删了！")
        finally:
            shutil.rmtree(tmp2, ignore_errors=True)
    check("(安全 5) 没有 marker 的目录拒删；有 marker 才重建；外来目录内容保留", _marker_guard)

    # ── DoD 17 的扫描断言 ───────────────────────────────────────────
    def _no_abs_literals():
        hits = []
        for path in sorted(PKG.glob("*.py")):
            text = path.read_text(encoding="utf-8", errors="replace")
            for tok in ("C:\\Users", "/home/"):
                if tok in text:
                    hits.append(f"{path.name}: {tok}")
            m = ABS_PATH_RE.search(text)
            if m:
                hits.append(f"{path.name}: {m.group(0)!r}")
        assert_(not hits, f"packaging\\*.py 里出现绝对路径字面量：{hits}")
    check("(i) packaging\\*.py 不含 X:\\ / C:\\Users / /home/", _no_abs_literals)

    # ── T-22 代码评审：console-script 包装器 / 构建机路径 / 字节码 ──
    def _builder_module():
        if str(PKG) not in sys.path:
            sys.path.insert(0, str(PKG))
        import build_portable  # noqa: PLC0415
        return build_portable

    def _console_scripts_removed():
        bp = _builder_module()
        tmp = new_tmp()
        try:
            deps = tmp / "deps"
            write(deps / "bin" / "yt-dlp.exe",
                  b"#!D:\\Python313\\python.exe\\r\\nprint('stub')\\r\\n")
            write(deps / "bin" / "ffmpeg.exe", b"MZ-stub")
            write(deps / "fake_pkg" / "__init__.py", b"x = 1\n")
            write(deps / "fake_pkg-1.0.dist-info" / "METADATA",
                  b"Name: fake_pkg\nVersion: 1.0\n")
            stage = tmp / "stage"
            (stage / "python" / "Lib" / "site-packages").mkdir(parents=True, exist_ok=True)
            bp.install_deps(tmp / "pyhome", stage, REPO, str(deps))
            sp = stage / "python" / "Lib" / "site-packages"
            assert_(not (sp / "bin").exists(),
                    "site-packages\\bin 必须被删掉（console-script 包装器带构建机路径）")
            hits = []
            for path in sp.rglob("*"):
                if not path.is_file():
                    continue
                if any(part.endswith(".dist-info") for part in path.parts):
                    continue
                blob = path.read_bytes()
                for token in ("D:\\Python313", "C:\\Users", "/home/"):
                    if token.encode() in blob:
                        hits.append("%s: %s" % (path.name, token))
            assert_(not hits, "包内残留构建机路径：%r" % hits)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    check("(j) 装依赖后删 site-packages\\bin；包内无构建机绝对路径", _console_scripts_removed)

    def _stage_has_no_build_paths():
        stage = STAGE
        assert_(not (stage / "python" / "Lib" / "site-packages" / "bin").exists(),
                "stage 里还有 site-packages\\bin")
        hits = []
        for path in stage.rglob("*"):
            if not path.is_file():
                continue
            rel = path.relative_to(stage)
            # 第三方/解释器自带的字符串是正常的（numpy 里也有 C:\Users）；
            # 只扫我们自己的文件：webapp\、pipeline\、packaging\、根目录脚本与模板。
            if rel.parts and rel.parts[0] == "python":
                continue
            try:
                blob = path.read_bytes()
            except OSError:
                continue
            for token in (b"D:\\Python313", b"C:\\Users", b"/home/"):
                if token in blob:
                    hits.append("%s: %r" % (rel.as_posix(), token))
        assert_(not hits, "stage 内（我们自己的文件）残留构建机路径：%r" % hits)
    check("(k) 我们自己的文件（webapp/pipeline/根/模板）不含 "
          "D:\\Python313 / C:\\Users / /home/", _stage_has_no_build_paths)

    def _pyc_scope():
        stage = STAGE
        assert_((stage / "python" / "Lib" / "__pycache__" / "os.cpython-313.pyc").is_file(),
                "python\\Lib 下的 .pyc 必须保留（CRD-0005）")
        assert_(not (stage / "pipeline" / "__pycache__").exists(),
                "仓库代码树（pipeline\\）的 __pycache__ 不许进包")
        assert_(not list((stage / "webapp").rglob("*.pyc")),
                "webapp\\ 下的 .pyc 不许进包")
    check("(m) python\\Lib 的 .pyc 保留，pipeline/webapp 的 __pycache__ 不进包",
          _pyc_scope)

    def _check_imports_sets_no_bytecode():
        bp = _builder_module()
        seen = []

        class _Done:
            def __init__(self, rc):
                self.returncode = rc
                self.stdout = ""
                self.stderr = ""

        original = bp.subprocess.run

        def fake(cmd, *a, **kw):
            seen.append(kw.get("env"))
            return _Done(1 if "import torch" in str(cmd) else 0)

        bp.subprocess.run = fake
        try:
            bp.check_imports(Path("dummy-stage"))
        finally:
            bp.subprocess.run = original
        assert_(len(seen) == 2, "check_imports 应跑两次子进程：%r" % len(seen))
        for env in seen:
            assert_(env is not None and env.get("PYTHONDONTWRITEBYTECODE") == "1",
                    "check_imports 必须带 PYTHONDONTWRITEBYTECODE=1：%r" % (env or {}).get(
                        "PYTHONDONTWRITEBYTECODE"))
    check("(l) check_imports 设 PYTHONDONTWRITEBYTECODE=1（不往 stage 写 .pyc）",
          _check_imports_sets_no_bytecode)

finally:
    shutil.rmtree(_TMP, ignore_errors=True)

print(f"\n通过 {_ok} / {_ok + _fail}")
sys.exit(0 if _fail == 0 else 1)
