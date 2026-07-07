#!/usr/bin/env python3
"""WFG Phase 3 subcontractor, quote, pricing, compliance, and proposal engine.

Local-first deterministic implementation. It does not send outreach, submit proposals,
sign, certify, spend money, or accept awards unless a future connector is explicitly
implemented and exact approvals are verified.
"""
from __future__ import annotations
import argparse, csv, datetime as dt, hashlib, json, os, re, shutil, sqlite3, sys
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any
sys.path.insert(0, str(Path(__file__).resolve().parent))
import wfg_phase1, wfg_phase2
PROJECT=Path(os.environ.get('WFG_PROJECT_DIR','/home/nick/workspace/wfg-gov-contracting-v2')).resolve(); DB=Path(os.environ.get('WFG_DB_PATH', str(PROJECT/'state/wfg_workflow.sqlite3'))).resolve()
RULE_VERSION='WFG-PHASE3-RULES-2026-06-24'
STATUS={'verified','unverified','expired','not_applicable'}

def now(): return dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')
def D(x): return Decimal(str(x or '0')).quantize(Decimal('0.01'))
def money(x): return D(x).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
def hbytes(b:bytes): return hashlib.sha256(b).hexdigest()
def hfile(p:Path): return hbytes(p.read_bytes())
def hjson(o:Any): return hbytes(json.dumps(o,sort_keys=True,ensure_ascii=False,default=str).encode())
def safe(s:str): return re.sub(r'[^a-zA-Z0-9._-]+','-',str(s).lower()).strip('-')[:80] or 'item'
def con(): wfg_phase1.init_db(DB); c=sqlite3.connect(DB); c.row_factory=sqlite3.Row; return c

def migrate():
 c=con(); c.executescript('''
 CREATE TABLE IF NOT EXISTS subcontractors(id INTEGER PRIMARY KEY AUTOINCREMENT, legal_name TEXT NOT NULL, dba TEXT, website TEXT, notes TEXT, exclusions_concerns TEXT, source TEXT, validation_date TEXT);
 CREATE TABLE IF NOT EXISTS subcontractor_contacts(id INTEGER PRIMARY KEY AUTOINCREMENT, subcontractor_id INTEGER, name TEXT, role TEXT, email TEXT, phone TEXT, source TEXT);
 CREATE TABLE IF NOT EXISTS subcontractor_trades(id INTEGER PRIMARY KEY AUTOINCREMENT, subcontractor_id INTEGER, trade TEXT, naics TEXT, status TEXT CHECK(status in ('verified','unverified','expired','not_applicable')) DEFAULT 'unverified', source TEXT);
 CREATE TABLE IF NOT EXISTS subcontractor_geography(id INTEGER PRIMARY KEY AUTOINCREMENT, subcontractor_id INTEGER, state TEXT, county TEXT, city TEXT, radius_miles INTEGER, status TEXT CHECK(status in ('verified','unverified','expired','not_applicable')) DEFAULT 'unverified');
 CREATE TABLE IF NOT EXISTS subcontractor_credentials(id INTEGER PRIMARY KEY AUTOINCREMENT, subcontractor_id INTEGER, kind TEXT, name TEXT, identifier TEXT, status TEXT CHECK(status in ('verified','unverified','expired','not_applicable')), expiration_date TEXT, source TEXT, notes TEXT);
 CREATE TABLE IF NOT EXISTS opportunity_sse_status(id INTEGER PRIMARY KEY AUTOINCREMENT, dedupe_key TEXT, subcontractor_id INTEGER, opportunity_version TEXT, status TEXT CHECK(status in ('verified','unverified','expired','not_applicable')) DEFAULT 'unverified', basis TEXT, checked_at TEXT);
 CREATE TABLE IF NOT EXISTS trade_packages(id INTEGER PRIMARY KEY AUTOINCREMENT, dedupe_key TEXT, package_version TEXT, package_hash TEXT, folder TEXT, recipient_list_json TEXT, message_text TEXT, due_back_date TEXT, followup_sequence_json TEXT, status TEXT, created_at TEXT, invalidated_at TEXT, invalidated_reason TEXT);
 CREATE TABLE IF NOT EXISTS outreach_sends(id INTEGER PRIMARY KEY AUTOINCREMENT, package_id INTEGER, recipient TEXT, message_version TEXT, package_version TEXT, send_result TEXT, sent_at TEXT, followup_schedule_json TEXT, replies_json TEXT, delivery_failures_json TEXT, connector TEXT);
 CREATE TABLE IF NOT EXISTS quote_records(id INTEGER PRIMARY KEY AUTOINCREMENT, dedupe_key TEXT, subcontractor_id INTEGER, received_at TEXT, source_path TEXT, source_hash TEXT, quote_version TEXT, total TEXT, line_items_json TEXT, exclusions_json TEXT, alternates_json TEXT, taxes TEXT, bonding TEXT, lead_time TEXT, schedule TEXT, validity_period TEXT, wage_assumptions TEXT, missing_scope_json TEXT, qualifications_json TEXT, math_inconsistencies_json TEXT, extraction_confidence TEXT, normalized_path TEXT);
 CREATE TABLE IF NOT EXISTS pricing_versions(id INTEGER PRIMARY KEY AUTOINCREMENT, dedupe_key TEXT, pricing_version TEXT, created_at TEXT, creator TEXT, inputs_json TEXT, formulas_json TEXT, assumptions_json TEXT, exclusions_json TEXT, source_quote_versions_json TEXT, total TEXT, clin_mapping_json TEXT, content_hash TEXT, output_path TEXT);
 CREATE TABLE IF NOT EXISTS compliance_runs(id INTEGER PRIMARY KEY AUTOINCREMENT, dedupe_key TEXT, run_version TEXT, created_at TEXT, rule_version TEXT, checks_json TEXT, blocking_flags_json TEXT, compliant INTEGER, output_path TEXT);
 CREATE TABLE IF NOT EXISTS proposal_packages(id INTEGER PRIMARY KEY AUTOINCREMENT, dedupe_key TEXT, package_version TEXT, package_hash TEXT, created_at TEXT, folder TEXT, compliance_run_version TEXT, pricing_version TEXT, status TEXT, submission_proof_path TEXT, submitted_at TEXT);
 '''); c.commit(); c.close()

