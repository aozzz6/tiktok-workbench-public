# -*- coding: utf-8 -*-
# One-off daily automation driver: 2026-10-18 23:59 round
import json, time, urllib.request, sys, io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

BASE = "http://127.0.0.1:8787"
DATE_FROM = "2026-10-11"
DATE_TO = "2026-10-18"
ACCOUNTS_JSON = r"D:\workbuddy_project\tiktok\pipeline\accounts.json"
REPORT = r"D:\workbuddy_project\tiktok\pipeline\reports\auto_daily_20261018_2359.json"

def log(msg):
    print(msg, flush=True)

def http_json(method, path, body=None, timeout=60):
    url = BASE + path
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())

with open(ACCOUNTS_JSON, encoding="utf-8") as f:
    cfg = json.load(f)

accounts = [a["account"] for a in cfg["accounts"] if a.get("active", True)]
log("TOTAL_ACTIVE=%d" % len(accounts))
log("ACCOUNTS=" + ",".join(accounts))

results = {}
for acct in accounts:
    lang = ""
    for a in cfg["accounts"]:
        if a["account"] == acct:
            lang = a.get("lang", "en")
    body = {
        "kind": "pipeline",
        "account": acct,
        "date_from": DATE_FROM,
        "date_to": DATE_TO,
        "steps": ["s0_check", "s1_download", "s2_scrape", "s5_transcribe"],
        "lang": lang,
        "backend": "openai",
        "allow_scrape_errors": True,
    }
    outcome = None
    for attempt in (1, 2):
        try:
            r = http_json("POST", "/api/run", body, timeout=120)
            run_id = r.get("id") or r.get("run_id")
            log("[%s] attempt=%d run_id=%s" % (acct, attempt, run_id))
        except Exception as e:
            log("[%s] attempt=%d POST FAIL: %r" % (acct, attempt, e))
            outcome = {"status": "post_fail", "error": repr(e)}
            break
        if not run_id:
            outcome = {"status": "no_run_id", "resp": r}
            break
        final = None
        while True:
            time.sleep(30)
            try:
                st = http_json("GET", "/api/runs/%s" % run_id, timeout=60)
            except Exception as e:
                log("[%s] poll error: %r" % (acct, e))
                time.sleep(30)
                continue
            status = st.get("status")
            if status in ("done", "failed"):
                final = st
                break
        steps = final.get("steps") or {}
        summary = {}
        if isinstance(steps, dict):
            for k, v in steps.items():
                if isinstance(v, dict):
                    summary[k] = v.get("status", v.get("result", "?"))
                else:
                    summary[k] = v
        outcome = {"status": final.get("status"), "steps": summary,
                   "error": final.get("error"), "run_id": run_id}
        log("[%s] attempt=%d -> %s steps=%s" % (acct, attempt, outcome["status"], json.dumps(summary)))
        if outcome["status"] == "done":
            break
        # failed -> wait 3 min then retry once (troubleshooting entry 1: TikTok risk control)
        log("[%s] failed, wait 180s then retry" % acct)
        time.sleep(180)
    results[acct] = outcome
    with open(REPORT, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=1)

fails = [k for k, v in results.items() if v and v.get("status") != "done"]
log("ALL_DONE fails=%s" % ",".join(fails) if fails else "ALL_DONE all_green")
