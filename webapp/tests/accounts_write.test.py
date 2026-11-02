# -*- coding: utf-8 -*-
"""回归测试：账号写操作（二期）+ 停用过滤。

关键保障：
  1) 原子写 —— 不留 .tmp 残file、JSON 合法、非目标行不变
  2) 主键只读 —— update 传 account 不得改主键
  3) 校验 —— 格式 / 长度 / Excel 非法字符 / 重名
  4) 停用过滤 —— load_accounts() 剔除、raw 保留
  5) 删除保护 —— 有产物的账号不可删
运行：python tests/accounts_write.test.py
注意：测试会真实读写 accounts.json，但结束时恢复原样（含字节级校验）。
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402

pass_n = 0
fail_n = 0


def check(name, fn):
    global pass_n, fail_n
    try:
        fn()
        print("  \u2713 " + name)
        pass_n += 1
    except Exception as e:  # noqa: BLE001
        print("  \u2717 " + name + " -> " + repr(e))
        fail_n += 1


def assert_(cond, msg=""):
    if not cond:
        raise AssertionError(msg or "assertion failed")


def expect_raise(fn, kind=ValueError, contains=None):
    try:
        fn()
    except kind as e:
        if contains and contains not in str(e):
            raise AssertionError(f"异常信息不含 {contains!r}: {e}")
        return
    except Exception as e:  # noqa: BLE001
        raise AssertionError(f"异常类型不符，实得 {type(e).__name__}: {e}")
    raise AssertionError("未抛异常")


BAK = config.ACCOUNTS_JSON.with_suffix(".json.testbak")
shutil.copy2(config.ACCOUNTS_JSON, BAK)
orig_text = config.ACCOUNTS_JSON.read_text(encoding="utf-8")
TEST_ACC = "__zz_test_acct__"

try:
    print("== 用例 1：新增账号 ==")

    def t1():
        r = config.add_account(TEST_ACC, {"lang": "en", "nickname": "测试", "tags": "a,b"})
        assert_(r["account"] == TEST_ACC)
        assert_(r["tags"] == ["a", "b"], f"tags 应被拆分，实得 {r['tags']}")
        assert_(r["audio_dir"].endswith(f"{TEST_ACC}_audio"), "audio_dir 应含账号名")
        assert_(r["active"] is True, "新增缺省启用")
        raw = config.load_accounts_raw()
        assert_(any(a["account"] == TEST_ACC for a in raw["accounts"]), "应已落盘")

    check("新增写入并落盘", t1)

    def t1b():
        raw = config.load_accounts_raw()
        row = next(a for a in raw["accounts"] if a["account"] == TEST_ACC)
        assert_("account" in row and "lang" in row and "out_dir" in row)
        # 顶层非账号字段不应被破坏
        assert_(raw.get("xlsx"), "顶层 xlsx 字段丢失")
        assert_(raw.get("reports_root"), "顶层 reports_root 字段丢失")

    check("顶层字段与其他账号不受影响", t1b)

    def t1c():
        assert_(not config.ACCOUNTS_JSON.with_suffix(".json.tmp").exists(),
                "不应残留 .tmp 文件（原子写应已 replace）")
        json.loads(config.ACCOUNTS_JSON.read_text(encoding="utf-8"))

    check("无 .tmp 残留且 JSON 合法", t1c)

    print("== 用例 2：重名与校验 ==")

    def t2():
        expect_raise(lambda: config.add_account(TEST_ACC), contains="已存在")

    check("重名被拦截", t2)

    def t2b():
        for bad in ["", "  ", "Bad-Name", "A B", "a/b", "x" * 40, "中文账号", "a*b"]:
            expect_raise(lambda b=bad: config.add_account(b))
        # 合法形式
        for good in ["a", "a1", "a.b", "a_b", "a.b_c1"]:
            config.validate_account_name(good)

    check("非法名全部拦截 / 合法名放行", t2b)

    print("== 用例 3：编辑（主键只读）==")

    def t3():
        r = config.update_account(TEST_ACC, {"account": "HACKED", "nickname": "改过"})
        assert_(r["account"] == TEST_ACC, "主键不应被改写")
        assert_(r["nickname"] == "改过", "nickname 应已更新")
        raw = config.load_accounts_raw()
        assert_(not any(a["account"] == "HACKED" for a in raw["accounts"]), "不得产生 HACKED")

    check("account 主键只读 + 其他字段可改", t3)

    def t3b():
        r = config.update_account(TEST_ACC, {"tags": ["x", "y"]})
        assert_(r["tags"] == ["x", "y"])
        r2 = config.update_account(TEST_ACC, {"tags": "p, q"})
        assert_(r2["tags"] == ["p", "q"], f"字符串 tags 应拆分，实得 {r2['tags']}")

    check("tags 字符串与数组均归一", t3b)

    def t3c():
        expect_raise(lambda: config.update_account("__no_such__", {"note": "x"}),
                     contains="不存在")

    check("改不存在的账号报错", t3c)

    print("== 用例 4：停用过滤 ==")

    def t4():
        before_enabled = {a["account"] for a in config.load_accounts()["accounts"]}
        config.set_account_active(TEST_ACC, False)
        raw = config.load_accounts_raw()
        en = config.load_accounts()
        enabled_names = {a["account"] for a in en["accounts"]}
        assert_(any(a["account"] == TEST_ACC for a in raw["accounts"]), "raw 应保留停用账号")
        assert_(not any(a["account"] == TEST_ACC for a in en["accounts"]),
                "过滤后不应含停用账号")
        assert_(enabled_names == before_enabled - {TEST_ACC},
                "停用只应从启用列表移除测试账号，不应影响其他账号")
        # include_inactive 显式取全
        allc = config.load_accounts(include_inactive=True)
        assert_(any(a["account"] == TEST_ACC for a in allc["accounts"]), "显式取全应含停用")

    check("停用后 load_accounts 剔除、raw 保留", t4)

    def t4b():
        config.set_account_active(TEST_ACC, True)
        en = config.load_accounts()
        assert_(any(a["account"] == TEST_ACC for a in en["accounts"]), "重新启用应回到过滤结果")

    check("重新启用后回到执行链", t4b)

    def t4c():
        # 老数据无 active 字段时不得被误滤
        cfg = config.load_accounts_raw()
        rows = [a for a in cfg["accounts"] if "active" not in a]
        kept = {a["account"] for a in config.load_accounts()["accounts"]}
        for a in rows:
            assert_(a["account"] in kept, f"{a['account']} 无 active 字段却被过滤")

    check("无 active 字段的老账号不被误滤", t4c)

    print("== 用例 5：删除保护 ==")

    def t5():
        # 真实账号有产物 → 拒绝删除
        real = [a["account"] for a in config.load_accounts_raw()["accounts"]
                if a["account"] != TEST_ACC]
        with_art = None
        for n in real:
            row = next(a for a in config.load_accounts_raw()["accounts"] if a["account"] == n)
            art = config.account_artifacts(row)
            if art["has_scrape"] or art["has_audio"] or art["has_sheet"]:
                with_art = n
                break
        if with_art:
            expect_raise(lambda: config.remove_account(with_art), contains="停用")
            assert_(any(a["account"] == with_art
                        for a in config.load_accounts_raw()["accounts"]), "有产物账号不应被删")
        else:
            print("    （跳过：本机无有产物账号）")

    check("有产物的账号禁止删除", t5)

    def t5b():
        expect_raise(lambda: config.remove_account("__no_such__"), contains="不存在")

    check("删不存在的账号报错", t5b)

    def t5c():
        config.remove_account(TEST_ACC)
        assert_(not any(a["account"] == TEST_ACC
                        for a in config.load_accounts_raw()["accounts"]), "应已删除")

    check("空壳账号可删除", t5c)

finally:
    # 恢复原文件，并做字节级校验
    shutil.copy2(BAK, config.ACCOUNTS_JSON)
    BAK.unlink(missing_ok=True)
    restored = config.ACCOUNTS_JSON.read_text(encoding="utf-8")
    same = restored == orig_text
    print(("\n  \u2713 " if same else "\n  \u2717 ") + "accounts.json 已按原样恢复（字节级一致）")
    if same:
        pass_n += 1
    else:
        fail_n += 1

print("\n结果: " + str(pass_n) + " 通过 / " + str(fail_n) + " 失败")
sys.exit(1 if fail_n else 0)
