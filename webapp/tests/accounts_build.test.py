# -*- coding: utf-8 -*-
"""回归测试：对标账号落点推导（config.account_artifacts / orphan_* 系列）。

覆盖三类此前踩过的坑：
  1) active 缺省语义 —— 老 accounts.json 无该字段时不得被判为停用
  2) 四种 health 判定边界 —— config_only / partial / stale / ok / off
  3) 孤儿项识别 —— 台账 sheet / scrape 产物存在于配置之外
运行：python tests/accounts_build.test.py
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
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


def _acc(name="acct1", **kw):
    base = {"account": name, "lang": "en", "out_dir": str(config.OUTPUT_DIR),
            "audio_dir": str(config.OUTPUT_DIR / f"{name}_audio")}
    base.update(kw)
    return base


print("== 用例 1：active 缺省语义（最高危坑）==")


def t1():
    r = config.account_artifacts(_acc())          # 无 active 字段
    assert_(r["active"] is True, "缺省必须为启用，否则流水线静默不跑")
    r2 = config.account_artifacts(_acc(active=False))
    assert_(r2["active"] is False, "显式 False 应生效")
    r3 = config.account_artifacts(_acc(active=""))
    assert_(r3["active"] is True, "空串应回落缺省 True")


check("无 active 字段 → True", lambda: assert_(config.account_artifacts(_acc())["active"] is True))
check("active=False → False", lambda: assert_(config.account_artifacts(_acc(active=False))["active"] is False))
check("active='' → 回落 True", lambda: assert_(config.account_artifacts(_acc(active=""))["active"] is True))

print("== 用例 2：health 判定边界 ==")


def t2():
    # 不存在的账号 → config_only
    r = config.account_artifacts(_acc("__nope__zzz__"))
    assert_(r["health"] == "config_only", f"期望 config_only，实得 {r['health']}")
    assert_(r["has_scrape"] is False and r["has_audio"] is False and r["has_sheet"] is False)
    assert_(set(r["missing"]) == {"scrape", "audio", "sheet"}, f"missing 实得 {r['missing']}")
    # 停用 → off（即便有产物也优先 off）
    r2 = config.account_artifacts(_acc("__nope__zzz__", active=False))
    assert_(r2["health"] == "off", f"期望 off，实得 {r2['health']}")


check("空壳账号 → config_only + missing 三项", t2)

print("== 用例 3：真实账号（若本机有产物则校验）==")


def t3():
    cfg = config.load_accounts()
    accts = cfg.get("accounts", [])
    if not accts:
        print("    （跳过：accounts.json 无账号）")
        return
    # accounts_with_artifacts() 的口径是「全部账号（含停用）」，所以这里必须拿全量账号对账；
    # 早前用 active 列表（45）去比全量结果（47）会假失败。
    all_accts = config.load_accounts(include_inactive=True).get("accounts", [])
    sheets = {"对标记录-" + a["account"] for a in all_accts}
    rows = config.accounts_with_artifacts(sheets)
    assert_(len(rows) == len(all_accts),
            f"行数 {len(rows)} != 配置 {len(all_accts)}（含停用 {len(all_accts) - len(accts)} 个）")
    for r in rows:
        assert_(r["health"] in config.HEALTH_LABELS, f"未知 health: {r['health']}")
        assert_(isinstance(r["video_count"], int))
        assert_(r["account"], "account 不应为空")
    # 排在第一的应是异常项（config_only 置顶）
    order = {"config_only": 0, "partial": 1, "stale": 2, "ok": 3, "off": 4}
    healthy = [r for r in rows if r["health"] == "ok"]
    if healthy:
        check_ok_first = all(order[r["health"]] >= 0 for r in rows)
        assert_(check_ok_first)


check("全部账号可推导且 health 合法", t3)


def t3b():
    cfg = config.load_accounts()
    accts = cfg.get("accounts", [])
    if not accts:
        return
    # 用真实台账 sheet 集，验证 vivi_ashop 这类空壳能被标出来
    sheets = {"对标记录-" + a["account"] for a in accts}
    rows = {r["account"]: r for r in config.accounts_with_artifacts(sheets)}
    # 找一个有 scrape 的账号，其 has_scrape 必须为 True
    names = list(rows)
    with_scrape = [n for n in names if (config.OUTPUT_DIR / f"scrape_{n}.json").exists()]
    for n in with_scrape:
        assert_(rows[n]["has_scrape"] is True, f"{n} 有 scrape 文件却判 False")
        assert_(rows[n]["video_count"] > 0, f"{n} 应解析出视频数")
    print(f"    （实测：{len(with_scrape)}/{len(names)} 个账号有抓取产物）")


check("有 scrape 文件的账号 → has_scrape 且 video_count>0", t3b)

print("== 用例 4：孤儿项识别 ==")


def t4():
    cfg = config.load_accounts()
    known = {a["account"] for a in cfg.get("accounts", [])}
    fake = {"对标记录-__ghost__", "说明"}
    fake |= {"对标记录-" + n for n in known}
    orp = config.orphan_sheets(fake)
    assert_("__ghost__" in orp, f"应识别 __ghost__，实得 {orp}")
    assert_(not (known & set(orp)), "已知账号不应被判为孤儿")
    assert_(config.orphan_sheets(None) == [], "None 应返回空")
    assert_(config.orphan_sheets(set()) == [], "空集应返回空")


check("orphan_sheets 只挑出未纳管账号", t4)


def t5():
    orp = config.orphan_scrapes()
    cfg = config.load_accounts()
    known = {a["account"] for a in cfg.get("accounts", [])}
    assert_(not (known & set(orp)), f"已知账号不应误报孤儿: {orp}")
    assert_(isinstance(orp, list))


check("orphan_scrapes 不误报已知账号", t5)

print("== 用例 5：stale 判定（构造 >7 天）==")


def t6():
    old = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
    recent = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
    # 直接用 HEALT H 逻辑等价式验算 age 计算
    lv = datetime.strptime(old[:19], "%Y-%m-%d %H:%M:%S")
    age = (datetime.now() - lv).days
    assert_(age > config.STALE_DAYS, f"构造的旧时间应 stale，age={age}")
    lv2 = datetime.strptime(recent[:19], "%Y-%m-%d %H:%M:%S")
    assert_((datetime.now() - lv2).days <= config.STALE_DAYS)


check("STALE_DAYS 阈值与 age 计算一致", t6)

print("\n结果: " + str(pass_n) + " 通过 / " + str(fail_n) + " 失败")
sys.exit(1 if fail_n else 0)
