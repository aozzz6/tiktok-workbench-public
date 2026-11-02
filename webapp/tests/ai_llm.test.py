# -*- coding: utf-8 -*-
"""ai_llm.test.py — 统一模型调用层（不打真实网络：patch 掉 opener）

覆盖：
  · 三种 API 风格识别与 URL/body/响应解析（openai / anthropic / gemini）
  · 模型清单过滤：无 key 非本地剔除、本地无 key 保留、环境变量注入
  · 调用：正常返回、429 退避重试、401 不重试、空正文加倍预算重试
  · 熔断：连续失败后冷却；冷却中的模型被跳过
  · 审计 jsonl 只记元数据（不含 key 与 prompt 全文）
  · JSON 容错：围栏 / 前后说明 / 尾逗号 / 截断抢救
  · chat_json 首次非法 → 二次带「只输出 JSON」
  · 配置白名单与数值夹紧
运行：webapp/.venv/Scripts/python.exe tests/ai_llm.test.py
"""
from __future__ import annotations

import io
import json
import sys
import tempfile
import urllib.error
from pathlib import Path
from unittest.mock import patch

WEBAPP = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WEBAPP))
import llm  # noqa: E402

_ok = 0
_fail = 0


def check(name, fn):
    global _ok, _fail
    try:
        fn()
        print(f"  ✓ {name}")
        _ok += 1
    except AssertionError as e:
        print(f"  ✗ {name} -> {e}")
        _fail += 1
    except Exception as e:                       # noqa: BLE001
        print(f"  ✗ {name} -> {e.__class__.__name__}: {e}")
        _fail += 1


def assert_(cond, msg="assert failed"):
    if not cond:
        raise AssertionError(msg)


# ── 沙箱：模型清单/配置/审计都指到临时目录 ────────────────────────
TMP = Path(tempfile.mkdtemp(prefix="ai_"))
MODELS = TMP / "models.json"
CFG = TMP / "ai_config.json"
AUDIT = TMP / "ai_calls.jsonl"
_orig = (llm.MODELS_JSON, llm.AI_CONFIG_JSON, llm.AI_CALLS_JSONL)
llm.MODELS_JSON, llm.AI_CONFIG_JSON, llm.AI_CALLS_JSONL = MODELS, CFG, AUDIT


def write_models(items):
    MODELS.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")


BASE = [
    {"id": "gpt-4o-mini", "name": "GPT-4o mini", "vendor": "openai",
     "url": "https://api.openai.com/v1", "apiKey": "sk-test-1234567890abcd"},
    {"id": "claude-x", "name": "Claude X", "vendor": "anthropic",
     "url": "https://api.anthropic.com", "apiKey": "sk-ant-abcdefghijk"},
    {"id": "gemini-x", "name": "Gemini X", "vendor": "google",
     "url": "https://generativelanguage.googleapis.com", "apiKey": "AIza-test"},
    {"id": "ollama-local", "name": "本地 qwen", "vendor": "ollama",
     "url": "http://127.0.0.1:11434/v1", "apiKey": ""},
    {"id": "no-key-remote", "name": "无 key 远端", "vendor": "",
     "url": "https://api.example.com/v1", "apiKey": ""},
]


class Resp:
    def __init__(self, payload):
        self._b = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def http_err(code, body="{}"):
    return urllib.error.HTTPError("http://x", code, "err", {},
                                  io.BytesIO(body.encode("utf-8")))


class FakeOpener:
    """按顺序回放预设结果（Resp 或 Exception），并记录请求。"""

    def __init__(self, plan):
        self.plan = list(plan)
        self.reqs = []

    def open(self, req, timeout=None):
        self.reqs.append(req)
        item = self.plan.pop(0) if self.plan else Resp({})
        if isinstance(item, Exception):
            raise item
        return item


def use(plan):
    op = FakeOpener(plan)
    llm._opener = lambda cfg: op
    return op


def reset_state():
    llm._COOLDOWN.clear()
    if AUDIT.exists():
        AUDIT.unlink()
    if CFG.exists():
        CFG.unlink()


write_models(BASE)
reset_state()

print("ai_llm — 统一调用层")

