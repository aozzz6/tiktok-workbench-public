# -*- coding: utf-8 -*-
r"""qa/T-12/cases.py —— T-12「AI 接口地址不再重复拼接」的 QA 用例

用例清单来源：`.dsh-crew/jobs/fix-check-findings/T-12-plan.md`（C-01 ~ C-07，一条一条照写）。
DoD 来源：docs/tasks/tasks.md 里 T-12 的 DoD 第 1~4 条；C-07 是回归检查，不属于 DoD。

被测产物：`webapp/llm.py` 的 `_endpoint(style, model)` —— 纯函数，不联网、不发任何请求。
  · C-01 跑 `webapp/tests/llm_endpoint_url.test.py`（T-12 自己的新用例）看退出码（DoD 1 前半）；
  · C-02 文档核对：改前红 / 改后绿两段真实输出（DoD 1 后半，不是命令核对）；
  · C-03 openai 完整端点原样返回，不出现两个 /chat/completions（DoD 2）；
  · C-04 openai 基础地址仍补全（DoD 3 前半）；
  · C-05 anthropic / gemini 拼接规则不变（DoD 3 后半）；
  · C-06 既有用例 `webapp/tests/ai_llm.test.py` 仍全绿（DoD 4）；
  · C-07 全套 `qa\run-all.ps1` 没有被本任务弄红（回归）。

为什么要「重新 import」，不拿 127.0.0.1:8789 的活服务当判据：
  那个进程内存里跑的还是**改动前**的 llm.py，问它只会得到旧行为（正是本任务要修的行为），
  所以本文件按自己的位置推 sys.path，直接 import 磁盘上的 `webapp/llm.py`；
  cwd 换到哪儿都不影响（不用相对路径、不依赖当前目录）。

约定：不联网、不调真实模型 API（无费用）、不写产品代码、不改被测文件、不写别的任务的目录。
跑法：powershell -NoProfile -ExecutionPolicy Bypass -File qa\T-12\run.ps1
"""
from __future__ import annotations

import contextlib
import io as _io
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

# 控制台/管道编码可能不是 UTF-8：
#  - 直接跑在真控制台（isatty）时交给 Python 的默认处理，中文照常显示；
#  - 输出被管道/重定向接走时改用 UTF-8，免得宿主按 UTF-8 解码看到乱码。
for _stream in (sys.stdout, sys.stderr):
    try:
        if not _stream.isatty():
            _stream.reconfigure(encoding="utf-8", errors="replace")
        else:
            _stream.reconfigure(errors="replace")
    except Exception:                       # noqa: BLE001
        pass

# ── 路径：全部按本文件位置推，与 cwd 无关 ─────────────────────────────
_REPO = Path(__file__).resolve().parents[2]          # qa/T-12/cases.py -> 仓库根
_WEBAPP = _REPO / "webapp"
_PY = _WEBAPP / ".venv" / "Scripts" / "python.exe"   # 项目约定的解释器
_TEST = _WEBAPP / "tests" / "llm_endpoint_url.test.py"
_AI_LLM_TEST = _WEBAPP / "tests" / "ai_llm.test.py"
_JOB = _REPO / ".dsh-crew" / "jobs" / "fix-check-findings"
_RUN_ALL = _REPO / "qa" / "run-all.ps1"
_T12_QA = _REPO / "qa" / "T-12"

# T-12 拥有的文件（任务行 Owns）；C-07 只用它判断「失败是不是本任务造成的」。
_OWNED = ("webapp/llm.py", "webapp/tests/llm_endpoint_url.test.py")

# C-07 的基线：**qa/T-12 目录建起来之前**，在 2026-11-05 实测过的一遍
#   `powershell -NoProfile -ExecutionPolicy Bypass -File qa\run-all.ps1`
# 结果是「通过 2 / 失败 1」，唯一失败项 T-04(exit=1)（T-04 自己的 C-08/C-10 红，与 llm.py 无关）。
# 记在这里是为了在输出里说清「哪些红是本任务之前就有的」，而不是为了给本任务开绿灯：
# C-07 判红与否看的是「失败项里有没有指向 T-12 拥有的文件 / T-12 自己的入口」。
_BASELINE_FAILING = ("T-04",)
_BASELINE_NOTE = "qa/T-12 建目录前实测：通过 2 / 失败 1（T-04(exit=1)，T-04 自己的 C-08/C-10 红）"

