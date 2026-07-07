#!/usr/bin/env python3
"""Create Gmail drafts for approved/local WFG markdown outreach drafts.

This wrapper is for manual/cron use. It scans opportunity folders for
`07_DRAFT_OUTREACH.md` and `scope_sheets/subcontractor_candidates.csv`, then
creates Gmail drafts for verified-looking email addresses only. It never sends.

Important safety behavior:
- Invalid placeholder recipients are skipped, not passed to Gmail.
- The generated subcontractor bid packet DOCX is preferred as an attachment when present.
- Internal review files are never attached to subcontractor-facing drafts.
"""
from __future__ import annotations

import csv
import json
import os
import re
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(os.environ.get("WFG_PROJECT_DIR", "/home/nick/workspace/wfg-gov-contracting-v2")).resolve()
DB = Path(os.environ.get("WFG_DB_PATH", str(ROOT / "state/wfg_workflow.sqlite3"))).resolve()
VALID_EMAIL_RE = re.compile(r"^[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}$", re.I)
INVALID_MARKERS = ("to verify", "do not send", "contact form", "placeholder", "unknown", "n/a", "none", "[", "]")


def con() -> sqlite3.Connection:
    DB.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c


def valid_email(value: str) -> bool:
    s = (value or "").strip()
    if not s:
        return False
    low = s.lower()
    if any(marker in low for marker in INVALID_MARKERS):
        return False
    return bool(VALID_EMAIL_RE.match(s))


def infer_dedupe(folder: Path) -> str:
    try:
        c = con()
        r = c.execute("select dedupe_key from opportunity_intakes where opportunity_folder=? order by id desc limit 1", (str(folder),)).fetchone()
        c.close()
        if r:
            return r["dedupe_key"]
    except Exception:
        pass
    m = re.match(r"([0-9a-f]{32})-", folder.name)
    return "notice:" + m.group(1) if m else ""


def existing(source: Path, to: str, subject: str) -> bool:
    try:
        c = con()
        r = c.execute(
            'select 1 from gmail_drafts where body_source_path=? and to_recipients=? and subject=? and status like "draft_created%"',
            (str(source), to, subject),
        ).fetchone()
        c.close()
        return bool(r)
    except Exception:
        return False


def extract_body_and_subject(src: Path, folder: Path) -> tuple[str, str]:
    body_md = src.read_text(errors="ignore")
    subject = ""
    m = re.search(r"^Subject:\s*(.+)$", body_md, flags=re.I | re.M)
    if m:
        subject = m.group(1).strip()
    if not subject:
        title = folder.name.replace("-", " ").strip().title()
        subject = f"Quote Request - {title}"
    m = re.search(r"##\s+Draft email\s*\n([\s\S]*)", body_md, flags=re.I)
    body = (m.group(1).strip() if m else body_md.strip())
    body = re.sub(r"^Subject:\s*.+$\n?", "", body, flags=re.I | re.M).strip()
    return body, subject


def packet_attachments(folder: Path) -> list[str]:
    candidates = [
        folder / "subcontractor_bid_packet/subcontractor_bid_packet.docx",
        folder / "subcontractor_bid_packet/subcontractor_bid_packet.md",
    ]
    # Never attach internal review/source map/data files to subcontractors.
    return [str(p) for p in candidates if p.exists()]


def create_draft(cmd: list[str]) -> dict[str, Any]:
    proc = subprocess.run(cmd, cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=180)
    if proc.returncode != 0:
        return {"ok": False, "error": proc.stdout.strip(), "command": cmd}
    try:
        data = json.loads(proc.stdout)
        data["ok"] = True
        return data
    except Exception:
        return {"ok": False, "error": "Gmail draft command returned non-JSON output", "output": proc.stdout.strip(), "command": cmd}


def main() -> int:
    created: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    opp_root = ROOT / "opportunities"
    if not opp_root.exists():
        print(json.dumps({"ok": True, "created": [], "skipped": [], "errors": [], "note": f"No opportunities folder at {opp_root}"}, indent=2))
        return 0

    for folder in sorted(opp_root.glob("*")):
        src = folder / "07_DRAFT_OUTREACH.md"
        csvp = folder / "scope_sheets/subcontractor_candidates.csv"
        if not src.exists() or not csvp.exists():
            continue
        body, subject = extract_body_and_subject(src, folder)
        attachments = packet_attachments(folder)
        dedupe = infer_dedupe(folder)
        with csvp.open(newline="", errors="ignore") as f:
            for row in csv.DictReader(f):
                raw_email = row.get("email") or row.get("Email") or ""
                emails = [e.strip() for e in re.split(r"[;,]", raw_email) if e.strip()]
                if not emails:
                    skipped.append({"folder": str(folder), "company": row.get("company") or row.get("Company") or "", "reason": "no_email"})
                    continue
                for email in emails:
                    company = (row.get("company") or row.get("Company") or "").strip()
                    if not valid_email(email):
                        skipped.append({"folder": str(folder), "company": company, "email": email, "reason": "invalid_or_placeholder_email"})
                        continue
                    if existing(src, email, subject):
                        skipped.append({"folder": str(folder), "company": company, "email": email, "reason": "draft_already_exists"})
                        continue
                    greeting = f"Hello {company} team," if company else "Hello,"
                    personalized = re.sub(r"^Hello,", greeting, body, count=1, flags=re.I | re.M)
                    cmd = [
                        sys.executable,
                        "scripts/wfg_gmail_drafts.py",
                        "create",
                        "--to", email,
                        "--subject", subject,
                        "--body", personalized,
                        "--dedupe-key", dedupe,
                        "--body-source-path", str(src),
                        "--notes", f"Auto-created Gmail draft for {company}; draft only, not sent.",
                    ]
                    for a in attachments:
                        cmd += ["--attachment", a]
                    out = create_draft(cmd)
                    out["company"] = company
                    out["source"] = str(src)
                    if out.get("ok"):
                        created.append(out)
                    else:
                        errors.append(out)
    print(json.dumps({"ok": not errors, "created": created, "skipped": skipped, "errors": errors}, indent=2, sort_keys=True))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
