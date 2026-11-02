# -*- coding: utf-8 -*-
"""redesign_apply.py — 把「工业实用」定调落地到真实页面（P0 为主）
策略：重绑既有 CSS 变量值（全文档 var() 自动生效）+ 定点清除渐变/硬编码色 + 追加新规则。
不动任何 DOM 结构与业务 JS 逻辑（仅 renderLedgerCards 加状态类）。
"""
from pathlib import Path

p = Path(r"D:\workbuddy_project\tiktok\webapp\static\style.css")
t = p.read_text(encoding="utf-8")
fixes = []

# ── 1. 变量重绑：暖纸面 + 墨字 + 工业琥珀（既有 var 名不变，全文档自动生效） ──
old_root = ''':root{

  --pri:#2563eb; --pri-d:#1d4ed8; --red:#dc2626; --green:#16a34a; --amber:#d97706;

  --purple:#7c3aed; --teal:#0d9488; --gray:#94a3b8;

  --ink:#1e293b; --sub:#64748b; --line:#e2e8f0; --bg:#f1f5f9; --card:#fff;

  --term-bg:#0f172a; --term-fg:#cbd5e1;

}'''
new_root = ''':root{

  /* ═══ 设计令牌 · 工业实用 Industrial（2026-10-15 定调） ═══
     暖纸面底 + 墨字 + 琥珀强调；数据读数一律等宽（--font-data） */
  --pri:#b45309; --pri-d:#92400e;                       /* 主行动 = 工业琥珀 */
  --red:#b91c1c; --green:#15803d; --amber:#b45309;

  --purple:#7c3aed; --teal:#0d9488; --gray:#a8a29e;

  --ink:#1c1917; --sub:#78716c; --line:rgba(28,25,23,.10);   /* 发丝线 */
  --bg:#f4f3f0; --card:#fbfaf8;                            /* 暖纸面 */

  --term-bg:#141210; --term-fg:#d6d3d1;

  --font-data:"Cascadia Code","JetBrains Mono",Consolas,monospace;
  --side-bg:#141210; --side-line:rgba(245,245,244,.08);
  --shadow-1:0 1px 2px rgba(28,25,23,.05);
  --shadow-2:0 8px 24px rgba(28,25,23,.10);
  --ease-out:cubic-bezier(.16,1,.3,1);
}'''
assert old_root in t, "root"
t = t.replace(old_root, new_root, 1)
fixes.append("1 设计令牌重绑（变量值整体换血）")

# ── 2. 侧栏：去蓝紫渐变，改纯深色 + 发丝线（引用新变量） ──
old_side = '.sidebar{width:228px;background:#0f172a;color:#cbd5e1;display:flex;flex-direction:column;position:sticky;top:0;height:100vh;flex-shrink:0}'
new_side = '.sidebar{width:228px;background:var(--side-bg);color:#d6d3d1;display:flex;flex-direction:column;position:sticky;top:0;height:100vh;flex-shrink:0;border-right:1px solid var(--side-line)}'
assert old_side in t, "sidebar"
t = t.replace(old_side, new_side, 1)
old_logo = '.logo{width:38px;height:38px;border-radius:11px;background:linear-gradient(135deg,#2563eb,#7c3aed);display:flex;align-items:center;justify-content:center;font-weight:800;color:#fff;font-size:15px}'
new_logo = '.logo{width:38px;height:38px;border-radius:10px;border:1px solid rgba(250,250,249,.25);display:flex;align-items:center;justify-content:center;font-weight:800;color:#fafaf9;font-size:14px;font-family:var(--font-data)}'
assert old_logo in t, "logo"
t = t.replace(old_logo, new_logo, 1)
old_nav_active = '.nav-item.active{background:linear-gradient(90deg,#2563eb,#3b82f6);color:#fff;font-weight:600}'
new_nav_active = '.nav-item.active{background:rgba(217,119,6,.14);color:#fafaf9;font-weight:600;box-shadow:inset 2px 0 0 var(--pri)}'
assert old_nav_active in t, "nav-active"
t = t.replace(old_nav_active, new_nav_active, 1)
fixes.append("2 侧栏去蓝紫渐变（纯深色 + 琥珀 inset 指示条）")

