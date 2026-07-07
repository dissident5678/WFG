#!/usr/bin/env python3
"""Run WFG test intake for selected SAM.gov opportunities.

Creates organized local opportunity folders, downloads SAM attachments, drafts non-binding
artifacts, creates Google Drive folders, uploads artifacts, and posts approval packets.
"""
from __future__ import annotations

import csv
import datetime as dt
import html
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from typing import Any

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

PROJECT = Path(os.environ.get('WFG_PROJECT_DIR', '/home/nick/workspace/wfg-gov-contracting-v2'))
ARCHIVE = PROJECT / 'opportunity-searches' / 'sam-api'
OPP_ROOT = PROJECT / 'opportunities'
SUMMARY_PATH = PROJECT / 'opportunities' / 'test-run-2026-06-23-summary.md'
DRIVE_TOKEN = Path('/home/nick/.hermes/google_token.json')
ENV_PATH = Path('/home/nick/.hermes/.env')
APPROVAL_TOPIC = 'telegram:-1003889564123:295'
TODAY = dt.date.today().isoformat()

TARGET_IDS = [
    'd85f3f5c453348b7ad0738472a15c8d0',  # Lights & Electric Repairs
    'f55267ff7c544844a34ad627ad3d21a0',  # DAFB ISWM (representative notice)
    'f788acf325d14e20b62e3d49b4ce3695',  # Specialty Cleaning BPA
]


def load_env() -> dict[str, str]:
    out = dict(os.environ)
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text(errors='ignore').splitlines():
            s = line.strip()
            if not s or s.startswith('#') or '=' not in s:
                continue
            k, v = s.split('=', 1)
            out.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    return out

ENV = load_env()


def service(name: str, version: str):
    creds = Credentials.from_authorized_user_file(str(DRIVE_TOKEN))
    if not creds.valid and creds.refresh_token:
        creds.refresh(Request())
        DRIVE_TOKEN.write_text(creds.to_json())
    return build(name, version, credentials=creds)


def drive_escape(s: str) -> str:
    return s.replace("'", "\\'")


def find_drive_folder(drive, name: str, parent: str | None = None) -> str | None:
    q = f"name='{drive_escape(name)}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
    if parent:
        q += f" and '{parent}' in parents"
    res = drive.files().list(q=q, fields='files(id,name)', pageSize=10).execute()
    files = res.get('files', [])
    return files[0]['id'] if files else None


def ensure_drive_folder(drive, name: str, parent: str | None = None) -> str:
    existing = find_drive_folder(drive, name, parent)
    if existing:
        return existing
    meta = {'name': name, 'mimeType': 'application/vnd.google-apps.folder'}
    if parent:
        meta['parents'] = [parent]
    created = drive.files().create(body=meta, fields='id,name,webViewLink').execute()
    return created['id']


def upload_file(drive, local: Path, parent_id: str) -> str:
    # overwrite by name in parent if present for idempotent test reruns
    q = f"name='{drive_escape(local.name)}' and '{parent_id}' in parents and trashed=false"
    res = drive.files().list(q=q, fields='files(id,name)', pageSize=10).execute()
    media = MediaFileUpload(str(local), resumable=False)
    meta = {'name': local.name, 'parents': [parent_id]}
    if res.get('files'):
        fid = res['files'][0]['id']
        drive.files().update(fileId=fid, media_body=media, fields='id,webViewLink').execute()
        return fid
    created = drive.files().create(body=meta, media_body=media, fields='id,webViewLink').execute()
    return created['id']


def upload_tree(drive, local_dir: Path, parent_id: str) -> None:
    for child in sorted(local_dir.iterdir()):
        if child.is_dir():
            cid = ensure_drive_folder(drive, child.name, parent_id)
            upload_tree(drive, child, cid)
        elif child.is_file():
            upload_file(drive, child, parent_id)


def drive_link(drive, file_id: str) -> str:
    meta = drive.files().get(fileId=file_id, fields='webViewLink,id,name').execute()
    return meta.get('webViewLink', f'https://drive.google.com/drive/folders/{file_id}')


