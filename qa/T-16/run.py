# -*- coding: utf-8 -*-
r"""qa/T-16/run.py —— T-16「日志写到 logs\：`_boot.py` / `_boot2.py` 读 `WB_LOG_DIR`」的 QA 用例

用例清单：`.dsh-crew/jobs/portable-package/M1-qa-plan.md` 的 T-16 段（C-01 ~ C-08，一条一条照写）。
DoD 来源：本作业 `docs/tasks/tasks.md` 里 T-16 的 DoD 第 1~5 条。

跑法（清单指定的解释器）：
    cd D:\tiktok-workbench
    pipeline\.venv\Scripts\python.exe qa\T-16\run.py

边界（硬约束）：
  · 只读产品代码，不写 `webapp\`，不修改 `_boot.py` / `_boot2.py`；
  · 所有「import 模块」的检查都在**子进程**里做，避免本用例自己把服务带起来；
  · 实跑只用 **8792**（清单/DoD 指定），起停只杀自己记录的 pid；不碰用户可能仍在跑的 8789；
  · 没有 SKIP 分支：端口被占、解释器缺失、目录建不出来都判红并写清原因，不把「没跑」当通过。
"""
from __future__ import annotations

import ast
import atexit
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

# ── 控制台/管道编码：被管道接走时用 UTF-8（与 qa/T-14 等既有目录同做法） ──────────────
for _stream in (sys.stdout, sys.stderr):
    try:
        if not _stream.isatty():
            _stream.reconfigure(encoding="utf-8", errors="replace")
        else:
            _stream.reconfigure(errors="replace")
    except Exception:                        # noqa: BLE001
        pass

# ── 路径：全部按本文件位置推，与 cwd 无关 ────────────────────────────────────────
QA_DIR = Path(__file__).resolve().parent            # ...\qa\T-16
REPO = QA_DIR.parents[1]                            # 仓库根
WEBAPP = REPO / "webapp"
BOOT = WEBAPP / "_boot.py"
BOOT2 = WEBAPP / "_boot2.py"
UNIT = WEBAPP / "tests" / "boot_log_dir.test.py"
WEBAPP_PY = WEBAPP / ".venv" / "Scripts" / "python.exe"   # 产品侧解释器（清单指定）
REPO_PIPELINE_PY = REPO / "pipeline" / ".venv" / "Scripts" / "python.exe"

LOG_NAME = "_server_run.log"
DEFAULT_LOG = WEBAPP / LOG_NAME
LIVE_PORT = 8792                                    # DoD 3 指定的实跑端口
OTHER_PORT = 9999                                   # DoD 5 用来证明 _boot2 不认 --port
LIVE_LOG_DIR = Path("D:/测试 目录/logs")            # DoD 3 字面路径（中文 + 空格）

OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
_ok = 0
_fail = 0
_failed_ids: list[str] = []
_unit_out = ""                                      # C-01 的输出，给 C-03 当「复用单测」证据
CHILDREN: list[tuple[subprocess.Popen, str]] = []    # 只用来兜底清理自己起的进程


# ═════════════════════════════ 小工具 ═════════════════════════════
def assert_(cond, msg="断言失败"):
    if not cond:
        raise AssertionError(msg)


def _emit(text):
    for line in (text or "").splitlines():
        if line.strip():
            print("        " + line.rstrip())


def check(cid, name, fn):
    """跑一条用例：异常一律转成 FAIL，不让整段崩掉；每条用例可无限独立失败。"""
    global _ok, _fail
    try:
        ev = fn() or []
    except AssertionError as exc:
        print("FAIL " + cid + " " + name + " -> " + str(exc))
        _emit(getattr(exc, "evidence", ""))
        _fail += 1
        _failed_ids.append(cid)
    except Exception as exc:                 # noqa: BLE001
        print("FAIL " + cid + " " + name + " -> " + type(exc).__name__ + ": " + str(exc))
        _fail += 1
        _failed_ids.append(cid)
    else:
        print("ok " + cid + " " + name)
        _emit("\n".join(ev) if isinstance(ev, list) else str(ev))
        _ok += 1


def clean_env(**extra):
    """剥掉 WB_* / HF_* / PYTHON*（包括父会话里的 WB_LOG_DIR），只留本用例显式给的。"""
    env = {k: v for k, v in os.environ.items()
           if not k.upper().startswith(("WB_", "HF_", "PYTHON"))}
    env["PYTHONDONTWRITEBYTECODE"] = "1"     # 别用 __pycache__ 干扰「import 不建文件」
    env["PYTHONIOENCODING"] = "utf-8"
    for key, value in extra.items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = str(value)
    return env


