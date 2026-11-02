"""Explicit live acceptance: two text-model operations, never generates a video."""
import json
import sys
import time
import urllib.request
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
from test_prompt_optimizer import payload

out=Path(sys.argv[1]);out.mkdir(parents=True,exist_ok=True)
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
split='--split' in sys.argv
for action in (['generate'] if split else ['analyze','generate']):
    t=time.monotonic()
    body=payload(duration=30,strategy='split',mode='reference',refs='产品正面图片仅锁定米白、翻领、长袖外观；对标视频只参考节奏，不复制竞品。人物使用文字描述，未提供真人脸素材。') if split else payload()
    req=urllib.request.Request('http://127.0.0.1:8791/api/prompt-optimizer/'+action,data=json.dumps(body,ensure_ascii=False).encode(),headers={'Content-Type':'application/json'})
    try:
        with opener.open(req,timeout=280) as r:data=json.load(r)
        (out/('live_'+('split_' if split else '')+action+'.json')).write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
        print(action,'OK',data.get('model'),round(time.monotonic()-t,1),'s',flush=True)
    except Exception as exc:
        print(action,'FAILED',type(exc).__name__,str(exc),flush=True)
        if hasattr(exc,'read'):print(exc.read().decode('utf-8','replace'),flush=True)
        sys.exit(1)
