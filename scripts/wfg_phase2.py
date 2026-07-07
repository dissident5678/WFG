#!/usr/bin/env python3
"""WFG Phase 2: opportunity intake, attachment/version tracking, drafts, and approvals.

Designed for local/offline-safe operation. It never sends external communications or
submits proposals. Optional Telegram/button delivery is handled by a separate command
and is not invoked by tests unless explicitly requested.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import html
import json
import mimetypes
import os
import re
import shutil
import secrets
import sqlite3
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import wfg_phase1

PROJECT = Path(os.environ.get('WFG_PROJECT_DIR', '/home/nick/workspace/wfg-gov-contracting-v2')).resolve()
OPP_ROOT = Path(os.environ.get('WFG_OPP_ROOT', str(PROJECT / 'opportunities'))).resolve()
DB_PATH = Path(os.environ.get('WFG_DB_PATH', str(PROJECT / 'state' / 'wfg_workflow.sqlite3'))).resolve()
ENV_PATHS = [Path('/home/nick/.hermes/.env'), PROJECT / '.env']
APPROVAL_TOPIC = 'telegram:-1003889564123:295'

# Consensus plan Section 5 state model. Legacy states remain valid during the
# transition; scripts/wfg_state_migration.py renames the mid-pipeline legacy
# states once, in the same change that introduced gate IDs.
STATUSES = [
    'discovered','analysis_in_progress','awaiting_pursue_decision','passed','watching','pursuing',
    'documents_downloading','documents_complete','analysis_complete','drafting_complete',
    'awaiting_outreach_approval','outreach_approved','outreach_sent','quotes_pending','pricing_in_progress',
    'awaiting_price_approval','proposal_in_progress','awaiting_submission_approval','submitted',
    'archived','cancelled','amended_reanalysis_required',
    # consensus-plan states
    'gate1_pending_pursue','gate2_pending_packet_and_recipients','gate2_pending_outreach_send',
    'gate5_pending_submission','awaiting_human_submission','submitted_by_human',
    'submission_proof_archived','amendment_review_required','closed_archived',
]
ALLOWED = {
    'discovered': {'analysis_in_progress','watching','passed','cancelled'},
    'analysis_in_progress': {'documents_downloading','awaiting_pursue_decision','gate1_pending_pursue','cancelled','amended_reanalysis_required','amendment_review_required'},
    'awaiting_pursue_decision': {'analysis_in_progress','pursuing','passed','watching','cancelled','amended_reanalysis_required','gate1_pending_pursue'},
    'gate1_pending_pursue': {'analysis_in_progress','pursuing','passed','watching','cancelled','amendment_review_required'},
    'pursuing': {'documents_downloading','analysis_in_progress','awaiting_outreach_approval','gate2_pending_packet_and_recipients','cancelled','amended_reanalysis_required','amendment_review_required'},
    'documents_downloading': {'documents_complete','amended_reanalysis_required','cancelled'},
    'documents_complete': {'analysis_complete','drafting_complete','awaiting_outreach_approval','gate2_pending_packet_and_recipients','amended_reanalysis_required','cancelled'},
    'analysis_complete': {'drafting_complete','awaiting_outreach_approval','gate2_pending_packet_and_recipients','amended_reanalysis_required','cancelled'},
    'drafting_complete': {'analysis_in_progress','awaiting_pursue_decision','awaiting_outreach_approval','gate2_pending_packet_and_recipients','proposal_in_progress','amended_reanalysis_required','cancelled'},
    'awaiting_outreach_approval': {'outreach_approved','passed','watching','amended_reanalysis_required','cancelled'},
    'gate2_pending_packet_and_recipients': {'gate2_pending_outreach_send','passed','watching','cancelled','amendment_review_required'},
    'gate2_pending_outreach_send': {'outreach_approved','passed','watching','cancelled','amendment_review_required'},
    'outreach_approved': {'outreach_sent','quotes_pending','pricing_in_progress','gate2_pending_outreach_send','cancelled','amended_reanalysis_required','amendment_review_required'},
    'outreach_sent': {'quotes_pending','pricing_in_progress','cancelled','amended_reanalysis_required','amendment_review_required'},
    'quotes_pending': {'pricing_in_progress','cancelled','amended_reanalysis_required','amendment_review_required'},
    'pricing_in_progress': {'awaiting_price_approval','proposal_in_progress','cancelled','amended_reanalysis_required','amendment_review_required'},
    'awaiting_price_approval': {'proposal_in_progress','cancelled','amended_reanalysis_required'},
    'proposal_in_progress': {'awaiting_submission_approval','gate5_pending_submission','cancelled','amended_reanalysis_required','amendment_review_required'},
    'awaiting_submission_approval': {'submitted','archived','cancelled','amended_reanalysis_required'},
    'gate5_pending_submission': {'awaiting_human_submission','cancelled','amendment_review_required'},
    'awaiting_human_submission': {'submitted_by_human','cancelled','amendment_review_required'},
    'submitted_by_human': {'submission_proof_archived','archived','amendment_review_required'},
    'submission_proof_archived': {'closed_archived','archived'},
    'watching': {'analysis_in_progress','pursuing','cancelled','amended_reanalysis_required'},
    'passed': {'archived','watching'},
    'amended_reanalysis_required': {'analysis_in_progress','documents_downloading','cancelled'},
    'amendment_review_required': {'analysis_in_progress','documents_downloading','pursuing','gate2_pending_outreach_send','proposal_in_progress','cancelled'},
    'submitted': {'archived','amended_reanalysis_required'},
    'archived': set(), 'cancelled': set(), 'closed_archived': set(),
}
GATES = {'pursue':'GATE 1 — Pursue or Pass','outreach':'GATE 2 — Authorize External Outreach','price':'GATE 3 — Approve Basis-of-Bid Subcontractors and Final Price','submission':'GATE 4 — Approve Final Submission Package'}


def now() -> str: return dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')
def sha256_bytes(data: bytes) -> str: return hashlib.sha256(data).hexdigest()
def sha256_file(p: Path) -> str: return hashlib.sha256(p.read_bytes()).hexdigest()
def safe(s: str, max_len: int=80) -> str:
    return re.sub(r'[^a-zA-Z0-9._-]+','-',s.lower()).strip('-')[:max_len] or 'opportunity'

def load_env() -> dict[str,str]:
    env=dict(os.environ)
    for p in ENV_PATHS:
        if p.exists():
            for line in p.read_text(errors='ignore').splitlines():
                if '=' in line and not line.strip().startswith('#'):
                    k,v=line.split('=',1); env.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    return env


def con() -> sqlite3.Connection:
    wfg_phase1.init_db(DB_PATH)
    c=sqlite3.connect(DB_PATH); c.row_factory=sqlite3.Row; return c


def migrate() -> None:
    c=con()
    cols={r['name'] for r in c.execute('pragma table_info(approvals)')}
    add=[]
    for name, typ in [('telegram_user_id','TEXT'),('artifact_version','TEXT'),('artifact_hash','TEXT'),('approved_price','TEXT'),('recipient_list_json','TEXT'),('conditions','TEXT'),('valid','INTEGER DEFAULT 1'),('invalidated_at','TEXT'),('invalidated_reason','TEXT')]:
        if name not in cols: add.append((name,typ))
    for name,typ in add: c.execute(f'alter table approvals add column {name} {typ}')
    c.executescript('''
    CREATE TABLE IF NOT EXISTS opportunity_intakes(id INTEGER PRIMARY KEY AUTOINCREMENT, dedupe_key TEXT, opportunity_folder TEXT, intake_at TEXT, version_hash TEXT, status TEXT, details_json TEXT);
    CREATE TABLE IF NOT EXISTS opportunity_version_manifests(version_hash TEXT PRIMARY KEY, dedupe_key TEXT NOT NULL, created_at TEXT NOT NULL, folder TEXT, metadata_hash TEXT, attachment_hashes_json TEXT, summary_path TEXT, stale_drafts_json TEXT);
    CREATE TABLE IF NOT EXISTS attachment_downloads(id INTEGER PRIMARY KEY AUTOINCREMENT, dedupe_key TEXT, source_url TEXT, retrieval_time TEXT, filename TEXT, content_type TEXT, size INTEGER, sha256 TEXT, local_path TEXT, version_hash TEXT, status TEXT, error TEXT, parse_status TEXT, parse_confidence TEXT, extraction_path TEXT, extraction_warnings TEXT);
    CREATE TABLE IF NOT EXISTS approval_commands(id INTEGER PRIMARY KEY AUTOINCREMENT, received_at TEXT, command_text TEXT, dedupe_key TEXT, gate TEXT, version TEXT, parsed_decision TEXT, accepted INTEGER, reason TEXT);
    ''')
    c.commit(); c.close()


def event(dedupe_key: str, event_type: str, details: dict[str,Any]|None=None) -> None:
    with con() as c:
        c.execute('insert into workflow_events(dedupe_key,event_type,event_at,actor,details_json) values(?,?,?,?,?)',(dedupe_key,event_type,now(),'wfg_phase2',json.dumps(details or {},sort_keys=True)))


def get_status(dedupe_key: str) -> str:
    with con() as c:
        r=c.execute('select workflow_status from opportunities where dedupe_key=?',(dedupe_key,)).fetchone()
    return (r['workflow_status'] if r else None) or 'discovered'


def transition(dedupe_key: str, new_status: str, reason: str='') -> None:
    if new_status not in STATUSES: raise ValueError(f'unknown workflow status {new_status}')
    old=get_status(dedupe_key)
    if old != new_status and new_status not in ALLOWED.get(old,set()):
        raise ValueError(f'invalid workflow transition {old} -> {new_status}')
    with con() as c:
        c.execute('update opportunities set workflow_status=? where dedupe_key=?',(new_status,dedupe_key)); c.commit()
    event(dedupe_key,'status_transition',{'from':old,'to':new_status,'reason':reason})


def item_from_db(identifier: str) -> tuple[str,dict[str,Any]]:
    ident=identifier.strip()
    with con() as c:
        r=c.execute('select dedupe_key, raw_json from opportunity_versions where dedupe_key=? order by id desc limit 1',(ident,)).fetchone()
        if not r:
            like=f'%{ident}%'
            r=c.execute('''select v.dedupe_key, v.raw_json from opportunity_versions v join opportunities o on o.dedupe_key=v.dedupe_key
                           where lower(o.notice_id)=lower(?) or lower(o.solicitation_number)=lower(?) or o.sam_link like ?
                           order by v.id desc limit 1''',(ident,ident,like)).fetchone()
    if not r: raise SystemExit(f'No local opportunity matched: {identifier}')
    return r['dedupe_key'], json.loads(r['raw_json'])


def opp_folder(item: dict[str,Any], dedupe_key: str) -> Path:
    nid=str(item.get('noticeId') or dedupe_key.replace(':','-'))
    sol=str(item.get('solicitationNumber') or '')
    title=safe(str(item.get('title') or sol or nid),60)
    folder=OPP_ROOT / safe(nid,40)
    if not folder.exists(): folder=OPP_ROOT / f"{safe(nid,28)}-{title}"
    for sub in ['source','versions','working','drafts','pricing','proposal','submission','audit','approvals','extracted-text']:
        (folder/sub).mkdir(parents=True, exist_ok=True)
    return folder


def resource_urls(item: dict[str,Any]) -> list[str]:
    raw=item.get('resourceLinks') or []
    out=[]
    for x in raw:
        if isinstance(x,dict): u=x.get('href') or x.get('url') or x.get('link')
        else: u=x
        if u: out.append(str(u))
    return list(dict.fromkeys(out))


def guess_name(url: str, headers=None, idx=0) -> str:
    headers=headers or {}
    cd=headers.get('Content-Disposition') or headers.get('content-disposition') or ''
    m=re.search(r"filename\*?=(?:UTF-8''|\")?([^\";]+)",cd,re.I)
    if m: return safe(urllib.parse.unquote(m.group(1)),120)
    base=Path(urllib.parse.urlparse(url).path).name
    return safe(base or f'attachment-{idx:02d}.bin',120)


def download(url: str, dest: Path, idx: int, timeout=60) -> dict[str,Any]:
    env=load_env(); retrieval=now()
    try:
        full=url
        if full.startswith('file://'):
            src=Path(urllib.parse.urlparse(full).path); data=src.read_bytes(); ctype=mimetypes.guess_type(str(src))[0] or 'application/octet-stream'; fname=src.name
        else:
            key=env.get('SAM_GOV_API_KEY') or env.get('SAM_API_KEY') or env.get('SAMGOV_API_KEY')
            if key and 'api_key=' not in full: full += ('&' if '?' in full else '?')+'api_key='+urllib.parse.quote(key)
            req=urllib.request.Request(full,headers={'User-Agent':'WFG-Hermes-Intake/2.0'})
            with urllib.request.urlopen(req,timeout=timeout) as r:
                data=r.read(); ctype=r.headers.get_content_type(); fname=guess_name(url,r.headers,idx)
        h=sha256_bytes(data); ext=Path(fname).suffix; target=dest/fname
        if target.exists() and sha256_file(target)!=h:
            target=dest/f"{Path(fname).stem}-{h[:12]}{ext}"
        target.write_bytes(data)
        return {'source_url':url,'retrieval_time':retrieval,'filename':target.name,'content_type':ctype,'size':len(data),'sha256':h,'local_path':str(target),'status':'downloaded','error':''}
    except Exception as e:
        return {'source_url':url,'retrieval_time':retrieval,'filename':'','content_type':'','size':0,'sha256':'','local_path':'','status':'failed','error':f'{type(e).__name__}: {e}'}


def extract_text(path: Path, out_dir: Path) -> dict[str,str]:
    out_dir.mkdir(parents=True,exist_ok=True); ext=path.suffix.lower(); warnings=[]; text=''; confidence='low'
    try:
        if ext=='.pdf':
            out=out_dir/f'{path.name}.txt'
            res=subprocess.run(['pdftotext','-layout',str(path),str(out)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=60)
            if out.exists(): text=out.read_text(errors='ignore'); confidence='medium' if text.strip() else 'low'
            if res.returncode: warnings.append(res.stderr.strip()[:300])
        elif ext in {'.docx','.xlsx'}:
            with zipfile.ZipFile(path) as z:
                names=z.namelist()
                if ext=='.docx': names=[n for n in names if n.startswith('word/') and n.endswith('.xml')]
                else: names=[n for n in names if (n.startswith('xl/worksheets/') or n=='xl/sharedStrings.xml') and n.endswith('.xml')]
                parts=[]
                for n in names:
                    raw=z.read(n).decode('utf-8','ignore')
                    raw=html.unescape(re.sub(r'<[^>]+>',' ',raw)); raw=re.sub(r'\s+',' ',raw).strip()
                    if raw: parts.append(f'[{n}]\n{raw}')
                text='\n\n'.join(parts); confidence='medium' if text else 'low'
        elif ext in {'.txt','.csv','.md','.json','.xml'}:
            text=path.read_text(errors='ignore'); confidence='high' if text else 'low'
        else:
            warnings.append(f'unsupported extraction type {ext or "no extension"}')
    except Exception as e:
        warnings.append(f'{type(e).__name__}: {e}')
    extraction_path=''
    if text:
        op=out_dir/f'{path.name}.extracted.txt'; op.write_text(text[:500000]); extraction_path=str(op)
    return {'parse_status':'parsed' if text else 'not_parsed','parse_confidence':confidence,'extraction_path':extraction_path,'extraction_warnings':'; '.join(w for w in warnings if w)}


def metadata_hash(item: dict[str,Any]) -> str:
    relevant={k:item.get(k) for k in ['noticeId','solicitationNumber','title','postedDate','responseDeadLine','type','typeOfSetAside','typeOfSetAsideDescription','naicsCode','classificationCode','uiLink','resourceLinks']}
    return hashlib.sha256(json.dumps(relevant,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def version_hash(item: dict[str,Any], downloads: list[dict[str,Any]]) -> str:
    payload={'metadata':metadata_hash(item),'attachments':sorted([d.get('sha256') or d.get('source_url','') for d in downloads])}
    return hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()


def previous_version(dedupe_key: str) -> str|None:
    with con() as c:
        r=c.execute('select version_hash from opportunity_version_manifests where dedupe_key=? order by created_at desc limit 1',(dedupe_key,)).fetchone()
    return r['version_hash'] if r else None


def attachment_diff(dedupe_key: str, new_downloads: list[dict[str,Any]]) -> dict[str,list[str]]:
    new={d['source_url']:d.get('sha256','') for d in new_downloads if d.get('status')=='downloaded'}
    with con() as c:
        rows=c.execute('select source_url, sha256 from attachment_downloads where dedupe_key=? and status="downloaded"',(dedupe_key,)).fetchall()
    old={r['source_url']:r['sha256'] for r in rows}
    return {'added':sorted([u for u in new if u not in old]), 'removed':sorted([u for u in old if u not in new]), 'changed':sorted([u for u in new if u in old and old[u]!=new[u]])}


def manifest_md(item:dict[str,Any], dedupe_key:str, vhash:str, downloads:list[dict[str,Any]], diffs:dict[str,list[str]]) -> str:
    lines=[f'# Attachment Manifest — {vhash}', '', f'- Notice ID: {item.get("noticeId")}', f'- Solicitation: {item.get("solicitationNumber")}', f'- Created: {now()}', f'- Dedupe key: `{dedupe_key}`','', '## Version changes','']
    for k in ['added','removed','changed']: lines.append(f'- {k}: {len(diffs.get(k,[]))} ' + (', '.join(diffs.get(k,[])[:5]) if diffs.get(k) else ''))
    lines += ['', '## Attachments','']
    for d in downloads:
        lines += [f"- Source URL: {d['source_url']}", f"  - Retrieval time: {d['retrieval_time']}", f"  - Filename: `{d.get('filename','')}`", f"  - Content type: {d.get('content_type','')}", f"  - Size: {d.get('size',0)}", f"  - SHA-256: `{d.get('sha256','')}`", f"  - Status: {d.get('status')} {d.get('error','')}", f"  - Parse: {d.get('parse_status','')} / confidence {d.get('parse_confidence','')}", f"  - Extraction warnings: {d.get('extraction_warnings','') or 'none'}", '']
    return '\n'.join(lines)


def citations_from_extracts(folder: Path) -> list[str]:
    cites=[]
    for p in sorted((folder/'extracted-text').glob('*.extracted.txt'))[:8]:
        text=p.read_text(errors='ignore')[:2000]
        for kw in ['deadline','submission','quote','wage','bond','insurance','site visit','evaluation','past performance']:
            m=re.search(r'.{0,80}'+re.escape(kw)+r'.{0,160}',text,re.I)
            if m: cites.append(f'- `{p.name}`: ...{m.group(0).strip()}...')
    return cites or ['- [DOCUMENT MISSING] No important requirements extracted with source references yet.']


def deadline_days(item):
    raw=item.get('responseDeadLine') or item.get('responseDeadline')
    try: return (dt.datetime.fromisoformat(str(raw).replace('Z','+00:00')).date()-dt.date.today()).days
    except Exception: return None


def write_drafts(folder: Path, item:dict[str,Any], dedupe_key:str, vhash:str, downloads:list[dict[str,Any]]) -> list[Path]:
    title=item.get('title') or 'Untitled'; sol=item.get('solicitationNumber') or ''; nid=item.get('noticeId') or ''
    facts=f"""- Notice ID: {nid}\n- Solicitation number: {sol}\n- Title: {title}\n- Agency: {item.get('fullParentPathName') or item.get('department')}\n- NAICS: {item.get('naicsCode')}\n- PSC: {item.get('classificationCode')}\n- Set-aside: {item.get('typeOfSetAsideDescription') or item.get('typeOfSetAside')}\n- Response deadline: {item.get('responseDeadLine') or item.get('responseDeadline')} ({deadline_days(item)} days if parsed)\n- SAM link: {item.get('uiLink')}\n- Current artifact version: `{vhash}`\n"""
    sources='\n'.join(citations_from_extracts(folder))
    docs='\n'.join(f"- `{d.get('filename')}` — {d.get('status')} — {d.get('sha256','')[:12]}" for d in downloads) or '- [DOCUMENT MISSING] No resource links.'
    markers='[USER INPUT REQUIRED] [DOCUMENT MISSING] [ASSUMPTION — MUST BE CONFIRMED] [SUBCONTRACTOR NOT VERIFIED] [QUOTE NOT RECEIVED] [PRICE NOT APPROVED] [LEGAL OR COMPLIANCE REVIEW REQUIRED] [NOT READY FOR SUBMISSION]'
    templates={
'00_INTAKE.md':f"# 00_INTAKE\n\n## Verified solicitation facts\n{facts}\n## Attachments\n{docs}\n## Extracted facts with source references\n{sources}\n## Agent inference\n- This opportunity requires controlled WFG review before any external action.\n## Missing information / human decisions\n- {markers}\n",
'01_BID_NO_BID_SCORECARD.md':f"# 01_BID_NO_BID_SCORECARD\n\n## Preliminary recommendation\n[USER INPUT REQUIRED] Gate 1 required: pursue or pass.\n\n## Verified facts\n{facts}\n## Fit reasons\n- Fits WFG starter workflow if scope, deadline, subcontractor availability, insurance/bonding, and set-aside eligibility check out.\n## Risks / why it may not fit\n- [SUBCONTRACTOR NOT VERIFIED]\n- [PRICE NOT APPROVED]\n- [LEGAL OR COMPLIANCE REVIEW REQUIRED]\n## Source references\n{sources}\n",
'02_SOLICITATION_BRIEF.md':f"# 02_SOLICITATION_BRIEF\n\n## Verified facts\n{facts}\n## Scope summary\n[ASSUMPTION — MUST BE CONFIRMED] Scope inferred from title/metadata until document review is complete.\n## Source references\n{sources}\n## Human decisions\n- Gate 1 pursue/pass.\n",
'03_COMPLIANCE_MATRIX.md':f"# 03_COMPLIANCE_MATRIX\n\n| Requirement | Source | Status | Notes |\n|---|---|---|---|\n| Response deadline | SAM record | Draft | {item.get('responseDeadLine') or item.get('responseDeadline')} |\n| Submission method | Solicitation docs | [DOCUMENT MISSING] | Must be confirmed before Gate 4 |\n| Set-aside eligibility | SAM record / WFG directive | [LEGAL OR COMPLIANCE REVIEW REQUIRED] | {item.get('typeOfSetAsideDescription')} |\n| Wage determination | Attachments | [DOCUMENT MISSING] | Check DBA/SCA |\n| Bond/insurance | Attachments | [DOCUMENT MISSING] | Check before pricing approval |\n| Required forms | Attachments | [DOCUMENT MISSING] | Check before proposal package |\n\n## Source references\n{sources}\n",
'04_MISSING_INFORMATION.md':f"# 04_MISSING_INFORMATION\n\n- [DOCUMENT MISSING] Submission method, required forms, Q&A deadline, amendments, wage determinations.\n- [USER INPUT REQUIRED] WFG eligibility and authorized approver.\n- [SUBCONTRACTOR NOT VERIFIED] No vendor contact made.\n- [QUOTE NOT RECEIVED] No pricing support.\n- [PRICE NOT APPROVED] No final bid price.\n",
'05_SCOPE_DECOMPOSITION.md':f"# 05_SCOPE_DECOMPOSITION\n\n## Verified solicitation facts\n{facts}\n## Work packages\n- Primary scope: [ASSUMPTION — MUST BE CONFIRMED]\n- Labor/material/equipment split: [DOCUMENT MISSING]\n- Subcontractor package: [SUBCONTRACTOR NOT VERIFIED]\n## Source references\n{sources}\n",
'06_SUBCONTRACTOR_SOURCING_CRITERIA.md':f"# 06_SUBCONTRACTOR_SOURCING_CRITERIA\n\n- Trade/NAICS: {item.get('naicsCode')}\n- Location: {item.get('placeOfPerformance') or '[DOCUMENT MISSING]'}\n- Must quote before WFG internal deadline: [USER INPUT REQUIRED]\n- Must provide license/insurance/schedule/exclusions.\n- Status: [SUBCONTRACTOR NOT VERIFIED]\n",
'07_DRAFT_OUTREACH.md':f"# 07_DRAFT_OUTREACH\n\n[DO NOT SEND — GATE 2 APPROVAL REQUIRED]\n\nHello, we are evaluating a government opportunity ({sol}) involving [ASSUMPTION — MUST BE CONFIRMED] near [DOCUMENT MISSING/SEE SAM LOCATION]. Can your team review the scope and provide subcontractor pricing by [USER INPUT REQUIRED]? Please include labor, materials, mobilization, exclusions, licensing/insurance status, and schedule.\n",
'08_PRICING_ASSUMPTIONS.md':f"# 08_PRICING_ASSUMPTIONS\n\n- [QUOTE NOT RECEIVED] No subcontractor quote.\n- [PRICE NOT APPROVED] No final price.\n- [ASSUMPTION — MUST BE CONFIRMED] OH/contingency/profit ranges only for scenario planning.\n- [LEGAL OR COMPLIANCE REVIEW REQUIRED] LOS/wage/bond treatment must inspect actual clauses.\n",
'09_TECHNICAL_PROPOSAL_SKELETON.md':f"# 09_TECHNICAL_PROPOSAL_SKELETON\n\n## Technical approach\n[ASSUMPTION — MUST BE CONFIRMED]\n## Staffing/subcontractor plan\n[SUBCONTRACTOR NOT VERIFIED]\n## Schedule\n[USER INPUT REQUIRED]\n## Quality control\nDraft only.\n## Submission status\n[NOT READY FOR SUBMISSION]\n",
'10_REQUIRED_FORMS_CHECKLIST.md':f"# 10_REQUIRED_FORMS_CHECKLIST\n\n- [DOCUMENT MISSING] Solicitation forms.\n- [DOCUMENT MISSING] Pricing schedule.\n- [DOCUMENT MISSING] Amendment acknowledgments.\n- [LEGAL OR COMPLIANCE REVIEW REQUIRED] Reps/certs.\n",
'11_SUBMISSION_CHECKLIST.md':f"# 11_SUBMISSION_CHECKLIST\n\n[NOT READY FOR SUBMISSION]\n\n- Confirm final docs/amendments.\n- Complete compliance matrix.\n- Approve final price.\n- Approve submission package.\n- Human submits manually; Hermes does not transmit.\n",
'12_RISK_REGISTER.md':f"# 12_RISK_REGISTER\n\n| Risk | Status | Mitigation |\n|---|---|---|\n| Missing documents/requirements | [DOCUMENT MISSING] | Complete document review |\n| No subcontractor quote | [QUOTE NOT RECEIVED] | Gate 2 outreach approval required |\n| No approved price | [PRICE NOT APPROVED] | Gate 3 required |\n| Compliance uncertainty | [LEGAL OR COMPLIANCE REVIEW REQUIRED] | Clause/LOS/wage review |\n| Submission not authorized | [NOT READY FOR SUBMISSION] | Gate 4 required |\n",
}
    written=[]
    for name,body in templates.items():
        p=folder/'drafts'/name; p.write_text(body); written.append(p)
        # compatibility copy at folder root
        (folder/name).write_text(body)
    (folder/'source'/'source-link.txt').write_text(str(item.get('uiLink') or ''))
    (folder/'source'/'raw-sam-record.json').write_text(json.dumps(item,indent=2,ensure_ascii=False))
    return written


def package_hash(paths:list[Path]) -> str:
    h=hashlib.sha256()
    for p in sorted(paths): h.update(p.name.encode()+b'\0'+p.read_bytes())
    return h.hexdigest()


def new_approval_id() -> str:
    return 'appr_' + secrets.token_urlsafe(12).replace('-', '').replace('_', '')[:16]

def approval_details(approval_id: str) -> dict[str,Any]:
    with con() as c:
        r=c.execute('select * from approvals where approval_id=?',(approval_id,)).fetchone()
    if not r: return {'found':False,'reason':'approval id not found'}
    return {'found':True, **{k:r[k] for k in r.keys()}}

def approval_packet(folder:Path,item:dict[str,Any],dedupe_key:str,gate:str,vhash:str,art_hash:str, paths:list[Path]) -> Path:
    gid=GATES[gate]; nid=item.get('noticeId'); sol=item.get('solicitationNumber'); title=item.get('title'); approval_id=new_approval_id()
    p=folder/'approvals'/f'{safe(gate)}-{vhash[:12]}-approval.md'
    action={'pursue':'Authorize WFG to pursue analysis/bid preparation for this opportunity, without external outreach yet.', 'outreach':'Authorize external subcontractor outreach using the exact draft outreach package.', 'price':'Approve basis-of-bid subcontractors and final price. [PRICE NOT APPROVED]', 'submission':'Approve final submission package for human submission only; Hermes must not transmit.'}[gate]
    command={'pursue':f'APPROVE PURSUE {nid} {vhash}', 'outreach':f'APPROVE OUTREACH {nid} {vhash}', 'price':f'APPROVE PRICE {nid} {vhash} <amount>', 'submission':f'APPROVE SUBMISSION {nid} {vhash}'}[gate]
    p.write_text(f"""# APPROVAL NEEDED — {title}\n\nApproval ID: {approval_id}\nCreated at: {now()}\nRequested by agent/subagent: Marcus / wfg_phase2\nOpportunity / project: {title}\nOpportunity folder: `{folder}`\nApproval type: {gid}\nCurrent status: pending human decision\nNotice ID: {nid}\nSolicitation number: {sol}\nArtifact/package version: `{vhash}`\nArtifact hash: `{art_hash}`\n\n## What has already been drafted\n""" + '\n'.join(f'- `{x}`' for x in paths) + f"""\n\n## What remains incomplete\n- [USER INPUT REQUIRED] Human gate decision.\n- [DOCUMENT MISSING] Any missing requirement identified in drafts.\n- [SUBCONTRACTOR NOT VERIFIED] No external outreach or quote received.\n- [PRICE NOT APPROVED] unless this is an approved price gate with amount.\n\n## Important risks\n- Deadline/time remaining: {item.get('responseDeadLine') or item.get('responseDeadline')} ({deadline_days(item)} days if parsed).\n- Approval of a prior version is invalid if package version changes.\n- This approval authorizes only the exact action/version shown.\n\n## Assumptions requiring confirmation\n- [ASSUMPTION — MUST BE CONFIRMED] WFG can legally pursue as prime.\n- [LEGAL OR COMPLIANCE REVIEW REQUIRED] Solicitation clauses and wage/bond/LOS treatment.\n\n## Exact action requiring authorization\n{action}\n\n## Exact item being approved\nArtifact/package version `{vhash}` with hash `{art_hash}`.\nRecipients/message/price/package: see listed files.\n\n## Expiration or invalidation condition\nThis request expires or is invalidated if any source attachment, package artifact, final price, recipients, or submission package changes.\n\n## Recommended decision\nReview and use exact command only if you approve this exact version.\n\n## Approval commands\n- {command}\n- PASS {nid} {vhash}\n\nAmbiguous replies, emoji reactions, silence, or approval of another version do not authorize this action.\n""")
    pending=PROJECT/'approvals'/'pending'/p.name; pending.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(p,pending)
    with con() as c:
        c.execute('insert into approvals(dedupe_key,gate,requested_at,decision,record_path,artifact_version,artifact_hash,valid,details_json,approval_id,exact_action,environment) values(?,?,?,?,?,?,?,?,?,?,?,?)',(dedupe_key,gid,now(),'pending',str(p),vhash,art_hash,1,json.dumps({'notice_id':nid,'solicitation':sol,'pending_packet':str(pending),'canonical_commands':['APPROVE '+approval_id,'REJECT '+approval_id,'DETAILS '+approval_id]}),approval_id,action,os.environ.get('WFG_ENV','production')))
        c.commit()
    event(dedupe_key,'approval_requested',{'gate':gid,'version':vhash,'artifact_hash':art_hash,'packet':str(p)})
    return p