# 计划里 C-02 要求红段能看到的失败症状
_DOUBLED = "/chat/completions/chat/completions"

_WEBAPP_STR = str(_WEBAPP)
if _WEBAPP_STR not in sys.path:
    sys.path.insert(0, _WEBAPP_STR)
import llm  # noqa: E402  —— 被测模块（磁盘上的当前版本，不是活服务内存里的旧版本）

_ok = 0
_fail = 0


# ────────────────────────── 小工具 ──────────────────────────
def assert_(cond, msg="assert failed"):
    if not cond:
        raise AssertionError(msg)


def _emit(text):
    for line in (text or "").splitlines():
        if line.strip():
            print("        " + line.rstrip())


def check(name, fn):
    global _ok, _fail
    buf = _io.StringIO()
    try:
        # 先把用例自己打的「证据」行缓冲着，这样日志里永远是 ok/FAIL 在前、证据在后
        with contextlib.redirect_stdout(buf):
            label = fn()
        # 用例可以返回一个字符串当这一条的标签（C-07 在嵌套 run-all 里用它说明「这次跳过」）
        print(f"  ok {label or name}")
        _emit(buf.getvalue())
        _ok += 1
    except AssertionError as e:
        print(f"  FAIL {name} -> {e}")
        _emit(buf.getvalue())
        _fail += 1
    except Exception as e:                  # noqa: BLE001 - 用例自己要报清原因，不能整段崩
        print(f"  FAIL {name} -> {type(e).__name__}: {e}")
        _emit(buf.getvalue())
        _fail += 1


def _py() -> str:
    """项目约定的解释器；万一不在，退回当前解释器并说一声。"""
    if _PY.is_file():
        return str(_PY)
    return sys.executable


def _decode(b: bytes) -> str:
    for enc in ("utf-8", "cp936", "mbcs"):
        try:
            return b.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return b.decode("utf-8", "replace")


def _run(argv, cwd, env_extra=None, timeout=900):
    """跑一个子进程，返回 (returncode, stdout+stderr 合起来的文本, 分开的 stderr)。

    不联网的用例也走子进程，为的是复核「计划里给的那条原命令」本身能跑出同样的结果。
    """
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    if env_extra:
        env.update(env_extra)
    p = subprocess.run([str(a) for a in argv], cwd=str(cwd), capture_output=True,
                       env=env, timeout=timeout)
    out, err = _decode(p.stdout), _decode(p.stderr)
    return p.returncode, (out + err), err


def _tail(text, n=8, width=300):
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    return " | ".join(lines[-n:])[-width:]


def _ep_via_subprocess(style: str, url: str):
    """按计划给的原命令形状复核一遍：python -c "import llm; print(llm._endpoint(...))"。"""
    code = "import llm; print(llm._endpoint(%r, {'url': %r}))" % (style, url)
    rc, text, _err = _run([_py(), "-c", code], _WEBAPP, timeout=120)
    return rc, text.strip(), text


# ────────────────────────── C-01 ──────────────────────────
def case_01():
    """DoD 1 前半：新用例文件存在，且现在从头跑一遍是绿的。"""
    assert_(_TEST.is_file(), f"期望新用例存在：{_TEST}，实得文件不存在")
    rc, text, _err = _run([_py(), r"tests\llm_endpoint_url.test.py"], _WEBAPP, timeout=300)
    print(f"证据：cd webapp; .venv\\Scripts\\python.exe tests\\llm_endpoint_url.test.py -> exit={rc}")
    for ln in [x.strip() for x in text.splitlines() if x.strip()][-4:]:
        print(f"证据：{ln}")
    assert_("Traceback" not in text, f"输出里出现 Traceback（用例自身崩了）：{_tail(text)}")
    assert_(rc == 0, f"期望退出码 0，实得 {rc}；输出尾：{_tail(text)}")