# ── 3. 硬编码色值 → 变量（选择性替换常见处） ──
pairs = [
    ("background:#fff;color:var(--ink)", "background:var(--card);color:var(--ink)"),          # .btn
    ("code{background:#eef2ff;", "code{background:rgba(217,119,6,.10);"),
    ('.file-tag{font-size:11.5px;padding:4px 10px;border-radius:8px;background:#f1f5f9;',
     '.file-tag{font-size:11.5px;padding:4px 10px;border-radius:8px;background:var(--bg);'),
    ('.file-tag.bad{background:#fef2f2;color:#991b1b;border-color:#fecaca}',
     '.file-tag.bad{background:rgba(185,28,28,.08);color:var(--red);border-color:rgba(185,28,28,.25)}'),
    ('.nav-item:hover{background:#1e293b;color:#e2e8f0}',
     '.nav-item:hover{background:rgba(250,250,249,.06);color:#e7e5e4}'),
    ('.side-foot{margin-top:auto;padding:14px 18px;display:flex;align-items:center;gap:8px;font-size:12px;color:#64748b;border-top:1px solid #1e293b}',
     '.side-foot{margin-top:auto;padding:14px 18px;display:flex;align-items:center;gap:8px;font-size:12px;color:#78716c;border-top:1px solid var(--side-line)}'),
    ('.brand-sub{font-size:11px;color:#64748b}', '.brand-sub{font-size:11px;color:#78716c}'),
    ('.nav-item span{width:16px;text-align:center;font-size:13px}',
     '.nav-item span{width:16px;text-align:center;font-size:13px;color:#78716c;font-family:var(--font-data)}'),
    ('.nav-item{display:flex;align-items:center;gap:10px;padding:10px 14px;border-radius:9px;color:#94a3b8;',
     '.nav-item{display:flex;align-items:center;gap:10px;padding:10px 14px;border-radius:9px;color:#a8a29e;'),
]
for old, new in pairs:
    assert old in t, old[:50]
    t = t.replace(old, new, 1)
fixes.append("3 硬编码色值 → 变量 x9")

# ── 4. 等宽数字：数据读数处统一 ──
t += '''
/* ═══ 工业实用 · 数据读数与状态（P0/P1 追加） ═══ */
#ledger-summary .lb-n,#ledger-grid .stats b,#pipe-table td,#recent-runs,.badge{
  font-family:var(--font-data);font-variant-numeric:tabular-nums}
#ledger-summary .lb-n{font-size:24px;color:var(--ink);font-weight:700;letter-spacing:-.02em}
#ledger-summary .lb-l{letter-spacing:.04em}
#ledger-grid .card{border-left:3px solid var(--line)}
#ledger-grid .card.st-error{border-left-color:var(--red)}
#ledger-grid .card.st-warn{border-left-color:var(--amber)}
#ledger-grid .card.st-ok{border-left-color:var(--green)}
/* 焦点可见（可访问性） */
:focus-visible{outline:2px solid var(--pri);outline-offset:2px;border-radius:4px}
/* 动效降级 */
@media (prefers-reduced-motion:reduce){
  *{animation:none!important;transition:none!important}
}
/* 入场编排：总览 5 组错峰（切页重放） */
#view-overview>*{animation:rise .34s var(--ease-out) backwards}
#view-overview>*:nth-child(2){animation-delay:60ms}
#view-overview>*:nth-child(3){animation-delay:120ms}
#view-overview>*:nth-child(4){animation-delay:180ms}
#view-overview>*:nth-child(5){animation-delay:240ms}
@keyframes rise{from{opacity:0;transform:translateY(14px)}to{opacity:1;transform:none}}
'''
fixes.append("4 等宽数字 + 状态左边框 + 焦点/降级 + 入场编排")

p.write_text(t, encoding="utf-8")
print("\n".join(fixes))
print("STYLE APPLIED")

# ── app.js：账号卡加状态类（左边框色） ──
p2 = Path(r"D:\workbuddy_project\tiktok\webapp\static\app.js")
t2 = p2.read_text(encoding="utf-8")
old2 = '''    const card = el("div", "card clickable");'''
new2 = '''    const card = el("div", "card clickable" + (worst ? " st-" + worst : ""));'''
assert old2 in t2, "app.js status class"
t2 = t2.replace(old2, new2, 1)
# worst 需在 card 创建前定义：把 worst 计算上移
old3 = '''    const card = el("div", "card clickable" + (worst ? " st-" + worst : ""));'''
if "const worst = checks.some" in t2 and t2.index("const worst") > t2.index('card = el("div", "card clickable" + (worst'):
    # worst 在 card 之后定义 → 上移
    old4 = '''    const checks = x.checks || [];
    const worst = checks.some((c) => c.level === "error")
      ? "error"
      : checks.some((c) => c.level === "warn") ? "warn" : "";
'''
    assert old4 in t2, "worst block"
    t2 = t2.replace(old4, "", 1)
    old5 = '''    const card = el("div", "card clickable" + (worst ? " st-" + worst : ""));'''
    new5 = '''    const checks = x.checks || [];
    const worst = checks.some((c) => c.level === "error")
      ? "error"
      : checks.some((c) => c.level === "warn") ? "warn" : "";
    const card = el("div", "card clickable" + (worst ? " st-" + worst : ""));'''
    assert old5 in t2, "worst remount"
    t2 = t2.replace(old5, new5, 1)
fixes.append("5 账号卡状态左边框（JS 状态类）")
p2.write_text(t2, encoding="utf-8")
print("APP.JS APPLIED")