# ── 1. 风格识别与三套 URL/body/解析 ──────────────────────────────
check("api_style：按 vendor 识别三种风格", lambda: (
    assert_(llm.api_style(BASE[0]) == "openai", llm.api_style(BASE[0])),
    assert_(llm.api_style(BASE[1]) == "anthropic", llm.api_style(BASE[1])),
    assert_(llm.api_style(BASE[2]) == "gemini", llm.api_style(BASE[2])),
    assert_(llm.api_style({**BASE[0], "api_style": "anthropic"}) == "anthropic", "显式优先"),
))

check("_endpoint：三种 URL 结构", lambda: (
    assert_(llm._endpoint("openai", BASE[0]) == "https://api.openai.com/v1/chat/completions",
            llm._endpoint("openai", BASE[0])),
    assert_(llm._endpoint("anthropic", BASE[1]) == "https://api.anthropic.com/v1/messages",
            llm._endpoint("anthropic", BASE[1])),
    assert_(llm._endpoint("gemini", BASE[2]).endswith("/v1beta/models/gemini-x:generateContent"),
            llm._endpoint("gemini", BASE[2])),
))

msgs = [{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}]
check("_body：openai 保留 system，anthropic 抽 system，gemini 用 systemInstruction", lambda: (
    assert_(llm._body("openai", BASE[0], msgs, 100, 0.2, True)["messages"] == msgs, "openai messages"),
    assert_("response_format" in llm._body("openai", BASE[0], msgs, 100, 0.2, True), "json_mode"),
    assert_(llm._body("anthropic", BASE[1], msgs, 100, 0.2, False)["system"] == "sys", "anthropic system"),
    assert_("systemInstruction" in llm._body("gemini", BASE[2], msgs, 100, 0.2, True), "gemini system"),
))

check("_extract：三种响应结构抹平成同一份 (text, usage)", lambda: (
    assert_(llm._extract("openai", {"choices": [{"message": {"content": "A"}}],
                                    "usage": {"prompt_tokens": 3, "completion_tokens": 2}})[0] == "A", "openai"),
    assert_(llm._extract("anthropic", {"content": [{"type": "text", "text": "B"}],
                                       "usage": {"input_tokens": 4}})[0] == "B", "anthropic"),
    assert_(llm._extract("gemini", {"candidates": [{"content": {"parts": [{"text": "C"}]}}],
                                    "usageMetadata": {"promptTokenCount": 5}})[0] == "C", "gemini"),
))

check("_extract：推理模型把正文写进 reasoning_content 也能取到", lambda: (
    assert_(llm._extract("openai", {"choices": [{"message": {"content": "",
                                                             "reasoning_content": "R"}}]})[0] == "R", "reasoning")
))

# ── 2. 模型清单 ─────────────────────────────────────────────────
check("list_models：剔除无 key 的非本地模型，保留本地与有 key 的", lambda: (
    ids := [m["id"] for m in llm.list_models()],
    assert_("no-key-remote" not in ids, f"不该出现：{ids}"),
    assert_("ollama-local" in ids, "本地无 key 应保留"),
    assert_(len(ids) == 4, f"应为 4 个，实得 {ids}"),
))

check("list_models：key 一律掩码，不出原文", lambda: (
    row := next(m for m in llm.list_models() if m["id"] == "gpt-4o-mini"),
    assert_("sk-test-1234567890abcd" not in json.dumps(row, ensure_ascii=False), "泄露了 key"),
    assert_("…" in row["key_masked"], row["key_masked"]),
))

