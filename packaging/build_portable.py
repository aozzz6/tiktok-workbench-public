#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""packaging/build_portable.py - assemble the portable (double-click) package.

Task T-15.  Contract: docs/design/api/builder-portable.md .
Standard library only.  The output folder is "the package": copy it anywhere,
double-click the entry `.bat`, and it runs without any system Python.

Usage (repo root; `--ffmpeg-dir` is required on this machine - see contract §7):

    webapp\\.venv\\Scripts\\python.exe packaging\\build_portable.py --flavor slim
        --ffmpeg-dir "<dir containing ffmpeg.exe and ffprobe.exe>"

Exit codes: 0 = built, 1 = input / environment / content check failed,
2 = refused or unsafe invocation (`--out` outside the allowed places, a stage
without our marker) - nothing is deleted in that case.
Every failure prints one last line `[FAIL] <reason>`; the half-built stage is
kept on purpose so it can be inspected, but `build-info.json` and
`licenses/PYTHON-PACKAGES.txt` are only written when every check passed.
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

APP_NAME = "tiktok-workbench"
MODEL_REPO = "Systran/faster-whisper-medium"
MODEL_DIRNAME = "models--Systran--faster-whisper-medium"
MODEL_MIN_BYTES = 1_000_000_000
MODEL_DIR_MAX_BYTES = 1_900_000_000
SITE_PACKAGES_LIMIT = 419_430_400          # 400 MiB, contract §5
UNPACKED_LIMITS = {"slim": 734_003_200, "full": 2_400_000_000}   # 700 MiB / 2.4 GB

VC_DLLS = (
    "msvcp140.dll", "msvcp140_1.dll", "msvcp140_2.dll",
    "concrt140.dll", "vcruntime140.dll", "vcruntime140_1.dll",
)

VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")

# Stage marker: only a directory carrying this file may be deleted and rebuilt.
# A mistaken `--out` can therefore never wipe an unrelated folder (security review).
STAGE_MARKER = ".build-portable-stage"

# Never ship credentials / local secrets, whatever they are called.
SECRET_NAME_GLOBS = (
    ".env", ".env.*", "*.pem", "*.key", "*cookie*", "*token*", "*secret*",
)

