"""Isolated provider onboarding tests with a real loopback HTTP provider, no internet.
From repo root: python webapp/tests/ai_provider_flow.test.py
Optional browser: --browser (requires Playwright Python + Chromium via CHROMIUM_PATH).
"""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch
from urllib.request import urlopen

WEB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WEB))
import _isolate  # noqa: F401,E402
import app  # noqa: E402
import llm  # noqa: E402
import tk_reverse  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

VALID_KEY = "fixture-valid-token"
BAD_KEY = "fixture-wrong-token"
calls = []


class Fixture(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def answer(self, payload, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(payload).encode())

    def do_GET(self):
        calls.append(("GET", self.path, self.headers.get("Authorization"), None))
        if self.headers.get("Authorization") != f"Bearer {VALID_KEY}":
            self.answer({"raw": BAD_KEY}, 401)
        elif self.path.endswith("/models"):
            self.answer({"data": [{"id": "same-model"}, {"id": "org/vision"}, {"id": "same-model"}]})
        else:
            self.answer({}, 404)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
        auth = (self.headers.get("x-api-key") or self.headers.get("x-goog-api-key")
                or (self.headers.get("Authorization") or "").removeprefix("Bearer "))
        calls.append(("POST", self.path, auth, body))
        if auth != VALID_KEY:
            self.answer({"error": "Provider echoes " + auth}, 401)
            return
        if self.path.startswith("/wrong"):
            self.answer({}, 404)
            return
        out = '{"ok":true,"subject":"fixture","fixture":{"1":"translated"}}'
        if self.path.endswith("/chat/completions"):
            self.answer({"choices": [{"message": {"content": out}}]})
        elif self.path.endswith("/responses"):
            self.answer({"output": [{"type": "message", "content": [{"type": "output_text", "text": out}]}]})
        elif self.path.endswith("/messages"):
            self.answer({"content": [{"type": "text", "text": out}]})
        elif self.path.endswith(":generateContent"):
            self.answer({"candidates": [{"content": {"parts": [{"text": out}]}}]})
        else:
            self.answer({}, 404)


def provider(base, pid, *rows, style="openai", key=VALID_KEY):
    return {"provider_id": pid, "provider_name": pid.title(), "url": base,
            "api_style": style, "apiKey": key, "models": [
                {"id": row, "images": row.endswith("vision")} for row in rows]}


def api(base, tmp):
    with patch.object(app.config, "DATA_DIR", tmp), patch.object(llm, "MODELS_JSON", tmp / "models.json"), \
         patch.object(llm, "AI_CONFIG_JSON", tmp / "ai_config.json"), \
         patch.object(llm, "AI_CALLS_JSONL", tmp / "calls.jsonl"), \
         patch.object(tk_reverse, "MODELS_JSON", tmp / "models.json"), \
         patch.object(app, "PS_SCAN_CACHE", tmp / "scan.json"), \
         patch.dict(os.environ, {"WB_AI_KEY": "", "WB_AI_BASE": "", "WB_AI_MODEL": "", "WB_AI_DISABLED": "", "WB_AI_PROXY": ""}):
        client = TestClient(app.app)
        fresh = provider(base + "/one/v1", "one", "same-model", "org/vision")
        ret = client.post("/api/ai/providers/discover", json=fresh)
        assert ret.status_code == 200 and ret.json()["models"] == ["org/vision", "same-model"], ret.text
        assert not llm.MODELS_JSON.exists() and len(calls) == 1 and calls[-1][0] == "GET"
        print("PASS exact host model listing, dedupe, no disk write or inference")
        before = len(calls)
        out = client.post("/api/ai/providers", json=fresh)
        assert out.status_code == 200, out.text
        assert len(calls) == before and VALID_KEY not in out.text and "fixture" not in out.text
        assert not llm.load_config()["enabled"] and not llm.load_config()["model"] and not llm.available()
        assert app._ps_vision_model() is None and llm.resolve("one:org/vision")["supportsImages"]
        print("PASS first save writes locally, stays disabled, returns no key")
        second = provider(base + "/two/v1", "two", "same-model", key=BAD_KEY)
        assert client.post("/api/ai/providers", json=second).status_code == 200
        assert client.post("/api/ai/config", json={"enabled": True, "model": "one:same-model",
                                                    "purposes": {"ps_product_scan": "one:org/vision"}}).status_code == 200
        assert app._ps_vision_model() == "one:org/vision"
        assert llm.resolve("one:same-model")["provider_id"] == "one"
        assert llm.resolve("two:same-model")["provider_id"] == "two"
        before = len(calls)
        ret = client.post("/api/ai/test", json={"model": "two:same-model"})
        assert ret.status_code == 200 and not ret.json()["ok"] and BAD_KEY not in ret.text, ret.text
        assert len(calls) == before + 1 and calls[-1][1] == "/two/v1/chat/completions" and calls[-1][3]["model"] == "same-model"
        assert BAD_KEY not in llm.AI_CALLS_JSONL.read_text()
        print("PASS same model ID on two providers, selected auth failure never falls back or exposes key")
        before = len(calls)
        invalid = provider(base + "/wrong/v1", "wrong", "same-model")
        assert client.post("/api/ai/providers", json=invalid).status_code == 200
        ret = client.post("/api/ai/test", json={"model": "wrong:same-model"})
        assert not ret.json()["ok"] and len(calls) == before + 1 and "不存在" in ret.json()["error"]
        assert client.post("/api/ai/test", json={"model": "missing"}).status_code == 400
        assert len(calls) == before + 1
        print("PASS wrong endpoint and unknown model without fallbacks")
        cfg_before = llm.AI_CONFIG_JSON.read_bytes()
        legacy = {"id": "legacy", "name": "Legacy", "url": base + "/legacy/v1", "apiKey": VALID_KEY, "unknown": {"keep": True}}
        stored = json.loads(llm.MODELS_JSON.read_text())
        llm.MODELS_JSON.write_text(json.dumps({"keep": {"top": True}, "models": [legacy] + stored}))
        refreshed = provider(fresh["url"], "one", "another", key="")
        assert client.post("/api/ai/providers", json=refreshed).status_code == 200
        persisted = json.loads(llm.MODELS_JSON.read_text())
        assert persisted["models"][0] == legacy and persisted["keep"] == {"top": True}
        assert llm.AI_CONFIG_JSON.read_bytes() == cfg_before and llm.resolve("one:another")["apiKey"] == VALID_KEY
        assert "one:org/vision" == app._ps_vision_model()
        print("PASS update retains provider key, existing models, legacy fields, default and purposes")
        scan = client.post("/api/ps/product-scan", json={"images": [{"data": "AA==", "mime": "image/png"}]}).json()
        assert scan.get("ok") and scan["model"] == "one:org/vision", scan
        assert calls[-1][3]["messages"][0]["content"][1]["type"] == "image_url"
        print("PASS product recognition sends image via exact bound Chat Completions route")
        for proto, prefix, suffix, model in [
            ("responses", "/resp/v1", "/responses", "org/vision"),
            ("anthropic", "/claude/v1", "/messages", "native-text"),
            ("gemini", "/google/v1beta", "/models/native-text:generateContent", "native-text"),
        ]:
            payload = provider(base + prefix, proto, model, style=proto)
            saved = client.post("/api/ai/providers", json=payload)
            assert saved.status_code == 200, saved.text
            ret = client.post("/api/ai/test", json={"model": f"{proto}:{model}"}).json()
            assert ret["ok"], ret
            assert calls[-1][1] == prefix + suffix, calls[-1]
            assert proto == "gemini" or calls[-1][3]["model"] == model, calls[-1]
            print("PASS selected " + proto + " uses native transport and real model ID")
        native = llm.resolve("responses:org/vision")
        text = tk_reverse._chat(native, [{"type": "text", "text": "fixture"},
                                            {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA=="}}], retries=0)
        assert text and calls[-1][3]["input"][0]["content"][1]["type"] == "input_image"
        print("PASS reverse uses native Responses image request")
        script = WEB.parent / "pipeline" / "scripts" / "auto_translate.py"
        spec = importlib.util.spec_from_file_location("fixture_auto_translate", script)
        translate = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(translate)
        with patch.object(translate, "MODELS_JSON", llm.MODELS_JSON):
            selected = translate.load_model()
            out = translate.call_llm(selected, [{"account": "fixture", "row": 1, "text": "fixture"}])
            assert selected["id"] == "one:same-model" and out["fixture"]["1"] == "translated", out
        print("PASS pipeline translation uses provider's remote ID and persists selected route")


def browser(base, tmp):
    import socket
    from playwright.sync_api import sync_playwright, expect

    root = tmp / "browser"
    root.mkdir()
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    origin = f"http://127.0.0.1:{port}"
    env = {**os.environ, "WB_HOME": str(root / "workbuddy"), "WB_REVERSE_DIR": str(root / "reverse"),
           "WB_AI_KEY": "", "WB_AI_BASE": "", "WB_AI_MODEL": "", "WB_AI_DISABLED": "",
           "WB_AI_PROXY": ""}
    def start():
        code = ("from pathlib import Path; import app, config, llm, sys, uvicorn; "
                "config.DATA_DIR = Path(sys.argv[2]); llm.AI_CONFIG_JSON = config.DATA_DIR / 'ai_cfg.json'; "
                "llm.AI_CALLS_JSONL = config.DATA_DIR / 'ai_calls.jsonl'; "
                "uvicorn.run(app.app, host='127.0.0.1', port=int(sys.argv[1]), log_level='error')")
        proc = subprocess.Popen([sys.executable, "-c", code, str(port), str(root)], cwd=WEB, env=env)
        for _ in range(100):
            if proc.poll() is not None:
                raise RuntimeError("isolated workbench stopped")
            try:
                with urlopen(origin + "/api/ai/config", timeout=1):
                    return proc
            except OSError:
                time.sleep(0.1)
        proc.terminate()
        raise RuntimeError("isolated workbench startup timed out")

    def open_page(page):
        page.goto(origin, wait_until="domcontentloaded")
        page.get_by_text("AI 接入", exact=True).first.click()
        expect(page.get_by_role("heading", name="模型服务商")).to_be_visible()

    proc = start()
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(executable_path=os.environ.get("CHROMIUM_PATH", "/usr/bin/chromium"),
                                         args=["--no-sandbox"])
            ctx = browser.new_context()
            ctx.route("**/*", lambda route: route.continue_() if route.request.url.startswith(origin + "/") else route.abort())
            page = ctx.new_page()
            page.on("dialog", lambda dialog: dialog.accept())
            open_page(page)
            page.get_by_role("button", name="添加模型服务商").click()
            page.locator("#ai-provider-preset").select_option("deepseek")
            expect(page.locator("#ai-provider-url")).to_have_value("https://api.deepseek.com")
            page.locator("#ai-provider-preset").select_option("")
            page.locator("#ai-provider-id").fill("fixture")
            page.locator("#ai-provider-name").fill("Browser fixture")
            page.locator("#ai-provider-url").fill(base + "/browser/v1")
            page.locator("#ai-provider-key").fill(VALID_KEY)
            page.get_by_role("button", name="获取可用模型").click()
            page.locator("#ai-provider-found").select_option("same-model")
            page.get_by_role("button", name="添加模型", exact=True).click()
            page.locator("#ai-provider-model-1").fill("org/vision")
            page.locator("#ai-provider-image-1").check()
            expect(page.locator("#ai-provider-key")).to_have_value(VALID_KEY)
            expect(page.locator("#ai-provider-key")).not_to_have_attribute("value", VALID_KEY)
            before = len(calls)
            page.get_by_role("button", name="保存服务商和模型").click()
            expect(page.get_by_role("button", name="Browser fixture")).to_be_visible()
            assert len(calls) == before
            expect(page.locator("#ai-enabled")).not_to_be_checked()
            page.get_by_role("button", name="测试连通").click()
            expect(page.get_by_text("连通 ✓", exact=False).first).to_be_visible()
            page.get_by_role("button", name="设为默认").click()
            expect(page.locator("#ai-default")).to_have_value("fixture:same-model")
            expect(page.locator("#ai-enabled")).not_to_be_checked()
            page.locator("#ai-enabled").check()
            expect(page.locator("#ai-enabled")).to_be_checked()
            print("PASS Chromium: preset/custom, discover, add vision row, save without request, exact test, default and enable")
            proc.terminate()
            proc.wait(timeout=10)
            proc = start()
            open_page(page)
            expect(page.get_by_role("button", name="Browser fixture")).to_be_visible()
            expect(page.locator("#ai-default")).to_have_value("fixture:same-model")
            expect(page.locator("#ai-enabled")).to_be_checked()
            page.get_by_role("button", name="编辑服务商").click()
            expect(page.locator("#ai-provider-key")).to_have_value("")
            page.get_by_role("button", name="取消").click()
            expect(page.get_by_role("dialog")).to_have_count(0)
            print("PASS Chromium: real app restart preserves selection and keeps saved key out of edit form")
            browser.close()
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def main():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Fixture)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with tempfile.TemporaryDirectory() as root:
            base = f"http://127.0.0.1:{server.server_port}"
            api(base, Path(root))
            if "--browser" in sys.argv:
                browser(base, Path(root))
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
