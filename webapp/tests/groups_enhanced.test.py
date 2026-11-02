"""分组增强审查回归：临时文件、模拟任务，不修改业务数据。"""
import datetime as dt
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app
import benchmark
import config
import groups
import scheduler
from fastapi import HTTPException


def _LocalRequest(host="127.0.0.1"):
    """最小 Request 替身：`_guard_local_write` 只读 request.client.host。"""
    return Mock(client=Mock(host=host))


class GroupEnhancementTests(unittest.TestCase):
    def test_corrupt_storage_preserved_for_reads_and_writes(self):
        for text in ('{broken', '{"groups":null,"members":[]}', '[]'):
            with self.subTest(text=text), tempfile.TemporaryDirectory() as td:
                f = Path(td) / 'groups.json'
                f.write_text(text, encoding='utf-8')
                with patch.object(groups, 'GROUPS_FILE', f):
                    for action in (groups.list_groups, lambda: groups.create_group('x')):
                        with self.assertRaises(groups.GroupStorageError):
                            action()
                    self.assertEqual(f.read_text(encoding='utf-8'), text)

    def test_default_members_and_read_without_rewrite(self):
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / 'groups.json'
            with patch.object(groups, 'GROUPS_FILE', f), patch.object(config, 'load_accounts', return_value={'accounts':[{'account':'alpha'}]}):
                groups.list_groups()
                before = f.stat().st_mtime_ns
                self.assertEqual(groups.members_of('default'), ['alpha'])
                self.assertEqual(f.stat().st_mtime_ns, before)

    def test_invalid_types_return_400(self):
        actions = (lambda: app.groups_create({'name':123}),
                   lambda: app.groups_rename('x', {'name':[]}),
                   lambda: app.groups_add_member('x', {'account':123}),
                   # create_account 2026-10 起要求 request（本机写保护只看 client.host）
                   lambda: app.create_account({'account':123}, _LocalRequest()))
        for action in actions:
            with self.subTest(action=action), self.assertRaises(HTTPException) as exc:
                action()
            self.assertEqual(exc.exception.status_code, 400)

    def test_empty_and_normalized_subset(self):
        fixture = {'videos':[], 'accounts':[], 'kpis':{'videos':231}, 'account_options':[]}
        with patch.object(benchmark, '_aggregate', return_value=fixture), patch.object(benchmark, '_deps_mtime', return_value=0):
            empty = app.benchmark_view(accounts=' , ', refresh=True)
            self.assertEqual(empty['kpis']['videos'], 0)
            self.assertEqual(empty['filtered_accounts'], [])
            sub = app.benchmark_view(accounts=' beta,alpha,beta, ', refresh=True)
            self.assertEqual(sub['filtered_accounts'], ['beta','alpha'])
            full = benchmark.build()
            self.assertEqual(full['kpis']['videos'], 231)

    def test_metrics_dedup_numbers_dates_and_product_sample(self):
        now = dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).replace(tzinfo=None)
        recent = (now - dt.timedelta(days=1)).strftime('%Y-%m-%d %H:%M:%S')
        old = (now - dt.timedelta(days=20)).strftime('%Y-%m-%d %H:%M:%S')
        future = (now + dt.timedelta(days=20)).strftime('%Y-%m-%d %H:%M:%S')
        items = [{'id':'1','stats':{'playCount':'10000.0'},'publish_time_gmt8':recent},
                 {'id':'1','stats':{'playCount':999999},'publish_time_gmt8':recent},
                 {'id':'2','stats':{'playCount':'N/A'},'publish_time_gmt8':old},
                 {'id':'3','stats':{'playCount':'Infinity'},'publish_time_gmt8':future},
                 {'id':'4','stats':None,'publish_time_gmt8':'invalid'}]
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root/'scrape_alpha.json').write_text(json.dumps({'items':items}), encoding='utf-8')
            (root/'alpha').mkdir()
            (root/'alpha'/'alpha_products.json').write_text(json.dumps({'products':{
                '1':[{'product_id':'p'}], '2':[], '3':None, 'old':[{'product_id':'q'}]}}), encoding='utf-8')
            with patch.object(config,'OUTPUT_DIR',root), patch.object(config,'LEDGER_DIR',root), patch.object(groups,'list_groups',return_value={'groups':[{'id':'x','name':'X','members':[{'account':'alpha'}]}]}):
                row = app._group_metrics_for('x')['accounts'][0]
            self.assertEqual(row['videos'],4)
            self.assertEqual(row['total_plays'],10000)
            self.assertEqual(row['viral'],1)
            self.assertEqual(row['recent7d'],1)
            self.assertEqual(row['last_pub'],recent)
            self.assertEqual(row['product_coverage'],25)

    def test_empty_group_and_missing_group(self):
        with patch.object(groups,'list_groups',return_value={'groups':[{'id':'x','name':'X','members':[]}]}):
            self.assertTrue(app._group_metrics_for('x')['empty'])
            with self.assertRaises(HTTPException) as exc:
                app._group_metrics_for('missing')
            self.assertEqual(exc.exception.status_code,404)

    def test_ledger_boundary_and_missing_file(self):
        with tempfile.TemporaryDirectory() as td, patch.object(config,'LEDGER_DIR',Path(td)):
            for name, code in [('../outside.xlsx',403), ('missing.xlsx',404), ('bad.txt',400)]:
                with self.subTest(name=name), self.assertRaises(HTTPException) as exc:
                    app._ledger_file(name)
                self.assertEqual(exc.exception.status_code,code)
        self.assertEqual(app._cell_str(dt.date(2026,10,17)), '2026-10-17')

    def test_exact_ledger_match_across_files(self):
        import openpyxl
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            paths = []
            for filename, sheet in [('first.xlsx','对标记录-alicebob'),('second.xlsx','对标记录-bob')]:
                wb = openpyxl.Workbook()
                wb.active.title = sheet
                p = root/filename
                wb.save(p)
                wb.close()
                paths.append(p)
            # 2026-11-02（CRD 0002 / ADR 0001）：P2 起账号不在 accounts.json 会 404，
            # 精确匹配跨文件这条契约的 bob / icebob 必须先算「账号存在」。
            with patch.object(config,'list_ledgers',return_value=paths), patch.object(config,'load_accounts',lambda *a, **k: {'accounts':[{'account':'bob'},{'account':'icebob'}]}):
                self.assertEqual(app.ledger_resolve('bob')['file'],'second.xlsx')
                self.assertIsNone(app.ledger_resolve('icebob')['sheet'])

    def test_schedule_validation_and_automation_override(self):
        # ★ 2026-10-24（D1-A）：定时页面与 /api/schedule* 已下线 →
        #   `set_schedule` 校验用例整体移除（校验逻辑随 scheduler.save 保留，
        #   但已无任何 HTTP 入口）；保留「自动化流程可覆盖账号名单」这段真实契约。
        fake = Mock()
        fake.snapshot.return_value = {'id':'fixture'}
        # 2026-11-02（CRD 0002 / ADR 0004）：P1-2 存在性校验后，桩账号 old/new 必须在账号表里，
        # 否则 400「账号不存在」；本用例要验证的是「payload 覆盖流程配置」，断言不变。
        with patch.object(config,'load_automations',return_value=[{'id':'x','kind':'daily','accounts':['old']}]), patch.object(config,'load_accounts',lambda *a, **k: {'accounts':[{'account':'old'},{'account':'new'}]}), patch.object(app.runner.MANAGER,'start',return_value=fake) as start:
            app.run_automation('x',{'accounts':['new']})
            self.assertEqual(start.call_args.kwargs['cmd'][-2:], ['--accounts','new'])
            app.run_automation('x',{'accounts':[]})
            self.assertNotIn('--accounts',start.call_args.kwargs['cmd'])

    def test_translation_reports_queue_only(self):
        # ★ 2026-10-24（D5）：桩要补齐 ok=True + by_account —— 真实 `_read_pending_unlocked`
        #   在 total>0 时**一定**下发 by_account；端点优先用它，不再二次读文件。
        #   缺 by_account 会退化成读文件，测试环境没有队列文件 → 503（假失败）。
        stub = {'total': 2, 'ok': True,
                'by_account': [{'account': 'alpha', 'count': 2}]}
        with patch.object(app,'_refresh_pending_translation',return_value=2), patch.object(app,'_read_pending_translation',return_value=stub):
            d = app.translation_backfill()
        self.assertNotIn('已触发',d['note'])
        self.assertIn('不会启动 Agent',d['note'])
        self.assertEqual(d['translated'],0)
        self.assertEqual(d['pending_total'],2)


if __name__ == '__main__':
    unittest.main(verbosity=2)
