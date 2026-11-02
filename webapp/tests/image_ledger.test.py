"""图文台账：临时文件与网络桩；不得调用真实采集/翻译/生产台账写入。"""
import contextlib
import datetime as dt
import importlib.util
import json
import sys
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, Mock
import openpyxl

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT/'webapp'),str(ROOT/'pipeline'),str(ROOT/'pipeline/scripts')]
import app,config
import build_ledger_15col as builder
import post_update as post
def module(name, file):
    spec=importlib.util.spec_from_file_location(name,file)
    value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value);return value
pipe=module('image_pipeline_test',ROOT/'pipeline/pipeline.py')
daily=module('image_daily_test',ROOT/'pipeline/daily_update.py')
enum=module('image_enum_test',ROOT/'pipeline/scripts/enumerate_image.py')
flow=module('image_flow_test',ROOT/'pipeline/scripts/post_update_flow.py')
VID='1234567890123456789'
OTHER='2234567890123456789'

class ImageLedgerTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)
        self.stack=contextlib.ExitStack();self.addCleanup(self.stack.close)
        self.video=self.root/'video.xlsx';self.image=self.root/'image.xlsx'
        self.book(self.video,'对标记录-videoacc',builder.HEADER)
        self.book(self.image,builder.IMG_SHEET,builder.IMG_HEADER)
        self.accs=[{'account':'videoacc','out_dir':str(self.root),'audio_dir':str(self.root/'video_audio')},
                   {'account':'photoacc','media_type':'image','out_dir':str(self.root),'audio_dir':str(self.root/'photo_audio')}]
        self.cfg={'xlsx':str(self.video),'img_xlsx':str(self.image),'accounts':self.accs,'reports_root':str(self.root),'recall_days':2}
        self.cfgfile=self.root/'accounts.json';self.cfgfile.write_text(json.dumps(self.cfg))
        for name,val in {'LEDGER_DIR':self.root,'ACCOUNTS_JSON':self.cfgfile,'OUTPUT_DIR':self.root}.items():
            self.stack.enter_context(patch.object(config,name,val))
        for cache in (app._ledger_cache,app._ledger_ids_cache,app._overview_cache):
            self.stack.enter_context(patch.dict(cache,{'ts':0.,'data':None}))
        self.stack.enter_context(patch.object(pipe,'RUN_LOG',self.root/'run.log'))
        self.stack.enter_context(patch.object(pipe,'STATE_FILE',self.root/'state.json'))
        self.args=SimpleNamespace(account='photoacc',media_type='image',date_from='2026-10-16',date_to='2026-10-17',allow_scrape_errors=True,scrape_incremental=True)
        for a in self.accs:self.scrape(a['account'])
    def book(self,path,sheet,header):
        wb=openpyxl.Workbook();ws=wb.active;ws.title=sheet;ws.append(header);wb.save(path);wb.close()
    def scrape(self,account,ids=(VID,)):
        value={'items':[{'id':vid,'url':f'https://www.tiktok.com/@{account}/video/{vid}',
                        'publish_time_gmt8':'2026-10-17 01:00:00','desc':'fixture #tag',
                        'stats':dict.fromkeys(builder.STAT_KEYS,10)} for vid in ids], 'errors':[]}
        (self.root/f'scrape_{account}.json').write_text(json.dumps(value))
    def write(self,accounts=None):return builder.main_write(accounts,self.cfg)
    def row(self,account='photoacc',vid=VID):return ['2026-10-17','@'+account,'2026-10-17 01:00:00','fixture','#tag',vid,'',10,10,10,10,10]
    def append(self,*rows):
        wb=openpyxl.load_workbook(self.image)
        for r in rows:wb.active.append(r)
        wb.save(self.image);wb.close()
    def test_image_scope_leaves_video_bytes_and_mtime(self):
        before=(self.video.read_bytes(),self.video.stat().st_mtime_ns)
        self.assertEqual(self.write(['photoacc']),0)
        self.assertEqual(before,(self.video.read_bytes(),self.video.stat().st_mtime_ns))
        self.assertFalse(list(self.root.glob('video.xlsx.bak*')))
    def test_video_scope_leaves_image_bytes_and_mtime(self):
        before=(self.image.read_bytes(),self.image.stat().st_mtime_ns)
        self.assertEqual(self.write(['videoacc']),0)
        self.assertEqual(before,(self.image.read_bytes(),self.image.stat().st_mtime_ns))
        self.assertFalse(list(self.root.glob('image.xlsx.bak*')))
    def test_image_scope_does_not_require_video_file(self):
        self.video.unlink();self.assertEqual(self.write(['photoacc']),0)
    def test_video_scope_does_not_require_image_file(self):
        self.image.unlink();self.assertEqual(self.write(['videoacc']),0)
    def test_image_must_not_fallback_or_alias_video(self):
        before=self.video.read_bytes()
        for val in (None,str(self.video.parent/'x/../video.xlsx')):
            self.cfg['img_xlsx']=val
            self.assertEqual(self.write(['photoacc']),1)
            self.assertEqual(before,self.video.read_bytes())
    def test_empty_scope_no_writes(self):
        with patch.object(openpyxl.Workbook,'save') as save:self.assertEqual(self.write([]),0)
        save.assert_not_called()
    def test_shared_image_accounts_idempotent_and_do_not_cross(self):
        self.cfg['accounts'].append({'account':'photoother','media_type':'image','out_dir':str(self.root)})
        self.scrape('photoother')
        self.assertEqual(self.write(['photoacc','photoother']),0)
        self.assertEqual(self.write(['photoacc']),0)
        wb=openpyxl.load_workbook(self.image);rows=list(wb.active.values);wb.close()
        self.assertEqual(len(rows),3)
        self.assertEqual({r[1] for r in rows[1:]},{'@photoacc','@photoother'})
    def test_image_updates_own_stats_preserves_manual_title(self):
        row=self.row();row[3]='manual title';row[7]=1;other=self.row('photoother');other[7]=99
        self.append(row,other);self.assertEqual(self.write(['photoacc']),0)
        wb=openpyxl.load_workbook(self.image);ws=wb.active
        self.assertEqual(ws.cell(2,4).value,'manual title');self.assertEqual(ws.cell(2,8).value,10)
        self.assertEqual(ws.cell(3,8).value,99);wb.close()
    def test_wrong_header_rejected_before_write(self):
        wb=openpyxl.load_workbook(self.image);wb.active.cell(1,3).value='wrong';wb.save(self.image);wb.close()
        before=self.image.read_bytes();self.assertEqual(self.write(['photoacc']),1);self.assertEqual(before,self.image.read_bytes())
    def test_post_detects_wrong_ids_with_same_count(self):
        self.append(self.row(vid=OTHER));r=post.check_ledger(self.cfg)
        self.assertIn('photoacc',r['bad'])
        self.assertEqual(next(a for a in r['accounts'] if a['account']=='photoacc')['missing_ids'],[VID])
    def test_post_detects_duplicate_ids(self):
        self.append(self.row(),self.row());r=post.check_ledger(self.cfg)
        self.assertIn('photoacc',r['bad'])
        self.assertEqual(next(a for a in r['accounts'] if a['account']=='photoacc')['duplicate_ids'],1)
    def test_post_deduplicates_expected_ids_and_checks_headers(self):
        self.cfg['accounts']=[self.accs[1]];self.scrape('photoacc',[VID,VID]);self.append(self.row())
        self.video.unlink();self.assertEqual(post.check_ledger(self.cfg)['status'],'ok')
        wb=openpyxl.load_workbook(self.image);wb.active.cell(1,3).value='wrong';wb.save(self.image);wb.close()
        self.assertEqual(post.check_ledger(self.cfg)['status'],'error')
    def test_post_rejects_image_fallback(self):
        self.cfg['img_xlsx']=str(self.video)
        self.assertEqual(post.check_ledger(self.cfg)['status'],'error')
    def test_image_history_prevents_account_deletion(self):
        self.append(self.row());(self.root/'scrape_photoacc.json').unlink()
        with patch.object(config,'_write_accounts') as write,self.assertRaises(ValueError):config.remove_account('photoacc')
        write.assert_not_called()
    def test_unreadable_image_history_prevents_deletion(self):
        self.image.write_bytes(b'corrupt');(self.root/'scrape_photoacc.json').unlink()
        with patch.object(config,'_write_accounts') as write,self.assertRaises(ValueError):config.remove_account('photoacc')
        write.assert_not_called()
    def test_media_schema_checks_and_detail_agree(self):
        self.append(self.row())
        image=next(s for s in app.ledger_overview(True)['sheets'] if s['media_type']=='image')
        self.assertEqual(image['checks'],[]);self.assertEqual(image['account'],'photoacc')
        self.assertEqual(app.ledger_sheet(builder.IMG_SHEET,self.image.name)['checks'],[])
    def test_shared_rows_and_coverage_are_per_account(self):
        self.append(self.row(),self.row('photoother',OTHER))
        self.assertEqual(app._ledger_row_counts(True)['photoacc'],1)
        ids=app._ledger_id_sets(True)
        self.assertEqual(ids['photoacc'],{VID});self.assertEqual(ids['photoother'],{OTHER})
        view=app._account_view(self.accs[1],{},app._ledger_row_counts(True),ids)
        self.assertEqual(view['ledger_covered'],1);self.assertEqual(view['landing'],100)
        self.assertEqual(view['severity'],'ok');self.assertIsNone(view['rates']['translation'])
        self.assertIsNone(view['rates']['products'])
    def test_image_resolve_finds_shared_sheet(self):
        self.append(self.row());r=app.ledger_resolve('photoacc')
        self.assertEqual((r['sheet'],r['file']),(builder.IMG_SHEET,self.image.name))
        # 2026-11-02（CRD 0002 / ADR 0002）：P2 起账号不在 accounts.json 会 404；
        # notthere 必须「账号表里有、台账里没有行也没有同名表」，才是原意图（sheet=None）。
        self.cfg['accounts'].append({'account':'notthere','out_dir':str(self.root)})
        self.cfgfile.write_text(json.dumps(self.cfg))
        self.assertIsNone(app.ledger_resolve('notthere')['sheet'])
    def test_main_ledger_cannot_be_image_or_archive_or_external(self):
        self.cfg['xlsx']=str(self.image);self.cfgfile.write_text(json.dumps(self.cfg))
        self.assertEqual(config.ledger_path(),self.video)
        self.video.unlink();self.assertIsNone(config.ledger_path())
    def proc(self,value,rc=0):return SimpleNamespace(returncode=rc,stdout=json.dumps(value),stderr='fixture failure' if rc else '')
    def test_enum_failure_does_not_reuse_history(self):
        with patch.object(pipe.subprocess,'run',return_value=self.proc({'ids':[],'error':'fixture failure'},1)):
            self.assertFalse(pipe._scrape_image(self.args,'fixture',self.root)[0])
    def test_enum_schema_or_error_not_success(self):
        for value in ({'ids':'bad'},{'ids':[1]},{'ids':[],'error':'offline'},[],{'ids':['bad']}):
            with self.subTest(value=value),patch.object(pipe.subprocess,'run',return_value=self.proc(value)):
                self.assertFalse(pipe._scrape_image(self.args,'fixture',self.root)[0])
    def test_incremental_no_new_reuses_history_with_current_policy(self):
        with patch.object(pipe.subprocess,'run',return_value=self.proc({'ids':[VID]})):
            ok,detail=pipe._scrape_image(self.args,'fixture',self.root)
        self.assertTrue(ok);self.assertIn(VID,detail)
    def test_incremental_scrape_merges_new_ids(self):
        with patch.object(pipe.subprocess,'run',return_value=self.proc({'ids':[VID,OTHER,OTHER]})),patch.object(pipe,'_subprocess_step',return_value=(True,'fixture')) as scrape:
            self.assertTrue(pipe._scrape_image(self.args,'fixture',self.root)[0])
        cmd=scrape.call_args.args[1]
        self.assertIn('--merge',cmd);self.assertEqual(cmd[cmd.index('--ids')+1],OTHER)
    def test_image_skips_audio_transcription_products_and_ffmpeg(self):
        with patch.dict(pipe.CFG,{'python':sys.executable}),patch('shutil.which',return_value=None):self.assertTrue(pipe.step0_check(self.args)[0])
        self.assertTrue(pipe.step1_download(self.args)[0]);self.assertTrue(pipe.step5_transcribe(self.args)[0]);self.assertTrue(pipe.step5b_products(self.args)[0])
    def test_image_daily_runs_only_collection_and_own_ledger(self):
        before=self.video.read_bytes()
        with patch.object(daily,'run_cmd',return_value=(0,'fixture')) as cmd:
            result=daily.run_account(self.accs[1],self.cfg,'2026-10-17',self.root)
        self.assertEqual(cmd.call_count,1);self.assertEqual(result['status'],'ok')
        self.assertEqual(before,self.video.read_bytes());self.assertFalse((self.root/'photo_audio').exists())
        self.assertIn('s2_scrape',cmd.call_args.args[0]);self.assertNotIn('s5_transcribe',cmd.call_args.args[0])
    def test_failed_image_daily_does_not_write_ledger(self):
        before=self.image.read_bytes()
        with patch.object(daily,'run_cmd',return_value=(1,'fixture failed')):
            result=daily.run_account(self.accs[1],self.cfg,'2026-10-17',self.root)
        self.assertEqual(result['status'],'failed');self.assertEqual(before,self.image.read_bytes())
    def test_daily_selection_includes_image_and_preserves_video(self):
        fake={'status':'ok','anomalies':[]}
        with patch.object(daily,'load_cfg',return_value=self.cfg),patch.object(daily,'run_account',return_value=fake) as run,patch.object(daily,'run_hot_monitor',return_value=None),patch.object(daily,'write_reports',return_value='INFO'),patch.object(sys,'argv',['daily_update.py','--date','2026-10-17']):
            self.assertEqual(daily.main(),0)
        self.assertEqual([c.args[0]['account'] for c in run.call_args_list],['videoacc','photoacc'])
    def test_workbench_sync_passes_exact_accounts(self):
        (self.root/'post_update_flow.py').touch()
        with patch.object(config,'SCRIPTS_DIR',self.root),patch.object(app,'_LEDGER_SYNC_ACTIVE',False),patch.object(app.runner.MANAGER,'start') as start:
            app._start_ledger_sync('fixture','photoacc',False)
        self.assertEqual(start.call_args.kwargs['cmd'][-2:],['--accounts','photoacc'])
    def test_image_completion_does_not_start_video_translation(self):
        run=SimpleNamespace(kind='ledger',status='done',account='photoacc',title='fixture',id='fixture')
        with patch.object(app,'_refresh_pending_translation',return_value=5),patch.object(app,'_LEDGER_SYNC_QUEUE',[]),patch.object(app,'_LEDGER_SYNC_ACTIVE',False),patch.object(config,'AUTO_TRANSLATE_ENABLED',True),patch.object(app.threading,'Thread') as thread:
            app._on_ledger_sync_done(run)
        thread.assert_not_called()
    def test_image_flow_preserves_video_pending_and_scopes_verify(self):
        pending=self.root/'pending_trans.json';pending.write_text('{"videoacc": ["existing"]}')
        before=pending.read_bytes()
        with patch.object(flow,'PIPE',self.root),patch.object(flow,'PENDING',pending),patch.object(flow,'run',return_value=(True,'fixture')) as run,patch.object(sys,'argv',['flow','--accounts','photoacc']):
            self.assertEqual(flow.main(),0)
        self.assertEqual([c.args[1] for c in run.call_args_list],[['write','--accounts','photoacc'],['verify','--skip-api','--accounts','photoacc']])
        self.assertEqual(pending.read_bytes(),before)
    def test_scoped_post_verify_ignores_unrelated_video_missing_sheet(self):
        self.append(self.row())
        self.assertEqual(post.check_ledger(self.cfg,['photoacc'])['status'],'ok')
        self.assertEqual(post.check_ledger(self.cfg,['unknown'])['status'],'error')
    def test_real_daily_pipeline_gates_and_image_write_with_local_collectors(self):
        scripts=self.root/'scripts';scripts.mkdir()
        (scripts/'enumerate_image.py').write_text("import json\nprint(json.dumps({'ids':['"+OTHER+"']}))\n")
        fixture={'items':[{'id':OTHER,'publish_time_gmt8':'2026-10-17 01:00:00','desc':'local fixture','stats':dict.fromkeys(builder.STAT_KEYS,20)}],'errors':[],'policy':'tolerant'}
        (scripts/'fixture_scrape.py').write_text(
            "import json,sys\nfrom pathlib import Path\n"
            "out=Path(sys.argv[sys.argv.index('--out-dir')+1])\n"
            "data="+repr(fixture)+"\n"
            "p=out/'scrape_photoacc.json'\n"
            "old=json.loads(p.read_text()) if p.exists() else {'items':[]}\n"
            "data['items']=old['items']+data['items']\n"
            "p.write_text(json.dumps(data))\nprint(json.dumps(data))\n")
        wrapper=("import importlib.util,sys\nfrom pathlib import Path\n"
                 "spec=importlib.util.spec_from_file_location('real_image_pipeline',"+repr(str(ROOT/'pipeline/pipeline.py'))+")\n"
                 "p=importlib.util.module_from_spec(spec);spec.loader.exec_module(p)\n"
                 "p.PIPE_DIR=Path(__file__).parent\np.RUN_LOG=p.PIPE_DIR/'run.log'\np.STATE_FILE=p.PIPE_DIR/'state.json'\n"
                 "p.CFG.update({'python':sys.executable,'scrape':str(p.PIPE_DIR/'scripts/fixture_scrape.py'),'output_root':str(p.PIPE_DIR)})\n"
                 "sys.exit(p.main())\n")
        (self.root/'pipeline.py').write_text(wrapper)
        before=(self.video.read_bytes(),self.video.stat().st_mtime_ns)
        with patch.object(daily,'PIPE_DIR',self.root),patch.object(daily,'PY',sys.executable):
            result=daily.run_account(self.accs[1],self.cfg,'2026-10-17',self.root)
        self.assertEqual(result['status'],'ok',result)
        self.assertEqual(before,(self.video.read_bytes(),self.video.stat().st_mtime_ns))
        self.assertTrue(json.loads((self.root/'state.json').read_text(encoding='utf-8'))['photoacc|s2_scrape']['ok'])
        self.assertEqual(result['known'],2)
        self.assertFalse((self.root/'photo_audio').exists())
    def test_notes_are_not_counted_as_business_records(self):
        self.append(self.row())
        wb=openpyxl.load_workbook(self.image);ws=wb.create_sheet('说明');ws.append(['说明']);ws.append(['not a record']);wb.save(self.image);wb.close()
        self.assertEqual(app.ledger_overview(True)['summary']['rows'],1)
    def test_image_header_allows_trailing_empty_styled_columns(self):
        self.assertEqual(app._column_checks(builder.IMG_HEADER+[None],'image'),[])
    def test_enum_ignores_empty_entries_and_normalizes_ids(self):
        info={'entries':[None,{'id':int(VID)},{'id':VID},{'other':1}]}
        ydl=Mock();ydl.__enter__=Mock(return_value=ydl);ydl.__exit__=Mock(return_value=False)
        ydl.extract_info.return_value=info
        with patch.dict(sys.modules,{'tk_audio_download':SimpleNamespace(resolve_uid=lambda a:'fixture'),
                                    'yt_dlp':SimpleNamespace(YoutubeDL=lambda opts:ydl)}):
            self.assertEqual(enum.enumerate_recent('photoacc',40),[VID])
    def test_empty_sync_scope_cannot_expand_to_all_accounts(self):
        with patch.object(flow,'run') as run,patch.object(sys,'argv',['flow','--accounts']):
            self.assertEqual(flow.main(),1)
        run.assert_not_called()
    def test_image_only_daily_skips_global_product_monitor(self):
        with patch.object(daily,'load_cfg',return_value=self.cfg),patch.object(daily,'run_account',return_value={'status':'ok','anomalies':[]}),patch.object(daily,'run_hot_monitor') as hot,patch.object(daily,'write_reports',return_value='INFO'),patch.object(sys,'argv',['daily','--accounts','photoacc','--date','2026-10-17']):
            self.assertEqual(daily.main(),0)
        hot.assert_not_called()
    def fake_ydl(self,entries):
        ydl=Mock();ydl.__enter__=Mock(return_value=ydl);ydl.__exit__=Mock(return_value=False)
        ydl.extract_info.return_value={'entries':entries}
        factory=Mock(return_value=ydl)
        return factory,patch.dict(sys.modules,{'tk_audio_download':SimpleNamespace(resolve_uid=lambda a:'fixture'),
                                             'yt_dlp':SimpleNamespace(YoutubeDL=factory)})
    def test_date_enumeration_includes_gmt8_boundaries_and_ignores_40_limit(self):
        tz=dt.timezone(dt.timedelta(hours=8))
        stamp=lambda text:dt.datetime.fromisoformat(text).replace(tzinfo=tz).timestamp()
        entries=[{'id':str(3000000000000000000+n),'timestamp':stamp('2026-10-18 00:00:00')} for n in range(45)]
        entries += [{'id':VID,'timestamp':stamp('2026-10-17 00:00:00')},
                    {'id':OTHER,'timestamp':stamp('2026-10-17 23:59:59')},
                    {'id':'4234567890123456789','timestamp':stamp('2026-10-16 23:59:59')}]
        factory,p=self.fake_ydl(entries)
        with p:self.assertEqual(enum.enumerate_recent('photoacc',40,'2026-10-17','2026-10-17'),[VID,OTHER])
        self.assertNotIn('playlistend',factory.call_args.args[0])
    def test_missing_timestamp_fails_date_enumeration(self):
        _,p=self.fake_ydl([{'id':VID}])
        with p,self.assertRaises(ValueError):enum.enumerate_recent('photoacc',0,'2026-10-17','2026-10-17')
    def test_invalid_date_range_rejected_before_network(self):
        factory,p=self.fake_ydl([])
        for dates in [('bad','2026-10-17'),('2026-10-18','2026-10-17'),(None,'2026-10-17')]:
            with p,self.assertRaises(ValueError):enum.enumerate_recent('photoacc',0,*dates)
        factory.assert_not_called()
    def test_pipeline_passes_dates_and_full_enumeration(self):
        with patch.object(pipe.subprocess,'run',return_value=self.proc({'ids':[VID]})) as run:
            self.assertTrue(pipe._scrape_image(self.args,'fixture',self.root)[0])
        cmd=run.call_args.args[0]
        self.assertEqual(cmd[cmd.index('--limit')+1],'0')
        self.assertEqual(cmd[cmd.index('--from')+1],'2026-10-16')
        self.assertEqual(cmd[cmd.index('--to')+1],'2026-10-17')
    def test_full_refresh_includes_existing_ids_and_still_merges(self):
        self.args.scrape_incremental=False
        with patch.object(pipe.subprocess,'run',return_value=self.proc({'ids':[VID,OTHER]})),patch.object(pipe,'_subprocess_step',return_value=(True,'fixture')) as scrape:
            self.assertTrue(pipe._scrape_image(self.args,'fixture',self.root)[0])
        cmd=scrape.call_args.args[1]
        self.assertEqual(cmd[cmd.index('--ids')+1],VID+','+OTHER);self.assertIn('--merge',cmd)
    def test_successfully_enumerated_empty_window_passes_gate(self):
        (self.root/'scrape_photoacc.json').unlink()
        with patch.object(pipe.subprocess,'run',return_value=self.proc({'ids':[]})):
            ok,detail=pipe._scrape_image(self.args,'fixture',self.root)
        state={}
        with patch.object(pipe,'PIPE_DIR',self.root),patch.object(pipe,'_current_account','photoacc'):
            self.assertTrue(pipe._bookkeep('s2_scrape',ok,detail,0,state))
        self.assertEqual(json.loads((self.root/'scrape_photoacc.json').read_text())['items'],[])
        from gate import gate_scrape
        self.assertFalse(gate_scrape({'items':[],'errors':[]})[0])
    def test_image_scrape_does_not_resume_a_different_date_range(self):
        state={'photoacc|s2_scrape':{'ok':True,'date_range':['2026-10-01','2026-10-02']}}
        fn=Mock(return_value=(True,json.dumps({'items':[{'id':VID,'publish_time_gmt8':'2026-10-17 01:00:00'}]})))
        with patch.object(pipe,'_current_account','photoacc'),patch.object(pipe,'run_gates',return_value=True):
            self.assertTrue(pipe.run_step('s2_scrape',fn,self.args,state))
        fn.assert_called_once()
        self.assertEqual(state['photoacc|s2_scrape']['date_range'],['2026-10-16','2026-10-17'])
    def test_another_process_cannot_write_same_image_book(self):
        code=("import sys\nfrom pathlib import Path\n"
              "sys.path.insert(0,sys.argv[1])\nimport build_ledger_15col as b\n"
              "with b._writer_lock([Path(sys.argv[2])]):\n"
              " print('locked',flush=True)\n sys.stdin.readline()\n")
        before=self.image.read_bytes()
        proc=subprocess.Popen([sys.executable,'-u','-c',code,str(ROOT/'pipeline/scripts'),str(self.image)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding='utf-8')
        try:
            self.assertEqual(proc.stdout.readline().strip(),'locked')
            self.assertEqual(self.write(['photoacc']),1)
            self.assertEqual(before,self.image.read_bytes())
        finally:
            proc.communicate('release\n',timeout=10)
        self.assertEqual(self.write(['photoacc']),0)
    def test_failed_save_preserves_original_workbook(self):
        before=self.image.read_bytes()
        def failed(path):
            Path(path).write_bytes(b'partial write')
            raise OSError('fixture disk failure')
        with self.assertRaises(OSError):builder._save_ledger(SimpleNamespace(save=failed),self.image)
        self.assertEqual(before,self.image.read_bytes())
        self.assertEqual(list(self.root.glob('.ledger-*')),[])
    def test_direct_image_translation_export_keeps_video_queue(self):
        pending=self.root/'pending_trans.json';pending.write_text('{"videoacc":["keep"]}')
        before=pending.read_bytes()
        self.video.unlink()
        self.assertEqual(builder.main_export_trans(['photoacc'],self.cfg),0)
        self.assertEqual(pending.read_bytes(),before)

if __name__=='__main__':unittest.main(verbosity=2)
