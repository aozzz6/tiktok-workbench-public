"""Explainable local checks. A rule pass is never a semantic or video-quality approval."""
from datetime import datetime, timezone, timedelta
from difflib import SequenceMatcher
import math
import re

import seedance_template as seedance


LABELS = {"pass": "通过", "needs_revision": "需修改", "manual": "需人工确认"}
AXES = ("hook", "scene", "story", "style")
CLAIMS = [
    (r"柔软|软糯|soft|weich", r"柔软|软糯|\bsoft\b|\bweich\w*"),
    (r"舒适|亲肤|comfortable|angenehm|bequem", r"舒适|亲肤|\bcomfort\w*|\bangenehm\w*|\bbequem\w*"),
    (r"耐穿|耐用|持久|durable|haltbar", r"耐穿|耐用|持久|\bdurable\b|\blanglebig\w*|\bhaltbar\w*"),
]


def _text(value):
    return value if isinstance(value, str) else ""


def _norm(value):
    return re.sub(r"[\W_]+", "", _text(value).casefold())


def _ratio(a, b):
    return SequenceMatcher(None, _norm(a), _norm(b)).ratio()


def _status(items):
    return "needs_revision" if any(i["status"] == "needs_revision" for i in items) else "manual" if any(i["status"] == "manual" for i in items) else "pass"


def _item(code, title, issues, success):
    return {"code": code, "title": title, "status": "needs_revision" if issues else "pass", "reasons": issues or [success]}


def _finite(n):
    return isinstance(n, (int, float)) and not isinstance(n, bool) and math.isfinite(n)


