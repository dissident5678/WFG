#!/usr/bin/env python3
"""Research-first barrier tests.

The rule under test: a subcontractor bid packet is the output of completed
research. The preflight must fail on scaffold/placeholder research, the packet
builder must refuse to render without a current PASS, and GATE_2_PACKAGE must
be impossible to create while research is incomplete.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

spec = importlib.util.spec_from_file_location("preflight", ROOT / "scripts" / "wfg_research_preflight.py")
preflight = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preflight)

spec2 = importlib.util.spec_from_file_location("outreach", ROOT / "scripts" / "wfg_outreach_cycle.py")
outreach = importlib.util.module_from_spec(spec2)
spec2.loader.exec_module(outreach)

GOOD_BRIEF = """# Solicitation Brief

- Title: Janitorial Services for Building 42
- Agency: USDA ARS, Frederick MD contracting office
- Solicitation: 1232SA26Q9999
- Notice ID: abc123def456
- Place of performance: Frederick, MD — Building 42
- Response deadline: 2026-08-15 14:00 ET
- Questions due: 2026-08-01 12:00 ET
- Site visit: None
- POP: 12 months from NTP
- Pricing format: Lump sum monthly price per CLIN

## Scope summary
- Daily cleaning of 24,000 sq ft office space, five days per week
- Restroom sanitation twice daily per PWS section 3.2
- Quarterly floor waxing per PWS section 3.5

## Price sheet
- CLIN 0001 Monthly janitorial service, 12 months
- CLIN 0002 Quarterly floor care, 4 quarters
"""

GOOD_SCOPE = """# Scope Decomposition

## Work packages
- Daily office cleaning, trash removal, and vacuuming per PWS 3.1
- Restroom sanitation and restocking per PWS 3.2
- Quarterly strip and wax of hard floors per PWS 3.5
"""

GOOD_CRITERIA = """# Sourcing Criteria

- Trade: commercial janitorial / custodial services
- Must serve Frederick, MD
- Must show commercial or government facility experience
- Exclude residential-only cleaners
"""

GOOD_MISSING = """# Missing Information

- Wage determination attachment referenced but not posted. Checked: solicitation PDF, SAM.gov attachments list. [DOCUMENT MISSING]
"""

GOOD_MANIFEST = """# Attachment Manifest

- solicitation.txt — combined solicitation and PWS text; subs need the PWS sections to price.
"""

SCAFFOLD_BRIEF = """# 02_SOLICITATION_BRIEF

