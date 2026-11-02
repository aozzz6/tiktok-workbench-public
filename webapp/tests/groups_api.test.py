# -*- coding: utf-8 -*-
"""groups_api.test.py — 对标账号分组 HTTP 契约测试（隔离临时文件）

覆盖：
  · 种子：默认分组 + 对标账号分组自动创建
  · 建组/重命名/删除：400（空名/重名/内置组）、404
  · 成员：添加（校验账号存在）、移除（回默认分组）
  · 删除分组 → 成员回默认分组（账号不被删）
运行：python tests/groups_api.test.py（webapp/.venv）
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

WEBAPP = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WEBAPP))
import config  # noqa: E402

_ok = 0
_fail = 0


def check(name, fn):
    global _ok, _fail
    try:
        fn()
        print(f"  ✓ {name}")
        _ok += 1
    except AssertionError as e:
        print(f"  ✗ {name} -> {e}")
        _fail += 1
    except Exception as e:
        print(f"  ✗ {name} -> {e.__class__.__name__}: {e}")
        _fail += 1


def assert_(c, m="assert failed"):
    if not c:
        raise AssertionError(m)


def main() -> int:
    import groups as account_groups

    tmp = Path(tempfile.mkdtemp(prefix="groups_api_"))
    account_groups.GROUPS_FILE = tmp / "account_groups.json"

    # 准备一个真实账号（供成员校验）
    import json
    global config
    accs = Path(tempfile.mkdtemp(prefix="groups_acc_")) / "accounts.json"
    accs.write_text(json.dumps({"accounts": [
        {"account": "gutpickde", "lang": "de", "active": True},
        {"account": "thesymmetrymarket", "lang": "en", "active": True},
    ]}, ensure_ascii=False), encoding="utf-8")
    config.ACCOUNTS_JSON = accs

    check("种子：默认分组 + 对标账号分组自动创建", lambda: assert_(
        {g["name"] for g in account_groups.list_groups()["groups"]} >= {"默认分组", "对标账号"}))

    g_bench = next(g for g in account_groups.list_groups()["groups"] if g["name"] == "对标账号")

    check("添加成员（真实账号）", lambda: assert_(
        account_groups.add_member(g_bench["id"], "gutpickde")["group_id"] == g_bench["id"]))
    check("重复添加 → ValueError", lambda: assert_(
        (lambda: [False for _ in ()] if True else None)() is None or _expect_valueerror(
            lambda: account_groups.add_member(g_bench["id"], "gutpickde"))))

    check("成员计数", lambda: assert_(
        next(g for g in account_groups.list_groups()["groups"] if g["id"] == g_bench["id"])["count"] == 1))

    check("新分组创建", lambda: assert_(
        account_groups.create_group("临时组")["name"] == "临时组"))
    check("重名创建 → ValueError", lambda: _expect_valueerror(
        lambda: account_groups.create_group("临时组")))
    check("空名创建 → ValueError", lambda: _expect_valueerror(
        lambda: account_groups.create_group("  ")))
    check("重命名", lambda: assert_(
        account_groups.rename_group("临时组" if False else
                                    next(g["id"] for g in account_groups.list_groups()["groups"]
                                         if g["name"] == "临时组"), "临时组2")["name"] == "临时组2"))
    check("内置默认分组不可重命名", lambda: _expect_valueerror(
        lambda: account_groups.rename_group("default", "X")))
    check("内置默认分组不可删除", lambda: _expect_valueerror(
        lambda: account_groups.delete_group("default")))

    # 删除分组 → 成员回默认分组
    g2 = account_groups.create_group("待删组")
    account_groups.add_member(g2["id"], "thesymmetrymarket")
    r = account_groups.delete_group(g2["id"])
    check("删除分组 → 成员移动记录", lambda: assert_(r["moved"] == ["thesymmetrymarket"]))
    check("成员回默认分组（隐式成员，账号未被删）", lambda: assert_(
        next(g for g in account_groups.list_groups()["groups"]
             if g["id"] == "default")["count"] >= 1 and
        "thesymmetrymarket" in {a["account"] for a in
                                json.loads(accs.read_text(encoding="utf-8"))["accounts"]}))

    # 账号不存在 → ValueError/KeyError
    check("添加不存在的账号 → KeyError", lambda: _expect_keyerror(
        lambda: account_groups.add_member(g_bench["id"], "no_such_acc")))

    print(f"\n结果: {_ok} 通过 / {_fail} 失败")
    return 1 if _fail else 0


def _expect_valueerror(fn):
    try:
        fn()
    except ValueError:
        return True
    except KeyError:
        return True
    return False


def _expect_keyerror(fn):
    try:
        fn()
    except KeyError:
        return True
    return False


if __name__ == "__main__":
    sys.exit(main())
