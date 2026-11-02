/* 四步创作工作流。项目是产品事实、方案快照、分镜和导出的唯一数据来源。
 * PS 仅提供对标档案解析；PT 仅提供图像压缩/素材仓，不读取其旧生成结果。
 */
(function () {
  'use strict';
  if (window.CW) return;
  const KEY = 'wb.creative.projects.v1';
  const $ = s => document.querySelector(s);
  const esc = x => String(x == null ? '' : x).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const clone = x => JSON.parse(JSON.stringify(x));
  const uid = () => 'cw_' + Date.now().toString(36) + Math.random().toString(36).slice(2,9);
  const fields = [['product','产品名称'],['audience','目标受众'],['material','材质'],['structure','结构 / 规格'],['benefit','主卖点与可见证据'],['scene','拍摄场景'],['heroAction','演示动作'],['accent','颜色'],['sounds','现场声音'],['source','商品链接（仅记录来源）']];
  const labels = Object.fromEntries(fields);
  const angles = [
    {key:'result',name:'结果先行',opening:'先展示产品完整使用或穿着效果，再靠近细节。',why:'让观众先看到结果，再判断是否适合自己。'},
    {key:'detail',name:'细节证明',opening:'从可核对的材质或结构细节开始，随后展示整体。',why:'用看得见的证据说明卖点。'},
    {key:'routine',name:'日常代入',opening:'从真实使用场景开始，让产品自然进入动作。',why:'让观众把产品和自己的生活联系起来。'}
  ];
  let W = {schema:1,activeId:'',projects:[]}, mounted=false, busy=false;
  const current = () => W.projects.find(p=>p.id===W.activeId);
  const selected = () => {const p=current();return p && p.concepts.find(c=>c.id===p.selectedId);};
  const say = m => typeof window.toast === 'function' ? window.toast(m) : console.log(m);
  function blank() {return {id:uid(),product:Object.fromEntries(fields.map(([k])=>[k,''])),confirmed:false,revision:0,images:[],suggestions:{},sourceNotes:{},reference:'',duration:15,shots:5,concepts:[],selectedId:'',step:1};}
  function save() {
    try {localStorage.setItem(KEY,JSON.stringify(W));const el=$('#cw-save');if(el)el.textContent='已保存到本机';return true;}
    catch(e){const el=$('#cw-save');if(el)el.textContent='保存失败，请立即导出项目备份';say('本机存储不足：本次修改尚未保存，请导出项目备份');return false;}
  }
  function validProject(p) {
    const product=v=>v&&fields.every(([k])=>typeof v[k]==='string');
    const images=v=>Array.isArray(v)&&v.length<=4&&v.every(im=>im&&['key','name','mime','thumb'].every(k=>typeof im[k]==='string')&&(!im.thumb||/^data:image\/(jpeg|png|webp);base64,/.test(im.thumb)));
    return !!(p && typeof p.id==='string' && product(p.product) && images(p.images) && typeof p.confirmed==='boolean' && Number.isFinite(p.revision) && typeof p.reference==='string' && [1,2,3,4].includes(p.step) && p.duration>=3 && p.duration<=120 && Number.isInteger(p.shots) && p.shots>=3 && p.shots<=12 && p.sourceNotes && p.suggestions && Array.isArray(p.concepts) && p.concepts.length<=32 && p.concepts.every(c=>c && ['id','title','why','angle','change'].every(k=>typeof c[k]==='string') && product(c.product) && images(c.images) && Number.isFinite(c.revision) && Array.isArray(c.shots) && c.shots.length>0 && c.shots.length<=32 && c.shots.every(s=>s && Number.isFinite(s.duration) && s.duration>=0.1 && s.duration<=120 && ['role','action','camera','voice','image'].every(k=>typeof s[k]==='string'))));
  }
  function load() {
    let raw=localStorage.getItem(KEY);
    if(raw){try{const data=JSON.parse(raw);if(data.schema!==1||!Array.isArray(data.projects)||!data.projects.every(validProject))throw Error('项目结构不完整');W=data;}catch(e){say('项目数据无法读取，原数据保留；请先导出原始备份');W={schema:1,activeId:'',projects:[],recovery:raw};}}
    if(!W.projects.length){
      const p=blank();
      const old=window.PT && PT.state.prod;
      const v=window.PS && PS.state.v;
      const imported=old && old.edited && old.edited.product ? old.edited : v;
      // 没有旧保存记录时，不把程序默认的湿巾当成真实产品。
      if(imported && (localStorage.getItem('wb.pp.prod.v1') || localStorage.getItem('wb.po.vars.v1'))){
        fields.forEach(([k])=>{p.product[k]=String(imported[k]||'');});
        if(old && old.recog)p.product.audience=String(old.recog.audience||'');
        p.images=clone((old && old.images)||[]).map(imageMeta);
        p.migration='已从旧产品资料迁入，请核对后确认。旧数据仍保留。';
        if(old && v && v.product && old.edited && old.edited.product && v.product!==old.edited.product){p.migration+='检测到旧①和④名称不同，已采用④；可在下方恢复旧①为另一个项目。';p.legacyVars=clone(v);}
      }
      try{const voices=JSON.parse(localStorage.getItem('wb.po.voice.v1')||'[]');if(Array.isArray(voices))p.legacyVoice=voices.map(x=>typeof x==='string'&&!PS.isPresetVoice(x)?x:'');}catch(e){/* 旧口播无法解析时保留旧存储，其他资料照常迁移 */}
      W.projects.push(p);W.activeId=p.id;
    }
    if(!current())W.activeId=W.projects[0].id;
  }
  function imageMeta(im){return {key:String(im.key||''),name:String(im.name||'参考图.jpg'),mime:String(im.mime||'image/jpeg'),thumb:String(im.thumb||''),w:im.w||0,h:im.h||0};}
  function productChange(k,value){const p=current();p.product[k]=String(value);p.revision++;p.confirmed=false;p.sourceNotes[k]='人工填写';save();renderStatus();}
  function refs(){
    const a=(window.PS?PS.state.bench.list:[]).map(b=>({id:'archive:'+b.id,name:b.name,beats:b.beats.map(x=>({role:x.name||'演示',dur:x.t1-x.t0})),source:'对标档案'}));
    const t=(window.PT?PT.state.tpls:[]).map(t=>({id:'template:'+t.id,name:t.name,beats:(t.ratio||[]).map(x=>({role:x.role||'演示',dur:Number(x.dur)})),source:'历史模板'}));
    return a.concat(t);
  }
  function timeline(durations,span){
    if(!durations.length || durations.length>32 || durations.some(d=>!Number.isFinite(d)||d<=0))throw Error('参考拍序无效，请选择其他参考');
    const total=durations.reduce((a,b)=>a+b,0),ticks=Math.round(span*10);let at=0,sum=0;
    if(ticks<durations.length)throw Error('总时长不足');
    return durations.map((d,i)=>{sum+=d;const end=i===durations.length-1?ticks:Math.min(ticks-(durations.length-i-1),Math.max(at+1,Math.round(sum/total*ticks)));const n=(end-at)/10;at=end;return n;});
  }
  function buildShots(p,angle){
    const ref=refs().find(r=>r.id===p.reference);
    if(p.reference&&!ref)throw Error('所选参考已删除，请重新选择或使用通用结构');
    const roles=ref ? ref.beats.map(x=>x.role) : Array.from({length:p.shots},(_,i)=>i===0?'开场':i===p.shots-1?'收口':i===2?'细节':'演示');
    const durations=timeline(ref?ref.beats.map(x=>x.dur):roles.map(()=>1),p.duration);
    const apparel=/服装|衣|衫|裙|裤|针织|套装|clothing|shirt|dress|sweater/i.test(p.product.product);
    return roles.map((role,i)=>({id:uid(),role,duration:durations[i],locked:false,image:p.images[0]?p.images[0].key:'',
      action:i===0?angle.opening:i===roles.length-1?'停留在'+p.product.product+'的完整展示上，以自然动作结束。':/细节|微距|特写/.test(role)?'展示可核对的材质与结构：'+(p.product.material||'〔材质待补〕')+'；'+(p.product.structure||'〔结构待补〕')+'。':/证据|结果|效果/.test(role)?'保持相同光线，展示卖点证据：'+p.product.benefit+'。':(p.product.heroAction||(apparel?'穿着者自然转身，展示轮廓与衣物活动状态。':'在真实使用位置完成一次核心动作，按产品实际用法演示。')),
      camera:/细节|微距|特写/.test(role)?'缓慢靠近细节，保持对焦稳定。':apparel?'平视构图，跟随穿着者自然动作。':'以产品及操作动作为主体，机位稳定，按现场光线拍摄。',voice:'',voiceChecked:false}));
  }
  function generate(){
    const p=current();if(!p.confirmed){say('请先确认产品档案');go(1);return false;}
    if(p.concepts.length+3>30){say('当前项目不足三个方案空位（上限30份），请新建项目或先备份整理');return false;}
    try{
      const ref=refs().find(r=>r.id===p.reference);
      const batch=angles.map(a=>({id:uid(),title:a.name,angle:a.key,why:a.why,product:clone(p.product),revision:p.revision,images:clone(p.images),reference:ref?{id:ref.id,name:ref.name,source:ref.source}:null,shots:buildShots(p,a),created:new Date().toISOString(),parentId:'',change:'初始方案',approved:false}));
      p.concepts.push(...batch);save();renderConcepts();renderStatus();say('已生成三个方向，选定后编辑分镜');return true;
    }catch(e){say(e.message);return false;}
  }
  function scrollStart(){const el=$('#creative-workflow');if(el&&el.scrollIntoView)el.scrollIntoView({block:'start',behavior:'instant'});}
  function select(id){const p=current();if(!p.concepts.some(c=>c.id===id))return false;p.selectedId=id;p.step=3;save();render();scrollStart();return true;}
  function go(step){current().step=step;save();render();scrollStart();}
  function addVariant(kind){
    const p=current(),base=selected();if(!base)return say('先选择一份方案');if(p.concepts.length>=30)return say('最多保留30份方案，请先备份整理');
    const c=clone(base);c.id=uid();c.parentId=base.id;c.approved=false;c.created=new Date().toISOString();
    const slot=kind==='hook'?0:c.shots.findIndex((s,i)=>i>0&&i<c.shots.length-1&&!s.locked&&/证据|演示|结果|效果/.test(s.role));
    if(slot<0||c.shots[slot].locked)return say('对应镜头已锁定或不存在，请先解锁需要修改的镜头');
    if(kind==='hook'){
      const a=angles[(angles.findIndex(a=>a.key===base.angle)+1)%angles.length];c.shots[0].action=a.opening;c.angle=a.key;c.change='只换开场画面；其他镜头、口播、时长保持不变';
    }else if(kind==='benefit'){
      const value=($('#cw-variant-value').value||'').trim();if(!value)return say('先填写这次要测试的新卖点及证据');
      c.shots[slot].action='保持相同场景，展示另一项可核实证据：'+value;c.change='只替换第'+(slot+1)+'镜卖点画面（新证据需核实）';
    }else{
      const value=($('#cw-variant-value').value||'').trim();if(!value)return say('先填写这次要测试的演示动作');
      c.shots[slot].action=value;c.change='只替换第'+(slot+1)+'镜演示动作';
    }
    c.title=base.title+' · '+(kind==='hook'?'新开场':kind==='benefit'?'新卖点':'新演示');
    p.concepts.push(c);p.selectedId=c.id;save();render();say('已另存变体，原方案保留');return c;
  }
  function syncProduct(){
    const p=current(),c=selected();if(!c)return;
    if(!p.confirmed){say('请先核对并确认最新产品档案');go(1);return;}
    c.product=clone(p.product);c.images=clone(p.images);c.revision=p.revision;c.approved=false;c.needsReview=true;
    c.shots.forEach(s=>{if(!c.images.some(im=>im.key===s.image))s.image='';});
    save();render();say('最新产品资料已采用，分镜和口播原文保留；请逐镜核对并重新绑定缺少的图片');
  }
  function importLegacyVoice(){
    const p=current(),c=selected();if(!c)return;
    let count=0;(p.legacyVoice||[]).forEach((line,i)=>{const s=c.shots[i];if(line&&s&&!s.voice.trim()&&!s.locked){s.voice=line;count++;}});
    if(!count)return say('没有可填入的空白未锁定口播位');
    c.approved=false;c.needsReview=true;save();render();say('已按原序填入'+count+'句旧口播，未覆盖已有句子；请核对新镜头语义');
  }
  function editShot(index,key,value){
    const c=selected(),s=c&&c.shots[index];if(!s)return;
    if(s.locked&&key!=='locked')return;
    if(key==='duration'){const n=Number(value);if(!Number.isFinite(n)||n<0.1||n>120){say('单镜头时长应为0.1–120秒');return false;}value=Math.round(n*10)/10;}
    if(!['duration','role','action','camera','voice','image','locked'].includes(key))return;
    s[key]=value;if(key==='voice')s.voiceChecked=false;c.approved=false;save();renderStatus();renderExport();return true;
  }
  function issues(c){
    if(!c)return ['尚未选择方案'];const out=[];
    if(!current().confirmed)out.push('产品档案尚未确认');
    if(c.revision!==current().revision)out.push('产品档案已修改：此方案保留旧产品快照，请在分镜页显式采用最新资料并核对，或重新生成方向');
    if(c.needsReview)out.push('资料已更新，请在分镜页确认画面与口播已逐镜核对');
    if(!c.images.length)out.push('未附产品参考图');
    c.shots.forEach((s,i)=>{
      const n='第'+(i+1)+'镜：';
      if(!s.action.trim()||!s.camera.trim()||/〔[^〕]*〕/.test(s.action+s.camera))out.push(n+'画面或镜头信息待补');
      if(!s.voice.trim())out.push(n+'德语口播待补');
      else {const words=(s.voice.match(/[\p{L}\p{N}]+(?:['’-][\p{L}\p{N}]+)*/gu)||[]).length;if(words/s.duration>3)out.push(n+'口播偏长（约'+(words/s.duration).toFixed(1)+'词/秒），请试听或延长时长');}
      if(/beste|garantiert|100\s*%|Nr\.?\s*1/i.test(s.voice))out.push(n+'口播含需复核的宣传用词');
      if(!s.image||!c.images.some(im=>im.key===s.image))out.push(n+'未绑定有效参考图');
    });return out;
  }
  function sections(c){
    let cursor=0;
    return [
      {title:'创作方向',body:c.title+'\n目标受众：'+c.product.audience+'\n主卖点：'+c.product.benefit+'\n参考：'+(c.reference?c.reference.name:'通用结构')+'\n版本变化：'+c.change},
      {title:'产品与素材',body:'产品：'+c.product.product+'\n材质：'+c.product.material+'\n结构：'+c.product.structure+'\n颜色：'+c.product.accent+'\n来源链接：'+(c.product.source||'未填写')+'\n参考图：'+(c.images.map(x=>x.name).join('、')||'未附')+'\n外观以参考图和已确认的产品事实为准。'},
      {title:'场景与画质',body:'场景：'+c.product.scene+'\n9:16，真实自然光；保持产品外观和人物一致，不添加未经确认的效果或功能。'},
      {title:'镜头与声音',body:'按下列分镜制作，不强制一镜到底。现场声音：'+(c.product.sounds||'按实拍环境确定')+'。德语口播逐镜头对应；总时长 '+duration(c)+' 秒。'},
      {title:'逐镜头脚本',body:c.shots.map((s,i)=>{const start=cursor;cursor=Math.round((cursor+s.duration)*10)/10;const im=c.images.find(x=>x.key===s.image);return '### '+(i+1)+'｜'+start+'–'+cursor+' 秒｜'+s.role+'\n画面：'+s.action+'\n镜头：'+s.camera+'\n口播：'+(s.voice||'〔德语口播待补〕')+'\n参考图：'+(im?im.name:'〔参考图待绑定〕');}).join('\n\n')}
    ];
  }
  function duration(c){return Math.round(c.shots.reduce((n,s)=>n+s.duration,0)*10)/10;}
  function markdown(c){return '# '+c.product.product+' · '+c.title+'\n\n> '+(c.approved&&!issues(c).length?'已确认制作稿':'草稿，须完成检查')+' · '+c.id+'\n\n'+sections(c).map((s,i)=>'## '+(i+1)+'. '+s.title+'\n\n'+s.body).join('\n\n');}
  function renderStatus(){
    const p=current(),c=selected(),el=$('#cw-status');if(el)el.textContent=(p.confirmed?'产品已确认':'产品待确认')+' · '+p.concepts.length+'份方案'+(c?' · 当前：'+c.title+' / '+c.shots.length+'镜 / '+duration(c)+'s':'');
    const b=$('#cw-confirm');if(b)b.textContent=p.confirmed?'已确认，可选择方向':'确认产品档案';
    const gen=$('#cw-generate');if(gen)gen.disabled=!p.confirmed;
  }
  function renderProduct(){
    const p=current();$('#cw-product-fields').innerHTML=fields.map(([k,l])=>'<label>'+l+'<input data-cw-field="'+k+'" value="'+esc(p.product[k])+'" placeholder="'+(k==='source'?'可留空；不自动抓取网页':'按产品实际信息填写')+'"><small>'+esc(p.sourceNotes[k]||'待核对')+'</small></label>').join('');
    $('#cw-migration').textContent=p.migration||'先确认产品事实。识别结果作为建议，采用后再确认。';
    $('#cw-legacy-import').hidden=!p.legacyVars;
    $('#cw-images').innerHTML=p.images.map((im,i)=>'<figure><img src="'+esc(im.thumb)+'" alt="'+esc(im.name)+'"><figcaption>'+esc(im.name)+'</figcaption><button type="button" data-cw-image-remove="'+i+'" aria-label="移除 '+esc(im.name)+'">移除</button></figure>').join('');
    $('#cw-suggestions').innerHTML=Object.keys(p.suggestions||{}).filter(k=>labels[k]&&p.suggestions[k]).map(k=>'<div><b>'+esc(labels[k])+'</b> '+esc(p.suggestions[k])+' <button type="button" data-cw-adopt="'+k+'">采用建议</button></div>').join('')||'<p class="muted">没有待采用的识别建议。你也可以直接手填。</p>';
  }
  function renderReferences(){
    const select=$('#cw-reference');if(!select)return;const p=current();
    select.innerHTML='<option value="">不使用对标 · 通用结构</option>'+refs().map(r=>'<option value="'+esc(r.id)+'">'+esc(r.source+' · '+r.name)+' · '+r.beats.length+'镜</option>').join('');
    if(p.reference&&!refs().some(r=>r.id===p.reference))select.insertAdjacentHTML('beforeend','<option value="'+esc(p.reference)+'">原参考已删除，请重新选择</option>');
    select.value=p.reference;$('#cw-shot-count').value=p.shots;$('#cw-shot-count').disabled=!!p.reference;$('#cw-duration').value=p.duration;
  }
  function renderConcepts(){
    const p=current();$('#cw-concepts').innerHTML=p.concepts.map(c=>'<article class="cw-concept '+(p.selectedId===c.id?'selected':'')+'"><span class="muted">'+(c.parentId?'派生变体':'创意方向')+' · '+c.shots.length+'镜 / '+duration(c)+'s</span><h3>'+esc(c.title)+'</h3><p>受众：'+esc(c.product.audience)+'</p><p>主卖点：'+esc(c.product.benefit)+'</p><p>开场：'+esc(c.shots[0].action)+'</p><p class="muted">'+esc(c.why)+' '+esc(c.change)+'</p>'+(c.revision!==p.revision?'<p class="cw-warning">保留旧产品快照</p>':'')+'<button type="button" class="btn primary" data-cw-select="'+c.id+'">'+(p.selectedId===c.id?'继续编辑':'选择并编辑')+'</button></article>').join('')||'<p class="ps-empty">先确认产品，再生成三个创意方向。参考广告可选。</p>';
  }
  function renderEditor(){
    const c=selected();$('#cw-editor-empty').hidden=!!c;$('#cw-editor-body').hidden=!c;if(!c)return;
    $('#cw-selected-title').textContent=c.title+' · '+c.product.product;
    $('#cw-version').innerHTML=current().concepts.map(x=>'<option value="'+x.id+'">'+esc(x.title)+'</option>').join('');$('#cw-version').value=c.id;
    $('#cw-change').textContent=c.change+(c.parentId?'；原稿保留，可用版本选择框返回。':'');
    $('#cw-sync-product').disabled=c.revision===current().revision;
    $('#cw-reviewed').hidden=!c.needsReview;
    $('#cw-import-voice').hidden=!(current().legacyVoice||[]).some(Boolean);
    let t=0;$('#cw-shots').innerHTML=c.shots.map((s,i)=>{const start=t;t=Math.round((t+s.duration)*10)/10;return '<article class="cw-shot" data-shot="'+i+'"><header><b>镜头 '+(i+1)+' · <span data-cw-time="'+i+'">'+start+'–'+t+'s</span></b><label><input type="checkbox" data-cw-shot="locked" data-index="'+i+'" '+(s.locked?'checked':'')+'>锁定此镜头</label></header><div class="cw-shot-grid">'+[['role','角色'],['duration','时长（秒）'],['action','画面与动作'],['camera','镜头'],['voice','德语口播']].map(([k,l])=>'<label>'+l+(k==='duration'?'<input type="number" min="0.1" max="120" step="0.1"':k==='role'?'<input type="text"':'<textarea rows="3"')+' data-cw-shot="'+k+'" data-index="'+i+'" '+(s.locked?'disabled':'')+(k==='duration'||k==='role'?' value="'+esc(s[k])+'">': '>'+esc(s[k])+'</textarea>')+'</label>').join('')+'<label>参考图<select data-cw-shot="image" data-index="'+i+'" '+(s.locked?'disabled':'')+'><option value="">请选择参考图</option>'+c.images.map(im=>'<option value="'+esc(im.key)+'" '+(s.image===im.key?'selected':'')+'>'+esc(im.name)+'</option>').join('')+'</select></label></div></article>';}).join('');
  }
  function renderExport(){
    const c=selected(),list=issues(c);$('#cw-export-empty').hidden=!!c;$('#cw-export-body').hidden=!c;if(!c)return;
    $('#cw-export-title').textContent=c.title+' · '+c.shots.length+'镜 / '+duration(c)+'s';
    $('#cw-checks').innerHTML=list.length?'<b>待处理 '+list.length+' 项</b><ul>'+list.map(x=>'<li>'+esc(x)+'</li>').join('')+'</ul>':'<b>结构检查通过。请核对实物、德语表达并试听，再确认制作稿。</b>';
    $('#cw-approve').disabled=!!list.length;$('#cw-approve').textContent=c.approved?'已确认制作稿':'我已核对，确认制作稿';
    $('#cw-output').textContent=markdown(c);
  }
  function updateTimes(){const c=selected();if(!c)return;let t=0;c.shots.forEach((s,i)=>{const start=t;t=Math.round((t+s.duration)*10)/10;const el=$('[data-cw-time="'+i+'"]');if(el)el.textContent=start+'–'+t+'s';});}
  function render(){
    const p=current();if(!p)return;
    $('#cw-project').innerHTML=W.projects.map(x=>'<option value="'+x.id+'">'+esc(x.product.product||'未命名产品')+'</option>').join('');$('#cw-project').value=p.id;
    document.querySelectorAll('[data-cw-step]').forEach(el=>{el.hidden=Number(el.dataset.cwStep)!==p.step;});
    document.querySelectorAll('[data-cw-go]').forEach(el=>el.setAttribute('aria-current',Number(el.dataset.cwGo)===p.step?'step':'false'));
    renderProduct();renderReferences();renderConcepts();renderEditor();renderExport();renderStatus();
  }
  async function upload(files){
    if(busy)return;const p=current();busy=true;setBusy();
    try{PT.replaceProduct({id:p.id,images:clone(p.images),edited:clone(p.product),recog:null});await PT.addFiles(files);p.images=PT.state.prod.images.map(imageMeta);p.revision++;p.confirmed=false;save();render();}
    catch(e){say('图片添加失败：'+e.message);}finally{busy=false;setBusy();}
  }
  async function scan(){
    if(busy)return;const p=current();if(!p.images.length)return say('先添加产品图片');busy=true;setBusy();const ctl=new AbortController(),timer=setTimeout(()=>ctl.abort(),45000);
    try{
      const images=[];for(const im of p.images){const rec=await PT.getAsset(im.key);if(!rec||!rec.blob)throw Error('找不到图片 '+im.name+'，请重新上传');const data=await blobData(rec.blob);images.push({name:im.name,mime:im.mime,data:data.split(',')[1]});}
      const res=await fetch('/api/ps/product-scan',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({images,hint:p.product.product}),signal:ctl.signal});
      if(!res.ok)throw Error('HTTP '+res.status);const d=await res.json();if(!d.ok||!d.product)throw Error(d.reason||'未返回识别结果');const r=d.product;
      p.suggestions={product:String(r.category||r.subject||''),material:r.material==='未知'?'':String(r.material||''),scene:String(r.scene||''),benefit:Array.isArray(r.selling)?r.selling.join('；'):'',accent:Array.isArray(r.colors)?r.colors.join(' / '):'',audience:String(r.audience||'')};
      save();renderProduct();$('#cw-suggestions').closest('details').open=true;say('识别建议已列出，产品档案未被覆盖');
    }catch(e){say('识别未完成：'+(e.name==='AbortError'?'请求超时':e.message)+'。已填信息保留，可继续手填');}
    finally{clearTimeout(timer);busy=false;setBusy();}
  }
  function setBusy(){['#cw-project','#cw-new','#cw-upload','#cw-scan','#cw-restore','#cw-legacy-import'].forEach(s=>{const el=$(s);if(el)el.disabled=busy;});$('#cw-scan').textContent=busy?'处理中…':'识别图片（生成待确认建议）';}
  const blobData = blob => new Promise((resolve,reject)=>{const r=new FileReader();r.onload=()=>resolve(r.result);r.onerror=()=>reject(Error('读取图片失败'));r.readAsDataURL(blob);});
  function download(name,data,type){const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([data],{type:type||'text/plain;charset=utf-8'}));a.download=name;document.body.appendChild(a);a.click();setTimeout(()=>{URL.revokeObjectURL(a.href);a.remove();},1000);}
  async function copy(){const c=selected();if(!c)return;try{await navigator.clipboard.writeText(markdown(c));say('已复制当前方案');}catch(e){say('剪贴板不可用，请使用下载 Markdown');}}
  // 无压缩ZIP，包含当前稿件和绑定的实际图片；避免把“图片计数”当成交付素材。
  function zip(entries){
    const encoder=new TextEncoder(),parts=[],central=[];let offset=0;
    function crc(bytes){let n=0xffffffff;for(const b of bytes){n^=b;for(let i=0;i<8;i++)n=(n>>>1)^((n&1)?0xedb88320:0);}return (n^0xffffffff)>>>0;}
    for(const e of entries){const name=encoder.encode(e.name),data=typeof e.data==='string'?encoder.encode(e.data):e.data,check=crc(data);
      const h=new Uint8Array(30+name.length),v=new DataView(h.buffer);v.setUint32(0,0x04034b50,true);v.setUint16(4,20,true);v.setUint16(6,0x800,true);v.setUint32(14,check,true);v.setUint32(18,data.length,true);v.setUint32(22,data.length,true);v.setUint16(26,name.length,true);h.set(name,30);parts.push(h,data);
      const ch=new Uint8Array(46+name.length),cv=new DataView(ch.buffer);cv.setUint32(0,0x02014b50,true);cv.setUint16(4,20,true);cv.setUint16(6,20,true);cv.setUint16(8,0x800,true);cv.setUint32(16,check,true);cv.setUint32(20,data.length,true);cv.setUint32(24,data.length,true);cv.setUint16(28,name.length,true);cv.setUint32(42,offset,true);ch.set(name,46);central.push(ch);offset+=h.length+data.length;
    }
    const end=new Uint8Array(22),v=new DataView(end.buffer),size=central.reduce((n,b)=>n+b.length,0);v.setUint32(0,0x06054b50,true);v.setUint16(8,entries.length,true);v.setUint16(10,entries.length,true);v.setUint32(12,size,true);v.setUint32(16,offset,true);return new Blob(parts.concat(central,[end]),{type:'application/zip'});
  }
  async function exportZip(){
    const c=selected();if(!c)return;const snapshot=clone(c),entries=[{name:'完整提示词.md',data:markdown(snapshot)},{name:'逐镜头提示词.md',data:sections(snapshot)[4].body},{name:'德语口播.txt',data:snapshot.shots.map((s,i)=>(i+1)+'. '+(s.voice||'〔待补〕')).join('\n')},{name:'方案.json',data:JSON.stringify(snapshot,null,2)}];
    const missing=[],mapping=[];
    for(let i=0;i<snapshot.images.length;i++){const im=snapshot.images[i];try{const rec=await PT.getAsset(im.key);if(!rec||!rec.blob)throw Error('missing');const name='参考图/'+(i+1)+'.jpg';entries.push({name,data:new Uint8Array(await rec.blob.arrayBuffer())});mapping.push(im.name+' → '+name);}catch(e){missing.push(im.name);}}
    if(missing.length){say('无法导出完整制作包：缺少 '+missing.join('、')+'。请补传图片；文字稿仍可单独下载。');return false;}
    entries.push({name:'制作说明.txt',data:'使用当前选中方案，未另行生成内容。\n'+issues(snapshot).join('\n')+'\n\n参考图映射：\n'+mapping.join('\n')+'\n图片为上传时压缩的制作参考图；请在视频工具中按镜头绑定。'});
    download((snapshot.approved?'制作稿':'草稿')+'-'+snapshot.id+'.zip',zip(entries),'application/zip');say('已导出当前方案制作包');return true;
  }
  function mount(){
    if(mounted||!$('#creative-workflow')||!window.PS||!window.PT)return;
    PS_load();PT_load();load();mounted=true;
    $('#creative-workflow').innerHTML=`<div class="cw-toolbar"><label>产品项目 <select id="cw-project"></select></label><button type="button" class="btn" id="cw-new">新建产品</button><button type="button" class="btn" id="cw-backup">备份项目</button><button type="button" class="btn" id="cw-restore">导入项目备份</button><input type="file" id="cw-backup-file" accept=".json" hidden><span id="cw-save" role="status">本机项目</span></div>
      <nav class="cw-steps" aria-label="创作流程">${['建产品','选方向','编辑分镜','导出制作包'].map((x,i)=>'<button type="button" data-cw-go="'+(i+1)+'">'+(i+1)+' · '+x+'</button>').join('')}</nav><p id="cw-status" role="status"></p>
      <section class="panel" data-cw-step="1"><h2>① 建产品 · 一份资料，所有方案共用</h2><p id="cw-migration" class="muted"></p><button class="btn" type="button" id="cw-legacy-import" hidden>将旧①资料另存为项目</button><div class="cw-row"><button type="button" class="btn" id="cw-upload">上传产品图（最多4张）</button><input type="file" id="cw-file" accept="image/*" multiple hidden><button type="button" class="btn" id="cw-scan">识别图片（生成待确认建议）</button></div><div id="cw-images" class="cw-images"></div><div id="cw-product-fields" class="cw-fields"></div><details><summary>识别建议 · 采用后仍需核对</summary><div id="cw-suggestions"></div></details><div class="cw-row"><button type="button" class="btn primary" id="cw-confirm">确认产品档案</button><span class="muted">名称、受众、主卖点、场景必填；其他事实按实物核对。</span></div></section>
      <section class="panel" data-cw-step="2"><h2>② 选方向 · 先确定广告讲什么</h2><div class="cw-fields"><label>主参考（可选，仅借结构）<select id="cw-reference"></select></label><label>总时长（秒）<input type="number" id="cw-duration" min="3" max="120" step="0.1"></label><label>通用结构镜头数<input type="number" id="cw-shot-count" min="3" max="12" step="1"></label></div><p class="muted">只使用一条主参考；不会把不同广告按拍数平均混合。创意卡为规则生成的草稿，请自行选择和修改。</p><details id="cw-library"><summary>参考资料库 · 导入反推报告或管理旧档案</summary><div id="cw-library-host"></div></details><button type="button" class="btn primary" id="cw-generate">生成三个创意方向</button><div id="cw-concepts" class="cw-concepts"></div></section>
      <section class="panel" data-cw-step="3"><h2>③ 编辑分镜 · 画面、口播、时间和图片对齐</h2><p id="cw-editor-empty">先在②选择一份方向。</p><div id="cw-editor-body"><div class="cw-row"><h3 id="cw-selected-title"></h3><label>切换方案 <select id="cw-version"></select></label><button type="button" class="btn" id="cw-sync-product">采用最新产品资料（保留分镜）</button><button type="button" class="btn" id="cw-import-voice" hidden>填入旧手写口播（仅空句）</button><button type="button" class="btn" id="cw-reviewed" hidden>我已核对各镜头与口播</button></div><p id="cw-change" class="muted"></p><div class="cw-variants"><b>另存变体，原稿保留</b><input id="cw-variant-value" placeholder="换卖点或动作时，在此填写新内容"><button type="button" class="btn" data-cw-variant="hook">只换开头</button><button type="button" class="btn" data-cw-variant="benefit">只换卖点画面</button><button type="button" class="btn" data-cw-variant="demo">只换演示动作</button></div><div id="cw-shots"></div><button type="button" class="btn primary" data-cw-go="4">检查并导出当前方案</button></div></section>
      <section class="panel" data-cw-step="4"><h2>④ 导出制作包 · 内容来自当前选中方案</h2><p id="cw-export-empty">先选择并编辑方案。</p><div id="cw-export-body"><h3 id="cw-export-title"></h3><div id="cw-checks" class="cw-checks"></div><button type="button" class="btn" id="cw-approve">我已核对，确认制作稿</button><p class="muted">检查是结构和语速估算，不替代实物核对、德语审校与试听。未完成时仍可下载带草稿标记的文件。</p><div class="cw-row"><button type="button" class="btn primary" id="cw-zip">下载制作包 ZIP</button><button type="button" class="btn" id="cw-markdown">下载完整提示词</button><button type="button" class="btn" id="cw-copy">复制当前提示词</button></div><details><summary>五段式导出预览（同一份稿件）</summary><pre id="cw-output"></pre></details></div></section>`;
    const library=$('#ps-sec-bench');if(library){$('#cw-library-host').appendChild(library);library.querySelector('h2').childNodes[0].textContent='对标档案 ';}
    $('#cw-new').onclick=()=>{if(busy)return;const p=blank();W.projects.push(p);W.activeId=p.id;save();render();};
    $('#cw-project').onchange=e=>{if(busy)return;W.activeId=e.target.value;save();render();};
    $('#cw-confirm').onclick=()=>{const p=current();if(['product','audience','benefit','scene'].some(k=>!p.product[k].trim()))return say('请填写产品名称、受众、主卖点和场景');p.confirmed=true;save();go(2);};
    $('#cw-generate').onclick=generate;$('#cw-version').onchange=e=>select(e.target.value);
    $('#cw-sync-product').onclick=syncProduct;$('#cw-import-voice').onclick=importLegacyVoice;$('#cw-reviewed').onclick=()=>{const c=selected();if(c){c.needsReview=false;save();render();}};
    $('#cw-reference').onchange=e=>{current().reference=e.target.value;save();renderReferences();};
    $('#cw-duration').onchange=e=>{const n=Number(e.target.value);if(!Number.isFinite(n)||n<3||n>120){say('总时长应为3–120秒');e.target.value=current().duration;return;}current().duration=Math.round(n*10)/10;save();};
    $('#cw-shot-count').onchange=e=>{const n=Number(e.target.value);if(!Number.isInteger(n)||n<3||n>12){say('镜头数应为3–12');e.target.value=current().shots;return;}current().shots=n;save();};
    $('#cw-upload').onclick=()=>$('#cw-file').click();$('#cw-file').onchange=e=>{upload(e.target.files);e.target.value='';};$('#cw-scan').onclick=scan;
    $('#cw-copy').onclick=copy;$('#cw-markdown').onclick=()=>{const c=selected();if(c)download(c.id+'.md',markdown(c),'text/markdown;charset=utf-8');};$('#cw-zip').onclick=exportZip;
    $('#cw-approve').onclick=()=>{const c=selected();if(c&&!issues(c).length){c.approved=true;save();renderExport();}};
    $('#cw-backup').onclick=()=>download('创作项目备份.json',W.recovery||JSON.stringify(W,null,2),'application/json');
    $('#cw-restore').onclick=()=>$('#cw-backup-file').click();$('#cw-backup-file').onchange=async e=>{const f=e.target.files[0];e.target.value='';if(!f)return;try{if(f.size>8*1024*1024)throw Error('文件超过8MB');const data=JSON.parse(await f.text());if(data.schema!==1||!Array.isArray(data.projects)||!data.projects.length||!data.projects.every(validProject))throw Error('不是有效的项目备份');for(const raw of data.projects){const p=clone(raw);p.id=uid();p.confirmed=false;p.step=1;W.projects.push(p);W.activeId=p.id;}delete W.recovery;save();render();say('已作为新项目导入。参考图原文件需在本机存在，否则请补传。');}catch(err){say('导入失败：'+err.message);}};
    $('#cw-legacy-import').onclick=()=>{const old=current();if(!old.legacyVars)return;const p=blank();fields.forEach(([k])=>p.product[k]=String(old.legacyVars[k]||''));W.projects.push(p);W.activeId=p.id;save();render();};
    $('#creative-workflow').addEventListener('click',e=>{
      let b=e.target.closest('[data-cw-go]');if(b){go(Number(b.dataset.cwGo));return;}
      b=e.target.closest('[data-cw-select]');if(b){select(b.dataset.cwSelect);return;}
      b=e.target.closest('[data-cw-variant]');if(b){addVariant(b.dataset.cwVariant);return;}
      b=e.target.closest('[data-cw-adopt]');if(b){const p=current(),k=b.dataset.cwAdopt;productChange(k,p.suggestions[k]);p.sourceNotes[k]='采用识别建议，待核对';delete p.suggestions[k];save();renderProduct();return;}
      b=e.target.closest('[data-cw-image-remove]');if(b&&!busy){const p=current();p.images.splice(Number(b.dataset.cwImageRemove),1);p.revision++;p.confirmed=false;save();render();}
    });
    $('#creative-workflow').addEventListener('input',e=>{const t=e.target;if(t.dataset.cwField)productChange(t.dataset.cwField,t.value);else if(t.dataset.cwShot&&t.dataset.cwShot!=='locked'){editShot(Number(t.dataset.index),t.dataset.cwShot,t.value);updateTimes();}});
    $('#creative-workflow').addEventListener('change',e=>{const t=e.target;if(t.dataset.cwShot==='locked'){editShot(Number(t.dataset.index),'locked',t.checked);renderEditor();}else if(t.dataset.cwShot==='duration'){const c=selected();if(c)t.value=c.shots[Number(t.dataset.index)].duration;}});
    document.addEventListener('wb:archives-changed',()=>{if(mounted)renderReferences();});
    $('#ps-refresh').onclick=()=>{PS.refresh();renderReferences();say('已刷新参考库，当前创作稿保留');};
    render();if(!W.recovery)save();
  }
  window.CW={mount,current,selected,state:()=>W,generate,select,productChange,editShot,addVariant,syncProduct,importLegacyVoice,issues,sections,markdown,timeline,zip,exportZip,upload,scan,render,save,validProject};
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',mount);else mount();
})();