# Post-build scan of the packaged text files.  Patterns are deliberately
# strict (length-prefixed, word-boundary guarded) so ordinary words such as
# "task-", "risk-" or the UI placeholder "sk-..." cannot trip the build.
SECRET_PATTERNS = (
    ("OpenAI/DeepSeek key (sk-…)", re.compile(r"(?<![A-Za-z0-9_-])sk-[A-Za-z0-9_-]{16,}")),
    ("AWS access key (AKIA…)", re.compile(r"(?<![A-Z0-9])AKIA[0-9A-Z]{16}")),
    ("bot token (BOT_…)", re.compile(r"(?<![A-Za-z0-9_])BOT_[A-Za-z0-9_]{10,}")),
    ("Slack bot token (xoxb-…)", re.compile(r"xoxb-[0-9]{6,}")),
    ("private key block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
)
SECRET_SCAN_SUFFIXES = {
    ".py", ".pyw", ".js", ".mjs", ".cjs", ".ts", ".json", ".md", ".txt",
    ".bat", ".cmd", ".ps1", ".vbs", ".html", ".htm", ".css", ".yml", ".yaml",
    ".toml", ".ini", ".cfg", ".env", ".example", ".csv", ".log", ".svg",
}
# Third-party / toolchain trees: not our text, thousands of files, no secrets
# of ours can live there (they are covered by the name blacklist anyway).
SECRET_SCAN_SKIP_TOP = ("python", "ffmpeg")
SECRET_SCAN_MAX_BYTES = 2 * 1024 * 1024

# stdlib pieces that must not travel with the package (contract §5.2)
STDLIB_DROP = (
    "test", "idlelib", "tkinter", "turtledemo", "ensurepip",
    "pydoc_data", "venv", "site-packages",
)
PYTHON_DROP_TOP = ("Scripts", "include", "libs", "tcl")
PYTHON_MANDATORY_FILES = ("python.exe", "pythonw.exe", "python3.dll", "python313.dll")
PYTHON_OPTIONAL_GLOBS = ("vcruntime140*.dll",)

DATA_DIRS = (
    "ledger", "reverse", "logs", "workbuddy",
    "webapp/data", "pipeline/output", "pipeline/reports",
    "models/hf/hub",
)
README_DATA = "README-data.txt"
README_DATA_TEXT = (
    "这个目录放你的数据；升级时不要删它。\r\n"
    "（新版压缩包里没有这里的文件，所以“覆盖式升级”不会替换它。）\r\n"
)

# webapp: developer entry points and tests never travel (contract §6)
WEBAPP_DROP_NAMES = {
    ".venv", "data", "tests", "node_modules", "__pycache__",
    "README.md", "install.bat", "wb_start.ps1", "_wb_task.xml",
    "重启工作台.bat", "package.json", "package-lock.json",
}
WEBAPP_DROP_GLOBS = (
    "start*.bat", "start_workbench.*", "_serve*.bat", "_restart_*.bat",
    "_pw_*.js", "_verify_*.js", "*.log", "*.diff", "*.xlsx", "*.zip",
)

# pipeline: data, personal docs and local binaries never travel (contract §6)
PIPELINE_DROP_NAMES = {
    ".venv", "output", "reports", "tests", "__pycache__",
    "accounts.json", "pipeline_state.json", "gate_report.json",
    "pipeline_run.log", "cc_cookie.txt", "ffmpeg_bin",
    ".secuid_cache.json", ".uid_cache.json",
}
PIPELINE_DROP_SUFFIXES = (".md",)

GLOBAL_DROP_SUFFIXES = (".xlsx", ".zip", ".7z", ".rar", ".tmp", ".log", ".out", ".pyc", ".bak")
GLOBAL_DROP_DIR_RE = re.compile(
    r"^(_bak_|_backup_|_tmp_|_accept|_probes$|_audit_|_check_|_evidence_|_shots_|"
    r"验收包_|验收材料_|改前备份_|accept$)"
)

TEMPLATE_REQUIRED = ("_setenv.bat", "启动工作台.bat", "start.bat")
TEMPLATE_OPTIONAL = ("重启工作台.bat", "检查环境.bat", "使用说明.md", "README-FIRST.txt")

ROOT_FILES_REQUIRED = ("VERSION", "first_run.py")
ROOT_FILES_OPTIONAL = ("LICENSE", "THIRD_PARTY_NOTICES.md")


class BuildError(Exception):
    """Anything that must end the build with exit code 1."""


class UsageError(Exception):
    """A refused or unsafe invocation: exit code 2, nothing is touched."""


# ── small helpers ────────────────────────────────────────────────────
def mb(num: int) -> str:
    return f"{num / (1024 * 1024):.1f} MB"


def dir_bytes(path: Path) -> int:
    total = 0
    if not path.exists():
        return 0
    for p in path.rglob("*"):
        if p.is_file():
            try:
                total += p.stat().st_size
            except OSError:
                pass
    return total


def read_pins(path: Path) -> list[str]:
    rows = []
    if not path.is_file():
        raise BuildError(f"缺少依赖清单：{path.name}（{path.parent.name}\\ 下）")
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        rows.append(line)
    return rows


def remove_path(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=False)
    elif path.exists():
        path.unlink()


def should_drop_dir(name: str) -> bool:
    return bool(GLOBAL_DROP_DIR_RE.match(name))


def is_forbidden_name(name: str) -> bool:
    """Credentials and local secrets never travel, whatever they are called."""
    low = name.lower()
    return any(fnmatch.fnmatchcase(low, pattern) for pattern in SECRET_NAME_GLOBS)


# ── argument / input validation ──────────────────────────────────────
def parse_args(argv=None):
    ap = argparse.ArgumentParser(
        prog="build_portable.py",
        description="Build the portable package folder (task T-15).",
    )
    ap.add_argument("--flavor", choices=("slim", "full", "both"), required=True)
    ap.add_argument("--out", default=None, help="output root; default <repo>/build/portable")
    ap.add_argument("--python-home", default=None, help="CPython install dir; default sys.base_prefix")
    ap.add_argument("--ffmpeg-dir", default=None, help="dir with ffmpeg.exe and ffprobe.exe")
    ap.add_argument("--hf-cache", default=None, help="HuggingFace hub dir (required for full)")
    ap.add_argument("--vcruntime-dir", default=None, help="dir with the 6 VC runtime DLLs")
    ap.add_argument("--deps-from", default=None, help="copy an existing site-packages instead of pip")
    ap.add_argument("--skip-deps", action="store_true", help="structure only (unit tests)")
    ap.add_argument("--keep-stage", action="store_true", help="kept for readability; stage is always kept")
    return ap.parse_args(argv)


def resolve_python_home(args) -> Path:
    home = Path(args.python_home) if args.python_home else Path(sys.base_prefix)
    if not home.is_dir():
        raise BuildError(f"--python-home 不是目录：{home}")
    for name in ("python.exe", "python313.dll"):
        if not (home / name).is_file():
            raise BuildError(f"--python-home 里缺 {name}（{home}）")
    return home


def resolve_ffmpeg_dir(args, repo: Path) -> Path:
    candidates: list[Path] = []
    if args.ffmpeg_dir:
        candidates.append(Path(args.ffmpeg_dir))
    else:
        env_dir = os.environ.get("WB_FFMPEG_DIR")
        if env_dir:
            candidates.append(Path(env_dir))
        candidates.append(repo / "pipeline" / "ffmpeg_bin")
    for cand in candidates:
        if (cand / "ffmpeg.exe").is_file() and (cand / "ffprobe.exe").is_file():
            return cand
    shown = "、".join(str(c) for c in candidates)
    raise BuildError(
        "需要一个同时含 ffmpeg.exe 与 ffprobe.exe 的目录：给 --ffmpeg-dir "
        f"（试过：{shown}）。本机构建请显式传 --ffmpeg-dir，不要依赖 PATH。"
    )


def resolve_vcruntime_dir(args) -> Path:
    if args.vcruntime_dir:
        d = Path(args.vcruntime_dir)
    else:
        d = Path(os.environ.get("SystemRoot", "")) / "System32"
    if not d.is_dir():
        raise BuildError(f"VC 运行库目录不存在：{d}")
    missing = [n for n in VC_DLLS if not (d / n).is_file()]
    if missing:
        raise BuildError(f"VC 运行库目录 {d} 里缺：{', '.join(missing)}")
    return d


def resolve_hf_cache(args) -> tuple[Path, Path, int]:
    """Return (hub dir, model.bin path, model bytes) for `--flavor full`."""
    explicit = Path(args.hf_cache) if args.hf_cache else None
    candidates: list[Path] = []
    if explicit is not None:
        candidates.append(explicit)
    else:
        env = os.environ
        if env.get("HF_HUB_CACHE"):
            candidates.append(Path(env["HF_HUB_CACHE"]))
        if env.get("HF_HOME"):
            candidates.append(Path(env["HF_HOME"]) / "hub")
        if env.get("XDG_CACHE_HOME"):
            candidates.append(Path(env["XDG_CACHE_HOME"]) / "huggingface" / "hub")
        candidates.append(Path(os.path.expanduser("~")) / ".cache" / "huggingface" / "hub")

    for hub in candidates:
        snapshots = hub / MODEL_DIRNAME / "snapshots"
        if not snapshots.is_dir():
            continue
        for bin_path in sorted(snapshots.glob("*/model.bin")):
            if bin_path.is_file() and bin_path.stat().st_size >= MODEL_MIN_BYTES:
                return hub, bin_path, bin_path.stat().st_size

    shown = str(candidates[0]) if candidates else "（没有找到任何 hub 目录）"
    if explicit is None:
        raise BuildError(
            "--flavor full 需要 --hf-cache（或让 HF_HUB_CACHE / HF_HOME 指向含 "
            f"{MODEL_DIRNAME} 的 hub 目录）；试过：{'、'.join(str(c) for c in candidates)}"
        )
    hint = ""
    for cand in candidates:
        if (cand / MODEL_DIRNAME).is_dir():
            hint = f"--hf-cache {cand}"
            break
    raise BuildError(
        f"--hf-cache 指向 {shown}，但没找到 faster-whisper medium 的 model.bin"
        + (f"；本机可用：{hint}" if hint else "")
        + f"（需要 {MODEL_DIRNAME}\\snapshots\\*\\model.bin 且 ≥ 1 GB）"
    )


def read_version(repo: Path) -> str:
    path = repo / "VERSION"
    if not path.is_file():
        raise BuildError("VERSION 缺失或格式不对（仓库根需要一行 0.1.0 这种格式）")
    text = path.read_text(encoding="utf-8", errors="replace").strip()
    if not VERSION_RE.match(text):
        raise BuildError(f"VERSION 缺失或格式不对（读到 {text!r}，要求 X.Y.Z）")
    return text


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def resolve_out_root(args, repo: Path) -> Path:
    """Refuse any `--out` that could delete somebody else's data.

    Allowed: inside `<repo>/build`, or inside the system temp directory.
    Everything else (drive root, the repo itself, the user profile, a system
    folder, a project folder, ...) is refused with exit code 2 before anything
    is touched.
    """
    raw = args.out
    candidate = Path(raw).expanduser() if raw else repo / "build" / "portable"
    resolved = candidate.resolve()
    repo_res = repo.resolve()
    build_root = (repo_res / "build").resolve()
    temp_root = Path(tempfile.gettempdir()).resolve()
    home_root = Path.home().resolve()

    if resolved.parent == resolved:
        raise UsageError(
            f"拒绝 --out {resolved}：这是盘符根目录，构建会删掉它下面的 slim/ full/。"
            "允许的只有 <仓库>\\build 之下或系统临时目录之下。"
        )
    if resolved == repo_res or resolved in repo_res.parents:
        raise UsageError(
            f"拒绝 --out {resolved}：这是仓库本身或它的上级目录（构建会删掉整个仓库）。"
            "允许的只有 <仓库>\\build 之下或系统临时目录之下。"
        )
    if _is_within(resolved, build_root):
        return resolved
    if _is_within(resolved, temp_root):
        return resolved
    if resolved == home_root or _is_within(resolved, home_root):
        raise UsageError(
            f"拒绝 --out {resolved}：位于用户主目录下（用户数据）。"
            "允许的只有 <仓库>\\build 之下或系统临时目录之下。"
        )
    raise UsageError(
        f"拒绝 --out {resolved}：不在允许的两个位置之内。"
        "允许的只有 <仓库>\\build 之下或系统临时目录之下。"
    )


def scan_stage_secrets(stage: Path) -> list[tuple[str, str]]:
    """Return [(relative path, what)] for packaged text files holding a credential."""
    hits: list[tuple[str, str]] = []
    for path in sorted(stage.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(stage)
        if rel.parts and rel.parts[0] in SECRET_SCAN_SKIP_TOP:
            continue
        if path.suffix.lower() not in SECRET_SCAN_SUFFIXES:
            continue
        try:
            if path.stat().st_size > SECRET_SCAN_MAX_BYTES:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for label, pattern in SECRET_PATTERNS:
            if pattern.search(text):
                hits.append((rel.as_posix(), label))
                break
    return hits


def probe_ffmpeg(bin_dir: Path) -> dict:
    exe = bin_dir / "ffmpeg.exe"
    tail = ""
    for flag in ("-version", "--version"):
        try:
            proc = subprocess.run([str(exe), flag], capture_output=True, text=True,
                                  encoding="utf-8", errors="replace", timeout=120)
        except OSError as exc:
            raise BuildError(f"ffmpeg 无法运行（{exe}）：{exc}") from exc
        text = (proc.stdout or "") + (proc.stderr or "")
        if proc.returncode == 0 and text.strip():
            lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
            config = ""
            for line in lines:
                if line.lower().startswith("configuration:"):
                    config = line.split(":", 1)[1].strip()
                    break
            dlls = sorted(p.name for p in bin_dir.glob("*.dll"))
            return {
                "version_line": lines[0] if lines else "",
                "configuration": config,
                "build": "shared" if dlls else "static",
                "dll_count": len(dlls),
            }
        tail = text.strip()[-300:]
    raise BuildError(f"{exe.name} -version / --version 都跑不起来：{tail or '无输出'}")


# ── tree copying ─────────────────────────────────────────────────────
def _skip_entry(child: Path, *, top: bool, drop_names, drop_globs, drop_suffixes) -> bool:
    """One decision point for every copy, shared by the copier and the parity check.

    `drop_names` ("data", "tests", "README.md", ...) is a *top-level* list: it
    describes the repository's own directories, not names that may also occur
    inside a media/asset tree.  Applying it at every depth is what once dropped
    `webapp/static/prompt_v2/README.md` (code review).  `top` decides that.
    """
    name = child.name
    if top and name in drop_names:
        return True
    # __pycache__ is a cache, never content: excluded at every depth (contract §6).
    if name == "__pycache__":
        return True
    if should_drop_dir(name) or is_forbidden_name(name):
        return True
    if drop_globs and any(child.match(pat) for pat in drop_globs):
        return True
    if child.is_file():
        if name.endswith(GLOBAL_DROP_SUFFIXES):
            return True
        if drop_suffixes and name.endswith(drop_suffixes):
            return True
    return False


def copy_webapp(src: Path, dst: Path) -> None:
    dst.mkdir(parents=True, exist_ok=True)
    for child in sorted(src.iterdir()):
        if _skip_entry(child, top=True, drop_names=WEBAPP_DROP_NAMES,
                       drop_globs=WEBAPP_DROP_GLOBS, drop_suffixes=()):
            continue
        if child.is_dir():
            # Deeper levels: suffixes / temp dirs / credential names only.
            _copy_filtered(child, dst / child.name, WEBAPP_DROP_GLOBS, ())
        else:
            shutil.copy2(child, dst / child.name)


def copy_pipeline(src: Path, dst: Path) -> None:
    dst.mkdir(parents=True, exist_ok=True)
    for child in sorted(src.iterdir()):
        if _skip_entry(child, top=True, drop_names=PIPELINE_DROP_NAMES,
                       drop_globs=(), drop_suffixes=PIPELINE_DROP_SUFFIXES):
            continue
        if child.is_dir():
            _copy_filtered(child, dst / child.name, (), PIPELINE_DROP_SUFFIXES)
        else:
            shutil.copy2(child, dst / child.name)


def _copy_filtered(src: Path, dst: Path, drop_globs, drop_suffixes) -> None:
    dst.mkdir(parents=True, exist_ok=True)
    for child in sorted(src.iterdir()):
        if _skip_entry(child, top=False, drop_names=(),
                       drop_globs=drop_globs, drop_suffixes=drop_suffixes):
            continue
        if child.is_dir():
            _copy_filtered(child, dst / child.name, drop_globs, drop_suffixes)
        else:
            shutil.copy2(child, dst / child.name)


def expected_static_files(repo: Path) -> set[str]:
    """Files under `webapp/static` that the exclusion rules must keep.

    Built by replaying the very same `_skip_entry` decisions used by the copy,
    so the parity check below cannot drift away from the real behaviour.
    """
    src = repo / "webapp" / "static"
    kept: set[str] = set()
    if not src.is_dir():
        return kept

    def walk(directory: Path, prefix: str, top: bool) -> None:
        for child in sorted(directory.iterdir()):
            if _skip_entry(child, top=top, drop_names=WEBAPP_DROP_NAMES,
                           drop_globs=WEBAPP_DROP_GLOBS, drop_suffixes=()):
                continue
            name = f"{prefix}{child.name}"
            if child.is_dir():
                walk(child, name + "/", False)
            else:
                kept.add(name)

    walk(src, "", True)
    return kept


def check_static_parity(repo: Path, stage: Path) -> int:
    """Assert the packaged frontend assets are not silently short of the repo."""
    src_root = repo / "webapp" / "static"
    if not src_root.is_dir():
        return 0
    expected = expected_static_files(repo)
    dst_root = stage / "webapp" / "static"
    actual = ({p.relative_to(dst_root).as_posix() for p in dst_root.rglob("*") if p.is_file()}
              if dst_root.is_dir() else set())
    missing = sorted(expected - actual)
    if missing:
        raise BuildError(
            f"webapp\\static 比仓库少 {len(missing)} 个文件（例如 {missing[0]}）："
            "排除规则把页面资源丢了，用户在页面上只会看到 404"
        )
    return len(expected)


def copy_python(python_home: Path, stage: Path) -> None:
    py = stage / "python"
    py.mkdir(parents=True, exist_ok=True)
    for name in PYTHON_MANDATORY_FILES:
        src = python_home / name
        if not src.is_file():
            raise BuildError(f"--python-home 里缺 {name}（{python_home}）")
        shutil.copy2(src, py / name)
    for pattern in PYTHON_OPTIONAL_GLOBS:
        for src in python_home.glob(pattern):
            if src.is_file():
                shutil.copy2(src, py / src.name)

    if (python_home / "DLLs").is_dir():
        shutil.copytree(python_home / "DLLs", py / "DLLs", dirs_exist_ok=True)

    lib_src = python_home / "Lib"
    if not lib_src.is_dir():
        raise BuildError(f"--python-home 里缺 Lib\\（{python_home}）")
    lib_dst = py / "Lib"
    lib_dst.mkdir(parents=True, exist_ok=True)
    for child in sorted(lib_src.iterdir()):
        if child.name in STDLIB_DROP:
            continue
        if child.is_dir():
            shutil.copytree(child, lib_dst / child.name, dirs_exist_ok=True)
        else:
            shutil.copy2(child, lib_dst / child.name)
    (lib_dst / "site-packages").mkdir(parents=True, exist_ok=True)


def copy_vcruntime(vcruntime_dir: Path, stage: Path) -> None:
    py = stage / "python"
    for name in VC_DLLS:
        src = vcruntime_dir / name
        if not src.is_file():
            raise BuildError(f"VC 运行库缺 {name}（{vcruntime_dir}）")
        shutil.copy2(src, py / name)


def copy_ffmpeg(ffmpeg_dir: Path, stage: Path) -> None:
    dst = stage / "ffmpeg" / "bin"
    dst.mkdir(parents=True, exist_ok=True)
    for name in ("ffmpeg.exe", "ffprobe.exe"):
        src = ffmpeg_dir / name
        if not src.is_file():
            raise BuildError(f"ffmpeg 目录里缺 {name}（{ffmpeg_dir}）")
        shutil.copy2(src, dst / name)
    for dll in ffmpeg_dir.glob("*.dll"):
        shutil.copy2(dll, dst / dll.name)


def copy_templates(repo: Path, stage: Path) -> list[str]:
    tpl = repo / "packaging" / "templates"
    if not tpl.is_dir():
        raise BuildError("缺少 packaging\\templates\\（入口与说明书的唯一来源）")
    for name in TEMPLATE_REQUIRED:
        if not (tpl / name).is_file():
            raise BuildError(f"packaging\\templates\\ 里缺必需的 {name}")
    for src in sorted(tpl.iterdir()):
        if src.is_file():
            shutil.copy2(src, stage / src.name)
    return [name for name in TEMPLATE_OPTIONAL if not (tpl / name).is_file()]


def copy_licenses(repo: Path, stage: Path) -> list[str]:
    dst = stage / "licenses"
    dst.mkdir(parents=True, exist_ok=True)
    src = repo / "packaging" / "licenses"
    warns = []
    if src.is_dir():
        for child in sorted(src.iterdir()):
            if child.is_file():
                shutil.copy2(child, dst / child.name)
            elif child.is_dir():
                shutil.copytree(child, dst / child.name, dirs_exist_ok=True)
    else:
        warns.append("packaging\\licenses\\ 不存在（T-23 补齐之前允许缺）")
    return warns


def copy_models(hf_cache: Path, stage: Path) -> tuple[int, int]:
    dst_root = stage / "models" / "hf" / "hub"
    dst_root.mkdir(parents=True, exist_ok=True)
    src = hf_cache / MODEL_DIRNAME
    dst = dst_root / MODEL_DIRNAME
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)
    size = dir_bytes(dst)
    if size > MODEL_DIR_MAX_BYTES:
        raise BuildError(
            f"复制后的模型目录 {mb(size)} 超过 {mb(MODEL_DIR_MAX_BYTES)}："
            "缓存里 blobs 与 snapshots 可能存了同一份内容（符号链接被展开）"
        )
    files = sum(1 for p in dst.rglob("*") if p.is_file())
    return size, files


# ── dependency install ───────────────────────────────────────────────
def install_deps(python_home: Path, stage: Path, repo: Path, deps_from: str | None) -> str:
    target = stage / "python" / "Lib" / "site-packages"
    target.mkdir(parents=True, exist_ok=True)

    if deps_from:
        src = Path(deps_from)
        if not src.is_dir():
            raise BuildError(f"--deps-from 不是目录：{src}")
        for child in sorted(src.iterdir()):
            if child.name.startswith("pip"):
                continue
            if child.is_dir():
                shutil.copytree(child, target / child.name, dirs_exist_ok=True)
            else:
                shutil.copy2(child, target / child.name)
        source = "deps-from"
    else:
        cmd = [
            str(python_home / "python.exe"), "-m", "pip", "install",
            "--target", str(target),
            "--ignore-installed",
            "--only-binary", ":all:",
            "--no-warn-script-location",
            "--disable-pip-version-check",
            "--no-input",
            "-r", str(repo / "webapp" / "requirements.txt"),
            "-r", str(repo / "pipeline" / "requirements-portable.txt"),
        ]
        print("    pip install --target ...（联网，可能需要几分钟）")
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=7200)
        if proc.returncode != 0:
            tail = ((proc.stdout or "") + (proc.stderr or "")).strip().splitlines()[-15:]
            raise BuildError("pip install 失败：\n      " + "\n      ".join(tail))
        source = "pip"

    remove_pip(target)
    remove_console_scripts(target)
    clean_site_packages(target)
    return source


