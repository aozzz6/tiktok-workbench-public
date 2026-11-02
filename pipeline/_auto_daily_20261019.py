# -*- coding: utf-8 -*-
"""Daily incremental driver 2026-10-19 (automation 878024fb).
Serial per-account pipeline via webapp API, retry once on failure after 180s,
product extraction per region rule (us/uk/unset only), final verify."""
import json, time, os, subprocess, datetime, urllib.request

BASE = "http://127.0.0.1:8787"
OP = urllib.request.build_opener(urllib.request.ProxyHandler({}))
ACC_FILE = r"D:/workbuddy_project/tiktok/pipeline/accounts.json"
LOG = r"D:/workbuddy_project/tiktok/pipeline/reports/auto_daily_20261019.log"
PIPE_DIR = r"D:\workbuddy_project\tiktok\pipeline"
PY_EXTRACT = r"C:\Users\Administrator\.workbuddy\binaries\python\envs\tiktok\Scripts\python.exe"
EXTRACT = os.path.join(PIPE_DIR, "scripts", "extract_products.py")
PY_VERIFY = r"D:\workbuddy_project\tiktok\webapp\.venv\Scripts\python.exe"
POSTUP = os.path.join(PIPE_DIR, "scripts", "post_update.py")
SKIP_REGIONS = {"eu", "es", "it", "jp"}
RUN_CAP_SEC = 6000  # 100 min per run

log_f = open(LOG, "a", encoding="utf-8")

def log(msg):
    line = "[%s] %s" % (datetime.datetime.now().strftime("%m-%d %H:%M:%S"), msg)
    print(line, flush=True)
    log_f.write(line + "\n")
    log_f.flush()

def req(method, path, body=None, timeout=60):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method,
                               headers={"Content-Type": "application/json"})
    with OP.open(r, timeout=timeout) as resp:
        return json.loads(resp.read())

def line_text(item):
    if isinstance(item, str):
        return item
    if isinstance(item, dict):
        for k in ("text", "line", "msg", "data", "stream"):
            if k in item and isinstance(item[k], str):
                return item[k]
        return json.dumps(item, ensure_ascii=False)[:300]
    return str(item)[:300]

def run_once(acc, lang, d_from, d_to):
    body = {"kind": "pipeline", "account": acc, "date_from": d_from,
            "date_to": d_to,
            "steps": ["s0_check", "s1_download", "s2_scrape", "s5_transcribe"],
            "lang": lang, "backend": "openai", "allow_scrape_errors": True}
    r = req("POST", "/api/run", body)
    rid = r["id"]
    log("  run id=%s started" % rid)
    t0 = time.time()
    tail = []
    while True:
        time.sleep(30)
        try:
            snap = req("GET", "/api/runs/%s?since=0" % rid)
        except Exception as e:
            log("  poll error: %r" % e)
            continue
        if snap.get("status") != "running":
            lines = snap.get("lines") or []
            tail = [line_text(x) for x in lines][-80:]
            return rid, snap.get("status"), snap.get("exit_code"), tail
        if time.time() - t0 > RUN_CAP_SEC:
            return rid, "timeout", None, []

def run_account(acc, lang, d_from, d_to):
    rid, status, rc, tail = None, "not_started", None, []
    for attempt in (1, 2):
        if attempt == 2:
            log("  failed -> wait 180s then retry once (risk-control rule)")
            time.sleep(180)
        try:
            rid, status, rc, tail = run_once(acc, lang, d_from, d_to)
        except Exception as e:
            log("  attempt %d EXC: %r" % (attempt, e))
            status, rc, tail = "exc", None, [repr(e)[:300]]
            continue
        log("  attempt %d: status=%s exit=%s" % (attempt, status, rc))
        if status == "done" and rc == 0:
            return True, rid, status, rc, tail
        if status == "timeout":
            return False, rid, status, rc, tail
    return False, rid, status, rc, tail

def main():
    today = datetime.date.today()
    d_from = (today - datetime.timedelta(days=7)).isoformat()
    d_to = today.isoformat()
    cfg = json.load(open(ACC_FILE, encoding="utf-8"))
    accs = [a for a in cfg["accounts"] if a.get("active")]
    log("=== daily incremental start: %d active accounts, %s ~ %s ==="
        % (len(accs), d_from, d_to))
    results = []
    for i, a in enumerate(accs, 1):
        name = a["account"]
        lang = a.get("lang", "en")
        region = (a.get("region") or "").strip().lower()
        media = a.get("media_type", "video")
        log("[%d/%d] %s (lang=%s region=%s media=%s)"
            % (i, len(accs), name, lang, region or "-", media))
        ok, rid, status, rc, tail = run_account(name, lang, d_from, d_to)
        tag = "OK" if ok else "FAIL"
        log("  => %s (run %s, exit=%s)" % (tag, rid, rc))
        if not ok:
            for ln in tail[-25:]:
                if ln.strip():
                    log("    | " + ln[:260])
        results.append({"account": name, "lang": lang, "region": region,
                        "media": media, "ok": ok, "status": status,
                        "exit": rc, "run_id": rid})
    # product extraction per region rule
    log("=== product extraction (region rule: us/uk/unset only) ===")
    for r in results:
        if r["region"] in SKIP_REGIONS:
            log("  %s: skip (region=%s)" % (r["account"], r["region"]))
            r["extract"] = "skipped"
            continue
        acc = r["account"]
        scrape = os.path.join(PIPE_DIR, "output", "scrape_%s.json" % acc)
        if not os.path.exists(scrape):
            log("  %s: skip (no scrape json)" % acc)
            r["extract"] = "no_scrape"
            continue
        out_dir = os.path.join("D:\\1\\对标账号", acc)
        os.makedirs(out_dir, exist_ok=True)
        out = os.path.join(out_dir, "%s_products.json" % acc)
        try:
            p = subprocess.run([PY_EXTRACT, EXTRACT, "--account", acc,
                                "--scrape", scrape, "--out", out],
                               cwd=PIPE_DIR, capture_output=True, text=True,
                               timeout=600, encoding="utf-8", errors="replace")
            if p.returncode == 0:
                r["extract"] = "ok"
                log("  %s: extract ok -> %s" % (acc, out))
            else:
                r["extract"] = "failed"
                log("  %s: extract FAILED rc=%d :: %s"
                    % (acc, p.returncode, (p.stderr or p.stdout or "")[-200:]))
        except Exception as e:
            r["extract"] = "exc"
            log("  %s: extract EXC %r" % (acc, e))
    # final verify
    log("=== post_update verify ===")
    try:
        p = subprocess.run([PY_VERIFY, POSTUP, "verify"], cwd=PIPE_DIR,
                           capture_output=True, text=True, timeout=600,
                           encoding="utf-8", errors="replace")
        out = (p.stdout or "") + (p.stderr or "")
        for ln in out.strip().splitlines()[-40:]:
            if ln.strip():
                log("  | " + ln[:300])
        log("verify rc=%d" % p.returncode)
    except Exception as e:
        log("verify EXC %r" % e)
    # summary
    fails = [r for r in results if not r["ok"]]
    log("=== SUMMARY: %d accounts, ok=%d, fail=%d ==="
        % (len(results), len(results) - len(fails), len(fails)))
    for r in results:
        log("SUMMARY| %s | lang=%s | region=%s | media=%s | %s | status=%s exit=%s | extract=%s"
            % (r["account"], r["lang"], r["region"] or "-", r["media"],
               "OK" if r["ok"] else "FAIL", r["status"], r["exit"], r.get("extract")))
    log("=== done ===")
    log_f.close()

if __name__ == "__main__":
    main()
