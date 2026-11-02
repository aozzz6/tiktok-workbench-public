# -*- coding: utf-8 -*-
r"""qa/T-04/cases.py —— T-04「修好两个因 API 变更早已失效的旧测试」的 QA 用例

用例清单来源：`.dsh-crew/jobs/s2-scrape-failure-fix/T-04-plan.md`（C-01 ~ C-10，一条一条照写）。
DoD 来源：docs/tasks/tasks.md v1 里 T-04 的 DoD 第 1~4 条；C-10 覆盖 PRD 第 5 节第 5 条（统测全绿）。

本任务被测的产物**本身就是一个测试文件**（`pipeline/tests/test_acceptance_regressions.py`），
所以这里的用例多半是「检查那个测试文件」的元检查：
  - 跑它，看退出码 / Ran N tests / OK（C-01、C-02）
  - 静态读它，看有没有 skip、断言有没有被削弱（C-03、C-04、C-05）
  - 跑它的输出里不许出现兜底被真的调起（C-06）
  - 进程内把网络入口 / 浏览器兜底入口换成「被调用即抛异常」再跑一遍（C-07）
  - 报告里的两段真实输出 + patch 目标说明（C-08、C-09）
  - 项目统测命令全绿（C-10）

不联网、不写产品代码、不改被测文件；只在临时目录里放子脚本。
跑法：powershell -NoProfile -ExecutionPolicy Bypass -File qa\T-04\run.ps1
"""
from __future__ import annotations

import ast
import contextlib
import io as _io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import tokenize
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
    except Exception:
        pass

_REPO = Path(__file__).resolve().parents[2]
_PY = _REPO / "pipeline" / ".venv" / "Scripts" / "python.exe"
_REL = "pipeline/tests/test_acceptance_regressions.py"
_TARGET = _REPO / "pipeline" / "tests" / "test_acceptance_regressions.py"
_SCRIPTS = _REPO / "pipeline" / "scripts"
_JOB = _REPO / ".dsh-crew" / "jobs" / "s2-scrape-failure-fix"

_TEST_LONG = "test_scrape_long_desc_and_rate_limit"
_TEST_RETRY = "test_retry_scrape_preserves_long_desc_and_hashtags"
_MIN_TESTS = 27

# 「改动前」的基线必须钉死成具体提交，不能用 HEAD：
#   HEAD 会随 PM 的提交往前跑。PM 一提交，`git show HEAD:<文件>` 拿到的就是**修好的**文件，
#   C-08 于是变成「拿修好的文件和修好的工作树比」，恒绿，而且没人会察觉。
#   这里钉死本作业开工时唯一的提交 d58d9ed（"chore: 线上工作台基线（首次纳入版本库）"），
#   它里面的这个测试文件就是改动前（patch 的是 sc.parse_item）的版本。
#   钉死还不够 —— C-08 另外会核对：基线内容与当前工作树**不同**，且基线里 patch 的仍是
#   sc.parse_item（即旧 mock 目标）。历史被改写 / 哈希失效 / 钉错提交，都会判红，不会静默恒绿。
_BASELINE_COMMIT = "d58d9ed"

_ok = 0
_fail = 0


def check(name, fn):
    global _ok, _fail
    buf = _io.StringIO()
    try:
        # 先跑，把用例自己打的「证据」行缓冲起来，这样日志里永远是 ok/FAIL 在前、证据在后
        with contextlib.redirect_stdout(buf):
            fn()
        print(f"  ok {name}")
        _print_evidence(buf.getvalue())
        _ok += 1
    except AssertionError as e:
        print(f"  FAIL {name} -> {e}")
        _print_evidence(buf.getvalue())
        _fail += 1
    except Exception as e:  # noqa: BLE001 - QA 用例要自己说清失败原因，不能整段崩掉
        print(f"  FAIL {name} -> {type(e).__name__}: {e}")
        _print_evidence(buf.getvalue())
        _fail += 1


def _print_evidence(text):
    for line in text.splitlines():
        if line.strip():
            print("      " + line.strip())


def assert_(cond, msg="assert failed"):
    if not cond:
        raise AssertionError(msg)


# ---------------------------------------------------------------- 通用工具

_RUN_CACHE = {}


def _child_env():
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"   # 子进程一律 UTF-8 输出，本机控制台是 GBK
    return env


def _run(cmd, cwd=None, timeout=900):
    """跑一个子进程，返回 (退出码, stdout+stderr 合并文本, stdout, stderr)。"""
    p = subprocess.run(cmd, cwd=str(cwd or _REPO), capture_output=True,
                       env=_child_env(), timeout=timeout)
    out = p.stdout.decode("utf-8", "replace")
    err = p.stderr.decode("utf-8", "replace")
    return p.returncode, (out + "\n" + err), out, err


def _current_run():
    """跑一次被测测试文件（整轮用例共用同一次运行，判定才自洽）。"""
    if "run" not in _RUN_CACHE:
        assert_(_TARGET.exists(), f"被测测试文件不存在：{_TARGET}")
        rc, text, out, err = _run([str(_PY), str(_TARGET)])
        _RUN_CACHE["run"] = (rc, text)
        _RUN_CACHE["stdout"] = out
        _RUN_CACHE["stderr"] = err
    return _RUN_CACHE["run"]


def _target_source():
    return _TARGET.read_text(encoding="utf-8")