def remove_console_scripts(site_packages: Path) -> int:
    """Drop `site-packages\\bin\\` (pip console scripts).

    `pip install --target` writes `bin\\yt-dlp.exe` (and friends) whose launcher
    header embeds the **build machine's** interpreter path (`#!<build-python>\\python.exe`).
    Those wrappers cannot run on a user's machine and leak a build path into the
    ZIP; this project never calls a console script, so the directory goes.
    """
    bindir = site_packages / "bin"
    if not bindir.exists():
        return 0
    removed = sum(1 for p in bindir.rglob("*") if p.is_file())
    remove_path(bindir)
    return removed


def remove_pip(site_packages: Path) -> None:
    for child in sorted(site_packages.glob("pip*")):
        remove_path(child)


def clean_site_packages(site_packages: Path) -> int:
    """Contract §6 (whole tree): no *.zip / *.tmp / *.bak* / *.xlsx / *.log.

    A wheel can carry its own test fixtures (e.g. setuptools ships a 1.8 KB
    zip under pkg_resources/tests/data).  Data/backup/archive files have no
    business inside the shipped package, so they go - `.dist-info` stays.
    """
    removed = 0
    for pattern in ("*.zip", "*.tmp", "*.bak", "*.bak_*", "*.xlsx", "*.log", "*.7z", "*.rar"):
        for hit in sorted(site_packages.rglob(pattern)):
            if hit.is_file() and ".dist-info" not in hit.parts:
                hit.unlink(missing_ok=True)
                removed += 1
    return removed