# ────────────────────────── C-02 ──────────────────────────
_NONZERO_EXIT = re.compile(
    r"(?i)(T-12 exit code\s*=\s*[1-9]|EXITCODE\s*=\s*[1-9]|exit\s*code\s*[=:]\s*[1-9]"
    r"|退出码\s*(为|是)?\s*[1-9]|exit\s*=\s*[1-9])")
_ZERO_EXIT = re.compile(
    r"(?i)(T-12 exit code\s*=\s*0|EXITCODE\s*=\s*0|exit\s*code\s*[=:]\s*0"
    r"|退出码\s*(为|是)?\s*0|exit\s*=\s*0)")


def _nonzero_exit(text: str) -> bool:
    """报告里出现的非 0 退出码（改前那一段的硬证据）。"""
    return bool(_NONZERO_EXIT.search(text))


def _zero_exit(text: str) -> bool:
    """报告里出现的 0 退出码（改后那一段的硬证据）。"""
    return bool(_ZERO_EXIT.search(text))


def _fail_marker(text: str) -> bool:
    return ("\u2717" in text) or bool(re.search(r"\bFAIL\b", text))


def _all_pass(text: str):
    """报告里「全部通过」的那一行（红段里的 通过 0 / 9 不算）。"""
    for m in re.finditer(r"通过\s*(\d+)\s*/\s*(\d+)", text):
        a, b = int(m.group(1)), int(m.group(2))
        if b > 0 and a == b:
            return m.group(0)
    m = re.search(r"(?i)(\d+)\s*passed\D{0,20}0\s*failed", text)
    return m.group(0) if m else None


def _lines_with(text: str, needle: str, limit=3, width=220):
    out = []
    for ln in (text or "").splitlines():
        if needle in ln:
            out.append(ln.strip()[:width])
            if len(out) >= limit:
                break
    return out


def _docs_home() -> Path:
    """PM 的 docs home（`.dsh-crew` 之外的文档家）：<TEMP>/dsh-crew/<repo-slug>/<job-slug>。"""
    tmp = os.environ.get("TEMP") or os.environ.get("TMP") or tempfile.gettempdir()
    slug = str(_REPO).replace("\\", "-").replace(":", "").lower()
    return Path(tmp) / "dsh-crew" / slug / "fix-check-findings"


def _git_bodies():
    """提到 T-12 / llm_endpoint 的提交信息正文（提交之后 C-02 的另一处证据来源）。"""
    bodies = []
    try:
        rc, text, _err = _run(["git", "log", "--all", "--format=%B%x1e", "-100",
                               "--regexp-ignore-case",
                               "--grep=T-12", "--grep=llm_endpoint"], _REPO, timeout=120)
    except Exception as e:                  # noqa: BLE001
        print(f"证据：git log 没跑起来（{type(e).__name__}: {e}），只查文件")
        return bodies
    if rc != 0:
        print(f"证据：git log 返回 {rc}，只查文件")
        return bodies
    for chunk in text.split("\x1e"):
        body = chunk.strip()
        if body:
            bodies.append(body)
    return bodies


def _evidence_sources():
    """C-02 的证据来源：报告文件（优先 qa/T-12/engineer-report.md）+ 提交信息。

    刻意**不**扫 T-12-plan.md 这类计划文件：计划正文里就写着
    「/chat/completions/chat/completions 判红」，把它当证据会假绿。
    """
    src, seen = [], set()

    def add(label, path):
        key = str(path.resolve()).lower()
        if key in seen:
            return
        seen.add(key)
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            print(f"证据：读不到 {path}（{e}），跳过")
            return
        src.append((label, str(path), text))

    if _T12_QA.is_dir():
        for p in sorted(_T12_QA.glob("*.md")):
            add("报告（qa/T-12/）", p)
    if _JOB.is_dir():
        for p in sorted(_JOB.glob("**/*report*.md")):
            add("报告（作业目录）", p)
    home = _docs_home()
    if home.is_dir():
        for p in sorted(home.glob("**/*report*.md")):
            add("报告（文档家）", p)
    for i, body in enumerate(_git_bodies(), 1):
        src.append((f"提交信息（第 {i} 段）", "<git log>", body))
    return src