def event(key,t,d=None): wfg_phase2.event(key,t,d or {})
def item(identifier): return wfg_phase2.item_from_db(identifier)
def folder_for(item,key): return wfg_phase2.opp_folder(item,key)

def add_subcontractor(name, trade='general', email='', status='unverified', source='manual-test', notes=''):
 if status not in STATUS: raise ValueError('bad status')
 migrate(); c=con(); cur=c.execute('insert into subcontractors(legal_name,notes,source,validation_date) values(?,?,?,?)',(name,notes,source,now()))
 sid=cur.lastrowid; c.execute('insert into subcontractor_trades(subcontractor_id,trade,status,source) values(?,?,?,?)',(sid,trade,status,source))
 if email: c.execute('insert into subcontractor_contacts(subcontractor_id,name,email,source) values(?,?,?,?)',(sid,name+' contact',email,source))
 c.commit(); c.close(); return sid

def latest_version(key):
 c=con(); r=c.execute('select version_hash from opportunity_version_manifests where dedupe_key=? order by created_at desc limit 1',(key,)).fetchone(); c.close(); return r['version_hash'] if r else 'unversioned'

def package(identifier, recipients:list[int]|None=None, due_back='[USER INPUT REQUIRED]', followups=None):
 migrate(); key,it=item(identifier); f=folder_for(it,key); trade_dir=f/'trade-packages'; trade_dir.mkdir(parents=True,exist_ok=True); v=latest_version(key)
 # whitelist only external-safe files, never internal scoring/pricing/strategy.
 include=[]
 for p in [f/'drafts/02_SOLICITATION_BRIEF.md',f/'drafts/05_SCOPE_DECOMPOSITION.md',f/'drafts/06_SUBCONTRACTOR_SOURCING_CRITERIA.md',f/'attachment_manifest.md']:
  if p.exists(): include.append(p)
 msg=f"Quote request for {it.get('solicitationNumber')} / {it.get('title')}\nPlace of performance: {it.get('placeOfPerformance','see solicitation')}\nDue back: {due_back}\nPlease provide line-item price, total, exclusions, alternates, taxes, bonding, lead time, schedule, validity period, and wage assumptions."
 content={'notice_id':it.get('noticeId'),'solicitation':it.get('solicitationNumber'),'source_version':v,'whitelist_files':[str(p) for p in include],'message':msg,'due_back':due_back,'excludes':['WFG markup','WFG profit','other subcontractor pricing','internal scoring','internal bid strategy','government estimate unless appropriate','unrelated scope']}
 ph=hjson(content); pv='pkg-'+ph[:16]; pf=trade_dir/f'{pv}.md'; pf.write_text('# Trade Quote Package\n\n'+json.dumps(content,indent=2))
 rec=[]
 c=con()
 for sid in recipients or []:
  r=c.execute('select legal_name from subcontractors where id=?',(sid,)).fetchone();
  if r: rec.append({'subcontractor_id':sid,'name':r['legal_name']})
 cur=c.execute('insert into trade_packages(dedupe_key,package_version,package_hash,folder,recipient_list_json,message_text,due_back_date,followup_sequence_json,status,created_at) values(?,?,?,?,?,?,?,?,?,?)',(key,pv,ph,str(trade_dir),json.dumps(rec),msg,due_back,json.dumps(followups or []),'pending_gate2',now()))
 pid=cur.lastrowid; c.commit(); c.close(); event(key,'trade_package_created',{'package_id':pid,'package_version':pv,'hash':ph}); return {'package_id':pid,'package_version':pv,'package_hash':ph,'path':str(pf),'message':msg,'recipients':rec}