def check_imports(stage: Path) -> None:
    python = stage / "python" / "python.exe"
    # Never let the check write bytecode into the stage (T-22: --skip-build ZIPs
    # were carrying __pycache__ / *.pyc that the build never produced).
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    proc = subprocess.run([str(python), "-c", "import torch"], capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=300,
                          env=env)
    if proc.returncode == 0:
        raise BuildError("包里能用 import torch —— 依赖里混进了 PyTorch（ADR-0002 禁止）")
    proc = subprocess.run(
        [str(python), "-c", "import fastapi, uvicorn, openpyxl, ctranslate2, faster_whisper"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300,
        env=env)
    if proc.returncode != 0:
        tail = ((proc.stdout or "") + (proc.stderr or "")).strip().splitlines()[-8:]
        raise BuildError("包内依赖导不进去：" + " / ".join(tail))


def write_python_packages(stage: Path) -> int:
    site_packages = stage / "python" / "Lib" / "site-packages"
    rows: list[tuple[str, str]] = []
    for dist in sorted(site_packages.glob("*.dist-info")):
        meta = dist / "METADATA"
        if not meta.is_file():
            continue
        name = version = None
        for line in meta.read_text(encoding="utf-8", errors="replace").splitlines():
            if name is None and line.startswith("Name: "):
                name = line[6:].strip()
            elif version is None and line.startswith("Version: "):
                version = line[9:].strip()
            if name and version:
                break
        if name and version:
            rows.append((name, version))
    rows.sort(key=lambda item: item[0].lower())
    text = "".join(f"{name}=={version}\n" for name, version in rows)
    (stage / "licenses" / "PYTHON-PACKAGES.txt").write_text(text, encoding="utf-8", newline="\n")
    return len(rows)


def check_no_venv(stage: Path) -> None:
    leftovers = list(stage.rglob("pyvenv.cfg"))
    if leftovers:
        raise BuildError(f"包里出现 pyvenv.cfg：{leftovers[0]}")
    venvs = [p for p in stage.rglob(".venv") if p.is_dir()]
    if venvs:
        raise BuildError(f"包里出现 .venv 目录：{venvs[0]}")


# ── one flavor ───────────────────────────────────────────────────────
def build_one(flavor: str, repo: Path, out_root: Path, args, version: str) -> dict:
    stage = out_root / flavor
    print(f"\n== {flavor} ==")
    print(f"[1/9] 清空并重建 stage：{stage}")
    if stage.exists():
        # Security review: never rmtree a directory this script did not create.
        if not (stage / STAGE_MARKER).is_file():
            raise UsageError(
                f"拒绝重建 {stage}：目录里没有 {STAGE_MARKER} 标记，"
                "它看起来不是本脚本建出来的 stage（不会删别人的数据）。"
            )
        shutil.rmtree(stage)
    stage.mkdir(parents=True)
    (stage / STAGE_MARKER).write_text(
        f"built by packaging/build_portable.py for {APP_NAME} {version} "
        f"({flavor}); safe to delete/rebuild\n",
        encoding="utf-8", newline="\n")

    python_home = resolve_python_home(args)
    ffmpeg_dir = resolve_ffmpeg_dir(args, repo)
    vcruntime_dir = resolve_vcruntime_dir(args)
    pins_pipeline = read_pins(repo / "pipeline" / "requirements-portable.txt")
    pins_webapp = read_pins(repo / "webapp" / "requirements.txt")
    if not (repo / "first_run.py").is_file():
        raise BuildError("仓库根缺少 first_run.py（T-17 的入口脚本）")

    ff = probe_ffmpeg(ffmpeg_dir)
    print(f"      ffmpeg: {ff['version_line'][:70]}（{ff['build']}）")

    print("[2/9] 复制解释器（剔除 test/idlelib/tkinter/... 与 site-packages）")
    copy_python(python_home, stage)

    print("[3/9] 依赖")
    if args.deps_from:
        # --deps-from 是"给现成的一份 site-packages"，与 --skip-deps 互不冲突：
        # 它的用途之一就是离线单测（T-15 的用例用它验证裁剪与 PYTHON-PACKAGES）。
        deps_source = install_deps(python_home, stage, repo, args.deps_from)
    elif args.skip_deps:
        print("      --skip-deps：跳过 pip，只留空的 site-packages")
        deps_source = "pip"
    else:
        deps_source = install_deps(python_home, stage, repo, None)

    print("[4/9] VC 运行库 + ffmpeg")
    copy_vcruntime(vcruntime_dir, stage)
    copy_ffmpeg(ffmpeg_dir, stage)

    print("[5/9] webapp / pipeline 子集")
    copy_webapp(repo / "webapp", stage / "webapp")
    copy_pipeline(repo / "pipeline", stage / "pipeline")

    print("[6/9] 根级文件 / 模板 / 许可证")
    for name in ROOT_FILES_REQUIRED:
        src = repo / name
        if not src.is_file():
            raise BuildError(f"仓库根缺少 {name}")
        shutil.copy2(src, stage / name)
    for name in ROOT_FILES_OPTIONAL:
        src = repo / name
        if src.is_file():
            shutil.copy2(src, stage / name)
        else:
            print(f"      [WARN] 仓库根没有 {name}")
    missing_tpl = copy_templates(repo, stage)
    for name in missing_tpl:
        print(f"      [WARN] 模板还没有 {name}（T-18 / T-24 之前允许缺）")
    for warn in copy_licenses(repo, stage):
        print(f"      [WARN] {warn}")

    print("[7/9] 数据目录（空目录 + README-data.txt）")
    for rel in DATA_DIRS:
        target = stage / rel
        target.mkdir(parents=True, exist_ok=True)
        (target / README_DATA).write_text(README_DATA_TEXT, encoding="utf-8", newline="")

    model_bytes = 0
    model_files = 0
    if flavor == "full":
        hub, bin_path, size = resolve_hf_cache(args)
        print(f"[8/9] 模型：{bin_path.name} {mb(size)} ← {hub}")
        model_bytes, model_files = copy_models(hub, stage)
    else:
        print("[8/9] 模型：slim 不带（空目录）")

    print("[9/9] 校验")
    check_no_venv(stage)
    static_files = check_static_parity(repo, stage)
    print(f"      静态资源：{static_files} 个文件与仓库逐一对应（不含排除项）")
    if not args.skip_deps:
        check_imports(stage)
        site_bytes = dir_bytes(stage / "python" / "Lib" / "site-packages")
        if site_bytes > SITE_PACKAGES_LIMIT:
            raise BuildError(
                f"依赖体积 {mb(site_bytes)} 超过上限 {mb(SITE_PACKAGES_LIMIT)}（400 MiB）"
            )
    else:
        site_bytes = 0

    unpacked = dir_bytes(stage)
    limit = UNPACKED_LIMITS[flavor]
    if unpacked > limit:
        raise BuildError(
            f"整包未压缩 {mb(unpacked)} 超过上限 {mb(limit)}（{flavor}）"
        )

    secret_hits = scan_stage_secrets(stage)
    if secret_hits:
        detail = "；".join(f"{path}（{what}）" for path, what in secret_hits[:5])
        raise BuildError(
            f"包里发现疑似凭据，拒绝出包：{detail}"
            + (f"；共 {len(secret_hits)} 个文件" if len(secret_hits) > 5 else "")
        )
    print(f"      凭据扫描：未命中（{len(SECRET_NAME_GLOBS)} 条文件名黑名单已生效）")

    packages = write_python_packages(stage)
    info = {
        "app": APP_NAME,
        "version": version,
        "flavor": flavor,
        "built_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "python": {
            "version": sys.version.split()[0],
            "arch": "64bit" if sys.maxsize > 2 ** 32 else "32bit",
        },
        "ffmpeg": {
            "version_line": ff["version_line"],
            "configuration": ff["configuration"],
            "license": "GPLv3" if "--enable-gpl" in ff["configuration"] else "LGPL",
            "build": ff["build"],
        },
        "model": {
            "repo": MODEL_REPO,
            "present": flavor == "full",
            "bytes": model_bytes,
            "files": model_files,
        },
        "deps": {
            "site_packages_bytes": site_bytes,
            "unpacked_bytes": unpacked,
            "source": deps_source,
            "python_packages": packages,
        },
        "requirements": {"webapp": pins_webapp, "pipeline": pins_pipeline},
    }
    (stage / "build-info.json").write_text(
        json.dumps(info, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")

    print(f"[OK] {flavor}: {mb(unpacked)} 未压缩，依赖 {mb(site_bytes)}，"
          f"{packages} 个发行版")
    return info


# ── entry point ──────────────────────────────────────────────────────
def main(argv=None) -> int:
    args = parse_args(argv)
    repo = Path(__file__).resolve().parents[1]
    flavors = ["slim", "full"] if args.flavor == "both" else [args.flavor]
    started = time.time()
    try:
        version = read_version(repo)
        out_root = resolve_out_root(args, repo)
        out_root.mkdir(parents=True, exist_ok=True)
        for flavor in flavors:
            build_one(flavor, repo, out_root, args, version)
    except UsageError as exc:
        print(f"[FAIL] {exc}")
        return 2
    except BuildError as exc:
        print(f"[FAIL] {exc}")
        return 1
    except subprocess.SubprocessError as exc:
        # TimeoutExpired (pip install 7200 s, ffmpeg -version, import checks) is
        # not an OSError; without this the contract's "last line is [FAIL] …"
        # was replaced by a traceback (code review).
        what = getattr(exc, "cmd", None) or exc
        timeout = getattr(exc, "timeout", None)
        if timeout is not None:
            print(f"[FAIL] 超时（{timeout} 秒）：{what}")
        else:
            print(f"[FAIL] 子进程失败：{what}")
        return 1
    except OSError as exc:
        print(f"[FAIL] 文件操作失败：{exc}")
        return 1
    print(f"\n[OK] 构建完成：{out_root}（{time.time() - started:.1f}s）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
