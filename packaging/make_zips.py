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
import shutil
import subprocess
import sys
import tempfile
import time
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
# 即便 stage 里混进了这些，也不许进用户的 ZIP（T-22 DoD 3 + 安全评审）。
# 比较一律 casefold：Windows 上 .VENV / Accounts.json 也必须命中。
EXCLUDE_DIR_NAMES = {".venv"}
EXCLUDE_RELATIVE = {
    "pipeline/accounts.json",
    "webapp/data/runs.jsonl",
    "workbuddy/models.json",          # 用户的 AI key 配置，绝不进包
}
EXCLUDE_FILE_NAMES = {MARKER_NAME, "cc_cookie.txt", ".env"}
EXCLUDE_SUFFIXES = (".xlsx", ".pem", ".key")
# 只对**仓库代码树**排除 __pycache__ / *.pyc；python\Lib 与 site-packages 下的 .pyc
# 是解释器的一部分（CRD-0005：稳态启动 ≤3 秒靠它们），必须保留。
REPO_CODE_TOPS = {"webapp", "pipeline", "packaging"}
# 源码资产（T-23 的 Release 附件）若已在 dist/ 里，一并在 SHA256SUMS.txt 里给校验和。
EXTRA_ASSET_GLOBS = (
    "ffmpeg-*-source.tar.xz",
    "ffmpeg-*-static-libs-sources.txt",
    "pyav-*-ffmpeg-source.txt",
)


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


def _in_repo_code(rel: str) -> bool:
    """是不是"我们自己的代码树"（webapp/pipeline/packaging 或 stage 根级文件）。"""
    parts = [part.casefold() for part in rel.strip("/").split("/") if part]
    if not parts:
        return False
    return parts[0] in REPO_CODE_TOPS or len(parts) == 1


def _excluded(rel: str) -> bool:
    parts = rel.split("/")
    lowered = [part.casefold() for part in parts]
    if any(part in EXCLUDE_DIR_NAMES for part in lowered[:-1]):
        return True
    name = lowered[-1]
    if name in EXCLUDE_FILE_NAMES or "/".join(lowered) in EXCLUDE_RELATIVE:
        return True
    if name.startswith(".env") or name.startswith("~$"):
        return True
    if name.endswith(EXCLUDE_SUFFIXES):
        return True
    return name.endswith(".pyc") and _in_repo_code(rel)


def _is_junction(path: Path) -> bool:
    checker = getattr(os.path, "isjunction", None)
    if checker is None:
        return False
    try:
        return bool(checker(path))
    except OSError:
        return False


def _is_link(path: Path) -> bool:
    """符号链接或 junction：读的时候可以跟，打包时一律跳过。"""
    try:
        return os.path.islink(path) or _is_junction(path)
    except OSError:
        return False


def stage_entries(stage: Path):
    """(相对路径, Path|None) 列表；目录项 Path 为 None，末尾带 `/`。排序稳定。

    跳过符号链接 / junction（目录与文件都跳过），避免把 stage 外的文件带进 ZIP。
    """
    stage = Path(stage)
    entries = []
    for root, dirnames, filenames in os.walk(stage):
        root_path = Path(root)
        rel_root = root_path.relative_to(stage)
        kept = []
        for name in sorted(dirnames):
            full = root_path / name
            rel_name = (rel_root.as_posix() + "/" if rel_root != Path(".") else "") + name
            if name.casefold() in EXCLUDE_DIR_NAMES or _is_link(full):
                continue
            if name.casefold() == "__pycache__" and _in_repo_code(rel_name):
                continue
            kept.append(name)
        dirnames[:] = kept
        rel_dir = "" if rel_root == Path(".") else rel_root.as_posix()
        if rel_dir:
            entries.append((rel_dir + "/", None))
        for name in sorted(filenames):
            rel = name if not rel_dir else rel_dir + "/" + name
            if _excluded(rel):
                continue
            path = root_path / name
            if _is_link(path) or not path.is_file():
                continue
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


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def check_safe_root(flag: str, candidate, repo: Path, *, allow_dist: bool = False) -> Path:
    """`--out` / `--stage-root` 只允许：<repo>\\build 之下、系统 temp 之下；
    `--out` 额外允许契约规定的 <repo>\\dist（默认产物目录）。其余（盘符根、
    仓库根/上级、用户主目录、任意其它位置）一律退 2，什么都不碰。"""
    resolved = Path(candidate).expanduser().resolve()
    repo_res = repo.resolve()
    build_root = (repo_res / "build").resolve()
    dist_root = (repo_res / "dist").resolve()
    temp_root = Path(tempfile.gettempdir()).resolve()
    home_root = Path.home().resolve()
    allowed_tail = "只允许 <仓库>\\build、<仓库>\\dist 或系统临时目录之下。" if allow_dist \
        else "只允许 <仓库>\\build 或系统临时目录之下。"
    if resolved.parent == resolved:
        fail(2, "[FAIL] 拒绝 %s %s：这是盘符根目录。%s" % (flag, resolved, allowed_tail))
    if resolved == repo_res or resolved in repo_res.parents:
        fail(2, "[FAIL] 拒绝 %s %s：这是仓库本身或它的上级目录。%s"
             % (flag, resolved, allowed_tail))
    if _is_within(resolved, build_root) or _is_within(resolved, temp_root):
        return resolved
    if allow_dist and _is_within(resolved, dist_root):
        return resolved
    if resolved == home_root or _is_within(resolved, home_root):
        fail(2, "[FAIL] 拒绝 %s %s：位于用户主目录下（用户数据）。%s"
             % (flag, resolved, allowed_tail))
    fail(2, "[FAIL] 拒绝 %s %s：不在允许的位置之内。%s" % (flag, resolved, allowed_tail))


