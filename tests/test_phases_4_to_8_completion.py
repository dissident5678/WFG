#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_script(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


delegate = load_script("wfg_delegate_task")
outreach = load_script("wfg_outreach_cycle")
proposal = load_script("wfg_proposal_assembler")
command = load_script("wfg_command_center")
hardening = load_script("wfg_repo_hardening")
research_preflight = load_script("wfg_research_preflight")

RESEARCH_ARTIFACTS = {
    "02_SOLICITATION_BRIEF.md": (
        "# Brief\n\n"
        "- Title: Phase 5 Test Opportunity\n"
        "- Agency: Test Agency contracting office\n"
        "- Solicitation: SOL-1\n"
        "- Notice ID: testopp001\n"
        "- Place of performance: Testville, MD\n"
        "- Response deadline: 2026-08-01 12:00 ET\n"
        "- Questions due: None\n"
        "- Site visit: None\n"
        "- POP: 12 months\n"
        "- Pricing format: Lump sum\n\n"
        "## Scope summary\n- Provide commercial janitorial service for Building 1 per PWS 3.1\n"
    ),
    "05_SCOPE_DECOMPOSITION.md": "# Scope\n\n## Work packages\n- Daily cleaning per PWS 3.1\n- Restroom sanitation per PWS 3.2\n",
    "06_SUBCONTRACTOR_SOURCING_CRITERIA.md": "# Criteria\n\n- Trade: commercial janitorial\n- Serve Testville, MD\n",
    "04_MISSING_INFORMATION.md": "# Missing\n\n- None outstanding.\n",
    "attachment_manifest.md": "# Manifest\n\n- solicitation.txt — combined solicitation text; subs need PWS sections.\n",
}


def complete_research(opp: Path) -> None:
    """Write real research artifacts and pass the preflight — the mandatory
    first step of the pipeline since the research-first barrier."""
    (opp / "source").mkdir(exist_ok=True)
    (opp / "source" / "solicitation.txt").write_text("solicitation text", encoding="utf-8")
    for name, text in RESEARCH_ARTIFACTS.items():
        (opp / name).write_text(text, encoding="utf-8")
    report = research_preflight.run(opp)
    assert report["status"] == "PASS", report["failed"]


