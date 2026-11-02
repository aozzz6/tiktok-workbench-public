const { chromium } = require('D:/workbuddy_project/tiktok/webapp/node_modules/playwright-core');
const fs = require('fs'), path = require('path');
const out = process.argv[2];
fs.mkdirSync(out, {recursive:true});
const results = [], errors = [];
function check(name, pass, actual) { results.push({name, pass:!!pass, actual}); console.log(`${pass?'PASS':'FAIL'} ${name}`); }
(async()=>{
 const browser = await chromium.launch({executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe', headless:true, args:['--no-proxy-server']});
 try {
  const ctx = await browser.newContext({viewport:{width:1440,height:1050}});
  const page = await ctx.newPage(); page.on('pageerror',e=>errors.push(e.message));
  let product = {}, failure = false, scanCalls = 0;
  await page.route('**/api/ps/product-scan',r=>{scanCalls++;return r.fulfill({status:failure?502:200,contentType:'application/json',body:JSON.stringify(failure?{detail:'验收模拟识别失败'}:{ok:true,product})});});
  await page.goto('http://127.0.0.1:8789/#prompt',{waitUntil:'networkidle'});
  await page.waitForSelector('#po-source');
  await page.evaluate(()=>window.scrollTo(0,0));
  check('F7 右列包含两张独立卡',await page.locator('.po-col-r>.po-panel').count()===2);
  check('F9 无历史时隐藏历史条',await page.locator('#po-history').isHidden());
  const cta = await page.locator('#po-analyze').boundingBox(), entry = await page.locator('.po-imgdetails summary').boundingBox();
  check('F8 1440×1050首屏包含分析按钮与产品图入口',cta.y>=0&&cta.y+cta.height<=1050&&entry.y>=0&&entry.y+entry.height<=1050,{cta,entry});
  const heights = await page.locator('.po-input-grid').evaluate(e=>{const a=e.children[0].getBoundingClientRect(),b=e.children[1].getBoundingClientRect();return {left:a.bottom,right:b.bottom};});
  check('F7 桌面两列底部对齐',Math.abs(heights.left-heights.right)<3,heights);
  check('识别标注不误称仅在本机执行',!(await page.locator('.po-imgpanel .po-section-head').innerText()).includes('本机识别'));
  await page.screenshot({path:path.join(out,'桌面_初始.png')});
  await page.locator('.po-imgdetails summary').click();
  const png = Buffer.from(await page.evaluate(()=>{const c=document.createElement('canvas');c.width=c.height=30;return c.toDataURL('image/png').split(',')[1];}),'base64');
  await page.setInputFiles('#po-images',{name:'验收.png',mimeType:'image/png',buffer:png});
  await page.waitForFunction(()=>document.querySelector('#po-image-count').textContent.includes('已选 1'));
  const name = page.locator('[data-po-field="product"]'), facts = page.locator('[data-po-field="facts"]'), suggestions = page.locator('#po-image-suggestions');
  const scan = async(p,n)=>{product=p;await name.fill(n);await page.locator('#po-scan').click();await page.waitForFunction(()=>!document.querySelector('#po-scan').disabled);};
  const full = {category:'针织上衣',subject:'翻领长袖上衣',material:'未知',colors:['米白','未知',' '],scene:'室内窗边',audience:'成年女性',selling:['可见翻领','未知',' '],shoot:['推镜拍摄']};
  await facts.fill('手写事实保留。');await scan(full,'');
  check('F1 空名称自动填写',await name.inputValue()==='针织上衣');
  check('F3 场景人群卖点全部显示',(await suggestions.inputValue()).includes('可见场景：室内窗边')&&(await suggestions.inputValue()).includes('目标人群：成年女性')&&(await suggestions.inputValue()).includes('可见卖点：可见翻领'));
  check('F4 所有字段过滤未知及空白',!(await suggestions.inputValue()).includes('未知')&&(await suggestions.inputValue()).includes('颜色：米白\n'));
  check('拍摄建议不混入产品事实',!(await suggestions.inputValue()).includes('推镜拍摄'));
  check('F6 识别不自动改变事实',await facts.inputValue()==='手写事实保留。');
  await page.locator('#po-adopt').click();check('F6 采用只追加', (await facts.inputValue()).startsWith('手写事实保留。\n'));
  await scan(full,'手填名称');check('F2 手填名称保留',await name.inputValue()==='手填名称');
  await scan(full,'未知');check('F2 手填字面未知也必须保留',await name.inputValue()==='未知');
  await scan({category:'未知',colors:[],selling:[]},'已有名称');
  check('F5 无品类时准确提示保留已有名称',(await page.locator('#po-image-feedback').innerText()).includes('产品名称保留你已填的内容'));
  await scan({category:'未知',subject:'未知',material:'未知',scene:'未知',audience:'未知',colors:['未知',' '],selling:['未知']},'');
  check('F4 全未知不生成正文',await suggestions.inputValue()==='');
  check('F5 无名称无品类提示手填',(await page.locator('#po-image-feedback').innerText()).includes('未识别出品类，产品名称请手填'));
  check('F5 无可用信息提示换图',(await page.locator('#po-image-feedback').innerText()).includes('未识别出可用外观信息'));
  await scan({category:'杯子',colors:'红色',selling:[]},'');
  check('颜色非数组不导致识别中断',(await page.locator('#po-status').innerText()).startsWith('图片识别完成'));
  await scan({...full,colors:[null,{},'米白']},'');
  check('列表中的无效值不呈现为null或对象',!(await suggestions.inputValue()).includes('[object Object]')&&!(await suggestions.inputValue()).includes('null'));
  await scan(full,'');await page.screenshot({path:path.join(out,'桌面_识别后.png'),fullPage:true});
  const prev = await facts.inputValue();failure=true;await scan(full,'手填失败保护');
  check('识别失败保留手填名称与事实',await name.inputValue()==='手填失败保护'&&await facts.inputValue()===prev&&(await page.locator('#po-status').innerText()).includes('验收模拟识别失败'));failure=false;
  await page.reload({waitUntil:'networkidle'});check('手填名称持久保存',await name.inputValue()==='手填失败保护');
  for(const width of [1100,1050,768,390]){
   await page.setViewportSize({width,height:900});
   check(`${width}宽页面与模块无横向溢出`,await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2&&document.querySelector('#prompt-optimizer').scrollWidth<=document.querySelector('#prompt-optimizer').clientWidth+2));
  }
  await page.evaluate(()=>window.scrollTo(0,0));await page.screenshot({path:path.join(out,'手机_390.png'),fullPage:true});
  check('无未捕获脚本异常',errors.length===0,errors);
  fs.writeFileSync(path.join(out,'independent_results.json'),JSON.stringify({results,errors,scanCalls},null,2));
  if(results.some(r=>!r.pass))process.exitCode=1;
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
