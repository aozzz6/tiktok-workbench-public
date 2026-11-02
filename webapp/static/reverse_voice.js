/* Source speech belongs to each reverse task, never inferred from caption language. */
(() => {
  'use strict';
  let base=null,pending=null;
  const languages=['德语','英语','法语','西班牙语','意大利语','葡萄牙语','中文','日语','韩语','印尼语','泰语','越南语','阿拉伯语'];
  async function endpoint(){
    if(base!==null)return base;
    if(pending)return pending;
    pending=(async()=>{for(const b of ['', 'http://127.0.0.1:8791']){try{const r=await fetch(b+'/api/reverse-voice/profile',{signal:AbortSignal.timeout(3000)});if(r.ok&&(await r.json()).feature==='reverse_voice'){base=b;return b;}}catch(e){}}
      throw Error('原片口播信息接口未启动，请运行工作台优化接口启动脚本。');})();
    try{return await pending;}finally{pending=null;}
  }
  async function request(id,payload){
    if(!/^\d{6,30}$/.test(id))throw Error('视频编号无效');
    const b=await endpoint(),r=await fetch(b+'/api/reverse-voice/'+id,{method:payload?'POST':'GET',headers:payload?{'Content-Type':'application/json'}:{},body:payload?JSON.stringify(payload):undefined,cache:'no-store',signal:AbortSignal.timeout(10000)});
    const data=await r.json();if(!r.ok)throw Error(data.detail||'口播信息读取失败');return data.voice;
  }
  const label=v=>v?.state==='spoken'?`原片口播：${v.language}（用户确认）`:v?.state==='none'?'原片口播：无口播（用户确认；可有音乐或环境声）':'原片口播：尚未确认';
  window.ReverseVoice={languages,read:id=>request(id),save:(id,v)=>request(id,v),label};
})();