def case_02():
    """DoD 1 后半：报告/提交信息里有「改前红」与「改后绿」两段真实输出。文档核对。"""
    src = _evidence_sources()
    if not src:
        raise AssertionError(
            "找不到任何 T-12 的最后执行证据：qa/T-12/*.md 里没有报告，"
            ".dsh-crew/jobs/fix-check-findings/**/*report*.md 里没有报告，"
            "git 历史里也没有提到 T-12 / llm_endpoint 的提交"
            "（T-12 的改动现在还没提交：git status 里是 ' M webapp/llm.py'，"
            "新用例 webapp/tests/llm_endpoint_url.test.py 还是未跟踪文件）。"
            "按 T-02/T-03/T-04 的惯例，工程师原文应逐字落盘到 qa/T-12/engineer-report.md；"
            "落盘后重跑 powershell -NoProfile -ExecutionPolicy Bypass -File qa\\T-12\\run.ps1 即可")

    red, green = [], []
    for label, where, text in src:
        if _DOUBLED in text and _nonzero_exit(text) and _fail_marker(text):
            red.append((label, where, _lines_with(text, _DOUBLED)))
        ok_line = _all_pass(text)
        if ok_line is not None and _zero_exit(text):
            green.append((label, where, ok_line))

    for label, where, lines in red:
        print(f"证据：改前红 <{label}> {where}：退出码非 0 + 断言里有双份 /chat/completions")
        for ln in lines:
            print(f"证据：  {ln}")
    for label, where, ok_line in green:
        print(f"证据：改后绿 <{label}> {where}：{ok_line}")
    if not red:
        print(f"证据：搜索了 {len(src)} 处证据来源，都没有同时出现：双份 /chat/completions + 非 0 退出码 + 失败标记")
    if not green:
        print(f"证据：搜索了 {len(src)} 处证据来源，都没有「全部通过 + 退出码 0」这一段")

    assert_(red, "缺『改前红』这一段：需要一段真实输出，退出码非 0、失败断言里能看到 "
                 f"{_DOUBLED}（双份 /chat/completions）")
    assert_(green, "缺『改后绿』这一段：需要一段真实输出，全部通过（通过 N / N）且退出码 0")


# ────────────────────────── C-03 ──────────────────────────
_URL_FULL = "https://api.deepseek.com/v1/chat/completions"


def case_03():
    """DoD 2：完整端点原样返回，整行只出现一次 /chat/completions。"""
    got = llm._endpoint("openai", {"url": _URL_FULL})
    print(f"证据：直接 import 磁盘上 llm.py：_endpoint('openai', {{'url': '{_URL_FULL}'}}) -> {got}")
    assert_(got == _URL_FULL, f"期望原样返回 {_URL_FULL}，实得 {got}")
    n = got.count("/chat/completions")
    assert_(n == 1, f"期望整行只出现一次 /chat/completions，实得 {n} 次：{got}")
    assert_(_DOUBLED not in got, f"出现重复拼接 {_DOUBLED}：{got}")

    rc, line, text = _ep_via_subprocess("openai", _URL_FULL)
    print(f"证据：计划里的原命令（cwd=webapp）exit={rc}，输出={line}")
    assert_(rc == 0, f"计划里的原命令应退出码 0，实得 {rc}；输出：{_tail(text)}")
    assert_(line == _URL_FULL, f"计划里的原命令输出期望恰好是 {_URL_FULL}，实得 {line}")


# ────────────────────────── C-04 ──────────────────────────
_URL_BASE = "https://api.deepseek.com/v1"


def case_04():
    """DoD 3 前半：基础地址仍然补全成 .../chat/completions（老行为不变）。"""
    want = _URL_BASE + "/chat/completions"
    got = llm._endpoint("openai", {"url": _URL_BASE})
    print(f"证据：直接 import 磁盘上 llm.py：_endpoint('openai', {{'url': '{_URL_BASE}'}}) -> {got}")
    assert_(got == want, f"期望补全成 {want}，实得 {got}")

    rc, line, text = _ep_via_subprocess("openai", _URL_BASE)
    print(f"证据：计划里的原命令（cwd=webapp）exit={rc}，输出={line}")
    assert_(rc == 0, f"计划里的原命令应退出码 0，实得 {rc}；输出：{_tail(text)}")
    assert_(line == want, f"计划里的原命令输出期望恰好是 {want}，实得 {line}")