class Phases4To8CompletionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db = self.root / "state" / "wfg_workflow.sqlite3"
        self.opp = self.root / "2026-07-07_testopp_phase5"
        self.opp.mkdir(parents=True)
        for mod in [delegate, outreach, proposal, command]:
            mod.DB = self.db
        command.OUT = self.root / "command-center"
        command.OBSIDIAN = self.root / "obsidian" / "00-Dashboards"
        hardening.PROJECT = ROOT
        self._seed_opportunity()

    def tearDown(self):
        self.tmp.cleanup()

    def _seed_opportunity(self):
        self.db.parent.mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(self.db)
        c.executescript(
            """
            CREATE TABLE opportunities(
              dedupe_key TEXT PRIMARY KEY,
              title TEXT,
              agency TEXT,
              solicitation_number TEXT,
              response_deadline TEXT,
              workflow_status TEXT
            );
            CREATE TABLE artifact_index(
              artifact_id INTEGER PRIMARY KEY AUTOINCREMENT,
              dedupe_key TEXT,
              artifact_type TEXT,
              audience TEXT,
              local_path TEXT,
              drive_file_id TEXT,
              drive_web_view_link TEXT,
              version TEXT,
              sha256 TEXT,
              created_at TEXT,
              superseded_at TEXT,
              environment TEXT DEFAULT 'test'
            );
            """
        )
        c.execute(
            "insert into opportunities values(?,?,?,?,?,?)",
            ("notice:testopp", "Phase 5 Test Opportunity", "Test Agency", "SOL-1", "2026-08-01T12:00:00-04:00", "pursuing"),
        )
        c.commit()
        c.close()

    def _packet_inputs(self):
        complete_research(self.opp)
        packet_dir = self.opp / "subcontractor_bid_packet"
        packet_dir.mkdir(parents=True)
        (packet_dir / "subcontractor_bid_packet.md").write_text("# Packet\nExternal safe packet.\n", encoding="utf-8")
        msg = self.opp / "draft_outreach.md"
        msg.write_text("Subject: Quote Request - Test\n\nHello,\nPlease quote this scope.\n", encoding="utf-8")
        recipients = self.opp / "recipients.csv"
        recipients.write_text("company,email,subcontractor_id\nGood Sub,good@example.com,7\nPlaceholder,[email to verify],8\n", encoding="utf-8")
        return recipients, msg

    def _approve(self, approval_id: str, gate_id: str, package_hash: str):
        c = sqlite3.connect(self.db)
        c.execute(
            "update approvals set decision='approved', decided_at='2026-07-07T00:00:00+00:00', valid=1 where approval_id=? and gate_id=? and artifact_hash=?",
            (approval_id, gate_id, package_hash),
        )
        c.commit()
        c.close()

    def test_phase4_delegation_queues_role_bound_task_not_skill(self):
        out = delegate.queue_task(
            role_id="outreach-coordinator",
            task_type="prepare_gate2_package",
            dedupe_key="notice:testopp",
            opportunity_folder=str(self.opp),
            inputs={"packet": "ready"},
            next_gate="GATE_2_PACKAGE",
        )
        self.assertTrue(out["ok"])
        self.assertTrue(Path(out["task_brief"]).exists())
        with self.assertRaises(ValueError):
            delegate.queue_task(role_id="outreach-coordinator", task_type="wfg-outreach-drafter", dedupe_key="notice:testopp")

    def test_phase5_gate2_package_send_and_duplicate_block(self):
        recipients, msg = self._packet_inputs()
        pkg = outreach.build_package(self.opp, recipients, msg, dedupe_key="notice:testopp")
        self.assertEqual(len(pkg["recipients"]), 1)
        self.assertTrue(pkg["pending_subject"].startswith(outreach.PENDING_PREFIX))
        early = outreach.create_send_approval(pkg["package_version"])
        self.assertFalse(early["ok"])
        self._approve(pkg["package_approval_id"], "GATE_2_PACKAGE", pkg["package_hash"])
        send_req = outreach.create_send_approval(pkg["package_version"])
        self.assertTrue(send_req["ok"])
        dry = outreach.execute_send(pkg["package_version"])
        self.assertEqual(dry["sent"], 0)
        self._approve(pkg["send_approval_id"], "GATE_2_SEND", pkg["package_hash"])
        sent = outreach.execute_send(pkg["package_version"], execute=True, transport="mock")
        self.assertEqual(sent["sent"], 1)
        again = outreach.execute_send(pkg["package_version"], execute=True, transport="mock")
        self.assertEqual(again["sent"], 0)
        self.assertTrue(again["results"][0]["blocked"])

    def test_phase6_proposal_package_has_separate_gate4_and_gate5(self):
        out = proposal.assemble(self.opp, dedupe_key="notice:testopp", pricing_version="price-test", compliance_version="comp-test")
        self.assertTrue(out["ok"])
        self.assertTrue(Path(out["zip"]).exists())
        gate4 = Path(out["gate4_package_packet"]).read_text(encoding="utf-8")
        gate5 = Path(out["gate5_submit_packet"]).read_text(encoding="utf-8")
        self.assertIn("This does not submit", gate4)
        self.assertIn("APPROVE GATE_5", gate5)
        self.assertIn(out["package_hash"][:8], gate5)

    def test_phase7_command_center_answers_operator_questions(self):
        recipients, msg = self._packet_inputs()
        pkg = outreach.build_package(self.opp, recipients, msg, dedupe_key="notice:testopp")
        out = command.build(self.root / "cc")
        self.assertTrue(out["ok"])
        brief = Path(out["telegram_brief"]).read_text(encoding="utf-8")
        self.assertIn("Approvals waiting", brief)
        answers = out["answers"]
        self.assertIn(pkg["package_approval_id"], answers["what_needs_approval"])
        self.assertTrue(answers["what_is_hermes_working_on"])

    def test_phase8_hardening_files_present_and_scan_clean(self):
        out = hardening.check()
        self.assertEqual(out["missing_required_files"], [])
        self.assertEqual(out["findings"], [])


if __name__ == "__main__":
    unittest.main()