def scan_stage_secrets(stage: Path):
    """复用 build_portable.scan_stage_secrets（凭据规则只能有一份）。"""
    return _builder().scan_stage_secrets(Path(stage))


def _builder():
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        import build_portable  # noqa: PLC0415
    except Exception as exc:                                     # noqa: BLE001
        fail(1, "[FAIL] 无法 import build_portable：%s" % exc)
    return build_portable


def _builder_sources(bp, src: Path, prefix: str, drop_names, drop_globs, drop_suffixes):
    """Replay build_portable's copy decisions, so drift cannot silently skip a file."""
    for child in sorted(src.iterdir()):
        top = not prefix
        if bp._skip_entry(child, top=top, drop_names=drop_names,
                          drop_globs=drop_globs, drop_suffixes=drop_suffixes):
            continue
        if child.is_dir():
            yield from _builder_sources(bp, child, prefix + child.name + "/",
                                        drop_names, drop_globs, drop_suffixes)
        else:
            yield prefix + child.name, child


def stage_drift(repo: Path, stage: Path):
    """仓库里"会被复制进 stage"的文件与 stage 同名文件逐字节比对；返回不一致清单。"""
    bp = _builder()
    stage = Path(stage)
    diffs = []

    def add(rel, src):
        dst = stage / rel
        try:
            if not dst.is_file() or dst.read_bytes() != Path(src).read_bytes():
                diffs.append(rel)
        except OSError:
            diffs.append(rel)

    templates = repo / "packaging" / "templates"
    if templates.is_dir():
        for path in sorted(templates.rglob("*")):
            if path.is_file():
                add(path.relative_to(templates).as_posix(), path)
    for name in ("VERSION", "first_run.py", "LICENSE", "THIRD_PARTY_NOTICES.md"):
        src = repo / name
        if src.is_file():
            add(name, src)
    for top, drop_names, drop_globs, drop_suffixes in (
        ("webapp", bp.WEBAPP_DROP_NAMES, bp.WEBAPP_DROP_GLOBS, ()),
        ("pipeline", bp.PIPELINE_DROP_NAMES, (), bp.PIPELINE_DROP_SUFFIXES),
    ):
        src_root = repo / top
        if not src_root.is_dir():
            continue
        for rel, src in _builder_sources(bp, src_root, "",
                                         drop_names, drop_globs, drop_suffixes):
            add("%s/%s" % (top, rel), src)
    return diffs


def _stage_was_built(stage: Path) -> bool:
    """只有 build_portable 写出的 marker 才做 drift 比对（测试用假 stage 不受影响）。"""
    marker = Path(stage) / MARKER_NAME
    try:
        text = marker.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return text.startswith("built by packaging/build_portable.py")


