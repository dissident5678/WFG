#!/usr/bin/env python3
"""Reconcile WFG Telegram approval-button decisions into workflow DB/files.

The Telegram callback handler records button decisions in approvals/button-registry.json
and decision-log.md. This script makes those decisions visible to the WFG workflow DB
used by intake/approval automation, and moves central pending packets to approved/closed.

It is idempotent and safe to run frequently. It prints one line per reconciliation action;
no output means nothing changed.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import shutil
import sqlite3
from pathlib import Path

ROOT = Path('/home/nick/workspace/wfg-gov-contracting-v2')
DB = ROOT / 'state' / 'wfg_workflow.sqlite3'
REGISTRY = ROOT / 'approvals' / 'button-registry.json'
PENDING = ROOT / 'approvals' / 'pending'
APPROVED = ROOT / 'approvals' / 'approved'
CLOSED = ROOT / 'approvals' / 'closed'


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')


def read_registry() -> dict:
    if not REGISTRY.exists():
        return {}
    try:
        data = json.loads(REGISTRY.read_text(errors='ignore') or '{}')
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def field(text: str, label: str) -> str:
    m = re.search(rf'^{re.escape(label)}:\s*(.+)$', text, re.M)
    return m.group(1).strip().strip('`') if m else ''


def parse_packet(path: Path) -> dict[str, str]:
    text = path.read_text(errors='ignore')
    return {
        'approval_id': field(text, 'Approval ID'),
        'notice_id': field(text, 'Notice ID'),
        'solicitation': field(text, 'Solicitation number'),
        'artifact_version': field(text, 'Artifact/package version'),
        'artifact_hash': field(text, 'Artifact hash'),
        'approval_type': field(text, 'Approval type'),
        'title': field(text, 'Opportunity / project') or path.stem,
    }


def target_dir(status: str) -> Path:
    return APPROVED if status == 'approved' else CLOSED


def move_packet(packet: Path, status: str) -> None:
    dest_dir = target_dir(status)
    dest_dir.mkdir(parents=True, exist_ok=True)
    pending = PENDING / packet.name
    dest = dest_dir / packet.name
    source = pending if pending.exists() else packet
    if source.exists() and not dest.exists():
        shutil.copy2(source, dest)
    if pending.exists():
        pending.unlink()


def gate_transition(approval_type: str, status: str) -> str | None:
    if status != 'approved':
        return 'passed' if 'Pursue' in approval_type else None
    if 'Pursue' in approval_type:
        return 'pursuing'
    if 'Outreach' in approval_type:
        return 'outreach_approved'
    if 'Price' in approval_type:
        return 'proposal_in_progress'
    if 'Submission' in approval_type:
        return 'awaiting_submission_approval'
    return None


def gate_token(approval_type: str) -> str:
    text = approval_type or ''
    if 'Pursue' in text:
        return 'Pursue'
    if 'Outreach' in text or 'outreach' in text:
        return 'Outreach'
    if 'Price' in text or 'price' in text:
        return 'Price'
    if 'Submission' in text or 'submission' in text:
        return 'Submission'
    return ''


def main() -> int:
    reg = read_registry()
    if not reg or not DB.exists():
        return 0
    changed: list[str] = []
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    for button_id, rec in sorted(reg.items()):
        status = rec.get('status')
        if status not in {'approved', 'denied'}:
            continue
        packet = Path(rec.get('resolved_packet_path') or rec.get('packet_path') or '')
        if not packet.exists():
            continue
        info = parse_packet(packet)
        notice = info.get('notice_id')
        version = info.get('artifact_version')
        if not notice or not version:
            continue
        dedupe = 'notice:' + notice
        decision = 'approved' if status == 'approved' else 'rejected'
        packet_approval_id = info.get('approval_id') or ''
        row = None
        if packet_approval_id:
            row = con.execute(
                """select id, decision, valid, gate from approvals
                       where approval_id=? and valid=1
                       order by id desc limit 1""",
                (packet_approval_id,),
            ).fetchone()
        if row is None:
            token = gate_token(info.get('approval_type') or '')
            if token:
                row = con.execute(
                    """select id, decision, valid, gate from approvals
                           where dedupe_key=? and artifact_version=? and valid=1 and gate like ?
                           order by id desc limit 1""",
                    (dedupe, version, f'%{token}%'),
                ).fetchone()
            else:
                row = con.execute(
                    """select id, decision, valid, gate from approvals
                           where dedupe_key=? and artifact_version=? and valid=1
                           order by id desc limit 1""",
                    (dedupe, version),
                ).fetchone()
        if not row or row['decision'] == decision:
            move_packet(packet, status)
            continue
        decided_at = rec.get('decided_at') or now()
        approver = rec.get('decided_by') or 'Telegram approval button'
        approver_id = rec.get('decided_by_id')
        con.execute(
            """update approvals
                  set decision=?, decided_at=?, approver=?, telegram_user_id=?, used_at=?, conditions=?
                where id=?""",
            (decision, decided_at, approver, approver_id, decided_at,
             f'reconciled from approval button {button_id}', row['id']),
        )
        new_status = gate_transition(info.get('approval_type') or row['gate'] or '', status)
        if new_status:
            current = con.execute('select workflow_status from opportunities where dedupe_key=?', (dedupe,)).fetchone()
            current_status = current['workflow_status'] if current else None
            # Do not regress active work back to an earlier gate.
            safe = True
            if new_status == 'pursuing' and current_status not in {None, 'awaiting_pursue_decision', 'pursuing'}:
                safe = False
            if safe:
                con.execute('update opportunities set workflow_status=? where dedupe_key=?', (new_status, dedupe))
        con.execute(
            'insert into workflow_events(dedupe_key,event_type,event_at,actor,details_json) values(?,?,?,?,?)',
            (dedupe, 'approval_button_reconciled', now(), 'approval_reconciler', json.dumps({
                'button_id': button_id,
                'decision': decision,
                'packet': str(packet),
                'artifact_version': version,
            }, sort_keys=True)),
        )
        move_packet(packet, status)
        changed.append(f'{decision} {notice} {version[:12]} via {button_id}')
    con.commit()
    for line in changed:
        print(line)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
