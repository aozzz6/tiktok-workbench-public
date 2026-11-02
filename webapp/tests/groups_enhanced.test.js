/* DOM + async regressions; NODE_PATH must contain the test-only jsdom package. */
const fs = require('fs');
const path = require('path');
const assert = require('assert');
const {JSDOM} = require('jsdom');
const root = path.resolve(__dirname,'..');
const code = fs.readFileSync(path.join(root,'static/app.js'),'utf8').replace(/\nboot\(\);/g,'\n');
const html = fs.readFileSync(path.join(root,'static/index.html'),'utf8').replace(/<script src=[^>]+><\/script>/,'');
let passed = 0;
const clone = x => JSON.parse(JSON.stringify(x));
const tick = async () => { for (let i=0;i<30;i++) await Promise.resolve(); };
async function test(name, fn) {
  const dom = new JSDOM(html,{runScripts:'dangerously',url:'http://localhost/'});
  const w = dom.window;
  const errors=[];
  w.addEventListener('error',e=>errors.push(e.message));
  w.setInterval=()=>0;
  w.eval(code+`\nwindow.audit={
    setup(d){groupsData=d;grpSelSet.clear();grpUndoStack=[];renderGroupTabs();renderGroupDetail();},
    api(fn){api=fn;}, select(a){grpSelSet.add(a);}, undo(){return grpUndoStack;},
    group(a){benchGroupAccounts=a;}, busy(){return grpOperationBusy;},
    data(){return groupsData;}, newOpen(){return grpNewOpen;}
  };`);
  const data={groups:[{id:'default',name:'默认分组',builtin:true,members:[{account:'alpha'},{account:'beta'}]},
                      {id:'target',name:'目标组',members:[]}]};
  const calls=[];
  const server=async (url,opts)=>{
    calls.push([url,opts]);
    if (!opts) return clone(data);
    const account=JSON.parse(opts.body).account;
    const gid=decodeURIComponent(url.split('/')[3]);
    data.groups.forEach(g=>g.members=g.members.filter(m=>m.account!==account));
    data.groups.find(g=>g.id===gid).members.push({account});
    return {ok:true};
  };
  w.audit.setup(clone(data));
  w.audit.api(server);
  try { await fn(w,{data,calls,server}); await tick(); assert.deepStrictEqual(errors,[]); console.log('PASS '+name); passed++; }
  finally { dom.window.close(); }
}
(async()=>{
  await test('right-click button executes actual inline handler',async(w,{data})=>{
    w.cardCtx({preventDefault(){},clientX:10,clientY:10},'alpha');
    w.document.querySelector('.ctx-i').click();await tick();
    assert(data.groups[1].members.some(m=>m.account==='alpha'));
  });
  await test('search assignment executes actual inline handler',async(w,{data})=>{
    w.toggleGrpSearch(true);w.document.querySelector('#grp-q').value='alp';w.renderGroupSearchPanel();
    w.document.querySelector('.qrow button').click();await tick();
    assert(data.groups[1].members.some(m=>m.account==='alpha'));
    assert(!w.document.querySelector('#grp-q-panel'));
  });
  await test('card select moves account and undo restores',async(w,{data})=>{
    const s=w.document.querySelector('.ac-to');s.value='target';s.dispatchEvent(new w.Event('change',{bubbles:true}));await tick();
    assert.strictEqual(data.groups[1].members.length,1);
    await w.grpUndo();assert.strictEqual(data.groups[1].members.length,0);
  });
  await test('partial batch failure refreshes and keeps successful undo',async(w,{data,server})=>{
    w.audit.api(async(u,o)=>{if(o && JSON.parse(o.body).account==='beta')throw Error('fixture failure');return server(u,o);});
    await w._moveAccounts(['alpha','beta'],'target');
    assert.strictEqual(data.groups[1].members.length,1);
    assert.strictEqual(w.audit.undo()[0].batch.length,1);
    assert(w.document.querySelector('#lane-target .acard'));
    await w.grpUndo();assert.strictEqual(data.groups[1].members.length,0);
  });
  await test('duplicate clicks share operation lock',async(w,{calls,server})=>{
    let release;const gate=new Promise(r=>release=r);
    w.audit.api(async(u,o)=>{if(o)await gate;return server(u,o);});
    const first=w._moveAccounts(['alpha'],'target');const second=w._moveAccounts(['alpha'],'target');
    release();await Promise.all([first,second]);assert.strictEqual(calls.filter(c=>c[1]).length,1);
  });
  await test('undo waits before refresh and failed items remain retryable',async(w,{data,server,calls})=>{
    await w._moveAccounts(['alpha','beta'],'target');calls.length=0;
    let release;const gate=new Promise(r=>release=r);
    w.audit.api(async(u,o)=>{if(o){await gate;if(JSON.parse(o.body).account==='beta')throw Error('undo failure');}return server(u,o);});
    const undo=w.grpUndo();await tick();assert.strictEqual(calls.length,0);
    release();await undo;assert.strictEqual(w.audit.undo()[0].batch[0].account,'beta');
    w.audit.api(server);await w.grpUndo();assert.strictEqual(data.groups[1].members.length,0);
  });
  await test('batch selector clears including members already in target',async(w,{data})=>{
    await w._moveAccounts(['alpha'],'target');w.audit.select('alpha');w.audit.select('beta');w.updateGrpSelUI();
    w.document.querySelector('#grp-batch-to').value='target';await w.batchMove();
    assert.strictEqual(data.groups[1].members.length,2);assert(!w.document.querySelector('.grp-batchbar'));
    await w.grpUndo();assert.strictEqual(data.groups[1].members.length,1);
  });
  await test('half-selection survives rerender; batch bar fixed at viewport bottom',async(w)=>{
    w.audit.select('alpha');w.renderGroupDetail();
    // 新排序下首列不定，直接定位 default 列（alpha 已选、beta 未选 → tri-state）
    assert(w.document.querySelector('.lane[data-gid="default"] .lane-sel input').indeterminate);
    // P1-8：批量条挂 body 固定视口底部（不再插入 #grp-detail 文档流 → 勾选零跳版）
    assert.strictEqual(w.document.querySelector('.grp-batchbar').parentNode, w.document.body);
  });
  await test('group single-account selection and empty subset request',async(w,{calls})=>{
    w.audit.group(['alpha','beta']);const s=w.document.querySelector('#bench-account');
    s.innerHTML='<option value="alpha">alpha</option>';s.value='alpha';
    w.renderBench=()=>{};await w.loadBench(false);
    assert.strictEqual(new URL(calls.at(-1)[0],'http://localhost').searchParams.get('accounts'),'alpha');
    w.audit.group([]);await w.loadBench(false);
    assert.strictEqual(new URL(calls.at(-1)[0],'http://localhost').searchParams.get('accounts'),'');
  });
  await test('group entry resets prior account filter',async(w)=>{
    const s=w.document.querySelector('#bench-account');s.innerHTML='<option value="alpha">alpha</option>';
    w.audit.data().groups[1].members=[{account:'beta'}];w.loadBench=()=>{};
    await w.openGroupBench('target');assert.strictEqual(s.value,'');
  });
  await test('draft and search survive refresh; empty query hides panel',async(w)=>{
    w.toggleGroupCreate(true);w.document.querySelector('#grp-new-name').value='draft';
    w.document.querySelector('#grp-q').value='alp';w.toggleGrpSearch(true);await w.loadGroups();
    assert.strictEqual(w.document.querySelector('#grp-new-name').value,'draft');assert.strictEqual(w.document.querySelector('#grp-q').value,'alp');
    w.document.querySelector('#grp-q').value='';w.renderGroupSearchPanel();assert(!w.document.querySelector('#grp-q-panel'));
  });
  await test('all existing regions have selectable language',async(w)=>{
    for(const [region,lang] of [['fr','fr'],['es','es'],['it','it'],['jp','jp'],['kr','kr']]){
      w.document.querySelector('#run-add-region').value=region;w.syncAddLang();assert.strictEqual(w.document.querySelector('#run-add-lang').value,lang);
    }
  });
  await test('translation always refreshes queue and reports Agent action',async(w,{calls})=>{
    w.audit.api(async(u,o)=>{calls.push([u,o]);return u.endsWith('backfill')?{note:'请交给 Agent'}:{total:0};});
    await w.backfillTranslation();assert(calls.some(c=>c[0]==='/api/translation/backfill' && c[1].method==='POST'));
    assert(w.document.querySelector('#toast').textContent.includes('Agent'));
  });
  await test('unknown metrics do not report healthy',async(w)=>{
    const s=w.groupMetricsHtml({group:{name:'X'},accounts:[{account:'alpha',videos:0,total_plays:0,viral:0,stale_days:null,tags:['无数据']} ]});
    assert(s.includes('数据不足'));assert(!s.includes('组健康：'));
  });
  console.log(`${passed} UI regressions passed`);
})().catch(e=>{console.error(e);process.exitCode=1;});
