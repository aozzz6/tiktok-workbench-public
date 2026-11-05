# -*- coding: utf-8 -*-
"""统一 AI 模型调用层（2026-10-24）。

目标：一次接入、处处可用 —— 上层（ai_repair / 未来的其它 AI 能力）只说
`llm.chat(messages)`，不必关心是 OpenAI、DeepSeek、Claude 还是 Gemini。

三种 API 风格（按 `api_style` 字段 > vendor > url 推断自动路由）：
  · openai     —— /chat/completions（OpenAI、Azure 兼容层、DeepSeek、Moonshot、
                  智谱、通义 DashScope 兼容模式、MiniMax、豆包 Ark、SiliconFlow、
                  OpenRouter、Groq、Together、xAI、Ollama / vLLM / LM Studio / llama.cpp）
  · anthropic  —— /v1/messages（Claude 原生）
  · gemini     —— /models/{model}:generateContent（Google 原生）

铁律：
  1. 只有本模块发网络请求，上层只拿结构化结果；
  2. 模型拿不到/调用失败一律抛 LLMError（带回退提示），**绝不返回假数据**；
  3. 审计只记元数据（模型/耗时/token/成败/错误摘要），不落 prompt 全文与 key；
  4. 模型清单来自 ~/.workbuddy/models.json（裸数组或 {models:[]}），可用环境变量
     WB_AI_MODEL / WB_AI_BASE / WB_AI_KEY / WB_AI_DISABLED 覆盖。
"""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import config

MODELS_JSON = config.WORKBUDDY_HOME / "models.json"
AI_CONFIG_JSON = config.DATA_DIR / "ai_config.json"
AI_CALLS_JSONL = config.DATA_DIR / "ai_calls.jsonl"

DEFAULT_CFG = {
    "enabled": True,
    "model": "",                 # 空 = 自动挑选（见 pick()）
    "fallbacks": [],             # 主模型失败后依次尝试的 model id
    "purposes": {},              # 按用途绑定：{"repair.diagnose": "model-id", ...}；空 = 跟随默认
    "timeout_s": 90,
    "max_calls_per_round": 12,   # 一轮修复最多调几次模型
    "max_tokens_per_round": 40000,
    "temperature": 0.2,
    "proxy": "",                 # 空 = 直连（与 tk_reverse 一致）；填 http://127.0.0.1:7897 走代理
}

# 允许绑定的用途键（与前端「按用途绑定」一一对应；probe 永远打指定模型，不参与）
PURPOSES = ("repair.diagnose", "repair.attribute", "prompt_optimizer",
            "ps_product_scan", "reverse")

RETRY_HTTP = {408, 409, 425, 429, 500, 502, 503, 504}
FAIL_COOLDOWN_S = 120.0           # 连续失败 3 次后冷却
FAIL_THRESHOLD = 3
MAX_AUDIT_BYTES = 2 * 1024 * 1024  # 审计文件超 2MB 轮转一次

_LOCK = threading.RLock()
_COOLDOWN: dict[str, dict] = {}   # model_id -> {"fails": n, "until": ts, "last": msg}


class LLMError(RuntimeError):
    """模型不可用 / 调用失败。带 retryable 与 hint，便于上层决定是否换模型。"""

    def __init__(self, message: str, *, code: int | None = None,
                 retryable: bool = False, model: str = ""):
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.model = model
        self.requests = 0            # 本次逻辑调用实际发出的物理请求数（R3）


class BudgetExceeded(LLMError):
    """预算拦截：本次物理请求没有发出去。"""

    def __init__(self, message: str):
        super().__init__(message, retryable=False)


def _merge_usage(a: dict | None, b: dict | None) -> dict:
    """把两次响应的 usage 相加（chat_json 的重问、回退都要计入）。"""
    a, b = a or {}, b or {}
    return {"in": int(a.get("in") or 0) + int(b.get("in") or 0),
            "out": int(a.get("out") or 0) + int(b.get("out") or 0)}


