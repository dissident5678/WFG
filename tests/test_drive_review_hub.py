#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "wfg_drive_review_hub.py"
spec = importlib.util.spec_from_file_location("wfg_drive_review_hub", SCRIPT)
hub = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hub)


class FakeCall:
    def __init__(self, payload):
        self.payload = payload

    def execute(self):
        return self.payload


class FakeMedia:
    def __init__(self, path: str, mimetype: str, resumable: bool = False):
        self.path = path
        self.mimetype = mimetype
        self.resumable = resumable


class FakeFiles:
    def __init__(self, drive: "FakeDrive"):
        self.drive = drive

    def _name_from_query(self, q: str) -> str:
        marker = "name='"
        start = q.index(marker) + len(marker)
        end = q.index("'", start)
        return q[start:end]

    def _parent_from_query(self, q: str) -> str | None:
        marker = "' in parents"
        if marker not in q:
            return None
        prefix = q[: q.index(marker)]
        start = prefix.rfind("'") + 1
        return prefix[start:]

    def list(self, q: str, fields: str, spaces: str = "drive", pageSize: int = 10):
        name = self._name_from_query(q)
        parent = self._parent_from_query(q)
        folder_only = "mimeType='application/vnd.google-apps.folder'" in q
        matches = []
        for item in self.drive.items.values():
            if item["name"] != name:
                continue
            if folder_only and item.get("mimeType") != hub.FOLDER_MIME:
                continue
            if parent and parent not in item.get("parents", []):
                continue
            matches.append(item)
        return FakeCall({"files": matches[:pageSize]})

    def create(self, body: dict, fields: str, media_body=None):
        file_id = f"id-{self.drive.next_id}"
        self.drive.next_id += 1
        item = {
            "id": file_id,
            "name": body["name"],
            "mimeType": body.get("mimeType", getattr(media_body, "mimetype", "application/octet-stream")),
            "parents": list(body.get("parents", [])),
            "webViewLink": f"https://drive.test/{file_id}",
            "updates": 0,
        }
        self.drive.items[file_id] = item
        return FakeCall(item)

    def update(self, fileId: str, media_body, fields: str):
        self.drive.items[fileId]["updates"] += 1
        return FakeCall(self.drive.items[fileId])


class FakeDrive:
    def __init__(self):
        self.items = {}
        self.next_id = 1
        self._files = FakeFiles(self)

    def files(self):
        return self._files


def write(path: Path, text: str = "x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


class DriveReviewHubTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.opp = self.root / "2026-07-07_abc123_sludge-removal"
        self.opp.mkdir(parents=True)
        self.db = self.root / "state" / "wfg_workflow.sqlite3"
        hub.DB = self.db
        write(self.opp / "subcontractor_bid_packet" / "subcontractor_bid_packet.md", "# Packet\n")
        write(self.opp / "subcontractor_bid_packet" / "subcontractor_bid_packet.docx", "docx bytes")
        write(self.opp / "subcontractor_bid_packet" / "internal_review_summary.md", "# Internal\n")
        write(self.opp / "subcontractor_bid_packet" / "source_map.json", "{}")
        write(self.opp / "approvals" / "gate2_package.md", "# Approval\nGate ID: GATE_2_PACKAGE\n")
        write(self.opp / "drafts" / "quote_request.md", "# Draft\n")

    def tearDown(self):
        self.tmp.cleanup()

    def test_config_uses_consensus_append_only_tree(self):
        config = json.loads((ROOT / "config" / "drive-review-hub.json").read_text(encoding="utf-8"))
        leaves = hub.folder_names_from_tree(config)
        self.assertEqual(leaves[:11], [
            "00 Command Snapshot",
            "01 Source Docs",
            "02 Internal Review",
            "03 Subcontractor Packet",
            "04 Approvals",
            "05 Draft Emails",
            "06 Quotes Received",
            "07 Proposal Package",
            "08 Submission Proof",
            "09 Pricing and Bid Strategy",
            "10 Decision Logs",
        ])
        self.assertIn("02 Internal Review", config["mvde_folder_names"])

    def test_collect_artifacts_keeps_internal_files_out_of_subcontractor_folder(self):
        artifacts = hub.collect_review_artifacts(self.opp, dedupe_key="notice:test")
        by_name = {Path(a["local_path"]).name: a for a in artifacts}
        self.assertEqual(by_name["command_snapshot.md"]["folder"], "00 Command Snapshot")
        self.assertEqual(by_name["subcontractor_bid_packet.md"]["folder"], "03 Subcontractor Packet")
        self.assertEqual(by_name["subcontractor_bid_packet.docx"]["audience"], "subcontractor_facing")
        self.assertEqual(by_name["internal_review_summary.md"]["folder"], "02 Internal Review")
        self.assertEqual(by_name["source_map.json"]["folder"], "02 Internal Review")
        self.assertEqual(by_name["gate2_package.md"]["folder"], "04 Approvals")
        self.assertEqual(by_name["quote_request.md"]["folder"], "05 Draft Emails")
        self.assertTrue((self.opp / "00 Command Snapshot" / "command_snapshot.md").exists())

    def test_upload_is_idempotent_and_records_artifact_index(self):
        fake = FakeDrive()
        first = hub.upload_review_bundle(
            self.opp,
            dedupe_key="notice:test",
            drive=fake,
            media_cls=FakeMedia,
            config_path=ROOT / "config" / "drive-review-hub.json",
        )
        self.assertTrue(first["ok"])
        folder_names = {item["name"] for item in fake.items.values() if item["mimeType"] == hub.FOLDER_MIME}
        for required in ["WFG Review Hub", "SAM Opportunities", "2026", self.opp.name, "00 Command Snapshot", "02 Internal Review", "03 Subcontractor Packet", "04 Approvals", "05 Draft Emails"]:
            self.assertIn(required, folder_names)
        file_count_after_first = sum(1 for item in fake.items.values() if item["mimeType"] != hub.FOLDER_MIME)

        second = hub.upload_review_bundle(
            self.opp,
            dedupe_key="notice:test",
            drive=fake,
            media_cls=FakeMedia,
            config_path=ROOT / "config" / "drive-review-hub.json",
        )
        self.assertTrue(second["ok"])
        file_count_after_second = sum(1 for item in fake.items.values() if item["mimeType"] != hub.FOLDER_MIME)
        self.assertEqual(file_count_after_first, file_count_after_second)
        self.assertTrue(any(item["updates"] > 0 for item in fake.items.values() if item["mimeType"] != hub.FOLDER_MIME))

        c = sqlite3.connect(self.db)
        try:
            count = c.execute("select count(*) from artifact_index where superseded_at is null").fetchone()[0]
            links = c.execute("select count(*) from artifact_index where drive_web_view_link like 'https://drive.test/%'").fetchone()[0]
        finally:
            c.close()
        self.assertEqual(count, len(first["uploaded_files"]))
        self.assertEqual(links, len(first["uploaded_files"]))


if __name__ == "__main__":
    unittest.main()