def _code_only(src):
    """去掉注释后的源码（保留行号）。防止把注释里的一句话当成真断言。"""
    try:
        src = textwrap.dedent(src)
        toks = [t for t in tokenize.generate_tokens(_io.StringIO(src).readline)
                if t.type != tokenize.COMMENT]
        return tokenize.untokenize(toks)
    except Exception:
        return src


def _module_ast(src):
    return ast.parse(src)


def _base_name(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _testcase_classes(tree):
    out = []
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            if any(_base_name(b).endswith("TestCase") for b in node.bases):
                out.append(node)
    return out


def _test_methods(tree):
    names = []
    for cls in _testcase_classes(tree):
        for fn in cls.body:
            if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if fn.name.startswith("test"):
                    names.append(fn.name)
    return names


def _find_test_method(tree, name):
    for cls in _testcase_classes(tree):
        for fn in cls.body:
            if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) and fn.name == name:
                return fn
    return None


def _find_method_in_module(tree, name):
    """不限于 TestCase：找模块/类里任意同名函数（scrape_run 是类里的 helper）。"""
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    return None


def _skip_markers(tree, code_text):
    """找出所有 skip / expectedFailure：返回命中说明列表（空 = 干净）。"""
    hits = []
    skip_attrs = {"skip", "skipIf", "skipUnless", "skipTest", "expectedFailure"}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            for dec in node.decorator_list:
                target = dec.func if isinstance(dec, ast.Call) else dec
                nm = _base_name(target if not isinstance(target, ast.Call) else target.func)
                if isinstance(dec, ast.Call):
                    nm = _base_name(dec.func)
                if nm in skip_attrs:
                    hits.append(f"第 {dec.lineno} 行装饰器 {nm}（{node.name}）")
        if isinstance(node, ast.Attribute) and node.attr in skip_attrs - {"skipTest"}:
            if node.attr in ("expectedFailure", "skip", "skipIf", "skipUnless"):
                hits.append(f"第 {node.lineno} 行引用了 {node.attr}")
        if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "skipTest":
            hits.append(f"第 {node.lineno} 行调用了 skipTest")
    for token in ("skipTest", "@unittest.skip", "expectedFailure"):
        for m in re.finditer(re.escape(token), code_text):
            line = code_text[:m.start()].count("\n") + 1
            hits.append(f"第 ~{line} 行出现文本 {token}")
    # 去重、保序
    seen, out = set(), []
    for h in hits:
        if h not in seen:
            seen.add(h)
            out.append(h)
    return out


def _static_str_len(node):
    """能静态算长度就返回长度（'x'*250 + ' #tail' 这种），算不出来返回 None。"""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return len(node.value)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mult):
        left, right = node.left, node.right
        if isinstance(left, ast.Constant) and isinstance(left.value, (str, bytes)):
            if isinstance(right, ast.Constant) and isinstance(right.value, int):
                return len(left.value) * right.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        a, b = _static_str_len(node.left), _static_str_len(node.right)
        if a is not None and b is not None:
            return a + b
    return None


def _patch_object_targets(func_node):
    """收集 patch.object(sc, '名字', ...) 里的 '名字'。"""
    found = []
    for node in ast.walk(func_node):
        if not isinstance(node, ast.Call):
            continue
        if not (isinstance(node.func, ast.Attribute) and node.func.attr == "object"):
            continue
        if len(node.args) < 2:
            continue
        owner, name = node.args[0], node.args[1]
        if not (isinstance(owner, ast.Name) and owner.id == "sc"):
            continue
        if isinstance(name, ast.Constant) and isinstance(name.value, str):
            found.append(name.value)
    return found


def _called_in_module_func(path, func_name, alias="sc"):
    """模块里某个函数真正调用了哪些名字（裸调用 + alias.attr 调用）。"""
    tree = _module_ast(Path(path).read_text(encoding="utf-8"))
    fn = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == func_name:
            fn = node
            break
    assert_(fn is not None, f"{Path(path).name} 里找不到函数 {func_name}()")
    names = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Name):
                names.add(f.id)
            elif isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name):
                if f.value.id == alias:
                    names.add(f.attr)
    return names


# T-04 工程师报告的落盘位置，按优先级排：
#   第一位是**仓库里**那份（qa/T-04/engineer-report.md）—— 作业目录 `.dsh-crew/jobs/<job>/`
#   在作业收尾时会被删掉，仓库里这份不随作业消失，所以必须最先读它；否则作业目录一没，
#   C-08/C-09 会静默降级成替代判据，仓库里这份报告就再没人读。
#   后面几项是作业目录里的副本（本作业进行期间的原始落盘位置），只作兜底。
_REPORT_CANDIDATES = [
    _REPO / "qa" / "T-04" / "engineer-report.md",
    _JOB / "T-04-report.md",
    _JOB / "T-04-report.txt",
    _JOB / "reviews" / "T-04-report.md",
    _JOB / "reviews" / "T-04.md",
]


def _find_report():
    """T-04 工程师报告的落盘位置（优先读仓库里那份，它不随作业目录消失）。"""
    hits = [p for p in _REPORT_CANDIDATES if p.exists()]
    if hits:
        return hits[0]
    for p in sorted(_JOB.rglob("*")):
        if not p.is_file():
            continue
        nm = p.name.lower()
        if "t-04" in nm or "t04" in nm:
            if "report" in nm or "报告" in p.name or "summary" in nm:
                return p
    return None


_TMPDIR = None


