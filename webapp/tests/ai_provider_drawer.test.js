/* Dependency-free DOM-event stub; real UI browser test in ai_provider_flow.test.py. */
const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { join } = require("node:path");
const vm = require("node:vm");
const evt = {};
let html = "", fields = new Map(), posts = [], notifications = [];
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[c]);
const modelRow = {id:"fixture:remote", name:"remote", provider_id:"fixture", provider_name:"Fixture",
  provider_model:"remote", vendor:"Fixture", api_style:"openai", url:"http://127.0.0.1:8999/v1",
  has_key:true,key_masked:"已设置", supportsImages:false};
let config = {enabled:false, models:[], default:"", fallbacks:[], purposes:{}};
const host = {
  get innerHTML() { return html; },
  set innerHTML(v) {
    html = v;
    fields = new Map();
    for (const m of v.matchAll(/<(input|select)\b([^>]*\bid="([^"]+)"[^>]*)>([^<]*(?:<option[^>]*>[^<]*<\/option>\s*)*)/g)) {
      const select = [...m[4].matchAll(/<option value="([^"]*)"([^>]*)>/g)];
      fields.set(m[3], {id:m[3], value:m[1] === "select"
        ? (select.find(e=>e[2].includes("selected"))?.[1] || select[0]?.[1] || "")
        : (m[2].match(/\bvalue="([^"]*)"/)?.[1] || ""), checked:/\bchecked\b/.test(m[2]),
        disabled:/\bdisabled\b/.test(m[2]), focus() {}, getAttribute() { return null; }});
    }
  },
  addEventListener(t, fn) { evt[t] = fn; },
};
const document = {
  getElementById: id => id === "ai-conf-host" ? host : fields.get(id) || null,
  addEventListener() {},
};
const pending = () => new Promise(r => setImmediate(r));
function click(act, extra={}) {
  assert.ok(html.includes('data-act="' + act + '"'), "missing control " + act);
  const button = {getAttribute:k => k === "data-act" ? act : extra[k]};
  return evt.click({target:{closest:()=>button}});
}
function value(id, v, kind="input") {
  assert.ok(fields.has(id), "missing field " + id);
  const f=fields.get(id);
  if (typeof v === "boolean") f.checked=v;
  else f.value=v;
  evt[kind]({target:f});
}
const window = {confirm:()=>true, fetch:null};
const ctx={window,document,esc,toast:(...x)=>notifications.push(x),AbortController,setTimeout,clearTimeout,setInterval,clearInterval,
  api: async p => p.includes("calls?") ? {calls:[]} : config,
  fetch: async (url,o)=>{
    const body=JSON.parse(o.body);
    posts.push({url,body});
    let out={ok:true};
    if (url.endsWith("discover")) out={models:["remote","vision"]};
    else if (url.endsWith("/providers")) {
      out={ok:true, models:[modelRow]}; config={...config,models:[modelRow]};
    } else if (url.endsWith("/test")) out={ok:true, latency_ms:17, model:body.model};
    else if (url.endsWith("/config")) config={...config, enabled:body.enabled, default:body.model};
    return {ok:true, text:async()=>JSON.stringify(out)};
  }};
vm.runInNewContext(readFileSync(join(__dirname,"../static/ai_admin.js"),"utf8"),ctx);
(async()=>{
  await ctx.window.AIA.load();
  await click("provider-open");
  value("ai-provider-preset","deepseek","change");
  assert.equal(fields.get("ai-provider-url").value,"https://api.deepseek.com");
  value("ai-provider-preset","","change");
  value("ai-provider-id","fixture");
  value("ai-provider-name","Fixture");
  value("ai-provider-url","http://127.0.0.1:8999/v1");
  value("ai-provider-key","fixture-token");
  await click("provider-discover");
  assert.deepEqual(posts.map(p=>p.url), ["/api/ai/providers/discover"]);
  value("ai-provider-found","remote","change");
  await click("provider-add-row");
  value("ai-provider-model-1","vision");
  value("ai-provider-image-1",true);
  assert.ok(!html.includes("fixture-token"), "key must not be serialized as a value attribute");
  await click("provider-save");
  assert.deepEqual(posts[1].body.models,[{id:"remote",images:false},{id:"vision",images:true}]);
  assert.equal(posts[1].body.apiKey,"fixture-token");
  assert.deepEqual(posts.map(p=>p.url), ["/api/ai/providers/discover","/api/ai/providers"]);
  assert.equal(config.enabled,false);
  assert.equal(config.default,"");
  await click("test",{"data-id":"fixture:remote"});
  assert.deepEqual(posts[2],{url:"/api/ai/test",body:{model:"fixture:remote"}});
  assert.ok(notifications.some(n=>n[0].includes("连通 ✓")));
  await click("def",{"data-id":"fixture:remote"});
  assert.equal(config.default,"fixture:remote");
  assert.equal(config.enabled,false);
  value("ai-enabled",true,"change");
  await pending();
  assert.equal(config.enabled,true);
  await click("provider-edit",{"data-id":"fixture"});
  assert.equal(fields.get("ai-provider-key").value,"");
  await click("drawer-close");
  assert.ok(!html.includes('id="ai-provider-key"'));
  console.log("PASS exact provider drawer DOM events: preset, discovery, multiple models, save/test/default/enable, blank edit key");
})().catch(e=>{console.error(e);process.exitCode=1;});