def build_report(versions, p, validator, asserted, renderer):
    """Audit supplied snapshots without changing them or making any model requests."""
    try:
        validator({"versions": versions}, p)
        gate = _item("generation_rules", "原生成规则复核", [], "六版通过结构、时间轴、口播和差异规则。")
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        gate = _item("generation_rules", "原生成规则复核", [str(exc)], "")
    reports, flattened = [], []
    for i, v in enumerate(versions):
        clips = v.get("clips", [])
        shots = [s for c in clips for s in c.get("shots", [])]
        missing = [k for k in ("title", "difference", "theme", "character", "look", "camera", "sound", *AXES) if not _text(v.get(k)).strip()]
        structure = ["缺少字段：" + "、".join(missing)] if missing else []
        if len(clips) != len(p["segments"]):
            structure.append("片段数量与生成计划不符。")
        timing, speech, framing, final_prompt, template = [], [], [], [], []
        for ci, c in enumerate(clips):
            cs = c.get("shots", [])
            if not _text(c.get("continuity")).strip():
                structure.append(f"第 {ci+1} 段缺少衔接与一致性说明。")
            expected = p["segments"][ci] if ci < len(p["segments"]) else None
            if not 1 <= len(cs) <= 6:
                structure.append(f"第 {ci+1} 段需要 1–6 个分镜。")
            if not _finite(c.get("duration")) or c["duration"] != expected:
                timing.append(f"第 {ci+1} 段声明时长与计划不符。")
            end, has_voice = 0, False
            for si, s in enumerate(cs):
                if any(not _text(s.get(k)).strip() for k in ("action", "camera")) or not isinstance(s.get("dialogue"), str) or _text(s.get("beat")) not in {"hook","setup","problem","demo","choice","reaction","reveal","twist","resolution","cta"}:
                    structure.append(f"第 {ci+1} 段第 {si+1} 镜字段不完整或叙事功能不合法。")
                start, stop = s.get("start"), s.get("end")
                valid_time = _finite(start) and _finite(stop) and start >= 0 and stop > start
                if not valid_time or abs(start-end) > .05:
                    timing.append(f"第 {ci+1} 段第 {si+1} 镜时间不连续或不合法。")
                end = stop if valid_time else end
                line = _text(s.get("dialogue")).strip()
                if not line:
                    continue
                has_voice = True
                units = len(re.findall(r"[\u3400-\u9fff\u3040-\u30ff\uac00-\ud7af]", line)) + len(re.findall(r"[A-Za-zÀ-ž0-9]+(?:['’][A-Za-zÀ-ž]+)?", line))
                rate = 4.5 if re.search(r"[\u3400-\u9fff\u3040-\u30ff\uac00-\ud7af]", line) else 3
                if valid_time and units > (stop-start)*rate:
                    speech.append(f"第 {ci+1} 段第 {si+1} 镜口播偏密（规则估计 {units} 字/词，{stop-start:g} 秒）。")
                elif units == 0 or any(ch.isalpha() and not re.match(r"[A-Za-zÀ-ž\u3400-\u9fff\u3040-\u30ff\uac00-\ud7af]", ch) for ch in line):
                    speech.append(f"第 {ci+1} 段第 {si+1} 镜超出口播计数规则覆盖范围，请人工检查长度。")
                action, camera = _text(s.get("action")), _text(s.get("camera"))
                if not re.search(r"面部|嘴部|正面|四分之三|面对镜头|面向镜头|对镜头", action+camera):
                    framing.append(f"第 {ci+1} 段第 {si+1} 镜有台词，未明确人物面部可见。")
                if re.search(r"微距|(?:袖口|领口|翻领|产品|手部|手指).{0,4}(?:特写|近景)", camera):
                    framing.append(f"第 {ci+1} 段第 {si+1} 镜产品特写同时承担口播。")
            if expected is not None and abs(end-expected) > .05:
                timing.append(f"第 {ci+1} 段未完整覆盖 {expected:g} 秒。")
            if not has_voice:
                speech.append(f"第 {ci+1} 段缺少人物口播。")
            try:
                if c.get("prompt") != renderer(v, c, p, ci):
                    final_prompt.append(f"第 {ci+1} 段最终提示词与结构化镜头不一致或缺失，请重新生成后核对。")
            except (KeyError, TypeError, ValueError):
                final_prompt.append(f"第 {ci+1} 段无法校验最终提示词，需修正片段结构。")
            prompt_text = _text(c.get("prompt"))
            if prompt_text.strip():
                for issue in seedance.audit(prompt_text):
                    template.append(f"第 {ci+1} 段：{issue}")
        voice = " ".join(_text(s.get("dialogue")) for s in shots)
        visual = " ".join(_text(s.get("action")) for s in shots)
        facts = p["facts"] + "。" + p["product"]
        unsupported = [m.group(0) for allowed, claimed in CLAIMS if not asserted(facts, allowed) for m in re.finditer(claimed, voice, re.I)]
        items = [
            _item("structure", "字段与片段结构", structure, "必填字段完整，片段数量符合计划。"),
            _item("timeline", "时间轴与总时长", timing, "分镜从 0 连续覆盖计划时长。"),
            _item("speech", "口播长度", speech, "在已覆盖文字范围内，未超过口播密度阈值；真实语速仍需试听。"),
            _item("framing", "说话镜头安排", framing, "说话镜头明确面部可见，未安排产品特写承担口播。"),
            _item("claims", "已覆盖卖点规则", ["未有肯定事实支持的表达：" + "、".join(dict.fromkeys(unsupported))] if unsupported else [], "未命中柔软、舒适、耐用等已覆盖的无依据表达；其他卖点仍需人工核实。"),
            _item("final_prompt", "最终提示词一致性", final_prompt, "复制用提示词与当前镜头及产品快照一致，含无字幕和人物口型要求。"),
            _item("template", "Seedance 2.0 模板", template, "六要素、保持不变、音频、负向齐全，顺序符合模板。"),
            {"code":"facts_manual", "title":"产品事实核对", "status":"manual", "reasons":["逐句核对材质、功效、价格、促销等表述是否有依据，确认未沿用竞品事实。规则未覆盖所有表达。"]},
            {"code":"language_manual", "title":"语言与故事质量", "status":"manual", "reasons":[f"核对口播是否为{p['language']}、表达是否自然，检查动作可执行性及人物/产品连续性。"]},
            {"code":"video_manual", "title":"成片效果", "status":"manual", "reasons":["视频生成后检查真实嘴型、遮挡、字幕、产品外观及语速；文本自检不能验证成片。"]},
        ]
        if i>=4:
            items.append(_item("new_story", "C 类故事机制", [] if any(s.get("beat") in ("problem","choice","twist","reaction") for s in shots) else ["C 类缺少人物选择、问题、反应或反转，需重新构思。"], "包含问题、选择、反应或反转；故事质量仍需人工确认。"))
        if any(s.startswith("第") and "覆盖范围" in s for s in speech):
            items[2]["status"] = "needs_revision" if any("偏密" in s or "缺少" in s for s in speech) else "manual"
        warnings = [w for w in v.get("warnings", []) if isinstance(w, str)]
        if warnings:
            items.append({"code":"model_warnings", "title":"生成模型提示（未独立核实）", "status":"manual", "reasons":warnings})
        reports.append({"index":i, "label":"优化版" if i==0 else f"B{i}" if i<=3 else f"C{i-3}", "title":_text(v.get("title")), "status":_status(items), "items":items})
        flattened.append({"voice":voice,"visual":visual,"beats":[s.get("beat") for s in shots]})
    pairs = []
    for j in range(len(versions)):
        for i in range(j):
            a,b = versions[i], versions[j]
            changed = [k for k in AXES if _ratio(a.get(k),b.get(k)) < .8]
            vr, ar = _ratio(flattened[i]["voice"],flattened[j]["voice"]), _ratio(flattened[i]["visual"],flattened[j]["visual"])
            issues = []
            if len(changed)<2: issues.append("开头、场景、叙事、风格中少于两项达到文字差异阈值。")
            if vr>.88: issues.append(f"口播文字相似度 {vr:.0%}，需独立重写。")
            if ar>.9: issues.append(f"画面动作文字相似度 {ar:.0%}，可能只更换了标题或场景。")
            if j>=4 and flattened[i]["beats"] == flattened[j]["beats"]: issues.append("C 类沿用了另一版本的分镜叙事功能顺序。")
            pairs.append({"left":i,"right":j,"status":"needs_revision" if issues else "pass","changed_axes":changed,"voice_similarity":round(vr,4),"action_similarity":round(ar,4),"reasons":issues or ["达到文字差异阈值；创意是否实质不同仍需人工确认。"]})
    for r in reports:
        conflicts = [pair for pair in pairs if pair["status"]=="needs_revision" and r["index"] in (pair["left"],pair["right"])]
        r["items"].append(_item("cross_version", "与其他版本对比", [f"与{reports[q['right'] if q['left']==r['index'] else q['left']]['label']}："+"；".join(q["reasons"]) for q in conflicts], "与其余五版达到文字差异阈值。"))
        r["status"] = _status(r["items"])
        r["rule_status"] = _status([x for x in r["items"] if x["code"] not in {"facts_manual", "language_manual", "video_manual", "model_warnings"}])
    status = "needs_revision" if gate["status"]=="needs_revision" or any(r["status"]=="needs_revision" for r in reports) else "manual"
    return {"schema_version":1,"method":"local_rules","checked_at":datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds"),"status":status,"rule_gate":gate,"versions":reports,"comparisons":pairs,"summary":{"versions":len(reports),"rule_passed":sum(r["rule_status"]=="pass" for r in reports),"rule_manual":sum(r["rule_status"]=="manual" for r in reports),"needs_revision":sum(r["status"]=="needs_revision" for r in reports),"manual":sum(r["status"]=="manual" for r in reports),"pairs":len(pairs)},"limitations":["本报告采用本地规则，不新增模型调用；通过仅表示已覆盖规则通过。","文字相似度不等于创意质量，口播语言、事实依据与成片效果仍需人工确认。"]}