def _tmpdir():
    global _TMPDIR
    if _TMPDIR is None:
        _TMPDIR = Path(tempfile.mkdtemp(prefix="qa_t04_"))
    return _TMPDIR


def _write_child(name, text):
    p = _tmpdir() / name
    p.write_text(text, encoding="utf-8")
    return p


# ---------------------------------------------------------------- C-01
def case_01_file_is_green():
    rc, text = _current_run()
    assert_(rc == 0, f"期望退出码 0，实得 {rc}；输出尾部：{text.strip()[-400:]}")
    assert_(re.search(r"^OK", text, re.M),
            "期望输出里有 unittest 的 OK 行，实得没有")
    assert_(not re.search(r"^FAILED", text, re.M),
            "期望没有 FAILED 行，实得有一条 FAILED 行：" +
            str(re.findall(r"^FAILED.*$", text, re.M)[:1]))
    bad = [n for n in re.findall(r"errors=(\d+)", text) if int(n) > 0]
    assert_(not bad,
            f"期望 errors=0（产品的 [saved]/[merge] 行会正常打印 errors=0），实得非零 errors={bad}")


# ---------------------------------------------------------------- C-02
def case_02_test_count_not_reduced():
    rc, text = _current_run()
    m = re.search(r"^Ran (\d+) tests? in", text, re.M)
    assert_(m is not None, "期望输出里有 'Ran N tests in ...' 行，实得没有（用例数无法判定）")
    n = int(m.group(1))
    assert_(n >= _MIN_TESTS,
            f"期望用例数 >= {_MIN_TESTS}（改动前基线 27），实得 Ran {n} tests")


# ---------------------------------------------------------------- C-03
def case_03_no_skip_and_names_kept():
    src = _target_source()
    tree = _module_ast(src)
    code = _code_only(src)

    hits = _skip_markers(tree, code)
    assert_(not hits,
            "期望没有 skipTest / @unittest.skip / expectedFailure，实得：" + "；".join(hits[:6]))

    methods = _test_methods(tree)
    assert_(len(methods) >= _MIN_TESTS,
            f"期望 unittest 能收集到 >= {_MIN_TESTS} 个 test 方法，实得 {len(methods)} 个")
    for name in (_TEST_LONG, _TEST_RETRY):
        assert_(name in methods,
                f"期望收集到的用例名里有 {name}，实得没有；现有 {len(methods)} 个")

    # 与运行时 Ran 数对照：静态数少于运行时数，说明有非 TestCase 的用例被漏数
    rc, text = _current_run()
    m = re.search(r"^Ran (\d+) tests? in", text, re.M)
    if m:
        assert_(int(m.group(1)) <= len(methods),
                f"运行时 Ran {m.group(1)} 个用例，但静态只数到 {len(methods)} 个 TestCase 方法"
                "（说明用例定义在别处，本用例的静态判定不完整）")


# ------------------------------------------------ 断言强度工具（C-04 / C-05 共用）

def _assert_guard_ids(fn):
    """所有「断言上下文」里的节点 id：assert 语句，或 self.assertXxx(...) 调用。"""
    ids = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Assert):
            for n in ast.walk(node):
                ids.add(id(n))
        elif isinstance(node, ast.Call) and _base_name(node.func).startswith("assert"):
            for n in ast.walk(node):
                ids.add(id(n))
    return ids


def _u(node):
    try:
        return ast.unparse(node)
    except Exception:
        return ""


def _len_of(node):
    """len(非字符串字面量) —— len('desc') 这种假断言不算。"""
    if not (isinstance(node, ast.Call) and _base_name(node.func) == "len"):
        return False
    if not node.args:
        return False
    arg = node.args[0]
    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
        return False
    return True


def _scraped_vars(fn):
    """函数里从文件读回来的变量名（json.loads(...) / read_text(...) 之类）。"""
    names = set()
    for node in ast.walk(fn):
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)):
            text = _u(node.value)
            if "json.loads" in text or "read_text" in text or "loads(" in text:
                names.add(node.targets[0].id)
    return names


def _from_output(expr_src, vars_):
    if not vars_:
        return True
    s = expr_src.lstrip()
    return any(s.startswith(v + "[") or s.startswith(v + ".") for v in vars_)


def _is_int(node):
    return (isinstance(node, ast.Constant) and isinstance(node.value, int)
            and not isinstance(node.value, bool))


def _is_str_list(node):
    return (isinstance(node, ast.List) and bool(node.elts)
            and all(isinstance(e, ast.Constant) and isinstance(e.value, str) for e in node.elts))


def _desc_length_asserts(fn, guards, from_vars=None):
    """desc 长度 > 200 的断言：len(...desc...) > N / assertGreater(len(...desc...), N)。

    from_vars 给了就要求被量的那个 desc 来自「从文件读回来的变量」。
    """
    hits = []
    for node in ast.walk(fn):
        if id(node) not in guards:
            continue
        if isinstance(node, ast.Compare):
            text = _u(node)
            if "desc" not in text:
                continue
            left = node.left
            for i, op in enumerate(node.ops):
                right = node.comparators[i]
                if (isinstance(op, (ast.Gt, ast.GtE)) and _len_of(left) and "desc" in _u(left)
                        and _from_output(_u(left.args[0]), from_vars)
                        and _is_int(right) and right.value >= 200):
                    hits.append((node.lineno, text))
                if (isinstance(op, (ast.Lt, ast.LtE)) and _len_of(right) and "desc" in _u(right)
                        and _from_output(_u(right.args[0]), from_vars)
                        and _is_int(left) and left.value >= 200):
                    hits.append((node.lineno, text))
                left = right
        elif isinstance(node, ast.Call):
            nm = _base_name(node.func)
            if nm in ("assertGreater", "assertGreaterEqual") and len(node.args) >= 2:
                a, b = node.args[0], node.args[1]
                for x, y in ((a, b), (b, a)):
                    if (_len_of(x) and "desc" in _u(x)
                            and _from_output(_u(x.args[0]), from_vars)
                            and _is_int(y) and y.value >= 200):
                        hits.append((node.lineno, _u(node)))
    return hits


