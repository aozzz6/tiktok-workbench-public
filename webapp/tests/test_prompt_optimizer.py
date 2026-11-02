import copy
import asyncio
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import prompt_optimizer as po
from fastapi import FastAPI


class TestClient:
    """Minimal ASGI harness, avoiding optional httpx dependency."""
    def __init__(self, app):self.app=app
    def post(self, path, json):
        import json as codec
        messages=[]
        async def request():
            async def receive():return {'type':'http.request','body':codec.dumps(json).encode(),'more_body':False}
            async def send(message):messages.append(message)
            await self.app({'type':'http','asgi':{'version':'3.0'},'http_version':'1.1','method':'POST','scheme':'http','path':path,'raw_path':path.encode(),'query_string':b'','headers':[(b'content-type',b'application/json')],'server':('test',80),'client':('test',123),'root_path':''},receive,send)
        asyncio.run(request())
        class Response:
            status_code=next(m['status'] for m in messages if m['type']=='http.response.start')
            text=b''.join(m.get('body',b'') for m in messages).decode()
            def json(self):return codec.loads(self.text)
        return Response()


def payload(**overrides):
    return {"source":"15 秒，9:16。女子在窗边展示黑色运动夹克，德语口播：Schau dir diese Jacke an! 镜头从半身到袖口特写。", "product":"米白翻领针织上衣", "facts":"米白色，翻领，长袖；展示领口和袖口纹理。未知纤维成分与价格。",
            "mode":"text", "refs":"", "language":"德语", "duration":15, "clip_limit":15, "strategy":"compress", "ratio":"9:16", "model":"", **overrides}


def fixture(p=None):
    p=p or po.inputs(payload(), True)
    hooks=['自然穿搭揭示', '疑问开场试穿', '近看袖口针织', '衣柜搭配选择', '清晨出门倒叙', '与过去自己对话']
    scenes=['窗边日光卧室','咖啡馆门前街角','梳妆台旁镜子','衣柜内色彩排列','雨后门厅换鞋区','极简纯色摄影空间']
    stories=['整体效果然后细节', '问题再回答最后展示', '纹理引入拉远到全身', '两种下装比较并选择', '倒叙展示出门准备过程', '两位同一人物交替对白']
    looks=['窗边自然光，暖白色调','街角阴天冷调','镜前柔光，低饱和','衣柜内暖光，柔和阴影','雨后门厅冷调，逆光','纯色棚拍柔光，低饱和']
    cameras=['中景固定机位，浅景深','手持跟拍，中近景','固定机位缓推，近景','横移跟拍，中景','缓慢推近，中近景','过肩半身，固定机位']
    styles=['生活化纪实','街头快节奏','安静极简','轻快穿搭教程','暖调叙事','极简戏剧']
    sounds=['环境声：室内安静底噪；音效：衣物摩擦；台词：见口播；BGM：无']*6
    voices=['So trage ich meinen neuen Pullover heute am Fenster.', 'Wie kombinierst du helle Farben im Alltag?', 'Sieh dir die feine Struktur am Ärmel an.', 'Welche Hose passt heute zu meinem Look?', 'Mein Morgen beginnt mit einer kleinen Entscheidung.', 'Damals suchte ich lange. Heute wähle ich einfach.']
    return {"versions":[{"title":hooks[i],"difference":stories[i],"theme":"日常穿搭", "character":"年轻成年女性，米白翻领上衣", "look":looks[i],"camera":cameras[i],"sound":sounds[i],"hook":hooks[i],"scene":scenes[i],"story":stories[i],"style":styles[i],"warnings":[],"clips":[{"continuity":"同一人物、米白上衣与光线", "shots":[{"start":0,"end":d,"action":hooks[i]+'；'+stories[i]+'。人物正对镜头，嘴部可见。',"camera":cameras[i],"dialogue":voices[i],"beat":['hook','problem','demo','choice','reaction','twist'][i]}]} for d in p['segments']]} for i in range(6)]}


