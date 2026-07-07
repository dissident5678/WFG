#!/usr/bin/env python3
from __future__ import annotations
import os
from pathlib import Path as _P
_BASE=_P(os.environ.get('WFG_PROJECT_DIR', str(_P(__file__).resolve().parents[1])))
os.environ.setdefault('WFG_PROJECT_DIR',str(_BASE))
os.environ.setdefault('WFG_ENV','test')
os.environ.setdefault('WFG_DB_PATH',str(_BASE/'state/test/wfg_workflow_test.sqlite3'))
os.environ.setdefault('WFG_STATE_DIR',str(_BASE/'state/test'))
os.environ.setdefault('WFG_BATCHES_DIR',str(_BASE/'test-artifacts/batches'))
os.environ.setdefault('WFG_ARCHIVE_DIR',str(_BASE/'test-artifacts/sam-api'))
os.environ.setdefault('WFG_OPP_ROOT',str(_BASE/'test-artifacts/opportunities'))
import csv, json, sqlite3, sys, tempfile, unittest
from pathlib import Path
PROJECT=Path(str(_BASE)); sys.path.insert(0,str(PROJECT/'scripts'))
import wfg_phase1, wfg_phase2, wfg_phase3

class Phase3Tests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  wfg_phase3.migrate(); cls.tmp=Path(tempfile.mkdtemp(prefix='wfg-phase3-'))
  cls.notice='PHASE3TEST001'; cls.sol='PHASE3-SOL-001'; cls.key='notice:'+cls.notice.lower()
  item={'noticeId':cls.notice,'solicitationNumber':cls.sol,'title':'Phase 3 Fixture Specialty Trade','fullParentPathName':'Synthetic Agency','naicsCode':'238210','classificationCode':'Z2AA','type':'Combined Synopsis/Solicitation','typeOfSetAside':'SBA','typeOfSetAsideDescription':'Total Small Business Set-Aside','responseDeadLine':'2026-08-01T12:00:00-04:00','uiLink':'https://sam.gov/opp/phase3','resourceLinks':[]}
  wfg_phase1.upsert_opportunity(item,cls.key,'phase3-fixture','pursue',85)
  with sqlite3.connect(wfg_phase1.DB_PATH) as c: c.execute('update opportunities set workflow_status="discovered" where dedupe_key=?',(cls.key,)); c.commit()
  # create minimal phase2 version/folder
  folder=wfg_phase2.opp_folder(item,cls.key); (folder/'drafts').mkdir(exist_ok=True); (folder/'attachment_manifest.md').write_text('manifest')
  for n in ['02_SOLICITATION_BRIEF.md','05_SCOPE_DECOMPOSITION.md','06_SUBCONTRACTOR_SOURCING_CRITERIA.md']: (folder/'drafts'/n).write_text('external safe solicitation facts only')
  with sqlite3.connect(wfg_phase1.DB_PATH) as c:
   c.execute('insert or replace into opportunity_version_manifests(version_hash,dedupe_key,created_at,folder,metadata_hash,attachment_hashes_json,summary_path,stale_drafts_json) values(?,?,?,?,?,?,?,?)',('phase3ver',cls.key,wfg_phase3.now(),str(folder),'mh','[]',str(folder/'versions/phase3ver.md'),'[]')); c.commit()
  cls.sub_verified=wfg_phase3.add_subcontractor('Synthetic Verified Electrical LLC','electrical','verified@example.invalid','verified')
  cls.sub_unverified=wfg_phase3.add_subcontractor('Synthetic Unverified Small LLC','electrical','unverified@example.invalid','unverified')
  with sqlite3.connect(wfg_phase1.DB_PATH) as c:
   c.execute('insert into opportunity_sse_status(dedupe_key,subcontractor_id,opportunity_version,status,basis,checked_at) values(?,?,?,?,?,?)',(cls.key,cls.sub_verified,'phase3ver','verified','synthetic fixture verified for this opportunity',wfg_phase3.now()))
   c.execute('insert into opportunity_sse_status(dedupe_key,subcontractor_id,opportunity_version,status,basis,checked_at) values(?,?,?,?,?,?)',(cls.key,cls.sub_unverified,'phase3ver','unverified','small business claim not verified as similarly situated',wfg_phase3.now()))
   c.commit()
  cls.quote=cls.tmp/'quote.csv'
  with cls.quote.open('w',newline='') as f:
   w=csv.DictWriter(f,fieldnames=['description','qty','unit_price','line_total','exclusion','alternate','taxes','bonding','lead_time','schedule','validity','wage_assumptions']); w.writeheader()
   w.writerow({'description':'labor','qty':'10','unit_price':'100','line_total':'1000','taxes':'0','bonding':'50','lead_time':'7 days','schedule':'2 weeks','validity':'30 days','wage_assumptions':'SCLS/DBA TBD'})
   w.writerow({'description':'materials','qty':'5','unit_price':'20','line_total':'100','exclusion':'permits by others'})

 def test_01_no_outreach_without_exact_approval_and_change_invalidates(self):
  pkg=wfg_phase3.package(self.notice,[self.sub_verified],due_back='2026-07-01')
  denied=wfg_phase3.send_outreach(pkg['package_id'],dry_run=True)
  self.assertFalse(denied['sent']); self.assertIn('approval',denied['reason'])
  bad=wfg_phase3.approve_outreach(pkg['package_id'],pkg['package_version'],'bad-hash')
  self.assertFalse(bad['approved'])
  ok=wfg_phase3.approve_outreach(pkg['package_id'],pkg['package_version'],pkg['package_hash'])
  self.assertTrue(ok['approved'])
  dry=wfg_phase3.send_outreach(pkg['package_id'],dry_run=True)
  self.assertFalse(dry['sent']); self.assertTrue(dry['dry_run'])
  pkg2=wfg_phase3.package(self.notice,[self.sub_verified],due_back='2026-07-02')
  self.assertNotEqual(pkg['package_hash'],pkg2['package_hash'])
  real=wfg_phase3.send_outreach(pkg2['package_id'],dry_run=True)
  self.assertFalse(real['sent'])

 def test_02_quote_and_pricing_reconcile(self):
  q=wfg_phase3.quote_intake(self.notice,self.sub_verified,self.quote)
  self.assertEqual(q['total'],'1100.00')
  self.assertEqual(q['math_inconsistencies'],[])
  pr=wfg_phase3.price(self.notice,[q['quote_version']],{'overhead_pct':'0.10','g_and_a_pct':'0.05','contingency_pct':'0.10','profit_pct':'0.10','taxes':'0','bonding':'50','assumptions':['synthetic scenario pending approval']})
  comps=pr['components']; calc=sum(wfg_phase3.D(comps[k]) for k in ['subtotal','overhead','g_and_a','contingency','profit','extras'])
  self.assertEqual(str(wfg_phase3.money(calc)),pr['total'])
  self.__class__.price_version=pr['pricing_version']

 def test_03_compliance_rules(self):
  service=wfg_phase3.compliance(self.notice,'service',wage_docs_present=False,subcontractor_ids=[self.sub_unverified],materials='100')
  self.assertFalse(service['compliant'])
  self.assertTrue(any('Materials exclusion not permitted' in b for b in service['blocking_flags']))
  self.assertTrue(any('unverified' in b for b in service['blocking_flags']))
  self.assertTrue(any('Wage determination' in b for b in service['blocking_flags']))
  const=wfg_phase3.compliance(self.notice,'special_trade',wage_docs_present=True,subcontractor_ids=[self.sub_verified],materials='100')
  self.assertTrue(any(c['rule_id']=='LOS-SPECIAL-TRADE' for c in const['checks']))
  self.assertFalse(any('Materials exclusion not permitted' in b for b in const['blocking_flags']))
  self.__class__.comp_version=const['run_version']

 def test_04_proposal_no_fabrication_and_no_auto_submission(self):
  prop=wfg_phase3.proposal(self.notice,getattr(self.__class__,'price_version',''),getattr(self.__class__,'comp_version',''))
  text=(Path(prop['folder'])/'past_performance.md').read_text()
  self.assertIn('No verified WFG past performance inserted',text)
  appr=wfg_phase3.approve_final(self.notice,prop['package_version'],prop['package_hash'])
  self.assertTrue(appr['approved'])
  no=wfg_phase3.record_submission(self.notice,None)
  self.assertFalse(no['submitted'])
  proof=self.tmp/'submission-proof.txt'; proof.write_text('synthetic human-entered proof')
  yes=wfg_phase3.record_submission(self.notice,proof)
  self.assertTrue(yes['submitted'])

if __name__=='__main__': unittest.main(verbosity=2)