def _equality_asserts(fn, guards, needle, want_list=False, from_vars=None):
    """相等断言（两边的写法必须不同，排除 X == X 这种恒真）。

    assertEqual(A, B) / assertTrue(A == B)。want_list=True 时要求其中一边是字符串列表字面量；
    from_vars 给了就要求其中一边来自「从文件读回来的变量」。
    """
    hits = []
    for node in ast.walk(fn):
        if isinstance(node, ast.Call) and _base_name(node.func) == "assertEqual" and len(node.args) >= 2:
            s0, s1 = _u(node.args[0]), _u(node.args[1])
            if needle not in (s0 + s1):
                continue
            if s0.strip() == s1.strip():
                continue
            if want_list and not (_is_str_list(node.args[0]) or _is_str_list(node.args[1])):
                continue
            if from_vars and not (_from_output(s0, from_vars) or _from_output(s1, from_vars)):
                continue
            hits.append((node.lineno, f"assertEqual({s0}, {s1})"))
        elif isinstance(node, ast.Compare) and id(node) in guards and needle in _u(node):
            if not isinstance(node.ops[0], ast.Eq):
                continue
            left, right = node.left, node.comparators[0]
            s0, s1 = _u(left), _u(right)
            if s0.strip() == s1.strip():
                continue
            if want_list and not (_is_str_list(left) or _is_str_list(right)):
                continue
            if from_vars and not (_from_output(s0, from_vars) or _from_output(s1, from_vars)):
                continue
            hits.append((node.lineno, _u(node)))
    return hits


def _once_asserts(fn):
    """「恰好一次」的断言：assert_called_once[_with] / call_count == 1 / assertEqual(call_count, 1)。"""
    hits = []
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call):
            continue
        nm = _base_name(node.func)
        if nm in ("assert_called_once", "assert_called_once_with"):
            hits.append((node.lineno, _u(node)))
            continue
        if nm == "assertEqual" and len(node.args) >= 2:
            s0 = _u(node.args[0])
            if "call_count" in s0 and _is_int(node.args[1]) and node.args[1].value == 1:
                hits.append((node.lineno, _u(node)))
        for cmp in ast.walk(node):
            if not isinstance(cmp, ast.Compare):
                continue
            text = _u(cmp)
            if "call_count" not in text:
                continue
            if (any(isinstance(op, ast.Eq) for op in cmp.ops)
                    and any(_is_int(c) and c.value == 1 for c in cmp.comparators)):
                hits.append((cmp.lineno, text))
    return hits


def _swallowed_asserts(fn):
    """断言被 `try: ... except AssertionError: pass` 吞掉 —— 等于没断言。"""
    bad = []
    for node in ast.walk(fn):
        if not isinstance(node, ast.Try):
            continue
        swallows = False
        for h in node.handlers:
            if h.type is None:
                swallows = True
                continue
            names = set()
            for n in ast.walk(h.type):
                if isinstance(n, ast.Name):
                    names.add(n.id)
                elif isinstance(n, ast.Attribute):
                    names.add(n.attr)
            if names & {"AssertionError", "Exception", "BaseException"}:
                swallows = True
        if not swallows:
            continue
        for inner in ast.walk(node):
            if isinstance(inner, ast.Assert) or (
                    isinstance(inner, ast.Call) and _base_name(inner.func).startswith("assert")):
                bad.append(f"第 {inner.lineno} 行 {_u(inner)[:70]}")
    return list(dict.fromkeys(bad))


def _dead_branch_asserts(fn):
    """断言被塞进 `if False:` / `if 0:` 这种恒假分支里 —— 与注释掉等价，也要算削弱。"""
    dead = []
    for node in ast.walk(fn):
        if not (isinstance(node, ast.If) and isinstance(node.test, ast.Constant)
                and not node.test.value):
            continue
        for inner in ast.walk(node):
            if isinstance(inner, ast.Assert) or (
                    isinstance(inner, ast.Call) and _base_name(inner.func).startswith("assert")):
                dead.append(f"第 {inner.lineno} 行 {_u(inner)[:80]}")
    return list(dict.fromkeys(dead))


