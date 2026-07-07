#!/usr/bin/env python3
"""Consensus Gate 2 outreach package, send approval, and send execution worker.

Phase 5 implementation. This script prepares decisions and records proof. It
will not execute a real external send unless all of these are true:
- a valid approved GATE_2_SEND row exists for the exact package hash;
- every recipient is still in the approved recipient list;
- external_action_ledger has no prior contact for that opportunity/recipient;
- the operator passes --execute with an explicit transport.
"""
from __future__ import annotations

import argparse
import base64
import csv
import datetime as dt
import email.message
import hashlib
import json
import os
import re
import sqlite3
from pathlib import Path
from typing import Any

PROJECT = Path(os.environ.get("WFG_PROJECT_DIR", str(Path(__file__).resolve().parents[1]))).resolve()
DB = Path(os.environ.get("WFG_DB_PATH", str(PROJECT / "state" / "wfg_workflow.sqlite3"))).resolve()
PENDING_PREFIX = "[PENDING WFG GATE 2] "
VALID_EMAIL_RE = re.compile(r"^[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}$", re.I)
INVALID_MARKERS = ("to verify", "do not send", "contact form", "placeholder", "unknown", "n/a", "none", "[", "]")


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def sha256_json(data: Any) -> str:
    return sha256_bytes(json.dumps(data, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8"))


def con() -> sqlite3.Connection:
    DB.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c


def add_column(c: sqlite3.Connection, table: str, name: str, decl: str) -> None:
    cols = {r["name"] for r in c.execute(f"pragma table_info({table})")}
    if name not in cols:
        c.execute(f"alter table {table} add column {name} {decl}")


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
          recipient_list_json TEXT,
          valid INTEGER DEFAULT 1,
          details_json TEXT,
          used_at TEXT,
          decided_at TEXT,
          approver TEXT,
          conditions TEXT,
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
        CREATE TABLE IF NOT EXISTS subcontractor_interactions(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          subcontractor_id INTEGER,
          dedupe_key TEXT,
          contact_id INTEGER,
          interaction_type TEXT,
          status TEXT,
          direction TEXT,
          occurred_at TEXT,
          subject TEXT,
          local_path TEXT,
          external_id TEXT,
          notes TEXT,
          gmail_message_id TEXT,
          gmail_thread_id TEXT
        );
        CREATE TABLE IF NOT EXISTS external_action_ledger(
          action_id INTEGER PRIMARY KEY AUTOINCREMENT,
          dedupe_key TEXT,
          action_type TEXT NOT NULL,
          recipient_key TEXT,
          recipient_email TEXT,
          artifact_version TEXT,
          artifact_hash TEXT,
          approval_id TEXT,
          status TEXT NOT NULL,
          created_at TEXT NOT NULL,
          executed_at TEXT,
          proof_path TEXT,
          external_id TEXT,
          idempotency_key TEXT UNIQUE,
          sources_json TEXT,
          needs_human_review INTEGER DEFAULT 0,
          environment TEXT DEFAULT 'production'
        );
        CREATE TABLE IF NOT EXISTS gate2_outreach_packages(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          dedupe_key TEXT,
          opportunity_folder TEXT,
          packet_path TEXT,
          packet_hash TEXT,
          packet_version TEXT,
          message_path TEXT,
          message_hash TEXT,
          subject TEXT,
          recipients_json TEXT,
          package_version TEXT,
          package_hash TEXT UNIQUE,
          package_approval_id TEXT,
          send_approval_id TEXT,
          status TEXT,
          created_at TEXT,
          updated_at TEXT,
          proof_dir TEXT,
          environment TEXT DEFAULT 'production'
        );
        CREATE INDEX IF NOT EXISTS idx_gate2_packages_hash ON gate2_outreach_packages(package_hash);
        CREATE INDEX IF NOT EXISTS idx_gate2_packages_opp ON gate2_outreach_packages(dedupe_key);
        CREATE INDEX IF NOT EXISTS idx_ledger_opp_recipient ON external_action_ledger(dedupe_key, recipient_key);
        """
    )
    for col, decl in [
        ("telegram_user_id", "TEXT"),
        ("invalidated_at", "TEXT"),
        ("invalidated_reason", "TEXT"),
        ("exact_action", "TEXT"),
    ]:
        add_column(c, "approvals", col, decl)


def event(c: sqlite3.Connection, dedupe_key: str, event_type: str, details: dict[str, Any]) -> None:
    c.execute(
        "insert into workflow_events(dedupe_key,event_type,event_at,actor,details_json) values(?,?,?,?,?)",
        (dedupe_key, event_type, utc_now(), "wfg_outreach_cycle", json.dumps(details, sort_keys=True)),
    )


def valid_email(email: str) -> bool:
    s = (email or "").strip()
    if not s:
        return False
    low = s.lower()
    if any(marker in low for marker in INVALID_MARKERS):
        return False
    return bool(VALID_EMAIL_RE.match(s))


def load_recipients(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if path.suffix.lower() == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        rows = data if isinstance(data, list) else data.get("recipients", [])
    else:
        with path.open(newline="", encoding="utf-8", errors="ignore") as f:
            rows = list(csv.DictReader(f))
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        email = (row.get("email") or row.get("Email") or row.get("recipient_email") or "").strip().lower()
        company = (row.get("company") or row.get("Company") or row.get("legal_name") or "").strip()
        if not valid_email(email):
            continue
        if email in seen:
            continue
        seen.add(email)
        out.append({"email": email, "company": company, "source": row.get("source") or row.get("Source") or "", "subcontractor_id": row.get("subcontractor_id") or ""})
    return out


def extract_subject_body(path: Path) -> tuple[str, str]:
    text = path.read_text(encoding="utf-8", errors="ignore")
    subject = ""
    body_lines: list[str] = []
    for line in text.splitlines():
        if line.lower().startswith("subject:") and not subject:
            subject = line.split(":", 1)[1].strip()
            continue
        body_lines.append(line)
    body = "\n".join(body_lines).strip()
    subject = subject or "WFG subcontractor quote request"
    subject = subject.removeprefix(PENDING_PREFIX).strip()
    return subject, body


def pending_subject(subject: str) -> str:
    subject = subject.strip()
    return subject if subject.startswith(PENDING_PREFIX) else PENDING_PREFIX + subject


def infer_dedupe_key(opp: Path) -> str:
    m = re.match(r"([0-9a-f]{32})-", opp.name)
    if m:
        return "notice:" + m.group(1)
    return opp.name


def latest_packet_path(opp: Path) -> Path:
    for rel in ["subcontractor_bid_packet/subcontractor_bid_packet.docx", "subcontractor_bid_packet/subcontractor_bid_packet.md"]:
        p = opp / rel
        if p.exists():
            return p
    raise FileNotFoundError("subcontractor packet not found; run wfg_sub_bid_packet.py first")


def approval_id(prefix: str, digest: str) -> str:
    return f"{prefix}_{digest[:12]}"


def write_package_approval(opp: Path, data: dict[str, Any]) -> Path:
    out = opp / "approvals" / f"{data['package_approval_id']}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f"# APPROVAL NEEDED - Gate 2 Outreach Package",
        "",
        f"Approval ID: {data['package_approval_id']}",
        "Gate ID: GATE_2_PACKAGE",
        f"Created at: {utc_now()}",
        "Requested by agent/subagent: Outreach Coordinator",
        f"Opportunity / project: {opp.name}",
        f"Opportunity folder: `{opp}`",
        "Approval type: Gate 2 - Approve Outreach Package",
        "Current status: ready for review",
        f"Notice ID: {data['dedupe_key'].removeprefix('notice:')}",
        "Solicitation number: [see intake files]",
        f"Artifact/package version: `{data['package_version']}`",
        f"Artifact hash: `{data['package_hash']}`",
        "Recommended decision: approve if packet, recipients, and message are correct; otherwise revise the named component.",
        "Invalidation condition: packet, recipient list, message body, message subject, or source package changes.",
        "",
        "## Exact action requiring authorization",
        "",
        "Approve this exact outreach package for a separate GATE_2_SEND decision. This does not send anything.",
        "",
        "## Exact item being approved",
        "",
        f"- Packet: `{data['packet_path']}`",
        f"- Packet hash: `{data['packet_hash']}`",
        f"- Message: `{data['message_path']}`",
        f"- Message hash: `{data['message_hash']}`",
        f"- Subject for review: {pending_subject(data['subject'])}",
        "",
        "## Recipients",
    ]
    for r in data["recipients"]:
        lines.append(f"- {r.get('company') or '(company unknown)'} <{r['email']}>")
    lines += [
        "",
        "## Files / documents / emails / drafts Nick should review",
        f"- `{data['packet_path']}` - subcontractor-facing packet",
        f"- `{data['message_path']}` - exact outreach message text",
        "",
        "## Important risks",
        "- No send occurs from this approval. GATE_2_SEND is still required.",
        "- Prior-contact disclosures must be reviewed before GATE_2_SEND.",
        "",
        "## Approval log location",
        "`approvals/decision-log.md`",
        "",
    ]
    out.write_text("\n".join(lines), encoding="utf-8")
    return out


def write_send_approval(opp: Path, data: dict[str, Any], duplicate_report: list[dict[str, Any]]) -> Path:
    out = opp / "approvals" / f"{data['send_approval_id']}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# APPROVAL NEEDED - Gate 2 Send Outreach",
        "",
        f"Approval ID: {data['send_approval_id']}",
        "Gate ID: GATE_2_SEND",
        f"Created at: {utc_now()}",
        "Requested by agent/subagent: Outreach Coordinator",
        f"Opportunity / project: {opp.name}",
        f"Opportunity folder: `{opp}`",
        "Approval type: Gate 2-SEND - Approve Sending Outreach",
        "Current status: ready for review",
        f"Notice ID: {data['dedupe_key'].removeprefix('notice:')}",
        "Solicitation number: [see intake files]",
        f"Artifact/package version: `{data['package_version']}`",
        f"Artifact hash: `{data['package_hash']}`",
        "Recommended decision: approve only if this exact package should be sent now.",
        "Invalidation condition: packet, recipient list, message body, message subject, or source package changes.",
        "",
        "## Exact action requiring authorization",
        "",
        "Send the exact approved outreach package to the exact approved recipients once each, after the ledger check.",
        "",
        "## Exact item being approved",
        "",
        f"- GATE_2_PACKAGE approval ID: `{data['package_approval_id']}`",
        f"- Packet hash: `{data['packet_hash']}`",
        f"- Message hash: `{data['message_hash']}`",
        "",
        "## Recipients and duplicate check",
    ]
    for r in data["recipients"]:
        dup = next((d for d in duplicate_report if d["email"] == r["email"]), None)
        note = f" - PRIOR CONTACT: {dup['status']} {dup.get('executed_at') or ''}" if dup else ""
        lines.append(f"- {r.get('company') or '(company unknown)'} <{r['email']}>{note}")
    lines += [
        "",
        "## Important risks",
        "- The send worker must stop on any duplicate ledger row.",
        "- Changed packet/message/recipients require a new GATE_2_PACKAGE.",
        "",
        "## Approval log location",
        "`approvals/decision-log.md`",
        "",
    ]
    out.write_text("\n".join(lines), encoding="utf-8")
    return out


def insert_approval(c: sqlite3.Connection, data: dict[str, Any], approval_id_value: str, gate_id: str, record_path: Path, exact_action: str) -> None:
    c.execute(
        """insert or ignore into approvals(
             approval_id,dedupe_key,gate,gate_id,requested_at,decision,record_path,
             artifact_version,artifact_hash,recipient_list_json,valid,details_json,exact_action,environment)
           values(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            approval_id_value,
            data["dedupe_key"],
            gate_id,
            gate_id,
            utc_now(),
            "pending",
            str(record_path),
            data["package_version"],
            data["package_hash"],
            json.dumps(data["recipients"], sort_keys=True),
            1,
            json.dumps(data, sort_keys=True),
            exact_action,
            os.environ.get("WFG_ENV", "production"),
        ),
    )


def build_package(opp: Path, recipients_path: Path, message_path: Path, *, dedupe_key: str = "") -> dict[str, Any]:
    opp = opp.resolve()
    dedupe_key = dedupe_key or infer_dedupe_key(opp)
    recipients = load_recipients(recipients_path)
    if not recipients:
        raise ValueError("no valid recipients found; placeholder/contact-form recipients are blocked")
    packet = latest_packet_path(opp)
    subject, body = extract_subject_body(message_path)
    message_hash = sha256_bytes(body.encode("utf-8"))
    packet_hash = sha256_file(packet)
    package_payload = {
        "dedupe_key": dedupe_key,
        "packet_path": str(packet),
        "packet_hash": packet_hash,
        "packet_version": f"packet-{packet_hash[:12]}",
        "message_path": str(message_path.resolve()),
        "message_hash": message_hash,
        "subject": subject,
        "recipients": recipients,
    }
    package_hash = sha256_json(package_payload)
    package_payload["package_hash"] = package_hash
    package_payload["package_version"] = f"gate2pkg-{package_hash[:12]}"
    package_payload["package_approval_id"] = approval_id("g2pkg", package_hash)
    package_payload["send_approval_id"] = approval_id("g2send", package_hash)
    packet_approval = write_package_approval(opp, package_payload)
    with con() as c:
        ensure_schema(c)
        c.execute(
            """insert or ignore into gate2_outreach_packages(
                 dedupe_key,opportunity_folder,packet_path,packet_hash,packet_version,
                 message_path,message_hash,subject,recipients_json,package_version,package_hash,
                 package_approval_id,send_approval_id,status,created_at,updated_at,environment)
               values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                dedupe_key,
                str(opp),
                str(packet),
                packet_hash,
                package_payload["packet_version"],
                str(message_path.resolve()),
                message_hash,
                subject,
                json.dumps(recipients, sort_keys=True),
                package_payload["package_version"],
                package_hash,
                package_payload["package_approval_id"],
                package_payload["send_approval_id"],
                "gate2_package_pending",
                utc_now(),
                utc_now(),
                os.environ.get("WFG_ENV", "production"),
            ),
        )
        insert_approval(c, package_payload, package_payload["package_approval_id"], "GATE_2_PACKAGE", packet_approval, "Approve packet + recipients + message as an outreach package. Does not send.")
        event(c, dedupe_key, "gate2_package_created", {"package_version": package_payload["package_version"], "approval_id": package_payload["package_approval_id"], "packet": str(packet_approval)})
        c.commit()
    return {**package_payload, "approval_packet": str(packet_approval), "pending_subject": pending_subject(subject)}


def get_package(c: sqlite3.Connection, package_version_or_hash: str) -> dict[str, Any]:
    row = c.execute(
        "select * from gate2_outreach_packages where package_version=? or package_hash=? order by id desc limit 1",
        (package_version_or_hash, package_version_or_hash),
    ).fetchone()
    if not row:
        raise ValueError(f"Gate 2 package not found: {package_version_or_hash}")
    data = dict(row)
    data["recipients"] = json.loads(data.pop("recipients_json") or "[]")
    return data


def approval_is_approved(c: sqlite3.Connection, approval_id_value: str, gate_id: str, package_hash: str) -> bool:
    row = c.execute(
        """select * from approvals
            where approval_id=? and gate_id=? and decision='approved' and valid=1
              and artifact_hash=? order by id desc limit 1""",
        (approval_id_value, gate_id, package_hash),
    ).fetchone()
    return bool(row)


def ledger_block(c: sqlite3.Connection, dedupe_key: str, email: str) -> dict[str, Any] | None:
    row = c.execute(
        """select * from external_action_ledger
            where dedupe_key=? and recipient_key=?
              and status in ('executed','historical_sent_proof')
            order by action_id asc limit 1""",
        (dedupe_key, email.lower()),
    ).fetchone()
    return dict(row) if row else None


def create_send_approval(package_version_or_hash: str) -> dict[str, Any]:
    with con() as c:
        ensure_schema(c)
        data = get_package(c, package_version_or_hash)
        if not approval_is_approved(c, data["package_approval_id"], "GATE_2_PACKAGE", data["package_hash"]):
            return {"ok": False, "reason": "GATE_2_PACKAGE is not approved for this exact package hash"}
        duplicates = []
        for r in data["recipients"]:
            prior = ledger_block(c, data["dedupe_key"], r["email"])
            if prior:
                duplicates.append({"email": r["email"], "status": prior.get("status"), "executed_at": prior.get("executed_at"), "action_id": prior.get("action_id")})
        packet = write_send_approval(Path(data["opportunity_folder"]), data, duplicates)
        insert_approval(c, data, data["send_approval_id"], "GATE_2_SEND", packet, "Send exact approved outreach package to exact approved recipients after ledger check.")
        c.execute("update gate2_outreach_packages set send_approval_id=?, status=?, updated_at=? where package_hash=?", (data["send_approval_id"], "gate2_send_pending", utc_now(), data["package_hash"]))
        event(c, data["dedupe_key"], "gate2_send_approval_created", {"package_version": data["package_version"], "approval_id": data["send_approval_id"], "duplicates": duplicates})
        c.commit()
    return {"ok": True, "send_approval_packet": str(packet), "send_approval_id": data["send_approval_id"], "duplicates": duplicates}


def send_with_gmail(recipient: str, subject: str, body: str, attachment: Path | None = None) -> dict[str, Any]:
    if os.environ.get("WFG_ALLOW_REAL_SEND") != "1":
        raise RuntimeError("real Gmail send requires WFG_ALLOW_REAL_SEND=1")
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build

    token = Path(os.environ.get("GOOGLE_TOKEN_PATH", "/home/nick/.hermes/google_token.json"))
    scopes = ["https://www.googleapis.com/auth/gmail.send"]
    creds = Credentials.from_authorized_user_file(str(token), scopes=scopes)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
    svc = build("gmail", "v1", credentials=creds, cache_discovery=False)
    msg = email.message.EmailMessage()
    msg["To"] = recipient
    msg["From"] = os.environ.get("WFG_GMAIL_FROM", "wrightfostergroup@gmail.com")
    msg["Subject"] = subject
    msg.set_content(body)
    if attachment and attachment.exists():
        msg.add_attachment(attachment.read_bytes(), maintype="application", subtype="octet-stream", filename=attachment.name)
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    sent = svc.users().messages().send(userId="me", body={"raw": raw}).execute()
    return {"external_id": sent.get("id"), "thread_id": sent.get("threadId"), "transport": "gmail"}


def execute_send(package_version_or_hash: str, *, execute: bool = False, transport: str = "mock") -> dict[str, Any]:
    with con() as c:
        ensure_schema(c)
        data = get_package(c, package_version_or_hash)
        if not approval_is_approved(c, data["send_approval_id"], "GATE_2_SEND", data["package_hash"]):
            return {"ok": False, "sent": 0, "reason": "GATE_2_SEND is not approved for this exact package hash"}
        subject, body = extract_subject_body(Path(data["message_path"]))
        packet = Path(data["packet_path"])
        proof_dir = Path(data["opportunity_folder"]) / "outreach_proof" / data["package_version"]
        proof_dir.mkdir(parents=True, exist_ok=True)
        results = []
        for r in data["recipients"]:
            email = r["email"].lower()
            prior = ledger_block(c, data["dedupe_key"], email)
            if prior:
                results.append({"email": email, "sent": False, "blocked": True, "reason": "duplicate_ledger_block", "prior_action_id": prior.get("action_id")})
                continue
            if not execute:
                results.append({"email": email, "sent": False, "dry_run": True})
                continue
            if transport == "mock":
                transport_result = {"external_id": f"mock-{sha256_json([data['package_hash'], email])[:16]}", "transport": "mock"}
            elif transport == "gmail":
                transport_result = send_with_gmail(email, subject, body, packet)
            else:
                raise ValueError(f"unsupported transport {transport!r}")
            proof_path = proof_dir / f"{email.replace('@','_at_')}.json"
            proof = {"recipient": r, "subject": subject, "message_hash": data["message_hash"], "packet_hash": data["packet_hash"], "approval_id": data["send_approval_id"], "executed_at": utc_now(), **transport_result}
            proof_path.write_text(json.dumps(proof, indent=2, sort_keys=True), encoding="utf-8")
            idem = f"send:{data['send_approval_id']}:{email}:{data['message_hash']}:{data['packet_hash']}"
            c.execute(
                """insert or ignore into external_action_ledger(
                     dedupe_key,action_type,recipient_key,recipient_email,artifact_version,artifact_hash,
                     approval_id,status,created_at,executed_at,proof_path,external_id,idempotency_key,sources_json,environment)
                   values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (data["dedupe_key"], "subcontractor_email", email, email, data["package_version"], data["package_hash"], data["send_approval_id"], "executed", utc_now(), proof["executed_at"], str(proof_path), transport_result.get("external_id"), idem, json.dumps([{"script": "wfg_outreach_cycle", "transport": transport}], sort_keys=True), os.environ.get("WFG_ENV", "production")),
            )
            c.execute(
                """insert into subcontractor_interactions(
                     subcontractor_id,dedupe_key,interaction_type,status,direction,occurred_at,subject,local_path,external_id,notes)
                   values(?,?,?,?,?,?,?,?,?,?)""",
                (int(r["subcontractor_id"]) if str(r.get("subcontractor_id") or "").isdigit() else None, data["dedupe_key"], "email", "sent", "outbound", proof["executed_at"], subject, str(proof_path), transport_result.get("external_id"), "Sent by approved Gate 2 send worker"),
            )
            results.append({"email": email, "sent": True, "proof_path": str(proof_path), **transport_result})
        sent_count = sum(1 for x in results if x.get("sent"))
        if sent_count:
            c.execute("update gate2_outreach_packages set status=?, proof_dir=?, updated_at=? where package_hash=?", ("outreach_sent", str(proof_dir), utc_now(), data["package_hash"]))
            event(c, data["dedupe_key"], "gate2_outreach_sent", {"package_version": data["package_version"], "sent_count": sent_count, "transport": transport})
        c.commit()
    return {"ok": True, "execute": execute, "transport": transport, "sent": sent_count, "results": results}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("build-package")
    a.add_argument("opportunity_folder", type=Path)
    a.add_argument("--recipients", type=Path, required=True)
    a.add_argument("--message", type=Path, required=True)
    a.add_argument("--dedupe-key", default="")
    a = sub.add_parser("create-send-approval")
    a.add_argument("package")
    a = sub.add_parser("execute-send")
    a.add_argument("package")
    a.add_argument("--execute", action="store_true")
    a.add_argument("--transport", choices=["mock", "gmail"], default="mock")
    args = ap.parse_args()
    if args.cmd == "build-package":
        out = build_package(args.opportunity_folder, args.recipients, args.message, dedupe_key=args.dedupe_key)
    elif args.cmd == "create-send-approval":
        out = create_send_approval(args.package)
    elif args.cmd == "execute-send":
        out = execute_send(args.package, execute=args.execute, transport=args.transport)
    else:
        return 2
    print(json.dumps(out, indent=2, sort_keys=True, default=str))
    return 0 if out.get("ok", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