def port_busy(port, timeout=0.4):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def wait_port_free(port, timeout=20.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not port_busy(port):
            return True
        time.sleep(0.2)
    return not port_busy(port)


def wait_health(port, timeout=10.0):
    """等 /api/health 200；返回 (ok, 用时秒, 最后一次错误)。"""
    started = time.time()
    last = "（还没有尝试）"
    while time.time() - started < timeout:
        try:
            with OPENER.open("http://127.0.0.1:%d/api/health" % port, timeout=1.5) as resp:
                if resp.status == 200:
                    return True, time.time() - started, ""
                last = "HTTP %d" % resp.status
        except Exception as exc:             # noqa: BLE001  还没起来
            last = type(exc).__name__ + ": " + str(exc)
        time.sleep(0.2)
    return False, time.time() - started, last


def read_since(path: Path, offset: int):
    """从字节偏移读追加内容（日志可能正被别的进程写，所以按字节 seek 再解码）。"""
    if not path.exists():
        return ""
    size = path.stat().st_size
    if size <= offset:
        return ""
    with open(path, "rb") as fh:
        fh.seek(offset)
        return fh.read().decode("utf-8", "replace")


def file_state(path: Path):
    try:
        st = path.stat()
        return (st.st_size, st.st_mtime_ns)
    except OSError:
        return None


def top_level_logs():
    return sorted(p.name for p in WEBAPP.glob("*.log"))


def webapp_names():
    skip = {"__pycache__"}
    return sorted(p.name for p in WEBAPP.iterdir() if p.name not in skip)


def logs_dir_names():
    d = WEBAPP / "logs"
    if not d.is_dir():
        return None
    return sorted(p.name for p in d.iterdir())


def _kill(proc):
    if proc is None or proc.poll() is not None:
        return
    try:
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                       capture_output=True, text=True, timeout=30)
        proc.wait(timeout=15)
    except Exception:                        # noqa: BLE001  兜底：杀不掉就强杀
        try:
            proc.kill()
            proc.wait(timeout=10)
        except Exception:                    # noqa: BLE001
            pass


def stop(proc):
    _kill(proc)
    try:
        CHILDREN.remove(next(item for item in CHILDREN if item[0] is proc))
    except (StopIteration, ValueError):
        pass


def _cleanup_all():
    for proc, _label in list(CHILDREN):
        _kill(proc)


atexit.register(_cleanup_all)


def start_boot(script: Path, args, env, label: str):
    """起一个 boot wrapper；只记录自己起的 pid，绝不做按名字批量杀。"""
    assert_(WEBAPP_PY.is_file(), "找不到产品侧解释器：%s" % WEBAPP_PY)
    flags = 0
    if os.name == "nt":
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    proc = subprocess.Popen(
        [str(WEBAPP_PY), str(script), *[str(a) for a in args]],
        cwd=str(WEBAPP), env=env,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=flags,
    )
    CHILDREN.append((proc, label))
    return proc


# ═════════════════════════════ C-01 ═════════════════════════════
# 清单 1 / DoD 1：工程师的单测退出 0。这里不只看退出码：还要求汇总行是「通过 N / N」且 N 不少于
# 该用例应有的 8 条检查（删检查、静默变少都会判红）。
def case_01_unit_test():
    global _unit_out
    assert_(UNIT.is_file(), "找不到工程师单测：%s" % UNIT)
    assert_(not port_busy(LIVE_PORT),
            "端口 %d 已被占用，单测里的实跑必然失败（换时间重跑；不做 SKIP）" % LIVE_PORT)
    proc = subprocess.run([str(WEBAPP_PY), str(UNIT)], cwd=str(WEBAPP), env=clean_env(),
                          capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=300)
    _unit_out = (proc.stdout or "") + (proc.stderr or "")
    tail = "\n".join([ln for ln in _unit_out.splitlines() if ln.strip()][-6:])
    assert_(proc.returncode == 0,
            "webapp\\tests\\boot_log_dir.test.py 退出码 %d；输出尾部：\n%s" % (proc.returncode, tail))
    hits = []
    for line in _unit_out.splitlines():
        line = line.strip()
        if line.startswith("通过 ") and "/" in line:
            try:
                got, total = line.replace("通过", "").split("/")
                hits.append((int(got.strip()), int(total.strip())))
            except ValueError:
                pass
    assert_(hits, "单测输出里没有「通过 N / M」汇总行；尾部：\n%s" % tail)
    got, total = hits[-1]
    assert_(got == total, "单测没有全绿：%d / %d" % (got, total))
    assert_(total >= 8, "单测只有 %d 条检查（T-16 应有 8 条：import 4 + 形状 2 + 实跑 2），"
                        "像是检查被删/被跳过" % total)
    assert_("失败" not in _unit_out, "单测输出里出现「失败」行（退出码却是 0，自相矛盾）")
    return ["证据：cd webapp && .venv\\Scripts\\python.exe tests\\boot_log_dir.test.py -> exit=%d，%s"
            % (proc.returncode, "通过 %d / %d" % (got, total)),
            "证据：单测输出尾部：" + (tail.splitlines()[-1] if tail else "（空）")]