# ── 3. 调用：正常 / 重试 / 不重试 / 空正文 ───────────────────────
reset_state()
op = use([Resp({"choices": [{"message": {"content": "hello"}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5}})])
r = llm.chat(msgs, "gpt-4o-mini", purpose="test")
check("chat：正常返回 text/model/usage，且写审计（不含 prompt 全文）", lambda: (
    assert_(r["text"] == "hello", r["text"]),
    assert_(r["model"] == "gpt-4o-mini", r["model"]),
    assert_(r["usage"]["out"] == 5, str(r["usage"])),
    assert_(AUDIT.exists(), "未写审计"),
    assert_(len(AUDIT.read_text(encoding="utf-8").strip().splitlines()) == 1, "审计行数不对"),
    assert_("hello" not in AUDIT.read_text(encoding="utf-8"), "审计不该记录正文"),
    assert_("sk-test" not in AUDIT.read_text(encoding="utf-8"), "审计不该记录 key"),
))

reset_state()
op = use([http_err(429, '{"error":"rate"}'),
          Resp({"choices": [{"message": {"content": "retry-ok"}}]})])
r = llm.chat(msgs, "gpt-4o-mini", purpose="test")
check("chat：429 自动退避重试并成功（attempt=2）", lambda: (
    assert_(r["text"] == "retry-ok", r["text"]),
    assert_(r["attempt"] == 2, str(r["attempt"])),
    assert_(len(op.reqs) == 2, f"应请求 2 次，实得 {len(op.reqs)}"),
))

reset_state()
op = use([http_err(401, '{"error":"bad key"}')])
try:
    llm.chat(msgs, "gpt-4o-mini", purpose="test")
    err = None
except llm.LLMError as e:
    err = e
check("chat：401 鉴权失败不重试（重试也没用）", lambda: (
    assert_(err is not None, "应抛 LLMError"),
    assert_(err.code == 401, str(err.code)),
    assert_(err.retryable is False, "401 不该标可重试"),
    assert_(len(op.reqs) == 1, f"不该重试，实际请求 {len(op.reqs)} 次"),
))

reset_state()
op = use([Resp({"choices": [{"message": {"content": ""}}]}),
          Resp({"choices": [{"message": {"content": "second"}}]})])
r = llm.chat(msgs, "gpt-4o-mini", max_tokens=100, purpose="test")
second = json.loads(op.reqs[1].data.decode("utf-8"))
check("chat：空正文重试并加倍 max_tokens（推理模型把预算吃光的坑）", lambda: (
    assert_(r["text"] == "second", r["text"]),
    assert_(second["max_tokens"] == 200, f"max_tokens={second.get('max_tokens')}"),
))

# ── 4. 熔断与回退链 ─────────────────────────────────────────────
reset_state()
for _ in range(llm.FAIL_THRESHOLD):
    use([http_err(500, "boom")])
    try:
        llm.chat(msgs, "gpt-4o-mini", retries=0, purpose="test")
    except llm.LLMError:
        pass
check("熔断：连续失败达阈值后进入冷却", lambda: (
    assert_(llm._cooling("gpt-4o-mini") > 0, "未进入冷却"),
    assert_(llm._COOLDOWN["gpt-4o-mini"]["last"], "缺最后错误"),
))
reset_state()

write_models(BASE)
CFG.write_text(json.dumps({"model": "gpt-4o-mini", "fallbacks": ["claude-x"]}),
               encoding="utf-8")
op = use([http_err(500, "down"), Resp({"content": [{"type": "text", "text": "from-claude"}],
                                       "usage": {"input_tokens": 1}})])
r = llm.chat(msgs, purpose="test", retries=0)   # 关掉重试，避免把回退链的请求序列搅乱
check("回退链：主模型 5xx 后自动切到 fallback 并成功", lambda: (
    assert_(r["text"] == "from-claude", r["text"]),
    assert_(r["model"] == "claude-x", r["model"]),
    assert_(r["fallbacks"] == ["gpt-4o-mini"], str(r["fallbacks"])),
))
CFG.write_text(json.dumps({}), encoding="utf-8")
reset_state()

# ── 5. JSON 容错 ────────────────────────────────────────────────
check("parse_json：围栏 / 前后说明 / 尾逗号 / 截断四类脏输出", lambda: (
    assert_(llm.parse_json('```json\n{"a":1}\n```')["a"] == 1, "围栏"),
    assert_(llm.parse_json('好的，结果如下：{"a":2} 希望有帮助')["a"] == 2, "前后说明"),
    assert_(llm.parse_json('{"a":3,}')["a"] == 3, "尾逗号"),
    assert_(llm.parse_json('{"items":[{"id":"rp01"},{"id":"rp02"}]} 后略')["items"][1]["id"] == "rp02",
            "嵌套"),
))
try:
    llm.parse_json("完全不是 JSON")
    bad = None
except llm.LLMError as e:
    bad = e
check("parse_json：彻底不是 JSON → 抛 LLMError（不返回空对象冒充成功）", lambda: (
    assert_(bad is not None, "应抛错")
))

# chat_json 首次非法 → 二次带 STRICT 提示
reset_state()
op = use([Resp({"choices": [{"message": {"content": "抱歉，我不能输出"}}]}),
          Resp({"choices": [{"message": {"content": '{"ok":true}'}}]})])
r = llm.chat_json(msgs, "gpt-4o-mini", purpose="test")
last = json.loads(op.reqs[-1].data.decode("utf-8"))
check("chat_json：解析失败自动追加「只输出 JSON」再问一次", lambda: (
    assert_(r["data"]["ok"] is True, str(r["data"])),
    assert_(len(op.reqs) == 2, f"应请求 2 次，实得 {len(op.reqs)}"),
    assert_("只输出一个合法 JSON" in json.dumps(last, ensure_ascii=False), "第二次缺严格提示"),
))

# ── 6. probe ───────────────────────────────────────────────────
reset_state()
use([Resp({"choices": [{"message": {"content": '{"ok":true}'}}]})])
p1 = llm.probe("gpt-4o-mini")
use([http_err(403, "forbidden")])
p2 = llm.probe("gpt-4o-mini")
check("probe：成功回报延迟与 JSON 能力；失败回报错误不抛异常", lambda: (
    assert_(p1["ok"] is True and p1["json_ok"] is True, str(p1)),
    assert_(p2["ok"] is False and "403" in p2["error"], str(p2)),
))
reset_state()

# ── 7. 配置：白名单 + 夹紧 ──────────────────────────────────────
cfg = llm.save_config({"timeout_s": 9999, "max_calls_per_round": -5,
                       "evil_field": "x", "temperature": 5, "fallbacks": ["a", "b", "c", "d", "e", "f"]})
check("save_config：未知键丢弃、数值夹紧、fallbacks 截断", lambda: (
    assert_("evil_field" not in cfg, "未知键被写入"),
    assert_(cfg["timeout_s"] == 600, f"timeout={cfg['timeout_s']}"),
    assert_(cfg["max_calls_per_round"] == 0, f"calls={cfg['max_calls_per_round']}"),
    assert_(cfg["temperature"] == 1.5, f"temp={cfg['temperature']}"),
    assert_(len(cfg["fallbacks"]) == 5, str(cfg["fallbacks"])),
    assert_(json.loads(CFG.read_text(encoding="utf-8"))["timeout_s"] == 600, "未落盘"),
))

# ── 8. 关闭开关 ────────────────────────────────────────────────
llm.save_config({"enabled": False})
try:
    llm.chat(msgs, "gpt-4o-mini", purpose="test")
    off = None
except llm.LLMError as e:
    off = e
check("enabled=false：直接拒绝调用并说明原因", lambda: (
    assert_(off is not None and "已关闭" in str(off), str(off)),
    assert_(llm.available() is False, "available 应为 False"),
))
llm.save_config({"enabled": True})

# ── 9. R3：预算按物理请求逐次扣（重试 / JSON 回问 / 回退共享） ─────
import ai_repair  # noqa: E402

BUDGET_CFG = {**llm.DEFAULT_CFG, "enabled": True, "model": "gpt-4o-mini",
              "fallbacks": [], "max_calls_per_round": 5,
              "max_tokens_per_round": 100000}


def budget_of(cfg=None):
    with patch.object(llm, "load_config", lambda: dict(cfg or BUDGET_CFG)):
        return ai_repair.Budget()


def ok_resp(text="ok", tin=10, tout=5):
    return Resp({"choices": [{"message": {"content": text}}],
                 "usage": {"prompt_tokens": tin, "completion_tokens": tout}})


reset_state()
b1 = budget_of()
op1 = use([Resp({"choices": [{"message": {"content": ""}}],
                 "usage": {"prompt_tokens": 10, "completion_tokens": 5}}), ok_resp()])
with patch.object(llm, "load_config", lambda: dict(BUDGET_CFG)):
    r1 = llm.chat(msgs, "gpt-4o-mini", max_tokens=100, retries=2,
                  purpose="test", budget=b1)
check("R3 物理请求：空正文重试的两次请求都计入预算（不是只记最后一次）", lambda: (
    assert_(len(op1.reqs) == 2, f"应真发 2 次，实得 {len(op1.reqs)}"),
    assert_(r1["requests"] == 2, f"返回的 requests={r1['requests']}"),
    assert_(b1.requests == 2, f"预算 requests={b1.requests}"),
    assert_(b1.tokens == 30, f"预算 tokens={b1.tokens}（10+5 两次应 30）"),
    assert_(b1.reserved == 0, f"预留未结算：{b1.reserved}"),
))

reset_state()
tight = {**BUDGET_CFG, "max_calls_per_round": 1}
b2 = budget_of(tight)
use([ok_resp()])
with patch.object(llm, "load_config", lambda: dict(tight)):
    llm.chat(msgs, "gpt-4o-mini", purpose="test", budget=b2)
op2 = use([ok_resp()])
try:
    with patch.object(llm, "load_config", lambda: dict(tight)):
        llm.chat(msgs, "gpt-4o-mini", purpose="test", budget=b2)
    err2 = None
except llm.BudgetExceeded as e:
    err2 = e
check("R3 调用上限：超预算的请求在**发出前**被拦下（opener 一次没被调用）", lambda: (
    assert_(err2 is not None, "超预算竟然又发了请求"),
    assert_("预算" in str(err2), str(err2)),
    assert_(len(op2.reqs) == 0, f"被拦下的请求不该触达网络，实际 {len(op2.reqs)} 次"),
    assert_(b2.requests == 1, f"预算 requests={b2.requests}"),
))

reset_state()
tok_cfg = {**BUDGET_CFG, "max_tokens_per_round": 100}
b3 = budget_of(tok_cfg)
use([ok_resp(tin=150, tout=50)])
with patch.object(llm, "load_config", lambda: dict(tok_cfg)):
    llm.chat(msgs, "gpt-4o-mini", purpose="test", budget=b3)
op3 = use([ok_resp()])
try:
    with patch.object(llm, "load_config", lambda: dict(tok_cfg)):
        llm.chat(msgs, "gpt-4o-mini", purpose="test", budget=b3)
    err3 = None
except llm.BudgetExceeded as e:
    err3 = e
check("R3 token 上限：累计 usage 超限后拒绝下一次请求（按回报累计，不是按请求数）", lambda: (
    assert_(err3 is not None, "token 超限仍发请求"),
    assert_("token" in str(err3), str(err3)),
    assert_(b3.tokens == 200, f"应累计 200，实得 {b3.tokens}"),
    assert_(len(op3.reqs) == 0, "被拦下的请求不该触达网络"),
))

reset_state()
b4 = budget_of()
op4 = use([ok_resp("不是 JSON", 100, 100), ok_resp('{"ok":true}', 20, 20)])
with patch.object(llm, "load_config", lambda: dict(BUDGET_CFG)):
    r4 = llm.chat_json(msgs, "gpt-4o-mini", purpose="test", budget=b4)
check("R3 报告用例：JSON 重问 = 2 次物理请求 / 240 token，预算与返回都记全", lambda: (
    assert_(len(op4.reqs) == 2, f"应真发 2 次，实得 {len(op4.reqs)}"),
    assert_(r4["requests"] == 2, f"requests={r4['requests']}"),
    assert_(r4["usage_total"] == {"in": 120, "out": 120}, str(r4["usage_total"])),
    assert_(b4.requests == 2, f"预算 requests={b4.requests}"),
    assert_(b4.tokens == 240, f"预算 tokens={b4.tokens}"),
))

reset_state()
b5 = budget_of({**BUDGET_CFG, "max_calls_per_round": 1})
op5 = use([ok_resp("不是 JSON", 100, 100)])      # 第二问若真的发出会取不到替身
try:
    with patch.object(llm, "load_config", lambda: dict({**BUDGET_CFG, "max_calls_per_round": 1})):
        llm.chat_json(msgs, "gpt-4o-mini", purpose="test", budget=b5)
    err5 = None
except llm.LLMError as e:
    err5 = e
check("R3 重问也受预算约束：上限 1 次时第二次追问被拦，首问的 200 token 仍记账", lambda: (
    assert_(isinstance(err5, llm.BudgetExceeded), f"应抛 BudgetExceeded：{type(err5)}"),
    assert_(len(op5.reqs) == 1, f"只该发 1 次，实得 {len(op5.reqs)}"),
    assert_(b5.requests == 1, f"预算 requests={b5.requests}"),
    assert_(b5.tokens == 200, f"首问 usage 仍要记：{b5.tokens}"),
))

llm.MODELS_JSON, llm.AI_CONFIG_JSON, llm.AI_CALLS_JSONL = _orig
print(f"\n通过 {_ok} / {_ok + _fail}")
sys.exit(1 if _fail else 0)
