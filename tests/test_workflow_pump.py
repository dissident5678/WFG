#!/usr/bin/env python3
"""Workflow pump tests (plan Phase 2): the pump runs its steps against an
isolated project copy, logs each run, and fails loudly when the project dir is
missing. Isolation matters: the pump's reconcile step moves packet files, so it
must never run against the real repo from a test."""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class WorkflowPumpTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.proj = Path(self.tmp.name) / "proj"
        (self.proj / "scripts").mkdir(parents=True)
        for name in (
            "wfg_workflow_pump.py", "wfg_approval_dispatcher.py",
            "reconcile_wfg_approval_buttons.py", "wfg_gates.py", "wfg_tracking_schema.py",
        ):
            shutil.copy2(ROOT / "scripts" / name, self.proj / "scripts" / name)
        (self.proj / "approvals" / "pending").mkdir(parents=True)
        (self.proj / "state").mkdir()
        db = self.proj / "state" / "wfg_workflow.sqlite3"
        c = sqlite3.connect(db)
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
        c.commit()
        c.close()

    def tearDown(self):
        self.tmp.cleanup()

    def run_pump(self, project: Path) -> tuple[int, dict]:
        env = dict(os.environ, WFG_PROJECT_DIR=str(project))
        proc = subprocess.run(
            [sys.executable, str(self.proj / "scripts" / "wfg_workflow_pump.py"), "--no-kanban-dispatch"],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env, timeout=300,
        )
        try:
            data = json.loads(proc.stdout)
        except Exception:
            data = {"raw": proc.stdout}
        return proc.returncode, data

    def test_pump_runs_all_steps_and_writes_run_log(self):
        rc, data = self.run_pump(self.proj)
        self.assertEqual(rc, 0, data)
        self.assertTrue(data["ok"], data)
        names = [s["name"] for s in data["steps"]]
        self.assertEqual(names, ["reconcile_approval_buttons", "dispatch_approved_gates"])
        self.assertTrue(all(s["ok"] for s in data["steps"]), data)
        logs = list((self.proj / "state" / "workflow-pump-runs").glob("*.json"))
        self.assertEqual(len(logs), 1)
        logged = json.loads(logs[0].read_text())
        self.assertTrue(logged["ok"])

    def test_pump_fails_loudly_on_missing_project(self):
        rc, data = self.run_pump(Path(self.tmp.name) / "does-not-exist")
        self.assertEqual(rc, 2)
        self.assertFalse(data.get("ok"))

    def test_pump_step_failure_is_reported_not_hidden(self):
        # Break the dispatcher so its step fails; the pump must report ok=false
        # and still write the run log with the error captured.
        bad = self.proj / "scripts" / "wfg_approval_dispatcher.py"
        bad.write_text("import sys\nsys.exit(3)\n")
        rc, data = self.run_pump(self.proj)
        self.assertNotEqual(rc, 0)
        self.assertFalse(data["ok"])
        step = next(s for s in data["steps"] if s["name"] == "dispatch_approved_gates")
        self.assertFalse(step["ok"])
        logs = list((self.proj / "state" / "workflow-pump-runs").glob("*.json"))
        self.assertEqual(len(logs), 1)


if __name__ == "__main__":
    unittest.main()