# ---------------------------------------------------------------- C-04
def case_04_first_test_assertions_intact():
    tree = _module_ast(_target_source())
    fn = _find_test_method(tree, _TEST_LONG)
    assert_(fn is not None, f"期望测试文件里有 {_TEST_LONG}，实得没有")
    guards = _assert_guard_ids(fn)
    vars_ = _scraped_vars(fn)
    missing = []

    long_hits = _desc_length_asserts(fn, guards, vars_)
    if not long_hits:
        missing.append("(1) desc 长度 > 200 的断言")

    tag_hits = _equality_asserts(fn, guards, "hashtags", want_list=True, from_vars=vars_)
    if not tag_hits:
        missing.append("(2) hashtags == ['tail'] 的相等断言")

    once_hits = _once_asserts(fn)
    if not once_hits:
        missing.append("(3) sleep 恰被调用一次的断言")

    dead = _dead_branch_asserts(fn)
    assert_(not dead,
            f"{_TEST_LONG} 有断言藏在恒假分支里（等于没断言）：" + "；".join(dead[:4]))
    swallowed = _swallowed_asserts(fn)
    assert_(not swallowed,
            f"{_TEST_LONG} 有断言被 try/except 吞掉（等于没断言）：" + "；".join(swallowed[:4]))
    assert_(not missing,
            f"{_TEST_LONG} 的断言缺了 {len(missing)} 条：" + "；".join(missing) +
            f"（函数体 {getattr(fn, 'end_lineno', 0) - fn.lineno + 1} 行）")
    print(f"        证据：长 desc 断言第 {long_hits[0][0]} 行 / hashtags 断言第 "
          f"{tag_hits[0][0]} 行 / 只调一次断言第 {once_hits[0][0]} 行")


# ---------------------------------------------------------------- C-05
def case_05_second_test_assertions_intact():
    tree = _module_ast(_target_source())
    fn = _find_test_method(tree, _TEST_RETRY)
    assert_(fn is not None, f"期望测试文件里有 {_TEST_RETRY}，实得没有")
    guards = _assert_guard_ids(fn)
    vars_ = _scraped_vars(fn)
    missing = []

    # 函数里那条「长 desc」的来源：desc 值能静态算出 > 200 字
    long_fixture = None
    for node in ast.walk(fn):
        if isinstance(node, ast.Dict):
            for k, v in zip(node.keys, node.values):
                if isinstance(k, ast.Constant) and k.value == "desc":
                    ln = _static_str_len(v)
                    if ln is not None and ln > 200:
                        long_fixture = (node.lineno, ln)

    # (a) 合并后的 desc 不丢：一条相等断言（两边写法必须不同，X == X 不算）
    desc_eq = _equality_asserts(fn, guards, "desc", from_vars=vars_)
    if not desc_eq:
        missing.append("(a) 合并后 desc 不丢的相等断言")
    elif long_fixture is None and not _desc_length_asserts(fn, guards, vars_):
        missing.append("(a) 长 desc 的来源（函数里没有长度 > 200 的 desc fixture，"
                       "无法证明『长 desc 不丢』而不是『短 desc 相等』）")

    # (b) hashtags 不丢（等于原值 / 等于期望列表）
    tag_hits = _equality_asserts(fn, guards, "hashtags", want_list=True, from_vars=vars_)
    if not tag_hits:
        missing.append("(b) hashtags 不丢的断言（应是等于原值的相等断言）")

    dead = _dead_branch_asserts(fn)
    assert_(not dead,
            f"{_TEST_RETRY} 有断言藏在恒假分支里（等于没断言）：" + "；".join(dead[:4]))
    swallowed = _swallowed_asserts(fn)
    assert_(not swallowed,
            f"{_TEST_RETRY} 有断言被 try/except 吞掉（等于没断言）：" + "；".join(swallowed[:4]))
    assert_(not missing,
            f"{_TEST_RETRY} 的断言缺了 {len(missing)} 条：" + "；".join(missing))
    print(f"        证据：长 desc fixture 第 {long_fixture[0]} 行（长度 {long_fixture[1]}），"
          f"desc 断言第 {desc_eq[0][0]} 行，hashtags 断言第 {tag_hits[0][0]} 行")


# ---------------------------------------------------------------- C-06
def case_06_no_fallback_in_output():
    rc, text = _current_run()
    assert_("[fallback]" not in text,
            "期望运行输出里没有 [fallback]（出现即表示浏览器兜底被真的调起），实得有")
    assert_("[warn] 浏览器兜底" not in text,
            "期望运行输出里没有 '[warn] 浏览器兜底'（出现即表示兜底被调起），实得有")


# ---------------------------------------------------------------- C-07
_GUARDED = r'''# -*- coding: utf-8 -*-
"""QA C-07：把网络入口 / 浏览器兜底入口换成「被调用即抛异常」，再跑一遍被测文件。"""
import io, json, sys, types, unittest, importlib.util
from pathlib import Path

repo = Path(sys.argv[1]); target = Path(sys.argv[2]); outp = Path(sys.argv[3])
sys.path.insert(0, str(repo / "pipeline" / "scripts"))

fired = []
def boom(what):
    def _b(*a, **k):
        fired.append(what)
        raise AssertionError("QA-C07: 不允许触达 " + what)
    return _b

import scrape_tiktok_video as sc
import tiktok_fetch
import extract_products as products
import auto_translate as at

sc.fetch_page = boom("sc.fetch_page")                       # 抓取层总入口
sc.browser_batch_fallback = boom("sc.browser_batch_fallback")  # 浏览器兜底入口
tiktok_fetch.fetch_html = boom("tiktok_fetch.fetch_html")   # 真正发 HTTP 的那层
products.fetch_html = boom("products.fetch_html")
at.call_llm = boom("at.call_llm")                           # LLM HTTP
at.load_model = boom("at.load_model")

spec = importlib.util.spec_from_file_location("t04_target", str(target))
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

suite = unittest.defaultTestLoader.loadTestsFromModule(mod)
buf = io.StringIO()
res = unittest.TextTestRunner(stream=buf, verbosity=1).run(suite)
detail = buf.getvalue()
if res.errors:
    detail += "\n".join(str(t) for _, t in res.errors)
if res.failures:
    detail += "\n".join(str(t) for _, t in res.failures)
outp.write_text(json.dumps({
    "tests": res.testsRun,
    "errors": len(res.errors),
    "failures": len(res.failures),
    "wasSuccessful": res.wasSuccessful(),
    "fired": fired,
    "detail": detail[-3000:],
}, ensure_ascii=False), encoding="utf-8")
'''


