# -*- coding: utf-8 -*-
"""make_zips · 一条命令把已构建好的 stage 压成两个 ZIP（T-22）。

产物（默认 `<repo>\\dist\\`）：
  tiktok-workbench-<VERSION>-full.zip / -slim.zip
  SHA256SUMS.txt（每行 `<64位小写hex>  <文件名>`）
  build-report.json（字段见 docs/design/api/builder-portable.md §9）

只压**已构建完成**的 stage（带 `.build-portable-stage` marker 与 build-info.json）；
`--skip-build` 时缺其中任何一个 → 退出码 1。体积超预算 → 退出码 2 并打印实际值与上限。
marker 文件本身**绝不进** ZIP。本脚本只写 `dist\\`（`.gitignore` 已覆盖）。
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import re
import subprocess
import sys
import zipfile
from pathlib import Path

APP_NAME = "tiktok-workbench"
BUDGET_BYTES = {"slim": 524_288_000, "full": 2_040_109_466}
FLAVOR_ORDER = ("full", "slim")
MARKER_NAME = ".build-portable-stage"

REQUIRED_FILES = (
    "启动工作台.bat",
    "_setenv.bat",
    "VERSION",
    "python/python.exe",
    "ffmpeg/bin/ffmpeg.exe",
    "webapp/app.py",
    "pipeline/pipeline.py",
)
# 即便 stage 里混进了这些，也不许进用户的 ZIP（T-22 DoD 3）。
EXCLUDE_DIR_NAMES = {".venv"}
EXCLUDE_RELATIVE = {"pipeline/accounts.json", "webapp/data/runs.jsonl"}
EXCLUDE_FILE_NAMES = {MARKER_NAME}


class MakeError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def fail(code: int, message: str):
    raise MakeError(code, message)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_version(repo: Path) -> str:
    path = repo / "VERSION"
    if not path.is_file():
        fail(1, "[FAIL] VERSION 缺失或格式不对：%s" % path)
    value = path.read_text(encoding="utf-8").strip()
    if not re.fullmatch(r"\d+\.\d+\.\d+", value):
        fail(1, "[FAIL] VERSION 缺失或格式不对：%r" % value)
    return value


def _excluded(rel: str) -> bool:
    parts = rel.split("/")
    if any(part in EXCLUDE_DIR_NAMES for part in parts[:-1]):
        return True
    name = parts[-1]
    if name in EXCLUDE_FILE_NAMES or rel in EXCLUDE_RELATIVE:
        return True
    low = name.lower()
    return low.endswith(".xlsx") or low.startswith("~$")


def stage_entries(stage: Path):
    """(相对路径, Path|None) 列表；目录项 Path 为 None，末尾带 `/`。排序稳定。"""
    stage = Path(stage)
    entries = []
    for root, dirnames, filenames in os.walk(stage):
        root_path = Path(root)
        rel_root = root_path.relative_to(stage)
        dirnames[:] = sorted(d for d in dirnames if d not in EXCLUDE_DIR_NAMES)
        rel_dir = "" if rel_root == Path(".") else rel_root.as_posix()
        if rel_dir:
            entries.append((rel_dir + "/", None))
        for name in sorted(filenames):
            rel = name if not rel_dir else rel_dir + "/" + name
            if _excluded(rel):
                continue
            path = root_path / name
            if path.is_file():
                entries.append((rel, path))
    return entries


def validate_stage(stage: Path, flavor: str, version: str) -> dict:
    stage = Path(stage)
    if not stage.is_dir():
        fail(1, "[FAIL] stage 不存在（--skip-build 时不会替你构建）：%s" % stage)
    if not (stage / MARKER_NAME).is_file():
        fail(1, "[FAIL] stage 缺 %s marker，不是完整构建产物：%s" % (MARKER_NAME, stage))
    info_path = stage / "build-info.json"
    if not info_path.is_file():
        fail(1, "[FAIL] stage 缺 build-info.json，不是完整构建产物：%s" % stage)
    try:
        info = json.loads(info_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        fail(1, "[FAIL] build-info.json 读不了（%s）：%s" % (exc, info_path))
    if info.get("flavor") != flavor:
        fail(1, "[FAIL] stage flavor 不符：build-info=%r，期望 %r" % (info.get("flavor"), flavor))
    if str(info.get("version")) != version:
        fail(1, "[FAIL] stage 版本与本仓库 VERSION 不符：build-info=%r，VERSION=%r；先重跑构建"
             % (info.get("version"), version))
    for rel in REQUIRED_FILES:
        if not (stage / rel).is_file():
            fail(1, "[FAIL] stage 缺必含文件：%s" % rel)
    return info


def ensure_stage(flavor: str, repo: Path, stage_root: Path, skip_build: bool,
                 build_opts) -> Path:
    stage = Path(stage_root) / flavor
    if skip_build:
        return stage
    if stage.is_dir() and (stage / "build-info.json").is_file():
        return stage
    build = repo / "packaging" / "build_portable.py"
    if not build.is_file():
        fail(1, "[FAIL] 找不到构建脚本：%s" % build)
    cmd = [sys.executable, str(build), "--flavor", flavor, "--out", str(stage_root)]
    cmd += list(build_opts)
    print("[zips] stage 缺失/不完整，先构建 %s：%s" % (flavor, " ".join(cmd)))
    proc = subprocess.run(cmd, cwd=str(repo))
    if proc.returncode != 0:
        fail(1, "[FAIL] 构建 %s stage 失败（退出码 %s）" % (flavor, proc.returncode))
    return stage


def write_zip(stage: Path, flavor: str, version: str, out_dir: Path) -> dict:
    top = "%s-%s-%s" % (APP_NAME, version, flavor)
    zip_name = top + ".zip"
    zip_path = Path(out_dir) / zip_name
    tmp_path = Path(out_dir) / (zip_name + ".tmp")
    entries = stage_entries(stage)
    unpacked = 0
    file_count = 0
    try:
        with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED,
                             allowZip64=True, compresslevel=6) as zf:
            for rel, path in entries:
                arcname = top + "/" + rel
                if path is None:
                    info = zipfile.ZipInfo(arcname)
                    info.external_attr = (0o40755 << 16) | 0x10
                    info.compress_type = zipfile.ZIP_STORED
                    zf.writestr(info, b"")
                else:
                    zf.write(str(path), arcname)
                    unpacked += path.stat().st_size
                    file_count += 1
        os.replace(tmp_path, zip_path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
    zip_bytes = zip_path.stat().st_size
    budget = BUDGET_BYTES[flavor]
    return {
        "flavor": flavor,
        "zip_name": zip_name,
        "zip_bytes": zip_bytes,
        "unpacked_bytes": unpacked,
        "file_count": file_count,
        "sha256": sha256_file(zip_path),
        "budget_bytes": budget,
        "over_budget": zip_bytes > budget,
    }


def build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="把已构建的 stage 压成两个 ZIP（T-22）")
    ap.add_argument("--flavor", choices=("slim", "full", "both"), default="both")
    ap.add_argument("--out", default=None, help="ZIP 输出目录；默认 <repo>/dist")
    ap.add_argument("--stage-root", default=None,
                    help="stage 根目录（其下是 slim/ 与 full/）；默认 <repo>/build/portable")
    ap.add_argument("--skip-build", action="store_true",
                    help="不构建，直接用现有 stage（缺 marker/build-info 时退出 1）")
    # 仅在需要代为构建时透传给 build_portable.py；压现有 stage 时忽略。
    ap.add_argument("--ffmpeg-dir", default=None)
    ap.add_argument("--hf-cache", default=None)
    ap.add_argument("--python-home", default=None)
    ap.add_argument("--vcruntime-dir", default=None)
    ap.add_argument("--deps-from", default=None)
    ap.add_argument("--skip-deps", action="store_true")
    return ap


def main(argv=None) -> int:
    args = build_arg_parser().parse_args(argv)
    repo = Path(__file__).resolve().parents[1]
    try:
        version = read_version(repo)
        stage_root = (Path(args.stage_root).resolve() if args.stage_root
                      else repo / "build" / "portable")
        out_dir = (Path(args.out).resolve() if args.out else repo / "dist")
        out_dir.mkdir(parents=True, exist_ok=True)
        flavors = list(FLAVOR_ORDER) if args.flavor == "both" else [args.flavor]
        build_opts = []
        for arg_name, flag in (("ffmpeg_dir", "--ffmpeg-dir"), ("hf_cache", "--hf-cache"),
                               ("python_home", "--python-home"),
                               ("vcruntime_dir", "--vcruntime-dir"),
                               ("deps_from", "--deps-from")):
            value = getattr(args, arg_name)
            if value:
                build_opts += [flag, value]
        if args.skip_deps:
            build_opts.append("--skip-deps")

        results = []
        for flavor in flavors:
            stage = ensure_stage(flavor, repo, stage_root, args.skip_build, build_opts)
            validate_stage(stage, flavor, version)
            result = write_zip(stage, flavor, version, out_dir)
            results.append(result)
            print("[OK] %s：%s  zip_bytes=%d unpacked_bytes=%d file_count=%d sha256=%s"
                  % (flavor, result["zip_name"], result["zip_bytes"],
                     result["unpacked_bytes"], result["file_count"], result["sha256"]))

        sums_path = out_dir / "SHA256SUMS.txt"
        sums_path.write_text(
            "".join("%s  %s\n" % (item["sha256"], item["zip_name"]) for item in results),
            encoding="ascii")
        report = {
            "built_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
            "version": version,
            "flavors": [
                {key: item[key] for key in ("flavor", "zip_name", "zip_bytes",
                                            "unpacked_bytes", "file_count", "sha256",
                                            "budget_bytes", "over_budget")}
                for item in results
            ],
        }
        (out_dir / "build-report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        over = [item for item in results if item["over_budget"]]
        if over:
            for item in over:
                print("[FAIL] %s ZIP 超过预算：%d B > %d B"
                      % (item["flavor"], item["zip_bytes"], item["budget_bytes"]))
            return 2
        print("[OK] 产出：%s" % out_dir)
        return 0
    except MakeError as exc:
        print(exc.message)
        return exc.code
    except OSError as exc:
        print("[FAIL] 文件操作失败：%s" % exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
