#!/usr/bin/env python3
"""One-time WFG state migration + historical sent-proof ledger backfill.

Consensus plan Section 5 (docs/strategy/WFG_HERMES_DIGITAL_EMPLOYEE_CONSENSUS_PLAN.md):

- Source column: opportunities.workflow_status (the table has no 'status' column).
- Mapping: discovered -> discovered; awaiting_pursue_decision -> gate1_pending_pursue;
  outreach_approved with verified sent proof -> quotes_pending;
  outreach_approved without proof -> gate2_pending_outreach_send plus a queued
  workflow_tasks row to create a new GATE_2_SEND packet — a legacy broad Gate 2
  approval is never executed automatically.
- Ledger backfill: one external_action_ledger row per real-world contact,
  deduped across gmail_drafts / subcontractor_interactions / workflow_events by
  (opportunity, recipient email, date); sources_json records every contributing
  source row. Form contacts without a recipient email are keyed by interaction
  and marked needs_human_review.
- Decision vocabulary: approvals.decision 'rejected' -> 'denied'.
- approvals.gate_id backfilled from the exact legacy gate-text map (wfg_gates).
- --dry-run (default) writes nothing. --apply makes a timestamped DB backup,
  writes a workflow_events record per migrated opportunity, and is idempotent:
  a second --apply is a no-op.

Test fixtures (is_test_fixture=1 / environment='test') are migrated with the
same mapping but excluded from production report counts.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any

PROJECT = Path(os.environ.get("WFG_PROJECT_DIR", "/home/nick/workspace/wfg-gov-contracting-v2")).resolve()
DB = Path(os.environ.get("WFG_DB_PATH", str(PROJECT / "state" / "wfg_workflow.sqlite3"))).resolve()

sys.path.insert(0, str(Path(__file__).resolve().parent))
import wfg_gates  # noqa: E402
import wfg_tracking_schema  # noqa: E402

STATE_MAP_SIMPLE = {
    "discovered": "discovered",
    "awaiting_pursue_decision": "gate1_pending_pursue",
}

SENT_EVENT_TYPES = (
    "subcontractor_outreach_email_sent",
    "gate2_outreach_sent",
    "agency_q_and_a_sent",
    "gmail_draft_sent",
)

# Outbound interaction types that are real external contacts. web_route_check
# is a website availability probe, not a contact, and is deliberately excluded.
CONTACT_INTERACTION_TYPES = ("email", "form", "contact_form")


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def con() -> sqlite3.Connection:
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c


def date_of(ts: str | None) -> str:
    return (ts or "")[:10]


def split_recipients(raw: str | None) -> list[str]:
    out = []
    for part in (raw or "").replace(";", ",").split(","):
        addr = part.strip().lower()
        if addr and "@" in addr:
            out.append(addr)
    return out


def collect_backfill(c: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    """Build the deduped historical-contact map: canonical key -> ledger row."""
    entries: dict[str, dict[str, Any]] = {}

    def add(key: str, dedupe_key: str, action_type: str, recipient_key: str,
            recipient_email: str | None, executed_at: str | None,
            source: dict[str, Any], needs_review: bool = False,
            approval_id: str | None = None) -> None:
        e = entries.setdefault(key, {
            "dedupe_key": dedupe_key,
            "action_type": action_type,
            "recipient_key": recipient_key,
            "recipient_email": recipient_email,
            "approval_id": approval_id,
            "status": "historical_sent_proof",
            "executed_at": executed_at,
            "idempotency_key": "hist:" + hashlib.sha256(key.encode()).hexdigest()[:24],
            "sources": [],
            "needs_human_review": 1 if needs_review else 0,
        })
        e["sources"].append(source)
        if approval_id and not e.get("approval_id"):
            e["approval_id"] = approval_id
        if executed_at and (not e["executed_at"] or executed_at < e["executed_at"]):
            e["executed_at"] = executed_at

    # Source A: outbound subcontractor interactions (join contact email).
    rows = c.execute(
        """select i.id, i.dedupe_key, i.interaction_type, i.occurred_at,
                  i.subcontractor_id, i.contact_id, sc.email as contact_email
             from subcontractor_interactions i
             left join subcontractor_contacts sc on sc.id = i.contact_id
            where i.direction='outbound' and i.interaction_type in (?,?,?)""",
        CONTACT_INTERACTION_TYPES,
    ).fetchall()
    for r in rows:
        src = {"table": "subcontractor_interactions", "id": r["id"], "type": r["interaction_type"]}
        email = (r["contact_email"] or "").strip().lower()
        dk = r["dedupe_key"] or ""
        if email:
            key = f"{dk}|{email}|{date_of(r['occurred_at'])}"
            add(key, dk, "subcontractor_email" if r["interaction_type"] == "email" else "form_contact",
                email, email, r["occurred_at"], src)
        else:
            # No recipient email recorded: key by interaction so the contact is
            # still visible to duplicate checks, flagged for human review.
            key = f"{dk}|interaction:{r['id']}"
            add(key, dk, "form_contact" if r["interaction_type"] != "email" else "subcontractor_email",
                f"form:{r['subcontractor_id']}", None, r["occurred_at"], src, needs_review=True)

    # Source B: Gmail drafts that were actually sent.
    rows = c.execute(
        """select id, dedupe_key, to_recipients, sent_at, sent_message_id, status
             from gmail_drafts
            where sent_at is not null or status like '%sent%'"""
    ).fetchall()
    for r in rows:
        src = {"table": "gmail_drafts", "id": r["id"], "sent_message_id": r["sent_message_id"]}
        dk = r["dedupe_key"] or ""
        recipients = split_recipients(r["to_recipients"])
        if recipients:
            for addr in recipients:
                key = f"{dk}|{addr}|{date_of(r['sent_at'])}"
                add(key, dk, "subcontractor_email", addr, addr, r["sent_at"], src)
        else:
            key = f"{dk}|gmail_draft:{r['id']}"
            add(key, dk, "subcontractor_email", f"gmail_draft:{r['id']}", None, r["sent_at"], src, needs_review=True)

    # Source C: sent-related workflow events. Fold into existing entries when
    # the recipient email is in the details; otherwise standalone review rows.
    rows = c.execute(
        f"""select id, dedupe_key, event_type, event_at, details_json
              from workflow_events
             where event_type in ({','.join('?' for _ in SENT_EVENT_TYPES)})""",
        SENT_EVENT_TYPES,
    ).fetchall()
    for r in rows:
        try:
            details = json.loads(r["details_json"] or "{}")
        except Exception:
            details = {}
        src = {"table": "workflow_events", "id": r["id"], "event_type": r["event_type"]}
        dk = r["dedupe_key"] or ""
        sent_at = details.get("sent_at") or r["event_at"]
        to = (details.get("to") or details.get("recipient") or "").strip().lower()
        action_type = "agency_email" if r["event_type"] == "agency_q_and_a_sent" else "subcontractor_email"
        approval_id = details.get("approval_id")
        if to and "@" in to:
            key = f"{dk}|{to}|{date_of(sent_at)}"
            add(key, dk, action_type, to, to, sent_at, src, approval_id=approval_id)
            continue
        # Try folding a gmail_draft_sent event into its Source-B entry by message id.
        mid = details.get("message_id") or details.get("sent_message_id")
        folded = False
        if mid:
            for e in entries.values():
                if any(s.get("sent_message_id") == mid for s in e["sources"]):
                    e["sources"].append(src)
                    folded = True
                    break
        if not folded:
            key = f"{dk}|event:{r['id']}"
            add(key, dk, action_type, f"event:{r['id']}", None, sent_at, src,
                needs_review=True, approval_id=approval_id)

    return entries


def plan_migration(c: sqlite3.Connection) -> dict[str, Any]:
    """Compute everything the migration would do. Pure read."""
    wfg_tracking_schema.ensure_phase2_workflow_schema(c)  # gate_id column must exist to plan its backfill

    backfill = collect_backfill(c)
    proof_opportunities = {e["dedupe_key"] for e in backfill.values() if e["dedupe_key"]}

    opp_rows = c.execute(
        """select dedupe_key, workflow_status, is_test_fixture, environment
             from opportunities where workflow_status != 'discovered'
             or workflow_status is null"""
    ).fetchall()
    proposals: list[dict[str, Any]] = []
    for r in opp_rows:
        old = r["workflow_status"] or "discovered"
        if old in STATE_MAP_SIMPLE:
            new = STATE_MAP_SIMPLE[old]
        elif old == "outreach_approved":
            new = "quotes_pending" if r["dedupe_key"] in proof_opportunities else "gate2_pending_outreach_send"
        else:
            new = old
        if new != old:
            proposals.append({
                "dedupe_key": r["dedupe_key"], "from": old, "to": new,
                "is_test_fixture": int(r["is_test_fixture"] or 0),
                "environment": r["environment"] or "production",
                "sent_proof_found": r["dedupe_key"] in proof_opportunities,
                "needs_new_gate2_send_packet": new == "gate2_pending_outreach_send",
            })

    counts = {}
    for row in c.execute(
        """select workflow_status, is_test_fixture, count(*) n
             from opportunities group by 1,2"""
    ):
        bucket = "test" if row["is_test_fixture"] else "production"
        counts.setdefault(bucket, {})[row["workflow_status"]] = row["n"]

    existing_ledger = {
        r["idempotency_key"] for r in c.execute("select idempotency_key from external_action_ledger")
    }
    new_backfill = [e for e in backfill.values() if e["idempotency_key"] not in existing_ledger]

    rejected = c.execute("select count(*) n from approvals where decision='rejected'").fetchone()["n"]
    gate_id_backfillable = [
        {"id": r["id"], "gate": r["gate"], "gate_id": wfg_gates.resolve_gate_id(dict(r))}
        for r in c.execute("select id, gate, gate_id from approvals where gate_id is null or gate_id=''")
    ]
    gate_id_backfillable = [g for g in gate_id_backfillable if g["gate_id"]]

    return {
        "db": str(DB),
        "computed_at": now(),
        "status_counts": counts,
        "state_proposals": proposals,
        "ledger_backfill_total_contacts": len(backfill),
        "ledger_backfill_new_rows": len(new_backfill),
        "ledger_backfill_needs_review": sum(1 for e in backfill.values() if e["needs_human_review"]),
        "decision_rejected_to_denied": rejected,
        "approvals_gate_id_backfill": len(gate_id_backfillable),
        "_backfill_entries": backfill,
        "_new_backfill": new_backfill,
        "_gate_id_backfill": gate_id_backfillable,
    }


def backup_db() -> str:
    ts = now().replace(":", "").replace("+", "Z")
    backup_dir = DB.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    dest = backup_dir / f"wfg_workflow-pre-migration-{ts}.sqlite3"
    src = sqlite3.connect(DB)
    dst = sqlite3.connect(dest)
    with dst:
        src.backup(dst)
    src.close()
    dst.close()
    return str(dest)


def apply_migration(c: sqlite3.Connection, plan: dict[str, Any]) -> dict[str, Any]:
    applied = {"states_migrated": 0, "ledger_rows_inserted": 0, "gate2_send_tasks_queued": 0,
               "decisions_standardized": 0, "gate_ids_backfilled": 0}

    for e in plan["_new_backfill"]:
        rowid = wfg_tracking_schema.record_ledger_action(
            c,
            dedupe_key=e["dedupe_key"],
            action_type=e["action_type"],
            recipient_key=e["recipient_key"],
            recipient_email=e["recipient_email"],
            approval_id=e.get("approval_id"),
            status=e["status"],
            executed_at=e["executed_at"],
            idempotency_key=e["idempotency_key"],
            sources_json=json.dumps(e["sources"], sort_keys=True),
            needs_human_review=e["needs_human_review"],
        )
        if rowid:
            applied["ledger_rows_inserted"] += 1

    for p in plan["state_proposals"]:
        c.execute("update opportunities set workflow_status=? where dedupe_key=? and workflow_status=?",
                  (p["to"], p["dedupe_key"], p["from"]))
        c.execute(
            "insert into workflow_events(dedupe_key,event_type,event_at,actor,details_json) values(?,?,?,?,?)",
            (p["dedupe_key"], "state_migration", now(), "wfg_state_migration",
             json.dumps({"from": p["from"], "to": p["to"], "sent_proof_found": p["sent_proof_found"]}, sort_keys=True)),
        )
        applied["states_migrated"] += 1
        if p["needs_new_gate2_send_packet"]:
            cur = c.execute(
                """insert or ignore into workflow_tasks
                   (dedupe_key,role_id,task_type,current_state,input_json,idempotency_key,created_at,next_gate,environment)
                   values(?,?,?,?,?,?,?,?,?)""",
                (p["dedupe_key"], "outreach", "create_gate2_send_packet", "queued",
                 json.dumps({"reason": "legacy outreach_approved without sent proof; new GATE_2_PACKAGE/GATE_2_SEND cycle required (plan Section 5)"}),
                 f"migration:g2send:{p['dedupe_key']}", now(),
                 wfg_gates.gate_display_name("GATE_2_SEND"), p["environment"]),
            )
            if cur.rowcount:
                applied["gate2_send_tasks_queued"] += 1

    cur = c.execute("update approvals set decision='denied' where decision='rejected'")
    applied["decisions_standardized"] = cur.rowcount

    for g in plan["_gate_id_backfill"]:
        c.execute("update approvals set gate_id=? where id=? and (gate_id is null or gate_id='')",
                  (g["gate_id"], g["id"]))
        applied["gate_ids_backfilled"] += 1

    c.commit()
    return applied


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Report what would change (default).")
    mode.add_argument("--apply", action="store_true", help="Back up the DB, then apply the migration.")
    ap.add_argument("--json", action="store_true", help="Print full JSON report including per-row proposals.")
    args = ap.parse_args()

    if not DB.exists():
        print(json.dumps({"ok": False, "error": f"DB not found: {DB}"}))
        return 2

    with con() as c:
        plan = plan_migration(c)
        report = {k: v for k, v in plan.items() if not k.startswith("_")}
        if args.apply:
            report["backup_path"] = backup_db()
            report["applied"] = apply_migration(c, plan)
            report["mode"] = "apply"
            log_dir = PROJECT / "state" / "migrations"
            log_dir.mkdir(parents=True, exist_ok=True)
            (log_dir / f"state-migration-{now().replace(':','')}.json").write_text(
                json.dumps(report, indent=2, sort_keys=True, default=str), encoding="utf-8")
        else:
            report["mode"] = "dry-run"

    if not args.json:
        report["state_proposals"] = f"{len(report['state_proposals'])} rows (use --json for detail)"
    print(json.dumps(report, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
