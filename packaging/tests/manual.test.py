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
    """① 粘贴链接**就是不可用**；不许再说「其实带了 yt-dlp」或让用户去设 WB_YTDLP。"""
    assert_("yt-dlp" in TEXT, "限制①没写 yt-dlp")
    assert_("链接" in TEXT, "限制①没写「粘贴链接」这件事")
    assert_("本地视频" in TEXT, "限制①没写明请先把视频下到本地")
    assert_("其实带了" not in TEXT, "限制①还在说「其实带了 yt-dlp」（它跑不起来，不是「带了就能用」）")
    assert_("WB_YTDLP" not in TEXT, "限制①还在教用户去看 WB_YTDLP 这种内部设置")
    assert_(has_any("不支持", "还不支持", "不会下载"), "限制①没明确写「不支持/不会下载」")


def case_limit_ai_online():
    assert_(has_any("API key", "API Key", "api key"), "限制②没写 API key")
    assert_("联网" in TEXT, "限制②没写必须联网")
    assert_("models.json" in TEXT, "限制②没写 workbuddy\\models.json")
    # 2026-11-09（文档评审）：不许教用户手写 JSON，页面「AI 接入」才是指定动作
    assert_("AI 接入" in TEXT, "限制②没指到「AI 接入」页面")
    assert_("新增模型" in TEXT, "限制②没写「＋ 新增模型」这一步")


def case_limit_proxy():
    assert_("代理" in TEXT, "限制③没写代理")
    assert_("离线" in TEXT, "限制③没写哪些功能可以离线")
    # 2026-11-09（QA 抓错）：反推要调远程视觉模型，**不能**算进离线清单
    assert_("可以完全离线" not in TEXT, "限制③还在说「可以完全离线」——反推/翻译/对话都要联网")
    assert_("离线可用" in TEXT, "限制③应分两栏写：离线可用 / 需要联网")
    assert_("需要联网" in TEXT, "限制③没写哪些功能必须联网")
    online_part = TEXT.split("需要联网", 1)[1][:120] if "需要联网" in TEXT else ""
    assert_("反推" in online_part, f"「需要联网」那一栏里没有反推：{online_part!r}")
    # 2026-11-09（文档评审）：全站没有代理输入框，只有 WB_PROXY
    assert_("WB_PROXY" in TEXT, "限制③没写 WB_PROXY（要固定代理只能这么设）")
    assert_("系统代理" in TEXT, "限制③没写「工作台会自动用系统代理」")
    assert_("账号设置" not in TEXT or "代理" not in TEXT.split("账号设置", 1)[1][:40],
            "限制③还在说「在账号设置里填代理」——没有这个输入框")


# ─────────────────────────── 3b. 文档评审（2026-11-09）的 5 条 blocking ───────────────────────────
def case_zip_wrapper_dir():
    """ZIP 顶层有一层 tiktok-workbench-<版本>-full/，升级时要进到那一层再复制。"""
    assert_("tiktok-workbench-" in TEXT, "没写 ZIP 里那层外壳目录的名字")
    assert_(has_any("外壳", "这一层", "那一层", "双击进去"), "没说清「解压后多一层、要进去」")
    upgrade = TEXT.split("## 五", 1)[1] if "## 五" in TEXT else ""
    assert_("tiktok-workbench-" in upgrade, "§五 升级没提「进入 tiktok-workbench-… 这一层」")
    assert_(has_any("进入", "双击进去"), "§五 没写「进入这一层」")


def case_no_task_manager():
    assert_("任务管理器" not in TEXT, "还在教用户用任务管理器结束 Python 进程（常驻的是 pythonw.exe，会误杀）")
    assert_("重启电脑" in TEXT, "没给「要彻底停就重启电脑」这条")


def case_backup_lists_accounts():
    sec5 = TEXT.split("## 五", 1)[1] if "## 五" in TEXT else ""
    sec6 = TEXT.split("## 六", 1)[1] if "## 六" in TEXT else ""
    for body, where in ((sec5, "§五「不要删」"), (sec6, "§六「只备份主要数据」")):
        assert_("accounts.json" in body and "pipeline" in body,
                f"{where}清单里没有 pipeline 里的 accounts.json（账号清单）")
        for needle in ("pipeline\\reports", "reverse"):
            assert_(needle in body, f"{where}清单里没有 {needle}")


def case_ports_include_8787():
    assert_("8787" in TEXT, "端口候选里没写 8787（_launch.py 的最后一个候选）")


def case_version_on_page():
    assert_("右上角" in TEXT, "§八 没写「页面右上角也显示同一个版本号」")


def case_checkenv_description():
    for needle in ("代理", "台账", "cookie"):
        assert_(needle in TEXT, f"§四 检查环境 的描述里没提 {needle}")
    assert_("WARN" in TEXT, "没写 WARN 是提醒不是错误")


def case_ledger_origin():
    assert_("首次" in TEXT and "台账" in TEXT, "没写台账从哪来（首次自动建模板）")
    assert_("ledger" in TEXT, "没写旧台账放进 ledger\\ 再重启")


def case_windows11_properties():
    assert_("显示更多选项" in TEXT, "§一 没写 Win11 右键要先点「显示更多选项」")


def case_extract_time():
    assert_("2~3 分钟" in TEXT or "2-3 分钟" in TEXT, "§二 没写解压大约 2~3 分钟")


def case_offline_frames():
    offline = TEXT.split("离线可用", 1)[1][:120] if "离线可用" in TEXT else ""
    assert_("抽帧" in offline or "导入本地视频" in offline,
            f"「离线可用」那一栏没写「导入本地视频 + 抽帧」：{offline!r}")


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
    "pipeline\\accounts.json": "首次运行/加账号后才生成（包里带的是 accounts.example.json）",
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
check("① 粘贴链接就是不可用（不说「其实带了 yt-dlp」、不教 WB_YTDLP），请用本地视频", case_limit_no_ytdlp)
check("② AI 翻译/对话要联网 + 走页面「AI 接入 → ＋ 新增模型」填 key", case_limit_ai_online)
check("③ 采集要代理（WB_PROXY/系统代理）；离线可用 vs 需要联网分栏，反推在联网栏",
      case_limit_proxy)

print("[3b] 文档评审 2026-11-09 的 blocking")
check("ZIP 有一层外壳目录 tiktok-workbench-<版本>-…，§二 说明、§五 要求进这一层再复制",
      case_zip_wrapper_dir)
check("不再教用户用任务管理器；彻底停 = 重启电脑 / 重启工作台.bat", case_no_task_manager)
check("备份与「不要删」两处都含 pipeline\\accounts.json / pipeline\\reports / reverse",
      case_backup_lists_accounts)
check("端口候选含 8787", case_ports_include_8787)
check("§八 写了页面右上角也显示同一个版本号", case_version_on_page)
check("§四 检查环境的描述列了 代理/台账/cookie，并说明 WARN 不是错误", case_checkenv_description)
check("§四 写了台账从哪来（首次自动建模板 / 旧台账放进 ledger\\ 重启生效）", case_ledger_origin)
check("§一 写了 Win11 右键先点「显示更多选项」", case_windows11_properties)
check("§二 写了解压约 2~3 分钟", case_extract_time)
check("离线栏里含「导入本地视频 / 抽帧」", case_offline_frames)

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