def find_records() -> dict[str, dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    for p in sorted(ARCHIVE.glob('raw-*.json')):
        try:
            data = json.loads(p.read_text(errors='replace'))
        except Exception:
            continue
        for item in data.get('opportunitiesData') or data.get('data') or []:
            nid = str(item.get('noticeId') or '')
            if nid in TARGET_IDS:
                rec = found.setdefault(nid, {'item': item, 'files': []})
                rec['item'] = item
                rec['files'].append(p.name)
    return found


def slugify(s: str, max_len: int = 70) -> str:
    s = re.sub(r'[^a-zA-Z0-9]+', '-', s.lower()).strip('-')
    return s[:max_len].strip('-') or 'opportunity'


def write_note(notes: Path, seq: int, title: str, body: str) -> None:
    notes.mkdir(parents=True, exist_ok=True)
    p = notes / f'{TODAY}_{seq:03d}_{slugify(title, 32)}.txt'
    p.write_text(f'{dt.datetime.now().isoformat(timespec="seconds")}\n{title}\n\n{body}\n')


def request_json(url: str, timeout: int = 30) -> Any:
    req = urllib.request.Request(url, headers={'User-Agent': 'WFG-Hermes-Intake/1.0'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode('utf-8', 'replace'))


def fetch_description(item: dict) -> str:
    url = item.get('description') or ''
    key = ENV.get('SAM_GOV_API_KEY') or ENV.get('SAM_API_KEY') or ENV.get('SAMGOV_API_KEY')
    if not url:
        return ''
    if key and 'api_key=' not in url:
        url += ('&' if '?' in url else '?') + 'api_key=' + urllib.parse.quote(key)
    try:
        data = request_json(url, 20)
        text = data.get('description') if isinstance(data, dict) else str(data)
    except Exception as e:
        return f'[DESCRIPTION FETCH FAILED: {type(e).__name__}: {e}]'
    text = re.sub(r'<[^>]+>', ' ', str(text or ''))
    return html.unescape(re.sub(r'\s+', ' ', text).strip())


def content_filename(headers, fallback: str) -> str:
    cd = headers.get('Content-Disposition') or headers.get('content-disposition') or ''
    m = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)', cd, re.I)
    if m:
        return urllib.parse.unquote(m.group(1)).strip().strip('"')
    return fallback


def download_resources(item: dict, dest: Path) -> list[dict[str, str]]:
    dest.mkdir(parents=True, exist_ok=True)
    out = []
    links = item.get('resourceLinks') or []
    key = ENV.get('SAM_GOV_API_KEY') or ENV.get('SAM_API_KEY') or ENV.get('SAMGOV_API_KEY')
    for idx, url in enumerate(links, 1):
        dl_url = url
        if key and 'api_key=' not in dl_url:
            dl_url += ('&' if '?' in dl_url else '?') + 'api_key=' + urllib.parse.quote(key)
        try:
            req = urllib.request.Request(dl_url, headers={'User-Agent': 'WFG-Hermes-Intake/1.0'})
            with urllib.request.urlopen(req, timeout=60) as r:
                data = r.read()
                fname = content_filename(r.headers, f'attachment_{idx:02d}.bin')
                fname = re.sub(r'[\\/]+', '_', fname)
                path = dest / fname
                if path.exists():
                    stem, suffix = path.stem, path.suffix
                    path = dest / f'{stem}_{idx:02d}{suffix}'
                path.write_bytes(data)
                out.append({'url': url, 'path': str(path), 'status': 'downloaded', 'bytes': str(len(data))})
        except Exception as e:
            out.append({'url': url, 'path': '', 'status': f'FAILED {type(e).__name__}: {e}', 'bytes': '0'})
    return out


def file_type(path: Path) -> str:
    try:
        return subprocess.run(['file', '-b', str(path)], text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=10).stdout.strip()
    except Exception:
        return ''


def extract_pdf_text(path: Path) -> str:
    out = path.with_suffix(path.suffix + '.txt')
    try:
        subprocess.run(['pdftotext', '-layout', str(path), str(out)], check=False, timeout=60, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if out.exists():
            return out.read_text(errors='ignore')[:12000]
    except Exception:
        pass
    return ''


def extract_zip_xml_text(path: Path) -> str:
    texts = []
    try:
        with zipfile.ZipFile(path) as z:
            for name in z.namelist():
                if name.endswith('.xml') and any(part in name for part in ['word/document', 'xl/sharedStrings', 'ppt/slides']):
                    raw = z.read(name).decode('utf-8', 'ignore')
                    raw = re.sub(r'<[^>]+>', ' ', raw)
                    raw = html.unescape(re.sub(r'\s+', ' ', raw))
                    if raw.strip():
                        texts.append(raw.strip())
    except Exception:
        return ''
    return '\n'.join(texts)[:12000]


def extract_docs(doc_dir: Path, out_dir: Path) -> dict[str, str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    snippets = {}
    for p in sorted(doc_dir.iterdir()):
        if not p.is_file():
            continue
        typ = file_type(p)
        text = ''
        if 'PDF' in typ or p.suffix.lower() == '.pdf':
            text = extract_pdf_text(p)
        elif 'Zip archive' in typ or p.suffix.lower() in {'.docx', '.xlsx', '.pptx'}:
            text = extract_zip_xml_text(p)
        elif 'text' in typ.lower() or p.suffix.lower() in {'.txt', '.csv', '.md'}:
            text = p.read_text(errors='ignore')[:12000]
        if text:
            tpath = out_dir / f'{p.name}.extracted.txt'
            tpath.write_text(text)
            snippets[p.name] = text[:3000]
        else:
            snippets[p.name] = f'[NO TEXT EXTRACTED] file type: {typ}'
    return snippets


def poc_lines(item):
    raw = item.get('pointOfContact') or []
    if isinstance(raw, dict): raw = [raw]
    lines=[]
    for p in raw:
        lines.append(f"- {p.get('type','poc')}: {p.get('fullName','[name missing]')} | {p.get('email','[email missing]')} | {p.get('phone','[phone missing]')}")
    return '\n'.join(lines) or '- [POC MISSING]'


def pop_label(item):
    pop=item.get('placeOfPerformance') or {}
    if isinstance(pop, dict):
        city=pop.get('city'); city=city.get('name') if isinstance(city,dict) else city
        st=pop.get('state'); st=st.get('code') if isinstance(st,dict) else st
        zipc=pop.get('zip') or ''
        return ', '.join(x for x in [str(city or ''), str(st or ''), str(zipc or '')] if x)
    return str(pop or '')


def naics_label(code):
    return {'238210':'Electrical contractors','562111':'Solid waste collection','561790':'Other building services / specialty cleaning'} .get(str(code), '')


def due_days(item):
    raw=item.get('responseDeadLine') or item.get('responseDeadline') or ''
    try:
        d=dt.datetime.fromisoformat(str(raw).replace('Z','+00:00')).date()
        return (d-dt.date.today()).days
    except Exception:
        return None


def geocode(query: str):
    key=ENV.get('GOOGLE_MAPS_API_KEY') or ENV.get('GOOGLE_PLACES_API_KEY')
    if not key: return None
    url='https://maps.googleapis.com/maps/api/geocode/json?' + urllib.parse.urlencode({'address': query, 'key': key})
    try:
        data=request_json(url, 20)
        if data.get('results'):
            loc=data['results'][0]['geometry']['location']
            return loc['lat'], loc['lng']
    except Exception:
        return None
    return None


def places_candidates(item: dict, folder: Path) -> list[dict[str, str]]:
    key=ENV.get('GOOGLE_PLACES_API_KEY') or ENV.get('GOOGLE_MAPS_API_KEY')
    if not key: return []
    title=item.get('title','')
    code=str(item.get('naicsCode') or '')
    place=pop_label(item)
    if code=='238210': terms=['electrical contractor','commercial electrician']
    elif code=='562111': terms=['commercial waste management','dumpster service','solid waste collection']
    elif code=='561790': terms=['commercial cleaning service','specialty cleaning','pressure washing']
    else: terms=['contractor']
    loc=geocode(place)
    candidates=[]
    seen=set()
    for term in terms:
        params={'query': f'{term} near {place}', 'key': key}
        if loc:
            params['location']=f'{loc[0]},{loc[1]}'; params['radius']='50000'
        url='https://maps.googleapis.com/maps/api/place/textsearch/json?' + urllib.parse.urlencode(params)
        try:
            data=request_json(url, 25)
        except Exception:
            continue
        for r in data.get('results', [])[:8]:
            pid=r.get('place_id')
            if not pid or pid in seen: continue
            seen.add(pid)
            candidates.append({
                'name': r.get('name',''),
                'address': r.get('formatted_address',''),
                'rating': str(r.get('rating','')),
                'user_ratings_total': str(r.get('user_ratings_total','')),
                'place_id': pid,
                'search_term': term,
                'status': '[SUBCONTRACTOR NOT VERIFIED]',
                'notes': 'Candidate from Google Places; no outreach sent.'
            })
            if len(candidates)>=12: break
        if len(candidates)>=12: break
    return candidates


def save_candidates_csv(cands, path: Path):
    fields=['name','address','rating','user_ratings_total','place_id','search_term','status','notes']
    with path.open('w', newline='') as f:
        w=csv.DictWriter(f, fieldnames=fields)
        w.writeheader(); w.writerows(cands)


def doc_manifest(downloads, snippets):
    lines=['# Attachment Manifest','']
    for d in downloads:
        p=Path(d.get('path') or '')
        lines.append(f"- Source: {d['url']}")
        lines.append(f"  - Status: {d['status']}")
        if p:
            lines.append(f"  - Local file: `{p}`")
            lines.append(f"  - Bytes: {d.get('bytes','')}")
            lines.append(f"  - Extraction: {snippets.get(p.name, '[not attempted]')[:300].replace(chr(10),' ')}")
        lines.append('')
    return '\n'.join(lines)


def scorecard(item, snippets):
    days=due_days(item)
    code=str(item.get('naicsCode') or '')
    value_score=8 if '23821' in code or code in ['561790','562111'] else 5
    sub_score=4
    procedure=7 if item.get('type')=='Combined Synopsis/Solicitation' else 5
    deadline=8 if days is not None and days>=14 else 4
    elig=8 if item.get('typeOfSetAside')=='SBA' else 5
    past=6
    geo=10
    wage=2
    comp=4
    total=round(value_score*2 + sub_score*1.5 + procedure*1.5 + deadline + elig + past + geo + wage + comp)
    rec='REVIEW'
    if total>=75: rec='BID-RECOMMENDED SUBJECT TO APPROVAL'
    elif total<50: rec='NO-BID RECOMMENDED'
    return total, rec, f"""# 01_BID_NO_BID_SCORECARD

## Recommendation

**Preliminary recommendation:** {rec}  
**Score:** {total}/100  
**Approval status:** `[USER INPUT REQUIRED]` — this is not approval to contact vendors, approve price, or submit anything.

## Scorecard

1. Value band fit (20): {value_score}/10 weighted = {value_score*2}/20. Evidence: `[ASSUMPTION — MUST BE CONFIRMED]` no verified magnitude unless documents state otherwise.
2. Sub coverage (15): {sub_score}/10 weighted = {sub_score*1.5}/15. Evidence: candidate list generated; subcontractors are `[SUBCONTRACTOR NOT VERIFIED]`.
3. Procedure complexity (15): {procedure}/10 weighted = {procedure*1.5}/15. Evidence: notice type `{item.get('type')}`.
4. Deadline runway (10): {deadline}/10. Evidence: due `{item.get('responseDeadLine')}`, approx {days} days from today.
5. Eligibility clean (10): {elig}/10. Evidence: set-aside `{item.get('typeOfSetAsideDescription')}`; WFG SAM/prime status still `[USER INPUT REQUIRED]`.
6. Past performance asked (10): {past}/10. Evidence: `[DOCUMENT REVIEW REQUIRED]` formal past-performance requirements not fully confirmed.
7. Geography (10): {geo}/10. Evidence: place of performance {pop_label(item)}.
8. Wage/labor clarity (5): {wage}/5. Evidence: `[DOCUMENT REVIEW REQUIRED]` wage determinations/labor clauses must be confirmed from attachments.
9. Competition signal (5): {comp}/5. Evidence: niche/service scope likely, but competition unknown.

## Hard no-bid screen

- Certification-gated set-aside: no; listed as `{item.get('typeOfSetAsideDescription')}`.
- Visible value over hard cap: `[ASSUMPTION — MUST BE CONFIRMED]` not found in SAM record.
- Bonding: `[DOCUMENT REVIEW REQUIRED]`.
- CUI/clearance: `[DOCUMENT REVIEW REQUIRED]`.
- Licensing: `[USER INPUT REQUIRED]` via subcontractor/prime compliance review.

## Next true authorization gate

External subcontractor outreach. Drafts are prepared separately, but no outreach has been sent.
"""


def make_common_files(folder: Path, item: dict, downloads, snippets, desc: str, candidates, drive_url: str | None):
    title=item.get('title','Untitled')
    days=due_days(item)
    pocs=poc_lines(item)
    docs='\n'.join(f"- `{Path(d['path']).name}` — {d['status']}" for d in downloads if d.get('path')) or '- [DOCUMENT MISSING] No resources downloaded.'
    folder.joinpath('source-link.txt').write_text(str(item.get('uiLink') or ''))
    folder.joinpath('raw-sam-record.json').write_text(json.dumps(item, indent=2))
    folder.joinpath('attachment_manifest.md').write_text(doc_manifest(downloads, snippets))
    folder.joinpath('opportunity_manifest.md').write_text(f"""# Opportunity Manifest

- Opportunity ID: {item.get('noticeId')}
- Solicitation number: {item.get('solicitationNumber')}
- Title: {title}
- Agency / office: {item.get('fullParentPathName')}
- Buyer contact:\n{pocs}
- Set-aside / eligibility: {item.get('typeOfSetAsideDescription')} / {item.get('typeOfSetAside')}
- NAICS: {item.get('naicsCode')} {naics_label(item.get('naicsCode'))}
- PSC: {item.get('classificationCode')}
- Place of performance: {pop_label(item)}
- Due date and timezone: {item.get('responseDeadLine')}
- Q&A deadline: [DOCUMENT MISSING]
- Submission method: [DOCUMENT MISSING]
- Current status: TEST INTAKE / DRAFTING ONLY
- Current owner subagent/skill: Marcus using wfg-opportunity-intake, wfg-bid-no-bid, wfg-solicitation-reader
- Human gate needed: external outreach approval, bid/no-bid approval, pricing approval, submission approval
- Source link: {item.get('uiLink')}
- Google Drive folder: {drive_url or '[PENDING UPLOAD]'}
- Documents:\n{docs}
- Scope summary: [DOCUMENT REVIEW REQUIRED] {desc[:500]}
- Key risks: missing verified magnitude; subcontractors not verified; no external communications sent.
- Next action: review approval packet before any subcontractor outreach.
""")
    folder.joinpath('00_INTAKE.md').write_text(f"""# 00_INTAKE

## Verified facts from SAM.gov record

- Title: {title}
- Notice ID: {item.get('noticeId')}
- Solicitation number: {item.get('solicitationNumber')}
- Notice type: {item.get('type')}
- Posted date: {item.get('postedDate')}
- Response deadline: {item.get('responseDeadLine')} ({days} days from today if parsed)
- Agency path: {item.get('fullParentPathName')}
- NAICS: {item.get('naicsCode')} {naics_label(item.get('naicsCode'))}
- PSC: {item.get('classificationCode')}
- Set-aside: {item.get('typeOfSetAsideDescription')}
- Place of performance: {pop_label(item)}
- SAM link: {item.get('uiLink')}

## Points of contact

{pocs}

## Downloaded solicitation resources

{docs}

## Description text

{desc or '[DOCUMENT MISSING] Description not available.'}

## Missing information

- [DOCUMENT MISSING] Confirm full solicitation instructions, all attachments, amendments, representations, and submission method.
- [USER INPUT REQUIRED] Confirm WFG prime registration/eligibility posture for this opportunity.
- [PRICE NOT APPROVED] No final price has been approved.
- [SUBCONTRACTOR NOT VERIFIED] No subcontractor has been contacted or validated.

## Notes

This is an internal draft intake only. No external communication, quote request, representation, price approval, or submission has occurred.
""")
    total, rec, score_text = scorecard(item, snippets)
    folder.joinpath('01_BID_NO_BID_SCORECARD.md').write_text(score_text)
    folder.joinpath('02_SOLICITATION_BRIEF.md').write_text(f"""# 02_SOLICITATION_BRIEF

## Summary

- Opportunity: {title}
- Solicitation: {item.get('solicitationNumber')}
- Agency: {item.get('fullParentPathName')}
- Work location: {pop_label(item)}
- Due: {item.get('responseDeadLine')}
- Set-aside: {item.get('typeOfSetAsideDescription')}

## Scope summary

[ASSUMPTION — MUST BE CONFIRMED] Based on title/NAICS/PSC and available description, the likely scope is {naics_label(item.get('naicsCode')) or title}. Full scope must be confirmed from downloaded solicitation documents.

## Extracted description

{desc or '[DOCUMENT MISSING]'}

## Key dates

- Proposal due date: {item.get('responseDeadLine')}
- Questions deadline: [DOCUMENT MISSING]
- Site visit: [DOCUMENT MISSING]
- Period of performance: [DOCUMENT MISSING]

## Submission instructions

[DOCUMENT MISSING] Must be extracted from solicitation attachments before submission.

## Contracting contacts

{pocs}

## Document extraction status

{docs}
""")
    folder.joinpath('03_COMPLIANCE_MATRIX.md').write_text(f"""# 03_COMPLIANCE_MATRIX

| Requirement | Source | Status | Owner | Notes |
|---|---|---|---|---|
| Confirm solicitation due date | SAM record | Draft | Marcus | {item.get('responseDeadLine')} |
| Confirm submission method | Solicitation docs | [DOCUMENT MISSING] | Solicitation Reader | Required before any submission |
| Confirm Q&A deadline | Solicitation docs | [DOCUMENT MISSING] | Solicitation Reader | Required before buyer questions |
| Confirm site visit requirement | Solicitation docs | [DOCUMENT MISSING] | Solicitation Reader | Mandatory site visit could drive no-bid |
| Confirm required forms | Solicitation docs | [DOCUMENT MISSING] | Proposal Compiler | See required-form checklist |
| Confirm wage determinations | Solicitation docs | [DOCUMENT REVIEW REQUIRED] | Pricing | Labor pricing cannot be final without this |
| Confirm bonding/insurance/license requirements | Solicitation docs/subcontractor validation | [DOCUMENT REVIEW REQUIRED] | Validator | No reliance before approval |
| Confirm set-aside eligibility | SAM + WFG profile | Draft | Marcus | {item.get('typeOfSetAsideDescription')} |
| Prepare subcontractor quote request | Internal draft | Draft | Marcus | Do not send without approval |
| Final bid price approval | Pricing package | [PRICE NOT APPROVED] | Authorized human | Binding gate |
| Final proposal submission | Proposal package | [USER INPUT REQUIRED] | Authorized human | Binding gate |
| Past performance claims | WFG records | [USER INPUT REQUIRED] | Proposal Compiler | Use verified info only |
""")
    folder.joinpath('04_MISSING_DOCUMENTS_AND_INFORMATION.md').write_text(f"""# Missing Documents and Information

- [DOCUMENT MISSING] Full solicitation instructions, including submission method.
- [DOCUMENT MISSING] Amendments, if any.
- [DOCUMENT MISSING] Wage determination / labor standards status.
- [DOCUMENT MISSING] Site visit requirement.
- [DOCUMENT MISSING] Questions deadline.
- [DOCUMENT MISSING] Evaluation factors.
- [DOCUMENT MISSING] Required representations/forms.
- [USER INPUT REQUIRED] WFG current SAM/UEI/CAGE/insurance/bonding readiness.
- [SUBCONTRACTOR NOT VERIFIED] Qualified subcontractor coverage for {pop_label(item)}.
- [PRICE NOT APPROVED] Final pricing, overhead, contingency, and profit.
""")
    trade=naics_label(item.get('naicsCode')) or item.get('title')
    folder.joinpath('05_SUBCONTRACTOR_SOURCING_CRITERIA.md').write_text(f"""# Subcontractor Sourcing Criteria

## Trade / service

{trade}

## Geography

Prioritize subcontractors within practical travel distance of {pop_label(item)}.

## Minimum criteria

- Licensed/qualified for the trade where applicable.
- Commercial/government site experience preferred.
- Can quote before {item.get('responseDeadLine')} with enough time for WFG review.
- Can provide labor, material, mobilization, exclusions, schedule, and assumptions.
- Can provide insurance/licensing documentation if selected.
- Not excluded/debarred. `[SUBCONTRACTOR NOT VERIFIED]`

## Disqualifiers / watch-outs

- Cannot meet schedule.
- Refuses government/commercial documentation.
- Requires WFG to make unsupported certification/experience claims.
- Cannot comply with wage, safety, site-access, or background requirements if present.
""")
    save_candidates_csv(candidates, folder/'06_SUBCONTRACTOR_CANDIDATES.csv')
    folder.joinpath('07_DRAFT_SUBCONTRACTOR_QUOTE_REQUEST.md').write_text(f"""# Draft Subcontractor Quote Request — DO NOT SEND WITHOUT APPROVAL

Subject: Quote request — {title} — {pop_label(item)}

Hello,

This is Marcus with Wright Foster Group LLC. We are evaluating a potential prime bid for a government contract opportunity involving {trade} at/near {pop_label(item)}.

Could your team review the attached scope documents and provide preliminary pricing and availability by [USER INPUT REQUIRED — quote deadline]?

Please include:
- Labor
- Materials/equipment
- Mobilization/travel
- Schedule/earliest availability
- Exclusions and assumptions
- Licensing/insurance status
- Any site-access constraints
- Whether your price includes all taxes/fees/surcharges

Important: this is a request for subcontractor pricing only. No award or commitment is made unless separately authorized in writing.

Thank you,
Marcus
Operations Assistant
Wright Foster Group LLC

[DO NOT SEND — EXTERNAL OUTREACH APPROVAL REQUIRED]
""")
    folder.joinpath('08_DRAFT_EMAILS_MESSAGES_CALL_SCRIPTS.md').write_text(f"""# Draft Outreach Emails / Messages / Call Script

## Email draft

See `07_DRAFT_SUBCONTRACTOR_QUOTE_REQUEST.md`.

## Short message draft

Hello, this is Marcus with Wright Foster Group LLC. We are pricing a government opportunity near {pop_label(item)} for {trade}. Are you open to reviewing the scope and providing subcontractor pricing this week?

[DO NOT SEND — EXTERNAL OUTREACH APPROVAL REQUIRED]

## Call script

1. Introduce Wright Foster Group LLC and disclose Marcus as AI operations assistant if asked.
2. Confirm the company handles {trade} near {pop_label(item)}.
3. Ask whether they can review government scope documents and quote by [USER INPUT REQUIRED].
4. Ask for best email for the quote package.
5. Do not promise award, exclusive status, or final terms.

[DO NOT CALL — EXTERNAL OUTREACH APPROVAL REQUIRED]
""")
    folder.joinpath('09_SUBCONTRACTOR_VALIDATION_CHECKLIST.md').write_text(f"""# Subcontractor Validation Checklist

For each candidate:

- Legal business name: [SUBCONTRACTOR NOT VERIFIED]
- Contact person/email/phone: [SUBCONTRACTOR NOT VERIFIED]
- Trade/license status: [SUBCONTRACTOR NOT VERIFIED]
- Insurance/COI: [SUBCONTRACTOR NOT VERIFIED]
- SAM/exclusion check where applicable: [SUBCONTRACTOR NOT VERIFIED]
- Relevant experience: [SUBCONTRACTOR NOT VERIFIED]
- Quote includes labor/materials/mobilization: [SUBCONTRACTOR NOT VERIFIED]
- Exclusions listed: [SUBCONTRACTOR NOT VERIFIED]
- Schedule/availability: [SUBCONTRACTOR NOT VERIFIED]
- Wage/labor compliance understanding: [SUBCONTRACTOR NOT VERIFIED]
- Site access/security readiness: [SUBCONTRACTOR NOT VERIFIED]
""")
    folder.joinpath('10_QUOTE_COMPARISON_AND_EXCLUSION_ANALYSIS.md').write_text(f"""# Quote Comparison and Exclusion Analysis

No subcontractor quotes have been received.

| Subcontractor | Price | Includes | Exclusions | Schedule | Risk | Status |
|---|---:|---|---|---|---|---|
| [SUBCONTRACTOR NOT VERIFIED] | [PRICE NOT APPROVED] | [USER INPUT REQUIRED] | [USER INPUT REQUIRED] | [USER INPUT REQUIRED] | [USER INPUT REQUIRED] | Pending outreach approval |

## Required analysis after quotes arrive

- Normalize scope inclusions.
- Identify exclusions that conflict with solicitation.
- Confirm mobilization/travel assumptions.
- Confirm wage/labor requirements.
- Confirm insurance/licensing.
- Compare lead times against due date and period of performance.
""")
    folder.joinpath('11_PRELIMINARY_PRICING_WORKSHEET.md').write_text(f"""# Preliminary Pricing Worksheet

Status: `[PRICE NOT APPROVED]`

## Known inputs

- Solicitation: {item.get('solicitationNumber')}
- Scope: {trade}
- Location: {pop_label(item)}
- Value/magnitude: [DOCUMENT MISSING]
- Subcontractor quotes: [SUBCONTRACTOR NOT VERIFIED]

## Suggested markup ranges for scenario planning only

- Overhead: 8%–15% `[ASSUMPTION — MUST BE CONFIRMED]`
- Contingency: 5%–15% depending on scope/document risk `[ASSUMPTION — MUST BE CONFIRMED]`
- Profit: 8%–15% starter target `[ASSUMPTION — MUST BE CONFIRMED]`

## Scenario table

| Scenario | Subtotal | OH | Contingency | Profit | Total | Status |
|---|---:|---:|---:|---:|---:|---|
| Low | [USER INPUT REQUIRED] | 8% | 5% | 8% | [PRICE NOT APPROVED] | Draft only |
| Base | [USER INPUT REQUIRED] | 12% | 10% | 10% | [PRICE NOT APPROVED] | Draft only |
| High-risk | [USER INPUT REQUIRED] | 15% | 15% | 15% | [PRICE NOT APPROVED] | Draft only |
""")
    folder.joinpath('12_LIMITATIONS_ON_SUBCONTRACTING_ANALYSIS.md').write_text(f"""# Limitations on Subcontracting Analysis

Status: preliminary only.

Set-aside: {item.get('typeOfSetAsideDescription')}
NAICS: {item.get('naicsCode')} {trade}

## Applicability

Because this appears to be a small-business set-aside, limitations-on-subcontracting and similarly situated entity rules may apply. Exact treatment depends on solicitation clauses, contract type, and whether the work is service/construction/supply.

## Current finding

[ASSUMPTION — MUST BE CONFIRMED] WFG cannot approve a final bid structure until subcontractor roles, scope split, and applicable clauses are confirmed.

## Required before pricing approval

- Identify prime-performed vs subcontracted work.
- Confirm similarly situated subcontractor status if relied upon.
- Confirm applicable FAR clause(s).
- Confirm whether material/supply exclusions apply.
""")
    proposal=folder/'proposal'; proposal.mkdir(exist_ok=True)
    proposal.joinpath('13_TECHNICAL_PROPOSAL_DRAFT.md').write_text(f"""# Technical Proposal Draft

## Cover / introduction

Wright Foster Group LLC is evaluating this opportunity as a prime contractor and would coordinate qualified subcontractor performance for {trade} at {pop_label(item)}.

## Technical approach

[ASSUMPTION — MUST BE CONFIRMED] WFG would assign a qualified subcontractor for field execution, with WFG managing schedule, quality-control coordination, communication, and documentation.

## Staffing / subcontractor plan

[SUBCONTRACTOR NOT VERIFIED] Specific subcontractor not selected. Candidate list is in `06_SUBCONTRACTOR_CANDIDATES.csv`.

## Schedule

[USER INPUT REQUIRED] Final schedule depends on solicitation period of performance and subcontractor availability.

## Quality control

Draft QC concepts:
- Verify scope and site requirements before work.
- Track completion against solicitation requirements.
- Require subcontractor photos/reports where applicable.
- Maintain issue log and closeout checklist.

## Compliance caveat

This draft contains assumptions and cannot be submitted until the solicitation instructions, pricing, forms, representations, and authorized-human approval are complete.
""")
    proposal.joinpath('14_PAST_PERFORMANCE_SECTION.md').write_text(f"""# Past Performance Section

Status: draft using verified information only.

Wright Foster Group LLC is in startup/buildout stage. Do not claim federal past performance, CPARS, certifications, bonding, licenses, insurance, or subcontractor commitments unless verified and approved.

## Available verified statements

- [USER INPUT REQUIRED] Confirm legal entity registration status.
- [USER INPUT REQUIRED] Confirm SAM/UEI/CAGE status.
- [USER INPUT REQUIRED] Confirm any relevant owner/member or subcontractor experience that may be truthfully cited.

## Placeholder language

[USER INPUT REQUIRED] Wright Foster Group LLC will coordinate qualified subcontractor performance and contract administration using documented quality-control and communication procedures.

Do not submit this section without attorney/procurement review if past performance is an evaluated factor.
""")
    folder.joinpath('15_REQUIRED_FORM_CHECKLIST.md').write_text(f"""# Required Form Checklist

- Solicitation/standard form: [DOCUMENT MISSING]
- Pricing schedule/CLIN form: [DOCUMENT MISSING]
- Representations/certifications: [DOCUMENT MISSING]
- Wage determination acknowledgment: [DOCUMENT REVIEW REQUIRED]
- Amendment acknowledgments: [DOCUMENT REVIEW REQUIRED]
- Subcontractor forms: [SUBCONTRACTOR NOT VERIFIED]
- Signature authority: [USER INPUT REQUIRED]
""")
    folder.joinpath('16_FINAL_COMPLIANCE_AUDIT.md').write_text(f"""# Final Compliance Audit

Status: NOT READY FOR SUBMISSION.

## Blocking items

- [DOCUMENT MISSING] Full solicitation instructions not fully extracted/verified.
- [PRICE NOT APPROVED] Final price missing.
- [SUBCONTRACTOR NOT VERIFIED] No subcontractor quote/credentials.
- [USER INPUT REQUIRED] No authorized-human approval for pursuit, outreach, pricing, or submission.

## Current audit result

NO-GO for submission. Internal drafting may continue; external actions require approval.
""")
    folder.joinpath('17_RED_TEAM_REVIEW.md').write_text(f"""# Red-Team Review

## Major risks

- Scope may be incomplete until all attachments are reviewed.
- No subcontractor has been verified.
- No final pricing exists.
- Wage/labor, bonding, insurance, site-access, and form requirements are not confirmed.
- Deadline pressure: due {item.get('responseDeadLine')}.

## Recommendation

Proceed only with controlled test-intake and possible subcontractor outreach after approval. Do not bid yet.
""")
    folder.joinpath('18_SUBMISSION_CHECKLIST.md').write_text(f"""# Submission Checklist

Status: NOT READY.

- [ ] Confirm final solicitation and amendments.
- [ ] Complete compliance matrix.
- [ ] Confirm required forms.
- [ ] Confirm final technical response.
- [ ] Confirm final price.
- [ ] Obtain authorized-human approval.
- [ ] Submit through approved method only.
- [ ] Save submission proof under `submission-proof/`.
""")
    folder.joinpath('19_DRAFT_SUBMISSION_EMAIL_AND_FOLLOWUP.md').write_text(f"""# Draft Submission Email and Follow-Up Messages

## Draft submission email — DO NOT SEND

Subject: Submission for {item.get('solicitationNumber')} — {title}

Dear Contracting Officer,

Please find attached Wright Foster Group LLC's response to solicitation {item.get('solicitationNumber')}.

[DOCUMENT MISSING — attach required proposal files]
[PRICE NOT APPROVED]
[USER INPUT REQUIRED — authorized signature/representations]

Respectfully,
[AUTHORIZED SENDER REQUIRED]
Wright Foster Group LLC

## Follow-up tracking message

Hello, this is Marcus with Wright Foster Group LLC. I am following up to confirm receipt of our submission for {item.get('solicitationNumber')}.

[DO NOT SEND — EXTERNAL COMMUNICATION APPROVAL REQUIRED]
""")
    approvals=folder/'approvals'; approvals.mkdir(exist_ok=True)
    approval_packet=approvals/'outreach-approval-packet.md'
    review_files=[
        '00_INTAKE.md','01_BID_NO_BID_SCORECARD.md','02_SOLICITATION_BRIEF.md','03_COMPLIANCE_MATRIX.md','06_SUBCONTRACTOR_CANDIDATES.csv','07_DRAFT_SUBCONTRACTOR_QUOTE_REQUEST.md','08_DRAFT_EMAILS_MESSAGES_CALL_SCRIPTS.md'
    ]
    approval_packet.write_text(f"""# APPROVAL NEEDED — {title}

Approval type: External subcontractor outreach
Opportunity folder: `{folder}`
Google Drive folder: {drive_url or '[PENDING UPLOAD]'}

## Review files

""" + '\n'.join(f'- `{folder/name}`' for name in review_files) + f"""

## What has already been drafted

- Intake
- Bid/no-bid scorecard
- Solicitation brief and compliance matrix drafts
- Missing information list
- Subcontractor sourcing criteria and candidate list
- Draft quote request, email/message, and call script
- Validation checklist
- Pricing/LOS/proposal/audit placeholders

## Still incomplete

- Full document review and exact submission instructions
- Final bid/no-bid authorization
- Subcontractor validation and quotes
- Final pricing
- Final proposal package

## Important risks

- Deadline: {item.get('responseDeadLine')}
- No final magnitude/price confirmed unless stated in documents.
- Subcontractors are not verified.
- No external communication has been sent.

## Assumptions requiring confirmation

- [ASSUMPTION — MUST BE CONFIRMED] WFG can prime this opportunity.
- [ASSUMPTION — MUST BE CONFIRMED] Qualified local subcontractors can quote in time.

## Exact action requiring authorization

Authorize Marcus/WFG to send the draft subcontractor quote request to selected candidate subcontractors for this opportunity.

## Exact item being approved

Draft in `{folder/'07_DRAFT_SUBCONTRACTOR_QUOTE_REQUEST.md'}` and supporting outreach scripts in `{folder/'08_DRAFT_EMAILS_MESSAGES_CALL_SCRIPTS.md'}`.

## Reply options

- APPROVE: send subcontractor quote requests for {item.get('solicitationNumber')}
- REVISE: [requested edits]
- HOLD
- REJECT / NO-BID
""")
    return total, rec, approval_packet


def post_approval(summary: str) -> None:
    # Use send_message by invoking Hermes? Simpler: write summary; final agent can relay if needed.
    pass


def main() -> int:
    records=find_records()
    missing=[x for x in TARGET_IDS if x not in records]
    if missing:
        print('Missing records: '+', '.join(missing), file=sys.stderr)
    drive=service('drive','v3')
    root=ensure_drive_folder(drive, 'Wright Foster Group')
    opp_parent=ensure_drive_folder(drive, 'Opportunities', root)
    test_parent=ensure_drive_folder(drive, f'Test Intake Run {TODAY}', opp_parent)
    summaries=[]
    for index,nid in enumerate(TARGET_IDS,1):
        rec=records[nid]; item=rec['item']; title=item.get('title','Untitled')
        folder=OPP_ROOT / f"{TODAY}-{slugify(item.get('solicitationNumber') or title, 20)}-{slugify(title, 45)}"
        for sub in ['solicitation-docs','extracted-text','scope_sheets','quotes','pricing','proposal','submission-proof','approvals','notes']:
            (folder/sub).mkdir(parents=True, exist_ok=True)
        write_note(folder/'notes',1,'intake-started',f'Started test intake for {title} ({nid}).')
        (folder/'source-archive-files.txt').write_text('\n'.join(rec.get('files',[])))
        desc=fetch_description(item)
        write_note(folder/'notes',2,'description-fetched',f'Description length: {len(desc)} characters.')
        downloads=download_resources(item, folder/'solicitation-docs')
        write_note(folder/'notes',3,'documents-downloaded',f"Attempted {len(item.get('resourceLinks') or [])} downloads; success {sum(1 for d in downloads if d['status']=='downloaded')}.")
        snippets=extract_docs(folder/'solicitation-docs', folder/'extracted-text')
        write_note(folder/'notes',4,'document-text-extracted',f'Extracted text/snippets for {len(snippets)} files.')
        candidates=places_candidates(item, folder)
        write_note(folder/'notes',5,'subcontractor-candidates-found',f'Generated {len(candidates)} public candidate records from Google Places; no outreach sent.')
        drive_folder_id=ensure_drive_folder(drive, folder.name, test_parent)
        drive_url=drive_link(drive, drive_folder_id)
        total, rec_status, approval_packet=make_common_files(folder,item,downloads,snippets,desc,candidates,drive_url)
        write_note(folder/'notes',6,'draft-artifacts-created',f'Created intake, scorecard, solicitation/compliance, sourcing, pricing/proposal/audit/submission drafts. Recommendation: {rec_status}, score {total}.')
        # copy central pending approval packet
        pending=PROJECT/'approvals/pending'/f"{folder.name}-outreach-approval.md"
        pending.write_text(approval_packet.read_text())
        write_note(folder/'notes',7,'approval-packet-created',f'Created outreach approval packet: {approval_packet}; copied to {pending}.')
        upload_tree(drive, folder, drive_folder_id)
        write_note(folder/'notes',8,'drive-upload-complete',f'Uploaded local opportunity folder to Drive folder {drive_url}.')
        # upload note 8 after writing it
        upload_tree(drive, folder, drive_folder_id)
        summaries.append({
            'index':index,'title':title,'folder':str(folder),'drive_url':drive_url,'score':total,'recommendation':rec_status,
            'downloads_success':sum(1 for d in downloads if d['status']=='downloaded'),'downloads_total':len(downloads),'candidates':len(candidates),
            'approval_packet':str(approval_packet),'pending_packet':str(pending),'notice_id':nid,'solicitation':item.get('solicitationNumber')
        })
        print(json.dumps(summaries[-1], indent=2))
    SUMMARY_PATH.parent.mkdir(parents=True, exist_ok=True)
    SUMMARY_PATH.write_text('# Test Intake Run Summary\n\n' + '\n\n'.join(
        f"## {s['index']}. {s['title']}\n\n- Solicitation: {s['solicitation']}\n- Notice ID: {s['notice_id']}\n- Local folder: `{s['folder']}`\n- Google Drive: {s['drive_url']}\n- Score/recommendation: {s['score']} / {s['recommendation']}\n- Downloads: {s['downloads_success']}/{s['downloads_total']}\n- Subcontractor candidates: {s['candidates']}\n- Approval packet: `{s['approval_packet']}`\n- Pending hub packet: `{s['pending_packet']}`\n" for s in summaries))
    print('\nSUMMARY_FILE '+str(SUMMARY_PATH))
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
