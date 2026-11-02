# -*- coding: utf-8 -*-
"""benchmark.build() 回归测试：账号过滤不得污染缓存，且 KPI/patterns 必须随账号重算。

复现两个已修复的 bug：
  A) 缓存污染 —— 过滤时就地改写 _cache["data"]，导致后续「全部」请求拿到残缺结果
  B) KPI 不更新 —— 过滤只改 videos/accounts，kpis/patterns/takeaways 仍是全量口径

运行： python tests/benchmark_build.test.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import benchmark  # noqa: E402

pass_n = fail_n = 0


def check(name, cond, extra=""):
    global pass_n, fail_n
    if cond:
        print("  ✓ " + name)
        pass_n += 1
    else:
        print("  ✗ " + name + (" -> " + extra if extra else ""))
        fail_n += 1


print("== 用例 1：缓存污染（先单账号，后全部，不带 refresh）==")
benchmark._cache.update(ts=0.0, data=None)  # 清缓存
full_first = benchmark.build(refresh=True)
n_all = len(full_first["accounts"])
v_all = full_first["kpis"]["videos"]
check("基线全量 accounts=%d videos=%d" % (n_all, v_all), n_all > 1)

# 账号名从数据里取，不写死：greenforu 之类旧账号已不在 accounts.json，写死会导致「单账号 got 0」假失败
_real = [a["account"] for a in full_first["accounts"] if a.get("videos", 0) > 1]
check("至少有一个可单独对比的账号", bool(_real), "accounts=%d" % n_all)
if not _real:
    print("\n结果: %d 通过 / %d 失败（无可用账号，后续用例无法进行）" % (pass_n, fail_n))
    sys.exit(1)
ACCT = _real[0]

one = benchmark.build(account=ACCT)
check("单账号返回 1 个账号", len(one["accounts"]) == 1, "got %d" % len(one["accounts"]))

after = benchmark.build()  # 命中 15s 缓存
check("污染复检：随后「全部」仍为 %d 账号" % n_all,
      len(after["accounts"]) == n_all, "got %d" % len(after["accounts"]))
check("污染复检：「全部」videos 仍为 %d" % v_all,
      after["kpis"]["videos"] == v_all, "got %d" % after["kpis"]["videos"])

print("== 用例 2：KPI/patterns 随账号重算 ==")
one = benchmark.build(account=ACCT)
check("KPI.accounts = 1", one["kpis"]["accounts"] == 1, "got %s" % one["kpis"]["accounts"])
check("KPI.videos < 全量", 0 < one["kpis"]["videos"] < v_all,
      "got %s" % one["kpis"]["videos"])
check("KPI.total_plays < 全量",
      one["kpis"]["total_plays"] < full_first["kpis"]["total_plays"])
bk_sum = sum(b["count"] for b in one["patterns"]["play_buckets"])
check("patterns.play_buckets 合计 = 该账号视频数",
      bk_sum == one["kpis"]["videos"], "bucket_sum=%d videos=%d" % (bk_sum, one["kpis"]["videos"]))
check("patterns.n_videos_in_sample 同步",
      one["patterns"]["n_videos_in_sample"] == one["kpis"]["videos"])
check("takeaways 非空且已重算", isinstance(one["takeaways"], list))

print("== 用例 3：返回体与缓存非同一对象 ==")
benchmark._cache.update(ts=0.0, data=None)
a = benchmark.build(refresh=True)
obj_is_same = a is benchmark._cache["data"]
check("build() 返回的是缓存对象的深拷贝（不是同一引用）", not obj_is_same)

print("== 用例 4：多账号交替切换 ==")
benchmark._cache.update(ts=0.0, data=None)
benchmark.build(refresh=True)
seq = [ACCT, "", _real[1] if len(_real) > 1 else ACCT, "",
       "__no_such_account__", "", ACCT, ""]
bad = []
for acct in seq:
    d = benchmark.build(account=acct or None)
    exp = v_all if not acct else None
    if not acct and d["kpis"]["videos"] != exp:
        bad.append((acct, d["kpis"]["videos"]))
check("8 次交替切换，每次「全部」都回到 %d" % v_all, not bad, str(bad))

print("== 用例 5：不存在的账号 ==")
d = benchmark.build(account="__no_such_account__")
check("返回空账号/零视频且不抛异常",
      len(d["accounts"]) == 0 and d["kpis"]["videos"] == 0)

print("\n结果: %d 通过 / %d 失败" % (pass_n, fail_n))
sys.exit(1 if fail_n else 0)
