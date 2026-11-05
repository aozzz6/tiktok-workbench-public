# -*- coding: utf-8 -*-
"""旧仓库路径回归：仓库搬家后，脚本与用例不许再写死旧仓库路径。

扫描范围：
  - webapp/tests/*.js、webapp/tests/*.py
  - webapp/*.bat、webapp/*.vbs（启动脚本）

命中硬编码旧仓库路径即失败（退出码 1），逐条列出文件:行号。
针串在运行时拼出，避免本文件自己命中。

跑法：cd webapp && .venv\\Scripts\\python.exe tests\\repo_paths.test.py
"""
import sys
from pathlib import Path

WEBAPP = Path(__file__).resolve().parents[1]
NEEDLE = "workbuddy" + "_project"


def targets():
    tests = WEBAPP / "tests"
    files = list(tests.glob("*.js")) + list(tests.glob("*.py"))
    files += list(WEBAPP.glob("*.bat")) + list(WEBAPP.glob("*.vbs"))
    return sorted(files)


def scan(files):
    hits = []
    for path in files:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            hits.append((path, 0, "无法读取：%s" % exc))
            continue
        for no, line in enumerate(text.splitlines(), 1):
            if NEEDLE in line:
                hits.append((path, no, line.strip()))
    return hits


def main():
    files = targets()
    if not files:
        print("FAIL 待扫描文件为空：%s" % WEBAPP)
        return 1
    print("扫描 %d 个文件（tests/*.js、tests/*.py、*.bat、*.vbs）" % len(files))
    hits = scan(files)
    if hits:
        print("FAIL 发现 %d 处硬编码旧仓库路径（%s）：" % (len(hits), NEEDLE))
        for path, no, line in hits:
            try:
                rel = path.relative_to(WEBAPP)
            except ValueError:
                rel = path
            print("  %s:%d: %s" % (rel, no, line))
        print("共 %d 个文件受影响。" % len({h[0] for h in hits}))
        return 1
    print("PASS 未发现硬编码旧仓库路径")
    return 0


if __name__ == "__main__":
    sys.exit(main())
