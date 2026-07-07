#!/usr/bin/env python3
"""Assemble version-bound WFG proposal packages and approval packets.

Phase 6 implementation. This creates review artifacts only. It never submits,
signs, certifies, or approves price.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import sqlite3
import zipfile
from pathlib import Path
from typing import Any

PROJECT = Path(os.environ.get("WFG_PROJECT_DIR", str(Path(__file__).resolve().parents[1]))).resolve()
DB = Path(os.environ.get("WFG_DB_PATH", str(PROJECT / "state" / "wfg_workflow.sqlite3"))).resolve()


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_json(data: Any) -> str:
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def con() -> sqlite3.Connection:
    DB.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c


def ensure_schema(c: sqlite3.Connection) -> None:
    c.executescript(
        """
        CREATE TABLE IF NOT EXISTS approvals(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          approval_id TEXT,
          dedupe_key TEXT,
          gate TEXT,
          gate_id TEXT,
          requested_at TEXT,
          decision TEXT,
          record_path TEXT,
          artifact_version TEXT,
          artifact_hash TEXT,
          valid INTEGER DEFAULT 1,
          details_json TEXT,
          exact_action TEXT,
          environment TEXT DEFAULT 'production'
        );
        CREATE TABLE IF NOT EXISTS proposal_packages(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          dedupe_key TEXT,
          package_version TEXT,
          package_hash TEXT,
          created_at TEXT,
          folder TEXT,
          compliance_run_version TEXT,
          pricing_version TEXT,
          status TEXT,
          submission_proof_path TEXT,
          submitted_at TEXT,
          environment TEXT DEFAULT 'production'
        );
        CREATE TABLE IF NOT EXISTS workflow_events(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          dedupe_key TEXT,
          event_type TEXT,
          event_at TEXT,
          actor TEXT,
          details_json TEXT
        );
        """
    )


def event(c: sqlite3.Connection, dedupe_key: str, event_type: str, details: dict[str, Any]) -> None:
    c.execute(
        "insert into workflow_events(dedupe_key,event_type,event_at,actor,details_json) values(?,?,?,?,?)",
        (dedupe_key, event_type, utc_now(), "wfg_proposal_assembler", json.dumps(details, sort_keys=True)),
    )


def infer_dedupe_key(opp: Path) -> str:
    return opp.name


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def approval_packet(opp: Path, gate_id: str, approval_id: str, package_version: str, package_hash: str, exact_action: str, review_files: list[Path], hash_confirmation: bool = False) -> Path:
    out = opp / "approvals" / f"{approval_id}.md"
    lines = [
        f"# APPROVAL NEEDED - {gate_id}",
        "",
        f"Approval ID: {approval_id}",
        f"Gate ID: {gate_id}",
        f"Created at: {utc_now()}",
        "Requested by agent/subagent: Proposal Assembler",
        f"Opportunity / project: {opp.name}",
        f"Opportunity folder: `{opp}`",
        f"Approval type: {gate_id}",
        "Current status: ready for review",
        f"Notice ID: {infer_dedupe_key(opp).removeprefix('notice:')}",
        "Solicitation number: [see proposal manifest]",
        f"Artifact/package version: `{package_version}`",
        f"Artifact hash: `{package_hash}`",
        "Recommended decision: review exact files and approve only if ready.",
        "Invalidation condition: any proposal, pricing, compliance, form, source, or submission-method change.",
        "",
        "## Exact action requiring authorization",
        "",
        exact_action,
        "",
        "## Files / documents / emails / drafts Nick should review",
    ]
    for p in review_files:
        lines.append(f"- `{p}`")
    if hash_confirmation:
        lines += [
            "",
            "## Elevated confirmation required",
            "",
            f"Reply exactly: `APPROVE GATE_5 {package_hash[:8]}`",
            "Hermes must not submit automatically; this only authorizes the human submission handoff.",
        ]
    lines += [
        "",
        "## Important risks",
        "- Final package approval does not imply submission unless Gate 5 explicitly says so.",
        "- Submission proof is required before state becomes `submitted_by_human`.",
        "",
    ]
    return write(out, "\n".join(lines))


def insert_approval(c: sqlite3.Connection, dedupe_key: str, gate_id: str, approval_id: str, record_path: Path, version: str, package_hash: str, exact_action: str) -> None:
    c.execute(
        """insert or ignore into approvals(
             approval_id,dedupe_key,gate,gate_id,requested_at,decision,record_path,
             artifact_version,artifact_hash,valid,details_json,exact_action,environment)
           values(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (approval_id, dedupe_key, gate_id, gate_id, utc_now(), "pending", str(record_path), version, package_hash, 1, json.dumps({"gate_id": gate_id, "record_path": str(record_path)}, sort_keys=True), exact_action, os.environ.get("WFG_ENV", "production")),
    )


