#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""first_run.py — 新机部署自检 + 初始化（不覆盖任何已有文件）。

做什么
------
1. 检查 Python / 两个虚拟环境 / ffmpeg / 代理 / TikTok 连通性 / whisper 模型缓存
2. 缺什么就建什么（只创建、不覆盖）：
   - 台账目录（默认 <仓库>/ledger，可用 WB_LEDGER_DIR 覆盖）
   - 台账模板 tiktok_对标.xlsx（14 列表头 + 「对标记录-模板」+「说明」）
   - pipeline/accounts.json（从 accounts.example.json 生成，xlsx 指向上面那份台账）
   - pipeline/reports 目录
3. 打印还缺哪些人工步骤（TikTok cookie、账号、代理端口）

用法
----
    python first_run.py                 # 自检 + 自动补齐能补的
    python first_run.py --dry-run       # 只看结论，不写任何文件
    python first_run.py --ledger-dir D:/data/ledger
    python first_run.py --no-network    # 跳过 TikTok 连通性测试（离线时用）

推荐用 webapp 的 python 跑（有 openpyxl，才能建台账）：
    webapp\\.venv\\Scripts\\python.exe first_run.py
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PIPE = ROOT / "pipeline"
WEBAPP = ROOT / "webapp"
SCRIPTS = PIPE / "scripts"

OK, WARN, FAIL = "[ OK ]", "[WARN]", "[FAIL]"


def _line(flag: str, text: str) -> None:
    print(f"{flag} {text}")


def check_python() -> bool:
    major, minor = sys.version_info[:2]
    if (major, minor) >= (3, 13):
        _line(OK, f"Python {major}.{minor}（仓库按 3.13 验证）")
        return True
    if (major, minor) >= (3, 11):
        _line(WARN, f"Python {major}.{minor}：仓库按 3.13 验证，能跑但未验证")
        return True
    _line(FAIL, f"Python {major}.{minor} 太旧，需要 3.11+（推荐 3.13）")
    return False


def check_venv(name: str, path: Path, create_hint: str) -> bool:
    exe = path / "Scripts" / "python.exe"
    if exe.exists():
        _line(OK, f"{name} 虚拟环境：{exe}")
        return True
    _line(FAIL, f"{name} 虚拟环境缺失：{path}")
    print(f"       创建：{create_hint}")
    return False


def check_ffmpeg() -> bool:
    exe = shutil.which("ffmpeg")
    if exe:
        _line(OK, f"ffmpeg：{exe}")
        return True
    _line(WARN, "ffmpeg 不在 PATH：音频提取/转码会失败，装上并确保 ffmpeg -version 可用")
    return False


def detect_proxy() -> str:
    try:
        sys.path.insert(0, str(SCRIPTS))
        from proxy_detect import detect_proxy as _dp

        return _dp(force=True)
    except Exception as exc:  # noqa: BLE001
        _line(WARN, f"代理探测模块不可用：{exc}")
        return ""


def check_proxy(proxy: str) -> bool:
    if not proxy:
        _line(FAIL, "没探测到本机代理（抓取 TikTok 需要）")
        return False
    from urllib.parse import urlsplit

    parts = urlsplit(proxy)
    host, port = parts.hostname or "127.0.0.1", parts.port or 80
    try:
        with socket.create_connection((host, port), timeout=1.5):
            _line(OK, f"代理可用：{proxy}")
            return True
    except OSError:
        _line(WARN, f"探测到 {proxy}，但端口连不上：代理软件没启动？或设 WB_PROXY 指定端口")
        return False


def check_tiktok(proxy: str, timeout: int = 20) -> bool:
    """优先用 curl（与抓取层的 TLS 行为接近）；没有 curl 再退回 urllib。"""
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


def check_model() -> bool:
    hub = Path.home() / ".cache" / "huggingface" / "hub"
    hits = list(hub.glob("models--Systran--faster-whisper-*")) if hub.exists() else []
    if hits:
        for h in hits:
            size = sum(p.stat().st_size for p in h.rglob("*") if p.is_file())
            _line(OK, f"whisper 模型已缓存：{h.name}（{size / 1024 / 1024:.0f} MB）")
        return True
    _line(WARN, "没找到 faster-whisper 模型缓存：首次转写需要下载（约 1.5 GB）")
    print("       国内直连 HuggingFace 常失败，先设 HF_ENDPOINT=https://hf-mirror.com")
    print("       或把 medium-ct2 模型目录拷过来，用 --model-dir 指定")
    return False


def read_accounts() -> dict:
    p = PIPE / "accounts.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def find_ledger(accounts: dict, ledger_dir: Path) -> Path | None:
    raw = str(accounts.get("xlsx") or "").strip()
    if raw:
        configured = Path(raw)
        if configured.is_file():
            return configured
    if ledger_dir.exists():
        files = sorted(ledger_dir.glob("*.xlsx"))
        if files:
            return files[0]
    return None


