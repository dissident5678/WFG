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
import json, sqlite3, subprocess, sys, tempfile, zipfile, unittest
from pathlib import Path

PROJECT=Path(str(_BASE))
SCRIPTS=PROJECT/'scripts'
sys.path.insert(0,str(SCRIPTS))
import wfg_phase2
import wfg_phase1

class Phase2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        wfg_phase2.migrate()
        cls.tmp=Path(tempfile.mkdtemp(prefix='wfg-phase2-fixtures-'))
        (cls.tmp/'scope.txt').write_text('Submission deadline and evaluation. Wage determination may apply. Insurance required. Quote due details.')
        # simple docx-like zip
        with zipfile.ZipFile(cls.tmp/'spec.docx','w') as z:
            z.writestr('word/document.xml','<w:t>Site visit optional. Bond and past performance requirements must be reviewed.</w:t>')
        cls.notice='PHASE2TESTNOTICE001'
        cls.sol='PHASE2-SOL-001'
        cls.dedupe='notice:'+cls.notice.lower()
        item={
            'noticeId':cls.notice,'solicitationNumber':cls.sol,'title':'Phase 2 Fixture Janitorial Services',
            'fullParentPathName':'Test Agency','naicsCode':'561720','classificationCode':'S201','type':'Combined Synopsis/Solicitation',
            'typeOfSetAside':'SBA','typeOfSetAsideDescription':'Total Small Business Set-Aside',
            'postedDate':'2026-06-24','responseDeadLine':'2026-07-30T12:00:00-04:00','uiLink':'https://sam.gov/opp/phase2test',
            'resourceLinks':['file://'+str(cls.tmp/'scope.txt'),'file://'+str(cls.tmp/'spec.docx')]
        }
        wfg_phase1.upsert_opportunity(item,cls.dedupe,'phase2-fixture','pursue',80)
        with sqlite3.connect(wfg_phase1.DB_PATH) as c:
            c.execute('update opportunities set workflow_status="discovered" where dedupe_key=?',(cls.dedupe,)); c.commit()

    def test_01_intake_resolves_downloads_hashes_drafts(self):
        data=wfg_phase2.intake(self.notice, fixture_no_network=True)
        self.__class__.first=data
        self.assertEqual(data['dedupe_key'], self.dedupe)
        self.assertEqual(len([d for d in data['downloads'] if d['status']=='downloaded']),2)
        self.assertTrue(all(d['sha256'] for d in data['downloads']))
        folder=Path(data['folder'])
        self.assertTrue((folder/'attachment_manifest.md').exists())
        for name in ['00_INTAKE.md','01_BID_NO_BID_SCORECARD.md','02_SOLICITATION_BRIEF.md','03_COMPLIANCE_MATRIX.md','04_MISSING_INFORMATION.md','05_SCOPE_DECOMPOSITION.md','06_SUBCONTRACTOR_SOURCING_CRITERIA.md','07_DRAFT_OUTREACH.md','08_PRICING_ASSUMPTIONS.md','09_TECHNICAL_PROPOSAL_SKELETON.md','10_REQUIRED_FORMS_CHECKLIST.md','11_SUBMISSION_CHECKLIST.md','12_RISK_REGISTER.md']:
            self.assertTrue((folder/'drafts'/name).exists(), name)
        self.assertIn('[DOCUMENT MISSING]', (folder/'drafts'/'04_MISSING_INFORMATION.md').read_text())
        self.assertEqual(wfg_phase2.get_status(self.dedupe),'awaiting_pursue_decision')

    def test_02_approval_exact_hash_and_ambiguous_rejected(self):
        data=getattr(self.__class__,'first',None) or wfg_phase2.intake(self.notice, fixture_no_network=True)
        bad=wfg_phase2.record_approval_command('looks good',approver='Test')
        self.assertFalse(bad['accepted'])
        wrong=wfg_phase2.record_approval_command(f'APPROVE PURSUE {self.notice} wrongversion',approver='Test')
        self.assertFalse(wrong['accepted'])
        ok=wfg_phase2.record_approval_command(f'APPROVE PURSUE {self.notice} {data["version_hash"]}',approver='Test',telegram_user_id='123')
        self.assertTrue(ok['accepted'], ok)
        with sqlite3.connect(wfg_phase1.DB_PATH) as c:
            c.row_factory=sqlite3.Row
            r=c.execute('select artifact_hash, telegram_user_id, decision from approvals where dedupe_key=? and artifact_version=? order by id desc limit 1',(self.dedupe,data['version_hash'])).fetchone()
        self.assertEqual(r['decision'],'approved')
        self.assertEqual(r['telegram_user_id'],'123')
        self.assertTrue(r['artifact_hash'])

    def test_03_changed_attachment_new_version_invalidates_old(self):
        data=getattr(self.__class__,'first',None) or wfg_phase2.intake(self.notice, fixture_no_network=True)
        (self.tmp/'scope.txt').write_text('CHANGED attachment content. New amendment terms. Submission method changed.')
        # reset to allow re-intake from pursuing
        with sqlite3.connect(wfg_phase1.DB_PATH) as c:
            c.execute('update opportunities set workflow_status="awaiting_pursue_decision" where dedupe_key=?',(self.dedupe,)); c.commit()
        new=wfg_phase2.intake(self.notice, fixture_no_network=True)
        self.assertNotEqual(data['version_hash'], new['version_hash'])
        self.assertIn('file://'+str(self.tmp/'scope.txt'), new['diffs']['changed'])
        with sqlite3.connect(wfg_phase1.DB_PATH) as c:
            c.row_factory=sqlite3.Row
            rows=c.execute('select valid, invalidated_reason from approvals where dedupe_key=? and artifact_version=?',(self.dedupe,data['version_hash'])).fetchall()
        self.assertTrue(any(r['valid']==0 and 'changed' in (r['invalidated_reason'] or '') for r in rows))
        self.assertTrue((Path(new['folder'])/'versions'/f"{new['version_hash']}.md").exists())
        self.assertTrue((Path(new['folder'])/'versions'/f"{data['version_hash']}.md").exists())

    def test_04_no_external_message_or_submission(self):
        # Phase2 tests only call local intake/approval parser and file:// downloads.
        with sqlite3.connect(wfg_phase1.DB_PATH) as c:
            n=c.execute("select count(*) from workflow_events where event_type in ('external_message_sent','submitted_to_government') and dedupe_key=?",(self.dedupe,)).fetchone()[0]
        self.assertEqual(n,0)

if __name__=='__main__': unittest.main(verbosity=2)
