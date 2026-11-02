# -*- coding: utf-8 -*-
"""One-off driver for 2026-10-18 daily incremental update (automation 878024fb).
Serial per-account pipeline runs via webapp API, 30s polling, one retry on failure.
ASCII-only output to avoid Windows console encoding issues.
"""
import json
import subprocess
import sys
import time
import urllib.request
from datetime import date, timedelta

BASE = "http://127.0.0.1:8787"
ACCOUNTS_JSON = r"D:\workbuddy_project\tiktok\pipeline\accounts.json"
LOG_PATH = r"D:\workbuddy_project\tiktok\pipeline\_daily_run_20261018.log"
TODAY = date(2026, 10, 18)
DATE_FROM = str(TODAY - timedelta(days=7))
DATE_TO = str(TODAY)


def http_json(method, path, payload=None, timeout=60):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(
        BASE + path, data=data, method=method,
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def log(msg):
    line = "[%s] %s" % (time.strftime("%H:%M:%S"), msg)
    print(line, flush=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def summarize(run):
    parts = []
    for st in run.get("steps", []):
        name = st.get("step") or st.get("name") or "?"
        status = st.get("status") or "?"
        parts.append("%s=%s" % (name, status))
    return " ".join(parts)


def run_account(acc, lang):
    body = {
        "kind": "pipeline",
        "account": acc,
        "date_from": DATE_FROM,
        "date_to": DATE_TO,
        "steps": ["s0_check", "s1_download", "s2_scrape", "s5_transcribe"],
        "lang": lang,
        "backend": "openai",
        "allow_scrape_errors": True,
    }
    r = http_json("POST", "/api/run", body)
    run_id = r["id"]
    log("%s: run started id=%s" % (acc, run_id))
    while True:
        time.sleep(30)
        run = http_json("GET", "/api/runs/%s" % run_id)
        st = run.get("status")
        if st in ("done", "failed"):
            return run
        # periodic heartbeat
        log("%s: still running (%s)" % (acc, summarize(run)))


def main():
    with open(ACCOUNTS_JSON, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    accounts = [a for a in cfg["accounts"] if a.get("active", False)]
    log("total active accounts: %d" % len(accounts))
    results = []
    for a in accounts:
        acc = a["account"]
        lang = a.get("lang", "en")
        t0 = time.time()
        try:
            run = run_account(acc, lang)
        except Exception as e:
            log("%s: EXCEPTION %r" % (acc, e))
            run = {"status": "failed", "error": repr(e)}
        if run.get("status") != "done":
            log("%s: FAILED first attempt (%s), wait 180s then retry once"
                % (acc, run.get("error", "")))
            time.sleep(180)
            try:
                run = run_account(acc, lang)
            except Exception as e:
                log("%s: EXCEPTION on retry %r" % (acc, e))
                run = {"status": "failed", "error": repr(e)}
        dur = int(time.time() - t0)
        status = run.get("status")
        err = run.get("error", "")
        results.append({"account": acc, "status": status,
                        "duration_s": dur, "error": err,
                        "steps": summarize(run)})
        log("%s: FINAL %s in %ds %s %s"
            % (acc, status, dur, summarize(run), err or ""))
    ok = [r for r in results if r["status"] == "done"]
    bad = [r for r in results if r["status"] != "done"]
    log("=== SUMMARY: %d done, %d failed ===" % (len(ok), len(bad)))
    for r in bad:
        log("FAILED: %s -> %s | %s" % (r["account"], r["status"], r["error"]))
    log("ALL_ACCOUNTS_DONE")


if __name__ == "__main__":
    main()
