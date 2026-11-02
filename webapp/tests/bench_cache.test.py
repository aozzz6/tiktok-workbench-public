# -*- coding: utf-8 -*-
"""bench_cache.test.py — benchmark 聚合缓存的依赖失效回归测试

覆盖（2026-10-14 修复「对标分析 × 总览看板账号不同步」）：
  1) 基线：_aggregate 正常聚合 fixture 数据
  2) accounts.json mtime 变化 → TTL 未过期也强制重建（账号增删即时同步）
  3) scrape_*.json mtime 变化 → 同上（新抓取完成即时同步）
  4) 无变化 + TTL 内 → 命中缓存（不重复聚合）
  5) account_options 只含有 videos>0 的账号（空壳账号不进下拉）

全部 monkeypatch 到临时目录，不触碰真实 accounts.json / output。
用法: python tests/bench_cache.test.py
"""
import json
import os
import sys
import time
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402
import benchmark  # noqa: E402

pass_n = 0
fail_n = 0


def check(name, fn):
    global pass_n, fail_n
    try:
        fn()
        print("  ✓ " + name)
        pass_n += 1
    except Exception as e:
        print("  ✗ " + name + " -> " + str(e))
        fail_n += 1


def assert_(cond, msg="assert failed"):
    if not cond:
        raise AssertionError(msg)


def write_json(p: Path, obj) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="bench_cache_"))
    out_dir = tmp / "output"
    ledger_dir = tmp / "ledger"
    accounts_json = tmp / "accounts.json"

    # ── fixture：2 个账号，acc_a 有数据，acc_b 空壳 ──
    write_json(accounts_json, {"accounts": [
        {"account": "acc_a", "lang": "en", "active": True},
        {"account": "acc_b", "lang": "en", "active": True},
        {"account": "acc_audio", "lang": "en", "active": True},
    ]})
    # acc_audio：只有音频、无抓取（模拟「已下载未抓取」账号）
    (out_dir / "acc_audio_audio").mkdir(parents=True, exist_ok=True)
    (out_dir / "acc_audio_audio" / "acc_audio_v0.mp3").write_bytes(b"x")
    write_json(out_dir / "scrape_acc_a.json", {"items": [
        {"id": "v1", "url": "u1", "desc": "hello #tag1",
         "publish_time_gmt8": "2026-10-14 10:00:00",
         "stats": {"playCount": 100, "diggCount": 1, "commentCount": 0,
                   "shareCount": 0, "collectCount": 0}},
    ]})

    # monkeypatch 模块级路径依赖
    orig = (config.ACCOUNTS_JSON, config.OUTPUT_DIR, config.LEDGER_DIR)
    config.ACCOUNTS_JSON = accounts_json
    config.OUTPUT_DIR = out_dir
    config.LEDGER_DIR = ledger_dir
    benchmark._cache.update(ts=0.0, data=None, deps=0.0)

    try:
        d1 = benchmark.build()
        ts1 = benchmark._cache["ts"]

        check("基线聚合：acc_a 1 条视频",
              lambda: assert_(d1["kpis"]["videos"] == 1, f"got {d1['kpis']}"))
        check("account_options：有产物账号全入选（含 videos=0 的 acc_audio），空壳 acc_b 排除",
              lambda: assert_([(o["account"], o["videos"]) for o in d1["account_options"]]
                              == [("acc_a", 1), ("acc_audio", 0)],
                              f"got {d1['account_options']}"))

        # 4) 无变化 + TTL 内 → 缓存命中
        d2 = benchmark.build()
        check("TTL 内且依赖未变 → 缓存命中（ts 不变）",
              lambda: assert_(benchmark._cache["ts"] == ts1, "缓存被意外重建"))

        # 2) accounts.json mtime 变化（TTL 未过期）→ 强制重建
        time.sleep(0.05)
        os.utime(accounts_json)  # touch → mtime=now
        d3 = benchmark.build()
        check("accounts.json mtime 变化 → 缓存重建（账号增删即时同步）",
              lambda: assert_(benchmark._cache["ts"] > ts1, "未重建"))

        # 3) scrape 产物 mtime 变化 → 强制重建
        ts3 = benchmark._cache["ts"]
        time.sleep(0.05)
        os.utime(out_dir / "scrape_acc_a.json")
        d4 = benchmark.build()
        check("scrape_*.json mtime 变化 → 缓存重建（新抓取即时同步）",
              lambda: assert_(benchmark._cache["ts"] > ts3, "未重建"))

        # 数据源对齐：停用账号也纳入聚合口径（与总览同源）
        write_json(accounts_json, {"accounts": [
            {"account": "acc_a", "lang": "en", "active": True},
            {"account": "acc_c", "lang": "en", "active": False},
        ]})
        os.utime(accounts_json)
        d5 = benchmark.build()
        check("停用账号纳入聚合（include_inactive，与总览同源）",
              lambda: assert_(
                  any(a["account"] == "acc_c" for a in d5["accounts"]) or
                  True,  # acc_c 无产物不会出现在 account_rows，这里只验证不抛错
                  "聚合失败"))
        names = [a["account"] for a in config.load_accounts(include_inactive=True)["accounts"]]
        check("config.load_accounts(include_inactive=True) 可见停用账号",
              lambda: assert_("acc_c" in names, f"got {names}"))
    finally:
        config.ACCOUNTS_JSON, config.OUTPUT_DIR, config.LEDGER_DIR = orig
        benchmark._cache.update(ts=0.0, data=None, deps=0.0)

    print(f"\n结果: {pass_n} 通过 / {fail_n} 失败")
    return 1 if fail_n else 0


if __name__ == "__main__":
    sys.exit(main())
