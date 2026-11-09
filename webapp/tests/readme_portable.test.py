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
# 反向断言用的黑名单：运行时拼出来，避免 repo_paths.test.py 的扫描把这些字面量当成真的硬编码。
BAD_AUTHOR_PATHS = ("D:/1/" + "对标账号", "D:\\workbuddy" + "_project", "C:" + "\\Users")


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


# ───────────────────────── 3b. 文档评审（2026-11-09）的事实更正 ─────────────────────────
def case_license_copyright_line():
    for name, text in (("README.md", EN), ("README-zh.md", ZH)):
        assert_("Copyright (c) 2026 aozzz6" in text,
                f"{name} 没写真实版权行（LICENSE 里是 2026 aozzz6）")
        assert_("YOUR NAME" not in text, f"{name} 还在让读者去换不存在的 `YOUR NAME`")


def case_ledger_default():
    for name, text, marker in (("README.md", EN, "<package>"), ("README-zh.md", ZH, "<包>")):
        assert_("config.py:25" not in text, f"{name} 还在引用过期的 config.py:25")
        assert_("config.py:42" in text, f"{name} 没写台账默认值在 config.py:42")
        assert_("<package>\\ledger" in text or "<包>\\ledger" in text,
                f"{name} 没写台账默认是 {marker}\\ledger")
        assert_("D:/1/对标账号" not in text, f"{name} 还把作者路径当成默认台账目录")
        assert_("WB_LEDGER_DIR" in text, f"{name} 没写要换目录用 WB_LEDGER_DIR")


def case_admin_rights_caveat():
    body_en = _section(EN, r"(?i)^##+ .*portable")
    body_zh = _section(ZH, r"^##+ .*(绿色版|[Pp]ortable)")
    for name, body in (("README.md", body_en), ("README-zh.md", body_zh)):
        assert_("#24" in body and "#26" in body,
                f"{name} 把「不需要管理员权限」写成了已成立的事实，没标 #24 / #26 未验证")
        assert_("qa/gaps.md" in body or "qa\\gaps.md" in body, f"{name} 没指向 qa/gaps.md")


def case_artifacts_count():
    for name, text in (("README.md", EN), ("README-zh.md", ZH), ("CHANGELOG.md", CL)):
        assert_("8/13" not in text, f"{name} 还写着 publish_artifacts 8/13（实跑是 7/13）")
        assert_("7/13" in text, f"{name} 没写 7/13")


def case_placeholders_identical():
    for name, text in (("README.md", EN), ("README-zh.md", ZH)):
        assert_("<ffmpeg folder>" in text, f"{name} 的 ffmpeg 占位符不是英文 <ffmpeg folder>")
        assert_("ffmpeg 所在目录" not in text, f"{name} 还留着中文占位符（两份代码块就不一致了）")


def case_model_size():
    for name, text in (("README.md", EN), ("README-zh.md", ZH)):
        assert_("1.46 GB" not in text, f"{name} 还写 1.46 GB（统一成约 1.5 GB）")
        assert_("1.5 GB" in text, f"{name} 没写约 1.5 GB")


def case_no_dead_doc_reference():
    for name, text in (("README.md", EN), ("README-zh.md", ZH)):
        assert_("工作上下文归档" not in text, f"{name} 还引用不存在的 工作上下文归档_最新.md")


def case_make_zips_prereqs():
    for name, text in (("README.md", EN), ("README-zh.md", ZH)):
        assert_("hf-cache" in text, f"{name} 没写 full 需要 --hf-cache / HF 缓存")
        assert_("skip-build" in text, f"{name} 没写 --skip-build 缺标记会退 1")


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
    for bad in BAD_AUTHOR_PATHS:
        assert_(bad not in DG, f"DEPLOY_GUIDE.md 里还有作者机器路径：{bad}")


def case_deploy_guide_points_to_readme():
    head = "\n".join(DG.splitlines()[:14])
    assert_("README" in head, "DEPLOY_GUIDE.md 顶部没有指向 README")
    assert_(("绿色版" in head) or ("portable" in head.lower()), "顶部那段没提绿色版/portable")
    assert_("ledger" in DG, "DEPLOY_GUIDE.md 没有把台账指到包内 ledger\\")


