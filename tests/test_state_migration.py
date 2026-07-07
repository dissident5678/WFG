#!/usr/bin/env python3
"""State migration + historical ledger backfill tests (plan Phase 2).

Uses a synthetic fixture DB shaped like the real one so the tests stay valid
after the real database has been migrated. Covers: the state mapping, sent-proof
detection, cross-source dedupe (one send recorded in three tables becomes one
ledger row with three sources), form contacts without email flagged for review,
test-fixture exclusion from production counts, backup creation, per-row
migration events, and idempotent --apply (second run is a no-op).
"""
from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

spec = importlib.util.spec_from_file_location("migration", ROOT / "scripts" / "wfg_state_migration.py")
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)

import wfg_tracking_schema  # noqa: E402


def build_fixture_db(db_path: Path) -> None:
    c = sqlite3.connect(db_path)
    c.executescript(
        """
        CREATE TABLE opportunities(dedupe_key TEXT PRIMARY KEY, notice_id TEXT, title TEXT,
          workflow_status TEXT DEFAULT 'discovered', is_test_fixture INTEGER DEFAULT 0,
          environment TEXT DEFAULT 'production');
        CREATE TABLE subcontractor_contacts(id INTEGER PRIMARY KEY, email TEXT);
        CREATE TABLE subcontractor_interactions(id INTEGER PRIMARY KEY AUTOINCREMENT,
          subcontractor_id INTEGER, dedupe_key TEXT, interaction_type TEXT, status TEXT,
          direction TEXT, occurred_at TEXT, contact_id INTEGER);
        CREATE TABLE gmail_drafts(id INTEGER PRIMARY KEY AUTOINCREMENT, dedupe_key TEXT,
          to_recipients TEXT, sent_at TEXT, sent_message_id TEXT, status TEXT DEFAULT 'draft_created');
        CREATE TABLE workflow_events(id INTEGER PRIMARY KEY AUTOINCREMENT, dedupe_key TEXT,
          event_type TEXT, event_at TEXT, actor TEXT, details_json TEXT);
        CREATE TABLE approvals(id INTEGER PRIMARY KEY AUTOINCREMENT, approval_id TEXT,
          dedupe_key TEXT, gate TEXT, gate_id TEXT, decision TEXT, valid INTEGER DEFAULT 1);
        """
    )
    # Opportunities: A has sent proof, B does not, C/D await pursuit (D is a test fixture).
    c.executemany(
        "insert into opportunities(dedupe_key,title,workflow_status,is_test_fixture,environment) values(?,?,?,?,?)",
        [
            ("notice:aaa", "Opp A", "outreach_approved", 0, "production"),
            ("notice:bbb", "Opp B", "outreach_approved", 0, "production"),
            ("notice:ccc", "Opp C", "awaiting_pursue_decision", 0, "production"),
            ("notice:ddd", "Opp D fixture", "awaiting_pursue_decision", 1, "test"),
            ("notice:eee", "Opp E untouched", "discovered", 0, "production"),
        ],
    )
    c.execute("insert into subcontractor_contacts(id,email) values(1,'x@sub.com')")
    # One real-world send to x@sub.com recorded in THREE places (same day):
    c.execute(
        "insert into subcontractor_interactions(subcontractor_id,dedupe_key,interaction_type,direction,occurred_at,contact_id)"
        " values(7,'notice:aaa','email','outbound','2026-07-01T10:00:00+00:00',1)"
    )
    c.execute(
        "insert into gmail_drafts(dedupe_key,to_recipients,sent_at,sent_message_id,status)"
        " values('notice:aaa','x@sub.com','2026-07-01T10:00:05+00:00','msg-1','sent')"
    )
    c.execute(
        "insert into workflow_events(dedupe_key,event_type,event_at,actor,details_json)"
        " values('notice:aaa','subcontractor_outreach_email_sent','2026-07-01T10:00:06+00:00','test',?)",
        (json.dumps({"to": "x@sub.com", "sent_at": "2026-07-01T10:00:06+00:00", "approval_id": "appr-legacy-1"}),),
    )
    # A form contact with no recipient email -> needs_human_review.
    c.execute(
        "insert into subcontractor_interactions(subcontractor_id,dedupe_key,interaction_type,direction,occurred_at,contact_id)"
        " values(9,'notice:aaa','form','outbound','2026-07-02T09:00:00+00:00',NULL)"
    )
    # A web_route_check must NOT count as a contact.
    c.execute(
        "insert into subcontractor_interactions(subcontractor_id,dedupe_key,interaction_type,direction,occurred_at,contact_id)"
        " values(9,'notice:aaa','web_route_check','outbound','2026-07-02T09:01:00+00:00',NULL)"
    )
    # Approvals: legacy gate texts without gate_id, one legacy 'rejected' decision.
    c.executemany(
        "insert into approvals(approval_id,dedupe_key,gate,decision) values(?,?,?,?)",
        [
            ("appr-1", "notice:ccc", "GATE 1 — Pursue or Pass", "pending"),
            ("appr-2", "notice:aaa", "GATE 2 — Authorize External Outreach", "approved"),
            ("appr-3", "notice:eee", "GATE 1 — Pursue or Pass", "rejected"),
        ],
    )
    c.commit()
    c.close()


class StateMigrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        tmp_path = Path(self.tmp.name)
        self.db = tmp_path / "state" / "wfg_workflow.sqlite3"
        self.db.parent.mkdir(parents=True)
        build_fixture_db(self.db)
        migration.DB = self.db
        migration.PROJECT = tmp_path

    def tearDown(self):
        self.tmp.cleanup()

    def q(self, sql: str, *params):
        c = sqlite3.connect(self.db)
        c.row_factory = sqlite3.Row
        rows = [dict(r) for r in c.execute(sql, params)]
        c.close()
        return rows

    def plan(self):
        c = migration.con()
        try:
            return migration.plan_migration(c)
        finally:
            c.close()

    def apply(self):
        c = migration.con()
        try:
            plan = migration.plan_migration(c)
            backup = migration.backup_db()
            applied = migration.apply_migration(c, plan)
            return plan, applied, backup
        finally:
            c.close()

    def test_dry_run_proposals_and_counts(self):
        plan = self.plan()
        proposals = {p["dedupe_key"]: p for p in plan["state_proposals"]}
        self.assertEqual(proposals["notice:aaa"]["to"], "quotes_pending")
        self.assertTrue(proposals["notice:aaa"]["sent_proof_found"])
        self.assertEqual(proposals["notice:bbb"]["to"], "gate2_pending_outreach_send")
        self.assertFalse(proposals["notice:bbb"]["sent_proof_found"])
        self.assertEqual(proposals["notice:ccc"]["to"], "gate1_pending_pursue")
        self.assertEqual(proposals["notice:ddd"]["to"], "gate1_pending_pursue")
        self.assertEqual(proposals["notice:ddd"]["is_test_fixture"], 1)
        self.assertNotIn("notice:eee", proposals)  # discovered is untouched
        # Test fixtures are counted separately from production.
        self.assertEqual(plan["status_counts"]["test"]["awaiting_pursue_decision"], 1)
        self.assertEqual(plan["status_counts"]["production"]["awaiting_pursue_decision"], 1)
        self.assertEqual(plan["decision_rejected_to_denied"], 1)
        self.assertEqual(plan["approvals_gate_id_backfill"], 3)
        # Dry run writes nothing.
        self.assertEqual(self.q("select count(*) n from external_action_ledger")[0]["n"], 0)

    def test_backfill_dedupes_across_sources_and_flags_form_contacts(self):
        plan = self.plan()
        entries = list(plan["_backfill_entries"].values())
        # One send in three tables -> one entry with three sources; one form review row.
        self.assertEqual(len(entries), 2)
        email_entry = next(e for e in entries if e["recipient_email"] == "x@sub.com")
        self.assertEqual(len(email_entry["sources"]), 3)
        self.assertEqual({s["table"] for s in email_entry["sources"]},
                         {"subcontractor_interactions", "gmail_drafts", "workflow_events"})
        self.assertEqual(email_entry["approval_id"], "appr-legacy-1")
        form_entry = next(e for e in entries if e["recipient_email"] is None)
        self.assertEqual(form_entry["needs_human_review"], 1)
        self.assertTrue(form_entry["recipient_key"].startswith("form:"))
        # web_route_check contributed nothing.
        all_sources = [s for e in entries for s in e["sources"]]
        self.assertFalse(any(s.get("type") == "web_route_check" for s in all_sources))

    def test_apply_then_reapply_is_noop(self):
        plan1, applied1, backup = self.apply()
        self.assertTrue(Path(backup).exists())
        self.assertEqual(applied1["states_migrated"], 4)
        self.assertEqual(applied1["ledger_rows_inserted"], 2)
        self.assertEqual(applied1["gate2_send_tasks_queued"], 1)
        self.assertEqual(applied1["decisions_standardized"], 1)
        self.assertEqual(applied1["gate_ids_backfilled"], 3)
        # States landed.
        states = {r["dedupe_key"]: r["workflow_status"] for r in self.q("select dedupe_key,workflow_status from opportunities")}
        self.assertEqual(states["notice:aaa"], "quotes_pending")
        self.assertEqual(states["notice:bbb"], "gate2_pending_outreach_send")
        self.assertEqual(states["notice:ccc"], "gate1_pending_pursue")
        self.assertEqual(states["notice:ddd"], "gate1_pending_pursue")
        self.assertEqual(states["notice:eee"], "discovered")
        # Fixture keeps its environment and never enters production counts.
        row = self.q("select is_test_fixture, environment from opportunities where dedupe_key='notice:ddd'")[0]
        self.assertEqual((row["is_test_fixture"], row["environment"]), (1, "test"))
        # Migration events written per migrated opportunity.
        events = self.q("select dedupe_key from workflow_events where event_type='state_migration'")
        self.assertEqual(len(events), 4)
        # Legacy Gate 2 without proof queues a new-cycle task, not a send.
        tasks = self.q("select * from workflow_tasks where task_type='create_gate2_send_packet'")
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["dedupe_key"], "notice:bbb")
        # Decision vocabulary and gate IDs.
        self.assertEqual(self.q("select count(*) n from approvals where decision='rejected'")[0]["n"], 0)
        self.assertEqual(self.q("select count(*) n from approvals where decision='denied'")[0]["n"], 1)
        gate_ids = {r["approval_id"]: r["gate_id"] for r in self.q("select approval_id, gate_id from approvals")}
        self.assertEqual(gate_ids["appr-1"], "GATE_1_PURSUE")
        self.assertEqual(gate_ids["appr-2"], "LEGACY_GATE_2_BROAD")
        # Second apply: everything is a no-op.
        plan2, applied2, _ = self.apply()
        self.assertEqual(plan2["state_proposals"], [])
        self.assertEqual(applied2["states_migrated"], 0)
        self.assertEqual(applied2["ledger_rows_inserted"], 0)
        self.assertEqual(applied2["gate2_send_tasks_queued"], 0)
        self.assertEqual(applied2["decisions_standardized"], 0)
        self.assertEqual(applied2["gate_ids_backfilled"], 0)

    def test_historical_proof_blocks_duplicate_send(self):
        self.apply()
        c = migration.con()
        try:
            wfg_tracking_schema.ensure_phase2_workflow_schema(c)
            blocked = wfg_tracking_schema.ledger_blocks_send(c, "notice:aaa", "x@sub.com")
            self.assertIsNotNone(blocked)
            self.assertEqual(blocked["status"], "historical_sent_proof")
            # Same recipient, different opportunity: not blocked.
            self.assertIsNone(wfg_tracking_schema.ledger_blocks_send(c, "notice:bbb", "x@sub.com"))
        finally:
            c.close()


if __name__ == "__main__":
    unittest.main()
