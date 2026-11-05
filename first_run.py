#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""first_run.py — 新机部署自检 + 初始化（不覆盖任何已有文件）。

做什么
------
1. 检查 Python / 两个虚拟环境（仅开发检出） / ffmpeg / 代理 / TikTok 连通性 / whisper 模型缓存
2. 缺什么就建什么（只创建、不覆盖）：
   - 台账目录（默认 <仓库>/ledger，可用 WB_LEDGER_DIR 覆盖）
   - 台账模板 tiktok_对标.xlsx（14 列表头 + 「对标记录-模板」+「说明」）
   - pipeline/accounts.json（从 accounts.example.json 生成，xlsx 指向上面那份台账）
   - pipeline/output、pipeline/reports、webapp/data 目录
3. 打印还缺哪些人工步骤（TikTok cookie、账号、代理端口）

两种运行形态
------------
* **开发检出**（不带 `--portable`）：行为与本作业之前一致 —— 查两个 venv，缺了就 `[FAIL]`。
* **便携包**（`--portable`，2026-11-05 T-17 新增）：包里没有 venv，所以只打印
  `[ OK ] 包内解释器：…`；`[FAIL]` 只允许三类（台账无法创建 / `accounts.json`
  无法生成或解析 / 包内目录不可写），其余（ffmpeg、模型、代理、TikTok）一律 `[WARN]`。
  契约见 `docs/design/api/launcher-runtime.md` §3。
* **每次启动的快路径**（`--portable --init-only`）：**不检查、不联网、只补缺失文件**，
  单次 < 2 秒；失败不阻断启动，写进 `logs\\first-run.log` 并继续（退出码仍 0）。

用法
----
    python first_run.py                          # 自检 + 自动补齐能补的（开发检出）
    python first_run.py --dry-run                # 只看结论，不写任何文件
    python first_run.py --ledger-dir D:/data/ledger
    python first_run.py --pipeline-dir D:/pkg/pipeline
    python first_run.py --no-network             # 跳过 TikTok 连通性测试（离线时用）
    python first_run.py --portable               # 便携包：检查环境（检查环境.bat 用它 + --no-network）
    python first_run.py --portable --init-only   # 便携包：每次启动都跑，只补缺失文件

推荐用 webapp 的 python 跑（有 openpyxl，才能建台账）：
    webapp\\.venv\\Scripts\\python.exe first_run.py
