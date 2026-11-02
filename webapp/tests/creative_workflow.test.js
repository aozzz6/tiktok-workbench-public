const fs=require('fs'),path=require('path'),assert=require('assert');
const {JSDOM}=require('jsdom');
const root=process.env.WEBAPP||process.cwd();
let total=0,failed=0;
function setup(seed={}){
 const dom=new JSDOM(fs.readFileSync(path.join(root,'static/index.html'),'utf8'),{url:'http://localhost/#prompt',runScripts:'outside-only',pretendToBeVisual:true});
 const w=dom.window;w.setInterval=()=>0;w.fetch=()=>Promise.resolve({ok:true,json:()=>Promise.resolve({items:[]})});w.TextEncoder=TextEncoder;
 const errors=[];w.addEventListener('error',e=>errors.push(e.message));
 Object.entries(seed).forEach(([k,v])=>w.localStorage.setItem(k,typeof v==='string'?v:JSON.stringify(v)));
 w.eval(fs.readFileSync(path.join(root,'static/app.js'),'utf8').replace(/\nboot\(\);/g,'\n'));
 ['prompt_studio.js','ps_pipeline.js','creative_workflow.js'].forEach(f=>w.eval(fs.readFileSync(path.join(root,'static',f),'utf8')));
 w.CW.mount();return {w,d:w.document,cw:w.CW,dom,errors};
}
function product(c){const p=c.cw.current();Object.assign(p.product,{product:'针织毛衣',audience:'日常穿搭人群',benefit:'领口结构可见',scene:'真实卧室',material:'针织面料',structure:'翻领',heroAction:'转身展示衣物轮廓'});p.confirmed=true;return p;}
function ready(c){const p=product(c);assert(c.cw.generate());c.cw.select(p.concepts[0].id);return c.cw.selected();}
function test(name,fn){total++;const c=setup();try{fn(c);assert.equal(c.errors.length,0,c.errors.join());console.log('PASS',name);}catch(e){failed++;console.log('FAIL',name,e.stack);}finally{c.dom.window.close();}}
test('仅四个流程入口，旧生成器隐藏',({d})=>{assert.equal(d.querySelectorAll('[data-cw-go]').length,5);assert(d.querySelector('#ps-legacy').hidden);assert.equal(d.querySelector('#ps-sec-bench').closest('#ps-legacy'),null);});
test('新用户没有湿巾默认产品',({cw})=>assert.equal(cw.current().product.product,''));
test('产品未确认时拒绝生成',({cw})=>{assert.equal(cw.generate(),false);assert.equal(cw.current().concepts.length,0);});
test('必填信息不完整不能确认',({d,cw})=>{d.querySelector('#cw-confirm').click();assert.equal(cw.current().confirmed,false);});
test('产品确认后生成三张简短方向卡',c=>{product(c);c.d.querySelector('#cw-confirm').click();assert.equal(c.cw.current().step,2);assert(c.cw.generate());assert.equal(c.d.querySelectorAll('.cw-concept').length,3);});
test('选择方向后编辑的就是该方案',c=>{const s=ready(c);assert.equal(c.cw.current().step,3);assert.equal(c.d.querySelectorAll('.cw-shot').length,s.shots.length);});
test('无对标也能生成且可设镜头数',c=>{const p=product(c);p.shots=7;c.cw.generate();assert.equal(p.concepts[0].shots.length,7);});
test('单条主参考继承七拍并精确15秒',c=>{const p=product(c);c.w.PS.importReverseMarkdown('# 参考\n'+Array.from({length:7},(_,i)=>'## '+i*2+'-'+(i+1)*2+'s 细节\n画面：展示产品真实纹理').join('\n'));p.reference='archive:'+c.w.PS.state.bench.list[0].id;c.cw.generate();const s=p.concepts[0];assert.equal(s.shots.length,7);assert.equal(Math.round(s.shots.reduce((n,s)=>n+s.duration,0)*10),150);});
test('已删除的主参考不能静默换成通用结构',c=>{const p=product(c);p.reference='archive:missing';assert.equal(c.cw.generate(),false);});
test('编辑动作同步导出，未二次生成',c=>{const s=ready(c);c.cw.editShot(1,'action','仅此一条的人工动作');assert(c.cw.markdown(s).includes('仅此一条的人工动作'));});
test('七镜都有独立口播编辑位',c=>{const p=product(c);p.shots=7;c.cw.generate();c.cw.select(p.concepts[0].id);assert.equal(c.d.querySelectorAll('[data-cw-shot="voice"]').length,7);c.cw.editShot(6,'voice','Die letzte Zeile.');assert(c.cw.markdown(c.cw.selected()).includes('Die letzte Zeile.'));});
test('五段式只是选中稿的五个输出区块',c=>{const s=ready(c);assert.equal(c.cw.sections(s).length,5);assert(c.cw.sections(s)[4].body.includes(s.shots[0].action));});
test('产品修改保留原方案快照并标记过期',c=>{const s=ready(c);c.cw.productChange('product','新的衬衫');assert.equal(s.product.product,'针织毛衣');assert(c.cw.issues(s).some(x=>x.includes('产品档案已修改')));assert(!c.cw.current().confirmed);});
test('锁定镜头不能编辑内容',c=>{const s=ready(c),before=s.shots[1].action;c.cw.editShot(1,'locked',true);c.cw.editShot(1,'action','覆盖');assert.equal(s.shots[1].action,before);});
test('只换钩子保持正文、时长和口播',c=>{const original=ready(c),before=JSON.stringify(original),variant=c.cw.addVariant('hook');assert(variant);assert.equal(JSON.stringify(original),before);assert.notEqual(variant.shots[0].action,original.shots[0].action);assert.equal(JSON.stringify(variant.shots.slice(1)),JSON.stringify(original.shots.slice(1)));assert.equal(variant.shots[0].voice,original.shots[0].voice);assert.equal(variant.parentId,original.id);});
test('锁定开头时不能生成换钩子变体',c=>{ready(c);c.cw.editShot(0,'locked',true);const n=c.cw.current().concepts.length;c.cw.addVariant('hook');assert.equal(c.cw.current().concepts.length,n);});
test('换卖点只修改一个未锁定中间镜头',c=>{const s=ready(c);c.d.querySelector('#cw-variant-value').value='另一项经过核实的结构';const v=c.cw.addVariant('benefit');const changes=v.shots.filter((shot,i)=>JSON.stringify(shot)!==JSON.stringify(s.shots[i]));assert.equal(changes.length,1);assert.equal(v.product.benefit,s.product.benefit);});
test('空口播、缺图及占位符会被检查识别',c=>{const s=ready(c);s.shots[0].action='〔画面待补〕';const issues=c.cw.issues(s);assert(issues.some(x=>x.includes('德语口播待补')));assert(issues.some(x=>x.includes('参考图')));assert(issues.some(x=>x.includes('画面或镜头')));});
test('语速过快会提示',c=>{const s=ready(c);s.shots[0].voice='eins zwei drei vier fünf sechs sieben acht neun zehn elf zwölf';s.shots[0].duration=1;assert(c.cw.issues(s).some(x=>x.includes('口播偏长')));});
test('非正数和非有限时长被拒绝',c=>{const s=ready(c),before=s.shots[0].duration;assert.equal(c.cw.editShot(0,'duration',-1),false);assert.equal(c.cw.editShot(0,'duration','NaN'),false);assert.equal(s.shots[0].duration,before);});
test('实际总时长随编辑同步而非固定15秒',c=>{const s=ready(c);c.cw.editShot(0,'duration',5);assert(c.cw.markdown(s).includes('总时长 17 秒'));});
test('生成稿和人工修改持久化恢复',c=>{const s=ready(c);c.cw.editShot(0,'voice','Meine Zeile.');c.cw.save();const data=c.w.localStorage.getItem('wb.creative.projects.v1');const second=setup({'wb.creative.projects.v1':data});try{assert.equal(second.cw.selected().id,s.id);assert.equal(second.cw.selected().shots[0].voice,'Meine Zeile.');}finally{second.dom.window.close();}});
test('新建与切换产品项目互不覆盖',c=>{const s=ready(c),old=c.cw.current().id;c.d.querySelector('#cw-new').click();assert.equal(c.cw.current().concepts.length,0);assert.equal(c.cw.current().product.product,'');c.cw.state().activeId=old;c.cw.render();assert.equal(c.cw.selected().id,s.id);});
test('导出文本不受隐藏旧引擎改动影响',c=>{const s=ready(c),text=c.cw.markdown(s);c.w.PS.state.v.product='隐藏旧产品';c.w.PS.state.voice=['垃圾'];assert.equal(c.cw.markdown(s),text);});
test('新增档案后参考下拉自动刷新',c=>{c.w.PS.importReverseMarkdown('# 新参考\n## 0-3s 演示\n画面：一条足够长的演示文字');assert(c.d.querySelector('#cw-reference').textContent.includes('新参考'));});
test('标题和镜头内容不会作为HTML执行',c=>{const s=ready(c);s.title='<img src=x onerror=alert(1)>';s.shots[0].action='<script>alert(1)</script>';c.cw.render();assert.equal(c.d.querySelectorAll('#cw-concepts img').length,0);assert(c.d.querySelector('#cw-output').textContent.includes('<script>'));});
test('损坏备份被判为无效项目',({cw})=>{assert.equal(cw.validProject({id:'a',product:{},images:[],concepts:[]}),false);});
test('缺少分镜素材结构的备份被拒绝',c=>{ready(c);const p=JSON.parse(JSON.stringify(c.cw.current()));delete p.concepts[0].images;assert.equal(c.cw.validProject(p),false);});
test('产品资料显式更新保留人工稿并要求复核',c=>{const s=ready(c);c.cw.editShot(1,'action','手填动作');c.cw.productChange('material','新材质');c.cw.current().confirmed=true;c.cw.syncProduct();assert.equal(s.product.material,'新材质');assert.equal(s.shots[1].action,'手填动作');assert(s.needsReview);assert(c.cw.issues(s).some(x=>x.includes('逐镜核对')));c.d.querySelector('#cw-reviewed').click();assert(!s.needsReview);});
test('未确认的产品资料不能直接替换稿件快照',c=>{const s=ready(c);c.cw.productChange('product','新产品');c.cw.syncProduct();assert.equal(s.product.product,'针织毛衣');assert.equal(c.cw.current().step,1);});
test('容量不足三份时不超限也不丢旧稿',c=>{ready(c);const p=c.cw.current();while(p.concepts.length<29)p.concepts.push(JSON.parse(JSON.stringify(p.concepts[0])));assert.equal(c.cw.generate(),false);assert.equal(p.concepts.length,29);});
test('坏存储原文保留供备份',c=>{const second=setup({'wb.creative.projects.v1':'broken raw data'});try{assert.equal(second.cw.state().recovery,'broken raw data');assert.equal(second.w.localStorage.getItem('wb.creative.projects.v1'),'broken raw data');}finally{second.dom.window.close();}});
test('旧手写口播仅填空位且尊重锁定',c=>{const s=ready(c);c.cw.current().legacyVoice=['旧句一','旧句二','旧句三'];s.shots[1].voice='新句';s.shots[2].locked=true;c.cw.importLegacyVoice();assert.equal(s.shots[0].voice,'旧句一');assert.equal(s.shots[1].voice,'新句');assert.equal(s.shots[2].voice,'');assert(s.needsReview);});
test('新上传图片不改已有方案素材快照',c=>{const s=ready(c);c.cw.current().images.push({key:'later',name:'新图',mime:'image/jpeg',thumb:''});assert.equal(s.images.length,0);});
test('完成检查后允许确认，修改后撤销确认',c=>{const s=ready(c);s.images=[{key:'img',name:'图.jpg'}];s.shots.forEach(x=>{x.image='img';x.voice='Hier siehst du es.';});c.cw.render();assert.equal(c.cw.issues(s).length,0);c.d.querySelector('#cw-approve').click();assert(s.approved);c.cw.editShot(1,'action','新的动作');assert(!s.approved);});
const migration=setup({'wb.pp.prod.v1':{id:'old',edited:{product:'旧毛衣',material:'旧材质'},images:[]},'wb.po.vars.v1':{product:'旧衬衫'}});
total++;try{assert.equal(migration.cw.current().product.product,'旧毛衣');assert.equal(migration.cw.current().legacyVars.product,'旧衬衫');assert(migration.w.localStorage.getItem('wb.po.vars.v1'));console.log('PASS 旧双产品冲突保留并可恢复');}catch(e){failed++;console.log('FAIL 旧双产品迁移',e.message);}finally{migration.dom.window.close();}
console.log(`TOTAL ${total} FAIL ${failed}`);process.exitCode=failed?1:0;
