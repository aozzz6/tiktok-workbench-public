# -*- coding: utf-8 -*-
"""groups_category.test.py — 大分类（顶级分类）模型测试（隔离临时文件）

层级：大分类 category ← 分组 group ← 账号 account（账号不直接挂大类）
覆盖：
  · 种子：own/bench/inbox 三个内置大类 + 顺序
  · 旧数据迁移：无 categories / 分组无 category_id 的归属推断
  · 计数：group_count / account_count
  · 大类 CRUD：自建可增删改；内置不可改名/删除；有分组不可删
  · 分组换大类：组内账号随组迁移；默认分组固定「未分类」
运行：python tests/groups_category.test.py（webapp/.venv）
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

WEBAPP = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WEBAPP))
import config  # noqa: E402
import groups as account_groups  # noqa: E402

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


def expect(exc, fn, m="未抛异常"):
    try:
        fn()
    except exc:
        return
    except Exception as e:                      # noqa: BLE001
        raise AssertionError(f"{m}：抛了 {e.__class__.__name__}: {e}")
    raise AssertionError(m)


def fresh(tmp: Path, seed: dict | None = None) -> None:
    tmp.mkdir(parents=True, exist_ok=True)
    account_groups.GROUPS_FILE = tmp / "account_groups.json"
    if seed is not None:
        account_groups.GROUPS_FILE.write_text(
            json.dumps(seed, ensure_ascii=False), encoding="utf-8")


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="cat_test_"))
    accs = tmp / "accounts.json"
    accs.write_text(json.dumps({"accounts": [
        {"account": "own_one", "lang": "de", "active": True},
        {"account": "own_two", "lang": "en", "active": True},
        {"account": "rival_one", "lang": "de", "active": True},
    ]}, ensure_ascii=False), encoding="utf-8")
    config.ACCOUNTS_JSON = accs

    # ── 1. 全新文件：种子三分类 ──
    fresh(tmp / "s1")
    d = account_groups.list_groups()
    ids = [c["id"] for c in d["categories"]]
    check("种子：内置三类 own/bench/inbox 且按 order 排序",
          lambda: assert_(ids == ["own", "bench", "inbox"], f"实际 {ids}"))
    check("种子：对标账号组 → 对标账号大类",
          lambda: assert_(next(g["category_id"] for g in d["groups"]
                               if g["name"] == "对标账号") == "bench"))
    check("种子：默认分组 → 未分类大类",
          lambda: assert_(next(g["category_id"] for g in d["groups"]
                               if g["id"] == "default") == "inbox"))

    # ── 2. 旧数据迁移 ──
    fresh(tmp / "s2", {
        "groups": [
            {"id": "default", "name": "默认分组", "builtin": True, "created_at": 1},
            {"id": "g_own", "name": "黄慧账号", "builtin": False, "created_at": 1},
        ],
        "members": [{"account": "own_one", "group_id": "g_own", "joined_at": 1}],
    })
    d2 = account_groups.list_groups()
    check("迁移：补出 categories 且三类齐全",
          lambda: assert_({c["id"] for c in d2["categories"]} >= {"own", "bench", "inbox"}))
    check("迁移：历史真人名分组 → 自有账号",
          lambda: assert_(next(g["category_id"] for g in d2["groups"]
                               if g["id"] == "g_own") == "own"))
    check("迁移：默认分组 → 未分类",
          lambda: assert_(next(g["category_id"] for g in d2["groups"]
                               if g["id"] == "default") == "inbox"))
    check("迁移：落盘后 category_id 持久化",
          lambda: assert_(all("category_id" in g for g in json.loads(
              account_groups.GROUPS_FILE.read_text(encoding="utf-8"))["groups"])))

    # ── 3. 层级归属与计数 ──
    check("层级：账号 → 分组 → 大类（own 命中 1 个账号）",
          lambda: assert_(account_groups.accounts_of_category("own") == ["own_one"]))
    cat_own = next(c for c in account_groups.list_groups()["categories"] if c["id"] == "own")
    check("计数：group_count=1 / account_count=1",
          lambda: assert_((cat_own["group_count"], cat_own["account_count"]) == (1, 1),
                          str(cat_own)))

    # ── 4. 自建大类 + 其下建组 ──
    c = account_groups.create_category("合作账号", "外部合作方账号")
    check("自建大类：默认 builtin=False 且排在未分类之前",
          lambda: assert_(c["builtin"] is False and
                          [x["id"] for x in account_groups.list_groups()["categories"]].index(c["id"])
                          < [x["id"] for x in account_groups.list_groups()["categories"]].index("inbox")))
    check("自建大类：重名 → ValueError",
          lambda: expect(ValueError, lambda: account_groups.create_category("合作账号")))
    check("自建大类：名称 >12 字 → ValueError",
          lambda: expect(ValueError, lambda: account_groups.create_category("字" * 13)))
    g = account_groups.create_group("合作一组", c["id"])
    check("按大类建组：category_id 生效",
          lambda: assert_(g["category_id"] == c["id"]))
    check("按大类建组：list 返回 category_name",
          lambda: assert_(next(x["category_name"] for x in account_groups.list_groups()["groups"]
                               if x["id"] == g["id"]) == "合作账号"))
    check("不存在的大类建组 → KeyError",
          lambda: expect(KeyError, lambda: account_groups.create_group("野组", "nope")))

    # ── 5. 分组换大类：账号随组走 ──
    account_groups.add_member(g["id"], "rival_one")
    check("换大类前：rival_one 不在 own",
          lambda: assert_("rival_one" not in account_groups.accounts_of_category("own")))
    account_groups.set_group_category(g["id"], "own")
    check("换大类后：账号随组迁移到 own",
          lambda: assert_("rival_one" in account_groups.accounts_of_category("own")))
    check("换大类后：原大类计数归零",
          lambda: assert_(next(x["account_count"] for x in account_groups.list_groups()["categories"]
                               if x["id"] == c["id"]) == 0))
    check("默认分组不可换大类 → ValueError",
          lambda: expect(ValueError, lambda: account_groups.set_group_category("default", "own")))
    check("换不存在的大类 → KeyError",
          lambda: expect(KeyError, lambda: account_groups.set_group_category(g["id"], "nope")))

    # ── 6. 大类改 / 删边界 ──
    check("内置大类不可改名 → ValueError",
          lambda: expect(ValueError, lambda: account_groups.update_category("own", name="自有")))
    check("内置大类可改说明",
          lambda: assert_(account_groups.update_category("own", desc="本公司自营")["desc"] == "本公司自营"))
    check("自建大类可改名",
          lambda: assert_(account_groups.update_category(c["id"], name="合作方")["name"] == "合作方"))
    check("内置大类不可删除 → ValueError",
          lambda: expect(ValueError, lambda: account_groups.delete_category("bench")))
    g2 = account_groups.create_group("合作二组", c["id"])
    check("有分组的大类不可删 → ValueError",
          lambda: expect(ValueError, lambda: account_groups.delete_category(c["id"])))
    account_groups.delete_group(g2["id"])
    check("空大类可删除",
          lambda: assert_(account_groups.delete_category(c["id"])["id"] == c["id"]))
    check("删除后 categories 不再包含该大类",
          lambda: assert_(c["id"] not in [x["id"] for x in
                                          account_groups.list_groups()["categories"]]))

    print(f"\n结果: {_ok} 通过 / {_fail} 失败")
    return 0 if _fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