def case_07_no_real_network_and_no_fallback():
    outp = _tmpdir() / "c07_result.json"
    child = _write_child("c07_guard.py", _GUARDED)
    rc, text, out, err = _run([str(_PY), str(child), str(_REPO), str(_TARGET), str(outp)])
    assert_(outp.exists(),
            f"期望注入子进程写出结果文件，实得没有（退出码 {rc}）；输出尾部：{(text or '')[-500:]}")
    res = json.loads(outp.read_text(encoding="utf-8"))
    assert_(not res["fired"],
            "期望注入的网络/兜底入口一次都没被调用，实得被调用：" + "、".join(res["fired"]))
    assert_(res["tests"] >= _MIN_TESTS,
            f"期望注入后仍收集到 >= {_MIN_TESTS} 个用例，实得 {res['tests']} 个")
    assert_(res["errors"] == 0 and res["failures"] == 0 and res["wasSuccessful"],
            f"期望注入后仍全绿，实得 tests={res['tests']} errors={res['errors']} "
            f"failures={res['failures']}；尾部：{res['detail'][-600:]}")


# ---------------------------------------------------------------- C-08
# 「改动前」的取得方式：钉死基线提交，**不用 HEAD**。理由见 _BASELINE_COMMIT 处的注释：
# HEAD 会随着 PM 提交而前移，一提交 `git show HEAD:<文件>` 就变成「修好的文件 vs 修好的工作树」，
# 这段判据会恒绿。所以这里只能写具体的提交哈希。
_BASELINE = r'''# -*- coding: utf-8 -*-
"""QA C-08：用**钉死的基线提交**里那份测试文件（改动前）复现那 2 个 error，全程不联网。"""
import io, json, subprocess, sys, types, unittest
from pathlib import Path

repo = Path(sys.argv[1]); rel = sys.argv[2]; outp = Path(sys.argv[3]); commit = sys.argv[4]
# 注意：这里用 commit 而不是 HEAD —— HEAD 会在 PM 提交后指向修好的文件。
g = subprocess.run(["git", "show", commit + ":" + rel], cwd=str(repo), capture_output=True)
if g.returncode != 0:
    outp.write_text(json.dumps({"git_rc": g.returncode, "commit": commit,
                                "git_err": g.stderr.decode("utf-8", "replace")},
                               ensure_ascii=False), encoding="utf-8")
    sys.exit(3)

src = g.stdout.decode("utf-8")
sys.path.insert(0, str(repo / "pipeline" / "scripts"))
import scrape_tiktok_video as sc
import tiktok_fetch
import extract_products as products
import auto_translate as at

fired = []
def boom(what):
    def _b(*a, **k):
        fired.append(what)
        raise AssertionError("QA-C08: 不允许触达 " + what)
    return _b

# 不联网：抓取层返回一段没有 __UNIVERSAL_DATA_FOR_REHYDRATION__ 的固定页面，
# 浏览器兜底直接返回空 —— 这正是改动前的真实失败链（mock 打偏 → 抓到挑战页 → items 为空）。
sc.fetch_page = lambda url: "<html><body>qa-c08 fixed challenge fixture</body></html>"
sc.browser_batch_fallback = lambda account, vids: {}
tiktok_fetch.fetch_html = boom("tiktok_fetch.fetch_html")
products.fetch_html = boom("products.fetch_html")
at.call_llm = boom("at.call_llm")
at.load_model = boom("at.load_model")

mod = types.ModuleType("t04_baseline_regressions")
mod.__file__ = str(repo / rel)          # 模块按真实路径解析 PIPE，不写任何临时文件进仓库
sys.argv = ["t04_baseline_regressions"]
exec(compile(src, mod.__file__, "exec"), mod.__dict__)

suite = unittest.defaultTestLoader.loadTestsFromTestCase(mod.AcceptanceTests)
buf = io.StringIO()
res = unittest.TextTestRunner(stream=buf, verbosity=2).run(suite)
text = buf.getvalue()
for name, tb in res.errors:
    text += "\n" + str(name) + "\n" + tb
for name, tb in res.failures:
    text += "\n" + str(name) + "\n" + tb
outp.write_text(json.dumps({"ran": res.testsRun, "errors": len(res.errors),
                            "failures": len(res.failures), "fired": fired,
                            "commit": commit, "source": src, "text": text},
                           ensure_ascii=False), encoding="utf-8")
'''


