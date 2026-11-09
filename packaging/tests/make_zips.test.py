# -*- coding: utf-8 -*-
"""T-22 `make_zips.py` 单测：小 stage + `--skip-build` 造包。

覆盖：文件名/唯一顶层目录/目录条目/排除清单（marker、accounts.json、runs.jsonl、
*.xlsx、.venv）/SHA256SUMS/build-report.json 字段/预算超限退出码 2/缺 build-info 或
marker 退出码 1/两个包的差异只落在 models/ 下/覆盖升级四个数据点位逐字节不变。

跑法（仓库根）：`webapp\\.venv\\Scripts\\python.exe packaging\\tests\\make_zips.test.py`
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
MAKE = REPO / "packaging" / "make_zips.py"
VERSION = (REPO / "VERSION").read_text(encoding="utf-8").strip()
BUDGET = {"slim": 524_288_000, "full": 2_040_109_466}

_ok = 0
_fail = 0


def check(name, fn):
    global _ok, _fail
    try:
        fn()
        print("  PASS " + name)
        _ok += 1
    except AssertionError as exc:
        print("  FAIL " + name + " -> " + str(exc))
        _fail += 1
    except Exception as exc:                                     # noqa: BLE001
        print("  FAIL " + name + " -> " + type(exc).__name__ + ": " + str(exc))
        _fail += 1


def assert_(cond, msg="assert failed"):
    if not cond:
        raise AssertionError(msg)


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run_make(args, timeout=900):
    return subprocess.run([sys.executable, str(MAKE), *args], cwd=str(REPO),
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout)


def make_stage(root: Path, flavor: str, version: str = VERSION) -> Path:
    """造一个最小但结构完整的假 stage（含所有必含文件与要排除的诱饵）。"""
    stage = Path(root) / flavor
    files = {
        "启动工作台.bat": b"@echo off\r\necho ok\r\n",
        "_setenv.bat": b"@echo off\r\n",
        "VERSION": (version + "\n").encode("ascii"),
        "python/python.exe": b"MZ-fake-python",
        "ffmpeg/bin/ffmpeg.exe": b"MZ-fake-ffmpeg",
        "webapp/app.py": b"print('app')\n",
        "pipeline/pipeline.py": b"print('pipeline')\n",
        "ledger/README-data.txt": "这个目录放你的数据；升级时不要删它。".encode("utf-8"),
        "webapp/data/README-data.txt": b"data dir\n",
        # 下面都是必须被排除的诱饵：
        ".build-portable-stage": b"marker\n",
        ".venv/pyvenv.cfg": b"home = x\n",
        "pipeline/accounts.json": b'{"accounts": ["should-not-be-in-zip"]}\n',
        "webapp/data/runs.jsonl": b'{"run": 1}\n',
        "notes.xlsx": b"fake xlsx\n",
        "~$notes.xlsx": b"excel lock\n",
    }
    if flavor == "full":
        files["models/hf/hub/models--Systran--faster-whisper-medium/snapshots/abc123/model.bin"] = b"0" * 4096
    for rel, data in files.items():
        path = stage / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    (stage / "build-info.json").write_text(json.dumps({
        "app": "tiktok-workbench", "version": version, "flavor": flavor,
        "built_at": "2026-11-09T00:00:00+08:00",
    }, ensure_ascii=False), encoding="utf-8")
    return stage


def rel_names(zip_path, flavor):
    top = "tiktok-workbench-%s-%s/" % (VERSION, flavor)
    with zipfile.ZipFile(zip_path) as zf:
        return {item.filename[len(top):] for item in zf.infolist()
                if item.filename.startswith(top)}


def file_entries(zip_path):
    with zipfile.ZipFile(zip_path) as zf:
        return [item for item in zf.infolist() if not item.is_dir()]


def zip_names(zip_path):
    with zipfile.ZipFile(zip_path) as zf:
        return [item.filename for item in zf.infolist()]


print("T-22 make_zips")

# ── 1. slim：文件名 / 顶层目录 / 必含 / 排除 / 目录条目 / SHA / report ──
print("[1] slim 小 stage + --skip-build")

_work = Path(tempfile.mkdtemp(prefix="t22_zips_"))
_stage_root = _work / "stage"
_dist = _work / "dist"
try:
    make_stage(_stage_root, "slim")


    def _slim_zip():
        proc = run_make(["--flavor", "slim", "--skip-build",
                         "--stage-root", str(_stage_root), "--out", str(_dist)])
        assert_(proc.returncode == 0, "退出码应为 0，实得 %s；stdout=%r stderr=%r"
                % (proc.returncode, proc.stdout[-800:], proc.stderr[-800:]))
        name = "tiktok-workbench-%s-slim.zip" % VERSION
        zpath = _dist / name
        assert_(zpath.is_file(), "缺少 %s" % zpath)
        top = "tiktok-workbench-%s-slim" % VERSION
        names = zip_names(zpath)
        assert_(names, "ZIP 为空")
        assert_(all(n == top + "/" or n.startswith(top + "/") for n in names),
                "所有条目必须在唯一顶层目录 %r 下：%r" % (top, names[:5]))
        assert_(set(n.split("/")[0] for n in names) == {top},
                "顶层目录必须唯一：%r" % (set(n.split("/")[0] for n in names),))
        for required in ("启动工作台.bat", "_setenv.bat", "VERSION",
                         "python/python.exe", "ffmpeg/bin/ffmpeg.exe",
                         "webapp/app.py", "pipeline/pipeline.py"):
            assert_(top + "/" + required in names, "缺必含条目 %r" % required)
        forbidden = (".build-portable-stage", "pipeline/accounts.json",
                     "webapp/data/runs.jsonl", ".venv/", ".xlsx")
        for bad in forbidden:
            hit = [n for n in names if bad in n]
            assert_(not hit, "ZIP 里不许有 %r：%r" % (bad, hit))
        assert_(top + "/ledger/README-data.txt" in names,
                "ledger 下应只有 README-data.txt（或空目录条目）")
        # 空目录也要有目录条目：apple 里至少要有 python/、webapp/ 这类目录条目
        assert_(any(n.endswith("/") for n in names), "空目录/目录条目缺失")
        # SHA256SUMS
        sums = _dist / "SHA256SUMS.txt"
        assert_(sums.is_file(), "缺 SHA256SUMS.txt")
        lines = [ln for ln in sums.read_text(encoding="utf-8").splitlines() if ln.strip()]
        assert_(len(lines) == 1, "SHA256SUMS 应有一行：%r" % lines)
        hexsum, filename = lines[0].split("  ", 1)
        assert_(re.fullmatch(r"[0-9a-f]{64}", hexsum), "sha256 不是 64 位小写：%r" % lines[0])
        assert_(filename == name, "SHA256SUMS 文件名不对：%r" % filename)
        assert_(hexsum == sha256_file(zpath), "SHA256SUMS 的哈希与文件不一致")
        # build-report.json
        report = json.loads((_dist / "build-report.json").read_text(encoding="utf-8"))
        assert_(report.get("version") == VERSION, "report.version=%r" % report.get("version"))
        assert_(report.get("built_at"), "report 缺 built_at")
        flavors = report.get("flavors") or []
        assert_(len(flavors) == 1 and flavors[0].get("flavor") == "slim",
                "report.flavors 不对：%r" % flavors)
        entry = flavors[0]
        for field in ("flavor", "zip_name", "zip_bytes", "unpacked_bytes", "file_count",
                      "sha256", "budget_bytes", "over_budget"):
            assert_(field in entry, "report 缺字段 %r" % field)
        assert_(entry["zip_name"] == name, "report.zip_name=%r" % entry["zip_name"])
        assert_(entry["zip_bytes"] == zpath.stat().st_size, "report.zip_bytes 与实际不一致")
        assert_(entry["sha256"] == sha256_file(zpath), "report.sha256 与实际不一致")
        assert_(entry["budget_bytes"] == BUDGET["slim"], "slim budget 不对")
        assert_(entry["over_budget"] is False, "小包不该超预算")
        assert_(entry["file_count"] == len(file_entries(zpath)), "file_count 与 ZIP 不符")
        assert_(entry["unpacked_bytes"] == sum(i.file_size for i in file_entries(zpath)),
                "unpacked_bytes 与 ZIP 内文件大小之和不符")
        assert_(entry["unpacked_bytes"] > 0 and entry["file_count"] > 0, "report 计数为 0")


    check("slim：文件名/单一顶层目录/必含/排除/SHA256SUMS/report 字段", _slim_zip)


    # ── 2. 缺 build-info / 缺 marker → 退出码 1 ──────────────────
    print("[2] --skip-build 而 stage 不完整 → 退出码 1")


    def _bad_stage():
        bad_root = _work / "bad"
        stage = make_stage(bad_root, "slim")
        (stage / "build-info.json").unlink()
        proc = run_make(["--flavor", "slim", "--skip-build",
                         "--stage-root", str(bad_root), "--out", str(_work / "dist_bad")])
        assert_(proc.returncode == 1, "缺 build-info 应退出 1，实得 %s" % proc.returncode)
        out = proc.stdout + proc.stderr
        assert_("build-info" in out, "提示里应点名 build-info：%r" % out[-400:])

        stage2 = make_stage(bad_root / "nometa", "slim")
        (stage2 / ".build-portable-stage").unlink()
        proc2 = run_make(["--flavor", "slim", "--skip-build",
                          "--stage-root", str(bad_root / "nometa"), "--out", str(_work / "dist_bad2")])
        assert_(proc2.returncode == 1, "缺 marker 应退出 1，实得 %s" % proc2.returncode)
        out2 = proc2.stdout + proc2.stderr
        assert_("marker" in out2.lower(), "提示里应点名 marker：%r" % out2[-400:])


    check("缺 build-info / 缺 marker → 退出码 1（不 SKIP）", _bad_stage)


    # ── 3. 体积超限 → 退出码 2，打印实际值与上限 ────────────────
    print("[3] 预算超限 → 退出码 2")


    def _over_budget():
        big_root = _work / "big"
        stage = make_stage(big_root, "slim")
        big = stage / "assets" / "big.bin"
        big.parent.mkdir(parents=True, exist_ok=True)
        chunk = os.urandom(4 * 1024 * 1024)
        with big.open("wb") as fh:
            for _ in range(140):                       # 560 MB 随机数据：几乎不可压缩
                fh.write(chunk)
        proc = run_make(["--flavor", "slim", "--skip-build",
                         "--stage-root", str(big_root), "--out", str(_work / "dist_big")],
                        timeout=1800)
        assert_(proc.returncode == 2, "超预算应退出 2，实得 %s；stdout=%r"
                % (proc.returncode, proc.stdout[-500:]))
        out = proc.stdout + proc.stderr
        assert_("超过预算" in out, "应打印超预算提示：%r" % out[-500:])
        assert_(str(BUDGET["slim"]) in out, "应打印上限 %d：%r" % (BUDGET["slim"], out[-500:]))
        numbers = [int(n) for n in re.findall(r"\b\d{9,}\b", out)]
        assert_(any(n > BUDGET["slim"] for n in numbers), "应打印实际值（>上限）：%r" % numbers)
        report = json.loads((_work / "dist_big" / "build-report.json").read_text(encoding="utf-8"))
        entry = report["flavors"][0]
        assert_(entry["over_budget"] is True, "report.over_budget 应为 true")
        assert_(entry["zip_bytes"] > BUDGET["slim"], "report.zip_bytes 应超限")


    check("预算超限 → 退出码 2、打印实际值与上限、report.over_budget=true", _over_budget)


    # ── 4. 两个包差异只允许在 models/ 下 ────────────────────────
    print("[4] --flavor both：差异只在 models/")


    def _both_diff():
        both_root = _work / "both"
        make_stage(both_root, "full")
        make_stage(both_root, "slim")
        proc = run_make(["--flavor", "both", "--skip-build",
                         "--stage-root", str(both_root), "--out", str(_work / "dist_both")])
        assert_(proc.returncode == 0, "both 应退出 0：%r %r"
                % (proc.stdout[-600:], proc.stderr[-600:]))
        full_zip = _work / "dist_both" / ("tiktok-workbench-%s-full.zip" % VERSION)
        slim_zip = _work / "dist_both" / ("tiktok-workbench-%s-slim.zip" % VERSION)
        assert_(full_zip.is_file() and slim_zip.is_file(), "两个 ZIP 都要存在")
        full_names = rel_names(full_zip, "full")
        slim_names = rel_names(slim_zip, "slim")
        assert_("models/hf/hub/models--Systran--faster-whisper-medium/"
                "snapshots/abc123/model.bin" in full_names, "full 应含 model.bin")
        assert_(not any(n.endswith("model.bin") for n in slim_names),
                "slim 不得含 model.bin")
        diff = full_names - slim_names
        assert_(diff and all(d.startswith("models/") for d in diff),
                "两包差异只允许落在 models/：%r" % sorted(diff)[:10])
        assert_(not (slim_names - full_names), "slim 不应有 full 没有的条目")
        report = json.loads((_work / "dist_both" / "build-report.json").read_text(encoding="utf-8"))
        assert_(sorted(f["flavor"] for f in report["flavors"]) == ["full", "slim"],
                "report 应有两个 flavor")


    check("--flavor both：唯一差异在 models/，full 有 model.bin、slim 没有", _both_diff)


    # ── 5. 覆盖升级不丢数据（等价 Copy-Item -Recurse -Force） ──────
    print("[5] 覆盖升级：四个数据点位逐字节不变")


    def _upgrade_keeps_data():
        old = _work / "old_pkg"
        old.mkdir(parents=True, exist_ok=True)
        slim_zip = _dist / ("tiktok-workbench-%s-slim.zip" % VERSION)
        with zipfile.ZipFile(slim_zip) as zf:
            zf.extractall(old)
        # 老包解压后的包根（唯一顶层目录）
        roots = [p for p in old.iterdir() if p.is_dir()]
        assert_(len(roots) == 1, "ZIP 应只有一个顶层目录：%r" % roots)
        pkg = roots[0]
        # 老包的版本号故意写成旧值：覆盖后必须变成新 VERSION（否则断言恒真）
        (pkg / "VERSION").write_text("0.0.0\n", encoding="utf-8")
        # 模拟用户数据（升级时绝不能丢）
        (pkg / "pipeline" / "accounts.json").write_text(
            '{"accounts": ["老账号"]}\n', encoding="utf-8")
        (pkg / "ledger").mkdir(parents=True, exist_ok=True)
        (pkg / "ledger" / "台账.xlsx").write_bytes(b"fake-xlsx-bytes-14-cols")
        (pkg / "pipeline" / "output").mkdir(parents=True, exist_ok=True)
        (pkg / "pipeline" / "output" / "scrape_老账号.json").write_text(
            '{"account": "老账号", "rows": 1}\n', encoding="utf-8")
        (pkg / "webapp" / "data").mkdir(parents=True, exist_ok=True)
        (pkg / "webapp" / "data" / "runs.jsonl").write_text(
            '{"run": 1}\n{"run": 2}\n{"run": 3}\n', encoding="utf-8")
        data_files = [
            pkg / "pipeline" / "accounts.json",
            pkg / "ledger" / "台账.xlsx",
            pkg / "pipeline" / "output" / "scrape_老账号.json",
            pkg / "webapp" / "data" / "runs.jsonl",
        ]
        before = [sha256_file(p) for p in data_files]
        assert_((pkg / "VERSION").read_text(encoding="utf-8") == "0.0.0\n",
                "覆盖前老包的 VERSION 应是我们写的 0.0.0（否则后面断言恒真）")
        old_python_mtime = (pkg / "python" / "python.exe").stat().st_mtime
        os.utime(pkg / "python" / "python.exe", (946684800, 946684800))   # 2000-01-01
        # “把新包全部内容复制进去、选替换”：用同一份 ZIP 再解压覆盖
        time.sleep(0.05)
        with zipfile.ZipFile(slim_zip) as zf:
            zf.extractall(old)
        after = [sha256_file(p) for p in data_files]
        assert_(before == after, "升级覆盖后用户数据被改动：%r -> %r" % (before, after))
        assert_((pkg / "VERSION").read_text(encoding="utf-8") == VERSION + "\n",
                "新 VERSION 未生效")
        new_mtime = (pkg / "python" / "python.exe").stat().st_mtime
        assert_(new_mtime > 946684800.0, "新构建的 python.exe 应被覆盖（mtime 更新）")
        assert_(new_mtime >= old_python_mtime - 1, "mtime 异常")


    check("覆盖升级：accounts/xlsx/scrape/runs 四个点位哈希不变，VERSION 生效", _upgrade_keeps_data)


    # ── 6. .gitignore 覆盖 dist/（DoD 8 的静态部分） ─────────────
    print("[6] .gitignore 覆盖 dist/")


    def _gitignore_dist():
        text = (REPO / ".gitignore").read_text(encoding="utf-8", errors="replace")
        assert_(re.search(r"(?m)^\s*/?dist/?\s*$", text), ".gitignore 里应有 dist/")


    check(".gitignore 含 dist/（产物不入库）", _gitignore_dist)

    # ── 7. 安全评审：--out 拒绝盘符根（退 2） ───────────────────
    print("[7] --out 指向盘符根 → 退出码 2")


    def _refuse_out_drive_root():
        proc = run_make(["--flavor", "slim", "--skip-build",
                         "--stage-root", str(_stage_root), "--out", "C:\\"])
        out = proc.stdout + proc.stderr
        assert_(proc.returncode == 2, "盘符根 --out 应退出 2，实得 %s；%r"
                % (proc.returncode, out[-400:]))
        assert_("--out" in out and "只允许" in out, "应说明 --out 限制：%r" % out[-400:])


    check("--out C:\\ → 退出码 2，说明只允许哪些位置", _refuse_out_drive_root)


    # ── 8. 安全评审：--stage-root 指向用户主目录 → 退出码 2 ─────
    print("[8] --stage-root 指向用户主目录 → 退出码 2")


    def _refuse_stage_root_home():
        home = os.environ.get("USERPROFILE") or str(Path.home())
        proc = run_make(["--flavor", "slim", "--skip-build",
                         "--stage-root", home, "--out", str(_work / "dist_home")])
        out = proc.stdout + proc.stderr
        assert_(proc.returncode == 2, "主目录 --stage-root 应退出 2，实得 %s；%r"
                % (proc.returncode, out[-400:]))
        assert_("--stage-root" in out, "应点名 --stage-root：%r" % out[-400:])


    check("--stage-root 指用户主目录 → 退出码 2", _refuse_stage_root_home)


    # ── 9. 安全评审：排除表大小写不敏感 + 新增敏感名 + junction ──
    print("[9] .ENV / Accounts.json / cc_cookie / pem / key / junction 都不进 ZIP")


    def _case_and_links():
        root = _work / "case"
        stage = make_stage(root, "slim")
        (stage / ".ENV").write_text("OPENAI_API_KEY=sk-should-not-ship-0123456789\n",
                                    encoding="utf-8")
        (stage / "pipeline" / "Accounts.json").write_text('{"accounts":["x"]}\n',
                                                          encoding="utf-8")
        (stage / "workbuddy").mkdir(parents=True, exist_ok=True)
        (stage / "workbuddy" / "MODELS.JSON").write_text('{"api_key":"sk-x"}\n',
                                                         encoding="utf-8")
        (stage / "CC_COOKIE.TXT").write_text("sessionid=abc\n", encoding="utf-8")
        (stage / "cert.PEM").write_text("-----BEGIN CERTIFICATE-----\n", encoding="utf-8")
        (stage / "priv.KEY").write_text("-----BEGIN PRIVATE KEY-----\n", encoding="utf-8")
        # 代码评审：仓库代码树的 __pycache__ 与 *.pyc 不进 ZIP；
        # 但 python\Lib 下的 .pyc 是解释器的一部分，必须保留（CRD-0005）。
        (stage / "webapp" / "__pycache__").mkdir(parents=True, exist_ok=True)
        (stage / "webapp" / "__pycache__" / "app.cpython-313.pyc").write_bytes(b"pyc")
        (stage / "pipeline" / "scripts").mkdir(parents=True, exist_ok=True)
        (stage / "pipeline" / "scripts" / "orphan.pyc").write_bytes(b"pyc")
        (stage / "python" / "Lib" / "__pycache__").mkdir(parents=True, exist_ok=True)
        (stage / "python" / "Lib" / "__pycache__" / "os.cpython-313.pyc").write_bytes(b"pyc")
        outside = _work / "outside_secret"
        outside.mkdir(parents=True, exist_ok=True)
        (outside / "leak.txt").write_text("outside-the-stage\n", encoding="utf-8")
        junction = stage / "linked_dir"
        made = subprocess.run(["cmd", "/c", "mklink", "/J", str(junction), str(outside)],
                              capture_output=True, text=True)
        proc = run_make(["--flavor", "slim", "--skip-build",
                         "--stage-root", str(root), "--out", str(_work / "dist_case")])
        assert_(proc.returncode == 0, "应退出 0：%r" % (proc.stdout[-400:],))
        zpath = _work / "dist_case" / ("tiktok-workbench-%s-slim.zip" % VERSION)
        rels = [n.split("/", 1)[1].casefold() for n in zip_names(zpath) if "/" in n]
        for bad in (".env", "pipeline/accounts.json", "workbuddy/models.json",
                    "cc_cookie.txt", "cert.pem", "priv.key"):
            assert_(bad not in rels, "排除失效：%r 进了 ZIP" % bad)
        repo_pyc = [r for r in rels if (r.endswith(".pyc") or "__pycache__" in r)
                    and not r.startswith("python/lib/")]
        assert_(not repo_pyc, "仓库代码树的 *.pyc / __pycache__ 不许进 ZIP：%r" % repo_pyc[:5])
        assert_("python/lib/__pycache__/os.cpython-313.pyc" in rels,
                "python\\Lib 下的 .pyc 必须保留（CRD-0005）")
        if made.returncode == 0:
            assert_(not any(r.startswith("linked_dir") for r in rels),
                    "junction 指向的外部文件不应进 ZIP：%r"
                    % [r for r in rels if r.startswith("linked_dir")][:5])
        else:
            print("  NOTE 无法创建 junction（%r），junction 子项跳过" % (made.stderr or made.stdout))
        assert_(not any("leak.txt" in r for r in rels), "junction 目标泄漏进 ZIP")


    check("大小写/新增敏感名/junction 都不进 ZIP", _case_and_links)


    # ── 10. 安全评审：stage 含凭据 → 退出码 1 并点名文件 ─────────
    print("[10] stage 含 sk- 凭据 → 退出码 1")


    def _secret_stage_refused():
        root = _work / "secret"
        stage = make_stage(root, "slim")
        fake_key = "sk-" + ("A" * 24)
        (stage / "webapp" / "config.py").write_text(
            'API_KEY = "%s"\n' % fake_key, encoding="utf-8")
        proc = run_make(["--flavor", "slim", "--skip-build",
                         "--stage-root", str(root), "--out", str(_work / "dist_secret")])
        out = proc.stdout + proc.stderr
        assert_(proc.returncode == 1, "含凭据应退出 1，实得 %s；%r"
                % (proc.returncode, out[-400:]))
        assert_("凭据" in out, "应说明命中凭据：%r" % out[-400:])
        assert_("config.py" in out, "应点名文件：%r" % out[-400:])


    check("stage 含 sk- 凭据 → 退出码 1 并点名 webapp/config.py", _secret_stage_refused)


    # ── 11. 安全评审：dist 里的源码资产计入 SHA256SUMS ──────────
    print("[11] dist 里已有的源码资产一并写入 SHA256SUMS")


    def _extra_assets_in_sums():
        root = _work / "assets"
        make_stage(root, "slim")
        out_dir = _work / "dist_assets"
        out_dir.mkdir(parents=True, exist_ok=True)
        assets = {
            "ffmpeg-9.0.2-source.tar.xz": b"fake-source-tarball",
            "ffmpeg-9.0.2-static-libs-sources.txt": b"lib1.c lib2.c\n",
        }
        for name, data in assets.items():
            (out_dir / name).write_bytes(data)
        proc = run_make(["--flavor", "slim", "--skip-build",
                         "--stage-root", str(root), "--out", str(out_dir)])
        assert_(proc.returncode == 0, "应退出 0：%r" % (proc.stdout[-400:],))
        lines = [ln for ln in (out_dir / "SHA256SUMS.txt").read_text(encoding="ascii").splitlines()
                 if ln.strip()]
        assert_(len(lines) == 3, "应含 1 个 ZIP + 2 个资产：%r" % lines)
        sums = {ln.split("  ", 1)[1]: ln.split("  ", 1)[0] for ln in lines}
        for name in assets:
            assert_(name in sums, "SHA256SUMS 缺资产 %r" % name)
            assert_(sums[name] == sha256_file(out_dir / name), "资产 %r 哈希不对" % name)
        report = json.loads((out_dir / "build-report.json").read_text(encoding="utf-8"))
        assert_([a["name"] for a in report.get("assets", [])] == sorted(assets),
                "build-report.assets 不对：%r" % report.get("assets"))


    check("dist 里的 2 个源码资产计入 SHA256SUMS 与 report.assets", _extra_assets_in_sums)

    # ── 12. 代码评审：--skip-build + build_portable 的旧 stage 不一致 → 退 2 ──
    print("[12] --skip-build 复用旧 stage 前做仓库↔stage 比对")


    def _stale_stage_refused():
        root = _work / "stale"
        stage = make_stage(root, "slim")
        # marker 伪装成 build_portable 产出：这时必须逐文件比对，不一致就退 2
        (stage / ".build-portable-stage").write_text(
            "built by packaging/build_portable.py for tiktok-workbench %s (slim); safe\n"
            % VERSION, encoding="utf-8")
        (stage / "webapp" / "app.py").write_text("# 和仓库不一样\n", encoding="utf-8")
        proc = run_make(["--flavor", "slim", "--skip-build",
                         "--stage-root", str(root), "--out", str(_work / "dist_stale")])
        out = proc.stdout + proc.stderr
        assert_(proc.returncode == 2, "旧 stage 不一致应退 2，实得 %s；%r"
                % (proc.returncode, out[-500:]))
        assert_("不一致" in out and "重跑" in out, "应说明不一致并要求重建：%r" % out[-500:])


    check("--skip-build 且 build_portable stage 与仓库不一致 → 退出码 2", _stale_stage_refused)


    # ── 13. 代码评审：默认重建（只有 --skip-build 才复用） ────────
    print("[13] 默认调用 build_portable 重建（不是静默复用旧 stage）")


    def _default_rebuilds():
        root = _work / "default"
        make_stage(root, "slim")
        out = _work / "dist_default"
        # 不给 --python-home/--ffmpeg-dir：重建会失败，但必须"尝过重建"，
        # 而不是把旧 stage 直接打包成功。
        proc = run_make(["--flavor", "slim", "--stage-root", str(root), "--out", str(out)])
        text = proc.stdout + proc.stderr
        assert_("默认重建" in text, "默认应打印'默认重建'：%r" % text[-500:])
        assert_(proc.returncode != 0, "缺构建输入时默认重建应失败，实得 0")
        zips = list(out.glob("*.zip")) if out.exists() else []
        assert_(not zips, "重建失败后不许产出旧 stage 的 ZIP：%r" % zips)


    check("默认一律重建；构建失败时不产出 ZIP（不会静默复用旧 stage）", _default_rebuilds)


    # ── 14. 代码评审：单 flavor 不覆盖另一个包 ───────────────────
    print("[14] --flavor slim 再跑一次不抹掉 full 的校验和/报告条目")


    def _single_flavor_keeps_other():
        root = _work / "merge"
        make_stage(root, "full")
        make_stage(root, "slim")
        out = _work / "dist_merge"
        first = run_make(["--flavor", "both", "--skip-build",
                          "--stage-root", str(root), "--out", str(out)])
        assert_(first.returncode == 0, "both 应成功：%r" % (first.stdout[-400:],))
        full_zip = out / ("tiktok-workbench-%s-full.zip" % VERSION)
        full_sha = sha256_file(full_zip)
        second = run_make(["--flavor", "slim", "--skip-build",
                           "--stage-root", str(root), "--out", str(out)])
        assert_(second.returncode == 0, "单 flavor 应成功：%r" % (second.stdout[-400:],))
        lines = [ln for ln in (out / "SHA256SUMS.txt").read_text(encoding="ascii").splitlines()
                 if ln.strip()]
        assert_(len(lines) == 2, "单 flavor 跑完仍应保留 2 行：%r" % lines)
        assert_(("%s  %s" % (full_sha, full_zip.name)) in lines, "full 的校验和被抹掉了")
        report = json.loads((out / "build-report.json").read_text(encoding="utf-8"))
        assert_(sorted(f["flavor"] for f in report["flavors"]) == ["full", "slim"],
                "report 丢了另一个包：%r" % [f["flavor"] for f in report["flavors"]])
        assert_("保留另一个包" in second.stdout + second.stderr,
                "应打印保留了另一个包：%r" % second.stdout[-400:])


    check("--flavor slim 不抹掉 full 的 SHA256SUMS 行与 report 条目", _single_flavor_keeps_other)


    # ── 15. 代码评审：dist 锁（两个进程同时打包会互踩） ──────────
    print("[15] dist 有锁时第二个进程退 2")


    def _lock_blocks_second_run():
        root = _work / "locked"
        make_stage(root, "slim")
        out = _work / "dist_locked"
        out.mkdir(parents=True, exist_ok=True)
        lock = out / ".make_zips.lock"
        lock.write_text("12345", encoding="ascii")
        try:
            proc = run_make(["--flavor", "slim", "--skip-build",
                             "--stage-root", str(root), "--out", str(out)])
            text = proc.stdout + proc.stderr
            assert_(proc.returncode == 2, "有锁应退 2，实得 %s；%r"
                    % (proc.returncode, text[-400:]))
            assert_("另一个 make_zips" in text, "应说明锁被占用：%r" % text[-400:])
            assert_(not list(out.glob("*.zip")), "有锁时不应写任何 ZIP")
        finally:
            lock.unlink(missing_ok=True)


    check("dist 里有 .make_zips.lock → 第二个进程退 2 且不写 ZIP", _lock_blocks_second_run)
finally:
    shutil.rmtree(_work, ignore_errors=True)

print("\n通过 %d / %d" % (_ok, _ok + _fail))
sys.exit(0 if _fail == 0 else 1)
