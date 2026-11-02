/* 隔离DOM测试；真实脚本和HTML，不调用生产API。 */
const fs = require('fs'), path = require('path'), assert = require('assert/strict');
const {JSDOM, VirtualConsole} = require('jsdom');
const root=path.resolve(__dirname,'..');
const source=fs.readFileSync(path.join(root,'static/app.js'),'utf8').replace(/\nboot\(\);/g,'\n');
const html=fs.readFileSync(path.join(root,'static/index.html'),'utf8');
let pass=0,fail=0;
const errors=[];
function setup(accounts=['alpha','beta','gamma']) {
  const vc=new VirtualConsole(); vc.on('jsdomError',e=>errors.push(e.message));
  const dom=new JSDOM(html,{runScripts:'outside-only',virtualConsole:vc}),w=dom.window;
  w.setInterval=()=>0;
  w.addEventListener('error',e=>errors.push(e.message));
  w.EventSource=class {constructor(){ w.lastStream=this; } close(){} };
  w.fetch=async()=>({ok:true,json:async()=>({id:'fixture',title:'fixture'})});
  w.eval(source+`\nwindow.audit={setConfig:x=>CFG=x,setGroups:x=>groupsData=x,progress:()=>runProgress,setRun:x=>currentRun=x};
    refreshActiveView=()=>{}; adoptLedgerSync=()=>{};`);
  w.audit.setConfig({accounts,agent_steps:[],local_steps:['s0_check'],step_labels:{}});
  w.fillAccountSelects(); w.renderSteps('#run-steps',['s0_check'],new Set(['s0_check']));
  w.document.querySelector('#run-from').value='2026-10-16';
  w.document.querySelector('#run-to').value='2026-10-17';
  const calls=[];
  w.fetch=async(u,o)=>{calls.push({url:u,body:o?.body&&JSON.parse(o.body)});return {ok:true,json:async()=>({id:'fixture',title:'fixture'})};};
  return {w,calls,close:()=>dom.window.close(),pick:a=>{w.document.querySelector(`#run-multi-list input[value="${a}"]`).checked=true;w.updateMultiCount();}};
}
async function test(name,fn){try{await fn();console.log('PASS '+name);pass++;}catch(e){console.error('FAIL '+name+' '+e.stack);fail++;}}
async function using(fn,accounts){const c=setup(accounts);try{await fn(c);}finally{c.close();}}
function emit(w,msg){w.lastStream.onmessage({data:JSON.stringify(msg)});}
(async()=>{
await test('无勾选沿用主下拉',()=>using(async({w,calls})=>{await w.submitRun();assert.equal(calls[0].body.account,'alpha');assert.equal(calls[0].body.accounts,undefined);}));
await test('单勾选替代默认账号',()=>using(async({w,calls,pick})=>{pick('beta');await w.submitRun();assert.equal(calls[0].body.account,'beta');assert.equal(calls[0].body.accounts,undefined);}));
await test('两勾选仅运行勾选且计数一致',()=>using(async({w,calls,pick})=>{pick('beta');pick('gamma');await w.submitRun();assert.deepEqual(calls[0].body.accounts,['beta','gamma']);assert.equal(w.document.querySelector('#run-multi-count').textContent,'已选 2');}));
await test('每日增量使用勾选子集',()=>using(async({w,calls,pick})=>{pick('beta');w.document.querySelector('#run-daily').checked=true;await w.submitRun();assert.deepEqual(calls[0].body,{kind:'daily',accounts:['beta']});}));
await test('全选与清空计数',()=>using(async({w})=>{w.setAllAccounts(true);assert.equal(w.getSelectedAccounts().length,3);w.setAllAccounts(false);assert.equal(w.getSelectedAccounts().length,0);}));
await test('空账号不启动pipeline/daily',()=>using(async({w,calls})=>{await w.submitRun();w.document.querySelector('#run-daily').checked=true;await w.submitRun();assert.equal(calls.length,0);assert.match(w.document.querySelector('#toast').textContent,/选择账号/);},[]));
await test('空步骤拒绝且可重试',()=>using(async({w,calls})=>{w.document.querySelectorAll('#run-steps .chip').forEach(x=>x.classList.remove('on'));await w.submitRun();assert.equal(calls.length,0);w.document.querySelector('#run-steps .chip').classList.add('on');await w.submitRun();assert.equal(calls.length,1);}));
await test('重复点击只发一个请求',()=>using(async({w,calls})=>{await Promise.all([w.submitRun(),w.submitRun()]);assert.equal(calls.length,1);}));
await test('启动失败后可重试',()=>using(async({w,calls})=>{const original=w.fetch;w.fetch=async()=>{throw Error('fixture failure');};await w.submitRun();assert.match(w.document.querySelector('#toast').textContent,/fixture failure/);w.fetch=original;await w.submitRun();assert.equal(calls.length,1);}));
await test('账号切换重置首账号的完成状态',()=>using(async({w})=>{w.buildRunProgress(['s0_check']);w.parseRunLine('[multi] (1/2) 账号: alpha');w.parseRunLine('[12:00:00] s0_check | ok=True cost=1s | 门禁:PASS');w.parseRunLine('[12:00:00] DONE | 全链完成');assert.doesNotMatch(w.document.querySelector('#run-progress-note').textContent,/全部/);w.parseRunLine('[multi] (2/2) 账号: beta');assert.equal(w.audit.progress().state.s0_check.status,'wait');assert.match(w.document.querySelector('#run-progress-note').textContent,/2\/2：beta/);}));
await test('全部账号结束才提示完成',()=>using(async({w})=>{w.buildRunProgress(['s0_check']);w.parseRunLine('[multi] (2/2) 账号: beta');w.parseRunLine('[multi] 全部账号完成');assert.match(w.document.querySelector('#run-progress-note').textContent,/全部账号完成/);}));
await test('断点跳过标记完成',()=>using(async({w})=>{w.buildRunProgress(['s0_check']);w.parseRunLine('[12:00:00] s0_check | 已完成，跳过（断点续跑）cost=1s');assert.equal(w.audit.progress().state.s0_check.status,'ok');}));
await test('失败终态收尾并提示未同步台账',()=>using(async({w})=>{w.buildRunProgress(['s0_check','s1_download']);w.attachRun('fixture',null,'pipeline');w.parseRunLine('[multi] (2/3) 账号: beta');w.parseRunLine('[12:00:00] s0_check | 开始: ...');emit(w,{status:'failed',exit_code:7,done:true});assert.equal(w.audit.progress().state.s0_check.status,'fail');assert.equal(w.audit.progress().state.s1_download.status,'skip');assert.match(w.document.querySelector('#run-progress-note').textContent,/未自动同步台账/);}));
await test('用户停止收尾',()=>using(async({w})=>{w.buildRunProgress(['s0_check']);w.attachRun('fixture',null,'pipeline');w.parseRunLine('[12:00:00] s0_check | 开始: ...');emit(w,{status:'stopped',exit_code:-9,done:true});assert.equal(w.audit.progress().state.s0_check.status,'skip');assert.match(w.document.querySelector('#run-progress-note').textContent,/已终止/);}));
await test('停止失败不伪报成功',()=>using(async({w})=>{w.audit.setRun('fixture');w.fetch=async()=>({ok:true,json:async()=>({stopped:false})});await w.stopCurrent();assert.match(w.document.querySelector('#toast').textContent,/未能终止/);}));
await test('停止网络错误有提示',()=>using(async({w})=>{w.audit.setRun('fixture');w.fetch=async()=>{throw Error('fixture offline');};await w.stopCurrent();assert.match(w.document.querySelector('#toast').textContent,/终止失败/);}));
await test('停止成功提示',()=>using(async({w})=>{w.audit.setRun('fixture');w.fetch=async()=>({ok:true,json:async()=>({stopped:true})});await w.stopCurrent();assert.equal(w.document.querySelector('#toast').textContent,'任务已终止');}));
function groups(w) {
  w.audit.setGroups({groups:[
    {id:'default',name:'默认分组',builtin:true,members:[{account:'alpha'}]},
    {id:'big',name:'大组',members:[{account:'alpha'},{account:'beta'},{account:'disabled'}],count:3},
    {id:'small',name:'小组',members:[{account:'gamma'}],count:1},
    {id:'empty',name:'空组',members:[],count:0},
    {id:'inactive',name:'停用组',members:[{account:'disabled'}],count:1}
  ]});
  w.renderGroupDetail();
}
await test('未选组提示且保留已有勾选',()=>using(async({w,pick})=>{groups(w);pick('beta');w.setAccountsByGroup();assert.match(w.document.querySelector('#toast').textContent,/请先/);assert.deepEqual(Array.from(w.getSelectedAccounts()),['beta']);}));
await test('选择小组不回落到最大组',()=>using(async({w})=>{groups(w);w.document.querySelector('#lane-small .lane-name').click();w.setAccountsByGroup();assert.deepEqual(Array.from(w.getSelectedAccounts()),['gamma']);assert.match(w.document.querySelector('#toast').textContent,/小组.*1/);}));
await test('最近点击覆盖之前选择并支持默认组',()=>using(async({w})=>{groups(w);w.document.querySelector('#lane-small .lane-name').click();w.document.querySelector('#lane-default .lane-name').click();w.setAccountsByGroup();assert.deepEqual(Array.from(w.getSelectedAccounts()),['alpha']);}));
await test('点击卡片控件同样记录组并过滤停用账号',()=>using(async({w})=>{groups(w);w.document.querySelector('#lane-big .ac-sel input').click();w.setAccountsByGroup();assert.deepEqual(Array.from(w.getSelectedAccounts()),['alpha','beta']);assert.equal(w.document.querySelector('#run-multi-count').textContent,'已选 2');assert.match(w.document.querySelector('#toast').textContent,/2 个账号/);}));
await test('空组与全停用组明确提示且不更改勾选',()=>using(async({w,pick})=>{groups(w);pick('beta');for(const id of ['empty','inactive']){w.document.querySelector('#lane-'+id+' .lane-name').click();w.setAccountsByGroup();assert.match(w.document.querySelector('#toast').textContent,/暂无可运行账号/);assert.deepEqual(Array.from(w.getSelectedAccounts()),['beta']);}}));
await test('泳道重绘保留最近点击组',()=>using(async({w})=>{groups(w);w.document.querySelector('#lane-small .lane-name').click();w.renderGroupDetail();w.setAccountsByGroup();assert.deepEqual(Array.from(w.getSelectedAccounts()),['gamma']);}));
await test('已选组删除后要求重新选择',()=>using(async({w})=>{groups(w);w.document.querySelector('#lane-small .lane-name').click();w.fetch=async()=>({ok:true,json:async()=>({groups:[]})});await w.loadGroups();w.setAccountsByGroup();assert.match(w.document.querySelector('#toast').textContent,/请先/);assert.equal(w.getSelectedAccounts().length,0);}));
await test('DOM运行无未捕获异常',async()=>assert.deepEqual(errors,[]));
console.log(`结果: ${pass} 通过 / ${fail} 失败`);process.exitCode=fail?1:0;
})();
