#!/usr/bin/env python3
from __future__ import annotations
import os
from pathlib import Path as _P
_BASE=_P('/home/nick/workspace/gov-contracting')
os.environ.setdefault('WFG_ENV','test')
os.environ.setdefault('WFG_DB_PATH',str(_BASE/'state/test/wfg_workflow_test.sqlite3'))
os.environ.setdefault('WFG_STATE_DIR',str(_BASE/'state/test'))
os.environ.setdefault('WFG_BATCHES_DIR',str(_BASE/'test-artifacts/batches'))
os.environ.setdefault('WFG_ARCHIVE_DIR',str(_BASE/'test-artifacts/sam-api'))
os.environ.setdefault('WFG_OPP_ROOT',str(_BASE/'test-artifacts/opportunities'))
import json, shutil, sqlite3, subprocess, sys, unittest
from pathlib import Path

PROJECT = Path('/home/nick/workspace/gov-contracting')
SCRIPTS = PROJECT / 'scripts'
sys.path.insert(0, str(SCRIPTS))
import wfg_phase1
import sam_morning_opportunity_brief as brief
import sync_sam_opportunity_tracker as tracker

class Phase1Tests(unittest.TestCase):
    def setUp(self):
        self.prev_current = wfg_phase1.CURRENT_BATCH.read_text() if wfg_phase1.CURRENT_BATCH.exists() else None

    def tearDown(self):
        if self.prev_current is None:
            wfg_phase1.CURRENT_BATCH.unlink(missing_ok=True)
        else:
            wfg_phase1.atomic_write(wfg_phase1.CURRENT_BATCH, self.prev_current)

    def make_batch_from_latest_archive(self, completed=True):
        bid, bdir, _ = wfg_phase1.create_batch()
        raws = sorted(wfg_phase1.ARCHIVE.glob('raw-*.json'))
        self.assertTrue(raws, 'need at least one archived raw SAM file for replay')
        src = raws[-1]
        dst = bdir / 'raw-p1.json'
        shutil.copy2(src, dst)
        data = json.loads(dst.read_text(errors='replace'))
        count = len(data.get('opportunitiesData') or data.get('data') or [])
        (bdir / 'seen-keys.json').write_text(json.dumps({'batch_id': bid, 'seen_keys': []}))
        wfg_phase1.mark(bdir, snapshot_status='completed', snapshot_file=str(bdir/'seen-keys.json'), fetch_status='completed' if completed else 'failed', raw_files=['raw-p1.json'], api_pages=1, records=count)
        return bid, bdir, count

    def test_exact_dedupe_key_priority(self):
        self.assertEqual(brief.dedupe_key({'noticeId':'ABC','solicitationNumber':'X'}), 'notice:abc')
        self.assertEqual(brief.dedupe_key({'solicitationNumber':'X'}), 'sol:x')
        self.assertTrue(brief.dedupe_key({'title':'T','postedDate':'2026-01-01','uiLink':'U'}).startswith('fallback:'))

    def test_batch_manifest_and_completed_status(self):
        bid, bdir, count = self.make_batch_from_latest_archive(True)
        cbid, cbdir, manifest = wfg_phase1.current_completed_batch_dir()
        self.assertEqual(cbid, bid)
        self.assertEqual(manifest['records'], count)
        self.assertEqual(manifest['fetch_status'], 'completed')

    def test_stale_batch_prevention_failed_fetch(self):
        bid, bdir, _ = self.make_batch_from_latest_archive(False)
        with self.assertRaises(RuntimeError):
            wfg_phase1.current_completed_batch_dir()

    def test_workflow_events_append_only(self):
        wfg_phase1.init_db()
        con = sqlite3.connect(wfg_phase1.DB_PATH)
        con.execute("insert into workflow_events(event_type,event_at) values('test',?)", (wfg_phase1.utc_now(),)); con.commit()
        rowid = con.execute('select max(id) from workflow_events').fetchone()[0]
        with self.assertRaises(sqlite3.DatabaseError):
            con.execute("update workflow_events set event_type='changed' where id=?", (rowid,)); con.commit()
        con.close()

    def test_shortlist_limits_configured(self):
        profile = brief.load_profile()
        self.assertEqual(int(profile['brief']['max_pursue']), 10)
        self.assertEqual(int(profile['brief']['max_urgent']), 5)
        self.assertEqual(int(profile['brief']['max_sources_sought']), 5)
        self.assertEqual(int(profile['brief']['max_watch']), 10)

    def test_offline_replay_current_batch(self):
        bid, bdir, _ = self.make_batch_from_latest_archive(True)
        res = subprocess.run([sys.executable, str(SCRIPTS/'sam_morning_opportunity_brief.py'), '--offline', '--batch-id', bid, '--seen-keys-file', str(bdir/'seen-keys.json'), '--no-final-sync'], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
        self.assertEqual(res.returncode, 0, res.stderr + res.stdout)
        self.assertIn(f'Batch: {bid}', res.stdout)
        self.assertIn('WFG SAM.gov Morning Opportunity Brief', res.stdout)

    def test_failed_fetch_cli_prevents_stale_brief(self):
        bid, bdir, _ = self.make_batch_from_latest_archive(False)
        res = subprocess.run([sys.executable, str(SCRIPTS/'sam_morning_opportunity_brief.py'), '--offline', '--latest-seen-keys', '--no-final-sync'], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
        self.assertNotEqual(res.returncode, 0)
        self.assertIn('stale brief prevented', res.stdout.lower())

    def test_tracker_sync_dry_run_and_interrupt_before_write(self):
        bid, bdir, _ = self.make_batch_from_latest_archive(True)
        res = subprocess.run([sys.executable, str(SCRIPTS/'sync_sam_opportunity_tracker.py'), '--sync', '--batch-id', bid, '--dry-run'], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
        self.assertEqual(res.returncode, 0, res.stderr + res.stdout)
        self.assertIn('"spreadsheetId": "DRY_RUN"', res.stdout)
        res2 = subprocess.run([sys.executable, str(SCRIPTS/'sync_sam_opportunity_tracker.py'), '--sync', '--batch-id', bid, '--simulate-interrupt-before-write'], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
        self.assertNotEqual(res2.returncode, 0)
        self.assertIn('simulated interrupted tracker sync before any sheet clear/update', res2.stderr + res2.stdout)

    def test_tracker_grouped_layout_validation_catches_wrong_state_append(self):
        good = [
            ['Title'],
            ['STATE: MD  |  1 opportunities'],
            tracker.ORGANIZED_HEADERS,
            ['MD', '2026-08-01', '=IF(B4="","",INT(B4-TODAY()))', '', '', '', '', 1, 'notice:md1'],
        ]
        self.assertEqual(tracker.validate_grouped_layout(good, 'Organized Opportunities'), [])
        bad = good + [['VA', '2026-08-02', '=IF(B5="","",INT(B5-TODAY()))', '', '', '', '', 1, 'notice:va1']]
        errors = tracker.validate_grouped_layout(bad, 'Organized Opportunities')
        self.assertTrue(any('under STATE MD but row state is VA' in e for e in errors))
        self.assertEqual(tracker.clean_rows([['MD', None, True]])[0], ['MD', '', 'TRUE'])

if __name__ == '__main__':
    unittest.main(verbosity=2)