# ═════════════════════════════ C-02 ═════════════════════════════
# 清单 2：WB_LOG_DIR=<临时空目录> 下子进程 import 两个模块后正常退出；临时目录与 webapp\ 的
# 文件清单前后一致（不建文件、不起服务）。
def case_02_import_no_side_effect():
    assert_(BOOT.is_file() and BOOT2.is_file(), "找不到 _boot.py / _boot2.py")
    root = Path(tempfile.mkdtemp(prefix="qa_t16_import_"))
    probe_cwd = Path(tempfile.mkdtemp(prefix="qa_t16_cwd_"))
    guard = socket.socket(socket.AF_INET, socket.SOCK_STREAM)   # 占住一个端口：真起服务就会失败
    tmp_log_dir = root / "日志 目录"
    try:
        guard.bind(("127.0.0.1", 0))
        guard.listen(1)
        guard.setblocking(False)
        env = clean_env(WB_LOG_DIR=str(tmp_log_dir), WB_PORT=str(guard.getsockname()[1]))
        code = (
            "import os, sys\n"
            "sys.path.insert(0, %r)\n"
            "import _boot, _boot2\n"
            "print('BOOT=' + _boot.log_path())\n"
            "print('BOOT2=' + _boot2.log_path())\n"
            "print('CWD=' + os.getcwd())\n" % str(WEBAPP)
        )
        before_names = webapp_names()
        before_logs = top_level_logs()
        before_logs_dir = logs_dir_names()
        started = time.time()
        proc = subprocess.run([str(WEBAPP_PY), "-c", code], cwd=str(probe_cwd), env=env,
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=45)
        elapsed = time.time() - started
        out = (proc.stdout or "") + (proc.stderr or "")
        assert_(proc.returncode == 0,
                "子进程 import _boot, _boot2 退出码 %d；输出：\n%s" % (proc.returncode, out[-1200:]))
        assert_(elapsed < 20,
                "import 两个模块花了 %.1fs —— 看着像把服务也起起来了" % elapsed)
        values = {}
        for line in (proc.stdout or "").splitlines():
            if "=" in line:
                key, _, val = line.partition("=")
                values[key] = val
        assert_(values.get("BOOT") == str(tmp_log_dir / LOG_NAME),
                "_boot.log_path()=%r，期望 %r" % (values.get("BOOT"), str(tmp_log_dir / LOG_NAME)))
        assert_(values.get("BOOT2") == str(tmp_log_dir / LOG_NAME),
                "_boot2.log_path()=%r，期望 %r" % (values.get("BOOT2"), str(tmp_log_dir / LOG_NAME)))
        assert_(values.get("CWD") == str(probe_cwd),
                "import 改了 cwd：%r != %r" % (values.get("CWD"), str(probe_cwd)))
        leftovers = sorted(p.name for p in probe_cwd.iterdir())
        assert_(not leftovers, "import 在子进程 cwd 建了文件：%s" % leftovers)
        assert_(not tmp_log_dir.exists(),
                "import 就把 WB_LOG_DIR 建出来了（不该有副作用）：%s" % tmp_log_dir)
        # 端口守护：真起服务会占这个端口；我们仍持有它，说明没人监听
        try:
            guard.accept()
            raised = True
        except BlockingIOError:
            raised = False
        except OSError:
            raised = False
        assert_(not raised, "import 期间有人在守护端口上建立了连接（说明起了服务）")
        assert_(webapp_names() == before_names,
                "import 前后 webapp\\ 顶层清单变了：%s -> %s"
                % (sorted(set(before_names) - set(webapp_names())),
                   sorted(set(webapp_names()) - set(before_names))))
        assert_(top_level_logs() == before_logs, "import 在 webapp\\ 下新建了 *.log")
        assert_(logs_dir_names() == before_logs_dir, "import 改了 webapp\\logs 的清单")
        return ["证据：子进程 `import _boot, _boot2` exit=0，用时 %.2fs" % elapsed,
                "证据：WB_LOG_DIR（临时）= %s 不存在，import 没建它；子进程 cwd 空" % tmp_log_dir,
                "证据：webapp\\ 顶层清单、*.log 清单、logs\\ 清单前后一致（%d 项）" % len(before_names)]
    finally:
        guard.close()
        shutil.rmtree(root, ignore_errors=True)
        shutil.rmtree(probe_cwd, ignore_errors=True)


