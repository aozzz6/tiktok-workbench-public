/* Benchmark prompt optimizer: source + own product -> analysis -> six AI rewrites. */
(() => {
  'use strict';
  const KEY = 'wb.prompt.optimizer.v1';
  const LANGUAGE_KEY = 'wb.prompt.optimizer.last-language.v1';
  const LANGUAGES = ['德语','英语','法语','西班牙语','意大利语','葡萄牙语','中文','日语','韩语','印尼语','泰语','越南语','阿拉伯语'];
  const rememberedLanguage=()=>{try{const v=localStorage.getItem(LANGUAGE_KEY)||'';return v.length<=100?v:'';}catch(e){return '';}};
  const $ = s => document.querySelector(s);
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const clone = v => JSON.parse(JSON.stringify(v));
  const defaults = () => ({source:'',product:'',facts:'',refs:'',language:'',mode:'text',duration:'',clip_limit:'15',strategy:'',ratio:'',model:''});
  let state = {form:defaults(),analysis:null,result:null,history:[]}, busy=false, images=[], recovery='';
  let optimizerBase=null, validationAttempt=null;
  async function endpoint(){
    if(optimizerBase!==null)return optimizerBase;
    for(const base of ['', 'http://127.0.0.1:8791']){
      try{const r=await fetch(base+'/api/prompt-optimizer/profile',{signal:AbortSignal.timeout(3000)});if(r.ok){const d=await r.json();if(d.platform==='TikTok Symphony / Dreamina Seedance 2.0'){optimizerBase=base;return base;}}}catch(e){/* main app may still run an earlier version */}
    }
    throw Error('提示词优化接口尚未启动。请运行验收包中的启动脚本，或在日更结束后重启工作台。');
  }
  const fields = Object.keys(defaults());
  const importFields=['source','duration','ratio','refs','language','strategy'];
  const localAsset=(url,id)=>typeof url==='string'&&url.startsWith('/tk_reverse/'+id+'/')&&/^\/tk_reverse\/\d{6,30}\/[A-Za-z0-9_\u4e00-\u9fff./-]+$/.test(url)&&!url.split('/').includes('..');
  const isBenchmark=b=>!b||(b&&/^\d{6,30}$/.test(b.id)&&typeof b.source==='string'&&Number.isFinite(b.duration)&&Number.isInteger(b.shots)&&typeof b.audioNote==='string'&&localAsset(b.reportUrl,b.id)&&localAsset(b.sourceVideo,b.id)&&Array.isArray(b.warnings)&&b.warnings.every(v=>typeof v==='string')&&['references','clips'].every(k=>Array.isArray(b[k])&&b[k].every(v=>v&&localAsset(v.url,b.id)&&typeof v.label==='string')));
  const isForm=f=>f&&fields.every(k=>typeof f[k]==='string');
  const isResult=r=>!r||(r&&isForm(r.form)&&r.input&&['product','language'].every(k=>typeof r.input[k]==='string')&&typeof r.fingerprint==='string'&&Array.isArray(r.notes)&&r.notes.every(n=>typeof n==='string')&&Array.isArray(r.versions)&&r.versions.length===6&&r.versions.every(v=>v&&['title','difference','hook','scene','story','style','kind'].every(k=>typeof v[k]==='string')&&Array.isArray(v.warnings)&&v.warnings.every(w=>typeof w==='string')&&Array.isArray(v.clips)&&v.clips.length>0&&v.clips.every(c=>c&&typeof c.prompt==='string'&&Number.isFinite(c.duration)&&Array.isArray(c.shots)&&c.shots.every(s=>s&&typeof s.dialogue==='string'))));
  const isAnalysis=a=>!a||(a&&['summary','replacement','language','ratio'].every(k=>typeof a[k]==='string')&&['strengths','issues','changes','missing'].every(k=>Array.isArray(a[k])&&a[k].every(x=>typeof x==='string')));
  const isDraft=s=>s&&isForm(s.form)&&isResult(s.result)&&isBenchmark(s.result?.benchmark)&&isAnalysis(s.analysis)&&isBenchmark(s.benchmark)&&(!s.importUndo||(isForm(s.importUndo.form)&&isBenchmark(s.importUndo.benchmark)&&isAnalysis(s.importUndo.analysis)))&&Array.isArray(s.history)&&s.history.length<=5&&s.history.every(h=>h&&isForm(h.form)&&h.result&&isResult(h.result)&&isBenchmark(h.result.benchmark));
  function status(message, error=false) {validationAttempt=null;const el=$('#po-status');if(el){el.textContent=message;el.classList.toggle('error',error);}}
  function refreshValidation(){if(validationAttempt===null||busy||recovery)return;const full=validationAttempt;try{validate(full);status('输入已补齐，可以继续。');}catch(e){status(e.message,true);validationAttempt=full;}}
  function checkInputs(full){try{validate(full);return true;}catch(e){status(e.message,true);validationAttempt=full;return false;}}
  function useRememberedLanguage(){state.form.language=rememberedLanguage();state.languageOrigin=state.form.language?'preference':'';}
  function languageNote(){
    if(!state.form.language.trim())return '尚未选择。可先播放原片核对；没有口播时，选择你希望新增口播使用的语言。';
    const origins={source:'来自反推任务中用户确认的原片口播语言',manual:'你手动选择的语言',preference:'沿用你上次手选的语言，未识别本片音频',analysis:'根据提示词文字分析得到，未识别音频',draft:'当前草稿中的语言，请核对原片'};
    return `当前输出：${state.form.language}。${origins[state.languageOrigin]||origins.draft}。`;
  }
  function syncLanguage(){
    const v=state.form.language,custom=v&&!LANGUAGES.includes(v),sel=$('#po-language-choice');
    if(sel){sel.value=custom?'custom':v;$('#po-language-custom').hidden=!custom;}
    renderLanguageNote();
  }
  function renderLanguageNote(){
    $('#po-language-note').textContent=languageNote();
    const candidate=state.analysis?.language?.trim(),button=$('#po-use-analysis-language');
    button.hidden=!candidate||candidate===state.form.language;
    button.textContent=candidate?`采用文字分析建议：${candidate}`:'';
  }
  function rememberSelection(value){
    state.form.language=value;state.languageOrigin='manual';
    try{if(value.trim())localStorage.setItem(LANGUAGE_KEY,value.trim());else localStorage.removeItem(LANGUAGE_KEY);}catch(e){/* draft save reports storage failures */}
  }
  function durationPlan(){
    const total=Number(state.form.duration),limit=Number(state.form.clip_limit),strategy=state.form.strategy;
    if(!(total>=4&&total<=60&&limit>=4&&limit<=15))return {long:false,text:'填写目标时长和单段上限后，显示每个版本的生成计划。'};
    if(total<=limit)return {long:false,text:`每个版本生成 1 段，时长 ${total} 秒；无需压缩或拆段。共六个版本。`};
    if(!strategy)return {long:true,text:`目标 ${total} 秒超过单段 ${limit} 秒上限，请选择精简为一条，或拆段保留总时长。`};
    if(strategy==='compress')return {long:true,text:`每个版本精简为 1 段 ${limit} 秒（原目标 ${total} 秒），同步精简动作和口播。`};
    const count=Math.ceil(total/limit);
    if(count>4||total/count<4)return {long:true,error:true,text:'当前时长无法拆为最多 4 段、每段至少 4 秒，请调整目标时长或单段上限。'};
    const ticks=Math.round(total*10),segments=Array.from({length:count},(_,i)=>(Math.floor(ticks/count)+(i<ticks%count?1:0))/10);
    return {long:true,text:`每个版本拆为 ${count} 段：${segments.join(' + ')} 秒；共生成 ${6*count} 段提示词，分别生成视频后拼接。`};
  }
  function renderPlan(){const p=durationPlan();$('#po-strategy-wrap').hidden=!p.long;$('#po-duration-plan').textContent=p.text;$('#po-duration-plan').classList.toggle('po-warning',!!p.error);}
  function save() {
    if(recovery){status('旧草稿无法读取，已保留原数据。请先导出原始备份，再点击“新建”。',true);return;}
    try{localStorage.setItem(KEY,JSON.stringify(state));$('#po-saved').textContent='草稿已存本机';}
    catch(e){$('#po-saved').textContent='本机保存失败，请下载备份';}
  }
  function load() {
    let raw='';
    try{raw=localStorage.getItem(KEY)||'';if(!raw)return;const s=JSON.parse(raw);
      if(!isDraft(s))throw Error('bad draft');
      state=s;state.history=state.history.slice(0,5);
    }catch(e){recovery=raw;}
  }
  function requestBody(){return {...state.form,strategy:Number(state.form.duration)<=Number(state.form.clip_limit)?'compress':state.form.strategy,duration:Number(state.form.duration),clip_limit:Number(state.form.clip_limit)};}
  function fingerprint(){return JSON.stringify(state.form);}
  function stale(){return !!state.result&&state.result.fingerprint!==fingerprint();}
  function updateStale(){const el=$('#po-stale');if(el)el.textContent=stale()?'输入已修改。下面保留的是上次结果，请重新生成后再使用。':'';}
  async function request(url,body){
    const ctrl=new AbortController(),timer=setTimeout(()=>ctrl.abort(),280000);
    try{const base=url.startsWith('/api/prompt-optimizer/')?await endpoint():'';const r=await fetch(base+url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body),signal:ctrl.signal});
      const data=await r.json().catch(()=>({detail:`服务返回 ${r.status}，请检查服务是否更新并重启。`}));
      if(!r.ok||data.ok===false)throw Error(typeof data.detail==='string'?data.detail:data.reason||'请求未完成');return data;
    }finally{clearTimeout(timer);}
  }
  async function run(label,fn){
    if(busy)return;busy=true;$('#po-inputs').disabled=true;$('#po-settings').disabled=true;
    document.querySelectorAll('[data-po-job]').forEach(el=>el.disabled=true);status(label);
    try{await fn();}catch(e){status(e.name==='AbortError'?'等待超时。后台请求可能仍在完成，请稍后重试；输入和上次结果已保留。':e.message,true);}
    finally{busy=false;$('#po-inputs').disabled=false;$('#po-settings').disabled=false;document.querySelectorAll('[data-po-job]').forEach(el=>el.disabled=false);}
  }
  function syncForm(){fields.forEach(k=>{const el=$('[data-po-field="'+k+'"]');if(el)el.value=state.form[k];});modeHint();syncLanguage();renderPlan();renderBenchmark();}
  function renderBenchmark(){
    const host=$('#po-benchmark');if(!host)return;const b=state.benchmark;
    if(!b){host.innerHTML='';return;}
    if(!isBenchmark(b)){host.textContent='对标素材数据不完整，请重新导入。';return;}
    const picker=$('#po-report-select');if(picker&&Array.from(picker.options).some(o=>o.value===b.id))picker.value=b.id;
    host.innerHTML=`<div class="po-section-head"><h3>对标参考素材 · ${esc(b.id)}</h3>${state.importUndo?'<button type="button" class="btn" id="po-undo-import" data-po-job>撤销本次导入</button>':''}</div><p class="po-muted">${b.shots} 个镜头 · 原片 ${b.duration} 秒 · 仅参考构图、动作和运镜。右侧的“我的产品图”才用于识别你的产品。</p>
      ${state.form.source!==b.source?'<p class="po-warning">对标文字已手动调整；下列素材仍来自这份原始报告。</p>':''}
      <div class="po-benchmark-grid">${b.references.map(r=>`<figure><a href="${esc(r.url)}" target="_blank" rel="noopener"><img src="${esc(r.url)}" alt="${esc(r.label)}" loading="lazy"></a><figcaption>${esc(r.label)} · ${esc(r.time??'')} 秒<br>${esc(r.role||'参考动作')}</figcaption></figure>`).join('')}</div>
      <div class="po-actions"><a href="${esc(b.reportUrl)}" target="_blank" rel="noopener">打开原报告 ↗</a><a href="${esc(b.sourceVideo)}" target="_blank" rel="noopener">原视频 ↗</a>${b.clips.map(c=>`<a href="${esc(c.url)}" target="_blank" rel="noopener">${esc(c.label)} ↗</a>`).join('')}</div>
      <details><summary>播放原片，核对是否有口播及其语言</summary><video class="po-source-preview" controls preload="none" src="${esc(b.sourceVideo)}"></video><p class="po-muted">若无法播放，请使用上面的“原视频”链接。此处不会自动识别或转写音频。</p></details>
      <p class="po-warning">已调整：替换竞品，不锁定原人物；重写口播，不回挂旧原声；人物出镜说话，产品特写不承担口播。</p><p class="po-muted">${esc(b.audioNote)}</p>${b.warnings.map(w=>`<p class="po-warning">${esc(w)}</p>`).join('')}`;
  }
  async function loadReports(){
    const select=$('#po-report-select'),note=$('#po-report-note');note.textContent='正在读取已完成的原片还原报告…';
    try{const list=await PromptReportBridge.list();select.innerHTML='<option value="">选择一份原片还原报告</option>'+list.map(i=>`<option value="${esc(i.video_id)}">${esc(i.meta?.author||'本地视频')} · ${esc(i.video_id)} · ${esc(i.probe?.duration||i.meta?.duration||'?')} 秒</option>`).join('');if(state.benchmark)select.value=state.benchmark.id;note.textContent=list.length?`找到 ${list.length} 份报告。导入不调用 AI，也不会覆盖你的产品资料。`:'暂无已完成的原片还原报告，请先到“提示词反推”完成原片还原。';}
    catch(e){note.textContent=e.message;}
  }
  async function importReport(id){
    if(busy){status('请等待当前处理完成后再导入报告。',true);return;}
    await run('正在导入反推报告与对标素材…',async()=>{
      if(!id)throw Error('请先选择一份原片还原报告');
      const b=await PromptReportBridge.load(id);
      if(!isBenchmark(b))throw Error('报告素材数据不完整，未修改现有输入');
      state.importUndo={form:clone(state.form),benchmark:clone(state.benchmark||null),analysis:clone(state.analysis),languageOrigin:state.languageOrigin};
      state.benchmark=b;state.analysis=null;
      Object.assign(state.form,{source:b.source,refs:b.refs,ratio:b.ratio,language:'',strategy:'',duration:b.duration>=4&&b.duration<=60?String(Math.round(b.duration*10)/10):''});
      useRememberedLanguage();
      if(b.language){state.form.language=b.language;state.languageOrigin='source';}
      syncForm();renderAnalysis();save();updateStale();
      status('对标已导入，自己的产品资料和产品图保持不变。请核对口播语言与时长；可撤销本次导入。');
      $('#po-benchmark').scrollIntoView({behavior:'smooth',block:'nearest'});
    });
  }
  function modeHint(){const m=state.form.mode;$('#po-refs-wrap').hidden=m==='text';$('#po-mode-help').textContent={text:'只把最终提示词粘贴到平台；用文字描述你的产品与人物。',image:'在平台上传起始图片。提示词重点描述运动；人物出镜说话需要起始画面适合展示嘴部。',reference:'在平台上传产品图、视频等参考素材。下面逐项说明素材身份及用途，避免继承竞品和旧口播。'}[m]||'';}
  function renderAnalysis(){
    const a=state.analysis;const el=$('#po-analysis');if(!a){el.innerHTML='<p class="po-muted">先分析对标提示词，查看保留点、问题和替换产品的影响。</p>';return;}
    const list=(title,values)=>`<div><h4>${title}</h4><ul>${(values||[]).map(x=>`<li>${esc(x)}</li>`).join('')||'<li>无</li>'}</ul></div>`;
    el.innerHTML=`<p>${esc(a.summary)}</p><p><b>替换产品：</b>${esc(a.replacement)}</p><div class="po-analysis-grid">${list('值得保留',a.strengths)}${list('原文问题',a.issues)}${list('优化办法',a.changes)}${list('需补充 / 核对',a.missing)}</div><p class="po-muted">文字分析的语言建议：${esc(a.language||'未能确定')}（未识别音频） · 原时长：${esc(a.duration??'未识别')} · 比例：${esc(a.ratio||'未识别')}</p><p class="po-muted">${esc(languageNote())}</p>`;
  }
  async function analyze(){if(busy||!checkInputs(false))return;await run('正在分析原提示词和产品替换关系…',async()=>{
    const data=await request('/api/prompt-optimizer/analyze',{...requestBody(),language:''});state.analysis=data.analysis;
    if(data.analysis.language&&(!state.form.language||state.languageOrigin==='analysis')){state.form.language=data.analysis.language;state.languageOrigin='analysis';}
    if(!state.form.duration&&data.analysis.duration>=4&&data.analysis.duration<=60)state.form.duration=String(data.analysis.duration);
    if(!state.form.ratio)state.form.ratio=data.analysis.ratio||'';
    state.analysisSource=state.form.source;syncForm();renderAnalysis();save();updateStale();
    status('分析完成。请核对产品信息、原口播语言和时长设置，再生成六个版本。');
  });}
  function validate(full){
    if(!state.form.source.trim())throw Error('请粘贴或导入对标反推提示词');
    if(!state.form.product.trim()||!state.form.facts.trim())throw Error('请填写自己的产品名称和已确认的外观、功能等信息');
    if(full){
      if(!state.form.language.trim())throw Error('请选择口播语言；其他语言可选择“自定义语言”');
      if(!state.form.ratio)throw Error('请选择画面比例');
      if(!(Number(state.form.duration)>=4&&Number(state.form.duration)<=60))throw Error('请设置 4–60 秒目标总时长');
      if(!(Number(state.form.clip_limit)>=4&&Number(state.form.clip_limit)<=15))throw Error('请填写平台当前允许的单段时长上限（4–15 秒）');
      if(Number(state.form.duration)>Number(state.form.clip_limit)&&!state.form.strategy)throw Error('目标时长超出单段上限，请选择超长处理方式');
      const plan=durationPlan();if(plan.error)throw Error(plan.text);
      if(state.form.mode!=='text'&&!state.form.refs.trim())throw Error('请说明计划在生成平台上传的参考素材');
    }
  }
  async function generate(){if(busy||!checkInputs(true))return;await run('AI 正在改写六个版本并检查差异、口播长度和时间轴，通常需要 1–3 分钟…',async()=>{
    const fp=fingerprint(),data=await request('/api/prompt-optimizer/generate',requestBody());
    if(state.result)state.history.unshift({at:state.result.at,title:state.result.input.product,result:state.result,form:state.result.form});
    state.history=state.history.slice(0,5);state.result={...data,fingerprint:fp,form:clone(state.form),benchmark:clone(state.benchmark||null),at:new Date().toLocaleString()};
    save();renderResults();status('已生成 1 份优化版 + 3 份明显改编 + 2 份重新构思，并附自检报告。请核对需修改和需人工确认项。');
    $('#po-results').scrollIntoView({behavior:'smooth',block:'start'});
  });}
  const auditLabels={pass:'通过',needs_revision:'需修改',manual:'需人工确认'};
  const auditAxes={hook:'开头',scene:'场景',story:'叙事',style:'风格'};
  function validAudit(a){
    const status=s=>['pass','needs_revision','manual'].includes(s);
    const reasons=x=>Array.isArray(x)&&x.every(s=>typeof s==='string');
    return !!a&&a.schema_version===1&&status(a.status)&&!!a.summary&&!!a.rule_gate&&status(a.rule_gate.status)&&reasons(a.rule_gate.reasons)
      &&Array.isArray(a.versions)&&a.versions.length===6&&a.versions.every((v,i)=>v&&v.index===i&&typeof v.label==='string'&&status(v.status)&&Array.isArray(v.items)&&v.items.every(x=>x&&typeof x.title==='string'&&status(x.status)&&reasons(x.reasons)))
      &&Array.isArray(a.comparisons)&&a.comparisons.length===15&&a.comparisons.every(q=>q&&Number.isInteger(q.left)&&q.left>=0&&q.left<6&&Number.isInteger(q.right)&&q.right>q.left&&q.right<6&&status(q.status)&&Array.isArray(q.changed_axes)&&Number.isFinite(q.voice_similarity)&&Number.isFinite(q.action_similarity)&&reasons(q.reasons));
  }
  function auditBadge(status){return `<span class="po-check-badge po-check-${status}">${auditLabels[status]}</span>`;}
  function auditOverview(a){
    if(!validAudit(a))return '<section class="po-panel po-audit"><h3>提示词自检</h3><p class="po-muted">这份旧结果尚无有效自检报告。点击“重新自检”检查当前结果快照，无需重新生成。</p></section>';
    return `<section class="po-panel po-audit"><div class="po-section-head"><h3>六版自检总览</h3>${auditBadge(a.status)}</div><p>规则通过 ${esc(a.summary.rule_passed)} / 6 版 · 需修改 ${esc(a.summary.needs_revision)} 版 · 需人工确认 ${esc(a.summary.manual)} 版</p><p class="po-muted">本地规则自检 · ${esc(a.checked_at)} · 不新增模型调用。规则通过不等于事实、语言或成片效果已确认。</p><p class="${a.rule_gate.status==='needs_revision'?'po-warning':'po-muted'}">${esc(a.rule_gate.title)}：${a.rule_gate.reasons.map(esc).join('；')}</p><details class="po-audit-comparison"><summary>六版对比检查 · 15 组版本组合</summary><p class="po-muted">开头、场景、叙事、风格至少两项达到文字差异阈值；口播及动作分别比较。文字差异不等于创意实质差异，仍需人工确认。</p><div class="po-check-table-wrap"><table class="po-check-table"><thead><tr><th>版本组合</th><th>有差异的维度</th><th>口播 / 动作相似度</th><th>结论与原因</th></tr></thead><tbody>${a.comparisons.map(q=>`<tr><td>${esc(a.versions[q.left].label)} ↔ ${esc(a.versions[q.right].label)}</td><td>${q.changed_axes.map(k=>esc(auditAxes[k]||k)).join('、')||'无'}</td><td>${Math.round(q.voice_similarity*100)}% / ${Math.round(q.action_similarity*100)}%</td><td>${auditBadge(q.status)} ${q.reasons.map(esc).join('；')}</td></tr>`).join('')}</tbody></table></div></details></section>`;
  }
  function auditVersion(a,i){
    if(!validAudit(a))return '';
    const v=a.versions[i];return `<details class="po-version-check" ${v.status==='needs_revision'?'open':''}><summary>逐版自检 ${auditBadge(v.status)}</summary><ul>${v.items.map(x=>`<li>${auditBadge(x.status)} <strong>${esc(x.title)}</strong><p>${x.reasons.map(esc).join('；')}</p></li>`).join('')}</ul></details>`;
  }
  function auditMarkdown(a,i){
    if(!validAudit(a))return '自检：尚无有效报告，请在页面点击“重新自检”。';
    if(i!==undefined){const v=a.versions[i];return `**逐版自检：${auditLabels[v.status]}**\n`+v.items.map(x=>`- ${auditLabels[x.status]} · ${x.title}：${x.reasons.join('；')}`).join('\n');}
    return `### 六版自检总览\n结论：${auditLabels[a.status]}\n规则通过 ${a.summary.rule_passed}/6，需修改 ${a.summary.needs_revision}，需人工确认 ${a.summary.manual}。\n检查时间：${a.checked_at}\n本地规则检查，不新增模型调用；规则通过不代表事实、语言及成片效果已确认。\n原生成规则复核：${a.rule_gate.reasons.join('；')}\n\n### 六版对比检查\n`+a.comparisons.map(q=>`- ${a.versions[q.left].label} ↔ ${a.versions[q.right].label}：${auditLabels[q.status]}；差异维度 ${q.changed_axes.map(k=>auditAxes[k]||k).join('、')||'无'}；口播相似度 ${Math.round(q.voice_similarity*100)}%，动作相似度 ${Math.round(q.action_similarity*100)}%。${q.reasons.join('；')}`).join('\n');
  }
  async function recheckResult(){
    if(busy||!state.result)return;
    const r=state.result;
    await run('正在自检当前六版结果，不调用模型…',async()=>{
      const data=await request('/api/prompt-optimizer/self-check',{form:{...r.form,strategy:Number(r.form.duration)<=Number(r.form.clip_limit)?'compress':r.form.strategy,duration:Number(r.form.duration),clip_limit:Number(r.form.clip_limit)},versions:r.versions});
      if(!validAudit(data.self_check))throw Error('自检报告格式不完整，原结果已保留。');
      if(state.result!==r)return;
      r.self_check=data.self_check;save();renderResults();status(data.self_check.status==='needs_revision'?'自检完成：发现需修改项，请查看逐版报告和六版对比。':'自检完成：已覆盖规则通过；事实、语言和成片效果仍需人工确认。');
    });
  }

  function renderResults(){
    const r=state.result,el=$('#po-results');if(!r){el.innerHTML='';renderHistory();return;}
    el.innerHTML=`<div class="po-result-head"><div><h2>六个版本</h2><p class="po-muted">${esc(r.input.product)} · ${esc(r.input.language)}口播 · ${esc(r.at)} · 改写模型 ${esc(r.model)}</p></div><div class="po-actions"><button class="btn" id="po-recheck" data-po-job>重新自检</button><button class="btn" id="po-export-check">下载自检报告</button><button class="btn" id="po-export">下载全部提示词</button></div></div><p id="po-stale" class="po-warning"></p>${auditOverview(r.self_check)}
      <div class="po-cards">${r.versions.map((v,i)=>`<article class="po-version"><div class="po-version-top"><span class="po-badge">${i===0?'优化版':i<=3?'B · 明显改编':'C · 重新构思'}</span><h3>${esc(v.title)}</h3></div><p>${esc(v.difference)}</p><div class="po-axes"><span>开头：${esc(v.hook)}</span><span>场景：${esc(v.scene)}</span><span>叙事：${esc(v.story)}</span><span>风格：${esc(v.style)}</span></div>
      ${auditVersion(r.self_check,i)}${(v.warnings||[]).length?`<p class="po-warning">${v.warnings.map(esc).join('；')}</p>`:''}
      ${v.clips.map((c,j)=>`<details ${i===0&&j===0?'open':''}><summary>第 ${j+1}/${v.clips.length} 段 · ${esc(c.duration)} 秒 · 完整提示词</summary><pre>${esc(c.prompt)}</pre><div class="po-actions"><button class="btn primary" data-po-copy="${i}:${j}">复制本段提示词</button><button class="btn" data-po-voice="${i}:${j}">复制本段口播</button></div></details>`).join('')}</article>`).join('')}</div>
      <p class="po-muted">${(r.notes||[]).map(esc).join(' ')}</p>`;
    updateStale();renderHistory();
  }
  function renderHistory(){const el=$('#po-history');const n=state.history.length;el.hidden=!n;el.innerHTML=n?`<summary>上次结果（最近 ${n} 次）</summary>${state.history.map((h,i)=>`<button class="btn" data-po-restore="${i}" data-po-job>${esc(h.at)} · ${esc(h.title)}</button>`).join('')}`:'';}
  function download(name,content,type='text/plain;charset=utf-8'){const url=URL.createObjectURL(new Blob([content],{type}));const a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),3000);}
  function exportAll(){const r=state.result;if(!r)return;const body=[`# ${r.platform}\n产品：${r.input.product}\n生成时间：${r.at}`,stale()?'注意：当前输入已更改，下列为上次生成结果。':'',auditMarkdown(r.self_check),...r.versions.map((v,i)=>`## ${v.kind} · ${v.title}\n差异：${v.difference}\n\n${auditMarkdown(r.self_check,i)}\n\n${v.clips.map(c=>c.prompt).join('\n\n---\n\n')}`),...(r.notes||[])].join('\n\n');download('提示词优化_六个版本.md',body);}
  async function copy(text){try{await navigator.clipboard.writeText(text);status(stale()?'已复制上次结果；当前输入有变更，请留意。':state.result?.self_check?.status==='needs_revision'?'已复制，但自检发现需修改项，请核对报告后使用。':'已复制，可粘贴到视频生成平台。');}catch(e){status('复制受浏览器限制，请手动选择文字或下载提示词。',true);}}
  async function importText(file){if(!file)return;if(file.size>200000)throw Error('文本文件请小于 200 KB');if(!/\.(txt|md)$/i.test(file.name))throw Error('支持 .txt 或 .md 提示词');const s=await file.text();if(s.length>24000)throw Error('提示词最多 24000 字');state.form.source=s;state.analysis=null;state.benchmark=null;state.importUndo=null;if(['analysis','source'].includes(state.languageOrigin))useRememberedLanguage();syncForm();renderAnalysis();save();updateStale();refreshValidation();}
  async function compressImage(file){
    if(!/^image\/(png|jpeg|webp)$/.test(file.type)||file.size>12*1024*1024)throw Error('仅支持 12 MB 以内 PNG/JPEG/WebP 产品图');
    const url=URL.createObjectURL(file);try{const im=new Image();im.src=url;await im.decode();const scale=Math.min(1,1200/Math.max(im.width,im.height));const c=document.createElement('canvas');c.width=Math.max(1,Math.round(im.width*scale));c.height=Math.max(1,Math.round(im.height*scale));c.getContext('2d').drawImage(im,0,0,c.width,c.height);return {name:file.name,mime:'image/jpeg',data:c.toDataURL('image/jpeg',.85).split(',')[1]};}finally{URL.revokeObjectURL(url);}
  }
  function imageFeedback(message,error=false){const el=$('#po-image-feedback');el.textContent=message;el.classList.toggle('error',error);}
  function renderImages(){
    $('#po-image-count').textContent=images.length?`已选 ${images.length} / 4 张（可继续拖入，刷新后需重新选择）`:'尚未选择图片';
    $('#po-image-previews').innerHTML=images.map((im,i)=>`<div class="po-image-tile"><img src="data:image/jpeg;base64,${im.data}" alt="${esc(im.name)}"><span title="${esc(im.name)}">${esc(im.name)}</span><button type="button" class="btn" data-po-remove-image="${i}" aria-label="移除 ${esc(im.name)}">移除</button></div>`).join('');
    $('#po-image-review').hidden=true;
    imageFeedback('');
  }
  async function addImages(files){
    files=Array.from(files||[]);if(!files.length)return;
    if(busy){imageFeedback('正在处理，请完成后再添加图片。',true);return;}
    await run('正在准备产品图片…',async()=>{
      try{
        if(images.length+files.length>4)throw Error(`最多 4 张产品图，当前已有 ${images.length} 张；请先移除或减少本次图片。`);
        const next=[];
        for(const file of files)next.push(await compressImage(file));
        // Commit the batch only after all files decode, preserving earlier images on failure.
        images.push(...next);renderImages();imageFeedback(`已添加 ${next.length} 张，可点击“识别产品图”。`);status('产品图片已就绪，点击识别后核对建议。');
      }catch(e){imageFeedback(e.name==='EncodingError'?'图片无法读取，请换一张有效图片。':e.message,true);throw e;}
    });
  }
  // 品类自动补产品名称：只在用户没填时补，绝不覆盖手填内容。
  function adoptProductName(category){
    const name=clean(category);if(!name)return false;
    if(state.form.product.trim())return false;
    state.form.product=name.slice(0,150);const el=$('[data-po-field="product"]');if(el)el.value=state.form.product;
    save();updateStale();refreshValidation();return true;
  }
  function clean(v){const s=typeof v==='string'?v.trim():'';return s&&s!=='未知'?s:'';}
  function cleanList(v){return (Array.isArray(v)?v:[]).map(clean).filter(Boolean);}
  async function scanImages(){await run('正在识别产品图中的可见外观，识别结果需你确认…',async()=>{
    if(!images.length)throw Error('请先选择产品图片');const data=await request('/api/ps/product-scan',{images,hint:state.form.product});
    const p=data.product||{};
    const selling=cleanList(p.selling),colors=cleanList(p.colors);
    const lines=[clean(p.category),clean(p.subject),clean(p.material)?'可见材质描述：'+clean(p.material):'',colors.length?'颜色：'+colors.join('、'):'',clean(p.scene)?'可见场景：'+clean(p.scene):'',clean(p.audience)?'目标人群：'+clean(p.audience):'',...selling.map(x=>'可见卖点：'+x)].filter(Boolean);
    $('#po-image-suggestions').value=lines.join('\n');$('#po-image-review').hidden=false;
    const named=adoptProductName(p.category);
    imageFeedback([named?'产品名称已自动填入「'+state.form.product+'」':(state.form.product.trim()?'产品名称保留你已填的内容':'未识别出品类，产品名称请手填'),selling.length?'':'未识别出可验证卖点，请在产品事实里手填',lines.length?'':'未识别出可用外观信息，请换更清晰的产品图'].filter(Boolean).join('；'));
    status('图片识别完成。请删改推测内容，再追加到产品事实。');
  });}
  function mount(){
    const root=$('#prompt-optimizer');if(!root)return;load();
    if(!state.form.language&&!recovery)useRememberedLanguage();
    root.innerHTML=`<div class="po-banner"><div><span class="po-eyebrow">反推提示词 → 我的产品 → 六份创意</span><p>针对 TikTok Symphony · Dreamina Seedance 2.0。人物出镜口播，保留原语言并重写台词，不添加字幕。</p></div><a class="btn" href="https://ads.tiktok.com/creative/creativestudio/image-to-video" target="_blank" rel="noopener">打开生成平台 ↗</a></div>
      <div class="po-toolbar"><span id="po-saved" class="po-muted">草稿仅存本机</span><label class="btn po-file">恢复备份<input type="file" id="po-restore-file" accept=".json" hidden></label><button class="btn" id="po-backup">下载草稿备份</button><button class="btn" id="po-new" data-po-job>新建</button></div>
      <fieldset id="po-inputs"><div class="po-input-grid"><section class="po-panel"><div class="po-section-head"><h2>1. 对标反推提示词</h2><label class="btn po-file">导入 TXT / MD<input type="file" id="po-source-file" accept=".txt,.md" hidden></label></div><div class="po-report-picker"><label>从已有反推报告导入<select id="po-report-select"><option value="">正在读取报告…</option></select></label><div class="po-actions"><button type="button" class="btn" id="po-import-report" data-po-job>导入这份报告</button><button type="button" class="btn" id="po-refresh-reports" data-po-job>刷新列表</button></div><p id="po-report-note" class="po-muted"></p></div><textarea id="po-source" data-po-field="source" maxlength="24000" placeholder="把对标视频反推出来的提示词完整粘贴在这里，包括镜头、动作和原口播。" rows="13"></textarea><p class="po-muted">分析你提供的文字，不重新反推视频。原台词用于识别语言与意图，输出时重新创作。</p><div id="po-benchmark"></div></section>
      <div class="po-col-r">
      <section class="po-panel"><h2>2. 换成我的产品</h2><label>产品名称<input data-po-field="product" maxlength="150" placeholder="例如：米白色翻领针织上衣"></label><label>已确认的产品事实<textarea data-po-field="facts" maxlength="5000" rows="7" placeholder="外观、颜色、规格、真实功能、允许表达的卖点。未知材质、功效、价格请不要填写。"></textarea></label></section>
      <section class="po-panel po-imgpanel"><div class="po-section-head"><h2>产品图参考</h2><span class="po-muted">可选 · AI 辅助识别</span></div><details class="po-imgdetails"><summary>用产品图辅助填写</summary><p class="po-muted">图片压缩后交给工作台已配置的视觉模型识别；只辅助整理外观信息，不上传到 TikTok。刷新后需要重新选图。</p><button type="button" id="po-image-drop" aria-describedby="po-image-count po-image-feedback"><strong>将产品图片拖到这里</strong><span>或点击选择图片 · 支持多张追加</span><small>PNG / JPG / WebP · 最多 4 张 · 每张不超过 12 MB</small></button><input id="po-images" type="file" accept="image/png,image/jpeg,image/webp" multiple hidden><div id="po-image-previews"></div><p id="po-image-count" class="po-muted"></p><p id="po-image-feedback" class="po-muted" role="status" aria-live="polite"></p><button class="btn" id="po-scan" data-po-job>识别产品图</button><div id="po-image-review" hidden><label>核对后采用（图片无法证明材质成分或功效）<textarea id="po-image-suggestions" rows="5"></textarea></label><button class="btn" id="po-adopt">追加到产品事实</button></div></details><p class="po-muted po-imgfoot">图片仅用于整理外观信息，压缩后交给已配置的视觉模型，不上传到 TikTok。</p></section></div></div>
      <div class="po-actions"><button class="btn primary" id="po-analyze" data-po-job>分析对标与替换问题</button></div></fieldset>
      <section class="po-panel" id="po-analysis"></section>
      <fieldset id="po-settings" class="po-panel"><h2>3. 确认生成方式</h2><div class="po-settings-grid">
      <label>平台生成模式<select data-po-field="mode"><option value="text">文生视频 · 仅提示词</option><option value="image">图生视频 · 起始图片</option><option value="reference">参考生成 · 多图 / 视频等素材</option></select></label>
      <div><label>新视频口播语言<select id="po-language-choice" aria-describedby="po-language-note"><option value="">请选择语言</option>${LANGUAGES.map(v=>`<option>${v}</option>`).join('')}<option value="custom">自定义语言…</option></select></label><label id="po-language-custom" hidden>自定义语言<input data-po-field="language" maxlength="100" placeholder="例如：荷兰语、墨西哥西班牙语"></label><p id="po-language-note" class="po-muted" aria-live="polite"></p><button type="button" class="btn" id="po-use-analysis-language" hidden></button><small class="po-muted">记住最后一次手选；有原口播时请核对并保持原语言，台词仍会重新创作。</small></div>
      <label>画面比例<select data-po-field="ratio"><option value="">请确认</option><option>9:16</option><option>16:9</option><option>1:1</option></select></label>
      <label>目标总时长（秒，最多拆 4 段）<input data-po-field="duration" type="number" min="4" max="60" step="0.1" placeholder="4–60"></label>
      <label>入口可用单段上限（秒）<input data-po-field="clip_limit" type="number" min="4" max="15" step="0.1"></label>
      <label id="po-strategy-wrap">超长内容处理<select data-po-field="strategy"><option value="">请选择处理方式</option><option value="compress">压缩成一条 · 精简故事和台词</option><option value="split">拆为多段 · 分别生成后拼接</option></select></label></div><p id="po-duration-plan" class="po-muted" role="status"></p>
      <p id="po-mode-help" class="po-muted"></p><label id="po-refs-wrap" hidden>计划上传到平台的素材及用途<textarea data-po-field="refs" maxlength="3000" rows="3" placeholder="例如：产品正面图用于锁定颜色与结构；人物起始画面用于出镜；对标视频仅参考运镜与节奏。"></textarea></label>
      <details><summary>改写模型与平台说明</summary><label>负责分析和改写的 AI<select data-po-field="model" id="po-model"><option value="">工作台默认模型</option></select></label><p id="po-ai-state" class="po-muted">正在检查 AI 配置…</p><p class="po-muted">这里的 AI 负责写提示词；Seedance 2.0 在 TikTok 入口生成视频。官方单段范围为 4–15 秒，具体时长选项以你的账号界面为准。官方帮助曾限制含真人脸的参考素材，请核对当前入口能力。</p><p class="po-muted">结构借鉴 <a href="https://github.com/jnMetaCode/ai-shortfilm-prompts/blob/main/methodology.zh.md" target="_blank" rel="noopener">ai-shortfilm-prompts 方法论</a>；平台规则参考 <a href="https://ads.us.tiktok.com/resources/help/article/how-to-generate-videos-with-symphony-creative-studio" target="_blank" rel="noopener">TikTok 官方说明</a>（2026-10-27 核对）。</p></details></fieldset>
      <div class="po-actions"><button class="btn primary" id="po-generate" data-po-job>生成优化版 + 五个新版本</button><span class="po-muted">3 个明显改编 + 2 个重新构思 · 六份口播分别重写</span></div><p id="po-status" role="status" aria-live="polite"></p>
      <section id="po-results"></section><details id="po-history" class="po-panel"></details>`;
    syncForm();renderAnalysis();renderResults();renderImages();
    const drop=$('#po-image-drop');let dragDepth=0;
    const hasFiles=e=>Array.from(e.dataTransfer?.types||[]).includes('Files');
    drop.addEventListener('dragenter',e=>{if(!hasFiles(e))return;e.preventDefault();dragDepth++;if(!busy)drop.classList.add('is-dragging');});
    drop.addEventListener('dragover',e=>{if(!hasFiles(e))return;e.preventDefault();e.dataTransfer.dropEffect=busy?'none':'copy';});
    drop.addEventListener('dragleave',e=>{e.preventDefault();if(--dragDepth<=0){dragDepth=0;drop.classList.remove('is-dragging');}});
    drop.addEventListener('drop',e=>{e.preventDefault();e.stopPropagation();dragDepth=0;drop.classList.remove('is-dragging');if(!e.dataTransfer?.files.length){imageFeedback('请拖入本机图片文件；网页图片请先保存到本机。',true);return;}void addImages(e.dataTransfer.files);});
    root.addEventListener('dragover',e=>{if(hasFiles(e))e.preventDefault();});
    root.addEventListener('drop',e=>{if(hasFiles(e)){e.preventDefault();imageFeedback('请将图片放到“将产品图片拖到这里”区域。',true);}});
    root.addEventListener('input',e=>{const k=e.target.dataset.poField;if(k){state.form[k]=e.target.value;if(k==='language'){rememberSelection(e.target.value);renderLanguageNote();renderAnalysis();}if(k==='mode')modeHint();if(['duration','clip_limit','strategy'].includes(k))renderPlan();if(k==='source'){renderBenchmark();state.analysis=null;if(state.languageOrigin==='analysis'){useRememberedLanguage();syncLanguage();}renderLanguageNote();renderAnalysis();}save();updateStale();refreshValidation();}});
    root.addEventListener('change',async e=>{try{
      if(e.target.id==='po-language-choice'){
        const custom=e.target.value==='custom';rememberSelection(custom?'':e.target.value);$('[data-po-field="language"]').value=state.form.language;$('#po-language-custom').hidden=!custom;
        renderLanguageNote();renderAnalysis();save();updateStale();refreshValidation();if(custom)$('[data-po-field="language"]').focus();
      }
      if(e.target.id==='po-restore-file'){
        if(busy)throw Error('请等待当前请求完成后恢复备份');
        const f=e.target.files[0];if(!f)return;if(f.size>5000000)throw Error('备份不能超过 5 MB');const s=JSON.parse(await f.text());if(!isDraft(s))throw Error('备份结构不完整，当前草稿未改变');
        download('提示词优化_恢复前备份.json',recovery||JSON.stringify(state,null,2),'application/json');state=s;recovery='';images=[];renderImages();syncForm();renderAnalysis();renderResults();save();status('已恢复备份。产品图如需识别请重新选择。');
      }
      if(e.target.id==='po-source-file')await importText(e.target.files[0]);
      if(e.target.id==='po-images')await addImages(e.target.files);
    }catch(err){status(err.message,true);}finally{if(e.target.type==='file')e.target.value='';}});
    root.addEventListener('click',async e=>{const b=e.target.closest('button');if(!b)return;try{
      if(b.id==='po-analyze')await analyze();
      if(b.id==='po-import-report')await importReport($('#po-report-select').value);
      if(b.id==='po-refresh-reports')await loadReports();
      if(b.id==='po-undo-import'&&!busy&&state.importUndo){const u=state.importUndo;importFields.forEach(k=>state.form[k]=u.form[k]);state.languageOrigin=u.languageOrigin||'draft';state.benchmark=u.benchmark;state.analysis=u.analysis;state.importUndo=null;syncForm();renderAnalysis();save();updateStale();status('已恢复导入前的对标输入；自己的产品资料保留。');}
      if(b.id==='po-use-analysis-language'&&!busy&&state.analysis?.language){rememberSelection(state.analysis.language);syncLanguage();$('[data-po-field="language"]').value=state.form.language;renderAnalysis();save();updateStale();refreshValidation();}
      if(b.id==='po-generate')await generate();
      if(b.id==='po-scan')await scanImages();
      if(b.id==='po-image-drop'&&!busy)$('#po-images').click();
      if(b.dataset.poRemoveImage!==undefined&&!busy){images.splice(Number(b.dataset.poRemoveImage),1);renderImages();}
      if(b.id==='po-adopt'){const v=$('#po-image-suggestions').value.trim();if(v){state.form.facts=[state.form.facts,v].filter(Boolean).join('\n');syncForm();save();updateStale();status('已追加，请核对所有产品事实。');}}
      if(b.id==='po-backup')download('提示词优化_草稿备份.json',recovery||JSON.stringify(state,null,2),'application/json');
      if(b.id==='po-new'&&!busy){download('提示词优化_新建前备份.json',recovery||JSON.stringify(state,null,2),'application/json');state={form:defaults(),analysis:null,result:null,history:state.history};useRememberedLanguage();images=[];recovery='';renderImages();syncForm();renderAnalysis();renderResults();save();status('已新建；之前内容已下载备份，口播语言沿用上次手选。');}
      if(b.id==='po-recheck')await recheckResult();
      if(b.id==='po-export-check'&&state.result)download('提示词优化_自检报告.md',[`# 提示词自检报告\n产品：${state.result.input.product}\n口播语言：${state.result.input.language}\n生成时间：${state.result.at}`,stale()?'注意：当前输入已更改，本报告针对上次生成快照。':'',auditMarkdown(state.result.self_check),...state.result.versions.map((v,i)=>`## ${v.title}\n${auditMarkdown(state.result.self_check,i)}`)].join('\n\n'));
      if(b.id==='po-export')exportAll();
      if(b.dataset.poRestore!==undefined&&!busy){const h=state.history[Number(b.dataset.poRestore)];if(h?.result&&h.form){const previous=state.result;state.result=clone(h.result);state.form=clone(h.form);state.languageOrigin='draft';state.benchmark=clone(h.result.benchmark||null);state.importUndo=null;state.analysis=null;state.history.splice(Number(b.dataset.poRestore),1);if(previous)state.history.unshift({at:previous.at,title:previous.input.product,result:previous,form:previous.form});syncForm();renderAnalysis();renderResults();save();status('已恢复所选历史结果及其输入。');}}
      for(const key of ['poCopy','poVoice'])if(b.dataset[key]){const [i,j]=b.dataset[key].split(':').map(Number),c=state.result.versions[i].clips[j];await copy(key==='poCopy'?c.prompt:c.shots.map(s=>s.dialogue).filter(Boolean).join('\n'));}
    }catch(err){status(err.message,true);}});
    fetch('/api/ai/models').then(r=>r.json()).then(d=>{
      const list=d.models||[];$('#po-model').innerHTML='<option value="">工作台默认模型</option>'+list.map(m=>`<option value="${esc(m.id)}">${esc(m.name||m.id)}</option>`).join('');$('#po-model').value=state.form.model;
      $('#po-ai-state').textContent=d.available?'已有 AI 配置，点击分析/生成时调用；不会调用视频生成服务。':'尚无可用 AI，请先在工作台设置中配置模型。';
    }).catch(()=>{$('#po-ai-state').textContent='无法读取 AI 配置，请检查后端服务。';});
    if(recovery)status('草稿数据无法解析，原数据已保留；可下载原始备份。',true);
    window.PO={state:()=>state,requestBody,validate,exportAll,importReport};
    void loadReports();
    const params=new URLSearchParams(location.search),from=params.get('optimize_from');
    if(from){params.delete('optimize_from');const query=params.toString();history.replaceState(null,'',location.pathname+(query?'?'+query:'')+'#prompt');if(typeof window.switchView==='function')window.switchView('prompt');void importReport(from);}
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',mount);else mount();
})();
