import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import reverse_voice as V
import tk_reverse as T
from fastapi import FastAPI
from fastapi import HTTPException
from test_prompt_optimizer import TestClient


class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name)
        self.patch=patch.object(T,'REVERSE_DIR',self.root);self.patch.start()
        self.vid='7701034631671516439';self.d=self.root/self.vid;self.d.mkdir()
        (self.d/'status.json').write_text('{"busy":true}',encoding='utf-8')
        (self.d/'report.json').write_text('{"original":true}',encoding='utf-8')
        app=FastAPI();app.include_router(V.router);self.client=TestClient(app)
    def tearDown(self):
        self.patch.stop();self.tmp.cleanup()
    def test_roundtrip_and_worker_isolation(self):
        self.assertEqual(V.read(self.vid)['voice']['state'],'unknown')
        r=self.client.post('/api/reverse-voice/'+self.vid,json={'state':'spoken','language':' 德语 '})
        self.assertEqual(r.status_code,200);self.assertEqual(V.read(self.vid)['voice']['language'],'德语')
        self.assertEqual(json.loads((self.d/'status.json').read_text()),{'busy':True})
        self.assertEqual(json.loads((self.d/'report.json').read_text()),{'original':True})
        self.assertFalse(list(self.d.glob('.voice-*')))
    def test_none_unknown_clear_language(self):
        for state in ['none','unknown']:
            r=self.client.post('/api/reverse-voice/'+self.vid,json={'state':state,'language':'德语'})
            self.assertEqual(r.json()['voice']['language'],'')
            self.assertEqual(V.read(self.vid)['voice']['state'],state)
    def test_invalid_input_does_not_overwrite(self):
        self.client.post('/api/reverse-voice/'+self.vid,json={'state':'spoken','language':'英语'})
        old=(self.d/'voice.json').read_bytes()
        for body in [{'state':'spoken','language':''},{'state':'guess','language':'de'},{'state':'spoken','language':4},{'state':'spoken','language':'x'*101}]:
            self.assertEqual(self.client.post('/api/reverse-voice/'+self.vid,json=body).status_code,400)
            self.assertEqual((self.d/'voice.json').read_bytes(),old)
    def test_missing_task_and_path_rejected(self):
        for vid in ['123456','../outside']:
            with self.assertRaises(HTTPException):V.read(vid)
        self.assertFalse((self.root/'123456').exists())
    def test_corrupt_data_not_silently_unknown(self):
        (self.d/'voice.json').write_text('broken',encoding='utf-8')
        with self.assertRaises(HTTPException) as caught:V.read(self.vid)
        self.assertEqual(caught.exception.status_code,409)
        self.assertEqual(self.client.post('/api/reverse-voice/'+self.vid,json={'state':'none'}).status_code,200)
    def test_report_snapshot(self):
        import reverse_restore as R
        report={'_assets':'restore_runs/test','shots':[],'prompts':[],'gallery':[],'audio':{},'voice':{'state':'spoken','language':'德语<script>'}}
        html=R.render_report(self.vid,report)
        self.assertIn('德语&lt;script&gt;',html)
        self.assertIn('data-source-voice',html)
        self.assertIn('reverse_voice_report.js',html)

if __name__=='__main__':unittest.main(verbosity=2)
