# -*- coding: utf-8 -*-
"""T-26 读者文档验收：README.md / README-zh.md / CHANGELOG.md / DEPLOY_GUIDE.md。

查的事（对着 T-26 DoD）：
1. 两份 README 都有「绿色版 / portable」一节，给**同一条**构建命令、都写「开发者检出流程不变
   （两个 venv + `install.bat`）」、都提到 `packaging\\tests\\*.test.py` 的跑法；
2. `CHANGELOG.md` 新增一节（标题含 VERSION 的版本号 + 日期 2026-11-05），正文含
   「两个 ZIP（完整/精简）」「包内不自带 LLM key」「包内不含 PyTorch」；
3. 许可表述：仓库源码 MIT；ZIP 内含 GPLv3 的 ffmpeg（及 PyAV 的 FFmpeg）→ 见
   `THIRD_PARTY_NOTICES.md`；**不许**再说「整个包/整个项目是 MIT」；
4. `README-zh.md` 保留 `**Linux / macOS 完全没验证过。**`，并新增 `qa\\run-all.ps1` 恒红那句；
5. `DEPLOY_GUIDE.md`：8787→8789、15 列→14 列、作者路径→包内 `ledger\\`，顶部指向 README 绿色版一节。

跑法（仓库根）：`webapp\\.venv\\Scripts\\python.exe webapp\\tests\\readme_portable.test.py`
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]      # webapp/tests -> webapp -> 仓库根
VERSION_FILE = REPO / "VERSION"

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


def read(name: str) -> str:
    p = REPO / name
    assert_(p.is_file(), f"缺少文件：{name}")
    return p.read_text(encoding="utf-8")


EN = read("README.md")
ZH = read("README-zh.md")
CL = read("CHANGELOG.md")
DG = read("DEPLOY_GUIDE.md")
VERSION = VERSION_FILE.read_text(encoding="utf-8").strip()
BUILD_CMD = r"webapp\.venv\Scripts\python.exe packaging\make_zips.py"


# ───────────────────────── 1. 两份 README 的绿色版一节 ─────────────────────────
def _section(text: str, pattern: str) -> str:
    """取某个二级标题到下一个二级标题之间的正文（找不到返回空串）。"""
    lines = text.splitlines()
    start = next((i for i, ln in enumerate(lines) if re.match(pattern, ln)), None)
    if start is None:
        return ""
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    return "\n".join(lines[start:end])


def case_readme_en_has_portable():
    body = _section(EN, r"(?i)^##+ .*portable")
    assert_(body, "README.md 没有「portable」二级标题")
    assert_(BUILD_CMD in body, f"README.md 的 portable 一节没有这条命令：{BUILD_CMD}")
    assert_("install.bat" in body, "README.md portable 一节没说 install.bat 仍在")
    assert_(re.search(r"(?i)two venvs?|both venvs?|两个 venv", body),
            "README.md portable 一节没说开发者流程仍是「两个 venv」")
    assert_(r"packaging\tests\*.test.py" in body or "packaging/tests/*.test.py" in body,
            "README.md portable 一节没给 packaging 测试的跑法")


def case_readme_zh_has_portable():
    body = _section(ZH, r"^##+ .*(绿色版|[Pp]ortable)")
    assert_(body, "README-zh.md 没有「绿色版 / portable」二级标题")
    assert_(BUILD_CMD in body, f"README-zh.md 的绿色版一节没有这条命令：{BUILD_CMD}")
    assert_("install.bat" in body, "README-zh.md 绿色版一节没说 install.bat 仍在")
    assert_("两个 venv" in body or "两个虚拟环境" in body,
            "README-zh.md 绿色版一节没说开发者流程仍是「两个 venv」")
    assert_(r"packaging\tests\*.test.py" in body or "packaging/tests/*.test.py" in body,
            "README-zh.md 绿色版一节没给 packaging 测试的跑法")


def case_same_command_both_readmes():
    assert_(EN.count(BUILD_CMD) >= 1, f"README.md 里没有 {BUILD_CMD}")
    assert_(ZH.count(BUILD_CMD) >= 1, f"README-zh.md 里没有 {BUILD_CMD}")


# ───────────────────────── 2. CHANGELOG 新一节 ─────────────────────────
def case_changelog_section():
    lines = CL.splitlines()
    idx = next((i for i, ln in enumerate(lines)
                if ln.startswith("## ") and "2026-11-05" in ln and VERSION in ln), None)
    assert_(idx is not None, f"CHANGELOG 里没有标题同时含 {VERSION} 与 2026-11-05 的节")
    end = next((i for i in range(idx + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    body = "\n".join(lines[idx:end])
    for needle, why in (
        ("两个 ZIP", "没写「两个 ZIP」"),
        ("完整", "没写完整包"),
        ("精简", "没写精简包"),
        ("LLM key", "没写「包内不自带 LLM key」"),
        ("PyTorch", "没写「包内不含 PyTorch」"),
    ):
        assert_(needle in body, f"CHANGELOG 的 {VERSION} 节{why}")
    assert_("models.json" in body or "key" in body, "没说 key 要用户自己填")


# ───────────────────────── 3. 许可表述 ─────────────────────────
BAD_LICENSE = re.compile(
    r"(整个包|整个项目|全包|the (whole|entire) (package|project))[^\n]{0,40}MIT", re.I)


def case_license_en():
    assert_("THIRD_PARTY_NOTICES" in EN, "README.md 许可段没指向 THIRD_PARTY_NOTICES.md")
    assert_("GPL" in EN, "README.md 许可段没写 GPLv3")
    assert_("ffmpeg" in EN, "README.md 许可段没提 ffmpeg")
    assert_("PyAV" in EN, "README.md 许可段没提 PyAV 的 FFmpeg")
    assert_("MIT" in EN, "README.md 许可段没写 MIT（仓库源码）")
    assert_(not BAD_LICENSE.search(EN), "README.md 仍在说「整个包/整个项目是 MIT」")


def case_license_zh():
    assert_("THIRD_PARTY_NOTICES" in ZH, "README-zh.md 许可段没指向 THIRD_PARTY_NOTICES.md")
    assert_("GPL" in ZH, "README-zh.md 许可段没写 GPLv3")
    assert_("ffmpeg" in ZH, "README-zh.md 许可段没提 ffmpeg")
    assert_("PyAV" in ZH, "README-zh.md 许可段没提 PyAV 的 FFmpeg")
    assert_("MIT" in ZH, "README-zh.md 许可段没写 MIT（仓库源码）")
    assert_(not BAD_LICENSE.search(ZH), "README-zh.md 仍在说「整个包/整个项目是 MIT」")


# ───────────────────────── 4. README-zh 的两句 ─────────────────────────
def case_zh_keeps_unverified_line():
    assert_("**Linux / macOS 完全没验证过。**" in ZH,
            "README-zh.md 里 `**Linux / macOS 完全没验证过。**` 原句不见了（别人写的已知缺口，必须保留）")


def case_zh_runall_note():
    for needle in (r"qa\run-all.ps1", "恒红", "qa\\gaps.md", "#21", "不拿 run-all 全绿当基线"):
        assert_(needle in ZH, f"README-zh.md 缺 run-all 说明的片段：{needle!r}")


# ───────────────────────── 5/6. DEPLOY_GUIDE ─────────────────────────
def case_deploy_guide_fresh():
    assert_("8787" not in DG, "DEPLOY_GUIDE.md 里还有过期端口 8787")
    assert_("8789" in DG, "DEPLOY_GUIDE.md 没写端口 8789")
    assert_("15 列" not in DG, "DEPLOY_GUIDE.md 里还有过期的「15 列」")
    assert_("14 列" in DG, "DEPLOY_GUIDE.md 没写「14 列」")
    for bad in ("D:/1/对标账号", "D:\\workbuddy_project", "C:\\Users"):
        assert_(bad not in DG, f"DEPLOY_GUIDE.md 里还有作者机器路径：{bad}")


def case_deploy_guide_points_to_readme():
    head = "\n".join(DG.splitlines()[:14])
    assert_("README" in head, "DEPLOY_GUIDE.md 顶部没有指向 README")
    assert_(("绿色版" in head) or ("portable" in head.lower()), "顶部那段没提绿色版/portable")
    assert_("ledger" in DG, "DEPLOY_GUIDE.md 没有把台账指到包内 ledger\\")


def case_package_facts_en():
    """2026-11-09（QA 抓错）：包里**带着** yt-dlp，真因是只认 WB_YTDLP；离线/联网要分栏写。"""
    body = _section(EN, r"(?i)^##+ .*portable")
    assert_("WB_YTDLP" in body, "README.md 没写真实原因：工作台只认 WB_YTDLP")
    assert_("yt-dlp" in body, "README.md 没提 yt-dlp")
    assert_("no yt-dlp.exe" not in body and "ships no yt-dlp" not in body,
            "README.md 还在说「包里没有 yt-dlp.exe」——包里其实带了")
    assert_("offline" in body.lower(), "README.md 没写哪些功能离线可用")
    assert_("internet" in body.lower(), "README.md 没写哪些功能必须联网")
    offline_zone = body.lower().split("needs internet", 1)[1][:160] if "needs internet" in body.lower() else ""
    assert_("reverse" in offline_zone, f"「needs internet」那段里没有反推/reverse：{offline_zone!r}")


def case_package_facts_zh():
    body = _section(ZH, r"^##+ .*(绿色版|[Pp]ortable)")
    assert_("WB_YTDLP" in body, "README-zh.md 没写真实原因：工作台只认 WB_YTDLP")
    assert_("yt-dlp" in body, "README-zh.md 没提 yt-dlp")
    assert_("没有随包" not in body, "README-zh.md 还在说「yt-dlp 没有随包」——包里其实带了")
    assert_("离线可用" in body, "README-zh.md 没写「离线可用」那一栏")
    assert_("需要联网" in body, "README-zh.md 没写「需要联网」那一栏")
    online = body.split("需要联网", 1)[1][:120] if "需要联网" in body else ""
    assert_("反推" in online, f"「需要联网」那一栏里没有反推：{online!r}")


print("[1] 两份 README 的绿色版一节")
check("README.md：portable 一节 + 同一条构建命令 + install.bat/两个 venv + packaging 测试跑法",
      case_readme_en_has_portable)
check("README-zh.md：绿色版一节 + 同一条构建命令 + install.bat/两个 venv + packaging 测试跑法",
      case_readme_zh_has_portable)
check("两份 README 用的是同一条构建命令", case_same_command_both_readmes)

print("[1b] 2026-11-09 QA 抓到的两处错误说法")
check("README.md：带了 yt-dlp 但只认 WB_YTDLP；离线/联网分栏且反推在联网栏", case_package_facts_en)
check("README-zh.md：同上", case_package_facts_zh)

print("[2] CHANGELOG 新一节")
check(f"CHANGELOG：标题含 {VERSION} 与 2026-11-05，正文含两个 ZIP / 无 LLM key / 无 PyTorch",
      case_changelog_section)

print("[3] 许可表述")
check("README.md：MIT 源码 + GPLv3 ffmpeg/PyAV + THIRD_PARTY_NOTICES，且不再说整个包 MIT", case_license_en)
check("README-zh.md：同上", case_license_zh)

print("[4] README-zh 保留与新增的两句")
check("保留 `**Linux / macOS 完全没验证过。**` 原句", case_zh_keeps_unverified_line)
check("新增 qa\\run-all.ps1 恒红、不拿全绿当基线的说明", case_zh_runall_note)

print("[5] DEPLOY_GUIDE 过期点")
check("端口 8789 / 14 列 / 不再有作者路径", case_deploy_guide_fresh)
check("顶部指向 README 绿色版一节，台账指到包内 ledger\\", case_deploy_guide_points_to_readme)

print(f"\n通过 {_ok} / {_ok + _fail}")
sys.exit(0 if _fail == 0 else 1)