# ────────────────────────── 配置 ──────────────────────────
def load_config() -> dict:
    """项目级 AI 配置（data/ai_config.json）+ 环境变量覆盖。"""
    cfg = dict(DEFAULT_CFG)
    try:
        raw = json.loads(AI_CONFIG_JSON.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            cfg.update({k: v for k, v in raw.items() if k in DEFAULT_CFG})
            p = raw.get("purposes")
            if isinstance(p, dict):
                cfg["purposes"] = {str(a): str(b or "").strip()
                                   for a, b in p.items()
                                   if str(a) in PURPOSES and str(b or "").strip()}
            else:
                cfg["purposes"] = {}
    except (OSError, json.JSONDecodeError):
        pass
    if not isinstance(cfg.get("purposes"), dict):
        cfg["purposes"] = {}
    if os.environ.get("WB_AI_DISABLED", "") not in ("", "0", "false", "False"):
        cfg["enabled"] = False
    if os.environ.get("WB_AI_MODEL"):
        cfg["model"] = os.environ["WB_AI_MODEL"].strip()
    return cfg


def save_config(patch: dict) -> dict:
    """白名单字段合并落盘（未知键忽略，避免写入脏数据）。"""
    cur = load_config()
    for k, v in (patch or {}).items():
        if k not in DEFAULT_CFG:
            continue
        if k == "timeout_s":
            v = max(5, min(600, int(v)))
        elif k == "max_calls_per_round":
            v = max(0, min(60, int(v)))
        elif k == "max_tokens_per_round":
            v = max(0, min(400000, int(v)))
        elif k == "temperature":
            v = max(0.0, min(1.5, float(v)))
        elif k == "fallbacks":
            v = [str(x) for x in (v or [])][:5]
        elif k == "purposes":
            v = {str(a): str(b or "").strip() for a, b in (v or {}).items()
                 if str(a) in PURPOSES}
            v = {a: b for a, b in v.items() if b}
        elif k == "enabled":
            v = bool(v)
        elif k == "proxy":
            v = str(v or "").strip()
        cur[k] = v
    AI_CONFIG_JSON.parent.mkdir(parents=True, exist_ok=True)
    AI_CONFIG_JSON.write_text(json.dumps(cur, ensure_ascii=False, indent=1), encoding="utf-8")
    return cur


# ────────────────────────── 模型清单 ──────────────────────────
def _raw_models() -> list[dict]:
    items: list[dict] = []
    try:
        raw = json.loads(MODELS_JSON.read_text(encoding="utf-8"))
        items = raw if isinstance(raw, list) else (raw.get("models") or [])
    except (OSError, json.JSONDecodeError):
        items = []
    out = []
    for m in items:
        if not isinstance(m, dict):
            continue
        url = str(m.get("url") or "").strip()
        if not url:
            continue
        key = str(m.get("apiKey") or "").strip()
        # 本地推理服务（Ollama/vLLM/LM Studio）通常不要 key
        local = any(h in url for h in ("127.0.0.1", "localhost", "0.0.0.0", "host.docker"))
        if not key and not local:
            continue
        out.append({**m, "url": url, "apiKey": key})
    # 环境变量直接给一套（不必改 models.json）
    base, key2 = os.environ.get("WB_AI_BASE", "").strip(), os.environ.get("WB_AI_KEY", "").strip()
    if base and key2:
        out.insert(0, {"id": os.environ.get("WB_AI_MODEL", "env-model").strip() or "env-model",
                       "name": "环境变量模型", "vendor": os.environ.get("WB_AI_VENDOR", ""),
                       "url": base, "apiKey": key2, "_env": True})
    return out


def _public_row(m: dict, cfg: dict) -> dict:
    mid = str(m.get("id") or m.get("name") or "")
    cool = _COOLDOWN.get(mid) or {}
    return {
        "id": mid,
        "name": str(m.get("name") or mid),
        "vendor": str(m.get("vendor") or ""),
        "style": api_style(m),
        "supports_images": bool(m.get("supportsImages")),
        "supports_reasoning": bool(m.get("supportsReasoning")),
        "key_masked": mask_key(str(m.get("apiKey") or "")),
        "local": any(h in str(m.get("url") or "") for h in ("127.0.0.1", "localhost")),
        "env": bool(m.get("_env")),
        "cooldown_s": max(0, int((cool.get("until") or 0) - time.time())),
        "last_error": cool.get("last") or "",
        "is_default": mid == cfg.get("model"),
    }


def list_models() -> list[dict]:
    cfg = load_config()
    return [_public_row(m, cfg) for m in _raw_models()]


def mask_key(key: str) -> str:
    if not key:
        return "（无 key）"
    if len(key) <= 10:
        return key[:2] + "***"
    return f"{key[:5]}…{key[-4:]}"


def resolve(name: str) -> dict | None:
    if not name:
        return None
    for m in _raw_models():
        if name in (str(m.get("id") or ""), str(m.get("name") or "")):
            return m
    return None


def pick(chain: list[str] | None = None, cfg: dict | None = None) -> dict:
    """按优先级挑一个可用模型：显式 chain → 配置 model → 内置偏好 → 第一个可用。"""
    cfg = cfg or load_config()
    ids = [x for x in (chain or []) if x]
    if not ids:
        ids = ([cfg["model"]] if cfg.get("model") else []) + list(cfg.get("fallbacks") or [])
    for name in ids:
        m = resolve(name)
        if m:
            return m
    models = _raw_models()
    if not models:
        raise LLMError(f"没有可用模型：{MODELS_JSON} 里没有同时具备 url 与 apiKey 的条目")
    for pref in ("deepseek-flash", "TM ·Auto", "gpt-4o-mini", "claude-3-5-haiku"):
        for m in models:
            if str(m.get("id")) == pref:
                return m
    return models[0]


def cooldown_ms(mid: str) -> int:
    """该模型还要冷却多少毫秒（给管理页显示；0 = 不在冷却）。"""
    cool = _COOLDOWN.get(mid) or {}
    return max(0, int(((cool.get("until") or 0) - time.time()) * 1000))


def chain_for(model: str | None = None, purpose: str | None = None) -> list[str]:
    """本次调用的尝试顺序：用途绑定 → 指定模型 → 配置默认 → fallbacks。

    用途绑定（cfg["purposes"]）只影响「没有显式指定模型」的业务调用；
    绑定模型缺 key / 冷却时由 chat() 的循环跳过并继续走后面的候选。
    """
    cfg = load_config()
    seq: list[str] = []
    if model:
        seq.append(model)
    if purpose:
        bound = str((cfg.get("purposes") or {}).get(purpose) or "")
        if bound and bound not in seq:
            seq.append(bound)
    if cfg.get("model") and cfg["model"] not in seq:
        seq.append(cfg["model"])
    for x in cfg.get("fallbacks") or []:
        if x and x not in seq:
            seq.append(x)
    return seq


# ────────────────────────── API 风格路由 ──────────────────────────
def api_style(model: dict) -> str:
    """显式 api_style > vendor/url 推断。返回值：openai | anthropic | gemini。"""
    explicit = str(model.get("api_style") or model.get("apiStyle") or "").strip().lower()
    if explicit in ("openai", "anthropic", "gemini"):
        return explicit
    blob = " ".join(str(model.get(k) or "") for k in ("vendor", "id", "name", "url")).lower()
    if "anthropic" in blob or "claude" in blob:
        return "anthropic"
    if "generativelanguage" in blob or "gemini" in blob or "googleapis.com/v1beta" in blob:
        return "gemini"
    return "openai"


def _endpoint(style: str, model: dict) -> str:
    """拼出实际请求地址。url 既可以是基础地址，也可以是**完整端点**，两种都认。

    ★ T-12（2026-11-05）：用户 models.json 里常直接填完整端点
      （如 https://api.deepseek.com/v1/chat/completions）。旧实现无条件追加
      "/chat/completions"，请求变成 .../chat/completions/chat/completions → 404，
      设置页「测试连接」永远失败。现在：已经是目标端点就原样返回，
      是基础地址才按老规则拼接（老行为一字未改）。
    """
    url = str(model["url"]).rstrip("/")
    if style == "anthropic":
        if url.endswith("/messages"):       # 完整端点：.../v1/messages
            return url
        return url + "/messages" if url.endswith("/v1") else url + "/v1/messages"
    if style == "gemini":
        if ":generateContent" in url:       # 完整端点：.../models/<id>:generateContent
            return url
        base = url if "/v1beta" in url or "/v1/" in url else url + "/v1beta"
        mid = str(model.get("id") or model.get("name") or "")
        return f"{base}/models/{mid}:generateContent"
    if url.endswith("/chat/completions"):   # 完整端点：.../v1/chat/completions
        return url
    return url + "/chat/completions"


def _headers(style: str, model: dict) -> dict:
    key = str(model.get("apiKey") or "")
    if style == "anthropic":
        h = {"Content-Type": "application/json", "anthropic-version": "2023-06-01"}
        if key:
            h["x-api-key"] = key
        return h
    if style == "gemini":
        h = {"Content-Type": "application/json"}
        if key:
            h["x-goog-api-key"] = key
        return h
    h = {"Content-Type": "application/json"}
    if key:
        h["Authorization"] = "Bearer " + key
    return h


def _split_system(messages: list[dict]) -> tuple[str, list[dict]]:
    system = "\n".join(str(m.get("content") or "") for m in messages
                       if m.get("role") == "system").strip()
    rest = [m for m in messages if m.get("role") != "system"]
    return system, rest


def _body(style: str, model: dict, messages: list[dict], max_tokens: int,
          temperature: float, json_mode: bool, token_field: str = "max_tokens") -> dict:
    system, rest = _split_system(messages)
    if style == "anthropic":
        body = {"model": str(model.get("id") or model.get("name")),
                "max_tokens": max_tokens,
                "temperature": temperature,
                "messages": [{"role": m.get("role", "user"),
                              "content": [{"type": "text", "text": str(m.get("content") or "")}]}
                             for m in rest]}
        if system:
            body["system"] = system
        return body
    if style == "gemini":
        body = {"contents": [{"role": "model" if m.get("role") == "assistant" else "user",
                              "parts": [{"text": str(m.get("content") or "")}]} for m in rest],
                "generationConfig": {"maxOutputTokens": max_tokens, "temperature": temperature}}
        if json_mode:
            body["generationConfig"]["responseMimeType"] = "application/json"
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        return body
    body = {"model": str(model.get("id") or model.get("name")),
            "messages": messages,
            token_field: max_tokens,
            "temperature": temperature}
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    return body


def _extract(style: str, data: dict) -> tuple[str, dict]:
    """统一取出 (正文, usage)。三套响应结构在这里被抹平。"""
    if style == "anthropic":
        parts = [b.get("text") or "" for b in (data.get("content") or [])
                 if isinstance(b, dict) and b.get("type") == "text"]
        u = data.get("usage") or {}
        return "".join(parts), {"in": u.get("input_tokens"), "out": u.get("output_tokens")}
    if style == "gemini":
        cands = data.get("candidates") or []
        parts = []
        for c in cands:
            for p in ((c.get("content") or {}).get("parts") or []):
                parts.append(p.get("text") or "")
        u = data.get("usageMetadata") or {}
        return "".join(parts), {"in": u.get("promptTokenCount"), "out": u.get("candidatesTokenCount")}
    try:
        msg = (data.get("choices") or [{}])[0].get("message") or {}
    except (IndexError, AttributeError):
        msg = {}
    content = msg.get("content")
    if isinstance(content, list):        # 少数网关返回分段
        text = "".join(str(p.get("text") or "") for p in content if isinstance(p, dict))
    else:
        text = str(content or "")
    if not text and msg.get("reasoning_content"):   # 推理模型把正文写进 reasoning 的情况
        text = str(msg.get("reasoning_content") or "")
    u = data.get("usage") or {}
    return text, {"in": u.get("prompt_tokens"), "out": u.get("completion_tokens")}


def _opener(cfg: dict):
    proxy = (cfg.get("proxy") or os.environ.get("WB_AI_PROXY") or "").strip()
    handler = urllib.request.ProxyHandler({"http": proxy, "https": proxy} if proxy else {})
    return urllib.request.build_opener(handler)


def _audit(rec: dict) -> None:
    try:
        if AI_CALLS_JSONL.exists() and AI_CALLS_JSONL.stat().st_size > MAX_AUDIT_BYTES:
            AI_CALLS_JSONL.replace(AI_CALLS_JSONL.with_suffix(".jsonl.1"))
        with AI_CALLS_JSONL.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _cooling(model_id: str) -> float:
    c = _COOLDOWN.get(model_id) or {}
    left = (c.get("until") or 0) - time.time()
    return left if left > 0 else 0.0


def _note_fail(model_id: str, msg: str) -> None:
    with _LOCK:
        c = _COOLDOWN.setdefault(model_id, {"fails": 0, "until": 0, "last": ""})
        c["fails"] = int(c.get("fails") or 0) + 1
        c["last"] = msg[:160]
        if c["fails"] >= FAIL_THRESHOLD:
            c["until"] = time.time() + FAIL_COOLDOWN_S
            c["fails"] = 0


def _note_ok(model_id: str) -> None:
    with _LOCK:
        _COOLDOWN.pop(model_id, None)


# ────────────────────────── 调用 ──────────────────────────
def chat(messages: list[dict], model: str | None = None, *, max_tokens: int = 1200,
         temperature: float | None = None, json_mode: bool = False,
         timeout: float | None = None, purpose: str = "chat",
         retries: int = 2, budget=None) -> dict:
    """按回退链调用，返回 {text, model, style, usage, latency_ms, fallbacks, requests}。

    全部候选都失败才抛 LLMError（上层据此回退到规则结论）。

    ★ R3：budget（ai_repair.Budget）按**每一次物理 HTTP 请求**预检与记账 ——
      重试、空正文重问、回退链、chat_json 的二次追问共用同一份预算，
      不会再出现「逻辑调用 1 次、实际请求 2 次、只记最后一次 usage」。
      budget_note() 说明：token 上限是保守估算（按输出上限预留 + 服务端回报累计），
      不保证精确费用封顶；requests 才是真实请求数。
    """
    cfg = load_config()
    if not cfg.get("enabled"):
        raise LLMError("AI 已关闭（ai_config.json enabled=false 或 WB_AI_DISABLED=1）")
    bound = str((cfg.get("purposes") or {}).get(purpose) or "") if purpose else ""
    seq = chain_for(model, purpose) or [""]
    tried: list[str] = []
    last: LLMError | None = None
    counter = {"requests": 0}
    for name in seq:
        try:
            m = pick([name] if name else None, cfg)
        except LLMError as e:
            last = e
            continue
        mid = str(m.get("id") or m.get("name") or "")
        if mid in tried:
            continue
        left = _cooling(mid)
        if left > 0:
            last = LLMError(f"{mid} 冷却中（{int(left)}s）", model=mid)
            tried.append(mid)
            continue
        tried.append(mid)
        try:
            out = _call_one(m, messages, max_tokens=max_tokens,
                            temperature=cfg["temperature"] if temperature is None else temperature,
                            json_mode=json_mode,
                            timeout=timeout or cfg["timeout_s"],
                            purpose=purpose, retries=retries,
                            budget=budget, counter=counter,
                            bound=bool(bound and mid == bound))
            out["fallbacks"] = tried[:-1]
            out["requests"] = counter["requests"]
            out["charged"] = budget is not None
            return out
        except BudgetExceeded:
            raise                       # 预算耗尽不能再换模型再打一轮
        except LLMError as e:
            last = e
            continue
    if last is not None:
        last.requests = counter["requests"]
        raise last
    raise LLMError("没有可用模型（回退链为空）", model=",".join(tried))


def _call_one(model: dict, messages: list[dict], *, max_tokens: int, temperature: float,
              json_mode: bool, timeout: float, purpose: str, retries: int,
              budget=None, counter: dict | None = None, bound: bool = False) -> dict:
    mid = str(model.get("id") or model.get("name") or "")
    style = api_style(model)
    url = _endpoint(style, model)
    cfg = load_config()
    opener = _opener(cfg)
    max_out = max_tokens
    token_field = "max_tokens"
    last_err: LLMError | None = None
    t0 = time.time()
    extra = {"bound": True} if bound else {}
    for attempt in range(retries + 1):
        body = _body(style, model, messages, max_out, temperature, json_mode, token_field)
        req = urllib.request.Request(url, data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                                     headers=_headers(style, model))
        ticket = None
        if budget is not None:
            # 预检在请求发出之前：超预算就不打这一枪（BudgetExceeded 直接冒泡）
            ticket = budget.before_request(max_tokens=max_out)
        if counter is not None:
            counter["requests"] = int(counter.get("requests") or 0) + 1
        try:
            with opener.open(req, timeout=timeout) as r:
                data = json.loads(r.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            if ticket is not None:
                budget.note_failure(ticket)
            raw = e.read()[:300].decode("utf-8", "replace")
            # 部分网关只认 max_completion_tokens（新版 OpenAI 推理模型）→ 换字段重试一次
            if e.code == 400 and token_field == "max_tokens" and "max_completion_tokens" in raw:
                token_field = "max_completion_tokens"
                last_err = LLMError(f"{mid} 400：{raw[:120]}", code=400, retryable=True, model=mid)
                continue
            retryable = e.code in RETRY_HTTP
            last_err = LLMError(f"{mid} HTTP {e.code}：{raw[:200]}", code=e.code,
                                retryable=retryable, model=mid)
            if retryable and attempt < retries:
                time.sleep(min(8, 2 ** attempt))
                continue
            _note_fail(mid, str(last_err))
            _audit({"ts": time.time(), "model": mid, "style": style, "purpose": purpose,
                    "ok": False, "code": e.code, "error": raw[:200],
                    "latency_ms": int((time.time() - t0) * 1000), "attempt": attempt + 1,
                    **extra})
            raise last_err from None
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            if ticket is not None:
                budget.note_failure(ticket)
            msg = getattr(e, "reason", e)
            last_err = LLMError(f"{mid} 网络不可达：{msg}", retryable=True, model=mid)
            if attempt < retries:
                time.sleep(min(8, 2 ** attempt))
                continue
            _note_fail(mid, str(last_err))
            _audit({"ts": time.time(), "model": mid, "style": style, "purpose": purpose,
                    "ok": False, "error": str(msg)[:200],
                    "latency_ms": int((time.time() - t0) * 1000), "attempt": attempt + 1,
                    **extra})
            raise last_err from None
        except json.JSONDecodeError as e:      # 200 + HTML 错误页
            if ticket is not None:
                budget.note_failure(ticket)
            last_err = LLMError(f"{mid} 返回非 JSON：{e}", retryable=True, model=mid)
            if attempt < retries:
                time.sleep(min(4, 2 ** attempt))
                continue
            _note_fail(mid, str(last_err))
            raise last_err from None

        text, usage = _extract(style, data)
        if ticket is not None:
            budget.note_usage(ticket, usage)   # 拿到响应的都要记（含空正文重问）
        if not text.strip():
            # 空正文：推理模型会把预算全花在 reasoning 上（实测 deepseek-flash 会）
            last_err = LLMError(f"{mid} 返回空正文", retryable=True, model=mid)
            if attempt < retries:
                max_out = min(max_out * 2, max_out * 4)
                continue
            _note_fail(mid, str(last_err))
            _audit({"ts": time.time(), "model": mid, "style": style, "purpose": purpose,
                    "ok": False, "error": "empty content",
                    "latency_ms": int((time.time() - t0) * 1000), "attempt": attempt + 1,
                    **extra})
            raise last_err from None

        _note_ok(mid)
        latency = int((time.time() - t0) * 1000)
        _audit({"ts": time.time(), "model": mid, "style": style, "purpose": purpose,
                "ok": True, "latency_ms": latency,
                "tokens_in": usage.get("in"), "tokens_out": usage.get("out"),
                "chars_out": len(text), "attempt": attempt + 1, **extra})
        return {"text": text, "model": mid, "style": style, "usage": usage,
                "latency_ms": latency, "attempt": attempt + 1}
    raise last_err or LLMError(f"{mid} 调用失败", model=mid)


# ────────────────────────── JSON 解析容错 ──────────────────────────
STRICT_HINT = ("上一次输出不是合法 JSON（可能被截断或夹了解释文字）。"
               "这次只输出一个合法 JSON 对象，不要 Markdown 围栏、不要任何前后说明。")


def parse_json(text: str) -> dict:
    """5 级容错：直接解析 → 去围栏 → 截取首尾大括号 → 修尾逗号 → 截断抢救。"""
    s = (text or "").strip()
    if not s:
        raise LLMError("模型输出为空")
    try:
        v = json.loads(s)
        if isinstance(v, dict):
            return v
    except json.JSONDecodeError:
        pass
    fence = s.replace("```json", "```").split("```")
    for part in fence:
        p = part.strip()
        if p.startswith("{") and p.endswith("}"):
            try:
                v = json.loads(p)
                if isinstance(v, dict):
                    return v
            except json.JSONDecodeError:
                pass
    i, j = s.find("{"), s.rfind("}")
    if i >= 0 and j > i:
        frag = s[i:j + 1]
        for cand in (frag, frag.replace(",}", "}").replace(",]", "]")):
            try:
                v = json.loads(cand)
                if isinstance(v, dict):
                    return v
            except json.JSONDecodeError:
                pass
        # 截断抢救：从头部逐个括号配平，取最后一个完整对象
        depth, start = 0, None
        for k, ch in enumerate(frag):
            if ch == "{":
                if depth == 0:
                    start = k
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0 and start is not None:
                    try:
                        v = json.loads(frag[start:k + 1])
                        if isinstance(v, dict):
                            return v
                    except json.JSONDecodeError:
                        pass
                    start = None
    raise LLMError(f"模型未返回合法 JSON：{s[:160]}")


def chat_json(messages: list[dict], model: str | None = None, *, max_tokens: int = 1600,
              purpose: str = "json", retries: int = 2, budget=None) -> dict:
    """要 JSON 的调用：解析失败自动加「只输出 JSON」再问一次。

    ★ R3：这里可能**真的打两次网络**（首发 + 严格重问，每次还可能内部重试/回退）。
      返回的 `requests` 是物理请求总数、`usage_total` 是两次响应 usage 之和，
      上层若没有预算钩子（测试替身）就按这两个字段记账，不能只记最后一次。
    """
    out = chat(messages, model, max_tokens=max_tokens, json_mode=True,
               purpose=purpose, retries=retries, budget=budget)
    try:
        return {**out, "data": parse_json(out["text"]),
                "requests": int(out.get("requests") or 1),
                "usage_total": dict(out.get("usage") or {})}
    except LLMError as first:
        out2 = chat(list(messages) + [{"role": "user", "content": STRICT_HINT}],
                    model, max_tokens=max_tokens, json_mode=True,
                    purpose=purpose, retries=0, budget=budget)
        try:
            return {**out2, "data": parse_json(out2["text"]),
                    "requests": int(out.get("requests") or 1) + int(out2.get("requests") or 1),
                    "usage_total": _merge_usage(out.get("usage"), out2.get("usage")),
                    "charged": bool(out.get("charged") or out2.get("charged"))}
        except LLMError as second:
            raise LLMError(f"{second}（首次：{str(first)[:120]}）", model=out.get("model", "")) from None


def probe(model: str | None = None, timeout: float = 25) -> dict:
    """探活：发一条极短请求，验证「连得上 + 能出 JSON」。给设置页的「测试连接」用。

    ★ P2-6（2026-11-02）：显式传入的模型不在清单里时**抛 LLMError**，不再静默落到默认/
      内置偏好模型 —— 否则「测试连接」会测了另一个模型却报 ok，等于骗人。
      合法模型与不传模型（= 默认链）的路径完全不变。
    """
    if model and resolve(model) is None:
        raise LLMError(f"模型不存在：{model}", model=model)
    t0 = time.time()
    try:
        out = chat([{"role": "user", "content": '只输出 JSON：{"ok":true}'}],
                   model, max_tokens=64, temperature=0, json_mode=True,
                   timeout=timeout, purpose="probe", retries=0)
        try:
            parse_json(out["text"])
            json_ok = True
        except LLMError:
            json_ok = False
        return {"ok": True, "model": out["model"], "style": out["style"],
                "latency_ms": int((time.time() - t0) * 1000), "json_ok": json_ok,
                "usage": out.get("usage"), "preview": out["text"][:120]}
    except LLMError as e:
        return {"ok": False, "model": e.model or (model or ""), "error": str(e),
                "latency_ms": int((time.time() - t0) * 1000)}


def available() -> bool:
    """有没有可用的模型（不联网，只看配置与清单）。"""
    try:
        if not load_config().get("enabled"):
            return False
        return bool(_raw_models())
    except Exception:                       # noqa: BLE001
        return False