# ═════════════════════════════ C-03 ═════════════════════════════
# 清单 3 / DoD 1：WB_LOG_DIR=<临时> 时两者都是 <临时>\_server_run.log；未设时都是
# webapp\_server_run.log。硬判据是独立子进程探针（不引用工程师用例的断言），
# 同时打印单测里对应的 PASS 行与行号当「复用单测」证据。
def _probe_log_path(module: str, log_dir):
    code = (
        "import os, sys\n"
        "sys.path.insert(0, %r)\n"
        "import %s as m\n"
        "print('LOG=' + m.log_path())\n"
        "print('CONST=' + str(getattr(m, 'LOG', '')))\n"
        "print('CWD=' + os.getcwd())\n" % (str(WEBAPP), module)
    )
    env = clean_env(WB_LOG_DIR=(None if log_dir is None else str(log_dir)))
    proc = subprocess.run([str(WEBAPP_PY), "-c", code], cwd=str(WEBAPP), env=env,
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=45)
    assert_(proc.returncode == 0,
            "探针 import %s 退出码 %d；输出：%s" % (module, proc.returncode,
                                                    ((proc.stdout or "") + (proc.stderr or ""))[-800:]))
    values = {}
    for line in (proc.stdout or "").splitlines():
        if "=" in line:
            key, _, val = line.partition("=")
            values[key] = val
    return values


def case_03_log_path_values():
    root = Path(tempfile.mkdtemp(prefix="qa_t16_paths_"))
    custom = root / "测试 目录" / "logs"
    try:
        want_custom = str(custom / LOG_NAME)
        want_default = str(DEFAULT_LOG)
        ev = []
        for module in ("_boot", "_boot2"):
            got = _probe_log_path(module, custom)
            assert_(got.get("LOG") == want_custom,
                    "%s.log_path()=%r（WB_LOG_DIR 已设），期望 %r" % (module, got.get("LOG"), want_custom))
            assert_(got.get("CONST") in ("", want_custom),
                    "%s 模块级 LOG=%r，期望 %r" % (module, got.get("CONST"), want_custom))
            got2 = _probe_log_path(module, None)
            assert_(got2.get("LOG") == want_default,
                    "%s.log_path()=%r（未设 WB_LOG_DIR），期望 %r" % (module, got2.get("LOG"), want_default))
            assert_(got2.get("CONST") in ("", want_default),
                    "%s 模块级 LOG=%r（未设时），期望 %r" % (module, got2.get("CONST"), want_default))
            ev.append("证据：%s：WB_LOG_DIR 已设 -> %s；未设 -> %s" % (module, got.get("LOG"), got2.get("LOG")))
        assert_(not custom.exists(), "只算路径就建了自定义日志目录（不该有副作用）：%s" % custom)
        # 复用单测：把对应断言的 PASS 行与源码行号贴出来
        unit_lines = UNIT.read_text(encoding="utf-8", errors="replace").splitlines()
        needle_lines = [i + 1 for i, ln in enumerate(unit_lines) if "WB_LOG_DIR" in ln]
        pass_lines = [ln.strip() for ln in _unit_out.splitlines() if ln.strip().startswith("ok ")
                      and "WB_LOG_DIR" in ln]
        assert_(pass_lines, "单测输出里找不到与 WB_LOG_DIR 路径相关的 PASS 行（C-01 可能没跑单测）")
        assert_(needle_lines, "单测源码里找不到 WB_LOG_DIR 路径断言（用例已被改写？）")
        ev.append("证据：单测对应断言 PASS %d 条：%s" % (len(pass_lines), " | ".join(pass_lines[:4])))
        ev.append("证据：单测源码行号：%s" % ", ".join("boot_log_dir.test.py:%d" % n for n in needle_lines[:4]))
        return ev
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ═════════════════════════════ C-04 ═════════════════════════════
# 清单 4 / DoD 2：顶层只有 docstring/import/常量赋值/def/if __name__ 块（AST 级），
# 无顶层起服务调用；_boot.py 接 --port，_boot2.py 不接参数只认 WB_PORT。
_ALLOWED_NODES = (ast.Import, ast.ImportFrom, ast.Assign, ast.AnnAssign,
                  ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
_PURE_NAMES = {"log_path", "os.path.dirname", "os.path.abspath", "os.path.join",
               "os.path.dirname", "str", "int"}


def _is_main_guard(node):
    test = getattr(node, "test", None)
    return (isinstance(node, ast.If) and isinstance(test, ast.Compare)
            and isinstance(test.left, ast.Name) and test.left.id == "__name__"
            and len(test.comparators) == 1 and isinstance(test.comparators[0], ast.Constant)
            and test.comparators[0].value == "__main__")


def _dotted(func):
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        base = _dotted(func.value)
        return (base + "." if base else "") + func.attr
    return ""


def _shape_violations(source: str, name: str):
    """返回违规清单；空列表 = 顶层形状合格。"""
    tree = ast.parse(source, filename=name)
    bad, guards = [], []
    for node in tree.body:
        if (isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)):
            continue                                    # 模块 docstring
        if _is_main_guard(node):
            guards.append(node)
            continue
        if isinstance(node, _ALLOWED_NODES):
            value = getattr(node, "value", None)
            if isinstance(node, (ast.Assign, ast.AnnAssign)) and value is not None:
                for call in (n for n in ast.walk(value) if isinstance(n, ast.Call)):
                    called = _dotted(call.func)
                    if called not in _PURE_NAMES:
                        bad.append("%s@L%d 顶层赋值里调用了 %s()（只允许纯函数）"
                                   % (type(node).__name__, node.lineno, called or "<expr>"))
            continue
        bad.append("%s@L%d" % (type(node).__name__, getattr(node, "lineno", "?")))
    if len(guards) != 1:
        bad.append("if __name__ 块数量 = %d（期望恰好 1 个）" % len(guards))
    else:
        body = guards[0].body
        if not (len(body) == 1 and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Call)
                and _dotted(body[0].value.func) == "main"):
            bad.append("__main__ 块里不是单独一句 main()：%s"
                       % [type(n).__name__ for n in body])
    return bad


