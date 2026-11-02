# -*- coding: utf-8 -*-
"""「被挂车最多的商品」(patterns.products_top) 聚合回归测试。

验证目标：
  A) 归并键从「标题前缀」升级为 product_id 后，条目必须带出可直接跳转的字段
     （product_id / fastmoss_url / accounts / videos / total_plays / anchor_type）；
  B) 计数与播放合计必须与「独立复算」一致 —— 不复用 benchmark 内部聚合函数，
     而是直接读 scrape_<acc>.json + <acc>_products.json 自己统计；
  C) 排序、占比、URL 模板、关联视频截断等口径正确。

运行： python tests/bench_products_build.test.py
"""
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import benchmark  # noqa: E402
import config  # noqa: E402

_FASTMOSS = "https://www.fastmoss.com/e-commerce/detail/{}"

pass_n = fail_n = 0


def check(name, cond, extra=""):
    global pass_n, fail_n
    if cond:
        print("  ✓ " + name)
        pass_n += 1
    else:
        print("  ✗ " + name + (" -> " + extra if extra else ""))
        fail_n += 1


def _n(v):
    """与产出口径一致的数值化（stats 里可能是字符串）。"""
    if v is None:
        return 0
    try:
        return int(float(v))
    except (ValueError, TypeError):
        return 0


def raw_recount(account: str) -> tuple[Counter, Counter]:
    """独立复算：account → (product_id → 挂车视频数, product_id → 挂车视频播放合计)。

    只按 product_id 归并，用于与产物逐项比对；不依赖 benchmark 的任何内部函数。
    """
    cnt, plays = Counter(), Counter()
    scrape_p = config.OUTPUT_DIR / f"scrape_{account}.json"
    prod_p = config.LEDGER_DIR / account / f"{account}_products.json"
    if not scrape_p.exists() or not prod_p.exists():
        return cnt, plays

    items = (json.loads(scrape_p.read_text(encoding="utf-8")) or {}).get("items") or []
    pmap = json.loads(prod_p.read_text(encoding="utf-8")) or {}
    pmap = {str(k): (v if isinstance(v, list) else [v]) for k, v in pmap.items()}

    seen = set()
    for it in items:
        vid = str(it.get("id") or it.get("video_id") or "")
        if vid and vid in seen:
            continue
        if vid:
            seen.add(vid)
        pl = _n((it.get("stats") or {}).get("playCount"))
        for p in pmap.get(vid, []):
            if not isinstance(p, dict):
                continue
            pid = str(p.get("product_id") or "").strip()
            if not pid:
                continue
            cnt[pid] += 1
            plays[pid] += pl
    return cnt, plays


def pick_accounts(limit: int = 6) -> list[str]:
    """挑出「既有 scrape 又有 products」的真实账号。"""
    out = []
    for f in sorted(config.LEDGER_DIR.glob("*/*_products.json")):
        acc = f.parent.name
        if (config.OUTPUT_DIR / f"scrape_{acc}.json").exists():
            out.append(acc)
        if len(out) >= limit:
            break
    return out


accounts = pick_accounts()
print("== 用例 1：数据前提 ==")
check("找到至少 1 个同时具备 scrape + products 的账号", len(accounts) > 0, str(accounts))

print("== 用例 2：products_top 字段完整性 ==")
benchmark._cache.update(ts=0.0, data=None)
full = benchmark.build(refresh=True)
tops = full["patterns"]["products_top"]
check("全局 products_top 非空", len(tops) > 0, "n=%d" % len(tops))
check("条目数 ≤ 10", len(tops) <= 10, "n=%d" % len(tops))

required = {"product_id", "title", "fastmoss_url", "vids", "pct",
            "accounts", "videos", "total_plays", "anchor_type"}
missing = []
for t in tops:
    miss = required - set(t)
    if miss:
        missing.append((t.get("product_id"), sorted(miss)))
check("每条都含全部约定字段", not missing, str(missing[:3]))
check("旧字段 title/vids/pct 仍在（向后兼容 takeaways）",
      all(isinstance(t.get("title"), str) and isinstance(t.get("vids"), int) for t in tops))

bad_url = [t["product_id"] for t in tops
           if t["product_id"] and t["fastmoss_url"] != _FASTMOSS.format(t["product_id"])]
check("fastmoss_url 全部符合模板", not bad_url, str(bad_url[:3]))
check("有 product_id 的条目一定有 URL",
      all(t["fastmoss_url"] for t in tops if t["product_id"]))
check("无 product_id 的条目 URL 为空（不臆造）",
      all(not t["fastmoss_url"] for t in tops if not t["product_id"]))

