#!/usr/bin/env python3
"""Phase 4 production hardening helpers for WFG Bid Engine.

Safety: local-only. Does not send outreach, email, or submit. Live validation uses dry-run sync only.
"""
from __future__ import annotations
import argparse, datetime as dt, hashlib, json, os, re, shutil, sqlite3, subprocess, sys, urllib.request, mimetypes, zipfile
from pathlib import Path
from typing import Any

PROJECT=Path(os.environ.get('WFG_PROJECT_DIR','/home/nick/workspace/wfg-gov-contracting-v2')).resolve()
PROD_DB=PROJECT/'state/wfg_workflow.sqlite3'
TEST_ROOT=PROJECT/'state/test'
TEST_DB=TEST_ROOT/'wfg_workflow_test.sqlite3'
TEST_ARTIFACTS=PROJECT/'test-artifacts'
TEST_BATCHES=TEST_ARTIFACTS/'batches'
TEST_OPPS=TEST_ARTIFACTS/'opportunities'
RESULTS=PROJECT/'test-results'
SYN_KEYS=['notice:phase2testnotice001','notice:phase3test001']
SYN_IDS=['phase2testnotice001','phase3test001']


def now(): return dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')
def stamp(): return dt.datetime.now().strftime('%Y%m%d-%H%M%S')
def sha_file(p:Path):
    h=hashlib.sha256();
    with p.open('rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''): h.update(b)
    return h.hexdigest()

def con(path=PROD_DB):
    c=sqlite3.connect(path); c.row_factory=sqlite3.Row; return c

def add_col(c, table, coldef):
    try: c.execute(f'ALTER TABLE {table} ADD COLUMN {coldef}')
    except sqlite3.OperationalError as e:
        if 'duplicate column name' not in str(e).lower(): raise

def migrate_db(path=PROD_DB):
    sys.path.insert(0,str(PROJECT/'scripts'))
    env=os.environ.copy(); env.setdefault('WFG_DB_PATH',str(path))
    subprocess.check_call([sys.executable,str(PROJECT/'scripts/wfg_phase1.py')], env=env) if False else None
    c=con(path)
    # Ensure base tables exist by calling module init/migrations in-process with env set before import in subprocess avoided here.
    import importlib.util
    # add Phase4 columns/tables only; base DB already exists for prod and tests are bootstrapped by imports.
    tables=[r[0] for r in c.execute("select name from sqlite_master where type='table'")]
    for t in tables:
        if t not in ('sqlite_sequence',):
            if t in ['opportunities','batches','opportunity_versions','attachments','workflow_events','approvals','processing_errors','opportunity_intakes','opportunity_version_manifests','attachment_downloads','trade_packages','outreach_sends','quote_records','pricing_versions','compliance_runs','proposal_packages','approval_commands','subcontractors','subcontractor_contacts','subcontractor_trades','subcontractor_geography','subcontractor_credentials','opportunity_sse_status']:
                add_col(c,t,'environment TEXT DEFAULT "production"')
    for col in ['is_test_fixture INTEGER DEFAULT 0']:
        add_col(c,'opportunities',col)
    for col in ['reference_status TEXT DEFAULT "discovered_reference"','download_status TEXT DEFAULT "not_attempted"','local_path TEXT','parse_status TEXT DEFAULT "not_parsed"']:
        add_col(c,'attachments',col)
    for col in ['approval_id TEXT','exact_action TEXT','expires_at TEXT','used_at TEXT','superseded_by TEXT']:
        add_col(c,'approvals',col)
    c.executescript('''
    CREATE TABLE IF NOT EXISTS test_fixture_archive(id INTEGER PRIMARY KEY AUTOINCREMENT, table_name TEXT, record_pk TEXT, dedupe_key TEXT, archived_at TEXT, reason TEXT, copied_to_test INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS approval_attempts(id INTEGER PRIMARY KEY AUTOINCREMENT, attempted_at TEXT, command_text TEXT, approval_id TEXT, accepted INTEGER, reason TEXT, approver TEXT, telegram_user_id TEXT, environment TEXT DEFAULT 'production');
    CREATE TABLE IF NOT EXISTS source_coverage_reports(id INTEGER PRIMARY KEY AUTOINCREMENT, dedupe_key TEXT, created_at TEXT, report_path TEXT, coverage_json TEXT, status TEXT, environment TEXT DEFAULT 'production');
    CREATE INDEX IF NOT EXISTS idx_approvals_approval_id ON approvals(approval_id);
    ''')
    c.commit(); c.close()

def bootstrap_test_db():
    TEST_ROOT.mkdir(parents=True,exist_ok=True); TEST_ARTIFACTS.mkdir(parents=True,exist_ok=True); TEST_BATCHES.mkdir(parents=True,exist_ok=True); TEST_OPPS.mkdir(parents=True,exist_ok=True)
    if TEST_DB.exists(): TEST_DB.unlink()
    env=os.environ.copy(); env.update({'WFG_ENV':'test','WFG_DB_PATH':str(TEST_DB),'WFG_OPP_ROOT':str(TEST_OPPS),'WFG_BATCHES_DIR':str(TEST_BATCHES),'WFG_ARCHIVE_DIR':str(TEST_ARTIFACTS/'sam-api'),'WFG_STATE_DIR':str(TEST_ROOT)})
    code="""import sys, os; sys.path.insert(0, os.environ['WFG_PROJECT_DIR']+'/scripts' if 'WFG_PROJECT_DIR' in os.environ else '/home/nick/workspace/wfg-gov-contracting-v2/scripts'); import wfg_phase1,wfg_phase2,wfg_phase3; wfg_phase1.init_db(); wfg_phase2.migrate(); wfg_phase3.migrate(); print(wfg_phase1.DB_PATH)"""
    env['WFG_PROJECT_DIR']=str(PROJECT)
    subprocess.check_call([sys.executable,'-c',code], env=env)
    # Seed test archive with one copied public raw JSON fixture so replay tests never read production paths.
    ta=TEST_ARTIFACTS/'sam-api'; ta.mkdir(parents=True, exist_ok=True)
    raws=sorted((PROJECT/'opportunity-searches/sam-api').glob('raw-*.json'))
    if raws:
        shutil.copy2(raws[-1], ta/raws[-1].name)
    migrate_db(TEST_DB)

def copy_rows_to_test():
    pc=con(PROD_DB); tc=con(TEST_DB)
    copied={}
    for t in [r[0] for r in pc.execute("select name from sqlite_master where type='table' and name not like 'sqlite_%'")]:
        cols=[r[1] for r in pc.execute(f'pragma table_info({t})')]
        if not cols or t.startswith('test_fixture'): continue
        rows=[]
        if 'dedupe_key' in cols:
            q=f"select * from {t} where lower(coalesce(dedupe_key,'')) in ({','.join('?' for _ in SYN_KEYS)})"
            rows=pc.execute(q,SYN_KEYS).fetchall()
        elif t=='approval_commands':
            rows=pc.execute("select * from approval_commands where lower(command_text) like '%phase2testnotice001%' or lower(command_text) like '%phase3test001%'").fetchall()
        elif t.startswith('subcontractor') or t=='opportunity_sse_status':
            try: rows=pc.execute(f"select * from {t} where source like '%test%' or source like '%fixture%' or subcontractor_id in (select id from subcontractors where legal_name like 'Synthetic%')").fetchall()
            except Exception: rows=[]
        if rows:
            common=[c for c in cols if c in [x[1] for x in tc.execute(f'pragma table_info({t})')]]
            ph=','.join('?' for _ in common)
            for r in rows:
                vals=[r[c] for c in common]
                try: tc.execute(f"insert or ignore into {t}({','.join(common)}) values({ph})", vals)
                except Exception: pass
                pk=str(r[cols[0]]) if cols else ''
                pc.execute('insert into test_fixture_archive(table_name,record_pk,dedupe_key,archived_at,reason,copied_to_test) values(?,?,?,?,?,1)',(t,pk,r['dedupe_key'] if 'dedupe_key' in cols else '',now(),'Phase 4 synthetic fixture isolation'))
            copied[t]=len(rows)
    tc.commit(); pc.commit(); tc.close(); pc.close(); return copied

def mark_prod_test_excluded():
    c=con(PROD_DB)
    for key in SYN_KEYS:
        c.execute('update opportunities set is_test_fixture=1, environment="test" where dedupe_key=?',(key,))
        for t in ['opportunity_versions','attachments','approvals','opportunity_intakes','opportunity_version_manifests','attachment_downloads','trade_packages','quote_records','pricing_versions','compliance_runs','proposal_packages']:
            try: c.execute(f'update {t} set environment="test" where dedupe_key=?',(key,))
            except Exception: pass
    try: c.execute("update approval_commands set environment='test' where lower(command_text) like '%phase2testnotice001%' or lower(command_text) like '%phase3test001%'")
    except Exception: pass
    c.commit(); c.close()

def move_synthetic_folders():
    dest=TEST_ARTIFACTS/'archived-production-fixtures'; dest.mkdir(parents=True,exist_ok=True); moved=[]
    for pat in ['phase2testnotice001-*','phase3test001-*']:
        for p in (PROJECT/'opportunities').glob(pat):
            target=dest/p.name
            if target.exists(): shutil.rmtree(target)
            shutil.move(str(p), str(target)); moved.append((str(p),str(target)))
    return moved

def isolate():
    migrate_db(PROD_DB); bootstrap_test_db(); copied=copy_rows_to_test(); mark_prod_test_excluded(); moved=move_synthetic_folders()
    return {'test_db':str(TEST_DB),'copied_records':copied,'moved_folders':moved}

def table_counts(path=PROD_DB):
    c=con(path); out={}
    for (t,) in c.execute("select name from sqlite_master where type='table' and name not like 'sqlite_%' order by name"):
        out[t]=c.execute(f'select count(*) from {t}').fetchone()[0]
    c.close(); return out

def run_tests():
    ts=stamp(); outdir=RESULTS/ts; outdir.mkdir(parents=True,exist_ok=True)
    env=os.environ.copy(); env.update({'WFG_ENV':'test','WFG_DB_PATH':str(TEST_DB),'WFG_OPP_ROOT':str(TEST_OPPS),'WFG_BATCHES_DIR':str(TEST_BATCHES),'WFG_ARCHIVE_DIR':str(TEST_ARTIFACTS/'sam-api'),'WFG_STATE_DIR':str(TEST_ROOT),'WFG_PROJECT_DIR':str(PROJECT)})
    bootstrap_test_db()
    meta={'started_at':now(),'python':sys.version,'test_db':str(TEST_DB),'code_hash':hashlib.sha256((PROJECT/'scripts/wfg_phase1.py').read_bytes()+(PROJECT/'scripts/wfg_phase2.py').read_bytes()+(PROJECT/'scripts/wfg_phase3.py').read_bytes()).hexdigest()}
    (outdir/'metadata-start.json').write_text(json.dumps(meta,indent=2))
    cmd=[sys.executable,'-m','unittest','tests/test_phase1_pipeline.py','tests/test_phase2_intake.py','tests/test_phase3_engine.py','tests/test_phase4_hardening.py','-v']
    proc=subprocess.run(cmd,cwd=PROJECT,env=env,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=300)
    output=proc.stdout; (outdir/'unittest-output.txt').write_text(output)
    m=re.search(r'Ran (\d+) tests?',output); failed='FAILED' in output or proc.returncode!=0
    summary={'finished_at':now(),'returncode':proc.returncode,'tests':int(m.group(1)) if m else None,'passed':not failed,'failed':failed,'skipped':output.count(' skipped'), 'errors': output.count('ERROR:')}
    (outdir/'result.json').write_text(json.dumps(summary,indent=2))
    return {'path':str(outdir),**summary,'output_tail':'\n'.join(output.splitlines()[-12:])}

def real_http_download(url, destdir:Path):
    destdir.mkdir(parents=True,exist_ok=True); req=urllib.request.Request(url,headers={'User-Agent':'WFG-Hermes-validation/1.0'})
    rec={'source_url':url,'attempt_time':now()}
    try:
        with urllib.request.urlopen(req,timeout=45) as r:
            data=r.read(); rec['http_status']=getattr(r,'status',None); ctype=r.headers.get_content_type(); rec['content_type']=ctype; cd=r.headers.get('Content-Disposition','')
            name=url.split('/')[-1].split('?')[0] or 'attachment';
            m=re.search(r'filename="?([^";]+)',cd); name=m.group(1) if m else name
            norm=re.sub(r'[^A-Za-z0-9._-]+','-',name)[:100] or 'attachment.bin'; p=destdir/norm; p.write_bytes(data)
            rec.update({'reported_filename':name,'normalized_filename':norm,'byte_size':len(data),'sha256':sha_file(p),'local_path':str(p),'download_success':True})
            # guard html error pages
            head=data[:512].lower();
            if ctype=='text/html' or b'<html' in head: rec['warning']='HTML content; not treated as valid solicitation binary unless expected'
            if zipfile.is_zipfile(p):
                with zipfile.ZipFile(p) as z: rec['zip_entries']=[i.filename for i in z.infolist()[:50]]; rec['zip_safe_inspected']=True
            rec.update(parse_file(p,ctype)); return rec
    except Exception as e:
        rec.update({'download_success':False,'error':str(e),'byte_size':0,'sha256':'','local_path':''}); return rec

def parse_file(p:Path,ctype=''):
    res={'parsing_method':'unsupported','parsing_success':False,'parsing_confidence':'low','warnings':[]}
    suf=p.suffix.lower(); text=''
    try:
        if suf in ['.txt','.csv','.json','.xml','.html','.htm'] or ctype.startswith('text/'):
            text=p.read_text(errors='ignore'); res['parsing_method']='plain_text'; res['parsing_success']=True; res['parsing_confidence']='high'
        elif suf=='.pdf':
            out=subprocess.run(['pdftotext',str(p),'-'],text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=30)
            text=out.stdout; res['parsing_method']='pdftotext'; res['parsing_success']=out.returncode==0 and bool(text.strip()); res['warnings'] += ([out.stderr[:300]] if out.stderr.strip() else []); res['parsing_confidence']='medium' if res['parsing_success'] else 'low'
        elif suf in ['.docx','.xlsx'] and zipfile.is_zipfile(p):
            with zipfile.ZipFile(p) as z:
                parts=[]
                for n in z.namelist():
                    if n.endswith('.xml') and not ('vba' in n.lower()): parts.append(z.read(n)[:200000].decode('utf-8','ignore'))
                text=re.sub('<[^>]+>',' ','\n'.join(parts)); res['parsing_method']='safe_zip_xml'; res['parsing_success']=bool(text.strip()); res['parsing_confidence']='medium'
        if text:
            tp=p.with_suffix(p.suffix+'.extracted.txt'); tp.write_text(text[:1000000]); res['extraction_path']=str(tp)
    except Exception as e: res['warnings'].append(str(e))
    return res

def coverage_from_texts(folder:Path, item:dict):
    texts=[]
    for p in folder.rglob('*.extracted.txt'):
        texts.append((p.name,p.read_text(errors='ignore')[:300000]))
    joined='\n'.join(t for _,t in texts)
    checks={
      'solicitation number':[item.get('solicitationNumber'), r'solicitation\s*(number|no\.?|#)'], 'agency':[item.get('fullParentPathName'), r'agency|department'], 'response deadline':[item.get('responseDeadLine') or item.get('responseDeadline'), r'due date|deadline|quotes? due|response'], 'submission method':[None,r'email|SAM.gov|PIEE|portal|submit'], 'statement of work':[None,r'statement of work|scope of work|SOW'], 'CLIN or pricing schedule':[None,r'CLIN|price schedule|pricing'], 'period of performance':[None,r'period of performance|POP'], 'place of performance':[None,r'place of performance|location'], 'evaluation factors':[None,r'evaluation|award will be made'], 'required forms':[None,r'forms?|SF 1449|SF 18'], 'representations and certifications':[None,r'representations|certifications|52\.212-3'], 'amendment acknowledgments':[None,r'amendment'], 'bonding':[None,r'bond'], 'insurance':[None,r'insurance'], 'site visit':[None,r'site visit'], 'questions deadline':[None,r'questions'], 'wage determination':[None,r'wage determination'], 'Davis-Bacon or construction wage requirements':[None,r'Davis-Bacon|DBA|Construction Wage'], 'Service Contract Labor Standards requirements':[None,r'Service Contract Labor|SCLS|SCA'], 'security or badging':[None,r'security|badge|badging'], 'page limits':[None,r'page limit'], 'subcontracting restrictions':[None,r'limitations on subcontracting|subcontract'], 'applicable FAR clauses':[None,r'FAR|52\.']}
    cov=[]
    for name,(meta,pat) in checks.items():
        found_meta=bool(meta); m=re.search(pat,joined,re.I) if pat else None
        status='VERIFIED EXTRACTED' if m else ('PARTIALLY EXTRACTED' if found_meta else 'NOT FOUND')
        src='SAM metadata' if found_meta and not m else (next((fn for fn,txt in texts if re.search(pat,txt,re.I)), '') if m else '')
        cov.append({'item':name,'status':status,'source_reference':src or '','value':str(meta or (m.group(0) if m else ''))[:300]})
    return cov

def live_validation():
    # use current live fetch script but manual batch; no sheet write. Reads tracker keys only.
    bid='manual-validation-'+stamp(); bdir=PROJECT/'opportunity-searches/sam-api/batches'/bid; bdir.mkdir(parents=True,exist_ok=True)
    manifest={'batch_id':bid,'created_at':now(),'validation_only':True,'snapshot_status':'pending','fetch_status':'pending','brief_status':'pending','tracker_sync_status':'dry_run_pending','raw_files':[],'records':0,'api_pages':0,'warnings':[],'errors':[]}
    (bdir/'manifest.json').write_text(json.dumps(manifest,indent=2))
    # snapshot read
    snap=subprocess.run([sys.executable,str(PROJECT/'scripts/sync_sam_opportunity_tracker.py'),'--print-seen-keys'],cwd=PROJECT,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=120)
    seen=[x for x in snap.stdout.splitlines() if x.strip()] if snap.returncode==0 else []
    (bdir/'seen-keys.json').write_text(json.dumps({'batch_id':bid,'count':len(seen),'seen_keys':seen},indent=2)); manifest['snapshot_status']='completed' if snap.returncode==0 else 'failed'
    # live fetch via existing script
    fetch=subprocess.run([sys.executable,str(PROJECT/'scripts/sam_morning_opportunity_brief.py'),'--fetch-only','--batch-id',bid],cwd=PROJECT,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=180)
    manifest=json.loads((bdir/'manifest.json').read_text()) if (bdir/'manifest.json').exists() else manifest
    manifest['validation_fetch_returncode']=fetch.returncode; manifest['validation_fetch_output_tail']='\n'.join(fetch.stdout.splitlines()[-20:]); (bdir/'manifest.json').write_text(json.dumps(manifest,indent=2))
    # score brief offline to file, no final sync
    brief=subprocess.run([sys.executable,str(PROJECT/'scripts/sam_morning_opportunity_brief.py'),'--offline','--batch-id',bid,'--seen-keys-file',str(bdir/'seen-keys.json'),'--no-final-sync'],cwd=PROJECT,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=180)
    (bdir/'validation-brief.txt').write_text(brief.stdout)
    # tracker dry run
    sync=subprocess.run([sys.executable,str(PROJECT/'scripts/sync_sam_opportunity_tracker.py'),'--sync','--batch-id',bid,'--dry-run'],cwd=PROJECT,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=180)
    (bdir/'tracker-sync-dry-run.txt').write_text(sync.stdout)
    return {'batch_id':bid,'batch_dir':str(bdir),'snapshot_keys':len(seen),'fetch_returncode':fetch.returncode,'brief_returncode':brief.returncode,'sync_dry_run_returncode':sync.returncode,'brief_path':str(bdir/'validation-brief.txt'),'sync_path':str(bdir/'tracker-sync-dry-run.txt')}

def select_and_intake(batch_id):
    sys.path.insert(0,str(PROJECT/'scripts')); import wfg_phase1, wfg_phase2, wfg_phase3
    bdir=PROJECT/'opportunity-searches/sam-api/batches'/batch_id; items=wfg_phase1.load_items_from_batch(bdir)
    chosen=None
    for it in items:
        links=it.get('resourceLinks') or []
        if isinstance(links,str): links=[links]
        if links and (it.get('noticeId') and not re.search('cancel|archive',str(it.get('type',''))+str(it.get('title','')),re.I)):
            chosen=it; break
    if not chosen: raise RuntimeError('no validation opportunity with resourceLinks found')
    key='notice:'+chosen['noticeId'].lower(); wfg_phase1.upsert_opportunity(chosen,key,batch_id,'validation_only',0)
    # manual real downloads + parse evidence first; phase2 intake also runs full drafts/approval
    folder=wfg_phase2.opp_folder(chosen,key); folder.mkdir(parents=True,exist_ok=True); dl_dir=folder/'source'
    records=[]
    for u in (chosen.get('resourceLinks') or []): records.append(real_http_download(u,dl_dir))
    (folder/'phase4-real-download-evidence.json').write_text(json.dumps(records,indent=2))
    # run phase2 intake to generate drafts/approval; it will attempt downloads using its own code too
    intake=wfg_phase2.intake(chosen['noticeId'], fixture_no_network=False)
    coverage=coverage_from_texts(Path(intake['folder']), chosen); cov_path=Path(intake['folder'])/'source_coverage_report.json'; cov_path.write_text(json.dumps(coverage,indent=2))
    comp=wfg_phase3.compliance(chosen['noticeId'], 'service', wage_docs_present=any('VERIFIED' in x['status'] and 'wage' in x['item'].lower() for x in coverage), subcontractor_ids=[], materials='0')
    prelim='VERIFIED AGAINST IDENTIFIED REQUIREMENTS' if comp['compliant'] and all(x['status']=='VERIFIED EXTRACTED' for x in coverage) else ('PRELIMINARY SCREEN — BLOCKED' if comp['blocking_flags'] else 'HUMAN COMPLIANCE REVIEW REQUIRED')
    (Path(intake['folder'])/'preliminary_compliance_status.md').write_text(f"# Preliminary Compliance Status\n\nStatus: {prelim}\n\nCompliance run: {comp['run_version']}\n\nBlocking flags:\n"+'\n'.join('- '+b for b in comp['blocking_flags']))
    return {'selected_notice':chosen.get('noticeId'),'solicitation':chosen.get('solicitationNumber'),'title':chosen.get('title'),'folder':intake['folder'],'download_evidence':str(folder/'phase4-real-download-evidence.json'),'download_successes':sum(1 for r in records if r.get('download_success')),'coverage_path':str(cov_path),'coverage_summary':{x['item']:x['status'] for x in coverage},'intake':intake,'compliance_status':prelim,'compliance_run':comp['run_version']}

def main():
    ap=argparse.ArgumentParser(); sub=ap.add_subparsers(dest='cmd',required=True)
    sub.add_parser('migrate'); sub.add_parser('isolate'); sub.add_parser('test'); sub.add_parser('live'); a=sub.add_parser('intake'); a.add_argument('batch_id')
    args=ap.parse_args()
    if args.cmd=='migrate': migrate_db(); print(json.dumps({'ok':True},indent=2))
    elif args.cmd=='isolate': print(json.dumps(isolate(),indent=2))
    elif args.cmd=='test': print(json.dumps(run_tests(),indent=2))
    elif args.cmd=='live': print(json.dumps(live_validation(),indent=2))
    elif args.cmd=='intake': print(json.dumps(select_and_intake(args.batch_id),indent=2,default=str))
if __name__=='__main__': main()