# ────────────────────────── C-05 ──────────────────────────
_KEYWORDS = ("anthropic", "messages", "gemini", "generateContent")


def _findstr_hits():
    """计划里的辅助命令：findstr 四个关键字各命中几次（findstr 不在就返回 None）。"""
    argv = ["findstr", "/I", "/N"]
    for kw in _KEYWORDS:
        argv.append("/C:" + kw)
    argv.append(r"tests\llm_endpoint_url.test.py")
    try:
        rc, text, _err = _run(argv, _WEBAPP, timeout=120)
    except (OSError, subprocess.SubprocessError) as e:      # noqa: BLE001
        print(f"证据：findstr 跑不起来（{type(e).__name__}: {e}），改用文件内容计数")
        return None
    return rc, [ln for ln in text.splitlines() if ln.strip()]


def case_05():
    """DoD 3 后半：anthropic / gemini 的拼接规则没变。

    硬判据（计划里的「主验证」）：
      1) `tests\\llm_endpoint_url.test.py` 退出码 0；
      2) 该文件里 anthropic / messages / gemini / generateContent 四个关键字各至少出现一次
         （findstr 的同一条命令作为辅证）。
    两条直接调用的命令按计划只作**辅助证据**：计划里写明这两条命令的入参是转写来的、
    还没有经 Lead 确认，所以它们只打印，不作硬判据。
    """
    assert_(_TEST.is_file(), f"主验证要读的用例不存在：{_TEST}")
    rc, text, _err = _run([_py(), r"tests\llm_endpoint_url.test.py"], _WEBAPP, timeout=300)
    print(f"证据：主验证 exit={rc}（cd webapp; .venv\\Scripts\\python.exe tests\\llm_endpoint_url.test.py）")
    assert_(rc == 0, f"主验证期望退出码 0，实得 {rc}；输出尾：{_tail(text)}")

    body = _TEST.read_text(encoding="utf-8", errors="replace")
    counts = {kw: len(re.findall(re.escape(kw), body, re.IGNORECASE)) for kw in _KEYWORDS}
    print("证据：测试文件里的关键字命中 " + " / ".join(f"{k}={counts[k]}" for k in _KEYWORDS))
    missing = [k for k in _KEYWORDS if counts[k] < 1]
    assert_(not missing, f"测试文件里应各出现一次以上 {list(_KEYWORDS)}，缺 {missing}")

    hits = _findstr_hits()
    if hits is not None:
        frc, lines = hits
        print(f"证据：findstr 辅助命令 exit={frc}，命中 {len(lines)} 行")
        got_kw = {kw: sum(1 for ln in lines if kw.lower() in ln.lower()) for kw in _KEYWORDS}
        print("证据：findstr 命中数 " + " / ".join(f"{k}={got_kw[k]}" for k in _KEYWORDS))
        lack = [k for k in _KEYWORDS if got_kw[k] < 1]
        assert_(not lack, f"findstr 辅助命令里没命中 {lack}（文件里却有），两个判据不一致")

    # ── 辅助证据：两条直接命令（计划注明入参未确认，不作硬判据）──
    aux = (
        ("anthropic", "https://api.anthropic.com/v1", "/v1/messages"),
        ("gemini", "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash",
         ":generateContent"),
    )
    for style, url, suffix in aux:
        arc, aline, atext = _ep_via_subprocess(style, url)
        flag = "末尾符合" if aline.endswith(suffix) else "末尾不符合"
        print(f"证据：辅助（未确认入参，不作硬判据）_endpoint('{style}', {{'url': '{url}'}}) "
              f"-> {aline} [{flag} {suffix}]")
        if style == "gemini" and "/models/" in aline:
            print("证据：  注意：计划给的 gemini 入参里没有 id，按规则 mid 为空，"
                  "所以 '/models/' 后面是空的（这是入参缺 id，不是拼接规则变了）")


