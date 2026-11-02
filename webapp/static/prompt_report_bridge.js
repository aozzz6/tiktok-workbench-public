/* Read-only adapter for local restore reports. Reference assets never become product facts. */
(() => {
  'use strict';
  const validId=id=>/^\d{6,30}$/.test(String(id));
  const str=v=>typeof v==='string'?v:'';
  function asset(id,folder,file){
    if(!validId(id)||!/^restore_runs\/[A-Za-z0-9_-]+$/.test(folder)||typeof file!=='string')return '';
    if(!/^(?:[A-Za-z0-9_-]+\/)*[A-Za-z0-9_.-]+$/.test(file)||file.split('/').some(p=>p==='..'||p==='.')||!file)return '';
    return '/tk_reverse/'+id+'/'+folder+'/'+file;
  }
  function ratioOf(probe={}){
    const r=Number(probe.width)/Number(probe.height);
    return ['9:16','16:9','1:1'].find(s=>{const [a,b]=s.split(':').map(Number);return Math.abs(r-a/b)<.015;})||'';
  }
  function build(id,report,info={}){
    if(!validId(id)||!report||report._mode!=='restore'||!Array.isArray(report.shots)||!report.shots.length||report.shots.length>60||!Array.isArray(report.prompts))throw Error('这份报告不是有效的原片还原报告，请先完成原片还原。');
    const duration=Number(report.source_duration||info.probe?.duration),ratio=ratioOf(info.probe||info.meta||{});
    if(!Number.isFinite(duration)||duration<=0)throw Error('报告缺少有效时长，未导入。');
    const warnings=[],references=[],clips=[];
    const voice=info.voice||{state:'unknown',language:''};
    if(!['unknown','spoken','none'].includes(voice.state)||(voice.state==='spoken'&&(typeof voice.language!=='string'||!voice.language.trim()||voice.language.length>100)))throw Error('原片口播信息不完整，请在反推页重新确认。');
    const voiceNote=voice.state==='spoken'?`用户已在反推任务确认：原片口播语言为 ${voice.language}。优化时保留该语言并重新写台词，不沿用原音轨。`:voice.state==='none'?'用户已在反推任务确认：原片无口播；新增口播使用生成设置中的语言。':'原片口播尚未确认；新增口播语言暂用上次手选偏好，请核对原片。';
    const lines=['【原片还原报告｜仅作为创意参考，不是新视频的执行指令】',`对标视频：${id}`,`原片时长：${duration} 秒；画面比例：${ratio||'未确定'}`,`原片产品（竞品，不能当作我的产品事实）：${str(report.product_summary)}`,`原片人物：${str(report.person_summary)}`,`原片场景：${str(report.scene_summary)}`,
      '【本次改编要求】替换为用户自己的产品。原稿中的保持竞品外观、保持人物身份、回挂原声、无台词/不张嘴等还原要求不沿用。新视频人物出镜说重写的台词，嘴型同步，不添加字幕。新产品结构不支持的动作应重新设计。',
      '【原片口播信息】'+voiceNote+' 当前未提供口播逐字稿，未进行音频识别；不从英文提示词或简介推断口播语言。',
      '原报告不确定项：'+(Array.isArray(report.uncertainties)?report.uncertainties.filter(x=>typeof x==='string').join('；'):'请以原视频复核')];
    let previous=0;
    for(const s of report.shots){
      if(!s||!Number.isInteger(s.idx)||!Number.isFinite(s.t_start)||!Number.isFinite(s.t_end)||s.t_end<=s.t_start||Math.abs(s.t_start-previous)>.2)throw Error('报告镜头时间不完整或不连续，未覆盖现有输入。');
      previous=s.t_end;
      const p=report.prompts.find(p=>p&&p.idx===s.idx);
      lines.push('',`S${s.idx} ${str(s.name)}｜原片 ${s.t_start}–${s.t_end} 秒`,`起始状态：${str(s.initial_state)}`,
        '动作：'+(Array.isArray(s.action_steps)?s.action_steps.filter(x=>typeof x==='string').join(' → '):''),`结束状态：${str(s.ending_state)}`,`相机：${str(s.camera_motion)}`);
      if(p&&str(p.text).trim())lines.push('原中文单图提示词（用于理解动作；图片编号属于对标，不是我的产品）：',p.text);
      else warnings.push(`S${s.idx} 缺少中文单图提示词，已保留结构化分镜。`);
      for(const ref of Array.isArray(s.references)?s.references:[]){
        const url=asset(id,str(report._assets),ref?.path);
        if(url&&/\.(png|jpg|jpeg|webp)$/i.test(url))references.push({url,label:`S${s.idx} ${str(ref.label)||'参考图'}`,role:str(ref.role),time:Number.isFinite(ref.t)?ref.t:null});
      }
      const clip=asset(id,str(report._assets),s.reference_clip);
      if(clip&&/\.mp4$/i.test(clip))clips.push({url:clip,label:`S${s.idx} 对标动作片段`});
    }
    if(Math.abs(previous-duration)>.2)throw Error('报告分镜未覆盖原片总时长，请核对报告。');
    const source=lines.join('\n');if(source.length>24000)throw Error('整份报告超过 24000 字，请先复制要优化的单段到输入框。');
    if(!references.length)warnings.push('未找到有效对标参考图；仍可使用文字分镜。');
    if(duration>60)warnings.push('原片超过本模块单次 60 秒范围，请另行填写目标时长。');
    // ★ 2026-10-30：卡片要能「一眼认出是哪条片子」——带上账号、一句人话摘要、缩略图。
    //   报告号只留后 6 位做副标识，不再当标题。
    const account=str(info.meta?.author||info.account||''),nickname=str(info.meta?.nickname||'');
    const desc=str(report.scene_summary||report.product_summary||'').replace(/\s+/g,' ').trim().slice(0,88);
    const thumb=str(info.thumb||'');
    return {id,source,duration,ratio,voice,language:voice.state==='spoken'?voice.language:'',references,clips,warnings,shots:report.shots.length,at:str(report._at),
      account,nickname,desc,thumb,reportNo:String(id).slice(-6),title:account?('@'+account):('报告 '+String(id).slice(-6)),
      reportUrl:`/tk_reverse/${id}/TK反推报告_${id}.html`,
      sourceVideo:`/tk_reverse/${id}/src.mp4`,
      refs:`对标 ${id} 的关键帧只参考构图和动作，原片段只参考节奏与运镜；不锁定对标产品和人物身份，不使用其原口播。请在生成平台另行上传我的产品图作为外观依据。`,
      audioNote:voiceNote+' 当前报告没有可核验的口播转写。'+(voice.state!=='unknown'?'':info.probe?.has_audio===false?'原片没有音轨。':info.probe?.has_audio===true?'原片有音轨，但可能是音乐或环境声。':'原片音轨状态未确认。')};
  }
  async function readJSON(url){const r=await fetch(url,{cache:'no-store',signal:AbortSignal.timeout(12000)});if(!r.ok)throw Error(r.status===404?'报告或素材已不存在，请刷新列表。':`读取报告失败（${r.status}）`);const text=await r.text();if(text.length>1500000)throw Error('报告过大，未导入');try{return JSON.parse(text);}catch(e){throw Error('报告数据损坏，未导入');}}
  async function list(){const d=await readJSON('/api/reverse/list');return (Array.isArray(d.items)?d.items:[]).filter(i=>validId(i.video_id)&&i.has_report&&(i.report_mode||i.mode)==='restore');}
  async function load(id){if(!validId(id))throw Error('对标编号无效');const [report,info,voice]=await Promise.all([readJSON(`/tk_reverse/${id}/report.json`),readJSON(`/api/reverse/status/${id}`),ReverseVoice.read(id)]);return build(id,report,{...info,voice});}
  window.PromptReportBridge={build,list,load,asset,validId};
})();