class Tests(unittest.TestCase):
    def test_shapes_and_prompts(self):
        p=po.inputs(payload(),True);vs=po.validate_versions(fixture(p),p)
        self.assertEqual([v['kind'] for v in vs],['优化版','B','B','B','C','C'])
        s=po.render_prompt(vs[0],vs[0]['clips'][0],p,0)
        for word in [po.POLICY,po.LIPSYNC,p['product'],'德语','15 秒','9:16']:self.assertIn(word,s)
        for word in ['【主体】','【场景环境】','【动作表演】','【运镜方式】','【光影氛围】','【风格质感】','【保持不变】','【音频】','【负向】',po.seedance.NEGATIVE]:
            self.assertIn(word,s)
        self.assertEqual(po.seedance.audit(s),[])

    def test_timing_modes(self):
        for duration in [4,10,15,15.1,16,30,31,60]:
            p=po.inputs(payload(duration=duration,strategy='split'),True)
            self.assertAlmostEqual(sum(p['segments']),duration)
            self.assertTrue(all(4<=n<=15 for n in p['segments']))
        p=po.inputs(payload(duration=40,strategy='compress',clip_limit=10),True)
        self.assertEqual(p['segments'],[10])
        with self.assertRaises(ValueError):po.inputs(payload(duration=5,clip_limit=4,strategy='split'),True)
        with self.assertRaises(ValueError):po.inputs(payload(duration=60,clip_limit=4,strategy='split'),True)

    def test_invalid_input(self):
        for kw in [dict(source=''),dict(product=''),dict(facts=''),dict(language=''),dict(mode='other'),dict(duration=float('nan')),dict(duration=True),dict(duration=61),dict(clip_limit=16),dict(strategy=''),dict(ratio=''),dict(mode='image',refs=''),dict(mode='reference',refs=''),dict(source='x'*24001)]:
            with self.subTest(kw=kw),self.assertRaises(ValueError):po.inputs(payload(**kw),True)

    def test_duplicate_versions(self):
        p=po.inputs(payload(),True)
        for mutate in [lambda d:d['versions'].pop(),lambda d:d['versions'].__setitem__(1,copy.deepcopy(d['versions'][0])), lambda d:d['versions'][1]['clips'][0]['shots'][0].update(dialogue=d['versions'][0]['clips'][0]['shots'][0]['dialogue'])]:
            d=fixture(p);mutate(d)
            with self.assertRaises(ValueError):po.validate_versions(d,p)

    def test_broken_timeline(self):
        p=po.inputs(payload(),True)
        for change in [dict(start=1),dict(end=0),dict(end=14),dict(dialogue=''),dict(dialogue=' '.join(['word']*60))]:
            d=fixture(p);d['versions'][0]['clips'][0]['shots'][0].update(change)
            with self.assertRaises(ValueError):po.validate_versions(d,p)

    def test_image_reference_rules(self):
        for mode in ['image','reference']:
            p=po.inputs(payload(mode=mode,refs='产品图用于外观'),True);v=po.validate_versions(fixture(p),p)[0]
            self.assertIn('产品图用于外观',po.render_prompt(v,v['clips'][0],p,0))

    def test_semantic_guards(self):
        p=po.inputs(payload(),True)
        for change in [dict(camera='袖口特写'),dict(action='手指展示纹理',camera='固定镜头'),dict(dialogue='Es ist weich und bequem.')]:
            d=fixture(p);d['versions'][0]['clips'][0]['shots'][0].update(change)
            with self.assertRaises(ValueError):po.validate_versions(d,p)
        d=fixture(p);d['versions'][4]['clips'][0]['shots'][0]['beat']='hook'
        with self.assertRaises(ValueError):po.validate_versions(d,p)

    def test_negative_facts_do_not_unlock_claims(self):
        """未确认 / 禁止 / 不是 等否定事实不得解锁柔软·舒适·耐用的口播表达。"""
        cases = [
            ('蓝色，圆领。柔软/舒适/耐用均未确认，禁止宣传。', 'Es ist weich und bequem.', True),
            ('蓝色，圆领。', 'Es ist weich und bequem.', True),
            ('Softness and comfort are not confirmed, do not claim them.', 'It is soft and comfortable.', True),
            ('Weichheit und Komfort sind nicht bestätigt.', 'Es ist weich und bequem.', True),
            ('蓝色，圆领。不是柔软的。', 'Es ist weich.', True),
            ('蓝色，圆领。面料柔软，穿着舒适。', 'Es ist weich und bequem.', False),
        ]
        for facts, line, blocked in cases:
            with self.subTest(facts=facts):
                p = po.inputs(payload(facts=facts), True)
                d = fixture(p)
                d['versions'][0]['clips'][0]['shots'][0]['dialogue'] = line
                if blocked:
                    with self.assertRaises(ValueError):
                        po.validate_versions(d, p)
                else:
                    po.validate_versions(d, p)

    def test_truncated_json_repaired(self):
        with patch.object(po.llm,'available',return_value=True),patch.object(po.llm,'chat',side_effect=[{'text':'{"broken":'}, {'text':json.dumps(fixture()),'model':'fake'}]) as chat:
            r=po.generate(payload());self.assertEqual(len(r['versions']),6);self.assertEqual(chat.call_count,2)

    def test_analysis_unknowns(self):
        d=po.validate_analysis(dict(summary='原文无明确语言',language='',ratio='',duration=None,replacement='替换产品',strengths=[],issues=[],changes=[],missing=['口播语言']))
        self.assertIsNone(d['duration']);self.assertEqual(d['language'],'')

    def test_request_budget(self):
        b=po.RequestBudget();b.before_request();b.note_failure(1);b.before_request()
        with self.assertRaises(po.llm.BudgetExceeded):b.before_request()

    def test_api_no_fake_success(self):
        app=FastAPI();app.include_router(po.router);client=TestClient(app)
        with patch.object(po.llm,'available',return_value=False):
            self.assertEqual(client.post('/api/prompt-optimizer/generate',json=payload()).status_code,503)
        self.assertEqual(client.post('/api/prompt-optimizer/generate',json=payload(language='')).status_code,400)
        with patch.object(po.llm,'available',return_value=True),patch.object(po.llm,'chat',side_effect=po.llm.LLMError('secret diagnostic')):
            r=client.post('/api/prompt-optimizer/generate',json=payload());self.assertEqual(r.status_code,502);self.assertNotIn('secret',r.text)

    def test_api_success_and_repair(self):
        app=FastAPI();app.include_router(po.router);client=TestClient(app)
        good={'text':json.dumps(fixture(),ensure_ascii=False),'model':'fake-test'}
        with patch.object(po.llm,'available',return_value=True),patch.object(po.llm,'chat',side_effect=[{'text':'{"versions":[]}'},good]) as chat:
            r=client.post('/api/prompt-optimizer/generate',json=payload());self.assertEqual(r.status_code,200,r.text);self.assertEqual(chat.call_count,2)
            self.assertEqual(len(r.json()['versions']),6);self.assertIn('嘴型',r.json()['versions'][0]['clips'][0]['prompt'])

    def test_busy_and_release(self):
        with patch.object(po.llm,'available',return_value=True):
            po._gate.acquire()
            try:
                with self.assertRaises(po.HTTPException) as ctx:po.analyze(payload())
                self.assertEqual(ctx.exception.status_code,409)
            finally:po._gate.release()
        self.assertTrue(po._gate.acquire(False));po._gate.release()


if __name__=='__main__':unittest.main(verbosity=2)
