/* Live reports read the per-task confirmation; portable exports use their snapshot. */
(() => {
  const script=document.currentScript,id=script?.dataset.videoId;
  if(!/^\d{6,30}$/.test(id||''))return;
  let el=document.querySelector('[data-source-voice]');
  if(!el){el=document.createElement('p');el.dataset.sourceVoice='';document.querySelector('main section')?.append(el);}
  el.textContent='原片口播：正在读取确认信息…';
  fetch('/tk_reverse/'+id+'/voice.json',{cache:'no-store',signal:AbortSignal.timeout(8000)}).then(async r=>{
    if(r.status===404)return {state:'unknown'};if(!r.ok)throw Error('read');return r.json();
  }).then(v=>{
    el.textContent=v.state==='spoken'&&typeof v.language==='string'?'原片口播语言：'+v.language+'（用户在反推任务中确认；非音频自动识别）。优化时保留语言并重写台词。':v.state==='none'?'原片口播：无口播（用户确认）；优化时按生成设置新增口播。':'原片口播语言尚未确认，请在工作台“提示词反推”中播放原片并保存口播信息。';
  }).catch(()=>{el.textContent='原片口播信息暂时读取失败，请返回工作台反推任务核对。';});
})();