# ────────────────────────── C-06 ──────────────────────────
def case_06():
    """DoD 4：既有用例 webapp/tests/ai_llm.test.py 仍退出码 0。"""
    assert_(_AI_LLM_TEST.is_file(), f"期望既有用例存在：{_AI_LLM_TEST}，实得不存在")
    rc, text, _err = _run([_py(), r"tests\ai_llm.test.py"], _WEBAPP, timeout=300)
    print(f"证据：cd webapp; .venv\\Scripts\\python.exe tests\\ai_llm.test.py -> exit={rc}")
    for ln in [x.strip() for x in text.splitlines() if x.strip()][-3:]:
        print(f"证据：{ln}")
    assert_("Traceback" not in text, f"输出里出现 Traceback（既有用例崩了）：{_tail(text)}")
    assert_(rc == 0, f"期望退出码 0，实得 {rc}；输出尾：{_tail(text)}")


# ────────────────────────── C-07 ──────────────────────────
_ATTRIB = ("llm_endpoint_url", "llm.py", "llm._endpoint", "_endpoint(")

# 三个 QA 目录（T-11 / T-12 / T-13）共用同一个「我已经在 run-all 里面了」的标记。
# 任何一个目录的用例起 run-all 时都在子进程环境里设 QA_RUNALL_NESTED=1，被它调起的用例
# 见到这个标记就不起下一层。之前每个目录只管自己的私有标记（QA_T12_NESTED 之类），
# 互相不认 → 外层 run-all 里 T-11 起第二层、T-12 起第三层、T-13 起第四层，2026-11-05 实测
# 11 分钟跑不完。私有标记保留：qa/T-12/run.ps1 的护栏还认它（本次按要求没改 run.ps1）。
_SHARED_NESTED_FLAG = "QA_RUNALL_NESTED"
_OWN_NESTED_FLAG = "QA_T12_NESTED"


def _nested_run_all() -> bool:
    """当前进程是不是「某个用例起的 run-all」调起来的（共享标记为真）。"""
    v = str(os.environ.get(_SHARED_NESTED_FLAG, "")).strip().lower()
    return v not in ("", "0", "false", "no", "off")