def bootstrap_build_opts(repo: Path, stage_root: Path, build_opts, workdir: Path):
    """默认重建时，从已有 stage 借 ffmpeg/bin 与 site-packages（离线、快速）。"""
    opts = list(build_opts)

    def has(flag):
        return flag in opts

    need_ffmpeg = not has("--ffmpeg-dir")
    need_deps = not has("--deps-from")
    temps = []
    if not (need_ffmpeg or need_deps):
        return opts, temps
    for flavor in FLAVOR_ORDER:
        stage = Path(stage_root) / flavor
        if not (stage / "build-info.json").is_file():
            continue
        if need_ffmpeg:
            src = stage / "ffmpeg" / "bin"
            if (src / "ffmpeg.exe").is_file():
                dst = Path(workdir) / "bootstrap-ffmpeg"
                shutil.copytree(src, dst)
                opts += ["--ffmpeg-dir", str(dst)]
                temps.append(dst)
                need_ffmpeg = False
        if need_deps:
            src = stage / "python" / "Lib" / "site-packages"
            if src.is_dir() and any(src.iterdir()):
                dst = Path(workdir) / "bootstrap-deps"
                shutil.copytree(src, dst)
                opts += ["--deps-from", str(dst)]
                temps.append(dst)
                need_deps = False
        if not (need_ffmpeg or need_deps):
            break
    return opts, temps


def ensure_stage(flavor: str, repo: Path, stage_root: Path, skip_build: bool,
                 build_opts) -> Path:
    stage = Path(stage_root) / flavor
    if skip_build:
        return stage
    # 代码评审：默认一律重建（只有 --skip-build 才复用），避免拿旧 stage 出包。
    build = repo / "packaging" / "build_portable.py"
    if not build.is_file():
        fail(1, "[FAIL] 找不到构建脚本：%s" % build)
    cmd = [sys.executable, str(build), "--flavor", flavor, "--out", str(stage_root)]
    cmd += list(build_opts)
    print("[zips] 默认重建 %s stage：%s" % (flavor, " ".join(cmd)))
    proc = subprocess.run(cmd, cwd=str(repo))
    if proc.returncode != 0:
        fail(1, "[FAIL] 构建 %s stage 失败（退出码 %s）" % (flavor, proc.returncode))
    return stage


LOCK_NAME = ".make_zips.lock"


def acquire_lock(out_dir: Path) -> Path:
    """同一 dist 只允许一个 make_zips 在写（两个进程会互踩 SHA256SUMS/report）。"""
    lock = Path(out_dir) / LOCK_NAME
    try:
        fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode("ascii"))
        os.close(fd)
        return lock
    except FileExistsError:
        try:
            if time.time() - lock.stat().st_mtime > 3600:
                lock.unlink(missing_ok=True)
                return acquire_lock(out_dir)
        except OSError:
            pass
        fail(2, "[FAIL] 另一个 make_zips 正在运行（锁 %s）；等它结束再试。" % lock)


def release_lock(lock: Path) -> None:
    try:
        Path(lock).unlink(missing_ok=True)
    except OSError:
        pass


def _extra_assets(out_dir: Path):
    found = {}
    for pattern in EXTRA_ASSET_GLOBS:
        for path in sorted(Path(out_dir).glob(pattern)):
            if path.is_file() and not path.name.endswith(".tmp"):
                found[path.name] = path
    return [found[name] for name in sorted(found)]


def _parse_sums(path: Path):
    result = {}
    try:
        text = Path(path).read_text(encoding="ascii", errors="replace")
    except OSError:
        return result
    for line in text.splitlines():
        match = re.fullmatch(r"([0-9a-f]{64})  (.+)", line.strip())
        if match:
            result[match.group(2)] = match.group(1)
    return result


def _previous_flavors(out_dir: Path):
    path = Path(out_dir) / "build-report.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {item.get("flavor"): item for item in data.get("flavors", [])
            if isinstance(item, dict) and item.get("flavor")}


def merge_previous(out_dir: Path, results):
    """单 flavor 打包时，保留另一个 ZIP 的行/条目（必须按 sha 复核过）。"""
    current = {item["flavor"]: item for item in results}
    report_flavors = _previous_flavors(out_dir)
    sums = _parse_sums(Path(out_dir) / "SHA256SUMS.txt")
    for flavor in FLAVOR_ORDER:
        if flavor in current:
            continue
        item = report_flavors.get(flavor)
        if not item or not item.get("zip_name"):
            continue
        zip_path = Path(out_dir) / item["zip_name"]
        if not zip_path.is_file():
            continue
        digest = sha256_file(zip_path)
        if digest != item.get("sha256") or sums.get(item["zip_name"]) != digest:
            print("[WARN] 丢弃旧的 %s 条目：ZIP 的 sha256 与 SHA256SUMS/report 对不上" % flavor)
            continue
        current[flavor] = dict(item, zip_bytes=zip_path.stat().st_size)
        print("[zips] 保留另一个包：%s（sha256 已复核）" % item["zip_name"])
    return [current[flavor] for flavor in FLAVOR_ORDER if flavor in current]


