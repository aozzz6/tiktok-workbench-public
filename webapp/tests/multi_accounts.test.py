"""多账户批跑回归：仅用临时流水线/运行记录，不运行生产采集或台账写入。"""
import json
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app
import config
import runner
from fastapi import HTTPException


class MultiAccountsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.stack = __import__('contextlib').ExitStack()
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self.stack.close)
        for name, value in dict(PIPELINE_DIR=self.root, RUNS_JSONL=self.root/'runs.jsonl',
                                ACCOUNTS_JSON=self.root/'accounts.json').items():
            self.stack.enter_context(patch.object(config, name, value))
        self.stack.enter_context(patch.object(config, 'python_exe', return_value=sys.executable))
        self.stack.enter_context(patch.object(config, 'ledger_path', return_value=self.root/'fixture.xlsx'))
        config.ACCOUNTS_JSON.write_text(json.dumps({'accounts':[
            {'account':'alpha','lang':'en'}, {'account':'beta','lang':'de'}, {'account':'gamma','lang':'fr'}]}))
        self.base = dict(kind='pipeline', account='alpha', date_from='2026-10-16',
                         date_to='2026-10-17', steps=['s0_check'])
        (self.root/'pipeline.py').write_text(
            "import sys,time,json\nfrom pathlib import Path\n"
            "a=sys.argv[sys.argv.index('--account')+1]\n"
            "p=Path(__file__).with_name('events.jsonl')\n"
            "def event(s):\n"
            " with p.open('a') as f: f.write(json.dumps([a,s])+chr(10))\n"
            "event('begin')\nprint('BEGIN '+a,flush=True)\n"
            "time.sleep(20 if Path(__file__).with_name('slow').exists() else 0.05)\n"
            "event('end')\nprint('END '+a,flush=True)\n"
            "sys.exit(7 if a=='beta' and Path(__file__).with_name('fail').exists() else 0)\n")

    def call(self, **overrides):
        fake = SimpleNamespace(id='fixture', snapshot=lambda: {'id':'fixture'})
        with patch.object(app.runner.MANAGER, 'start', return_value=fake) as start:
            app.start_run({**self.base, **overrides})
            return start.call_args

    def test_single_array_overrides_dropdown(self):
        c = self.call(accounts=['beta'])
        self.assertEqual(c.kwargs['account'], 'beta')
        self.assertEqual(c.args[2][c.args[2].index('--account')+1], 'beta')

    def test_single_array_without_account(self):
        self.assertEqual(self.call(account=None, accounts=['beta']).kwargs['account'], 'beta')

    def test_normalize_and_deduplicate(self):
        c=self.call(accounts=[' beta ', 'beta'])
        self.assertEqual(c.kwargs['account'], 'beta')
        self.assertNotIn('-c',c.args[2])

    def test_invalid_arrays(self):
        for value in ('alpha', 42, {}, [1], [None], [''], [], None):
            with self.subTest(value=value), self.assertRaises(HTTPException) as raised:
                self.call(accounts=value)
            self.assertEqual(raised.exception.status_code,400)

    def test_daily_empty_or_invalid_is_rejected(self):
        for value in ([], None, 42, 'alpha', ['']):
            with self.subTest(value=value), self.assertRaises(HTTPException) as raised:
                self.call(kind='daily',accounts=value)
            self.assertEqual(raised.exception.status_code,400)

    def test_daily_subset(self):
        c=self.call(kind='daily',accounts=[' beta ','alpha','beta'])
        self.assertEqual(c.kwargs['cmd'][-3:],['--accounts','beta','alpha'])

    def test_daily_omitted_retains_all_semantics(self):
        c=self.call(kind='daily')
        self.assertNotIn('--accounts',c.kwargs['cmd'])

    def test_no_selection_or_invalid_steps(self):
        for steps in ([],None,'s0_check',[{}],['unknown']):
            with self.subTest(steps=steps), self.assertRaises(HTTPException) as raised:
                self.call(steps=steps)
            self.assertEqual(raised.exception.status_code,400)

    def test_invalid_dates(self):
        for dates in ({'date_from':'bad'},{'date_to':'2026-10-01'}):
            with self.subTest(dates=dates), self.assertRaises(HTTPException): self.call(**dates)

    def test_all_commands_validated_before_start(self):
        with patch.object(app.runner.MANAGER,'start') as start, self.assertRaises(HTTPException):
            app.start_run({**self.base,'accounts':['alpha','INVALID']})
        start.assert_not_called()

    def test_per_account_languages_and_options(self):
        c=self.call(accounts=['alpha','beta'], steps=['s2_scrape','s5_transcribe'],
                    backend='openai', no_resume=True, allow_scrape_errors=True)
        driver=c.args[2][-1]
        self.assertIn("'--lang', 'en'",driver)
        self.assertIn("'--lang', 'de'",driver)
        self.assertIn('s5b_products',driver)
        self.assertIn('--no-resume',driver)
        self.assertIn('--allow-scrape-errors',driver)

    def events(self):
        return [json.loads(s) for s in (self.root/'events.jsonl').read_text().splitlines()]

    def execute(self, accounts):
        return subprocess.run(app._build_multi_pipeline_cmd(self.base,accounts),cwd=self.root,
                              env=config.run_env(), capture_output=True,text=True,encoding='utf-8',timeout=10)

    def test_real_driver_serial_and_account_labels(self):
        r=self.execute(['alpha','beta','gamma'])
        self.assertEqual(r.returncode,0,r.stderr)
        self.assertEqual(self.events(),[[a,s] for a in ('alpha','beta','gamma') for s in ('begin','end')])
        self.assertIn('[multi] (2/3) 账号: beta',r.stdout)
        self.assertIn('[multi] 全部账号完成',r.stdout)

    def test_real_driver_fail_fast(self):
        (self.root/'fail').touch()
        r=self.execute(['alpha','beta','gamma'])
        self.assertEqual(r.returncode,7)
        self.assertEqual(self.events(),[['alpha','begin'],['alpha','end'],['beta','begin'],['beta','end']])
        self.assertNotIn('全部账号完成',r.stdout)

    def test_cancel_before_spawn(self):
        manager=runner.RunManager()
        with patch.object(threading.Thread,'start'):
            run=manager.start('pipeline','fixture',[sys.executable,'-c','raise RuntimeError()'])
        self.assertTrue(manager.stop(run.id))
        with patch.object(subprocess,'Popen') as popen: manager._pump(run)
        popen.assert_not_called()
        self.assertEqual(run.status,'stopped')

    def test_taskkill_failure_does_not_claim_stopped(self):
        manager=runner.RunManager()
        run=runner.Run('fixture','pipeline','fixture',[],str(self.root))
        run.proc=Mock(pid=123, poll=Mock(return_value=None))
        manager._runs[run.id]=run
        with patch.object(subprocess,'run',return_value=SimpleNamespace(returncode=1)):
            self.assertFalse(manager.stop(run.id))
        self.assertEqual(run.status,'running')

    def test_real_running_batch_can_stop_tree(self):
        (self.root/'slow').touch()
        manager=runner.RunManager()
        run=manager.start('pipeline','fixture',app._build_multi_pipeline_cmd(self.base,['alpha','beta']))
        deadline=time.monotonic()+8
        try:
            while time.monotonic()<deadline and not any('BEGIN alpha' in l['text'] for l in run.lines): time.sleep(.02)
            self.assertTrue(any('BEGIN alpha' in l['text'] for l in run.lines))
            self.assertTrue(manager.stop(run.id))
            run.proc.wait(timeout=5)
            self.assertEqual(run.status,'stopped')
            time.sleep(.1)
            self.assertEqual(self.events(),[['alpha','begin']])
        finally:
            if run.proc and run.proc.poll() is None: manager.stop(run.id)

    def test_failed_batch_does_not_write_ledger(self):
        run=SimpleNamespace(kind='pipeline',status='failed',title='fixture',id='fixture',account='alpha,beta')
        with patch.object(app,'_start_ledger_sync') as sync: app._auto_ledger_sync(run)
        sync.assert_not_called()


if __name__=='__main__': unittest.main(verbosity=2)