def case_07():
    """回归（非 DoD）：跑全套 qa\\run-all.ps1，被本任务弄红就判红。

    递归护栏（共享标记）：qa\\run-all.ps1 会枚举 qa\\ 下的目录，其中就有 qa/T-12 自己。
    所以：
      · 本用例起 run-all 时，在子进程环境里设 QA_RUNALL_NESTED=1（外加自己的
        QA_T12_NESTED=1，让 qa/T-12/run.ps1 的旧护栏也照常跳过）；
      · 本用例一进来先看 QA_RUNALL_NESTED：为真说明这次 T-12 是被别人的 run-all
        调起来的，那条链的外层已经负责「全套有没有被弄红」，这里直接报 ok 并返回，
        不再起第二层。T-11 / T-13 用同一个标记，三个目录才不会互相套娃。
    也就是说：**嵌套那一趟不真的验证 T-12 的全套回归**（由最外层那一次负责），
    顶层那一趟才跑真正的检查。

    判据（计划 C-07）：失败项涉及 T-12 拥有的文件 → 判红。
    全套的绝对退出码照实打印：基线里就有一条与本任务无关的既有红（见 _BASELINE_NOTE）。

    解析要小心两件 2026-11-05 实测踩到的事：
      · 别的任务的 cases.py 会在**失败原因里引用**「[FAIL] T-04 exit=1」这种字符串，
        所以 `[FAIL]` 必须锚定行首；不锚定会凭空多出几个「失败目录」，
        将来也可能凭一句被引用的话把 T-12 误判成红；
      · 有目录还没写入口时 run-all 只打「没有入口：T-13」并 exit 1，那不算 T-12 的红。
    """
    if _nested_run_all():
        # 外层那次 run-all 已经负责「全套有没有被弄红」；这里再起一层只会套娃。
        print(f"证据：检测到 {_SHARED_NESTED_FLAG}="
              f"{os.environ.get(_SHARED_NESTED_FLAG)!r}，本次不再起 run-all，"
              f"由外层那一次负责完整回归")
        return "C-07 (nested run-all: 跳过自身回归检查，由外层负责)"

    assert_(_RUN_ALL.is_file(), f"期望全套入口存在：{_RUN_ALL}，实得不存在")
    try:
        rc, text, _err = _run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                               "-File", str(_RUN_ALL)], _REPO, timeout=900,
                              env_extra={_SHARED_NESTED_FLAG: "1", _OWN_NESTED_FLAG: "1"})
    except subprocess.TimeoutExpired as e:
        raise AssertionError(f"全套 qa\\run-all.ps1 超过 900s 没跑完：{e}") from None

    # run-all.ps1 自己打的失败行锚定在行首；被别的用例引用到的同款字符串不算。
    failing_ids = [x[0] for x in re.findall(
        r"(?m)^\[FAIL\]\s+([A-Za-z0-9_.\-]+)\s+exit=(\d+)", text)]
    no_entry = []
    m = re.search(r"(?m)^\s*没有入口：(.+)$", text)
    if m:
        no_entry = [x.strip() for x in m.group(1).split(",") if x.strip()]
    summary = [ln.strip() for ln in text.splitlines() if "QA 用例" in ln]
    print(f"证据：qa\\run-all.ps1 exit={rc}")
    if summary:
        print(f"证据：{summary[-1]}")
    print(f"证据：失败目录={failing_ids or '无'}；缺入口目录={no_entry or '无'}")
    print(f"证据：基线（{_BASELINE_NOTE}）")
    new_ids = [x for x in failing_ids if x not in _BASELINE_FAILING]
    print(f"证据：比基线新增的失败目录={new_ids or '无'}")

    # 归因：只认「出现在失败行里」的 T-12 文件/符号；别的用例在通过行里提到 llm.py 不算。
    hits = []
    for ln in text.splitlines():
        if any(t in ln for t in _ATTRIB) and re.search(
                r"\[FAIL\]|\bFAIL\b|\u2717|Traceback|Error|exit\s*=\s*[1-9]", ln):
            hits.append(ln.strip()[:200])
    print(f"证据：失败行里提到 T-12 拥有的文件/符号={hits or '无'}")

    if rc != 0 and not failing_ids and not no_entry:
        raise AssertionError(f"全套退出码 {rc}，但输出里既解析不到 [FAIL] 行也解析不到"
                             f"『没有入口』（不认识的失败形状，不能当通过）：{_tail(text)}")
    assert_("T-12" not in failing_ids,
            f"T-12 自己的入口在全套里被判红（exit≠0）：{failing_ids}")
    assert_("T-12" not in no_entry,
            f"T-12 自己的入口在全套里被判成『没有入口』：{no_entry}")
    assert_(not hits,
            f"失败行里出现了 T-12 拥有的文件/符号，说明本任务的改动让别处变红了：{hits}")
    if rc != 0:
        print(f"证据：全套 exit={rc} 不是 0 —— 失败目录 {failing_ids or '无'} / "
              f"缺入口 {no_entry or '无'} 都指向别的任务，"
              f"与 T-12 拥有的 {list(_OWNED)} 无关；本条的判据是『失败是否指向 T-12 的文件』")


# ────────────────────────── 汇总 ──────────────────────────
CASES = [
    ("C-01 新用例 llm_endpoint_url.test.py 存在且现在为绿（DoD 1 前半）", case_01),
    ("C-02 有『改前红 / 改后绿』两段真实输出（DoD 1 后半，文档核对）", case_02),
    ("C-03 完整 openai 端点原样返回、不重复拼接（DoD 2）", case_03),
    ("C-04 openai 基础地址仍然补全（DoD 3 前半）", case_04),
    ("C-05 anthropic / gemini 拼接规则不变（DoD 3 后半）", case_05),
    ("C-06 既有用例 ai_llm.test.py 仍为绿（DoD 4）", case_06),
    ("C-07 全套 QA 没有被本任务弄红（回归，非 DoD 条目）", case_07),
]


def main():
    print("T-12 用例（qa/T-12/cases.py，清单见 .dsh-crew/jobs/fix-check-findings/T-12-plan.md）：")
    print(f"被测模块：{Path(llm.__file__).resolve()}（重新 import，不用 8789 活服务内存里的旧版本）")
    for name, fn in CASES:
        check(name, fn)
    total = _ok + _fail
    print(f"\n通过 {_ok} / {total}")
    print(f"T-12 RESULT: {_ok}/{total} ok, exit {0 if _fail == 0 else 1}")
    return 0 if _fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