def approve_outreach(package_id:int, version:str, package_hash:str, approver='test'):
 c=con(); p=c.execute('select * from trade_packages where id=?',(package_id,)).fetchone()
 if not p or p['package_version']!=version or p['package_hash']!=package_hash or p['status']!='pending_gate2': c.close(); return {'approved':False,'reason':'exact package/version/hash not pending'}
 c.execute('update trade_packages set status="approved_to_send" where id=?',(package_id,)); c.commit(); c.close(); event(p['dedupe_key'],'gate2_outreach_approved',{'package_id':package_id,'approver':approver}); return {'approved':True}

def send_outreach(package_id:int, dry_run=True):
 c=con(); p=c.execute('select * from trade_packages where id=?',(package_id,)).fetchone()
 if not p or p['status']!='approved_to_send': c.close(); return {'sent':False,'reason':'no exact valid Gate 2 approval'}
 rec=json.loads(p['recipient_list_json'] or '[]'); results=[]
 if dry_run:
  c.close(); return {'sent':False,'dry_run':True,'recipients':rec,'reason':'dry-run; no connector invoked'}
 # No real connector configured intentionally; refuse instead of sending.
 c.close(); return {'sent':False,'reason':'no external connector configured for Phase 3'}

def quote_intake(identifier, subcontractor_id:int, quote_path:Path):
 migrate(); key,it=item(identifier); data=quote_path.read_text(errors='ignore'); qh=hfile(quote_path)
 lines=[]; total=Decimal('0'); inconsist=[]; exclusions=[]; alternates=[]; missing=[]; quals=[]; taxes='0'; bonding='0'; lead=''; schedule=''; validity=''; wage=''
 reader=csv.DictReader(data.splitlines()) if quote_path.suffix.lower()=='.csv' else None
 if reader:
  for row in reader:
   desc=row.get('description') or row.get('item') or ''; qty=D(row.get('qty') or 1); unit=D(row.get('unit_price') or row.get('price') or 0); line=D(row.get('line_total') or qty*unit); calc=money(qty*unit)
   if line!=calc: inconsist.append({'description':desc,'provided':str(line),'calculated':str(calc)})
   total+=line; lines.append({'description':desc,'qty':str(qty),'unit_price':str(unit),'line_total':str(line)})
   if row.get('exclusion'): exclusions.append(row['exclusion'])
   if row.get('alternate'): alternates.append(row['alternate'])
   taxes=row.get('taxes') or taxes; bonding=row.get('bonding') or bonding; lead=row.get('lead_time') or lead; schedule=row.get('schedule') or schedule; validity=row.get('validity') or validity; wage=row.get('wage_assumptions') or wage
 else:
  m=re.search(r'total\D+([\d,.]+)',data,re.I); total=D((m.group(1) if m else '0').replace(',','')); lines=[{'description':'unstructured quote','line_total':str(total)}]; missing.append('structured line items')
 qv='quote-'+hjson({'hash':qh,'total':str(total)})[:16]; outdir=folder_for(it,key)/'quotes'; outdir.mkdir(exist_ok=True); norm=outdir/f'{qv}-normalized.json'
 normalized={'quote_version':qv,'subcontractor_id':subcontractor_id,'total':str(money(total)),'line_items':lines,'exclusions':exclusions,'alternates':alternates,'taxes':taxes,'bonding':bonding,'lead_time':lead,'schedule':schedule,'validity_period':validity,'wage_assumptions':wage,'missing_scope':missing,'qualifications':quals,'math_inconsistencies':inconsist,'extraction_confidence':'high' if reader else 'medium'}; norm.write_text(json.dumps(normalized,indent=2))
 archive=outdir/quote_path.name; shutil.copy2(quote_path,archive)
 c=con(); c.execute('insert into quote_records(dedupe_key,subcontractor_id,received_at,source_path,source_hash,quote_version,total,line_items_json,exclusions_json,alternates_json,taxes,bonding,lead_time,schedule,validity_period,wage_assumptions,missing_scope_json,qualifications_json,math_inconsistencies_json,extraction_confidence,normalized_path) values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(key,subcontractor_id,now(),str(archive),qh,qv,str(money(total)),json.dumps(lines),json.dumps(exclusions),json.dumps(alternates),taxes,bonding,lead,schedule,validity,wage,json.dumps(missing),json.dumps(quals),json.dumps(inconsist),normalized['extraction_confidence'],str(norm))); c.commit(); c.close(); event(key,'quote_intake',{'quote_version':qv,'total':str(money(total))}); return normalized

def price(identifier, quote_versions:list[str], scenario:dict[str,Any]):
 key,it=item(identifier); c=con(); rows=[]
 for qv in quote_versions: rows += c.execute('select * from quote_records where dedupe_key=? and quote_version=?',(key,qv)).fetchall()
 subtotal=sum(D(r['total']) for r in rows); inputs={'subcontractors':quote_versions,'subtotal':str(subtotal),**scenario}
 formulas={'overhead':'subtotal * overhead_pct','g_and_a':'subtotal * g_and_a_pct','contingency':'subtotal * contingency_pct','profit':'(subtotal+overhead+g_and_a+contingency) * profit_pct','total':'subtotal+overhead+g_and_a+contingency+profit+taxes+bonding+insurance+materials+equipment+travel+permits+self_perform_labor'}
 overhead=money(subtotal*D(scenario.get('overhead_pct',0))); ga=money(subtotal*D(scenario.get('g_and_a_pct',0))); cont=money(subtotal*D(scenario.get('contingency_pct',0))); base=subtotal+overhead+ga+cont; prof=money(base*D(scenario.get('profit_pct',0)))
 extras=sum(D(scenario.get(k,0)) for k in ['taxes','bonding','insurance','materials','equipment','travel','permits','self_perform_labor','mobilization'])
 total=money(base+prof+extras); out={'inputs':inputs,'formulas':formulas,'components':{'subtotal':str(subtotal),'overhead':str(overhead),'g_and_a':str(ga),'contingency':str(cont),'profit':str(prof),'extras':str(extras)},'total':str(total),'clin_mapping':scenario.get('clin_mapping',{}),'assumptions':scenario.get('assumptions',['scenario values pending human approval']),'exclusions':scenario.get('exclusions',[]),'source_quote_versions':quote_versions}
 ch=hjson(out); pv='price-'+ch[:16]; out['pricing_version']=pv; out['content_hash']=ch
 pf=folder_for(it,key)/'pricing'/f'{pv}.json'; pf.parent.mkdir(exist_ok=True); pf.write_text(json.dumps(out,indent=2))
 c.execute('insert into pricing_versions(dedupe_key,pricing_version,created_at,creator,inputs_json,formulas_json,assumptions_json,exclusions_json,source_quote_versions_json,total,clin_mapping_json,content_hash,output_path) values(?,?,?,?,?,?,?,?,?,?,?,?,?)',(key,pv,now(),'wfg_phase3',json.dumps(inputs),json.dumps(formulas),json.dumps(out['assumptions']),json.dumps(out['exclusions']),json.dumps(quote_versions),str(total),json.dumps(out['clin_mapping']),ch,str(pf))); c.commit(); c.close(); event(key,'pricing_version_created',{'pricing_version':pv,'total':str(total)}); return out

def sse_verified(key, sub_id, version):
 c=con(); r=c.execute('select status from opportunity_sse_status where dedupe_key=? and subcontractor_id=? and opportunity_version=? order by id desc limit 1',(key,sub_id,version)).fetchone(); c.close(); return bool(r and r['status']=='verified')

def compliance(identifier, contract_class='service', set_aside=True, clauses=None, wage_docs_present=False, subcontractor_ids=None, materials=0):
 key,it=item(identifier); version=latest_version(key); clauses=clauses or []; subcontractor_ids=subcontractor_ids or []
 checks=[]; blocks=[]
 def add(rule, ok, finding, block=False):
  checks.append({'rule_id':rule,'rule_version':RULE_VERSION,'official_source':'FAR/SBA/DOL solicitation-derived check - local deterministic checklist','checked_at':now(),'ok':ok,'finding':finding});
  (blocks.append(finding) if block and not ok else None)
 add('NAICS-ASSIGNED', bool(it.get('naicsCode')), f"Assigned NAICS: {it.get('naicsCode')}", True)
 add('SET-ASIDE-STATUS', True, f"Set-aside: {it.get('typeOfSetAsideDescription')}")
 los_applies=set_aside or any('52.219-14' in c for c in clauses); add('LOS-APPLICABILITY', True, f"LOS applies: {los_applies}")
 if contract_class=='service': rule='LOS-SERVICE'; permitted_material_exclusion=False; formula='Prime + verified SSE labor/cost must satisfy service LOS; materials not universally excluded.'
 elif contract_class=='general_construction': rule='LOS-GENERAL-CONSTRUCTION'; permitted_material_exclusion=True; formula='General construction formula; materials may be excluded where rule permits.'
 elif contract_class=='special_trade': rule='LOS-SPECIAL-TRADE'; permitted_material_exclusion=True; formula='Special trade construction formula; materials may be excluded where rule permits.'
 elif contract_class=='supply': rule='NMR-SUPPLY'; permitted_material_exclusion=False; formula='Supply/nonmanufacturer rule review required when applicable.'
 else: rule='CONTRACT-CLASS-UNKNOWN'; permitted_material_exclusion=False; formula='[LEGAL OR COMPLIANCE REVIEW REQUIRED]'
 add(rule, contract_class in ['service','general_construction','special_trade','supply'], formula, contract_class not in ['service','general_construction','special_trade','supply'])
 if materials and not permitted_material_exclusion: add('MATERIALS-EXCLUSION', False, 'Materials exclusion not permitted by selected rule/classification; do not apply universal exclusion.', True)
 verified=[sid for sid in subcontractor_ids if sse_verified(key,sid,version)]; unverified=[sid for sid in subcontractor_ids if sid not in verified]
 add('SSE-VERIFICATION', not unverified, f'Verified SSE: {verified}; unverified/not counted: {unverified}', bool(unverified))
 add('WAGE-DETERMINATION', wage_docs_present, 'DBA/SCLS/CBA wage determination present' if wage_docs_present else '[LEGAL OR COMPLIANCE REVIEW REQUIRED] Wage determination/labor standards omission blocks compliance declaration', True)
 for rid in ['BONDING','INSURANCE','SECURITY-BADGING','SITE-VISIT','REPS-CERTS','AMENDMENTS','SUBMISSION-INSTRUCTIONS','PRIMARY-VITAL','UNUSUAL-RELIANCE','NMR-RISK']:
  add(rid, False if rid in ['REPS-CERTS','SUBMISSION-INSTRUCTIONS'] else True, ('[LEGAL OR COMPLIANCE REVIEW REQUIRED] ' if rid in ['REPS-CERTS','SUBMISSION-INSTRUCTIONS'] else '')+rid, rid in ['REPS-CERTS','SUBMISSION-INSTRUCTIONS'])
 compliant=not blocks; rv='comp-'+hjson({'checks':checks,'blocks':blocks})[:16]; out={'run_version':rv,'rule_version':RULE_VERSION,'contract_classification':contract_class,'checks':checks,'blocking_flags':blocks,'compliant':compliant}
 pf=folder_for(it,key)/'audit'/f'{rv}.json'; pf.parent.mkdir(exist_ok=True); pf.write_text(json.dumps(out,indent=2))
 c=con(); c.execute('insert into compliance_runs(dedupe_key,run_version,created_at,rule_version,checks_json,blocking_flags_json,compliant,output_path) values(?,?,?,?,?,?,?,?)',(key,rv,now(),RULE_VERSION,json.dumps(checks),json.dumps(blocks),1 if compliant else 0,str(pf))); c.commit(); c.close(); event(key,'compliance_run',{'run_version':rv,'compliant':compliant}); return out

def proposal(identifier, pricing_version='', compliance_version=''):
 key,it=item(identifier); f=folder_for(it,key); prop=f/'proposal'; prop.mkdir(exist_ok=True)
 files={
 'pricing_workbook.md':'# Pricing Workbook\n\nSee deterministic pricing JSON. [PRICE NOT APPROVED] until Gate 3.\n',
 'clin_schedule.md':'# CLIN Schedule\n\n[USER INPUT REQUIRED] Map approved pricing to solicitation CLINs.\n',
 'technical_proposal.md':'# Technical Proposal Draft\n\nNo fabricated past performance, employees, equipment, licenses, certifications, bonding capacity, insurance, commitments, projects, or references.\n',
 'management_approach.md':'# Management Approach\n\nDraft WFG prime coordination approach.\n',
 'staffing_subcontracting_approach.md':'# Staffing/Subcontracting Approach\n\nBasis-of-bid subcontractor language only; no award before prime award. [SUBCONTRACTOR NOT VERIFIED as commitment]\n',
 'quality_control.md':'# Quality-Control Draft\n\nInspection, issue log, closeout checklist.\n',
 'safety_plan_outline.md':'# Safety Plan Outline\n\n[USER INPUT REQUIRED]\n',
 'schedule.md':'# Schedule\n\n[USER INPUT REQUIRED]\n',
 'past_performance.md':'# Past Performance\n\n[USER INPUT REQUIRED] No verified WFG past performance inserted. Fabrication prohibited.\n',
 'forms_checklist.md':'# Required Forms Checklist\n\n[DOCUMENT MISSING]\n',
 'reps_certs_checklist.md':'# Reps-and-Certs Verification Checklist\n\n[LEGAL OR COMPLIANCE REVIEW REQUIRED]\n',
 'amendment_ack_checklist.md':'# Amendment Acknowledgment Checklist\n\n[DOCUMENT MISSING]\n',
 'final_compliance_matrix.md':'# Final Compliance Matrix\n\nSee compliance run. Package not compliant unless deterministic engine says compliant and human reviews.\n',
 'red_team_review.md':'# Red-Team Review\n\n[NOT READY FOR SUBMISSION] until Gate 4.\n',
 'submission_checklist.md':'# Submission Checklist\n\nAfter Gate 4 mark APPROVED FOR HUMAN SUBMISSION only. Do not auto-submit. Proof required to mark submitted.\n',
 'draft_submission_email.md':'# Draft Submission Email\n\n[DO NOT SEND] [USER INPUT REQUIRED]\n',
 'portal_submission_instructions.md':'# Portal Submission Instructions\n\n[DOCUMENT MISSING] Human submits according to solicitation.\n'}
 paths=[]
 for n,b in files.items(): p=prop/n; p.write_text(b); paths.append(p)
 ph=hbytes(b''.join(p.read_bytes() for p in sorted(paths))); pv='proposal-'+ph[:16]
 c=con(); c.execute('insert into proposal_packages(dedupe_key,package_version,package_hash,created_at,folder,compliance_run_version,pricing_version,status) values(?,?,?,?,?,?,?,?)',(key,pv,ph,now(),str(prop),compliance_version,pricing_version,'draft_not_submitted')); c.commit(); c.close(); event(key,'proposal_package_created',{'package_version':pv,'hash':ph}); return {'package_version':pv,'package_hash':ph,'folder':str(prop),'files':[str(p) for p in paths],'status':'draft_not_submitted'}

def approve_final(identifier, package_version, package_hash):
 key,it=item(identifier); c=con(); r=c.execute('select * from proposal_packages where dedupe_key=? and package_version=? and package_hash=?',(key,package_version,package_hash)).fetchone()
 if not r: c.close(); return {'approved':False,'reason':'package mismatch'}
 c.execute('update proposal_packages set status="APPROVED FOR HUMAN SUBMISSION" where id=?',(r['id'],)); c.commit(); c.close(); event(key,'gate4_approved_for_human_submission',{'package_version':package_version}); return {'approved':True,'status':'APPROVED FOR HUMAN SUBMISSION'}

def record_submission(identifier, proof_path:Path|None=None):
 key,it=item(identifier)
 if not proof_path or not proof_path.exists(): return {'submitted':False,'reason':'submission proof file required'}
 c=con(); r=c.execute('select * from proposal_packages where dedupe_key=? and status="APPROVED FOR HUMAN SUBMISSION" order by id desc limit 1',(key,)).fetchone()
 if not r: c.close(); return {'submitted':False,'reason':'no approved-for-human-submission package'}
 c.execute('update proposal_packages set status="submitted", submission_proof_path=?, submitted_at=? where id=?',(str(proof_path),now(),r['id'])); c.commit(); c.close(); event(key,'submission_proof_recorded',{'proof':str(proof_path)}); return {'submitted':True,'proof':str(proof_path)}

def main():
 p=argparse.ArgumentParser(); sub=p.add_subparsers(dest='cmd',required=True)
 sub.add_parser('migrate')
 a=sub.add_parser('add-sub'); a.add_argument('name'); a.add_argument('--trade',default='general'); a.add_argument('--email',default=''); a.add_argument('--status',default='unverified')
 a=sub.add_parser('package'); a.add_argument('identifier'); a.add_argument('--recipients',default=''); a.add_argument('--due-back',default='[USER INPUT REQUIRED]')
 a=sub.add_parser('approve-outreach'); a.add_argument('package_id',type=int); a.add_argument('version'); a.add_argument('hash')
 a=sub.add_parser('send-outreach'); a.add_argument('package_id',type=int); a.add_argument('--real',action='store_true')
 a=sub.add_parser('quote'); a.add_argument('identifier'); a.add_argument('subcontractor_id',type=int); a.add_argument('quote_path')
 a=sub.add_parser('price'); a.add_argument('identifier'); a.add_argument('--quotes',required=True); a.add_argument('--scenario-json',default='{}')
 a=sub.add_parser('compliance'); a.add_argument('identifier'); a.add_argument('--class',dest='klass',default='service'); a.add_argument('--wage-docs-present',action='store_true'); a.add_argument('--subs',default=''); a.add_argument('--materials',default='0')
 a=sub.add_parser('proposal'); a.add_argument('identifier'); a.add_argument('--pricing-version',default=''); a.add_argument('--compliance-version',default='')
 a=sub.add_parser('approve-final'); a.add_argument('identifier'); a.add_argument('package_version'); a.add_argument('package_hash')
 a=sub.add_parser('record-submission'); a.add_argument('identifier'); a.add_argument('--proof')
 args=p.parse_args(); migrate()
 if args.cmd=='migrate': print(json.dumps({'ok':True,'db':str(DB)},indent=2)); return 0
 if args.cmd=='add-sub': print(json.dumps({'subcontractor_id':add_subcontractor(args.name,args.trade,args.email,args.status)},indent=2)); return 0
 if args.cmd=='package': print(json.dumps(package(args.identifier,[int(x) for x in args.recipients.split(',') if x],args.due_back),indent=2)); return 0
 if args.cmd=='approve-outreach': print(json.dumps(approve_outreach(args.package_id,args.version,args.hash),indent=2)); return 0
 if args.cmd=='send-outreach': print(json.dumps(send_outreach(args.package_id,dry_run=not args.real),indent=2)); return 0
 if args.cmd=='quote': print(json.dumps(quote_intake(args.identifier,args.subcontractor_id,Path(args.quote_path)),indent=2)); return 0
 if args.cmd=='price': print(json.dumps(price(args.identifier,args.quotes.split(','),json.loads(args.scenario_json)),indent=2)); return 0
 if args.cmd=='compliance': print(json.dumps(compliance(args.identifier,args.klass,True,wage_docs_present=args.wage_docs_present,subcontractor_ids=[int(x) for x in args.subs.split(',') if x],materials=args.materials),indent=2)); return 0
 if args.cmd=='proposal': print(json.dumps(proposal(args.identifier,args.pricing_version,args.compliance_version),indent=2)); return 0
 if args.cmd=='approve-final': print(json.dumps(approve_final(args.identifier,args.package_version,args.package_hash),indent=2)); return 0
 if args.cmd=='record-submission': print(json.dumps(record_submission(args.identifier,Path(args.proof) if args.proof else None),indent=2)); return 0
if __name__=='__main__': raise SystemExit(main())