def invalidate_approvals(dedupe_key:str, reason:str) -> None:
    with con() as c:
        c.execute('update approvals set valid=0, invalidated_at=?, invalidated_reason=? where dedupe_key=? and valid=1 and decision in ("pending","approved")',(now(),reason,dedupe_key)); c.commit()
    event(dedupe_key,'approvals_invalidated',{'reason':reason})


def intake(identifier: str, fixture_no_network: bool=False) -> dict[str,Any]:
    migrate(); dedupe_key,item=item_from_db(identifier); folder=opp_folder(item,dedupe_key)
    transition(dedupe_key,'analysis_in_progress','Phase 2 intake selected')
    transition(dedupe_key,'documents_downloading','Downloading public solicitation attachments')
    downloads=[]; source_dir=folder/'source'; extract_dir=folder/'extracted-text'
    diffs_preview=[]
    for idx,u in enumerate(resource_urls(item),1):
        d=download(u,source_dir,idx) if not fixture_no_network or u.startswith('file://') else {'source_url':u,'retrieval_time':now(),'filename':'','content_type':'','size':0,'sha256':'','local_path':'','status':'failed','error':'network disabled for fixture test'}
        if d.get('local_path'):
            d.update(extract_text(Path(d['local_path']),extract_dir))
        else:
            d.update({'parse_status':'not_parsed','parse_confidence':'low','extraction_path':'','extraction_warnings':'download failed'})
        downloads.append(d)
    diffs=attachment_diff(dedupe_key,downloads); vhash=version_hash(item,downloads); prev=previous_version(dedupe_key)
    for d in downloads: d['version_hash']=vhash
    if prev and prev!=vhash and (diffs['added'] or diffs['removed'] or diffs['changed']):
        invalidate_approvals(dedupe_key,'source attachment/version changed')
        transition(dedupe_key,'amended_reanalysis_required','Attachment/version changed')
        transition(dedupe_key,'analysis_in_progress','Automatic reanalysis after amendment')
        transition(dedupe_key,'documents_downloading','Refreshing changed attachments')
    with con() as c:
        for d in downloads:
            c.execute('''insert into attachment_downloads(dedupe_key,source_url,retrieval_time,filename,content_type,size,sha256,local_path,version_hash,status,error,parse_status,parse_confidence,extraction_path,extraction_warnings) values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',(dedupe_key,d['source_url'],d['retrieval_time'],d.get('filename'),d.get('content_type'),d.get('size'),d.get('sha256'),d.get('local_path'),vhash,d.get('status'),d.get('error'),d.get('parse_status'),d.get('parse_confidence'),d.get('extraction_path'),d.get('extraction_warnings')))
        c.execute('insert or replace into opportunity_version_manifests(version_hash,dedupe_key,created_at,folder,metadata_hash,attachment_hashes_json,summary_path,stale_drafts_json) values(?,?,?,?,?,?,?,?)',(vhash,dedupe_key,now(),str(folder),metadata_hash(item),json.dumps([d.get('sha256') for d in downloads]),str(folder/'versions'/f'{vhash}.md'),json.dumps([])))
        c.execute('insert into opportunity_intakes(dedupe_key,opportunity_folder,intake_at,version_hash,status,details_json) values(?,?,?,?,?,?)',(dedupe_key,str(folder),now(),vhash,'complete',json.dumps({'previous_version':prev,'diffs':diffs})))
        c.commit()
    (folder/'attachment_manifest.md').write_text(manifest_md(item,dedupe_key,vhash,downloads,diffs))
    (folder/'versions'/f'{vhash}.md').write_text(manifest_md(item,dedupe_key,vhash,downloads,diffs))
    transition(dedupe_key,'documents_complete','Attachments downloaded/recorded')
    draft_paths=write_drafts(folder,item,dedupe_key,vhash,downloads)
    transition(dedupe_key,'analysis_complete','Internal analysis drafts generated')
    transition(dedupe_key,'drafting_complete','Non-binding draft chain generated')
    art_hash=package_hash(draft_paths+[folder/'attachment_manifest.md'])
    packet=approval_packet(folder,item,dedupe_key,'pursue',vhash,art_hash,draft_paths[:5]+[folder/'attachment_manifest.md'])
    transition(dedupe_key,'awaiting_pursue_decision','Gate 1 packet ready')
    return {'dedupe_key':dedupe_key,'notice_id':item.get('noticeId'),'solicitation':item.get('solicitationNumber'),'title':item.get('title'),'folder':str(folder),'version_hash':vhash,'previous_version':prev,'diffs':diffs,'downloads':downloads,'drafts':[str(p) for p in draft_paths],'approval_packet':str(packet),'artifact_hash':art_hash}


def parse_approval_command(text: str) -> dict[str,Any]:
    t=' '.join(text.strip().split())
    mcanon=re.match(r'^(APPROVE|REJECT|DETAILS)\s+(appr_[A-Za-z0-9]+)$', t, re.I)
    if mcanon:
        return {'accepted_syntax':True,'canonical':True,'action':mcanon.group(1).lower(),'approval_id':mcanon.group(2)}
    patterns=[('pursue',r'^APPROVE PURSUE (\S+) (\S+)$'),('pass',r'^PASS (\S+) (\S+)$'),('outreach',r'^APPROVE OUTREACH (\S+) (\S+)$'),('price',r'^APPROVE PRICE (\S+) (\S+) ([\d,]+(?:\.\d{1,2})?)$'),('submission',r'^APPROVE SUBMISSION (\S+) (\S+)$')]
    for gate,pat in patterns:
        m=re.match(pat,t,re.I)
        if m:
            return {'accepted_syntax':True,'gate':gate,'identifier':m.group(1),'version':m.group(2),'amount':m.group(3) if len(m.groups())>=3 else None}
    return {'accepted_syntax':False,'reason':'ambiguous or unsupported approval wording'}


def record_approval_command(text: str, approver='UNKNOWN', telegram_user_id: str|None=None) -> dict[str,Any]:
    migrate(); parsed=parse_approval_command(text); accepted=0; reason=parsed.get('reason','')
    dedupe_key=''; gate_label=''
    if parsed.get('accepted_syntax') and parsed.get('canonical'):
        with con() as c:
            appr=c.execute('select * from approvals where approval_id=?',(parsed['approval_id'],)).fetchone()
        if not appr:
            reason='approval id not found'
        elif appr['valid']!=1 or appr['decision']!='pending' or appr['used_at']:
            dedupe_key=appr['dedupe_key'] or ''; gate_label=appr['gate'] or ''; reason='approval id expired, invalidated, used, or not pending'
        elif parsed['action']=='details':
            dedupe_key=appr['dedupe_key'] or ''; gate_label=appr['gate'] or ''; reason='details requested'; accepted=0
        else:
            dedupe_key=appr['dedupe_key'] or ''; gate_label=appr['gate'] or ''; accepted=1; decision='approved' if parsed['action']=='approve' else 'rejected'; reason='accepted canonical approval id'
            with con() as c:
                c.execute('update approvals set decision=?, decided_at=?, approver=?, telegram_user_id=?, used_at=?, conditions=? where id=?',(decision,now(),approver,telegram_user_id,now(),'canonical approval id exact match',appr['id'])); c.commit()
            event(dedupe_key,'approval_decision',{'gate':gate_label,'decision':decision,'approval_id':parsed['approval_id'],'artifact_version':appr['artifact_version'],'artifact_hash':appr['artifact_hash']})
            if decision=='approved' and 'Pursue' in gate_label: transition(dedupe_key,'pursuing','Gate 1 canonical approval recorded')
            elif decision=='rejected': transition(dedupe_key,'passed','Canonical reject recorded')
    elif parsed.get('accepted_syntax'):
        try:
            dedupe_key,item=item_from_db(parsed['identifier']); gate_label=GATES.get(parsed['gate'],parsed['gate'])
            with con() as c:
                appr=c.execute('select * from approvals where dedupe_key=? and artifact_version=? and gate like ? and valid=1 order by id desc limit 1',(dedupe_key,parsed['version'],f'%{gate_label.split(" — ")[1] if " — " in gate_label else gate_label}%')).fetchone()
            if appr:
                accepted=1; reason='accepted exact command/version'
                decision='passed' if parsed['gate']=='pass' else 'approved'
                with con() as c:
                    c.execute('update approvals set decision=?, decided_at=?, approver=?, telegram_user_id=?, approved_price=?, conditions=? where id=?',(decision,now(),approver,telegram_user_id,parsed.get('amount'),'exact command match',appr['id'])); c.commit()
                event(dedupe_key,'approval_decision',{'gate':gate_label,'decision':decision,'version':parsed['version'],'amount':parsed.get('amount')})
                # Continue non-binding state progression, no external action.
                if parsed['gate']=='pursue': transition(dedupe_key,'pursuing','Gate 1 approval recorded')
                elif parsed['gate']=='pass': transition(dedupe_key,'passed','Pass decision recorded')
                elif parsed['gate']=='outreach': transition(dedupe_key,'outreach_approved','Gate 2 approval recorded; no send performed by approval parser')
                elif parsed['gate']=='price': transition(dedupe_key,'proposal_in_progress','Gate 3 price approval recorded')
                elif parsed['gate']=='submission': transition(dedupe_key,'awaiting_submission_approval','Gate 4 recorded for human submission package; no transmission')
            else:
                reason='no valid pending approval matches this exact version/gate'
        except Exception as e: reason=f'{type(e).__name__}: {e}'
    with con() as c:
        c.execute('insert into approval_commands(received_at,command_text,dedupe_key,gate,version,parsed_decision,accepted,reason) values(?,?,?,?,?,?,?,?)',(now(),text,dedupe_key,gate_label,parsed.get('version'),parsed.get('gate'),accepted,reason)); c.commit()
    return {'accepted':bool(accepted),'reason':reason,'parsed':parsed,'dedupe_key':dedupe_key}


def main() -> int:
    ap=argparse.ArgumentParser(); sub=ap.add_subparsers(dest='cmd',required=True)
    p=sub.add_parser('migrate')
    p=sub.add_parser('intake'); p.add_argument('identifier'); p.add_argument('--fixture-no-network',action='store_true')
    p=sub.add_parser('approve-command'); p.add_argument('text'); p.add_argument('--approver',default='UNKNOWN'); p.add_argument('--telegram-user-id')
    args=ap.parse_args()
    if args.cmd=='migrate': migrate(); print(json.dumps({'ok':True,'db':str(DB_PATH),'statuses':STATUSES},indent=2)); return 0
    if args.cmd=='intake': print(json.dumps(intake(args.identifier,args.fixture_no_network),indent=2)); return 0
    if args.cmd=='approve-command': print(json.dumps(record_approval_command(args.text,args.approver,args.telegram_user_id),indent=2)); return 0
    return 0

if __name__=='__main__': raise SystemExit(main())