def assemble(opp: Path, *, dedupe_key: str = "", pricing_version: str = "", compliance_version: str = "") -> dict[str, Any]:
    opp = opp.resolve()
    dedupe_key = dedupe_key or infer_dedupe_key(opp)
    folder = opp / "07 Proposal Package"
    folder.mkdir(parents=True, exist_ok=True)
    files = [
        write(folder / "pricing_schedule.md", f"# Pricing Schedule\n\nPricing version: `{pricing_version or '[PRICE NOT APPROVED]'}`\n\n[USER INPUT REQUIRED] Map approved price to solicitation CLINs.\n"),
        write(folder / "technical_proposal.md", "# Technical Proposal Draft\n\nNo fabricated past performance, employees, equipment, licenses, certifications, bonding capacity, insurance, commitments, projects, or references.\n"),
        write(folder / "required_forms_checklist.md", "# Required Forms Checklist\n\n- [ ] Solicitation forms reviewed\n- [ ] Reps/certs reviewed by authorized human\n- [ ] Signature blocks verified\n"),
        write(folder / "compliance_matrix.md", f"# Compliance Matrix\n\nCompliance run: `{compliance_version or '[COMPLIANCE REVIEW REQUIRED]'}`\n\n[LEGAL OR COMPLIANCE REVIEW REQUIRED] before final package approval.\n"),
        write(folder / "red_team_review.md", "# Red Team Review\n\n- [ ] Price/source hashes match approved versions\n- [ ] Attachments complete\n- [ ] No unapproved certification claims\n- [ ] Submission instructions verified\n"),
        write(folder / "submission_checklist.md", "# Human Submission Checklist\n\n- [ ] Gate 4 final package approved\n- [ ] Gate 5 hash-confirmed approval received\n- [ ] Authorized human submits by the exact method\n- [ ] Proof archived before state change\n"),
    ]
    manifest_payload = {
        "created_at": utc_now(),
        "dedupe_key": dedupe_key,
        "opportunity_folder": str(opp),
        "pricing_version": pricing_version,
        "compliance_version": compliance_version,
        "files": [{"path": str(p), "sha256": sha256_file(p)} for p in files],
        "human_submission_only": True,
    }
    package_hash = sha256_json(manifest_payload)
    package_version = f"proposal-{package_hash[:12]}"
    manifest_payload["package_hash"] = package_hash
    manifest_payload["package_version"] = package_version
    manifest = write(folder / "proposal_manifest.json", json.dumps(manifest_payload, indent=2, sort_keys=True))
    zip_path = folder / f"{package_version}.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for p in files + [manifest]:
            z.write(p, p.relative_to(folder))
    package_hash = sha256_file(zip_path)
    package_version = f"proposal-{package_hash[:12]}"
    manifest_payload["package_hash"] = package_hash
    manifest_payload["package_version"] = package_version
    manifest.write_text(json.dumps(manifest_payload, indent=2, sort_keys=True), encoding="utf-8")

    strategy_id = f"g3strategy_{package_hash[:12]}"
    gate4_id = f"g4package_{package_hash[:12]}"
    gate5_id = f"g5submit_{package_hash[:12]}"
    gate3 = approval_packet(opp, "GATE_3_STRATEGY", strategy_id, package_version, package_hash, "Approve this pricing/basis-of-bid strategy for proposal assembly. This does not submit.", [manifest, folder / "pricing_schedule.md", folder / "compliance_matrix.md"])
    gate4 = approval_packet(opp, "GATE_4_PACKAGE", gate4_id, package_version, package_hash, "Approve this exact proposal package as final for submission preparation. This does not submit.", files + [manifest, zip_path])
    gate5 = approval_packet(opp, "GATE_5_SUBMIT", gate5_id, package_version, package_hash, "Authorize the human submission handoff for this exact package and method. Hermes does not submit.", [zip_path, folder / "submission_checklist.md", manifest], hash_confirmation=True)

    with con() as c:
        ensure_schema(c)
        c.execute(
            """insert or ignore into proposal_packages(
                 dedupe_key,package_version,package_hash,created_at,folder,
                 compliance_run_version,pricing_version,status,environment)
               values(?,?,?,?,?,?,?,?,?)""",
            (dedupe_key, package_version, package_hash, utc_now(), str(folder), compliance_version, pricing_version, "assembled_pending_approval", os.environ.get("WFG_ENV", "production")),
        )
        insert_approval(c, dedupe_key, "GATE_3_STRATEGY", strategy_id, gate3, package_version, package_hash, "Approve bid strategy and basis-of-bid inputs.")
        insert_approval(c, dedupe_key, "GATE_4_PACKAGE", gate4_id, gate4, package_version, package_hash, "Approve final proposal package. Does not submit.")
        insert_approval(c, dedupe_key, "GATE_5_SUBMIT", gate5_id, gate5, package_version, package_hash, f"Approve human submission only with hash prefix {package_hash[:8]}.")
        event(c, dedupe_key, "proposal_package_assembled", {"package_version": package_version, "package_hash": package_hash, "folder": str(folder)})
        c.commit()
    return {
        "ok": True,
        "package_version": package_version,
        "package_hash": package_hash,
        "folder": str(folder),
        "zip": str(zip_path),
        "manifest": str(manifest),
        "gate3_strategy_packet": str(gate3),
        "gate4_package_packet": str(gate4),
        "gate5_submit_packet": str(gate5),
        "human_submission_only": True,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("opportunity_folder", type=Path)
    ap.add_argument("--dedupe-key", default="")
    ap.add_argument("--pricing-version", default="")
    ap.add_argument("--compliance-version", default="")
    args = ap.parse_args()
    out = assemble(args.opportunity_folder, dedupe_key=args.dedupe_key, pricing_version=args.pricing_version, compliance_version=args.compliance_version)
    print(json.dumps(out, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
