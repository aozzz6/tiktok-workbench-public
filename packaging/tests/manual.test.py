# -*- coding: utf-8 -*-
"""T-24 使用者说明书验收（`packaging\templates\使用说明.md`）。

查四类事：
1. 8 个主题都在（二级标题里能找到关键词）；
2. 四个具体事实、三个已知限制、≥5 条常见故障的关键词都在；
3. 不许出现作者机器路径、不许教用户双击 `python\\python.exe`、不许建议 pip；
4. **文中反引号里的每个包内路径都真的存在**（`logs\\_server_run.log` 只查目录，
   另有两个"用户自己才会有的文件"按例外处理，理由写在下面）。

跑法（仓库根）：`webapp\\.venv\\Scripts\\python.exe packaging\\tests\\manual.test.py`
包路径默认 `build\\portable\\slim`；可用环境变量 `WB_MANUAL_PKG` 指到别的构建产物。
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent          # packaging/tests
REPO = HERE.parent.parent                       # 仓库根
MANUAL = REPO / "packaging" / "templates" / "使用说明.md"
PKG = Path(os.environ.get("WB_MANUAL_PKG") or (REPO / "build" / "portable" / "slim"))

_ok = 0
_fail = 0


def check(name, fn):
    global _ok, _fail
    try:
        fn()
    except Exception as exc:                    # noqa: BLE001
        _fail += 1
        print(f"  FAIL {name} -> {exc}")
        return
    _ok += 1
    print(f"  ok   {name}")


def assert_(cond, message=""):
    if not cond:
        raise AssertionError(message or "断言失败")


TEXT = MANUAL.read_text(encoding="utf-8") if MANUAL.is_file() else ""
LINES = TEXT.splitlines()
H2 = [ln for ln in LINES if ln.startswith("## ")]


def has_any(*words):
    return any(w in TEXT for w in words)


# ─────────────────────────── 0. 基本形态 ───────────────────────────
def case_file_shape():
    assert_(MANUAL.is_file(), f"说明书不存在：{MANUAL}")
    assert_(200 <= len(LINES) <= 400, f"行数应在 200~400，实得 {len(LINES)}")
    assert_(len(H2) >= 8, f"二级标题应有至少 8 个，实得 {len(H2)}：{H2}")


# ─────────────────────────── 1. 8 个主题 ───────────────────────────
TOPICS = ["解除锁定", "解压", "启动", "首次使用", "更新", "备份", "常见故障", "版本与日志"]


def case_topics():
    missing = [t for t in TOPICS if not any(t in h for h in H2)]
    assert_(not missing, f"二级标题里缺这些主题：{missing}（现有：{H2}）")


# ─────────────────────────── 2. 四个具体事实 ───────────────────────────
def case_port_fallback():
    for port in ("8791", "8792", "8793"):
        assert_(port in TEXT, f"没写端口被占时换到 {port}")
    assert_("8789" in TEXT, "没写默认端口 8789")
    assert_(has_any("127.0.0.1", "浏览器"), "没写会打开哪个地址")
    assert_(has_any("被占用", "占用", "被占"), "没说清为什么要换端口")


def case_upgrade_keeps_data():
    for keep in ("ledger", "pipeline\\output", "models", "webapp\\data",
                 "workbuddy", "_setenv.local.bat"):
        assert_(keep in TEXT, f"升级那节没写「不要删 {keep}」")
    assert_(has_any("复制", "覆盖"), "没写升级=把新内容复制过去")
    assert_("替换" in TEXT, "没写粘贴时要选「替换」")
    assert_(has_any("不要删", "别删", "不删"), "没写清楚哪些东西不要删")


def case_log_location():
    assert_("logs\\_server_run.log" in TEXT, "没写日志在 logs\\_server_run.log")


def case_version():
    assert_("VERSION" in TEXT, "没写版本号看根目录 VERSION")
    assert_("版本" in TEXT, "没提版本号")


# ─────────────────────────── 3. 三个已知限制 ───────────────────────────
def case_limit_no_ytdlp():
    assert_("yt-dlp" in TEXT, "限制①没写 yt-dlp")
    assert_("链接" in TEXT, "限制①没写「粘贴链接」这件事")
    assert_("本地视频" in TEXT, "限制①没写明只能用本地视频")


def case_limit_ai_online():
    assert_(has_any("API key", "API Key", "api key"), "限制②没写 API key")
    assert_("联网" in TEXT, "限制②没写必须联网")
    assert_("models.json" in TEXT, "限制②没写 workbuddy\\models.json")


def case_limit_proxy():
    assert_("代理" in TEXT, "限制③没写代理")
    assert_("离线" in TEXT, "限制③没写哪些功能可以离线")


# ─────────────────────────── 4. 常见故障 ≥5 条 ───────────────────────────
def case_faults():
    s = TEXT.index("## ") if False else 0  # noqa: F841  （保持简单：全文匹配关键词）
    pairs = [
        ("杀软拦截 python.exe", ("杀软", "安全中心", "杀毒"), ("python.exe",)),
        ("端口被占", ("端口",), ("占",)),
        ("模型下载失败 + HF_ENDPOINT", ("模型",), ("HF_ENDPOINT",)),
        ("缺 VC 运行库", ("VC", "运行库"), ("msvcp140", "0xc000007b", "0xC000007B")),
        ("中文/空格路径", ("中文",), ("空格",)),
    ]
    for name, left, right in pairs:
        assert_(any(w in TEXT for w in left), f"常见故障缺「{name}」（左关键词）")
        assert_(any(w in TEXT for w in right), f"常见故障缺「{name}」（右关键词）")


# ─────────────────────────── 5. 禁止出现的东西 ───────────────────────────
def case_no_author_paths():
    for bad in ("D:\\tiktok-workbench", "C:\\Users", "C:/Users", "/home/"):
        assert_(bad not in TEXT, f"说明书里出现了作者机器路径：{bad}")


def case_no_double_click_python():
    assert_("python\\python.exe" not in TEXT,
            "说明书里出现了 python\\python.exe（不许教用户双击它；入口只有 启动工作台.bat）")


def case_no_pip():
    low = TEXT.lower()
    assert_("pip install" not in low, "说明书里出现了 pip install（包里没有 pip）")
    assert_("python -m pip" not in low, "说明书里出现了 python -m pip")


def case_no_dev_jargon():
    for word in ("venv", "PATH", "环境变量", "命令行参数"):
        assert_(word not in TEXT, f"说明书里出现了开发者词：{word}")


# ─────────────────────────── 6. 反引号里的路径真的存在 ───────────────────────────
CODE_RE = re.compile(r"`([^`\n]+)`")
# 用户自己才会有的文件（包里没有是对的）：说明理由，不许滥用。
LOCAL_ONLY = {
    "_setenv.local.bat": "用户自建的本地覆盖文件（可选），包里没有",
    "workbuddy\\models.json": "用户自己填 API key 的文件，首次用 AI 功能后才出现",
}
# 启动后才生成的日志：只查目录
DIR_ONLY = {"logs\\_server_run.log": "logs"}


def looks_like_package_path(token: str) -> bool:
    t = token.strip()
    if not t or " " in t:
        return False
    if t.lower().startswith(("http://", "https://")):
        return False
    if "\\" in t or "/" in t:
        return True
    return t in {"VERSION"} or t.endswith((".bat", ".md", ".exe", ".log", ".json", ".txt"))


def case_backticked_paths_exist():
    assert_(PKG.is_dir(), f"包不存在：{PKG}（先跑 packaging\\build_portable.py --flavor slim）")
    tokens = [t.strip() for t in CODE_RE.findall(TEXT)]
    paths = [t for t in tokens if looks_like_package_path(t)]
    assert_(paths, "说明书里一个反引号路径都没有？")
    missing = []
    for t in paths:
        if t in LOCAL_ONLY:
            continue
        if t in DIR_ONLY:
            if not (PKG / DIR_ONLY[t]).is_dir():
                missing.append(f"{t}（应查目录 {DIR_ONLY[t]}\\）")
            continue
        rel = t.replace("/", "\\").rstrip("\\")
        if not (PKG / rel).exists():
            missing.append(t)
    assert_(not missing, f"这些反引号路径在包里不存在：{missing}")
    print(f"       （抽查 {len(paths)} 个反引号路径，例外 {len([t for t in paths if t in LOCAL_ONLY])} 个）")


print("[1] 形态与 8 个主题")
check("说明书存在，200~400 行，≥8 个二级标题", case_file_shape)
check("8 个主题都在二级标题里（解除锁定/解压/启动/首次使用/更新/备份/常见故障/版本与日志）",
      case_topics)

print("[2] 四个具体事实")
check("端口被占 → 8791/8792/8793，并打开正确地址", case_port_fallback)
check("升级=复制新内容+选替换，且不删 ledger/pipeline\\output/models/webapp\\data/workbuddy/_setenv.local.bat",
      case_upgrade_keeps_data)
check("日志在 logs\\_server_run.log", case_log_location)
check("版本号看根目录 VERSION", case_version)

print("[3] 三个已知限制")
check("① 粘贴 TikTok 链接不可用（无 yt-dlp），只能用本地视频", case_limit_no_ytdlp)
check("② AI 翻译/对话要联网 + 自己填 API key（workbuddy\\models.json）", case_limit_ai_online)
check("③ 采集 TikTok 要你自己的代理；台账/对标/反推/转写可离线", case_limit_proxy)

print("[4] 常见故障 ≥5 条")
check("杀软拦 python.exe / 端口被占 / 模型下载失败(HF_ENDPOINT) / 缺 VC 运行库 / 中文·空格路径",
      case_faults)

print("[5] 不许出现的东西")
check("没有作者机器路径（D:\\tiktok-workbench / C:\\Users / /home/）", case_no_author_paths)
check("没教用户双击 python\\python.exe", case_no_double_click_python)
check("没建议 pip install", case_no_pip)
check("没有 venv / PATH / 环境变量 这类开发者词", case_no_dev_jargon)

print("[6] 反引号路径在包里真实存在")
check("每个反引号路径都能在包里找到（日志只查目录）", case_backticked_paths_exist)

print(f"\n通过 {_ok} / {_ok + _fail}")
sys.exit(0 if _fail == 0 else 1)
