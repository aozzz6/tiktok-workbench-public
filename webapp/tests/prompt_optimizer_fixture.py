import json
import sys
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parent))
from test_prompt_optimizer import po, payload, fixture
with patch.object(po.llm,'available',return_value=True),patch.object(po.llm,'chat',return_value={'text':json.dumps(fixture(),ensure_ascii=False),'model':'测试替身'}):
    data=po.generate(payload())
Path(sys.argv[1]).write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