def write_zip(stage: Path, flavor: str, version: str, out_dir: Path) -> dict:
    top = "%s-%s-%s" % (APP_NAME, version, flavor)
    zip_name = top + ".zip"
    zip_path = Path(out_dir) / zip_name
    entries = stage_entries(stage)
    unpacked = 0
    file_count = 0
    # 随机临时名 + 同目录 os.replace：不跟随已存在的目标文件/符号链接。
    fd, tmp_name = tempfile.mkstemp(prefix=".zip-", suffix=".tmp", dir=str(out_dir))
    os.close(fd)
    tmp_path = Path(tmp_name)
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
    lock = None
    workdir = None
    try:
        version = read_version(repo)
        # 安全评审：--out / --stage-root 与 build_portable 同口径，只允许安全位置。
        out_dir = check_safe_root("--out", args.out or (repo / "dist"), repo, allow_dist=True)
        stage_root = check_safe_root("--stage-root", args.stage_root or (repo / "build" / "portable"),
                                     repo)
        out_dir.mkdir(parents=True, exist_ok=True)
        lock = acquire_lock(out_dir)
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

        if not args.skip_build:
            workdir = Path(tempfile.mkdtemp(prefix="make_zips-bootstrap-"))
            build_opts, _temps = bootstrap_build_opts(repo, stage_root, build_opts, workdir)

        results = []
        for flavor in flavors:
            stage = ensure_stage(flavor, repo, stage_root, args.skip_build, build_opts)
            validate_stage(stage, flavor, version)
            # 代码评审：复用旧 stage 前逐文件比对；build_portable 建出来的 stage
            # 一旦与仓库不一致，宁可退 2 也不出旧包。
            if _stage_was_built(stage):
                drift = stage_drift(repo, stage)
                if drift:
                    shown = "、".join(drift[:5])
                    fail(2, "[FAIL] stage 与仓库不一致（%d 个文件，例如 %s）：%s"
                         % (len(drift), shown,
                            "不要用 --skip-build，或先重跑 build_portable.py 重建。" ))
            # 与 build_portable 同一份凭据扫描：--skip-build 的 stage 也要过。
            hits = scan_stage_secrets(stage)
            if hits:
                for rel, label in hits:
                    print("[FAIL] stage 含凭据：%s（%s）" % (rel, label))
                return 1
            result = write_zip(stage, flavor, version, out_dir)
            results.append(result)
            print("[OK] %s：%s  zip_bytes=%d unpacked_bytes=%d file_count=%d sha256=%s"
                  % (flavor, result["zip_name"], result["zip_bytes"],
                     result["unpacked_bytes"], result["file_count"], result["sha256"]))

        merged = merge_previous(out_dir, results)
        assets = _extra_assets(out_dir)
        sums_lines = ["%s  %s\n" % (item["sha256"], item["zip_name"]) for item in merged]
        sums_lines += ["%s  %s\n" % (sha256_file(path), path.name) for path in assets]
        (out_dir / "SHA256SUMS.txt").write_text("".join(sums_lines), encoding="ascii")
        report = {
            "built_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
            "version": version,
            "flavors": [
                {key: item[key] for key in ("flavor", "zip_name", "zip_bytes",
                                            "unpacked_bytes", "file_count", "sha256",
                                            "budget_bytes", "over_budget")}
                for item in merged
            ],
            "assets": [{"name": path.name, "sha256": sha256_file(path),
                        "bytes": path.stat().st_size} for path in assets],
        }
        (out_dir / "build-report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        for path in assets:
            print("[OK] 源码资产已计入 SHA256SUMS：%s（%d B）" % (path.name, path.stat().st_size))

        over = [item for item in merged if item["over_budget"]]
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
    finally:
        if lock is not None:
            release_lock(lock)
        if workdir is not None:
            shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