## Verified solicitation facts
- Title: [USER INPUT REQUIRED]
## Missing information / human decisions
- [DOCUMENT MISSING]
"""


def make_opportunity(tmp: Path, *, complete: bool = True, drop_deadline: bool = False) -> Path:
    opp = tmp / "abc123def456-janitorial-building-42"
    opp.mkdir(parents=True, exist_ok=True)
    (opp / "source").mkdir(exist_ok=True)
    (opp / "source" / "solicitation.txt").write_text("full solicitation text", encoding="utf-8")
    if complete:
        brief = GOOD_BRIEF
        if drop_deadline:
            brief = brief.replace("- Response deadline: 2026-08-15 14:00 ET\n", "")
        (opp / "02_SOLICITATION_BRIEF.md").write_text(brief, encoding="utf-8")
        (opp / "05_SCOPE_DECOMPOSITION.md").write_text(GOOD_SCOPE, encoding="utf-8")
        (opp / "06_SUBCONTRACTOR_SOURCING_CRITERIA.md").write_text(GOOD_CRITERIA, encoding="utf-8")
        (opp / "04_MISSING_INFORMATION.md").write_text(GOOD_MISSING, encoding="utf-8")
        (opp / "attachment_manifest.md").write_text(GOOD_MANIFEST, encoding="utf-8")
    return opp


class PreflightTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_empty_folder_fails_with_blocker(self):
        opp = self.tmp_path / "empty-opp"
        opp.mkdir()
        report = preflight.run(opp)
        self.assertEqual(report["status"], "FAIL")
        self.assertTrue((opp / "research_blocker.md").exists())
        self.assertTrue((opp / "research_preflight.json").exists())
        self.assertIn("artifact_exists:02_SOLICITATION_BRIEF.md", report["failed"])
        self.assertIn("sources:present", report["failed"])

    def test_scaffold_placeholders_fail(self):
        opp = make_opportunity(self.tmp_path, complete=True)
        (opp / "02_SOLICITATION_BRIEF.md").write_text(SCAFFOLD_BRIEF, encoding="utf-8")
        report = preflight.run(opp)
        self.assertEqual(report["status"], "FAIL")
        self.assertIn("artifact_no_scaffold:02_SOLICITATION_BRIEF.md", report["failed"])
        blocker = (opp / "research_blocker.md").read_text()
        self.assertIn("USER INPUT REQUIRED", blocker)
        self.assertIn("- Title:", blocker)  # blocker teaches the exact format

    def test_markers_allowed_in_missing_information_file(self):
        opp = make_opportunity(self.tmp_path, complete=True)
        report = preflight.run(opp)
        # 04_MISSING_INFORMATION.md contains [DOCUMENT MISSING] and must not fail the run.
        self.assertEqual(report["status"], "PASS", report["failed"])

    def test_missing_deadline_fails(self):
        opp = make_opportunity(self.tmp_path, complete=True, drop_deadline=True)
        report = preflight.run(opp)
        self.assertEqual(report["status"], "FAIL")
        self.assertIn("fact:government_due_datetime", report["failed"])

    def test_generic_scope_fails(self):
        opp = make_opportunity(self.tmp_path, complete=True)
        (opp / "05_SCOPE_DECOMPOSITION.md").write_text("# Scope\n\nSee documents.\n", encoding="utf-8")
        brief_no_scope = GOOD_BRIEF.replace(
            "## Scope summary\n- Daily cleaning of 24,000 sq ft office space, five days per week\n"
            "- Restroom sanitation twice daily per PWS section 3.2\n"
            "- Quarterly floor waxing per PWS section 3.5\n", "")
        (opp / "02_SOLICITATION_BRIEF.md").write_text(brief_no_scope, encoding="utf-8")
        report = preflight.run(opp)
        self.assertEqual(report["status"], "FAIL")
        self.assertIn("fact:real_scope_items", report["failed"])

    def test_complete_research_passes_and_clears_blocker(self):
        opp = make_opportunity(self.tmp_path, complete=True)
        # Fail once to create a blocker, then fix and pass: blocker must be removed.
        (opp / "02_SOLICITATION_BRIEF.md").write_text(SCAFFOLD_BRIEF, encoding="utf-8")
        self.assertEqual(preflight.run(opp)["status"], "FAIL")
        (opp / "02_SOLICITATION_BRIEF.md").write_text(GOOD_BRIEF, encoding="utf-8")
        report = preflight.run(opp)
        self.assertEqual(report["status"], "PASS", report["failed"])
        self.assertFalse((opp / "research_blocker.md").exists())
        marker = json.loads((opp / "research_preflight.json").read_text())
        self.assertEqual(marker["status"], "PASS")
        self.assertIn("02_SOLICITATION_BRIEF.md", marker["artifact_hashes"])
        self.assertTrue(preflight.preflight_status(opp)["ok"])

    def test_pass_goes_stale_when_artifact_changes(self):
        opp = make_opportunity(self.tmp_path, complete=True)
        self.assertEqual(preflight.run(opp)["status"], "PASS")
        (opp / "02_SOLICITATION_BRIEF.md").write_text(GOOD_BRIEF + "\n- amended note\n", encoding="utf-8")
        status = preflight.preflight_status(opp)
        self.assertFalse(status["ok"])
        self.assertIn("stale", status["reason"].lower())

    def test_unparsed_pdf_is_reported_as_warning(self):
        opp = make_opportunity(self.tmp_path, complete=True)
        (opp / "source" / "drawings.pdf").write_bytes(b"%PDF-1.4 fake")
        report = preflight.run(opp)
        self.assertEqual(report["status"], "PASS")  # warning, not a hard fail
        self.assertIn("sources:pdfs_extracted", report["warnings"])


class PacketBuilderBarrierTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def run_builder(self, opp: Path, *extra: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "wfg_sub_bid_packet.py"), str(opp), *extra],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=120,
        )

    def test_builder_refuses_without_preflight_pass(self):
        opp = make_opportunity(self.tmp_path, complete=True)  # research ok but preflight never ran
        proc = self.run_builder(opp)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("REFUSED", proc.stdout)
        self.assertIn("wfg_research_preflight.py", proc.stdout)
        self.assertFalse((opp / "subcontractor_bid_packet" / "subcontractor_bid_packet.md").exists())

    def test_builder_refuses_when_pass_is_stale(self):
        opp = make_opportunity(self.tmp_path, complete=True)
        self.assertEqual(preflight.run(opp)["status"], "PASS")
        (opp / "05_SCOPE_DECOMPOSITION.md").write_text(GOOD_SCOPE + "- new item\n", encoding="utf-8")
        proc = self.run_builder(opp)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("stale", proc.stdout.lower())

    def test_builder_runs_after_pass(self):
        opp = make_opportunity(self.tmp_path, complete=True)
        self.assertEqual(preflight.run(opp)["status"], "PASS")
        proc = self.run_builder(opp)
        self.assertEqual(proc.returncode, 0, proc.stdout)
        packet = (opp / "subcontractor_bid_packet" / "subcontractor_bid_packet.md").read_text()
        self.assertIn("Janitorial Services for Building 42", packet)
        self.assertNotIn("To be set by WFG before outreach", packet)
        self.assertNotIn("See solicitation package", packet.split("\n\n")[0])

    def test_allow_incomplete_draft_is_an_explicit_escape_hatch_only(self):
        opp = self.tmp_path / "thin-opp"
        opp.mkdir()
        proc = self.run_builder(opp, "--allow-incomplete-draft")
        self.assertEqual(proc.returncode, 0, proc.stdout)


class OutreachBarrierTests(unittest.TestCase):
    def test_gate2_package_cannot_form_without_preflight_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            opp = make_opportunity(Path(tmp), complete=True)  # no preflight run
            with self.assertRaises(ValueError) as ctx:
                outreach.build_package(opp, opp / "recipients.csv", opp / "message.md")
            self.assertIn("preflight", str(ctx.exception).lower())


if __name__ == "__main__":
    unittest.main()