COLS = ["记录日期", "对标账号", "视频发布时间", "视频标题", "话题标签",
        "视频链接/ID", "商品链接", "播放量", "点赞数", "评论数",
        "转发数", "收藏数", "音频", "翻译"]
WIDTHS = [14, 14, 18, 42, 30, 38, 32, 10, 10, 10, 10, 10, 40, 40]


def create_ledger(path: Path) -> bool:
    try:
        import openpyxl
        from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
        from openpyxl.utils import get_column_letter
    except Exception:  # noqa: BLE001
        _line(FAIL, "缺 openpyxl，无法创建台账：webapp\\.venv\\Scripts\\python.exe -m pip install openpyxl")
        return False

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
    return True


def create_accounts(ledger: Path, dry: bool) -> bool:
    p = PIPE / "accounts.json"
    example = PIPE / "accounts.example.json"
    if p.exists():
        return True
    if not example.exists():
        _line(FAIL, f"缺 {example.name}，无法生成 accounts.json")
        return False
    cfg = json.loads(example.read_text(encoding="utf-8"))
    cfg["xlsx"] = str(ledger)
    cfg["metrics_file"] = str(PIPE / "reports" / "daily_metrics.jsonl")
    cfg["reports_root"] = str(PIPE / "reports")
    for acc in cfg.get("accounts", []):
        acc["active"] = False          # 示例账号默认停用，用户自己加真账号
        acc["out_dir"] = str(PIPE / "output")
        acc["audio_dir"] = str(PIPE / "output" / f"{acc.get('account', 'account')}_audio")
    if dry:
        return True
    p.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    (PIPE / "reports").mkdir(parents=True, exist_ok=True)
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description="TikTok 对标工作台 · 新机自检与初始化")
    ap.add_argument("--dry-run", action="store_true", help="只检查，不写文件")
    ap.add_argument("--ledger-dir", default=None, help="台账目录（默认 WB_LEDGER_DIR 或 <仓库>/ledger）")
    ap.add_argument("--no-network", action="store_true", help="跳过 TikTok 连通性测试")
    a = ap.parse_args()

    print("=" * 62)
    print("TikTok 对标工作台 · 首次运行自检")
    print("=" * 62)

    blocking = 0
    if not check_python():
        blocking += 1
    if not check_venv("pipeline", PIPE / ".venv",
                      r'py -3.13 -m venv pipeline\.venv && pipeline\.venv\Scripts\python.exe -m pip install -r pipeline\requirements.txt'):
        blocking += 1
    if not check_venv("webapp", WEBAPP / ".venv",
                      r'python -m venv webapp\.venv && webapp\.venv\Scripts\python.exe -m pip install -r webapp\requirements.txt'):
        blocking += 1
    check_ffmpeg()

    proxy = detect_proxy()
    check_proxy(proxy)
    if not a.no_network:
        check_tiktok(proxy)
    check_model()

    ledger_dir = Path(a.ledger_dir) if a.ledger_dir else Path(
        os.environ.get("WB_LEDGER_DIR") or (ROOT / "ledger")
    )
    accounts = read_accounts()
    ledger = find_ledger(accounts, ledger_dir)

    print("-" * 62)
    if ledger:
        _line(OK, f"台账：{ledger}")
    else:
        target = ledger_dir / "tiktok_对标.xlsx"
        _line(WARN, f"台账不存在，将创建：{target}")
        if a.dry_run:
            _line(WARN, "dry-run：未写入")
        elif create_ledger(target):
            ledger = target
            _line(OK, f"已创建台账模板：{target}")

    if ledger and create_accounts(ledger, a.dry_run):
        _line(OK, f"accounts.json：{'已存在' if (PIPE / 'accounts.json').exists() else '已生成（示例账号默认停用）'}")
    elif not ledger:
        _line(FAIL, "没有台账，跳过 accounts.json 生成")

    cookie = PIPE / "cc_cookie.txt"
    if cookie.exists():
        _line(OK, "TikTok cookie：已存在")
    else:
        _line(WARN, "TikTok cookie 缺失：抓取会受限，需要你自己导出并放到 pipeline\\cc_cookie.txt")

    print("-" * 62)
    print("下一步：")
    print("  1) 启动看板：webapp\\start.bat 或 webapp\\.venv\\Scripts\\python.exe webapp\\app.py")
    print("  2) 浏览器打开 http://127.0.0.1:8789 ，点「＋ 新增账号」加真账号（面向地区/语言按国家填）")
    print("  3) 放台账/跑一键运行；首次转写会下载 whisper 模型（国内先设 HF_ENDPOINT）")
    if blocking:
        print(f"\n有 {blocking} 项阻塞问题，先按上面的提示装好再继续。")
        return 1
    print("\n核心检查通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