"""
from __future__ import annotations

import json
import os
import sys

# ★ 2026-11-05（T-17 收尾）：顶层**只** import 三个启动期最轻的模块。
#   `--portable --init-only` 每次双击启动都会跑，它要是把 argparse / pathlib /
#   shutil / socket（更别说 openpyxl）拖进来，冷启动就白花 0.1~0.2 秒。
#   实测（包内 python，importtime 累计）：argparse ≈21 ms、pathlib ≈31 ms、
#   shutil ≈30 ms、socket ≈20 ms、openpyxl ≈990 ms（冷）。所以：
#     · 快路径（文件都在）只用 os/json/sys；
#     · pathlib / argparse 在 `_init_paths()` / `main()` 里按需 import；
#     · shutil / socket 在各自的检查函数里按需 import；
#     · openpyxl 只在真的要建台账时 import（create_ledger 内部）。
_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = None      # Path：`_init_paths()` 里赋值（非快路径才会走到）
PIPE = None
WEBAPP = None

OK, WARN, FAIL = "[ OK ]", "[WARN]", "[FAIL]"

# 表头必须与 webapp\config.py 的 EXPECTED_COLS 逐字一致（T-17 的用例守着这条；
# 这里不 import config：那条路会做代理探测与 mkdir，`--init-only` 的预算不能花在它上面）。
COLS = ["记录日期", "对标账号", "视频发布时间", "视频标题", "话题标签",
        "视频链接/ID", "商品链接", "播放量", "点赞数", "评论数",
        "转发数", "收藏数", "音频", "翻译"]
WIDTHS = [14, 14, 18, 42, 30, 38, 32, 10, 10, 10, 10, 10, 40, 40]


def _init_paths() -> None:
    """非快路径才建 Path 常量（import pathlib 本身有成本，快路径不付）。

    `Path` 也挂成模块全局：下面的辅助函数（hf_hub_dirs / find_example /
    first_run_log_path ……）都在非快路径上才被调用。
    """
    global ROOT, PIPE, WEBAPP, Path
    from pathlib import Path          # noqa: F811 —— 故意延后到非快路径
    ROOT = Path(_HERE)
    PIPE = ROOT / "pipeline"
    WEBAPP = ROOT / "webapp"


def _default_pipe() -> str:
    return os.environ.get("WB_PIPELINE_DIR") or os.path.join(_HERE, "pipeline")


def _default_ledger() -> str:
    return os.environ.get("WB_LEDGER_DIR") or os.path.join(_HERE, "ledger")


_FAST_SWITCHES = ("--portable", "--no-network")


def _fast_init_args(argv: list[str]) -> dict | None:
    """只认启动器实际用的那一小组参数，且只认 `--init-only`（非 dry-run）。

    任何没见过的写法（`-h`、拼错、缺值……）都返回 None，交给真正的 argparse ——
    这样错误信息与退出码和以前完全一致，快路径只是"认得出的那一种形状"的捷径。
    """
    opts = {"init_only": False, "dry_run": False, "pipeline_dir": None, "ledger_dir": None}
    i = 0
    n = len(argv)
    while i < n:
        a = argv[i]
        if a == "--init-only":
            opts["init_only"] = True
        elif a == "--dry-run":
            opts["dry_run"] = True
        elif a in _FAST_SWITCHES:
            pass
        elif a.startswith("--pipeline-dir="):
            opts["pipeline_dir"] = a.split("=", 1)[1]
        elif a.startswith("--ledger-dir="):
            opts["ledger_dir"] = a.split("=", 1)[1]
        elif a in ("--pipeline-dir", "--ledger-dir"):
            if i + 1 >= n:
                return None                       # 缺值 → 让 argparse 报错
            opts["pipeline_dir" if a == "--pipeline-dir" else "ledger_dir"] = argv[i + 1]
            i += 1
        else:
            return None
        i += 1
    if not opts["init_only"] or opts["dry_run"]:
        return None
    return opts


def _xlsx_ok(path) -> bool:
    """这本 xlsx 真能当台账用吗？（安全评审：不能只看"文件存在"）

    `openpyxl` 的 `wb.save()` 不是原子写：进程被杀会留下 0 字节或截断的 xlsx。
    那种文件如果被当成"台账已就绪"，快路径会零输出静默返回 0，而看板打不开
    台账 → 用户拿不到任何线索。判据（都只读文件头尾，毫秒级）：
      ① 大小 > 0；② 开头是 ZIP 魔数 `PK\\x03\\x04`；
      ③ 结尾附近有 ZIP 的 End of Central Directory `PK\\x05\\x06`（截断文件没有）。
    """
    try:
        size = os.path.getsize(path)
        if size <= 0:
            return False
        with open(path, "rb") as f:
            if f.read(4) != b"PK\x03\x04":
                return False
            f.seek(max(0, size - 1024))
            return b"PK\x05\x06" in f.read()      # 中央目录结束记录：完整 ZIP 才有
    except OSError:
        return False


def _find_xlsx(ledger_dir: str) -> bool:
    """ledger 目录里有没有**可用**的 xlsx（跳过 ~$ 锁文件；0 字节/截断的算没有）。"""
    try:
        with os.scandir(ledger_dir) as it:
            for e in it:
                try:
                    if e.name.startswith("~$") or not e.name.lower().endswith(".xlsx"):
                        continue
                    if e.is_file() and _xlsx_ok(e.path):
                        return True
                except OSError:
                    continue
    except OSError:
        return False
    return False


def _package_ready(pipe_dir: str, ledger_dir: str) -> bool:
    """`--init-only` 的快路径判据：目录齐 + 台账在 + accounts.json 可解析。

    全为真 → 这一次启动无事可做，直接返回 0（不输出、不建文件、不 import 重库）。
    任何一条不满足都返回 False，走原来的完整路径（该补的补、该写日志的写日志）。
    """
    data_dir = os.path.join(_HERE, "webapp", "data")
    for d in (pipe_dir, ledger_dir, os.path.join(pipe_dir, "output"),
              os.path.join(pipe_dir, "reports"), data_dir):
        if not os.path.isdir(d):
            return False

    acc_path = os.path.join(pipe_dir, "accounts.json")
    if not os.path.isfile(acc_path):
        return False
    try:
        with open(acc_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except (OSError, ValueError):
        return False                       # 坏了 → 走完整路径，让它写进 first-run.log
    if not isinstance(cfg, dict):
        return False

    if _find_xlsx(ledger_dir):
        return True
    # ledger 目录里没有可用 xlsx，但 accounts.json 指到别处一本真实、完整的台账也算就绪
    configured = str(cfg.get("xlsx") or "").strip()
    return bool(configured) and _xlsx_ok(configured)


def _line(flag: str, text: str) -> None:
    print(f"{flag} {text}")


def _mask_proxy(proxy: str) -> str:
    """打印代理用：去掉 `user:pass@` 凭据，只留 `scheme://host:port`。

    有些代理要认证，`WB_PROXY`/`HTTPS_PROXY` 里就会带 userinfo；`检查环境.bat`
    会把代理打到屏幕上，那会跟着截图/求助贴一起泄露。**没有 userinfo 时原样返回**，
    所以正常地址的输出跟以前一字不差。
    """
    s = str(proxy or "")
    if "@" not in s:
        return s
    scheme, sep, rest = s.partition("://")
    if sep:
        return scheme + sep + rest.rsplit("@", 1)[-1]     # 最后一个 @ 才是 userinfo 的边界
    return s.rsplit("@", 1)[-1]


# ────────────────────────── 检查 ──────────────────────────
def check_python(portable: bool = False) -> bool:
    major, minor = sys.version_info[:2]
    if (major, minor) >= (3, 13):
        _line(OK, f"Python {major}.{minor}（仓库按 3.13 验证）")
        return True
    if (major, minor) >= (3, 11):
        _line(WARN, f"Python {major}.{minor}：仓库按 3.13 验证，能跑但未验证")
        return True
    _line(WARN if portable else FAIL, f"Python {major}.{minor} 太旧，需要 3.11+（推荐 3.13）")
    return False


def check_package_python() -> None:
    """便携包的解释器：包内自带，没有 venv 可查，只如实打印它。"""
    ver = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    _line(OK, f"包内解释器：{sys.executable}（Python {ver}）")


def check_venv(name: str, path: Path, create_hint: str) -> bool:
    exe = path / "Scripts" / "python.exe"
    if exe.exists():
        _line(OK, f"{name} 虚拟环境：{exe}")
        return True
    _line(FAIL, f"{name} 虚拟环境缺失：{path}")
    print(f"       创建：{create_hint}")
    return False


def check_ffmpeg() -> bool:
    import shutil                     # 按需：快路径不该付这个 import 的钱
    exe = shutil.which("ffmpeg")
    if exe:
        _line(OK, f"ffmpeg：{exe}")
        return True
    _line(WARN, "ffmpeg 不在 PATH：音频提取/转码会失败，装上并确保 ffmpeg -version 可用")
    return False


def detect_proxy(scripts: Path | None = None) -> str:
    scripts = scripts or (PIPE / "scripts")
    try:
        if str(scripts) not in sys.path:
            sys.path.insert(0, str(scripts))
        from proxy_detect import detect_proxy as _dp

        return _dp(force=True)
    except Exception as exc:  # noqa: BLE001
        _line(WARN, f"代理探测模块不可用：{exc}")
        return ""


def check_proxy(proxy: str, portable: bool = False) -> bool:
    import socket                     # 按需
    if not proxy:
        _line(WARN if portable else FAIL,
              "没探测到本机代理（抓取 TikTok 需要；只看看板/转写可以不管）")
        return False
    from urllib.parse import urlsplit

    parts = urlsplit(proxy)
    host, port = parts.hostname or "127.0.0.1", parts.port or 80
    try:
        with socket.create_connection((host, port), timeout=1.5):
            _line(OK, f"代理可用：{_mask_proxy(proxy)}")      # 打印前去掉 user:pass@
            return True
    except OSError:
        _line(WARN, f"探测到 {_mask_proxy(proxy)}，但端口连不上："
                    "代理软件没启动？或设 WB_PROXY 指定端口")
        return False


def check_tiktok(proxy: str, timeout: int = 20) -> bool:
    """优先用 curl（与抓取层的 TLS 行为接近）；没有 curl 再退回 urllib。"""
    import shutil
    import subprocess

    curl = shutil.which("curl")
    if curl:
        cmd = [curl, "-s", "-o", os.devnull, "-w", "%{http_code}",
               "--max-time", str(timeout), "https://www.tiktok.com/"]
        if proxy:
            cmd[1:1] = ["-x", proxy]
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 5)
            code = (out.stdout or "").strip()
            if code.startswith(("2", "3")):
                _line(OK, f"TikTok 连通（HTTP {code}）")
                return True
            _line(WARN, f"TikTok 返回 HTTP {code or '000'}：代理节点没连上？")
            return False
        except Exception as exc:  # noqa: BLE001
            _line(WARN, f"TikTok 测试失败：{type(exc).__name__} {str(exc)[:80]}")
            return False

    import urllib.request

    opener = urllib.request.build_opener()
    if proxy:
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": proxy, "https": proxy})
        )
    req = urllib.request.Request(
        "https://www.tiktok.com/",
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
    )
    try:
        with opener.open(req, timeout=timeout) as resp:
            _line(OK, f"TikTok 连通（HTTP {resp.status}）")
            return True
    except Exception as exc:  # noqa: BLE001
        _line(WARN, f"TikTok 连不上：{type(exc).__name__} {str(exc)[:90]}")
        return False


def hf_hub_dirs() -> list[Path]:
    """按 huggingface_hub 的优先级列出「hub 缓存目录」候选（T-17 DoD 4）。

    优先级与 `launcher-runtime.md` §2 一致：`HF_HUB_CACHE` > `HF_HOME\\hub`
    > `XDG_CACHE_HOME\\huggingface\\hub` > `~/.cache/huggingface/hub`。
    另外把 `HF_HOME` / `XDG_CACHE_HOME\\huggingface` 本身也当候选（有人直接把
    HF_HOME 指到缓存根而不是 `<根>\\hub`）。
    """
    out: list[Path] = []

    def add(p) -> None:
        if not p:
            return
        pp = Path(p)
        if pp not in out:
            out.append(pp)

    hub_cache = os.environ.get("HF_HUB_CACHE")
    if hub_cache:
        add(hub_cache)
    hf_home = os.environ.get("HF_HOME")
    if hf_home:
        add(Path(hf_home) / "hub")
        add(hf_home)
    xdg = os.environ.get("XDG_CACHE_HOME")
    if xdg:
        add(Path(xdg) / "huggingface" / "hub")
        add(Path(xdg) / "huggingface")
    add(Path.home() / ".cache" / "huggingface" / "hub")
    return out


def find_cached_models() -> list[tuple[Path, Path | None]]:
    """返回 [(模型目录, 快照里的 model.bin 或 None)]；不 import huggingface_hub。"""
    hits: list[tuple[Path, Path | None]] = []
    seen: set[Path] = set()
    for hub in hf_hub_dirs():
        try:
            if not hub.is_dir():
                continue
            dirs = sorted(hub.glob("models--Systran--faster-whisper-*"))
        except OSError:
            continue
        for d in dirs:
            if not d.is_dir() or d in seen:
                continue
            try:
                bins = sorted(d.glob("snapshots/*/model.bin"))
            except OSError:
                bins = []
            seen.add(d)
            hits.append((d, bins[0] if bins else None))
    return hits


def check_model() -> bool:
    hits = find_cached_models()
    ready = [(d, b) for d, b in hits if b is not None]
    if ready:
        for d, b in ready:
            try:
                size = sum(p.stat().st_size for p in d.rglob("*") if p.is_file())
                size_text = f"{size / 1024 / 1024:.0f} MB"
            except OSError:
                size_text = "大小未知"
            _line(OK, f"whisper 模型已缓存：{d.name}（{size_text}）")
        return True
    searched = "；".join(str(p) for p in hf_hub_dirs())
    _line(WARN, f"没找到 faster-whisper 模型缓存（找过：{searched}）：首次转写需要下载（约 1.5 GB）")
    print("       国内直连 HuggingFace 常失败，先设 HF_ENDPOINT=https://hf-mirror.com")
    print("       或把 medium-ct2 模型目录拷过来，用 --model-dir 指定")
    return False


# ────────────────────────── 初始化（只创建缺失，绝不覆盖） ──────────────────────────
def read_accounts(pipe: Path) -> tuple[dict, str | None]:
    """读 accounts.json：不存在 → ({}, None)；坏了 → ({}, 错误文本)。"""
    p = pipe / "accounts.json"
    if not p.is_file():
        return {}, None
    try:
        v = json.loads(p.read_text(encoding="utf-8"))
        return (v if isinstance(v, dict) else {}), None
    except Exception as exc:  # noqa: BLE001
        return {}, f"{exc.__class__.__name__}: {exc}"


def _ledger_candidates(accounts: dict, ledger_dir: Path) -> list[Path]:
    """候选台账：accounts.json 指的那本 + ledger 目录里的 *.xlsx（跳过 ~$ 锁文件）。"""
    out: list[Path] = []
    seen: set[Path] = set()

    def add(p: Path) -> None:
        try:
            if p.name.startswith("~$") or not p.is_file() or p in seen:
                return
        except OSError:
            return
        seen.add(p)
        out.append(p)

    raw = str(accounts.get("xlsx") or "").strip()
    if raw:
        add(Path(raw))
    if ledger_dir.is_dir():
        try:
            for f in sorted(ledger_dir.glob("*.xlsx")):
                add(f)
        except OSError:
            pass
    return out


def find_ledger(accounts: dict, ledger_dir: Path) -> Path | None:
    """找一本**能用**的台账（0 字节 / 截断 / 非 ZIP 的都不算）。"""
    for p in _ledger_candidates(accounts, ledger_dir):
        if _xlsx_ok(p):
            return p
    return None


def find_broken_ledgers(accounts: dict, ledger_dir: Path) -> list[Path]:
    """存在但不可用的台账（0 字节 / 截断 / 不是 xlsx）。

    绝不覆盖它们，只把名字报出去 —— 覆盖等于毁掉用户手里唯一那份数据。
    """
    return [p for p in _ledger_candidates(accounts, ledger_dir) if not _xlsx_ok(p)]


def create_ledger(path: Path) -> tuple[bool, str]:
    """建 14 列台账模板。返回 (成功, 失败原因)。"""
    try:
        import openpyxl
        from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
        from openpyxl.utils import get_column_letter
    except Exception:  # noqa: BLE001
        return False, ("缺 openpyxl，无法创建台账："
                       "webapp\\.venv\\Scripts\\python.exe -m pip install openpyxl")

    try:
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "对标记录-模板"
        fill = PatternFill("solid", fgColor="1F4E79")
        font = Font(name="微软雅黑", size=11, bold=True, color="FFFFFF")
        thin = Side(style="thin", color="BFBFBF")
        border = Border(left=thin, right=thin, top=thin, bottom=thin)
        center = Alignment(horizontal="center", vertical="center", wrap_text=True)
        for i, (c, w) in enumerate(zip(COLS, WIDTHS), 1):
            cell = ws.cell(row=1, column=i, value=c)
            cell.fill, cell.font, cell.alignment, cell.border = fill, font, center, border
            ws.column_dimensions[get_column_letter(i)].width = w
        ws.row_dimensions[1].height = 26
        ws.freeze_panes = "C2"
        ws.auto_filter.ref = "A1:N1"

        note = wb.create_sheet("说明")
        rows = [
            "TikTok 对标工作台 · 视频台账",
            "",
            "1. 每个账号一张 sheet，名称必须是「对标记录-<账号名>」。",
            "2. 表头固定 14 列，不要改列名、不要插列、不要改顺序。",
            "3. 本文件必须放在台账目录（WB_LEDGER_DIR）内，工作台才能读到。",
            "4. 「对标记录-模板」只作格式模板：新增账号后用 pipeline\\scripts\\add_account_sheet.py 复制。",
            "5. 图文账号另有 12 列共用表，不在本文件。",
        ]
        for r, text in enumerate(rows, 1):
            note.cell(row=r, column=1, value=text)
        note.column_dimensions["A"].width = 80
        note["A1"].font = Font(name="微软雅黑", size=14, bold=True, color="1F4E79")

        path.parent.mkdir(parents=True, exist_ok=True)
        wb.save(path)
        return True, ""
    except OSError as exc:
        return False, f"台账无法创建（写入失败）：{path}（{exc.__class__.__name__}: {exc}）"
    except Exception as exc:  # noqa: BLE001
        return False, f"台账无法创建：{path}（{exc.__class__.__name__}: {exc}）"


def find_example(pipe: Path) -> Path | None:
    """accounts.example.json：先看当前 pipeline 目录，再退回包/仓库自带的 `pipeline\\`。

    （单测用 `--pipeline-dir <临时>` 时临时目录里当然没有示例文件，必须能退回仓库那份。）
    """
    for p in (pipe / "accounts.example.json", ROOT / "pipeline" / "accounts.example.json"):
        if p.is_file():
            return p
    return None


def create_accounts(pipe: Path, ledger: Path) -> tuple[bool, str]:
    """从 accounts.example.json 生成 accounts.json（示例账号全部停用）。返回 (成功, 失败原因)。"""
    p = pipe / "accounts.json"
    example = find_example(pipe)
    if example is None:
        return False, f"缺 accounts.example.json（找过 {pipe} 与 {ROOT / 'pipeline'}），无法生成 accounts.json"
    try:
        cfg = json.loads(example.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return False, f"accounts.example.json 解析失败：{exc.__class__.__name__}: {exc}"
    if not isinstance(cfg, dict):
        return False, "accounts.example.json 顶层不是对象"
    cfg["xlsx"] = str(ledger)
    cfg["metrics_file"] = str(pipe / "reports" / "daily_metrics.jsonl")
    cfg["reports_root"] = str(pipe / "reports")
    for acc in cfg.get("accounts", []):
        if not isinstance(acc, dict):
            continue
        acc["active"] = False          # 示例账号默认停用，用户自己加真账号
        acc["out_dir"] = str(pipe / "output")
        acc["audio_dir"] = str(pipe / "output" / f"{acc.get('account', 'account')}_audio")
    try:
        pipe.mkdir(parents=True, exist_ok=True)
        (pipe / "reports").mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        return True, ""
    except OSError as exc:
        return False, f"accounts.json 无法生成（写入失败）：{p}（{exc.__class__.__name__}: {exc}）"


def init_package(pipe: Path, ledger_dir: Path, webapp: Path, dry: bool,
                 quiet: bool = False) -> list[tuple[str, str]]:
    """只创建缺失的目录/台账/accounts.json。返回 [(kind, message)]，空 = 全就绪。

    kind ∈ {dirs, ledger, accounts, accounts_parse, ledger_broken}；
    `ledger_broken` 是"只报不修"的告警（坏台账绝不覆盖），调用方要打 [WARN] 而不是 [FAIL]。
    """
    def info(text: str) -> None:
        if not quiet:
            print(text)

    problems: list[tuple[str, str]] = []

    # 1) 目录：pipeline / ledger / output / reports / webapp\data
    for d in (pipe, ledger_dir, pipe / "output", pipe / "reports", webapp / "data"):
        if dry:
            continue
        try:
            d.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            problems.append(("dirs", f"包内目录不可写：{d}（{exc.__class__.__name__}: {exc}）"))
    if problems:                       # 目录都建不了，后面必然连锁失败，直接收口
        return problems

    # 2) 台账：只创建缺失，绝不覆盖（已存在时连 openpyxl 都不 import）
    accounts, acc_err = read_accounts(pipe)
    ledger = find_ledger(accounts, ledger_dir)
    if ledger is not None:
        info(f"{OK} 台账：{ledger}")
    else:
        target = ledger_dir / "tiktok_对标.xlsx"
        broken = find_broken_ledgers(accounts, ledger_dir)
        if broken:
            # 0 字节 / 截断的台账（openpyxl 非原子写的产物）：**只报不覆盖**，
            # 让用户知道该删哪一个、以及删了之后重启会自动重建。
            b = broken[0]
            try:
                size = b.stat().st_size
            except OSError:
                size = -1
            more = f"（另有 {len(broken) - 1} 本同样不可用）" if len(broken) > 1 else ""
            problems.append((
                "ledger_broken",
                f"台账文件损坏或为空：{b}（{size} 字节）{more} —— 工作台打不开它。"
                f" 删掉这个文件后重新启动会自动重建模板；或用 --ledger-dir 指向另一本台账。"
                f"（这里不会覆盖它）"))
        elif dry:
            info(f"{WARN} 台账不存在，将创建：{target}")
        else:
            ok, why = create_ledger(target)
            if ok:
                ledger = target
                info(f"{OK} 已创建台账模板：{target}")
            else:
                problems.append(("ledger", why))

    # 3) accounts.json：存在就只读不写；坏了如实报（这是允许的 [FAIL] 之一）
    if acc_err:
        problems.append(("accounts_parse",
                         f"accounts.json 无法解析：{pipe / 'accounts.json'}（{acc_err}）"))
    elif (pipe / "accounts.json").is_file():
        info(f"{OK} accounts.json：已存在（未改动）")
    elif ledger is None:
        info(f"{WARN} 没有台账，本轮的 accounts.json 生成被跳过")
    elif dry:
        info(f"{WARN} accounts.json 不存在，将生成（示例账号默认停用）")
    else:
        ok, why = create_accounts(pipe, ledger)
        if ok:
            info(f"{OK} accounts.json：已生成（示例账号默认停用）")
        else:
            problems.append(("accounts", why))
    return problems


def first_run_log_path() -> Path:
    log_dir = Path(os.environ.get("WB_LOG_DIR") or (ROOT / "logs"))
    return log_dir / "first-run.log"


def append_first_run_log(problems: list[tuple[str, str]]) -> Path | None:
    """`--init-only` 失败时把原因写进 logs\\first-run.log（失败不阻断启动）。"""
    p = first_run_log_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        # 每次启动都跑：若一直失败，不设上限这个文件会无限长（用户包里的隐患）
        if p.is_file() and p.stat().st_size > 512 * 1024:
            p.replace(p.with_suffix(".log.1"))
        import datetime as _dt

        stamp = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with p.open("a", encoding="utf-8") as f:
            for kind, msg in problems:
                f.write(f"[{stamp}] {kind}: {msg}\n")
        return p
    except OSError:
        return None


# ────────────────────────── 入口 ──────────────────────────
def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    # ── 每次启动的快路径（在 import argparse/pathlib 之前判断）──────────
    #   文件都在 → 无事可做：零输出、零写入、只碰 os/json/sys。
    quick = _fast_init_args(argv)
    if quick is not None:
        pipe_arg = quick["pipeline_dir"] or _default_pipe()
        ledger_arg = quick["ledger_dir"] or _default_ledger()
        if _package_ready(pipe_arg, ledger_arg):
            return 0

    import argparse                       # 按需：快路径不该付这个 import 的钱
    _init_paths()                         # 顺带把 pathlib 的 Path 挂成模块全局

    ap = argparse.ArgumentParser(description="TikTok 对标工作台 · 新机自检与初始化")
    ap.add_argument("--dry-run", action="store_true", help="只检查，不写文件")
    ap.add_argument("--ledger-dir", default=None,
                    help="台账目录（默认 WB_LEDGER_DIR 或 <包根>/ledger）")
    ap.add_argument("--pipeline-dir", default=None,
                    help="pipeline 目录（默认 WB_PIPELINE_DIR 或 <包根>/pipeline）")
    ap.add_argument("--no-network", action="store_true", help="跳过 TikTok 连通性测试")
    ap.add_argument("--portable", action="store_true",
                    help="便携包模式：不查 venv、缺件一律 [WARN]，[FAIL] 只留三类")
    ap.add_argument("--init-only", action="store_true",
                    help="每次启动的快路径：不检查、不联网、只补缺失文件（< 2 秒，退出码 0）")
    a = ap.parse_args(argv)

    pipe = Path(a.pipeline_dir) if a.pipeline_dir else Path(
        os.environ.get("WB_PIPELINE_DIR") or (ROOT / "pipeline"))
    ledger_dir = Path(a.ledger_dir) if a.ledger_dir else Path(
        os.environ.get("WB_LEDGER_DIR") or (ROOT / "ledger"))

    # ── 快路径：每次启动都跑，只补缺失文件，不联网、不检查、失败写日志继续 ──
    if a.init_only:
        problems = init_package(pipe, ledger_dir, WEBAPP, a.dry_run, quiet=True)
        if problems:
            log = append_first_run_log(problems)
            tail = f"，已写入日志：{log}" if log else "（日志写入失败）"
            _line(WARN, f"初始化未全部完成：{len(problems)} 项{tail}")
            for _kind, msg in problems:
                print(f"       - {msg}")
        return 0

    print("=" * 62)
    print("TikTok 对标工作台 · 首次运行自检" + ("（便携包）" if a.portable else ""))
    print("=" * 62)

    blocking = 0
    if a.portable:
        # 包里没有 venv：只如实打印包内解释器，绝不因为「缺 venv」报 FAIL
        check_package_python()
        check_python(portable=True)
    else:
        if not check_python():
            blocking += 1
        if not check_venv("pipeline", pipe / ".venv",
                          r'py -3.13 -m venv pipeline\.venv && pipeline\.venv\Scripts\python.exe -m pip install -r pipeline\requirements.txt'):
            blocking += 1
        if not check_venv("webapp", WEBAPP / ".venv",
                          r'python -m venv webapp\.venv && webapp\.venv\Scripts\python.exe -m pip install -r webapp\requirements.txt'):
            blocking += 1

    check_ffmpeg()

    proxy = detect_proxy(pipe / "scripts")
    check_proxy(proxy, portable=a.portable)
    if not a.no_network:
        check_tiktok(proxy)
    check_model()

    print("-" * 62)
    problems = init_package(pipe, ledger_dir, WEBAPP, a.dry_run)
    # `ledger_broken` 是"只报不修"的告警：坏台账绝不覆盖，只让用户知道去删哪个。
    # 它不算阻塞（检查环境.bat 的 [FAIL] 预算仍然只有三类），但必须打在屏幕上。
    fatal: list[tuple[str, str]] = []
    for kind, msg in problems:
        if kind == "ledger_broken":
            _line(WARN, msg)
        else:
            _line(FAIL, msg)
            fatal.append((kind, msg))

    cookie = pipe / "cc_cookie.txt"
    if cookie.is_file():
        _line(OK, "TikTok cookie：已存在")
    else:
        _line(WARN, "TikTok cookie 缺失：抓取会受限，需要你自己导出并放到 pipeline\\cc_cookie.txt")

    print("-" * 62)
    if a.portable:
        if fatal:
            print(f"\n有 {len(fatal)} 项阻塞问题，先按上面的提示处理再继续。")
            return 1
        print("\n环境检查通过。")
        return 0

    print("下一步：")
    print("  1) 启动看板：webapp\\start.bat 或 webapp\\.venv\\Scripts\\python.exe webapp\\app.py")
    print("  2) 浏览器打开 http://127.0.0.1:8789 ，点「＋ 新增账号」加真账号（面向地区/语言按国家填）")
    print("  3) 放台账/跑一键运行；首次转写会下载 whisper 模型（国内先设 HF_ENDPOINT）")
    blocking += len(fatal)             # ledger_broken 只告警，不改变退出码
    if blocking:
        print(f"\n有 {blocking} 项阻塞问题，先按上面的提示装好再继续。")
        return 1
    print("\n核心检查通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