def case_04_top_level_shape():
    violations = []
    for path in (BOOT, BOOT2):
        bad = _shape_violations(path.read_text(encoding="utf-8"), path.name)
        violations.extend("%s: %s" % (path.name, item) for item in bad)
    assert_(not violations, "顶层出现了导入就会执行的东西：\n" + "\n".join(violations))
    # 规则自检：喂一段「顶层直接 main()」的源码，必须被判违规，否则这条用例是空转的
    self_test = _shape_violations("import os\n\ndef main():\n    pass\n\nmain()\n", "<self-test>")
    assert_(self_test, "AST 规则自检失败：顶层 main() 没有被判成违规（规则形同虚设）")

    boot_src = BOOT.read_text(encoding="utf-8")
    boot2_src = BOOT2.read_text(encoding="utf-8")
    boot_tree = ast.parse(boot_src)
    boot2_tree = ast.parse(boot2_src)
    assert_("_port_arg" in boot_src, "_boot.py 应有 --port 解析（_port_arg）")
    assert_("_port_arg" not in boot2_src, "_boot2.py 不该有端口参数解析器")
    main_boot2 = next((n for n in boot2_tree.body
                       if isinstance(n, ast.FunctionDef) and n.name == "main"), None)
    assert_(main_boot2 is not None, "_boot2.py 应有 main()")
    assert_(not main_boot2.args.args,
            "_boot2.py 的 main() 不该接参数：%s" % [a.arg for a in main_boot2.args.args])
    reads_argv = [n for n in ast.walk(boot2_tree)
                  if isinstance(n, ast.Subscript) and isinstance(n.value, ast.Attribute)
                  and isinstance(n.value.value, ast.Name) and n.value.value.id == "sys"
                  and n.value.attr == "argv"]
    assert_(not reads_argv, "_boot2.py 不该读 sys.argv（它不接参数，端口只认 WB_PORT）")
    assert_(any(isinstance(n, ast.FunctionDef) and n.name == "main" for n in boot_tree.body),
            "_boot.py 应有 main()")
    return ["证据：_boot.py / _boot2.py 顶层语句类型 = %s / %s"
            % ([type(n).__name__ for n in boot_tree.body if not isinstance(n, ast.Expr)],
               [type(n).__name__ for n in boot2_tree.body if not isinstance(n, ast.Expr)]),
            "证据：违规清单为空；AST 规则自检（顶层 main()）已判违规 -> 规则不是空转",
            "证据：_boot.py 有 _port_arg/--port；_boot2.py 的 main() 无参数、不读 sys.argv"]


