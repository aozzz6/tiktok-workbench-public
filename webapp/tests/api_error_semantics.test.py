# -*- coding: utf-8 -*-
"""api_error_semantics.test.py — P2-5 404 文案去引号 + P2-6 ai/test 模型校验（T-03）

背景（2026-11-01 体检 5.1 节）
    P2-5：分组/分类写接口把 `str(KeyError)` 直接当 404 文案。KeyError 的 repr 自带首尾
          单引号，客户端看到 `'大分类不存在：x'`（多一对引号）。实测
          `DELETE /api/categories/16262b7e43` → `{"detail":"'大分类不存在：16262b7e43'"}`。
    P2-6：`POST /api/ai/test {"model": "不存在的模型"}` → 200，且实际探测的是**默认模型**
          （llm.chat 的候选链找不到显式模型时回退到清单里的第一个），返回体里的 model
          变成默认那个 —— 用户以为自己测的是指定模型。

本用例覆盖 T-03 DoD 1-3
    1. 5 个写接口（PATCH/DELETE /api/groups/{gid}、PATCH/DELETE /api/categories/{cid}、
       POST /api/groups/{gid}/members）的 404 detail 精确等于中文文案，无首尾单引号。
    2. ai/test 传不存在的模型 → 400「模型不存在：zzz_not_exist」，且 llm.probe 未被调用。
    3. 合法模型 → 行为不变（仍以同名模型调用 probe）；不传模型 → 仍走默认（probe(None)）。
       另测 llm.probe 自身对显式未知模型抛 LLMError（在发请求之前，不联网）。

隔离
    `import _isolate`（运行记录落临时文件）；groups.GROUPS_FILE 与 llm.MODELS_JSON 都指到
    临时目录；config.load_accounts 用桩账号表；llm.probe / llm.chat 全部 patch。
    不读生产 data/，不联网，不写任何生产文件。
运行：webapp/.venv/Scripts/python.exe tests/api_error_semantics.test.py
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

WEBAPP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WEBAPP))
import _isolate  # noqa: F401  运行 journal 落到临时文件，不污染生产 runs.jsonl
import app  # noqa: E402
import config  # noqa: E402
import groups  # noqa: E402
import llm  # noqa: E402
from fastapi import HTTPException  # noqa: E402

UNKNOWN = "zzz_not_exist"
MODEL_OK = "model_ok"


class ApiErrorSemanticsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

        # 分组数据指到临时文件（不存在 = 空骨架），绝不读/写生产 data/account_groups.json
        p = patch.object(groups, "GROUPS_FILE", self.root / "account_groups.json")
        p.start()
        self.addCleanup(p.stop)

        # add_member 先查账号是否在 accounts.json 里；用桩账号表，不读生产
        p = patch.object(config, "load_accounts",
                         lambda *a, **k: {"accounts": [{"account": "acc_x"}]})
        p.start()
        self.addCleanup(p.stop)

        # 模型清单指到临时文件：一个合法模型 model_ok（url+key 齐全，_raw_models 才会保留）
        self.models = self.root / "models.json"
        self.models.write_text(json.dumps({"models": [
            {"id": MODEL_OK, "name": "模型OK", "url": "http://127.0.0.1:9/v1", "apiKey": "k"},
        ]}, ensure_ascii=False), encoding="utf-8")
        p = patch.object(llm, "MODELS_JSON", self.models)
        p.start()
        self.addCleanup(p.stop)

    def _not_found_detail(self, fn, *args) -> str:
        with self.assertRaises(HTTPException) as ctx:
            fn(*args)
        self.assertEqual(ctx.exception.status_code, 404)
        return ctx.exception.detail

    # ── DoD 1：5 处 404 文案无单引号 ────────────────────────────
    def test_patch_group_not_found_message_has_no_quotes(self):
        d = self._not_found_detail(app.groups_update, "nope_gid", {"name": "x"})
        self.assertEqual(d, "分组不存在：nope_gid")

    def test_delete_group_not_found_message_has_no_quotes(self):
        d = self._not_found_detail(app.groups_delete, "nope_gid")
        self.assertEqual(d, "分组不存在：nope_gid")

    def test_add_member_group_not_found_message_has_no_quotes(self):
        d = self._not_found_detail(app.groups_add_member, "nope_gid", {"account": "acc_x"})
        self.assertEqual(d, "分组不存在：nope_gid")

    def test_patch_category_not_found_message_has_no_quotes(self):
        d = self._not_found_detail(app.categories_update, "nope_cid", {"name": "x"})
        self.assertEqual(d, "大分类不存在：nope_cid")

    def test_delete_category_not_found_message_has_no_quotes(self):
        # 体检实测的那一条：DELETE /api/categories/16262b7e43
        d = self._not_found_detail(app.categories_delete, "nope_cid")
        self.assertEqual(d, "大分类不存在：nope_cid")

    # ── DoD 1 扩展（2026-11-02，ADR 0002 / CRD 0002 选 B）────────
    # 同一类的另外 4 处也走 _key_msg：同类文案不再有一处带引号
    def test_create_group_bad_category_message_has_no_quotes(self):
        # POST /api/groups，category_id 不存在 → create_group 抛 KeyError
        d = self._not_found_detail(app.groups_create,
                                   {"name": "临时新组", "category_id": "nope_cid"})
        self.assertEqual(d, "大分类不存在：nope_cid")

    def test_set_group_category_not_found_message_has_no_quotes(self):
        # PATCH /api/groups/{gid}/category，分组不存在
        d = self._not_found_detail(app.groups_set_category, "nope_gid",
                                   {"category_id": "nope_cid"})
        self.assertEqual(d, "分组不存在：nope_gid")

    def test_transfer_to_missing_group_message_has_no_quotes(self):
        # POST /api/groups/{gid}/transfer，目标分组不存在
        d = self._not_found_detail(app.groups_transfer, "nope_gid",
                                   {"accounts": ["acc_x"]})
        self.assertEqual(d, "分组不存在：nope_gid")

    def test_remove_member_missing_group_message_has_no_quotes(self):
        # DELETE /api/groups/{gid}/members/{account}，分组不存在
        d = self._not_found_detail(app.groups_remove_member, "nope_gid", "acc_x")
        self.assertEqual(d, "分组不存在：nope_gid")

    # ── DoD 2：不存在的模型 → 400，且不探测（更不会去测默认模型）────
    def test_ai_test_unknown_model_is_400_and_never_probes(self):
        with patch.object(llm, "probe") as probe:
            with self.assertRaises(HTTPException) as ctx:
                app.ai_test({"model": UNKNOWN})
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertEqual(ctx.exception.detail, f"模型不存在：{UNKNOWN}")
        probe.assert_not_called()

    # ── DoD 3：合法模型 / 不传模型 → 行为不变 ────────────────────
    def test_ai_test_known_model_still_probes_the_same_model(self):
        canned = {"ok": True, "model": MODEL_OK, "json_ok": True, "latency_ms": 7}
        with patch.object(llm, "probe", return_value=canned) as probe:
            self.assertEqual(app.ai_test({"model": MODEL_OK}), canned)
        probe.assert_called_once()
        self.assertEqual(probe.call_args.args, (MODEL_OK,))

    def test_ai_test_without_model_keeps_default_path(self):
        canned = {"ok": False, "model": "default", "error": "offline"}
        with patch.object(llm, "probe", return_value=canned) as probe:
            self.assertEqual(app.ai_test({}), canned)
        probe.assert_called_once()
        self.assertEqual(probe.call_args.args, (None,))

    # ── llm 层：显式未知模型在发请求之前就报错 ───────────────────
    def test_llm_probe_unknown_model_raises_without_network(self):
        with patch.object(llm, "chat", side_effect=llm.LLMError("no network")) as chat_mock:
            with self.assertRaises(llm.LLMError) as ctx:
                llm.probe(UNKNOWN)
        self.assertEqual(str(ctx.exception), f"模型不存在：{UNKNOWN}")
        chat_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
