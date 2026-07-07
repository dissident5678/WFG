#!/usr/bin/env python3
from __future__ import annotations
import os, sqlite3, unittest
from pathlib import Path
BASE=Path('/home/nick/workspace/gov-contracting')
os.environ.setdefault('WFG_ENV','test')
os.environ.setdefault('WFG_DB_PATH',str(BASE/'state/test/wfg_workflow_test.sqlite3'))
os.environ.setdefault('WFG_STATE_DIR',str(BASE/'state/test'))
os.environ.setdefault('WFG_BATCHES_DIR',str(BASE/'test-artifacts/batches'))
os.environ.setdefault('WFG_ARCHIVE_DIR',str(BASE/'test-artifacts/sam-api'))
os.environ.setdefault('WFG_OPP_ROOT',str(BASE/'test-artifacts/opportunities'))
import sys; sys.path.insert(0,str(BASE/'scripts'))
import wfg_phase1, wfg_phase2, wfg_phase3

class Phase4HardeningTests(unittest.TestCase):
    def test_01_refuses_production_db_for_tests(self):
        self.assertIn('/state/test/', str(wfg_phase1.DB_PATH))
        self.assertNotEqual(str(wfg_phase1.DB_PATH), str(BASE/'state/wfg_workflow.sqlite3'))
    def test_02_test_artifacts_not_production_paths(self):
        bid,bdir,_=wfg_phase1.create_batch()
        self.assertIn('/test-artifacts/batches/', str(bdir))
        item={'noticeId':'PHASE4TEST001','solicitationNumber':'P4','title':'Phase 4 Test Fixture','resourceLinks':[]}
        key='notice:phase4test001'; wfg_phase1.upsert_opportunity(item,key,bid,'test',1)
        f=wfg_phase2.opp_folder(item,key)
        self.assertIn('/test-artifacts/opportunities/', str(f))
    def test_03_canonical_approval_ids_single_use_and_ambiguous_rejected(self):
        item={'noticeId':'PHASE4APPROVAL001','solicitationNumber':'P4A','title':'Phase 4 Approval Fixture','resourceLinks':[],'responseDeadLine':'2026-08-01T12:00:00-04:00'}
        key='notice:phase4approval001'; wfg_phase1.upsert_opportunity(item,key,'phase4','test',1)
        with sqlite3.connect(wfg_phase1.DB_PATH) as c: c.execute('update opportunities set workflow_status="discovered" where dedupe_key=?',(key,)); c.commit()
        data=wfg_phase2.intake('PHASE4APPROVAL001', fixture_no_network=True)
        with sqlite3.connect(wfg_phase1.DB_PATH) as c:
            c.row_factory=sqlite3.Row; r=c.execute('select approval_id,artifact_hash from approvals where dedupe_key=? order by id desc limit 1',(key,)).fetchone()
        self.assertTrue(r['approval_id'].startswith('appr_'))
        bad=wfg_phase2.record_approval_command('looks good',approver='Test')
        self.assertFalse(bad['accepted'])
        ok=wfg_phase2.record_approval_command('APPROVE '+r['approval_id'],approver='Test')
        self.assertTrue(ok['accepted'])
        again=wfg_phase2.record_approval_command('APPROVE '+r['approval_id'],approver='Test')
        self.assertFalse(again['accepted'])
    def test_04_attachment_reference_not_downloaded(self):
        with sqlite3.connect(wfg_phase1.DB_PATH) as c:
            c.row_factory=sqlite3.Row
            c.execute("insert into attachments(dedupe_key,resource_url,resource_name,reference_status,download_status,parse_status,environment) values(?,?,?,?,?,?,?)",('notice:refonly','https://example.invalid/a.pdf','a.pdf','discovered_reference','not_attempted','not_parsed','test'))
            c.commit()
            r=c.execute('select download_status,parse_status,local_path from attachments where dedupe_key="notice:refonly"').fetchone()
        self.assertEqual(r['download_status'],'not_attempted')
        self.assertEqual(r['parse_status'],'not_parsed')
        self.assertIsNone(r['local_path'])
    def test_05_compliance_not_fully_compliant_with_unresolved_facts(self):
        item={'noticeId':'PHASE4COMP001','solicitationNumber':'P4C','title':'Phase 4 Compliance Fixture','naicsCode':'561720','typeOfSetAsideDescription':'Total Small Business','resourceLinks':[]}
        key='notice:phase4comp001'; wfg_phase1.upsert_opportunity(item,key,'phase4','test',1)
        comp=wfg_phase3.compliance('PHASE4COMP001','service',wage_docs_present=False,subcontractor_ids=[],materials='0')
        self.assertFalse(comp['compliant'])
        self.assertTrue(comp['blocking_flags'])
    def test_06_no_external_send_or_submission(self):
        with sqlite3.connect(wfg_phase1.DB_PATH) as c:
            sends=c.execute('select count(*) from outreach_sends').fetchone()[0]
        self.assertEqual(sends,0)
        no=wfg_phase3.record_submission('PHASE4COMP001',None)
        self.assertFalse(no['submitted'])

if __name__=='__main__': unittest.main(verbosity=2)