# ═════════════════════════════ C-05 ═════════════════════════════
# 清单 5 / DoD 2：_boot.py --port 8792 起服务且 health 200；_boot2.py --port 9999 的实际监听
# 端口由 WB_PORT 决定（不是 9999）。[起停]
def case_05_boot_port_contract():
    assert_(not port_busy(LIVE_PORT), "端口 %d 已被占用，无法实跑（换时间重跑，不做 SKIP）" % LIVE_PORT)
    tmp = Path(tempfile.mkdtemp(prefix="qa_t16_boot_"))
    tmp2 = Path(tempfile.mkdtemp(prefix="qa_t16_boot2_"))
    ev = []
    try:
        # ① _boot.py --port 8792
        proc = start_boot(BOOT, ["--port", str(LIVE_PORT)],
                          clean_env(WB_LOG_DIR=str(tmp)), "C-05/_boot")
        try:
            ok, elapsed, last = wait_health(LIVE_PORT, timeout=15.0)
            assert_(ok, "`_boot.py --port %d` 起不来：15s 内 health 没到 200（最后一次：%s）"
                        % (LIVE_PORT, last))
            assert_(port_busy(LIVE_PORT), "health 200 但 %d 没有在监听" % LIVE_PORT)
            log_file = tmp / LOG_NAME
            assert_(log_file.is_file() and log_file.stat().st_size > 0,
                    "_boot.py 的日志没落在 WB_LOG_DIR：%s" % log_file)
            text = log_file.read_text(encoding="utf-8", errors="replace")
            assert_("端口 %d" % LIVE_PORT in text,
                    "日志里没有本次启动行（端口 %d）：%r" % (LIVE_PORT, text[-400:]))
            ev.append("证据：`_boot.py --port %d` pid=%d，health 200（%.2fs），日志 %s 非空（%d 字节）"
                      % (LIVE_PORT, proc.pid, elapsed, log_file, log_file.stat().st_size))
        finally:
            stop(proc)
        assert_(wait_port_free(LIVE_PORT), "杀掉 pid 后 %d 仍被占用" % LIVE_PORT)

        # ② _boot2.py --port 9999，但 WB_PORT=8792 -> 实际监听 8792
        assert_(not port_busy(OTHER_PORT), "端口 %d 已被别的程序占用，无法证明「不认 9999」" % OTHER_PORT)
        proc2 = start_boot(BOOT2, ["--port", str(OTHER_PORT)],
                           clean_env(WB_LOG_DIR=str(tmp2), WB_PORT=str(LIVE_PORT)), "C-05/_boot2")
        try:
            ok2, elapsed2, last2 = wait_health(LIVE_PORT, timeout=15.0)
            assert_(ok2, "`_boot2.py --port %d`（WB_PORT=%d）起不来：%s"
                         % (OTHER_PORT, LIVE_PORT, last2))
            assert_(not port_busy(OTHER_PORT),
                    "_boot2.py 认了命令行 --port：%d 在监听（它应只认 WB_PORT）" % OTHER_PORT)
            log2 = (tmp2 / LOG_NAME).read_text(encoding="utf-8", errors="replace")
            assert_("监听 http://127.0.0.1:%d" % LIVE_PORT in log2,
                    "app.py 日志里没有 8792 的监听行：%r" % log2[-400:])
            assert_("%d" % OTHER_PORT not in log2, "_boot2.py 的日志里出现了 %d" % OTHER_PORT)
            ev.append("证据：`_boot2.py --port %d`（WB_PORT=%d）pid=%d，health %d 200（%.2fs），"
                      "%d 未监听；日志含「监听 http://127.0.0.1:%d」"
                      % (OTHER_PORT, LIVE_PORT, proc2.pid, LIVE_PORT, elapsed2, OTHER_PORT, LIVE_PORT))
        finally:
            stop(proc2)
        assert_(wait_port_free(LIVE_PORT), "杀掉 pid 后 %d 仍被占用" % LIVE_PORT)
        return ev
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        shutil.rmtree(tmp2, ignore_errors=True)


