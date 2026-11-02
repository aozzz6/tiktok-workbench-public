# -*- coding: utf-8 -*-
r"""test_publish_artifacts.py — 可公开所需的最小产物（crew T-01）

覆盖 docs/tasks/tasks.md 里 T-01 的 DoD 1~4，以及三轮评审追加的要求：
  1. LICENSE 存在，是标准 MIT 全文；版权行有年份+名字（占位符或真名都行，
     所以作者填上自己的名字不会把测试弄红）
  2. pipeline/requirements.txt 每个包都在 pipeline\.venv 的 pip freeze 里（版本一致），
     且 torch / openai-whisper / yt-dlp / curl_cffi 必须在清单里；
     顶部要有 --extra-index-url 指向 PyTorch CPU 索引（否则 torch==2.7.0+cpu 装不上）
  3. pipeline/accounts.example.json 能被 webapp.config.load_accounts_raw() 读出结构，
     账号名是明显假名，不出现仓库里任何真实账号名（真实名单从 pipeline/accounts.json 读，
     读不到就打印 SKIP 跳过 —— 守卫自己不许硬编码任何真实 handle）
  4. README.md / README-zh.md 各追加一节，除旧四件事外还要有：建 venv 的两步命令、
     七个环境变量（含会让看板起不来的 WB_REVERSE_DIR）、代理写死在脚本常量里、
     浏览器兜底默认不可用、示例文件的 xlsx / metrics_file / reports_root 约束
  5. .gitignore 含 `*cookie*.txt`，能真的盖住 cc_cookie.txt
  6. THIRD_PARTY_NOTICES.md 列出字体与图表库及其各自许可证

另外三条结构性检查：
  - 两份 README 的 bat/powershell 代码块逐块比较（命令与路径必须完全一致，
    唯一允许的差异是既有「跑流水线」块里 <账号> / <account> 这个占位符的翻译）
  - README 里对 cookie 忽略规则的描述与 .gitignore 的实际规则一致
  - 其余既有章节的标题仍在（只追加，不改已有内容）

运行：D:\workbuddy_project\tiktok\pipeline\.venv\Scripts\python.exe pipeline\tests\test_publish_artifacts.py
"""
from __future__ import annotations

import fnmatch
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent      # pipeline/tests -> 仓库根
PIPE = ROOT / "pipeline"
WEBAPP = ROOT / "webapp"
PIPE_PY = PIPE / ".venv" / "Scripts" / "python.exe"

_ok = 0
_fail = 0


def check(name, fn):
    global _ok, _fail
    try:
        fn()
        print(f"  ok {name}")
        _ok += 1
    except AssertionError as e:
        print(f"  FAIL {name} -> {e}")
        _fail += 1
    except Exception as e:
        print(f"  FAIL {name} -> {e.__class__.__name__}: {e}")
        _fail += 1


def assert_(c, m="assert failed"):
    if not c:
        raise AssertionError(m)


def read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def canon(name: str) -> str:
    """PEP 503 归一化包名：curl_cffi / curl-cffi / curl.cffi 视为同一个。"""
    return re.sub(r"[-_.]+", "-", name).strip().lower()


# ══════════════════════════ DoD 1：LICENSE ══════════════════════════

def case_dod1_license_is_mit_with_a_named_copyright_line():
    p = ROOT / "LICENSE"
    assert_(p.exists(), f"LICENSE 不存在：{p}")
    t = read(p)
    assert_("MIT License" in t, "LICENSE 里没有 'MIT License' 标题")
    assert_("Permission is hereby granted, free of charge" in t,
            "LICENSE 里没有 'Permission is hereby granted, free of charge'（标准 MIT 正文）")
    assert_('THE SOFTWARE IS PROVIDED "AS IS"' in t,
            'LICENSE 里没有 \'THE SOFTWARE IS PROVIDED "AS IS"\'（免责段）')
    assert_("WITHOUT WARRANTY OF ANY KIND" in t, "LICENSE 里没有 'WITHOUT WARRANTY OF ANY KIND'")
    # 版权行要有年份、要有名字。占位符 `<YOUR NAME>` 和作者自己填的真名**都算合格** ——
    # 发布流程要求作者把占位符换成自己的名字，若这里锁死占位符，一填名字测试就红。
    lines = [ln.strip() for ln in t.splitlines() if ln.startswith("Copyright (c)")]
    assert_(lines, "LICENSE 里找不到以 'Copyright (c)' 开头的版权行")
    pat = re.compile(r"^Copyright \(c\) \d{4} \S.*$")
    assert_(pat.match(lines[0]),
            f"版权行不合格：{lines[0]!r}（要求形如 'Copyright (c) 2026 <名字>'，年份 4 位、后面非空）")
    assert_(lines[0] != "Copyright (c) 2026", "版权行只有年份、没有名字")


