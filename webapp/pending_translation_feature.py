# -*- coding: utf-8 -*-
"""pending_translation_feature.py — 待译队列自动暴露 + UI 角标"""
from pathlib import Path

APP = Path(r"D:\workbuddy_project\tiktok\webapp\app.py")
JS = Path(r"D:\workbuddy_project\tiktok\webapp\static\app.js")

# ═══ 1. app.py：ledger 同步完成钩子 → 写待译队列；overview 暴露数量 ═══
t = APP.read_text(encoding="utf-8")

old = '''runner.MANAGER.add_finish_hook(_auto_ledger_sync)'''
new = '''runner.MANAGER.add_finish_hook(_auto_ledger_sync)

PENDING_TRANS_FILE = Path(__file__).resolve().parent / "data" / "pending_translation.json"


def _refresh_pending_translation() -> int:
    """台账同步完成后刷新待译队列：读 post_update_flow 导出的 pending_trans.json
    （{acc: [{row, vid, text}]}），非空则写入 data/pending_translation.json 供
    Agent 自动翻译流程与总览角标消费；空则删除队列文件。返回待译条数。"""
    import json as _json
    src = config.PIPE / "output" / "pending_trans.json"
    try:
        pending = _json.loads(src.read_text(encoding="utf-8")) if src.exists() else {}
    except Exception:
        pending = {}
    items = []
    for acc, arr in (pending.items() if isinstance(pending, dict) else []):
        if not isinstance(arr, list):
            continue
        for it in arr:
            if isinstance(it, dict) and it.get("text") and it.get("vid"):
                items.append({"account": acc, "row": it.get("row"),
                              "vid": str(it.get("vid")), "text": str(it["text"])[:2000]})
    PENDING_TRANS_FILE.parent.mkdir(parents=True, exist_ok=True)
    if items:
        PENDING_TRANS_FILE.write_text(
            _json.dumps({"ts": __import__("time").time(), "total": len(items),
                         "items": items}, ensure_ascii=False, indent=1),
            encoding="utf-8")
    elif PENDING_TRANS_FILE.exists():
        PENDING_TRANS_FILE.unlink()
    return len(items)


def _read_pending_translation() -> dict:
    try:
        import json as _json
        d = _json.loads(PENDING_TRANS_FILE.read_text(encoding="utf-8"))
        return {"total": d.get("total", 0), "ts": d.get("ts")} if d.get("total") else {"total": 0}
    except Exception:
        return {"total": 0}


def _on_ledger_sync_done(run) -> None:
    """台账同步（write+导出待译）完成后 → 刷新待译队列，供 Agent 自动翻译。"""
    if run.kind != "ledger" or run.status != "done":
        return
    try:
        n = _refresh_pending_translation()
        if n:
            print(f"[pending] 待译队列已更新：{n} 条（Agent 将自动翻译回填）", flush=True)
    except Exception as e:
        print(f"[pending] 待译队列刷新失败: {e}", flush=True)


runner.MANAGER.add_finish_hook(_on_ledger_sync_done)'''
assert old in t, "hook"
t = t.replace(old, new, 1)

# overview 响应暴露待译数
old = '''        "schedule": scheduler.load(),
        "diagnostics": config.diagnostics(),'''
new = '''        "schedule": scheduler.load(),
        "pending_translation": _read_pending_translation(),
        "diagnostics": config.diagnostics(),'''
assert old in t, "overview field"
t = t.replace(old, new, 1)
APP.write_text(t, encoding="utf-8")
print("1 app.py ok")

# ═══ 2. app.js：总览诊断条追加「待翻译 N 条」角标 ═══
t = JS.read_text(encoding="utf-8")
old = '''    renderDiag(d.diagnostics);'''
new = '''    renderDiag(d.diagnostics);
    const pend = (d.pending_translation || {}).total || 0;
    if (pend) {
      $("#diag-bar").insertAdjacentHTML("beforeend",
        `<div class="pill warn" title="译文已排入自动翻译队列，完成后自动回填台账">待翻译 ${pend} 条</div>`);
    }'''
assert old in t, "js pending"
t = t.replace(old, new, 1)
JS.write_text(t, encoding="utf-8")
print("2 app.js ok")

# ═══ 3. CSS：pill.warn 样式兜底 ═══
c = Path(r"D:\workbuddy_project\tiktok\webapp\static\style.css")
cs = c.read_text(encoding="utf-8")
if ".pill.warn" not in cs:
    cs += '\n.pill.warn{background:rgba(217,119,6,.12);color:#92400e;border:1px solid rgba(217,119,6,.35);font-weight:600}\n'
    c.write_text(cs, encoding="utf-8")
    print("3 css ok")
else:
    print("3 css 已有 .pill.warn")
