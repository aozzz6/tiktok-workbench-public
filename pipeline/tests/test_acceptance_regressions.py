"""Acceptance regressions: isolated files, fake network/model calls, no live writes."""
import contextlib
import importlib.util
import io
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

PIPE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PIPE / 'scripts'))
import openpyxl
import auto_translate as at
import batch_transcribe_fw as bt
import build_ledger_15col as bl
import scrape_tiktok_video as sc
import retry_scrape_failed as retry
import extract_products as products
sys.path.insert(0, str(PIPE.parent / 'webapp'))
import runner
import groups


class AcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.out = self.root / 'output'
        self.out.mkdir()
        self.vid = '1111111111111111111'
        self.acc = 'probe'
        self.ledger = self.root / 'ledger.xlsx'
        self.cfg = {'xlsx': str(self.ledger), 'accounts': [
            {'account': self.acc, 'active': True, 'out_dir': str(self.out)}]}
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = '对标记录-probe'
        ws.append(bl.HEADER)
        ws.append(['2026-10-16', '@probe', '', '', '',
                   f'https://www.tiktok.com/@probe/video/{self.vid}', '',
                   1, 2, 3, 4, 5, '', ''])
        wb.save(self.ledger)
        wb.close()
        self.item = {'id': self.vid, 'desc': 'Full title #fallback',
                     'hashtags': ['topic'], 'stats': {}}
        self.write_items([self.item])

    def write_items(self, items):
        (self.out / 'scrape_probe.json').write_text(json.dumps({'items': items}), encoding='utf-8')

    def read_cells(self):
        wb = openpyxl.load_workbook(self.ledger)
        vals = list(wb.active.values)
        wb.close()
        return vals

    def transcript(self, text='This is the complete updated English transcript.'):
        d = self.out / 'probe_audio' / 'transcripts'
        d.mkdir(parents=True, exist_ok=True)
        (d / f'probe_{self.vid}_transcript.txt').write_text(
            f'[00:00:00,000 -> 00:00:01,000] {text}\n', encoding='utf-8')

    def test_existing_title_refill_preserves_manual_values(self):
        self.assertEqual(bl.main_write(cfg=self.cfg), 0)
        self.assertEqual(self.read_cells()[1][3], 'Full title')
        wb = openpyxl.load_workbook(self.ledger)
        wb.active['D2'] = 'manual title'
        wb.active['E2'] = 'manual tags'
        wb.save(self.ledger)
        wb.close()
        self.assertEqual(bl.main_write(cfg=self.cfg), 0)
        self.assertEqual(self.read_cells()[1][3:5], ('manual title', 'manual tags'))

    def test_transcript_refill_count_and_force(self):
        self.transcript()
        self.item.update(desc='', hashtags=[])
        self.write_items([self.item])
        log = io.StringIO()
        with contextlib.redirect_stdout(log):
            self.assertEqual(bl.main_write(cfg=self.cfg), 0)
        self.assertIn('"refilled": 1', log.getvalue())
        self.transcript('Replacement transcript after a corrected recognition pass.')
        bl.main_write(cfg=self.cfg)
        self.assertIn('complete updated', self.read_cells()[1][12])
        bl.main_write(cfg=self.cfg, force_refill=True)
        self.assertIn('Replacement', self.read_cells()[1][12])

    def test_numeric_ids_do_not_duplicate_existing_rows(self):
        self.item['id'] = int(self.vid)
        self.write_items([self.item])
        self.assertEqual(bl.main_write(cfg=self.cfg), 0)
        self.assertEqual(len(self.read_cells()), 2)

    def test_schema_mismatch_rejected_before_save(self):
        wb = openpyxl.load_workbook(self.ledger)
        wb.active['M1'] = 'unexpected column'
        wb.save(self.ledger)
        wb.close()
        before = self.ledger.read_bytes()
        self.assertNotEqual(bl.main_write(cfg=self.cfg), 0)
        self.assertEqual(self.ledger.read_bytes(), before)

    def test_verify_checks_video_identity_not_row_count(self):
        self.item['id'] = '2222222222222222222'
        self.write_items([self.item])
        # _verify 的签名已改为 (ledger, img_ledger=None, accounts=None, summary=None, cfg=None)
        self.assertNotEqual(bl._verify(self.ledger, None, ['probe'], cfg=self.cfg), 0)

    def test_apply_translation_validates_rows_and_keeps_backup(self):
        self.transcript()
        bl.main_write(cfg=self.cfg)
        payload = self.root / 'translate.json'
        before = self.ledger.read_bytes()
        with patch.object(bl, 'load_cfg', return_value=self.cfg):
            for rows in ({'1': 'header overwrite'}, {'999': 'phantom row'}, {'2': {'bad': 'type'}}):
                payload.write_text(json.dumps({'probe': rows}), encoding='utf-8')
                self.assertNotEqual(bl.main_apply_trans(str(payload)), 0)
                self.assertEqual(self.ledger.read_bytes(), before)
            baks_before = set(self.root.glob('ledger.xlsx.bak_*'))
            payload.write_text(json.dumps({'probe': {'2': '有效译文'}}), encoding='utf-8')
            self.assertEqual(bl.main_apply_trans(str(payload)), 0)
            self.assertGreater(len(set(self.root.glob('ledger.xlsx.bak_*')) - baks_before), 0)
            self.assertEqual(self.read_cells()[1][13], '有效译文')

    def transcribe_run(self, state, transcript_exists=True, failing=False, extra=()):
        audio = self.root / 'audio'
        audio.mkdir()
        stem = f'probe_{self.vid}'
        (audio / f'{stem}.mp3').write_bytes(b'fake-audio')
        td = audio / 'transcripts'
        td.mkdir()
        (td / '.transcribe_state.json').write_text(json.dumps(state), encoding='utf-8')
        if transcript_exists:
            (td / f'{stem}_transcript.txt').write_text('prior text', encoding='utf-8')
        calls = []
        class Model:
            def __init__(self, *args, **kwargs):
                calls.append(('model', args, kwargs))
            def transcribe(self, *args, **kwargs):
                calls.append(('transcribe', args, kwargs))
                if failing:
                    raise RuntimeError('fixture decode failure')
                return iter([types.SimpleNamespace(start=0,end=1,text='A complete transcript exceeding thirty characters.')]), types.SimpleNamespace(duration=1)
        fake = types.SimpleNamespace(WhisperModel=Model)
        out = io.StringIO()
        # CRD-0006：给走 `--backend faster` 的 5 个用例（stale_done / missing_transcript /
        # successful_retry / transcription_failure / japanese_language_alias）把
        # `model_cache.ensure_model` 打成桩 —— 它们验证的是转写/断点/语言逻辑，不该依赖
        # "本机有没有 faster-whisper 模型"，否则换台机器就红（没模型时还会真的去联网下载）。
        fake_model = self.root / 'fake_model'
        with patch.object(sys, 'argv', ['batch', '--audio-dir', str(audio), '--backend', 'faster', '--lang', 'en', *extra]), patch.dict(sys.modules, {'faster_whisper':fake}), patch.object(bt,'setup_cuda_dll_paths',return_value=[]), patch.object(bt,'cuda_device_count',return_value=1), patch.object(bt.model_cache,'ensure_model',return_value=fake_model), contextlib.redirect_stdout(out):
            rc = bt.main()
        return rc, json.loads(out.getvalue().splitlines()[-1]), calls, td

    def test_stale_done_does_not_skip_new_audio(self):
        rc, summary, calls, td = self.transcribe_run({'done': {'old_video': {'chars':50}}})
        self.assertEqual(summary['done'], [f'probe_{self.vid}'])
        self.assertTrue(any(c[0]=='transcribe' for c in calls))

    def test_missing_transcript_is_regenerated(self):
        rc, summary, calls, td = self.transcribe_run({'done': {f'probe_{self.vid}': {'chars':50}}}, transcript_exists=False)
        self.assertTrue(any(c[0]=='transcribe' for c in calls))
        self.assertTrue((td/f'probe_{self.vid}_transcript.txt').exists())

    def test_successful_retry_clears_old_error(self):
        stem = f'probe_{self.vid}'
        rc, summary, calls, td = self.transcribe_run({'done':{},'suspect_hallucination':[{'id':stem,'text':'TRANSCRIBE_ERROR: old failure'}]})
        self.assertEqual(summary['suspect_hallucination'], [])

    def test_transcription_failure_returns_nonzero(self):
        rc, summary, calls, td = self.transcribe_run({'done':{}}, failing=True)
        self.assertNotEqual(rc, 0)
        self.assertEqual(summary['done'], [])

    def test_japanese_language_alias_matches_whisper(self):
        rc,summary,calls,td=self.transcribe_run({'done':{}},extra=['--lang','jp'])
        self.assertEqual(next(c for c in calls if c[0]=='transcribe')[2]['language'],'ja')

    def test_cpu_backend_explicitly_uses_cpu(self):
        """CRD-0006（2026-11-06，QA 改写）：`--backend openai` 后端**已退役**（ADR-0002：包里不带
        openai-whisper/PyTorch），旧断言「退 0 且调用 `whisper.load_model`」被本作业有意作废。
        新判据 = T-20 DoD 1(a)：退出码 2、stderr 有一句可读提示（含「只带 faster-whisper」）、
        没有 Traceback / ModuleNotFoundError。不加载模型、不联网。
        """
        audio = self.root / 'audio_openai'
        audio.mkdir()
        (audio / f'probe_{self.vid}.mp3').write_bytes(b'fake-audio')
        out, err = io.StringIO(), io.StringIO()
        with patch.object(sys, 'argv', ['batch', '--audio-dir', str(audio),
                                        '--backend', 'openai']), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = bt.main()
        err_text = err.getvalue()
        self.assertEqual(rc, 2, f'--backend openai 已退役，应退出码 2，实得 {rc}；stderr={err_text!r}')
        self.assertIn('只带 faster-whisper', err_text)
        self.assertNotIn('Traceback', err_text)
        self.assertNotIn('ModuleNotFoundError', err_text)

    def scrape_run(self, ids, old=None, fail=False):
        path = self.out/'scrape_probe.json'
        if old is not None:
            path.write_text(old, encoding='utf-8')
        html_item = {'desc':'x'*250+' #tail', 'createTime':1720000000, 'challenges':[{'title':'tail'}]}
        output=io.StringIO()
        # ★ 2026-11-02（T-04）：main() 现在调 parse_item_detail()，返回 4 元组
        #   (item, reason, statusCode, statusMsg)。以前这里打的是 parse_item()，
        #   mock 打偏 → 真的去 fetch_page 抓取 → 触发浏览器兜底 → items 为空。
        parsed = (None, 'removed', None, '') if fail else (html_item, '', 0, '')
        with patch.object(sys,'argv',['scrape','--account','probe','--out-dir',str(self.out),'--ids',ids,'--merge']), patch.object(sc,'fetch_page',return_value='fixture'), patch.object(sc,'parse_item_detail',return_value=parsed), patch.object(sc.time,'sleep') as sleep, contextlib.redirect_stdout(output):
            rc = sc.main()
        return rc, path, sleep

    def test_scrape_long_desc_and_rate_limit(self):
        rc,path,sleep = self.scrape_run(self.vid+',2222222222222222222',old='{"items":[],"errors":[]}')
        data=json.loads(path.read_text(encoding='utf-8'))
        self.assertGreater(len(data['items'][0]['desc']),200)
        self.assertEqual(data['items'][0]['hashtags'],['tail'])
        sleep.assert_called_once_with(sc.RATE_LIMIT_SEC)

    def test_corrupt_merge_does_not_destroy_history(self):
        old='{"items": [corrupted history'
        rc,path,sleep=self.scrape_run(self.vid,old=old)
        self.assertNotEqual(rc,0)
        self.assertEqual(path.read_text(encoding='utf-8'),old)

    def test_translation_scan_keeps_complete_text(self):
        wb=openpyxl.load_workbook(self.ledger)
        wb.active['M2']='A'*2500+' END'
        wb.save(self.ledger)
        wb.close()
        with patch.object(at,'load_cfg',return_value=self.cfg):
            self.assertTrue(at.scan_pending()['probe'][0]['text'].endswith(' END'))

    def test_translation_rejects_unrequested_rows_and_partial_success(self):
        pending={'probe':[{'row':2,'vid':self.vid,'text':'Translate me fully.'}]}
        with patch.object(sys,'argv',['auto','--dry-run']), patch.object(at,'load_model',return_value={'id':'fixture'}), patch.object(at,'scan_pending',return_value=pending), patch.object(at,'call_llm',return_value={'probe':{'1':'bad header','999':'bad row'}}), contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertNotEqual(at.main(),0)
            self.assertFalse(json.loads(out.getvalue().splitlines()[-1])['ok'])

    def test_translation_snapshot_rejects_changed_source_or_manual_translation(self):
        self.transcript()
        bl.main_write(cfg=self.cfg)
        text=self.read_cells()[1][12]
        expected={'probe':{'2':{'vid':self.vid,'text':text}}}
        payload=self.root/'translation.json'
        payload.write_text(json.dumps({'probe':{'2':'有效译文'}}),encoding='utf-8')
        wb=openpyxl.load_workbook(self.ledger)
        wb.active['M2']='Edited source after translation started'
        wb.save(self.ledger)
        wb.close()
        before=self.ledger.read_bytes()
        with patch.object(bl,'load_cfg',return_value=self.cfg):
            self.assertEqual(bl.main_apply_trans(str(payload),expected=expected),1)
        self.assertEqual(self.ledger.read_bytes(),before)
        wb=openpyxl.load_workbook(self.ledger)
        wb.active['M2']=text
        wb.active['N2']='manual translation'
        wb.save(self.ledger)
        wb.close()
        before=self.ledger.read_bytes()
        with patch.object(bl,'load_cfg',return_value=self.cfg):
            self.assertEqual(bl.main_apply_trans(str(payload),expected=expected),1)
        self.assertEqual(self.ledger.read_bytes(),before)

    def test_translation_success_applies_with_original_snapshot(self):
        self.transcript()
        bl.main_write(cfg=self.cfg)
        text=self.read_cells()[1][12]
        payload=self.root/'translation.json'
        payload.write_text(json.dumps({'probe':{'2':'有效译文'}}),encoding='utf-8')
        with patch.object(bl,'load_cfg',return_value=self.cfg):
            self.assertEqual(bl.main_apply_trans(str(payload),expected={'probe':{'2':{'vid':self.vid,'text':text}}}),0)

    def test_authorized_retranslation_checks_previous_translation_snapshot(self):
        self.transcript()
        bl.main_write(cfg=self.cfg)
        text=self.read_cells()[1][12]
        wb=openpyxl.load_workbook(self.ledger)
        wb.active['N2']='old translation'
        wb.save(self.ledger)
        wb.close()
        payload=self.root/'retranslation.json'
        payload.write_text(json.dumps({'probe':{'2':'新译文'}}),encoding='utf-8')
        with patch.object(bl,'load_cfg',return_value=self.cfg):
            self.assertEqual(bl.main_apply_trans(str(payload),expected={'probe':{'2':{
                'vid':self.vid,'text':text,'translation':'old translation'}}}),0)
        self.assertEqual(self.read_cells()[1][13],'新译文')

    def test_auto_translation_partial_and_wrong_types_fail(self):
        pending={'probe':[{'row':2,'vid':self.vid,'text':'source one'},
                          {'row':3,'vid':'2222222222222222222','text':'source two'}]}
        for response in ({'probe':{'2':'有效译文'}}, {'probe':{'2':None,'3':{'bad':'type'}}}):
            with patch.object(sys,'argv',['auto','--dry-run']), patch.object(at,'load_model',return_value={'id':'fixture'}), patch.object(at,'scan_pending',return_value=pending), patch.object(at,'call_llm',return_value=response), contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertEqual(at.main(),1)
                self.assertFalse(json.loads(out.getvalue().splitlines()[-1])['ok'])

    def test_automatic_translation_no_pending_needs_no_model(self):
        with patch.object(sys,'argv',['auto']),patch.object(at,'scan_pending',return_value={}),patch.object(at,'load_model',side_effect=AssertionError('no model needed')),contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(at.main(),0)

    def test_auto_translation_passes_snapshot_to_writer(self):
        pending={'probe':[{'row':2,'vid':self.vid,'text':'Complete source text.'}]}
        def fake_apply(cmd,**kwargs):
            self.assertIn('--expected-source',cmd)
            src=json.loads(Path(cmd[-1]).read_text(encoding='utf-8'))
            self.assertEqual(src['probe']['2']['text'],'Complete source text.')
            self.assertEqual(src['probe']['2']['vid'],self.vid)
            return types.SimpleNamespace(returncode=0,stdout='apply PASS',stderr='')
        with patch.object(sys,'argv',['auto']),patch.object(at,'PIPE',self.root),patch.object(at,'load_model',return_value={'id':'fixture'}),patch.object(at,'scan_pending',return_value=pending),patch.object(at,'call_llm',return_value={'probe':{'2':'有效译文'}}),patch.object(at.subprocess,'run',side_effect=fake_apply),contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(at.main(),0)

    def test_empty_scrape_does_not_overwrite_existing_file(self):
        path=self.out/'scrape_probe.json'
        before=path.read_bytes()
        with patch.object(sys,'argv',['scrape','--account','probe','--audio-dir',str(self.root/'missing'),'--out-dir',str(self.out)]),patch.object(sc,'fetch_page') as fetch:
            self.assertNotEqual(sc.main(),0)
        self.assertEqual(path.read_bytes(),before)
        fetch.assert_not_called()

    def test_retry_scrape_preserves_long_desc_and_hashtags(self):
        path=self.out/'scrape_probe.json'
        path.write_text(json.dumps({'items':[],'errors':[{'id':self.vid,'error':'old'}]}),encoding='utf-8')
        item={'desc':'x'*250+' #tail','challenges':[{'title':'tail'}]}
        # ★ 2026-11-02（T-04）：retry_scrape_failed.py 第 56 行按二元组解包
        #   sc.parse_item_ex(...)，所以 mock 目标是 parse_item_ex，不是 parse_item。
        with patch.object(sys,'argv',['retry','--account','probe','--out-dir',str(self.out)]),patch.object(sc,'fetch_page',return_value='fixture'),patch.object(sc,'parse_item_ex',return_value=(item,'')),patch.object(retry.time,'sleep'),contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(retry.main(),0)
        data=json.loads(path.read_text(encoding='utf-8'))
        self.assertEqual(data['items'][0]['desc'],item['desc'])
        self.assertEqual(data['items'][0]['hashtags'],['tail'])

    def test_missing_video_detail_is_not_classified_as_no_products(self):
        html='<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__">'+json.dumps({'__DEFAULT_SCOPE__':{'webapp.video-detail':{'itemInfo':{}}}})+'</script>'
        with patch.object(products,'fetch_html',return_value=html):
            with self.assertRaisesRegex(RuntimeError,'itemStruct empty'):
                products.extract_products_for_video('probe',self.vid)

    def test_long_running_logs_keep_monotonic_cursor(self):
        manager=runner.RunManager()
        run=runner.Run(id='fixture',kind='script',title='fixture',cmd=[],cwd=str(self.root))
        manager._runs[run.id]=run
        for i in range(runner.MAX_LINES+3):
            manager._emit(run,str(i))
        rows=manager.lines(run.id,since=runner.MAX_LINES)
        self.assertEqual([r['i'] for r in rows],list(range(runner.MAX_LINES,runner.MAX_LINES+3)))
        self.assertEqual(run.snapshot()['line_count'],runner.MAX_LINES+3)

    def test_deleted_seed_group_does_not_return(self):
        with patch.object(groups,'GROUPS_FILE',self.root/'groups.json'),patch.object(groups.config,'load_accounts',return_value={'accounts':[]}):
            self.assertIn('bench',{g['id'] for g in groups.list_groups()['groups']})
            groups.delete_group('bench')
            self.assertNotIn('bench',{g['id'] for g in groups.list_groups()['groups']})
            self.assertIn('default',{g['id'] for g in groups.list_groups()['groups']})


if __name__ == '__main__':
    unittest.main(verbosity=2)
