"""API boundary checks without starting a process or writing production data."""
import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _isolate  # noqa: F401  —— 运行 journal 落到临时文件，别污染生产 runs.jsonl
import app
import runner
from fastapi import HTTPException


class ApiAcceptanceTests(unittest.TestCase):
    def test_completed_sse_has_done_marker(self):
        run = runner.Run(id='acceptance-fixture',kind='fixture',title='fixture',cmd=[],cwd='.',status='done',exit_code=0)
        async def consume():
            return [part async for part in app.stream_run(run.id).body_iterator]
        with patch.dict(runner.MANAGER._runs,{run.id:run}):
            messages=asyncio.run(consume())
        status=json.loads(messages[-1].strip()[6:])
        self.assertTrue(status['done'])
        self.assertEqual(status['status'],'done')

    def test_bad_run_arguments_are_rejected_before_start(self):
        base={'account':'probe','date_from':'2026-10-10','date_to':'2026-10-16'}
        cases=[{'account':123},{'date_from':''},{'date_from':'2026-10-17'},
               {'date_to':'not a date'},{'backend':'invalid'},
               {'steps':['misspelled_step']},{'steps':'s1_download'}]
        with patch.object(runner.MANAGER,'start') as start:
            for bad in cases:
                with self.subTest(bad=bad), self.assertRaises(HTTPException) as exc:
                    app.start_run(dict(base,**bad))
                self.assertEqual(exc.exception.status_code,400)
        start.assert_not_called()

    def test_valid_run_arguments_build_same_stack_command(self):
        cmd=app._build_pipeline_cmd({'account':'probe','date_from':'2026-10-10','date_to':'2026-10-16',
                                     'lang':'en','backend':'auto','steps':['s5_transcribe']})
        self.assertIn('s5_transcribe',cmd)
        self.assertIn('s5b_products',cmd)
        self.assertIn('auto',cmd)

    def test_failed_product_entries_are_not_counted_complete(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            output=root/'output'
            output.mkdir()
            (output/'scrape_probe.json').write_text(json.dumps({'items':[
                {'id':'1111111111111111111'},{'id':'2222222222222222222'}]}),encoding='utf-8')
            product_file=root/'probe'/'probe_products.json'
            product_file.parent.mkdir()
            product_file.write_text(json.dumps({'1111111111111111111':[],
                                                '2222222222222222222':None}),encoding='utf-8')
            with patch.object(app.config,'OUTPUT_DIR',output),patch.object(app,'_aux_dir',return_value=root):
                view=app._account_view({'account':'probe'},{})
            # ★ 2026-10-24（D4）：口径拆成两个数，本用例的原始意图（失败的提取条目
            #   不算"完整"）由 products_probed 承载：
            #     2222 的值是 None（提取失败）→ 不计入 probed；1111 是 []（提取成功但没挂车）
            #     → 计入 probed、不计入 products_count。
            self.assertEqual(view['products_probed'],1)      # 只有 1111 算提取过
            self.assertEqual(view['products_count'],0)       # 两条都没有真商品
            self.assertTrue(any(g['level']=='warn' and g['label']=='商品' for g in view['gaps']))


if __name__=='__main__':
    unittest.main(verbosity=2)