# ═════════════════════════════ C-06 ═════════════════════════════
# 清单 6 / DoD 3：WB_LOG_DIR=D:\测试 目录\logs 运行 _boot.py --port 8792，10 秒内
# <该目录>\_server_run.log 存在且非空、health 200；webapp\ 下无新日志。[起停]
def case_06_live_custom_log_dir():
    assert_(not port_busy(LIVE_PORT), "端口 %d 已被占用，无法实跑（换时间重跑，不做 SKIP）" % LIVE_PORT)
    log_file = LIVE_LOG_DIR / LOG_NAME
    pre_dir_exists = LIVE_LOG_DIR.is_dir()
    pre_log_exists = log_file.is_file()
    pre_size = log_file.stat().st_size if pre_log_exists else 0
    pre_default = file_state(DEFAULT_LOG)
    pre_logs = top_level_logs()
    proc = start_boot(BOOT, ["--port", str(LIVE_PORT)],
                      clean_env(WB_LOG_DIR=str(LIVE_LOG_DIR)), "C-06/_boot")
    started = time.time()
    try:
        # 日志要在 10 秒内出现且非空（DoD 的硬数字）
        deadline = started + 10.0
        while time.time() < deadline and not (log_file.is_file() and log_file.stat().st_size > pre_size):
            time.sleep(0.2)
        appeared = time.time() - started
        assert_(log_file.is_file(),
                "10 秒内 %s 没有出现（WB_LOG_DIR 没被 _boot.py 用上）" % log_file)
        size = log_file.stat().st_size
        assert_(size > pre_size and size > 0,
                "日志没有新增内容：前 %d 字节，后 %d 字节（%s）" % (pre_size, size, log_file))
        assert_(appeared <= 10.0, "日志 %.1fs 后才出现，超过 DoD 的 10 秒" % appeared)
        ok, elapsed, last = wait_health(LIVE_PORT, timeout=15.0)
        assert_(ok, "health 没到 200（最后一次：%s）" % last)
        text = log_file.read_text(encoding="utf-8", errors="replace")
        assert_("端口 %d" % LIVE_PORT in text,
                "日志里没有本次启动行（端口 %d）：%r" % (LIVE_PORT, text[-500:]))
        # webapp\ 下不许出现本次运行的日志：用「追加内容里有没有本进程的标记」判，避免把
        # 用户正在跑的 8789 服务写同一文件的字节算到本用例头上。
        appended = read_since(DEFAULT_LOG, pre_default[0] if pre_default else 0)
        assert_("端口 %d" % LIVE_PORT not in appended,
                "设了 WB_LOG_DIR，本次运行仍往 webapp\\_server_run.log 写了：%r" % appended[-400:])
        assert_(top_level_logs() == pre_logs,
                "webapp\\ 下出现了新的 *.log：%s" % sorted(set(top_level_logs()) - set(pre_logs)))
        return ["证据：WB_LOG_DIR=%s，%.2fs 出现日志（pre=%d -> now=%d 字节）"
                % (LIVE_LOG_DIR, appeared, pre_size, size),
                "证据：http://127.0.0.1:%d/api/health 200（%.2fs）；日志含「端口 %d」"
                % (LIVE_PORT, elapsed, LIVE_PORT),
                "证据：webapp\\_server_run.log 的追加段里没有本次运行的标记；webapp\\ 下无新 *.log",
                "证据：本次实跑 pid=%d（用完已停）" % proc.pid]
    finally:
        stop(proc)
        assert_(wait_port_free(LIVE_PORT), "杀掉 pid 后 %d 仍被占用" % LIVE_PORT)
        if not pre_log_exists and log_file.is_file():
            try:
                log_file.unlink()
            except OSError:
                pass
        if not pre_dir_exists:
            for folder in (LIVE_LOG_DIR, LIVE_LOG_DIR.parent):
                try:
                    folder.rmdir()
                except OSError:
                    break


# ═════════════════════════════ C-07 ═════════════════════════════
# 清单 7 / DoD 4：不设 WB_LOG_DIR 实跑，webapp\_server_run.log 被追加（size/mtime 增加），
# 且没有出现第二份日志文件。[起停]
def case_07_default_log_regression():
    assert_(not port_busy(LIVE_PORT), "端口 %d 已被占用，无法实跑（换时间重跑，不做 SKIP）" % LIVE_PORT)
    before_state = file_state(DEFAULT_LOG)
    assert_(before_state is not None, "webapp\\_server_run.log 不存在，无法验证「继续被追加」")
    before_logs = top_level_logs()
    logs_dir_before = logs_dir_names()
    logs_dir_log = (WEBAPP / "logs" / LOG_NAME)
    logs_dir_log_before = file_state(logs_dir_log)
    proc = start_boot(BOOT, ["--port", str(LIVE_PORT)], clean_env(), "C-07/_boot")
    try:
        ok, elapsed, last = wait_health(LIVE_PORT, timeout=15.0)
        assert_(ok, "不设 WB_LOG_DIR 时 _boot.py 起不来：%s" % last)
        appended = read_since(DEFAULT_LOG, before_state[0])
        assert_("端口 %d" % LIVE_PORT in appended,
                "webapp\\_server_run.log 的追加段里没有本次启动行（端口 %d）：%r"
                % (LIVE_PORT, appended[-500:]))
        after_state = file_state(DEFAULT_LOG)
        assert_(after_state is not None and after_state[0] > before_state[0],
                "webapp\\_server_run.log 没有被追加：size %d -> %s"
                % (before_state[0], after_state[0] if after_state else None))
        assert_(after_state[1] > before_state[1],
                "webapp\\_server_run.log 的 mtime 没有前进：%d -> %d"
                % (before_state[1], after_state[1]))
        assert_(top_level_logs() == before_logs,
                "webapp\\ 下出现了第二份 *.log：%s" % sorted(set(top_level_logs()) - set(before_logs)))
        assert_(logs_dir_names() == logs_dir_before,
                "webapp\\logs 里出现了新文件：%s"
                % sorted(set(logs_dir_names() or []) - set(logs_dir_before or [])))
        assert_(file_state(logs_dir_log) == logs_dir_log_before,
                "日志被写到了 webapp\\logs\\_server_run.log（不该出现第二份）")
        return ["证据：不设 WB_LOG_DIR 实跑 pid=%d，health 200（%.2fs）" % (proc.pid, elapsed),
                "证据：webapp\\_server_run.log size %d -> %d，mtime_ns %d -> %d"
                % (before_state[0], after_state[0], before_state[1], after_state[1]),
                "证据：追加段含「端口 %d」；webapp\\ 下无新 *.log，webapp\\logs 无新文件"
                % LIVE_PORT]
    finally:
        stop(proc)
        assert_(wait_port_free(LIVE_PORT), "杀掉 pid 后 %d 仍被占用" % LIVE_PORT)