def _baseline_result():
    if "baseline" not in _RUN_CACHE:
        outp = _tmpdir() / "c08_baseline.json"
        child = _write_child("c08_baseline.py", _BASELINE)
        rc, text, out, err = _run([str(_PY), str(child), str(_REPO), _REL, str(outp),
                                   _BASELINE_COMMIT])
        assert_(outp.exists(),
                f"期望基线复现子进程写出结果文件，实得没有（退出码 {rc}）；输出尾部：{(text or '')[-500:]}")
        res = json.loads(outp.read_text(encoding="utf-8"))
        assert_(res.get("git_rc", 0) == 0,
                f"期望能用 git show {_BASELINE_COMMIT}:{_REL} 取到改动前的版本（钉死的基线提交），"
                f"实得失败：{res.get('git_err', '')[:300]}")
        _RUN_CACHE["baseline"] = res
    return _RUN_CACHE["baseline"]


def _baseline_must_be_the_old_file(baseline):
    """防止这段判据变成「修好的文件 vs 修好的工作树」的恒绿。

    三件事都要成立，否则说明「改动前」取错了版本，直接判红：
      1) 取到的内容与当前工作树不同（同一个文件 = 这段判据没有意义）；
      2) 基线里 scrape_run 的 mock 目标仍是 sc.parse_item（旧的反例版本）；
      3) 基线里**没有** sc.parse_item_detail（修复后的目标）。
    """
    commit = baseline.get("commit") or _BASELINE_COMMIT
    base_src = baseline.get("source") or ""
    assert_(base_src.strip(),
            f"期望取到基线提交 {commit} 里 {_REL} 的文件内容，实得空内容")
    assert_(base_src != _target_source(),
            f"基线提交 {commit} 里的 {_REL} 和当前工作树**一模一样** -> 「改动前」取到了修好后的版本，"
            "这段判据会恒绿、不能算数（基线哈希可能钉错了）")

    base_tree = _module_ast(base_src)
    base_helper = _find_method_in_module(base_tree, "scrape_run")
    assert_(base_helper is not None,
            f"期望基线提交 {commit} 的 {_REL} 里有 scrape_run()（第一个用例的抓取入口），实得没有")
    base_patched = _patch_object_targets(base_helper)
    assert_("parse_item" in base_patched,
            f"期望基线提交 {commit} 里 scrape_run patch 的是旧目标 sc.parse_item，"
            f"实得 patch 了 {base_patched}（这不是「改动前」的那个版本）")
    assert_("parse_item_detail" not in base_patched,
            f"期望基线提交 {commit} 里 scrape_run **没有** patch sc.parse_item_detail，"
            f"实得 patch 了 {base_patched}（说明取到的是修好后的版本）")
    print(f"        证据：改动前 = 钉死的基线提交 {commit} 里的 {_REL}"
          f"（scrape_run patch 的是 {[p for p in base_patched if p.startswith('parse_item')]}，"
          f"与当前工作树不同）")


def case_08_before_and_after_evidence():
    report = _find_report()
    baseline = _baseline_result()
    _baseline_must_be_the_old_file(baseline)
    assert_(not baseline.get("fired"),
            "期望基线复现全程不联网，实得触达了：" + "、".join(baseline.get("fired") or []))
    before_ok = ("FAILED (errors=2)" in baseline["text"]
                 and re.search(r"^Ran \d+ tests? in", baseline["text"], re.M) is not None
                 and _TEST_LONG in baseline["text"] and _TEST_RETRY in baseline["text"]
                 and "Traceback (most recent call last)" in baseline["text"])
    assert_(before_ok,
            "期望复现出改动前的真实红：'FAILED (errors=2)' + 两个用例名 + Traceback，实得 "
            f"ran={baseline['ran']} errors={baseline['errors']} failures={baseline['failures']}；"
            f"尾部：{baseline['text'][-500:]}")

    rc, text = _current_run()
    after_ok = (re.search(r"^Ran \d+ tests? in", text, re.M) is not None
                and re.search(r"^OK", text, re.M) is not None)
    assert_(after_ok, "期望改动后的真实绿：'Ran N tests' + 'OK'，实得没有")

    if report is None:
        print("        [note] 没有找到 T-04 报告文件（先找 "
              + str(_REPORT_CANDIDATES[0].relative_to(_REPO))
              + "，再找作业目录里的副本）；本用例改用可复现证据：用钉死的基线提交真实重跑出改动前的红，"
              "再跑当前文件拿到改动后的绿。报告落盘后本用例自动改判报告文本。")
        return

    rtext = report.read_text(encoding="utf-8", errors="replace")
    need = ["FAILED (errors=2)", _TEST_LONG, _TEST_RETRY, "OK"]
    lack = [n for n in need if n not in rtext]
    if not re.search(r"Ran 27 tests", rtext):
        lack.append("Ran 27 tests")
    assert_(not lack,
            f"报告 {report.name} 里缺：{lack}（改动前两段 + 改动后一段真实输出）")
    print(f"        证据：读到报告 {report}（含改动前 'FAILED (errors=2)' + 两个用例名 + "
          f"改动后 'Ran 27 tests' + 'OK'）；另用钉死的基线提交 "
          f"{baseline.get('commit')} 真跑复现了改动前的红")


