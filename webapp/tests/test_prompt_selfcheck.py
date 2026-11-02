import copy
import json
import unittest
from unittest.mock import patch

from test_prompt_optimizer import po, payload, fixture, TestClient, FastAPI


def canonical(form=None):
    p=po.inputs(form or payload(), True)
    versions=po.validate_versions(fixture(p),p)
    for v in versions:
        for ci,c in enumerate(v['clips']): c['prompt']=po.render_prompt(v,c,p,ci)
    return versions


def report(versions=None, form=None):
    return po.self_check({'form':form or payload(),'versions':versions if versions is not None else canonical(form)})['self_check']


def item(r,code,index=0):
    return next(x for x in r['versions'][index]['items'] if x['code']==code)


class SelfCheckTests(unittest.TestCase):
    def test_generated_report_no_extra_model_request(self):
        with patch.object(po.llm,'available',return_value=True),patch.object(po.llm,'chat',return_value={'text':json.dumps(fixture()),'model':'fake'}) as chat:
            r=po.generate(payload())
        self.assertEqual(chat.call_count,1)
        self.assertEqual(r['self_check']['summary']['rule_passed'],6)
        self.assertEqual(r['self_check']['summary']['pairs'],15)
        self.assertEqual(r['self_check']['status'],'manual')

    def test_recheck_without_model_config_and_no_mutation(self):
        vs=canonical(); before=copy.deepcopy(vs)
        with patch.object(po.llm,'available',return_value=False),patch.object(po.llm,'chat',side_effect=AssertionError('must not call')):
            r=report(vs)
        self.assertEqual(vs,before)
        self.assertEqual(r['rule_gate']['status'],'pass')
        self.assertEqual(len(r['versions']),6)

    def test_every_version_has_concrete_manual_requirements(self):
        r=report()
        for v in r['versions']:
            self.assertEqual(v['status'],'manual')
            self.assertTrue({'facts_manual','language_manual','video_manual'} <= {x['code'] for x in v['items'] if x['status']=='manual'})

    def test_all_pairs_unique_and_complete(self):
        r=report()
        self.assertEqual({(q['left'],q['right']) for q in r['comparisons']},{(i,j) for j in range(6) for i in range(j)})
        self.assertTrue(all(len(q['changed_axes'])>=2 and q['status']=='pass' for q in r['comparisons']))

    def test_duplicate_versions_report_both_sides(self):
        vs=canonical();vs[1]=copy.deepcopy(vs[0]);r=report(vs)
        self.assertEqual(r['status'],'needs_revision')
        self.assertEqual(r['versions'][0]['status'],'needs_revision')
        self.assertEqual(r['versions'][1]['status'],'needs_revision')
        self.assertTrue(any(q['status']=='needs_revision' for q in r['comparisons']))

    def test_bad_timeline_identifies_version_and_shot(self):
        vs=canonical();vs[2]['clips'][0]['shots'][0]['start']=2;r=report(vs)
        self.assertEqual(item(r,'timeline',2)['status'],'needs_revision')
        self.assertIn('第 1 段第 1 镜',item(r,'timeline',2)['reasons'][0])

    def test_dense_speech_and_product_closeup(self):
        vs=canonical();vs[0]['clips'][0]['shots'][0].update(dialogue=' '.join(['word']*80),camera='产品特写')
        r=report(vs)
        self.assertEqual(item(r,'speech')['status'],'needs_revision')
        self.assertEqual(item(r,'framing')['status'],'needs_revision')

    def test_unconfirmed_claims_and_positive_facts(self):
        vs=canonical();vs[0]['clips'][0]['shots'][0]['dialogue']='Es ist weich und bequem.'
        r=report(vs, payload(facts='柔软和舒适未确认，禁止宣传。'))
        self.assertEqual(item(r,'claims')['status'],'needs_revision')
        r=report(vs,payload(facts='面料柔软，穿着舒适。'))
        self.assertEqual(item(r,'claims')['status'],'pass')

    def test_c_story_mechanism(self):
        vs=canonical();vs[4]['clips'][0]['shots'][0]['beat']='demo';r=report(vs)
        self.assertEqual(item(r,'new_story',4)['status'],'needs_revision')

    def test_final_prompt_changed_or_missing(self):
        vs=canonical();vs[0]['clips'][0]['prompt']+='添加竞品促销字幕'
        del vs[1]['clips'][0]['prompt'];r=report(vs)
        self.assertEqual(item(r,'final_prompt',0)['status'],'needs_revision')
        self.assertEqual(item(r,'final_prompt',1)['status'],'needs_revision')

    def test_uncovered_script_never_claims_speech_pass(self):
        vs=canonical();vs[0]['clips'][0]['shots'][0]['dialogue']='مرحبا بالعالم'
        r=report(vs)
        self.assertEqual(item(r,'speech')['status'],'manual')
        self.assertEqual(r['summary']['rule_passed'],5)

    def test_mixed_uncovered_script_also_requires_review(self):
        vs=canonical();vs[0]['clips'][0]['shots'][0]['dialogue']='Hello مرحبا بالعالم'
        self.assertEqual(item(report(vs),'speech')['status'],'manual')

    def test_invalid_beat_type_is_reported_without_server_error(self):
        vs=canonical();vs[0]['clips'][0]['shots'][0]['beat']=[]
        self.assertEqual(item(report(vs),'structure')['status'],'needs_revision')

    def test_model_warning_marked_unverified(self):
        vs=canonical();vs[0]['warnings']=['请核实材质'];r=report(vs)
        self.assertEqual(item(r,'model_warnings')['status'],'manual')
        self.assertIn('未独立核实',item(r,'model_warnings')['title'])

    def test_api_invalid_shapes_and_limits(self):
        app=FastAPI();app.include_router(po.router);client=TestClient(app)
        invalid=[{'form':payload(),'versions':[]},{'form':{},'versions':canonical()}, {'form':payload(),'versions':[None]*6}]
        vs=canonical();vs[0]['clips'][0]['shots']=[None];invalid.append({'form':payload(),'versions':vs})
        vs=canonical();vs[0]['title']='x'*250001;invalid.append({'form':payload(),'versions':vs})
        for body in invalid:
            with self.subTest(body=str(body)[:60]):self.assertEqual(client.post('/api/prompt-optimizer/self-check',json=body).status_code,400)

    def test_api_audits_revisions_without_failing_request(self):
        app=FastAPI();app.include_router(po.router);client=TestClient(app)
        vs=canonical();vs[0]['clips'][0]['shots'][0]['end']=14
        r=client.post('/api/prompt-optimizer/self-check',json={'form':payload(),'versions':vs})
        self.assertEqual(r.status_code,200)
        self.assertEqual(r.json()['self_check']['status'],'needs_revision')

    def test_split_clips_each_audited(self):
        r=report(form=payload(duration=30,strategy='split'))
        self.assertEqual(r['summary']['rule_passed'],6)
        self.assertEqual(item(r,'timeline')['status'],'pass')


if __name__=='__main__':unittest.main(verbosity=2)
