#!/usr/bin/env python3
"""Dispatcher safety tests (consensus plan Phase 2 acceptance criteria).

Covers: gate-ID-only matching, refusal on unknown gate, legacy broad Gate 2
never dispatching, denied/held/revise never dispatching, idempotent
re-dispatch, and Kanban mirror failure leaving a valid workflow_tasks row.
"""
from __future__ import annotations

import datetime as dt
import importlib.util
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

spec = importlib.util.spec_from_file_location("dispatcher", ROOT / "scripts" / "wfg_approval_dispatcher.py")
dispatcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dispatcher)

import wfg_tracking_schema  # noqa: E402


def utcnow() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


FAKE_KANBAN_TASK = {"task_id": "kanban-123", "title": "fake", "body": "fake", "raw": {}, "output": "{}"}


class DispatcherTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        tmp_path = Path(self.tmp.name)
        self.db = tmp_path / "wfg_test.sqlite3"
        dispatcher.DB = self.db
        dispatcher.PROJECT = tmp_path
        dispatcher.ROUTING = tmp_path / "config" / "approval-routing.json"  # absent -> no telegram
        c = sqlite3.connect(self.db)
        c.executescript(
            """
            CREATE TABLE opportunities(dedupe_key TEXT PRIMARY KEY, notice_id TEXT,
              solicitation_number TEXT, title TEXT, workflow_status TEXT DEFAULT 'discovered',
              is_test_fixture INTEGER DEFAULT 0, environment TEXT DEFAULT 'production');
            CREATE TABLE approvals(id INTEGER PRIMARY KEY AUTOINCREMENT, approval_id TEXT,
              dedupe_key TEXT, gate TEXT, gate_id TEXT, decision TEXT, valid INTEGER DEFAULT 1,
              used_at TEXT, decided_at TEXT, record_path TEXT, environment TEXT DEFAULT 'production');
            CREATE TABLE workflow_events(id INTEGER PRIMARY KEY AUTOINCREMENT, dedupe_key TEXT,
              event_type TEXT, event_at TEXT, actor TEXT, details_json TEXT);
            """
        )
        c.execute("insert into opportunities(dedupe_key,title,workflow_status) values('notice:t1','Test Opp','gate1_pending_pursue')")
        c.commit()
        c.close()
        dispatcher.migrate()

    def tearDown(self):
        self.tmp.cleanup()

    def add_approval(self, approval_id: str, gate: str = "", gate_id: str = "", decision: str = "approved") -> None:
        c = sqlite3.connect(self.db)
        c.execute(
            "insert into approvals(approval_id,dedupe_key,gate,gate_id,decision,valid,used_at,decided_at) values(?,?,?,?,?,1,?,?)",
            (approval_id, "notice:t1", gate, gate_id, decision, utcnow(), utcnow()),
        )
        c.commit()
        c.close()

    def q(self, sql: str, *params):
        c = sqlite3.connect(self.db)
        c.row_factory = sqlite3.Row
        rows = [dict(r) for r in c.execute(sql, params)]
        c.close()
        return rows

    def test_gate_id_dispatch_creates_db_task_first(self):
        self.add_approval("appr-1", gate_id="GATE_1_PURSUE")
        with mock.patch.object(dispatcher, "create_kanban_task", return_value=dict(FAKE_KANBAN_TASK)):
            result = dispatcher.run(do_dispatch=False)
        self.assertEqual(result["results"][0]["status"], "queued")
        tasks = self.q("select * from workflow_tasks")
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["task_type"], "gate1_subcontractor_sourcing")
        self.assertEqual(tasks[0]["current_state"], "queued")
        self.assertEqual(tasks[0]["kanban_mirror_status"], "mirrored")
        opp = self.q("select workflow_status from opportunities where dedupe_key='notice:t1'")
        self.assertEqual(opp[0]["workflow_status"], "pursuing")

    def test_unknown_gate_refused_once(self):
        self.add_approval("appr-2", gate="Some Unrecognized Gate Text")
        with mock.patch.object(dispatcher, "create_kanban_task", return_value=dict(FAKE_KANBAN_TASK)):
            r1 = dispatcher.run(do_dispatch=False)
            r2 = dispatcher.run(do_dispatch=False)
        self.assertEqual(r1["results"][0]["status"], "refused")
        self.assertEqual(r1["results"][0]["reason"], "unknown_gate_id")
        self.assertTrue(r1["results"][0]["first_refusal"])
        self.assertFalse(r2["results"][0]["first_refusal"])
        self.assertEqual(len(self.q("select * from workflow_tasks")), 0)
        self.assertEqual(len(self.q("select * from approval_dispatches where status='refused'")), 1)
        events = self.q("select * from workflow_events where event_type='approval_dispatch_refused'")
        self.assertEqual(len(events), 1)

    def test_legacy_broad_gate2_never_dispatches(self):
        self.add_approval("appr-3", gate="GATE 2 — Authorize External Outreach")
        with mock.patch.object(dispatcher, "create_kanban_task", return_value=dict(FAKE_KANBAN_TASK)):
            result = dispatcher.run(do_dispatch=False)
        self.assertEqual(result["results"][0]["status"], "refused")
        self.assertEqual(result["results"][0]["reason"], "gate_not_dispatchable")
        self.assertEqual(len(self.q("select * from workflow_tasks")), 0)

    def test_outreach_titled_gate_does_not_hit_send_path(self):
        # The old substring matcher would have dispatched gate2_outreach_execution
        # for any gate title containing "Outreach". Now: refusal.
        self.add_approval("appr-4", gate="Approve Outreach Draft")
        result = dispatcher.run(do_dispatch=False)
        self.assertEqual(result["results"][0]["status"], "refused")
        self.assertEqual(len(self.q("select * from workflow_tasks where task_type='gate2_outreach_execution'")), 0)

    def test_non_approved_decisions_never_dispatch(self):
        for decision in ("denied", "revise_requested", "held", "pending"):
            downstream, refusal = dispatcher.downstream_for_approval(
                {"decision": decision, "gate_id": "GATE_1_PURSUE", "gate": ""}
            )
            self.assertIsNone(downstream, decision)
            self.assertIsNone(refusal, decision)

    def test_idempotent_redispatch(self):
        self.add_approval("appr-5", gate_id="GATE_1_PURSUE")
        with mock.patch.object(dispatcher, "create_kanban_task", return_value=dict(FAKE_KANBAN_TASK)):
            r1 = dispatcher.run(do_dispatch=False)
            r2 = dispatcher.run(do_dispatch=False)
        self.assertEqual(r1["results"][0]["status"], "queued")
        self.assertEqual(r2["results"][0]["status"], "already_dispatched")
        self.assertEqual(len(self.q("select * from workflow_tasks")), 1)

    def test_kanban_failure_keeps_db_task_and_mirror_retries(self):
        self.add_approval("appr-6", gate_id="GATE_1_PURSUE")
        with mock.patch.object(dispatcher, "create_kanban_task", side_effect=RuntimeError("kanban down")):
            r1 = dispatcher.run(do_dispatch=False)
        self.assertEqual(r1["results"][0]["status"], "queued")
        self.assertFalse(r1["results"][0]["kanban_mirror_ok"])
        tasks = self.q("select * from workflow_tasks")
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["kanban_mirror_status"], "failed")
        # Mirror recovers on a later run without duplicating the task.
        with mock.patch.object(dispatcher, "create_kanban_task", return_value=dict(FAKE_KANBAN_TASK)):
            r2 = dispatcher.run(do_dispatch=False)
        self.assertEqual(r2["results"][0]["status"], "already_dispatched")
        self.assertTrue(r2["results"][0].get("retried_mirror"))
        tasks = self.q("select * from workflow_tasks")
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["kanban_mirror_status"], "mirrored")

    def test_gate2_package_queues_send_prep_not_send(self):
        self.add_approval("appr-7", gate_id="GATE_2_PACKAGE")
        with mock.patch.object(dispatcher, "create_kanban_task", return_value=dict(FAKE_KANBAN_TASK)):
            result = dispatcher.run(do_dispatch=False)
        self.assertEqual(result["results"][0]["dispatch_type"], "gate2_send_approval_prep")
        self.assertEqual(len(self.q("select * from workflow_tasks where task_type='gate2_outreach_execution'")), 0)

    def test_ledger_blocks_duplicate_send_same_opportunity_only(self):
        c = sqlite3.connect(self.db)
        c.row_factory = sqlite3.Row
        wfg_tracking_schema.record_ledger_action(
            c, dedupe_key="notice:t1", action_type="subcontractor_email",
            recipient_key="sub@example.com", recipient_email="sub@example.com",
            status="historical_sent_proof", idempotency_key="hist:test1",
        )
        c.commit()
        blocked = wfg_tracking_schema.ledger_blocks_send(c, "notice:t1", "SUB@example.com")
        self.assertIsNotNone(blocked)
        self.assertEqual(blocked["status"], "historical_sent_proof")
        self.assertIsNone(wfg_tracking_schema.ledger_blocks_send(c, "notice:other", "sub@example.com"))
        # Idempotent insert: same key inserts nothing new.
        again = wfg_tracking_schema.record_ledger_action(
            c, dedupe_key="notice:t1", action_type="subcontractor_email",
            recipient_key="sub@example.com", recipient_email="sub@example.com",
            status="historical_sent_proof", idempotency_key="hist:test1",
        )
        self.assertIsNone(again)
        c.close()


if __name__ == "__main__":
    unittest.main()