# ---------------------------------------------------------------- C-09
def case_09_report_explains_patch_target():
    src = _target_source()
    tree = _module_ast(src)

    helper = _find_method_in_module(tree, "scrape_run")
    assert_(helper is not None, "期望被测文件里有 scrape_run() helper（第一个用例的抓取入口），实得没有")
    patched = _patch_object_targets(helper)
    assert_("fetch_page" in patched,
            f"期望 scrape_run 里 patch 了 sc.fetch_page（不 patch 就会真去抓），实得 patch 了 {patched}")
    parser = [p for p in patched if p.startswith("parse_item")]
    assert_(len(parser) == 1,
            f"期望 scrape_run 里恰好 patch 一个 parse_item* 目标，实得 {parser or '一个都没有'}")
    called = _called_in_module_func(_SCRIPTS / "scrape_tiktok_video.py", "main")
    assert_(parser[0] in called,
            f"期望 patch 的目标 {parser[0]!r} 是 sc.main() 真正调用的符号，"
            f"实得 main() 调用的是 {sorted(called)}")
    assert_("parse_item" not in called,
            "期望 sc.main() 不调用 parse_item（所以 patch parse_item 是打偏），实得 main() 调用了它")

    rt = _find_test_method(tree, _TEST_RETRY)
    assert_(rt is not None, f"期望测试文件里有 {_TEST_RETRY}，实得没有")
    rpatched = _patch_object_targets(rt)
    rparser = [p for p in rpatched if p.startswith("parse_item")]
    assert_(len(rparser) == 1,
            f"期望第二个用例恰好 patch 一个 parse_item* 目标，实得 {rparser or '一个都没有'}")
    assert_("fetch_page" in rpatched,
            f"期望第二个用例 patch 了 sc.fetch_page，实得 patch 了 {rpatched}")
    rcalled = _called_in_module_func(_SCRIPTS / "retry_scrape_failed.py", "main")
    assert_(rparser[0] in rcalled,
            f"期望 patch 的目标 {rparser[0]!r} 是 retry.main() 真正调用的符号，"
            f"实得它调用的是 {sorted(rcalled)}")
    assert_("parse_item" not in rcalled,
            "期望 retry.main() 不调用 parse_item（所以 patch parse_item 是打偏），实得调用了它")
    print(f"        证据：scrape_run -> {parser[0]}（sc.main 实调） / "
          f"retry 用例 -> {rparser[0]}（retry.main 实调）")

    report = _find_report()
    if report is None:
        print("        [note] 没有报告文件，本用例只核对了『patch 目标 == main() 实调符号』"
              "（报告里那句『原来 patch X -> 现在 patch Y』等 PM 落盘后自动核对）")
        return
    rtext = report.read_text(encoding="utf-8", errors="replace")
    lack = []
    for sym in ("parse_item", parser[0], rparser[0]):
        if sym not in rtext:
            lack.append(sym)
    assert_(not lack, f"报告 {report.name} 里缺这些 patch 目标说明：{lack}")
    print(f"        证据：读到报告 {report}，里面写明 scrape_run 从 'parse_item' 改成 {parser[0]!r}、"
          f"retry 用例改成 {rparser[0]!r}")


# ---------------------------------------------------------------- C-10
def case_10_project_suite_green():
    tests = sorted((_REPO / "pipeline" / "tests").glob("test_*.py"))
    assert_(tests, "期望 pipeline/tests 下有 test_*.py，实得一个都没有")
    names = [p.name for p in tests]
    for need in ("test_scrape_failure_reason.py", "test_scrape_no_clobber.py"):
        assert_(need in names, f"期望统测文件清单里有本作业新增的 {need}，实得没有：{names}")

    bad = []
    for p in tests:
        rc, text, out, err = _run([str(_PY), str(p)])
        if rc != 0:
            tail = (text or "").strip().splitlines()[-6:]
            bad.append(f"{p.name}(exit={rc}) {' | '.join(tail)}")
    assert_(not bad,
            f"期望统测全绿（逐个跑 {len(tests)} 个测试文件，退出码全 0），实得 {len(bad)} 个红：" +
            " ;; ".join(bad)[:1200])
    print(f"        证据：{len(tests)} 个测试文件逐个跑完，退出码全 0（{', '.join(names)}）")


CASES = [
    ("C-01 被测测试文件现在全绿、退出码 0", case_01_file_is_green),
    ("C-02 用例数没有减少（>= 27）", case_02_test_count_not_reduced),
    ("C-03 没有靠删用例 / skip 换绿", case_03_no_skip_and_names_kept),
    ("C-04 第一个用例的三条断言没被削弱", case_04_first_test_assertions_intact),
    ("C-05 第二个用例的长 desc / hashtags 断言没被削弱", case_05_second_test_assertions_intact),
    ("C-06 运行输出里没有兜底被真的调起", case_06_no_fallback_in_output),
    ("C-07 网络与浏览器兜底入口注入异常后仍全绿", case_07_no_real_network_and_no_fallback),
    ("C-08 有改动前（2 errors）与改动后（OK）两段真实输出", case_08_before_and_after_evidence),
    ("C-09 patch 目标是 main() 真正调用的符号", case_09_report_explains_patch_target),
    ("C-10 项目统测命令全绿（PRD 第 5 节第 5 条）", case_10_project_suite_green),
]


def main():
    print("T-04 用例（qa/T-04/cases.py，清单见 .dsh-crew/jobs/s2-scrape-failure-fix/T-04-plan.md）：")
    try:
        for name, fn in CASES:
            check(name, fn)
    finally:
        if _TMPDIR is not None:
            shutil.rmtree(_TMPDIR, ignore_errors=True)
    print(f"\n通过 {_ok} / {_ok + _fail}")
    print(f"T-04 RESULT: {_ok}/{_ok + _fail} ok, exit {0 if _fail == 0 else 1}")
    return 0 if _fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
