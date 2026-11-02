import json, time, subprocess, sys, urllib.request, urllib.error

BASE = 'http://127.0.0.1:8787'
LOG_PATH = r'D:/workbuddy_project/tiktok/reports/auto_20261020.log'
SUMMARY_PATH = r'D:/workbuddy_project/tiktok/reports/auto_20261020_summary.json'
PY_EXTRACT = r'C:\Users\Administrator\.workbuddy\binaries\python\envs\tiktok\Scripts\python.exe'
PY_VERIFY = r'D:\workbuddy_project\tiktok\webapp\.venv\Scripts\python.exe'
PIPELINE_DIR = r'D:\workbuddy_project\tiktok\pipeline'
LEDGER_DIR = r'D:\1\duibiao'.replace('duibiao', '\u5bf9\u6807\u8d26\u53f7')
DATE_FROM = '2026-10-13'
DATE_TO = '2026-10-20'

log = open(LOG_PATH, 'w', encoding='utf-8')

def w(msg):
    log.write(msg + '\n')
    log.flush()
    print(msg, flush=True)

def post(path, body, timeout=60):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode('utf-8'),
                                 headers={'Content-Type': 'application/json'})
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read().decode('utf-8'))

def get(path, timeout=60):
    return json.loads(urllib.request.urlopen(BASE + path, timeout=timeout).read().decode('utf-8'))

accounts = json.load(open(r'D:/workbuddy_project/tiktok/pipeline/accounts.json', encoding='utf-8'))
accs = accounts if isinstance(accounts, list) else accounts.get('accounts', accounts)
todo = [a for a in accs if a.get('active', True)]
w('total=%d active=%d' % (len(accs), len(todo)))

results = []
for a in todo:
    name = a['account']
    lang = a.get('lang', 'en')
    body = {"kind": "pipeline", "account": name, "date_from": DATE_FROM, "date_to": DATE_TO,
            "steps": ["s0_check", "s1_download", "s2_scrape", "s5_transcribe"],
            "lang": lang, "backend": "openai", "allow_scrape_errors": True}
    ok = False
    st = None
    for attempt in (1, 2):
        try:
            r = post('/api/run', body)
            rid = r.get('id') or r.get('run_id') or r.get('runId')
            if not rid:
                w('%s attempt%d POST no id: %s' % (name, attempt, json.dumps(r)[:300]))
                break
            w('%s attempt%d run_id=%s' % (name, attempt, rid))
            while True:
                time.sleep(30)
                st = get('/api/runs/%s' % rid)
                if st.get('status') in ('done', 'failed'):
                    break
            ok = st.get('status') == 'done'
            w('%s attempt%d status=%s exit=%s' % (name, attempt, st.get('status'), st.get('exit_code')))
        except Exception as e:
            w('%s attempt%d EXC %r' % (name, attempt, e))
        if ok:
            break
        if attempt == 1:
            w('%s failed, wait 180s then retry' % name)
            time.sleep(180)
    results.append({'account': name, 'ok': ok,
                    'status': (st or {}).get('status'), 'exit_code': (st or {}).get('exit_code'),
                    'media_type': a.get('media_type', 'video'), 'region': a.get('region', '')})
    w('RESULT %s %s' % (name, 'OK' if ok else 'FAIL'))

# product extraction: only region us/uk/empty, active accounts
prod_results = []
for a in accs:
    if not a.get('active', True):
        continue
    region = a.get('region', '')
    if region not in ('', 'us', 'uk'):
        continue
    name = a['account']
    scrape = r'D:\workbuddy_project\tiktok\pipeline\output\scrape_%s.json' % name
    outdir = '%s\\%s' % (LEDGER_DIR, name)
    out = '%s\\%s_products.json' % (outdir, name)
    try:
        p = subprocess.run([PY_EXTRACT, r'scripts\extract_products.py', '--account', name,
                            '--scrape', scrape, '--out', out],
                           cwd=PIPELINE_DIR, capture_output=True, timeout=600)
        tail = (p.stdout or b'')[-500:].decode('utf-8', 'replace').replace('\r', '')
        w('PROD %s rc=%d %s' % (name, p.returncode, tail.strip().splitlines()[-1] if tail.strip() else ''))
        prod_results.append({'account': name, 'rc': p.returncode})
    except Exception as e:
        w('PROD %s EXC %r' % (name, e))
        prod_results.append({'account': name, 'rc': -999, 'err': repr(e)})

# verify
verify_out = ''
try:
    p = subprocess.run([PY_VERIFY, r'scripts\post_update.py', 'verify'],
                       cwd=PIPELINE_DIR, capture_output=True, timeout=1800)
    verify_out = ((p.stdout or b'') + (p.stderr or b'')).decode('utf-8', 'replace')
    w('VERIFY rc=%d' % p.returncode)
    w(verify_out)
except Exception as e:
    w('VERIFY EXC %r' % e)

json.dump({'results': results, 'products': prod_results, 'verify': verify_out[-4000:]},
          open(SUMMARY_PATH, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
w('ALL DONE')
log.close()
