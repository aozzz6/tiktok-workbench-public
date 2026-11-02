# -*- coding: utf-8 -*-
import json, subprocess, sys, io, os

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
PY = r"C:\Users\Administrator\.workbuddy\binaries\python\envs\tiktok\Scripts\python.exe"
CWD = r"D:\workbuddy_project\tiktok\pipeline"

with open(os.path.join(CWD, "accounts.json"), encoding="utf-8") as f:
    cfg = json.load(f)

SKIP = {"eu", "es", "it", "jp"}
for a in cfg["accounts"]:
    if not a.get("active", True):
        continue
    acct = a["account"]
    region = (a.get("region") or "").strip().lower()
    if region in SKIP:
        print("[%s] SKIP region=%s" % (acct, region), flush=True)
        continue
    scrape = os.path.join(CWD, "output", "scrape_%s.json" % acct)
    if not os.path.exists(scrape):
        print("[%s] NO_SCRAPE_JSON (region=%s)" % (acct, region or "none"), flush=True)
        continue
    out = r"D:\1\对标账号\%s\%s_products.json" % (acct, acct)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    cmd = [PY, r"scripts\extract_products.py", "--account", acct,
           "--scrape", scrape, "--out", out]
    r = subprocess.run(cmd, cwd=CWD, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=300)
    tail = (r.stdout or "").strip().splitlines()
    tail = tail[-1] if tail else ""
    if r.returncode != 0:
        err = ((r.stderr or "").strip().splitlines() or ["?"])[-1]
        print("[%s] FAIL rc=%d %s | %s" % (acct, r.returncode, tail, err), flush=True)
    else:
        print("[%s] OK %s" % (acct, tail), flush=True)
print("EXTRACT_DONE", flush=True)