# ═════════════════════════════ C-08 ═════════════════════════════
# 清单 8：QA 入口 —— 按清单指定的解释器/路径运行本文件，且上面的用例全部通过（退出码 0）。
# 这条自己也能红：解释器不对、文件不在清单位置、前面有用例失败，都会判红。
def case_08_qa_entry():
    assert_((QA_DIR / "run.py").resolve() == Path(__file__).resolve(),
            "本文件不在清单位置 qa\\T-16\\run.py：%s" % Path(__file__).resolve())
    assert_(Path(sys.executable).resolve() == REPO_PIPELINE_PY.resolve(),
            "本入口必须用清单指定的解释器跑：期望 %s，实得 %s"
            % (REPO_PIPELINE_PY, sys.executable))
    ids = [cid for cid, _name, _fn in CASES]
    expected = ["C-01", "C-02", "C-03", "C-04", "C-05", "C-06", "C-07", "C-08"]
    assert_(ids == expected, "用例表不是清单的 C-01~C-08：%s" % ids)
    if _failed_ids:
        raise AssertionError("前面有用例失败，QA 入口不能算绿：%s" % ", ".join(_failed_ids))
    return ["证据：qa\\T-16\\run.py + %s（清单指定解释器）" % sys.executable,
            "证据：本次进程退出码 0（C-01~C-07 全绿，见上面的 ok 行与汇总）"]


# ═════════════════════════════ main ═════════════════════════════
CASES = [
    ("C-01", "工程师单测退出 0（且汇总为 通过 N / N，N>=8）", case_01_unit_test),
    ("C-02", "子进程 import 两个模块无副作用（不建文件、不起服务）", case_02_import_no_side_effect),
    ("C-03", "WB_LOG_DIR 决定日志路径；未设回落 webapp\\（独立探针）", case_03_log_path_values),
    ("C-04", "顶层只有常量/函数，执行在 __main__ 下；_boot2 不接参数", case_04_top_level_shape),
    ("C-05", "_boot.py --port 8792 health 200；_boot2.py --port 9999 实际听 WB_PORT", case_05_boot_port_contract),
    ("C-06", "实跑 WB_LOG_DIR=D:\\测试 目录\\logs：10s 内日志出现、health 200、webapp 无新日志", case_06_live_custom_log_dir),
    ("C-07", "不设 WB_LOG_DIR：webapp\\_server_run.log 被追加、无第二份日志", case_07_default_log_regression),
    ("C-08", "QA 入口：清单解释器 + 用例表齐全 + 全绿", case_08_qa_entry),
]


def main():
    print("T-16 用例（qa/T-16/run.py，清单见 .dsh-crew/jobs/portable-package/M1-qa-plan.md 的 T-16 段）：")
    print("仓库=%s" % REPO)
    print("产品解释器=%s" % WEBAPP_PY)
    print("实跑端口=%d（只杀自己起的 pid；不碰 8789）" % LIVE_PORT)
    print("")
    started = time.time()
    for cid, name, fn in CASES:
        check(cid, name, fn)
    total = _ok + _fail
    print("")
    print("结果：通过 %d / %d（失败用例：%s）"
          % (_ok, total, ", ".join(_failed_ids) if _failed_ids else "无"))
    print("T-16 RESULT: %d/%d ok, exit %d（用时 %d 秒）"
          % (_ok, total, 0 if _fail == 0 else 1, round(time.time() - started)))
    return 0 if _fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