# ══════════════════ DoD 2：requirements.txt 与 pip freeze 双向核对 ══════════════════

def _freeze_runs() -> tuple[str, str]:
    r"""返回 (跑 pip freeze 用的解释器, 输出)。优先 pipeline\.venv（DoD 指定的那个环境）。"""
    py = PIPE_PY if PIPE_PY.exists() else Path(sys.executable)
    r = subprocess.run([str(py), "-m", "pip", "freeze"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert_(r.returncode == 0, f"pip freeze 失败（rc={r.returncode}）：{r.stderr[-400:]}")
    return str(py), r.stdout


def parse_freeze(text: str) -> dict[str, tuple[str, str]]:
    """pip freeze -> {归一化包名: (形式, 值)}。形式 == / url。"""
    out = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if " @ " in line:
            n, val = line.split(" @ ", 1)
            out[canon(n)] = ("url", val.strip())
        elif "==" in line:
            n, val = line.split("==", 1)
            out[canon(n)] = ("==", val.strip())
    return out


def parse_requirements(text: str) -> list[tuple[str, str, str]]:
    """requirements.txt -> [(原始包名, 版本, 原始行)]；注释与空行跳过。"""
    out = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        # pip 的选项行（--extra-index-url … / -r … / --find-links …）不是包，跳过
        if line.startswith("-"):
            continue
        m = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)\s*==\s*(\S+)$", line)
        assert_(m, f"清单这一行不是 `包名==版本` 形式：{line!r}")
        out.append((m.group(1), m.group(2), line))
    return out


def _freeze_satisfies(entry: tuple[str, str], name: str, version: str) -> bool:
    kind, val = entry
    if kind == "==":
        return canon(val) == canon(version)
    # 直接 URL 安装（本机 torch 就是这种：torch @ file:///.../torch-2.7.0%2Bcpu-cp313-...whl）
    u = urllib.parse.unquote(val).lower()
    return f"{name.lower()}-{version.lower()}-" in u or f"{canon(name)}-{version.lower()}-" in u


def case_dod2_every_requirement_is_installed():
    p = PIPE / "requirements.txt"
    assert_(p.exists(), f"pipeline/requirements.txt 不存在：{p}")
    reqs = parse_requirements(read(p))
    assert_(reqs, "requirements.txt 里一个包都没有")
    py, freeze_text = _freeze_runs()
    fz = parse_freeze(freeze_text)
    print(f"       （核对基准：{py}，freeze 里 {len(fz)} 个包）")
    missing, mismatch = [], []
    for name, ver, raw in reqs:
        e = fz.get(canon(name))
        if e is None:
            missing.append(raw)
        elif not _freeze_satisfies(e, name, ver):
            mismatch.append(f"{raw}（freeze 里是 {e[0]}:{e[1]}）")
    assert_(not missing, f"清单里这些包 pip freeze 里没有：{missing}")
    assert_(not mismatch, f"清单版本与 freeze 不一致：{mismatch}")
    # 反向：不许把 freeze 里没有的东西写进清单（上面 missing 已覆盖），
    # 也不许把整份 freeze 抄进来（间接依赖几百行）——清单应只含直接依赖
    assert_(len(reqs) <= 15,
            f"清单有 {len(reqs)} 行，像是把整份 pip freeze 抄进来了；只应列项目直接用的包")


def case_dod2b_required_packages_present():
    p = PIPE / "requirements.txt"
    assert_(p.exists(), f"pipeline/requirements.txt 不存在：{p}")
    text = read(p)
    names = {canon(n) for n, _v, _r in parse_requirements(text)}
    for need in ("torch", "openai-whisper", "yt-dlp", "curl-cffi"):
        assert_(need in names, f"清单里缺 **{need}**（项目真的用到，DoD 要求必须在）")
    # torch 必须是 CPU 版（本机实测 torch.__version__ == 2.7.0+cpu）
    torch_line = next((r for n, _v, r in parse_requirements(text) if canon(n) == "torch"), "")
    assert_("+cpu" in torch_line,
            f"torch 应标成 CPU 版（本机实装 2.7.0+cpu），实得 {torch_line!r}")
    # torch==2.7.0+cpu 在 PyPI 上不存在，只在 PyTorch 的 CPU 索引里 —— 清单必须自己带
    # --extra-index-url（否则读者照抄 pip install -r 会 No matching distribution found）。
    # 用 extra- 而不是 --index-url：PyPI 仍是主索引，其余包照常从 PyPI 装。
    opts = [ln.strip() for ln in text.splitlines() if ln.strip().startswith("--extra-index-url")]
    assert_(opts, "requirements.txt 顶部没有 --extra-index-url，torch==2.7.0+cpu 会装不上")
    assert_(any("download.pytorch.org/whl/cpu" in o for o in opts),
            f"--extra-index-url 没指向 PyTorch CPU 索引：{opts}")
    assert_(not [ln for ln in text.splitlines() if ln.strip().startswith("--index-url")],
            "清单里出现了 --index-url：会把所有包都指向该索引，应改用 --extra-index-url")


# ══════════════ DoD 3：accounts.example.json（假名字 + 真 schema） ══════════════

FAKE_NAME_RE = re.compile(r"^example_account_\d+$")
# webapp/config.py 认识的账号条目字段（DEFAULT_ACCOUNT_FIELDS + default_account + 扩展位）
KNOWN_ACCOUNT_KEYS = {
    "account", "lang", "out_dir", "audio_dir", "nickname", "active", "tags", "note",
    "region", "media_type",
}
# daily_update.py 用硬下标读的顶层键（不是 .get）：缺一个就是启动即 KeyError。
# :726 Path(cfg["reports_root"]) / :288、:299 cfg["metrics_file"]
REQUIRED_TOP_KEYS = ("metrics_file", "reports_root")


def real_account_names() -> list[str] | None:
    """真实账号名从 pipeline/accounts.json 读 —— 这个文件不进 git。

    **本测试会随公开版一起发布，所以这里不许硬编码任何真实 handle**：守卫本身不能
    变成泄露源。读不到就返回 None，调用方打印 SKIP 并跳过该项检查（不静默通过）。
    """
    p = PIPE / "accounts.json"
    if not p.exists():
        return None
    try:
        data = json.loads(read(p))
    except Exception:
        return None
    names = [a.get("account") for a in data.get("accounts", [])
             if isinstance(a, dict) and a.get("account")]
    return names or None


def case_dod3_example_schema_and_fake_names():
    p = PIPE / "accounts.example.json"
    assert_(p.exists(), f"pipeline/accounts.example.json 不存在：{p}")
    raw_text = read(p)
    data = json.loads(raw_text)
    assert_(isinstance(data, dict), "示例文件的根不是 JSON 对象")
    assert_("accounts" in data, "示例文件里没有 accounts 键")
    # 顶层必填键：daily_update.py 用 cfg[...] 硬下标读它们（不是 .get），
    # 示例文件缺了就会让照抄的人「复制完一跑日更就 KeyError」。
    for k in REQUIRED_TOP_KEYS:
        assert_(k in data and isinstance(data[k], str) and data[k].strip(),
                f"示例文件缺顶层键 {k!r}（pipeline/daily_update.py 用 cfg[{k!r}] 硬下标读，"
                f"缺了就是启动即 KeyError）")
    assert_("xlsx" in data, "示例文件缺顶层键 'xlsx'（台账路径）")
    accs = data["accounts"]
    assert_(isinstance(accs, list) and accs, "示例文件的 accounts 不是非空数组")
    for i, a in enumerate(accs):
        assert_(isinstance(a, dict), f"accounts[{i}] 不是对象")
        assert_("account" in a and isinstance(a["account"], str), f"accounts[{i}] 缺 account 字符串")
        assert_(FAKE_NAME_RE.match(a["account"]),
                f"accounts[{i}] 的账号名 {a['account']!r} 不是明显假名（应形如 example_account_1）")
        extra = set(a) - KNOWN_ACCOUNT_KEYS
        assert_(not extra, f"accounts[{i}] 有 webapp/config.py 不认识的字段：{sorted(extra)}")
    low = raw_text.lower()
    # 真实账号名单：从 accounts.json 读；读不到就 SKIP（不静默通过，也不写死名字）
    names = real_account_names()
    if names is None:
        print("       SKIP: accounts.json 不存在，跳过真实账号名检查")
    else:
        leaked = [n for n in names if n.lower() in low]
        assert_(not leaked,
                f"示例文件里出现了真实 accounts.json 的账号名：{leaked[:5]}")
    # 可移植性：不许带本机的用户目录/项目绝对路径
    for bad in ("workbuddy_project", "Administrator", "C:\\Users", "C:/Users"):
        assert_(bad.lower() not in low, f"示例文件里带了本机路径特征 {bad!r}（示例应可移植）")


def case_dod3b_example_readable_via_config():
    """DoD 要求的口径：WB_PIPELINE_DIR 指到临时目录后，用 config.load_accounts_raw() 读它。"""
    src = PIPE / "accounts.example.json"
    assert_(src.exists(), f"pipeline/accounts.example.json 不存在：{src}")
    py = PIPE_PY if PIPE_PY.exists() else Path(sys.executable)
    with tempfile.TemporaryDirectory(prefix="pub_example_") as td:
        td_p = Path(td)
        shutil.copyfile(src, td_p / "accounts.json")   # 换成真文件名，让 config 去找它
        code = (
            "import json, sys\n"
            f"sys.path.insert(0, {str(WEBAPP)!r})\n"
            "import config\n"
            "print(json.dumps({'path': str(config.ACCOUNTS_JSON),"
            " 'raw': config.load_accounts_raw()}, ensure_ascii=False))\n"
        )
        # 清掉外面可能已经设好的 WB_*，只让临时目录生效（否则会读到真的账号清单）
        env = {k: v for k, v in os.environ.items() if not k.startswith("WB_")}
        env["WB_PIPELINE_DIR"] = str(td_p)
        r = subprocess.run([str(py), "-c", code], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", env=env)
        assert_(r.returncode == 0,
                f"用临时 WB_PIPELINE_DIR 加载示例文件失败（rc={r.returncode}）：{r.stderr[-500:]}")
        got = json.loads(r.stdout)
        assert_(Path(got["path"]) == td_p / "accounts.json",
                f"config 读的不是临时目录里的那份：{got['path']}")
        raw = got["raw"]
        assert_(isinstance(raw, dict) and raw.get("accounts"),
                f"load_accounts_raw() 没读出 accounts：{str(raw)[:200]}")
        got_names = [a.get("account") for a in raw["accounts"]]
        assert_(got_names and all(FAKE_NAME_RE.match(str(n)) for n in got_names),
                f"经 config 读出来的账号名不是明显的假名：{got_names}")


# ══════════════════ DoD 4：README 两份各一节，四件事都在 ══════════════════

# 七个环境变量：五个在 webapp/config.py，两个在 webapp/tk_reverse.py
ENV_VARS = ["WB_PIPELINE_DIR", "WB_PYTHON", "WB_HOME", "WB_LEDGER_DIR", "WB_PROXY",
            "WB_REVERSE_DIR", "WB_YTDLP"]
HARDCODED = ["pipeline/pipeline.py:36", "pipeline/pipeline.py:41", "pipeline/daily_update.py:44"]
PROXY = "127.0.0.1:7897"
VENV_CMD = r"py -3.13 -m venv pipeline\.venv"
PIP_CMD = r"pipeline\.venv\Scripts\python.exe -m pip install -r pipeline\requirements.txt"
SCRIPTS_PROXY = ["tiktok_fetch.py:13", "scrape_tiktok_video.py:34", "tk_audio_download.py:19"]


def section_body(text: str, heading_re: str) -> str:
    """取出以 heading_re 匹配的 ## 标题开始、到下一个 ## 标题之前的那一节。"""
    lines = text.splitlines()
    start = None
    for i, ln in enumerate(lines):
        if re.match(r"^##\s", ln) and re.search(heading_re, ln, re.I):
            start = i
            break
    assert_(start is not None, f"找不到标题 /^## .*{heading_re}/ 这一节")
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if re.match(r"^##\s", lines[j]):
            end = j
            break
    return "\n".join(lines[start:end])


def _check_section_common(body: str, label: str) -> None:
    """两份 README 那一节都必须有的事实。"""
    assert_("Windows" in body, f"{label} 没提 Windows")
    assert_("3.13" in body, f"{label} 没提 Python 3.13")
    # 七个环境变量
    for v in ENV_VARS:
        assert_(v in body, f"{label} 没提环境变量 {v}")
    # 主链路 3 处写死路径（带文件与行号）
    for hp in HARDCODED:
        assert_(hp in body, f"{label} 没给出要改的位置 {hp}（必须带文件与行号）")
    # 有人得先建 pipeline\.venv，否则第一条命令就报「找不到路径」
    assert_(VENV_CMD in body, f"{label} 没有建 venv 的命令：{VENV_CMD}")
    assert_(PIP_CMD in body, f"{label} 没有这条安装命令：{PIP_CMD}")
    # 代理写死在脚本常量里，WB_PROXY 只管看板
    assert_(PROXY in body, f"{label} 没提代理 {PROXY}")
    for sp in SCRIPTS_PROXY:
        assert_(sp in body, f"{label} 没说清代理写死在哪：缺 {sp}")
    # 浏览器兜底默认不可用
    assert_("playwright" in body, f"{label} 没提 playwright（浏览器兜底要它）")
    # 示例文件的两个必填顶层键（daily_update.py 硬下标）
    for k in REQUIRED_TOP_KEYS:
        assert_(k in body, f"{label} 没提示例文件的必填键 {k}")


def case_dod4_readme_en():
    p = ROOT / "README.md"
    assert_(p.exists(), "README.md 不存在")
    body = section_body(read(p), r"own machine")
    _check_section_common(body, "README.md 那一节")
    assert_("cookie" in body.lower(), "英文那一节没提 TikTok 会话 cookie")
    assert_(re.search(r"must never be committed|never commit|do not commit", body, re.I),
            "英文那一节没写 cookie 绝不许提交")
    # xlsx 必须落在 LEDGER_DIR 里，否则看板静默找不到台账
    assert_("LEDGER_DIR" in body and re.search(r"silently|no error", body, re.I),
            "英文那一节没说「台账不在 LEDGER_DIR 里会被静默忽略」")


def case_dod4_readme_zh():
    p = ROOT / "README-zh.md"
    assert_(p.exists(), "README-zh.md 不存在")
    body = section_body(read(p), r"你自己机器上")
    _check_section_common(body, "中文那一节")
    assert_("cookie" in body.lower(), "中文那一节没提 TikTok 会话 cookie")
    assert_("绝不能提交" in body or "不许提交" in body, "中文那一节没写 cookie 绝不许提交")
    assert_("LEDGER_DIR" in body and "静默" in body,
            "中文那一节没说「台账不在 LEDGER_DIR 里会被静默忽略」")


# ══════════ 两份 README 的命令必须真的一致（不再只看几个字符串） ══════════

BLOCK_LANGS = ("bat", "powershell")
# 唯一允许的差异：既有「跑流水线」块里 <账号> / <account> 这个占位符的翻译。
# 它属于既有内容、不是本次新增，所以只在这一处做归一化，并且下面会断言
# 「<账号> 只出现在那一个块里」，避免这条归一化被拿去掩盖别的差异。
PLACEHOLDER_MAP = (("<账号>", "<account>"),)


def code_blocks(text: str) -> list[tuple[str, list[str]]]:
    """抽出 ```bat / ```powershell 代码块。"""
    out, cur, lang = [], None, None
    for line in text.splitlines():
        m = re.match(r"^```(\w+)\s*$", line.strip())
        if cur is None:
            if m and m.group(1).lower() in BLOCK_LANGS:
                cur, lang = [], m.group(1).lower()
        else:
            if line.strip() == "```":
                out.append((lang, cur))
                cur, lang = None, None
            else:
                cur.append(line)
    return out


def block_signature(lines: list[str]) -> tuple[str, ...]:
    """去掉空行 / 纯注释行 / 行尾注释，剩下的才是「命令与路径」。"""
    kept = []
    for ln in lines:
        s = ln.strip()
        if not s:
            continue
        if s.lower().startswith("rem ") or s.startswith("::") or s.startswith("#"):
            continue
        s = re.sub(r"\s+#.*$", "", s).rstrip()
        if s:
            kept.append(s)
    return tuple(kept)


def case_two_readmes_commands_match():
    """两份 README 的 bat/powershell 块逐块比较：命令与路径必须完全相同。

    允许的例外只有一个：既有「跑流水线」块里的占位符 <账号> / <account>
    （中文版把它翻译了）。除此之外任何差异 —— 少一个块、copy 写成 xcopy、
    端口不一样、路径不一样 —— 都会红。
    """
    en_blocks = code_blocks(read(ROOT / "README.md"))
    zh_blocks = code_blocks(read(ROOT / "README-zh.md"))
    assert_(en_blocks, "README.md 里没找到任何 bat/powershell 代码块")
    assert_(zh_blocks, "README-zh.md 里没找到任何 bat/powershell 代码块")

    def norm(sig: tuple[str, ...]) -> tuple[str, ...]:
        out = []
        for s in sig:
            for a, b in PLACEHOLDER_MAP:
                s = s.replace(a, b)
            out.append(s)
        return tuple(out)

    en = {norm(block_signature(lines)) for _lang, lines in en_blocks}
    zh = {norm(block_signature(lines)) for _lang, lines in zh_blocks}
    only_en = sorted(en - zh)
    only_zh = sorted(zh - en)
    assert_(not only_en, f"这些代码块只在 README.md 里有（中文版缺）：{only_en}")
    assert_(not only_zh, f"这些代码块只在 README-zh.md 里有（英文版缺）：{only_zh}")

    # 归一化必须只作用于那个已知占位符，不能掩盖别的差异
    zh_raw = {block_signature(lines) for _lang, lines in zh_blocks}
    holders = [b for b in zh_raw if any("<账号>" in s for s in b)]
    assert_(len(holders) == 1,
            f"中文版里有 {len(holders)} 个块含 <账号>（应只有「跑流水线」那一个）")
    en_raw = {block_signature(lines) for _lang, lines in en_blocks}
    assert_(not any("<账号>" in s for b in en_raw for s in b),
            "英文版里出现了 <账号>（应统一写成 <account>）")
    # 责任范围：这些关键命令必须真的在两边都出现过
    all_sigs = [s for b in (en | zh) for s in b]
    for must in (VENV_CMD, PIP_CMD, r"copy pipeline\accounts.example.json pipeline\accounts.json"):
        assert_(any(must == s or s.startswith(must) for s in all_sigs),
                f"两份 README 的代码块里都找不到这条命令：{must}")


# ══════════════ 追加要求：.gitignore 能盖住 cc_cookie.txt ══════════════

def case_gitignore_covers_session_cookie():
    p = ROOT / ".gitignore"
    assert_(p.exists(), ".gitignore 不存在")
    text = read(p)
    pats = [ln.strip() for ln in text.splitlines() if ln.strip() and not ln.strip().startswith("#")]
    assert_("*cookie*.txt" in pats,
            f".gitignore 里没有 `*cookie*.txt` 这一行；现有 cookie 规则 = "
            f"{[x for x in pats if 'cookie' in x.lower()]}")
    # 静态匹配：cc_cookie.txt 必须能被某条规则盖住
    assert_(any(fnmatch.fnmatch("cc_cookie.txt", pat) for pat in pats),
            "没有任何 .gitignore 规则能匹配 cc_cookie.txt")
    # 动态匹配：让 git 自己判（git 不可用就不强求）
    try:
        r = subprocess.run(["git", "check-ignore", "--no-index", "-q", "pipeline/cc_cookie.txt"],
                           cwd=str(ROOT), capture_output=True, text=True)
    except (FileNotFoundError, OSError):
        print("       （本机没有 git，跳过 check-ignore 动态核对）")
        return
    assert_(r.returncode in (0, 1),
            f"git check-ignore 异常退出 rc={r.returncode}：{r.stderr[-300:]}")
    assert_(r.returncode == 0,
            "git check-ignore 说 pipeline/cc_cookie.txt **仍然没有被忽略**（会话 cookie 会进版本库）")


# ══════════════ 防回退：两份 README 的其他既有章节仍在（只追加，不改已有内容）══════════════
#
# 唯一的例外是 "## Install" / "## 安装" 里那段「流水线环境没法从本仓库重建」——
# 加了 pipeline/requirements.txt 之后那句话就成了假话，PM 已授权把它改成事实。
# 那一段由下面的 case_install_section_tells_the_truth 专门检查；
# 这里只锁**其余**既有章节：它们的标题必须原样还在。

def case_existing_readme_sections_kept():
    en = read(ROOT / "README.md")
    for h in ("What is deliberately not in git", "First run on a fresh clone",
              "Run the tests", "Run the dashboard"):
        assert_(h in en, f"README.md 丢了既有章节标题：{h!r}")
    zh = read(ROOT / "README-zh.md")
    for h in ("有意不进 git 的东西", "新机器上第一次跑", "跑测试", "启动看板"):
        assert_(h in zh, f"README-zh.md 丢了既有章节标题：{h!r}")


# ══════════ 安装段已改成事实（唯一允许改动的既有段落）══════════

OLD_CLAIM_EN = "The pipeline environment is not reproducible from this repository"
# 更短的片段：即使将来那句话被折行，这个也还能捕捉到（已核实基线里它确实存在）
OLD_CLAIM_FRAGMENT_EN = "not reproducible from this repository"
OLD_CLAIM_ZH = "流水线环境没法从本仓库重建"
# 中英两版必须一字不差的安装命令
INSTALL_CMD = r"pipeline\.venv\Scripts\python.exe -m pip install -r pipeline\requirements.txt"


def case_install_section_tells_the_truth():
    en_text = read(ROOT / "README.md")
    zh_text = read(ROOT / "README-zh.md")

    # 1) 旧的那句假话必须消失（两份都不能再出现）
    #    已核实：在基线提交 19c0ba7 里这句是**一整行**（README.md 第 73 行、README-zh.md 第 62 行），
    #    所以这条断言在改动前确实是红的 —— 不是一句永远成立的废话。
    #    另外再锁一个更短的片段，防止将来有人把句子折行后整句匹配失效。
    assert_(OLD_CLAIM_EN not in en_text,
            f"README.md 里还留着那句已经不成立的旧话：{OLD_CLAIM_EN!r}")
    assert_(OLD_CLAIM_FRAGMENT_EN not in en_text.lower(),
            f"README.md 里还有旧话的片段：{OLD_CLAIM_FRAGMENT_EN!r}")
    assert_(OLD_CLAIM_ZH not in zh_text,
            f"README-zh.md 里还留着那句已经不成立的旧话：{OLD_CLAIM_ZH!r}")

    # 2) 安装段现在要说清四件事：有 pin 文件 / 怎么装 / 是 Windows+3.13 CPU 轮子且跨平台未验证 / 少了哪两个包
    en = section_body(en_text, r"^## Install$")
    zh = section_body(zh_text, r"^## 安装$")
    for label, body, cross_plat_ok in (
        ("README.md", en, re.search(r"nothing here has been tried|not been tried|not verified|untested",
                                    en, re.I)),
        ("README-zh.md", zh, re.search(r"没验证|未验证|没有验证", zh)),
    ):
        assert_(r"pipeline\requirements.txt" in body,
                f"{label} 的安装段没提到 pipeline\\requirements.txt")
        assert_(INSTALL_CMD in body,
                f"{label} 的安装段没给出这条一字不差的安装命令：{INSTALL_CMD}")
        assert_("torch==2.7.0+cpu" in body,
                f"{label} 的安装段没点明 torch 是 CPU 轮子（torch==2.7.0+cpu）")
        assert_("Windows" in body and "3.13" in body,
                f"{label} 的安装段没写明这套 pin 是 Windows + Python 3.13 的")
        assert_("Linux" in body and "macOS" in body and cross_plat_ok,
                f"{label} 的安装段没写明跨平台（Linux/macOS）未验证")
        assert_("scrapling" in body,
                f"{label} 的安装段没说 scrapling 不在清单里（可选，缺了退 curl_cffi）")
        assert_("playwright" in body,
                f"{label} 的安装段没说 playwright 不在清单里（只有浏览器兜底用）")


# ══════════ README 对 cookie 规则的描述必须与 .gitignore 一致 ══════════

def case_cookie_rules_consistent():
    """README 里讲「不提交 cookie」那段，必须与 .gitignore 的实际规则一致。

    历史上的 bug 正是这里：`.gitignore` 有三条 cookie 规则（含 `*cookie*.txt`），
    而那段文字还写着「只有 `cookie.txt` 和 `cookie*.json` 两个模式」。
    """
    gi = [ln.strip() for ln in read(ROOT / ".gitignore").splitlines()
          if ln.strip() and not ln.strip().startswith("#")]
    cookie_pats = [p for p in gi if "cookie" in p.lower()]
    assert_(len(cookie_pats) >= 3, f".gitignore 的 cookie 规则少于 3 条：{cookie_pats}")
    assert_("*cookie*.txt" in cookie_pats, f".gitignore 缺 *cookie*.txt：{cookie_pats}")

    en = section_body(read(ROOT / "README.md"), r"deliberately not in git")
    zh = section_body(read(ROOT / "README-zh.md"), r"有意不进 git")
    for label, body, bad in (
        ("README.md", en, re.compile(r"\btwo\b[^.]{0,40}pattern", re.I)),
        ("README-zh.md", zh, re.compile(r"两个[^。]{0,12}模式")),
    ):
        # 必须提到新加的那个模式（旧文字漏掉的就是它）
        assert_("*cookie*.txt" in body,
                f"{label} 讲「不提交 cookie」那段没提 *cookie*.txt（与 .gitignore 不一致）")
        # 不许再写死条数：写死条数，以后再加规则又会对不上
        assert_(not bad.search(body),
                f"{label} 那段还在写死 cookie 规则的条数（正则 {bad.pattern}）")


# ══════════ 第三方资产的法律声明 ══════════

THIRD_PARTY = ROOT / "THIRD_PARTY_NOTICES.md"
TP_ASSETS = [
    ("webapp/static/fonts/dm-sans-latin.woff2", "DM Sans", "OFL-1.1"),
    ("pipeline/assets/echarts.min.js", "ECharts", "Apache-2.0"),
]


def case_third_party_notices():
    assert_(THIRD_PARTY.exists(), f"{THIRD_PARTY.name} 不存在")
    t = read(THIRD_PARTY)
    for path, name, lic in TP_ASSETS:
        # 声明的资产必须真在仓库里，别写一条指向空气的声明
        assert_((ROOT / path).exists(),
                f"THIRD_PARTY_NOTICES.md 写了 {path}，但这个文件不存在")
        assert_(path in t, f"THIRD_PARTY_NOTICES.md 没列出 {path}")
        assert_(name in t, f"THIRD_PARTY_NOTICES.md 没写资产名 {name}")
        assert_(lic in t, f"THIRD_PARTY_NOTICES.md 没写许可证 {lic}")
    assert_("MIT" in t, "THIRD_PARTY_NOTICES.md 没提整体许可证 MIT")
    assert_(re.search(r"does \*\*not\*\* extend|not covered here|its own license", t, re.I),
            "THIRD_PARTY_NOTICES.md 没写「这些资产不走 MIT、按各自许可证分发」")
    assert_("openfontlicense.org" in t, "没有 OFL 的官方链接")
    assert_("apache.org/licenses/LICENSE-2.0" in t, "没有 Apache-2.0 的链接")
    # 不许自己编造许可证全文：OFL 全文不该被抄进来，而应写「须随附」的待办
    assert_("SIL OPEN FONT LICENSE Version 1.1" not in t.upper(),
            "THIRD_PARTY_NOTICES.md 里塞了 OFL 全文 —— 应只写链接与「全文需随附」的待办")
    assert_("TODO" in t.upper(), "没有标出发布前要补齐许可证全文的 TODO")


def main() -> int:
    print("可公开所需的最小产物（LICENSE / requirements / 示例账号 / README 一节 / .gitignore）：")
    for name, fn in [
        ("DoD1 LICENSE 是标准 MIT 全文且版权行有年份+名字", case_dod1_license_is_mit_with_a_named_copyright_line),
        ("DoD2 requirements.txt 每行都在 pip freeze 里且版本一致", case_dod2_every_requirement_is_installed),
        ("DoD2 必备包齐全 + 自带的 PyTorch CPU 索引", case_dod2b_required_packages_present),
        ("DoD3 示例账号文件是真 schema + 明显假名 + 必填顶层键", case_dod3_example_schema_and_fake_names),
        ("DoD3 示例账号文件能被 config.load_accounts_raw() 读出", case_dod3b_example_readable_via_config),
        ("DoD4 README.md 那一节事实齐全", case_dod4_readme_en),
        ("DoD4 README-zh.md 那一节事实齐全", case_dod4_readme_zh),
        ("两份 README 的 bat/powershell 代码块逐块一致", case_two_readmes_commands_match),
        (".gitignore 含 *cookie*.txt 且能盖住 cc_cookie.txt", case_gitignore_covers_session_cookie),
        ("README 里对 cookie 规则的描述与 .gitignore 一致", case_cookie_rules_consistent),
        ("THIRD_PARTY_NOTICES.md 列出字体与图表库的许可证", case_third_party_notices),
        ("防回退：两份 README 的其余既有章节仍在", case_existing_readme_sections_kept),
        ("安装段已改成事实（旧假话消失、四件事说清）", case_install_section_tells_the_truth),
    ]:
        check(name, fn)
    print(f"\n通过 {_ok} / {_ok + _fail}")
    return 0 if _fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