bad_pct = [t["product_id"] for t in tops
           if t["pct"] > 100 or t["pct"] < 0]
check("pct 落在 0–100", not bad_pct, str(bad_pct[:3]))

seq = [t["vids"] for t in tops]
check("按挂车视频数降序", seq == sorted(seq, reverse=True), str(seq))
check("每条 accounts 非空", all(len(t["accounts"]) >= 1 for t in tops))
check("每条 videos 非空且 ≤ 5", all(1 <= len(t["videos"]) <= 5 for t in tops))
check("videos 按播放降序",
      all([v["plays"] for v in t["videos"]] == sorted([v["plays"] for v in t["videos"]], reverse=True)
          for t in tops))
check("videos 元素字段齐全",
      all({"account", "vid", "title", "plays", "url", "publish_time"} <= set(v)
          for t in tops for v in t["videos"]))

print("== 用例 3：与独立复算逐项比对（多账号）==")
for acc in accounts:
    cnt, plays = raw_recount(acc)
    if not cnt:
        check("[%s] 原始数据无可归并商品（跳过比对）" % acc, True)
        continue
    d = benchmark.build(account=acc)
    got = {t["product_id"]: t for t in d["patterns"]["products_top"] if t["product_id"]}
    # 排序契约：先按挂车视频数降序，同数再按挂车视频播放合计降序（保证确定性，
    # 否则并排项的顺序取决于 dict 插入顺序，跨运行不稳定）。
    ranked = sorted(cnt.keys(), key=lambda pid: (-cnt[pid], -plays[pid]))
    top_ids = ranked[: benchmark._PRODUCTS_TOP_N]
    mism = []
    for pid in top_ids:
        t = got.get(pid)
        if t is None:
            mism.append((pid, "缺失"))
            continue
        if t["vids"] != cnt[pid]:
            mism.append((pid, "vids %s≠%s" % (t["vids"], cnt[pid])))
        elif t["total_plays"] != plays[pid]:
            mism.append((pid, "plays %s≠%s" % (t["total_plays"], plays[pid])))
    check("[%s] TOP%d 商品的 vids/total_plays 与独立复算一致" % (acc, len(top_ids)),
          not mism, str(mism[:3]))
    check("[%s] 返回条目数 = min(10, 不同商品数)" % acc,
          len(got) == min(benchmark._PRODUCTS_TOP_N, len(cnt)),
          "got %d / distinct %d" % (len(got), len(cnt)))
    # 边界不变量：入选项的 (vids, plays) 不得低于落选项
    if len(ranked) > benchmark._PRODUCTS_TOP_N:
        last_in = (cnt[top_ids[-1]], plays[top_ids[-1]])
        first_out = max((cnt[p], plays[p]) for p in ranked[benchmark._PRODUCTS_TOP_N:])
        check("[%s] 入选边界 ≥ 落选边界（无高挂车商品被漏掉）" % acc,
              last_in >= first_out, "%s < %s" % (last_in, first_out))
    check("[%s] 返回条目均属该账号口径" % acc,
          all(acc in t["accounts"] for t in d["patterns"]["products_top"]))

print("== 用例 4：账号维度隔离 ==")
if len(accounts) >= 2:
    a1, a2 = accounts[0], accounts[1]
    p1 = [t["product_id"] for t in benchmark.build(account=a1)["patterns"]["products_top"]]
    p2 = [t["product_id"] for t in benchmark.build(account=a2)["patterns"]["products_top"]]
    check("单账号 products_top 均为该账号商品（不串号）",
          all(a1 in t["accounts"] for t in benchmark.build(account=a1)["patterns"]["products_top"]))
    check("两账号结果不完全相同（确有隔离）", p1 != p2 or not p1, "%s vs %s" % (p1[:2], p2[:2]))
else:
    check("账号数不足 2，跳过隔离用例", True)

print("== 用例 5：缓存不被污染（products_top 也随之重算）==")
benchmark._cache.update(ts=0.0, data=None)
full = benchmark.build(refresh=True)
n_full = len(full["patterns"]["products_top"])
one = benchmark.build(account=accounts[0])
n_one = len(one["patterns"]["products_top"])
back = benchmark.build()
check("全局条目数 %d，单账号 %d" % (n_full, n_one), n_one <= n_full)
check("随后「全部」仍为 %d 条" % n_full,
      len(back["patterns"]["products_top"]) == n_full,
      "got %d" % len(back["patterns"]["products_top"]))

print("\n结果: %d 通过 / %d 失败" % (pass_n, fail_n))
sys.exit(1 if fail_n else 0)