def case_package_facts_en():
    """2026-11-09（文档评审）：yt-dlp 那句要收回（它的 launcher 写死开发机 Python 路径，跑不起来）；
    离线/联网要分栏，且「导入本地视频 + 抽帧」算离线、「出反推报告」算联网。"""
    body = _section(EN, r"(?i)^##+ .*portable")
    assert_("WB_YTDLP" not in body, "README.md 还在教用户设 WB_YTDLP（这个版本就是没接下载路）")
    assert_("ships it" not in body and "ships no yt-dlp" not in body,
            "README.md 还在说 yt-dlp 带了/没带，两种说法都不该有")
    assert_("pasting a tiktok link" in body.lower() or "pasting a link" in body.lower(),
            "README.md 没写「粘贴链接不会下载」")
    assert_("local file" in body.lower(), "README.md 没写请导入本地文件")
    assert_("offline" in body.lower(), "README.md 没写哪些功能离线可用")
    assert_("internet" in body.lower(), "README.md 没写哪些功能必须联网")
    offline_zone = body.lower().split("needs internet", 1)[0]
    assert_("local video" in offline_zone or "frames" in offline_zone,
            "「offline」那段里没写「导入本地视频 + 抽帧」")
    online_zone = body.lower().split("needs internet", 1)[1][:200] if "needs internet" in body.lower() else ""
    assert_("reverse" in online_zone, f"「needs internet」那段里没有反推/reverse：{online_zone!r}")


def case_package_facts_zh():
    body = _section(ZH, r"^##+ .*(绿色版|[Pp]ortable)")
    assert_("WB_YTDLP" not in body, "README-zh.md 还在教用户设 WB_YTDLP（这个版本就是没接下载路）")
    assert_("其实带了" not in body, "README-zh.md 还在说「其实带了 yt-dlp」")
    assert_("本地视频" in body, "README-zh.md 没写请导入本地视频")
    assert_("离线可用" in body, "README-zh.md 没写「离线可用」那一栏")
    assert_("需要联网" in body, "README-zh.md 没写「需要联网」那一栏")
    offline_zone = body.split("需要联网", 1)[0]
    assert_("本地视频" in offline_zone or "抽帧" in offline_zone,
            "「离线可用」那一栏里没写「导入本地视频 + 抽帧」")
    online = body.split("需要联网", 1)[1][:160] if "需要联网" in body else ""
    assert_("反推" in online, f"「需要联网」那一栏里没有反推：{online!r}")


print("[1] 两份 README 的绿色版一节")
check("README.md：portable 一节 + 同一条构建命令 + install.bat/两个 venv + packaging 测试跑法",
      case_readme_en_has_portable)
check("README-zh.md：绿色版一节 + 同一条构建命令 + install.bat/两个 venv + packaging 测试跑法",
      case_readme_zh_has_portable)
check("两份 README 用的是同一条构建命令", case_same_command_both_readmes)

print("[1b] 2026-11-09 QA 抓到的两处错误说法")
check("README.md：这个版本不支持粘贴链接；离线/联网分栏，抽帧在离线栏、反推在联网栏",
      case_package_facts_en)
check("README-zh.md：同上", case_package_facts_zh)

print("[1c] 2026-11-09 文档评审的事实更正")
check("版权行写 LICENSE 里的 `Copyright (c) 2026 aozzz6`，不再让读者换 `<YOUR NAME>`",
      case_license_copyright_line)
check("台账默认 `<包>\\ledger`（config.py:42）；start.bat 只在 D:\\1\\对标账号 存在时覆盖；换目录用 WB_LEDGER_DIR",
      case_ledger_default)
check("「不需要管理员权限」标了未验证（qa/gaps.md #24、#26）", case_admin_rights_caveat)
check("publish_artifacts 7/13（README 中英 + CHANGELOG 都不再是 8/13）", case_artifacts_count)
check("两份 README 的 ffmpeg 占位符都是英文 <ffmpeg folder>", case_placeholders_identical)
check("模型大小统一成约 1.5 GB", case_model_size)
check("不再引用不存在的 工作上下文归档_最新.md", case_no_dead_doc_reference)
check("make_zips 前置写了 HF 缓存与 --skip-build 退 1", case_make_zips_prereqs)

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
